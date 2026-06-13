from flask import (
    Blueprint, render_template, request,
    redirect, url_for, flash, abort,
)
from middleware.auth_guard import requer_admin
from services import investidor_service as inv_svc, auth_service

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


# ── Dashboard ─────────────────────────────────────────────────────────────────

@admin_bp.route("/")
@requer_admin
def dashboard():
    from services.usina_service import listar_usinas
    from services.veiculos_service import listar_empresas_veiculos

    all_usinas  = listar_usinas()
    all_carros  = listar_empresas_veiculos()

    # Monta lista de ativos para o seletor
    ativos = [
        {"id": u["id"], "nome": u["nome"], "tipo": "usina"}
        for u in all_usinas
    ] + [
        {"id": emp["slug"], "nome": emp["nome"], "tipo": "carro"}
        for emp in all_carros
    ]

    from flask import session as _sess
    ativo_id   = request.args.get("ativo_id", "")
    ativo_tipo = request.args.get("ativo_tipo", "")

    # Filtra dados conforme seleção
    if ativo_id and ativo_tipo == "usina":
        usinas           = [u for u in all_usinas if u["id"] == ativo_id]
        empresas_veiculos = []
        ativo_selecionado = next((a for a in ativos if a["id"] == ativo_id), None)
    elif ativo_id and ativo_tipo == "carro":
        usinas           = []
        empresas_veiculos = [e for e in all_carros if e["slug"] == ativo_id]
        ativo_selecionado = next((a for a in ativos if a["id"] == ativo_id), None)
    else:
        usinas            = all_usinas
        empresas_veiculos = all_carros
        ativo_selecionado = None

    # Persiste / limpa seleção na sessão para que outros menus mantenham o contexto
    if ativo_selecionado:
        _sess["ativo_id"]   = ativo_selecionado["id"]
        _sess["ativo_tipo"] = ativo_selecionado["tipo"]
        _sess["ativo_nome"] = ativo_selecionado["nome"]
    elif not ativo_id:
        _sess.pop("ativo_id",   None)
        _sess.pop("ativo_tipo", None)
        _sess.pop("ativo_nome", None)

    # Total investido — soma valor_investido_total das participações quando uma usina está selecionada
    total_investido = None
    if ativo_id and ativo_tipo == "usina":
        from services.usina_service import participacoes_da_usina
        _parts = participacoes_da_usina(ativo_id)
        _soma  = sum(float(p.get("valor_investido_total") or 0) for p in _parts)
        if _soma > 0:
            total_investido = _soma

    # Rendimento acumulado — créditos na conta bancária da usina selecionada
    rendimento_total   = None
    rendimento_meses   = []   # [{"mes": "jan", "valor": 5000.0}, ...]
    if ativo_id and ativo_tipo == "usina":
        _usina_sel = next((u for u in all_usinas if u["id"] == ativo_id), None)
        if _usina_sel:
            from services.supabase_client import get_service_client as _gsc
            from datetime import date as _date
            _sb2 = _gsc()
            _razao = (_usina_sel.get("razao_social") or _usina_sel.get("nome") or "")
            # Encontra conta bancária pelo titular_nome
            _contas = _sb2.from_("contas_bancarias") \
                          .select("id,titular_nome") \
                          .ilike("titular_nome", f"%{_razao[:8]}%") \
                          .execute().data or []
            if _contas:
                _conta_id = _contas[0]["id"]
                # Últimos 6 meses de créditos
                _hoje6 = _date.today()
                _inicio = _date(_hoje6.year if _hoje6.month > 6 else _hoje6.year - 1,
                                (_hoje6.month - 6) % 12 or 12, 1)
                _lanctos = _sb2.from_("lancamentos_bancarios") \
                               .select("valor,data_transacao") \
                               .eq("conta_bancaria_id", _conta_id) \
                               .eq("tipo", "credito") \
                               .gte("data_transacao", str(_inicio)) \
                               .ilike("descricao", f"%{_razao[:12]}%") \
                               .is_("deleted_at", "null") \
                               .order("data_transacao", desc=False) \
                               .execute().data or []
                if _lanctos:
                    rendimento_total = sum(float(l["valor"]) for l in _lanctos)
                    # Agrupa por mês
                    _meses_nomes = ["jan","fev","mar","abr","mai","jun",
                                    "jul","ago","set","out","nov","dez"]
                    _por_mes: dict = {}
                    for _l in _lanctos:
                        _ym = str(_l["data_transacao"])[:7]
                        _por_mes[_ym] = _por_mes.get(_ym, 0) + float(_l["valor"])
                    for _ym, _v in sorted(_por_mes.items()):
                        _mn = int(_ym.split("-")[1]) - 1
                        rendimento_meses.append({"mes": _meses_nomes[_mn], "valor": round(_v, 2)})

    # Contas a receber pendentes para "Próximos eventos"
    from services.supabase_client import get_service_client
    try:
        _sb   = get_service_client()
        _q    = _sb.from_("v_faturas_completas") \
                   .select("id,numero_fatura,ref_mes_ano,data_vencimento,status,"
                           "valor_final_cobrado,valor_total_cobrado,valor_liquido,"
                           "codigo_uc,uc_apelido,usina_razao_social") \
                   .in_("status", ["Pendente", "Emitida", "Enviada", "Atrasada"]) \
                   .order("data_vencimento", desc=False)
        if ativo_id and ativo_tipo == "usina":
            _usina_obj = next((u for u in all_usinas if u["id"] == ativo_id), None)
            if _usina_obj:
                _q = _q.eq("usina_id", ativo_id)
        from datetime import date
        _hoje = date.today()
        _raw  = _q.execute().data or []
        faturas_pendentes = []
        for _f in _raw:
            try:
                _y, _m, _d = str(_f["data_vencimento"])[:10].split("-")
                _dias = (date(int(_y), int(_m), int(_d)) - _hoje).days
            except Exception:
                _dias = 0
            _f["dias_vencimento"] = _dias
            faturas_pendentes.append(_f)
    except Exception:
        faturas_pendentes = []

    return render_template(
        "admin/dashboard.html",
        usinas=usinas,
        empresas_veiculos=empresas_veiculos,
        ativos=ativos,
        ativo_id=ativo_id,
        ativo_tipo=ativo_tipo,
        ativo_selecionado=ativo_selecionado,
        total_investido=total_investido,
        faturas_pendentes=faturas_pendentes,
        rendimento_total=rendimento_total,
        rendimento_meses=rendimento_meses,
    )


# ── Detalhe da usina ──────────────────────────────────────────────────────────

@admin_bp.route("/usina/<usina_id>")
@requer_admin
def usina_detalhe(usina_id):
    from datetime import date
    from services.usina_service import (
        buscar_usina, participacoes_da_usina,
        leituras_da_usina, pnl_da_usina,
        listar_lancamentos, listar_categorias, calcular_kpis,
        clientes_da_usina, auto_conciliar_neutros, listar_contas_da_usina,
    )
    usina = buscar_usina(usina_id)
    if not usina:
        abort(404)

    tab = request.args.get("tab", "socios")
    if tab not in ("socios", "clientes", "extrato", "dre"):
        tab = "socios"

    auto_conciliar_neutros(usina_id)

    contas      = listar_contas_da_usina(usina_id)
    conta_id    = request.args.get("conta_id") or (contas[0]["id"] if contas else None)
    conta_atual = next((c for c in contas if c["id"] == conta_id), contas[0] if contas else None)
    saldo_inicial = float(conta_atual["saldo_inicial"] or 0) if conta_atual else 0
    lancamentos = listar_lancamentos(usina_id, conta_id=conta_id)

    # Fluxo de caixa — por transação, excluindo fundos automáticos (neutros)
    # débitos antes de créditos no mesmo dia → mostra o saldo mínimo intradiário
    _lanc_op = sorted(
        [l for l in lancamentos if l.get("data_transacao") and not l.get("_neutro")],
        key=lambda l: (l["data_transacao"], 0 if l.get("tipo") == "credito" else 1)
    )
    _acc = saldo_inicial
    _dia_acc = {}
    for _l in _lanc_op:
        _v = abs(_l.get("valor") or 0)
        _acc += _v if _l.get("tipo") == "credito" else -_v
        _dia_acc[_l["data_transacao"][:10]] = round(_acc, 2)

    if _dia_acc:
        _chart_dates = ["Saldo ini."] + list(_dia_acc.keys())
        _chart_pts   = [round(saldo_inicial, 2)] + list(_dia_acc.values())
    else:
        _chart_dates, _chart_pts = [], []

    # Meses com transações operacionais (excl. neutros) para navegação do fluxo
    chart_meses     = sorted({
        l["data_transacao"][:7] for l in lancamentos
        if l.get("data_transacao") and not l.get("_neutro")
    })
    chart_fluxo_dates = _chart_dates
    chart_fluxo_pts   = _chart_pts
    leituras    = leituras_da_usina(usina_id)

    # Default to the most recent month with data, fallback to today
    if request.args.get("kpi_mes"):
        kpi_mes = request.args.get("kpi_mes")
    else:
        datas = [l["data_transacao"][:7] for l in lancamentos if l.get("data_transacao")]
        kpi_mes = max(datas) if datas else date.today().strftime("%Y-%m")
    pnl         = pnl_da_usina(usina_id)
    clientes    = clientes_da_usina(usina_id)
    categorias  = listar_categorias()
    num_ativos  = sum(1 for c in clientes if c.get("status") == "Ativo")

    # Faturas desta usina para painel Recebimentos
    try:
        from services.supabase_client import get_service_client as _gsc
        _hoje = date.today()
        _raw_fat = (
            _gsc().from_("v_faturas_completas")
            .select("id,numero_fatura,ref_mes_ano,data_vencimento,status,"
                    "valor_final_cobrado,codigo_uc,uc_apelido,usina_razao_social")
            .eq("usina_id", usina_id)
            .in_("status", ["Pendente", "Emitida", "Enviada", "Atrasada"])
            .order("data_vencimento", desc=False)
            .execute().data or []
        )
        faturas_usina = []
        for _f in _raw_fat:
            try:
                _y, _m, _d = str(_f["data_vencimento"])[:10].split("-")
                _f["dias_vencimento"] = (date(int(_y), int(_m), int(_d)) - _hoje).days
            except Exception:
                _f["dias_vencimento"] = 0
            faturas_usina.append(_f)
        fat_tot_emaberto = sum(
            float(_f.get("valor_final_cobrado") or 0) for _f in faturas_usina
        )
        fat_tot_vencido = sum(
            float(_f.get("valor_final_cobrado") or 0) for _f in faturas_usina
            if _f.get("status") == "Atrasada" or _f.get("dias_vencimento", 1) <= 0
        )
    except Exception:
        faturas_usina = []
        fat_tot_emaberto = 0.0
        fat_tot_vencido = 0.0

    return render_template(
        "admin/usina_detalhe.html",
        usina=usina,
        tab=tab,
        participacoes=participacoes_da_usina(usina_id),
        clientes=clientes,
        lancamentos=lancamentos,
        leituras=leituras,
        pnl=pnl,
        kpis=calcular_kpis(usina_id, kpi_mes, lancamentos, leituras, pnl, num_ativos),
        kpi_mes=kpi_mes,
        categorias=categorias,
        contas=contas,
        conta_id=conta_id,
        saldo_inicial=saldo_inicial,
        chart_meses=chart_meses,
        chart_fluxo_dates=chart_fluxo_dates,
        chart_fluxo_pts=chart_fluxo_pts,
        faturas_usina=faturas_usina,
        fat_tot_emaberto=fat_tot_emaberto,
        fat_tot_vencido=fat_tot_vencido,
    )


@admin_bp.route("/usina/<usina_id>/editar", methods=["POST"])
@requer_admin
def usina_editar(usina_id):
    from services.usina_service import atualizar_usina
    campos = {k: v for k, v in request.form.items()}
    atualizar_usina(usina_id, campos)
    flash("Usina atualizada.", "sucesso")
    return redirect(url_for("admin.usina_detalhe", usina_id=usina_id))


@admin_bp.route("/usina/<usina_id>/distribuicao/nova", methods=["POST"])
@requer_admin
def usina_distribuicao_nova(usina_id):
    from services.usina_service import adicionar_distribuicao
    res = adicionar_distribuicao(usina_id, request.form.to_dict())
    flash(
        "Distribuição registrada." if res["ok"] else f"Erro: {res['erro']}",
        "sucesso" if res["ok"] else "erro",
    )
    return redirect(url_for("admin.usina_detalhe", usina_id=usina_id))


@admin_bp.route("/usina/<usina_id>/documento/upload", methods=["POST"])
@requer_admin
def usina_documento_upload(usina_id):
    from services.usina_service import upload_documento
    arquivo = request.files.get("arquivo")
    categoria = request.form.get("categoria", "outros")
    if not arquivo:
        flash("Selecione um arquivo.", "erro")
        return redirect(url_for("admin.usina_detalhe", usina_id=usina_id))
    u = auth_service.usuario_logado()
    res = upload_documento(
        usina_id, arquivo.filename, categoria,
        arquivo.read(), arquivo.content_type,
        user_id=u["id"] if u else None,
    )
    flash(
        "Documento enviado." if res["ok"] else f"Erro: {res['erro']}",
        "sucesso" if res["ok"] else "erro",
    )
    return redirect(url_for("admin.usina_detalhe", usina_id=usina_id))


@admin_bp.route("/usina/<usina_id>/documento/<doc_id>/excluir", methods=["POST"])
@requer_admin
def usina_documento_excluir(usina_id, doc_id):
    from services.usina_service import excluir_documento
    excluir_documento(doc_id)
    flash("Documento excluído.", "sucesso")
    return redirect(url_for("admin.usina_detalhe", usina_id=usina_id))


# ── Configurações / usuários ──────────────────────────────────────────────────

@admin_bp.route("/configuracoes")
@requer_admin
def configuracoes():
    from services.usina_service import listar_usinas, listar_categorias, categorias_em_uso
    from services.veiculos_service import listar_empresas_veiculos
    return render_template(
        "admin/configuracoes.html",
        investidores=auth_service.listar_usuarios_com_acesso(),
        usinas=listar_usinas(),
        empresas_veiculos=listar_empresas_veiculos(),
        categorias=listar_categorias(),
        cats_em_uso=categorias_em_uso(),
    )


@admin_bp.route("/configuracoes/natureza/nova", methods=["POST"])
@requer_admin
def natureza_nova():
    from services.usina_service import criar_categoria
    nome = request.form.get("nome", "").strip()
    tipo = request.form.get("tipo", "")
    if not nome or tipo not in ("receita", "despesa"):
        flash("Preencha nome e tipo.", "erro")
        return redirect(url_for("admin.configuracoes") + "#naturezas")
    res = criar_categoria(nome, tipo)
    flash("Natureza criada." if res["ok"] else f"Erro: {res.get('erro')}", "sucesso" if res["ok"] else "erro")
    return redirect(url_for("admin.configuracoes") + "#naturezas")


@admin_bp.route("/configuracoes/natureza/<cat_id>/excluir", methods=["POST"])
@requer_admin
def natureza_excluir(cat_id):
    from services.usina_service import deletar_categoria
    res = deletar_categoria(cat_id)
    if not res["ok"]:
        if res.get("em_uso"):
            flash("Esta natureza está vinculada a lançamentos e não pode ser excluída.", "erro")
        else:
            flash(f"Erro: {res.get('erro')}", "erro")
    else:
        flash("Natureza removida.", "sucesso")
    return redirect(url_for("admin.configuracoes") + "#naturezas")


@admin_bp.route("/configuracoes/novo-usuario", methods=["POST"])
@requer_admin
def novo_usuario():
    username = request.form.get("username", "").strip()
    senha = request.form.get("senha", "").strip()
    nome = request.form.get("nome", "").strip()
    usina_ids = request.form.getlist("usina_ids")
    permissions = request.form.getlist("permissions")
    if not permissions:
        permissions = ["visao_geral", "distribuicoes", "documentos", "leituras"]
    if not all([username, senha, nome]):
        flash("Preencha todos os campos.", "erro")
        return redirect(url_for("admin.configuracoes"))
    res = auth_service.criar_usuario_admin(username, senha, nome, usina_ids, permissions)
    flash(
        f"Usuário '{username}' criado." if res["ok"] else f"Erro: {res['erro']}",
        "sucesso" if res["ok"] else "erro",
    )
    return redirect(url_for("admin.configuracoes"))


@admin_bp.route("/configuracoes/<investidor_id>/acesso", methods=["POST"])
@requer_admin
def atualizar_acesso(investidor_id):
    usina_ids = request.form.getlist("usina_ids")
    permissions = request.form.getlist("permissions")
    res = auth_service.atualizar_acesso_usuario(investidor_id, usina_ids, permissions)
    flash(
        "Acesso atualizado." if res["ok"] else f"Erro: {res['erro']}",
        "sucesso" if res["ok"] else "erro",
    )
    return redirect(url_for("admin.configuracoes"))


@admin_bp.route("/configuracoes/<investidor_id>/redefinir-senha", methods=["POST"])
@requer_admin
def redefinir_senha(investidor_id):
    from services.supabase_client import get_service_client
    nova_senha = request.form.get("nova_senha", "").strip()
    if not nova_senha:
        flash("Informe a nova senha.", "erro")
        return redirect(url_for("admin.configuracoes"))
    try:
        get_service_client().auth.admin.update_user_by_id(
            investidor_id, {"password": nova_senha}
        )
        flash("Senha redefinida.", "sucesso")
    except Exception as e:
        flash(f"Erro: {e}", "erro")
    return redirect(url_for("admin.configuracoes"))


@admin_bp.route("/configuracoes/<investidor_id>/excluir", methods=["POST"])
@requer_admin
def excluir_usuario(investidor_id):
    from services.supabase_client import get_service_client
    sb = get_service_client()
    try:
        sb.auth.admin.delete_user(investidor_id)
        sb.table("investidores").delete().eq("id", investidor_id).execute()
        flash("Usuário excluído.", "sucesso")
    except Exception as e:
        flash(f"Erro: {e}", "erro")
    return redirect(url_for("admin.configuracoes"))


# ── Extrato bancário ─────────────────────────────────────────────────────────

@admin_bp.route("/usina/<usina_id>/extrato/importar", methods=["POST"])
@requer_admin
def extrato_importar(usina_id):
    from services.usina_service import importar_extrato
    arquivo = request.files.get("arquivo")
    if not arquivo:
        flash("Selecione um arquivo.", "erro")
        return redirect(url_for("admin.usina_detalhe", usina_id=usina_id))
    ext = arquivo.filename.rsplit(".", 1)[-1].lower() if "." in arquivo.filename else ""
    if ext not in ("xlsx", "ofx"):
        flash("Formato inválido. Use .xlsx ou .ofx.", "erro")
        return redirect(url_for("admin.usina_detalhe", usina_id=usina_id))
    u = auth_service.usuario_logado()
    res = importar_extrato(usina_id, arquivo.read(), ext,
                           importado_por=u["id"] if u else None)
    flash(
        f"{res['inseridos']} lançamentos importados ({res['total']} no arquivo)." if res["ok"]
        else f"Erro: {res['erro']}",
        "sucesso" if res["ok"] else "erro",
    )
    return redirect(url_for("admin.usina_detalhe", usina_id=usina_id, tab="extrato"))


@admin_bp.route("/usina/<usina_id>/extrato/<lancamento_id>/conciliar", methods=["POST"])
@requer_admin
def extrato_conciliar(usina_id, lancamento_id):
    from services.usina_service import conciliar_lancamento
    categoria_id = request.form.get("categoria_id") or None
    observacao = request.form.get("observacao", "").strip() or None
    res = conciliar_lancamento(lancamento_id, categoria_id, observacao)
    flash("Lançamento conciliado." if res["ok"] else f"Erro: {res['erro']}",
          "sucesso" if res["ok"] else "erro")
    return redirect(url_for("admin.usina_detalhe", usina_id=usina_id, tab="extrato"))


@admin_bp.route("/usina/<usina_id>/extrato/manual", methods=["POST"])
@requer_admin
def extrato_manual(usina_id):
    from services.usina_service import criar_lancamento
    res = criar_lancamento(usina_id, request.form.to_dict())
    flash("Lançamento adicionado." if res["ok"] else f"Erro: {res['erro']}",
          "sucesso" if res["ok"] else "erro")
    return redirect(url_for("admin.usina_detalhe", usina_id=usina_id, tab="dre"))


# ── Fluxo de Caixa / Conciliação ─────────────────────────────────────────────

@admin_bp.route("/fluxo")
@requer_admin
def fluxo_de_caixa():
    from services.conciliacao_service import (
        listar_contas, listar_categorias, listar_centros_custo,
        listar_lancamentos, meses_da_conta, kpis_periodo,
        saldo_atual, ultima_importacao,
    )
    contas      = listar_contas()
    categorias  = listar_categorias()
    centros     = listar_centros_custo()

    conta_id    = request.args.get("conta_id") or (contas[0]["id"] if contas else None)
    mes         = request.args.get("mes") or ""
    filtro_tipo = request.args.get("tipo") or ""
    cat_id      = request.args.get("categoria_id") or ""
    cc_id       = request.args.get("centro_custo_id") or ""
    status      = request.args.get("status") or ""

    conta_atual = next((c for c in contas if c["id"] == conta_id), contas[0] if contas else None)
    saldo_ini   = float(conta_atual["saldo_inicial"] or 0) if conta_atual else 0.0

    lancamentos = listar_lancamentos(
        conta_id,
        mes=mes or None,
        tipo=filtro_tipo or None,
        categoria_id=cat_id or None,
        centro_custo_id=cc_id or None,
        status=status or None,
    )
    meses       = meses_da_conta(conta_id) if conta_id else []
    kpis        = kpis_periodo(lancamentos)
    saldo_atual_ = saldo_atual(conta_id, saldo_ini) if conta_id else saldo_ini
    ult_imp     = ultima_importacao(conta_id) if conta_id else None

    return render_template(
        "admin/fluxo.html",
        contas=contas,
        conta_atual=conta_atual,
        conta_id=conta_id,
        saldo_ini=saldo_ini,
        saldo_atual=saldo_atual_,
        lancamentos=lancamentos,
        meses=meses,
        kpis=kpis,
        ult_imp=ult_imp,
        categorias=categorias,
        centros=centros,
        filtros={
            "mes": mes, "tipo": filtro_tipo,
            "categoria_id": cat_id, "centro_custo_id": cc_id, "status": status,
        },
    )


@admin_bp.route("/fluxo/importar", methods=["POST"])
@requer_admin
def fluxo_importar():
    from services.conciliacao_service import importar_ofx
    conta_id = request.form.get("conta_id")
    arquivo  = request.files.get("arquivo")
    if not arquivo or not conta_id:
        flash("Selecione um arquivo e uma conta.", "erro")
        return redirect(url_for("admin.fluxo_de_caixa"))
    ext = arquivo.filename.rsplit(".", 1)[-1].lower() if "." in arquivo.filename else ""
    if ext not in ("xlsx", "ofx"):
        flash("Formato inválido. Use .xlsx ou .ofx.", "erro")
        return redirect(url_for("admin.fluxo_de_caixa", conta_id=conta_id))
    u = auth_service.usuario_logado()
    res = importar_ofx(conta_id, arquivo.read(), ext, importado_por=u["id"] if u else None)
    flash(
        f"{res['inseridos']} lançamentos importados ({res['total']} no arquivo)." if res["ok"]
        else f"Erro: {res['erro']}",
        "sucesso" if res["ok"] else "erro",
    )
    return redirect(url_for("admin.fluxo_de_caixa", conta_id=conta_id))


# ── Detalhe empresa de veículos ──────────────────────────────────────────────

@admin_bp.route("/empresa-veiculos/<slug>")
@requer_admin
def empresa_veiculo_detalhe(slug):
    from services.veiculos_service import buscar_empresa_veiculos, recebimentos_da_empresa
    empresa = buscar_empresa_veiculos(slug)
    if not empresa:
        abort(404)
    historico = recebimentos_da_empresa(empresa)
    return render_template(
        "admin/empresa_veiculo_detalhe.html",
        empresa=empresa,
        historico=historico,
    )


# ── Portfólios ────────────────────────────────────────────────────────────────

@admin_bp.route("/portfolio/usinas")
@requer_admin
def portfolio_usinas():
    from flask import session as _sess
    from services.usina_service import listar_usinas
    todas = listar_usinas()
    ativo_id   = _sess.get("ativo_id", "")
    ativo_tipo = _sess.get("ativo_tipo", "")
    if ativo_id and ativo_tipo == "usina":
        usinas = [u for u in todas if u["id"] == ativo_id]
    else:
        usinas = todas
    return render_template("admin/portfolio_usinas.html", usinas=usinas)


@admin_bp.route("/portfolio/carros")
@requer_admin
def portfolio_carros():
    from services.veiculos_service import listar_empresas_veiculos
    return render_template("admin/portfolio_carros.html",
                           empresas_veiculos=listar_empresas_veiculos())


# ── Preview portal do investidor ──────────────────────────────────────────────

@admin_bp.route("/investidores")
@requer_admin
def investidores():
    return render_template(
        "admin/investidores.html",
        investidores=inv_svc.listar_investidores(),
    )


@admin_bp.route("/investidores/<investidor_id>/preview")
@requer_admin
def preview_portal(investidor_id):
    auth_service.set_preview(investidor_id)
    return redirect(url_for("portal.home"))


@admin_bp.route("/preview/sair")
@requer_admin
def sair_preview():
    auth_service.clear_preview()
    return redirect(url_for("admin.dashboard"))
