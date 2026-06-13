from flask import Blueprint, render_template, request, redirect, url_for, abort, session as _sess
from middleware.auth_guard import requer_login
from services.auth_service import usuario_logado, is_admin, preview_investidor_id

portal_bp = Blueprint("portal", __name__, url_prefix="/portal")

_MESES = ["jan","fev","mar","abr","mai","jun","jul","ago","set","out","nov","dez"]


@portal_bp.route("/")
@portal_bp.route("/home")
@requer_login
def home():
    from services.usina_service import usinas_do_usuario, usinas_do_investidor
    from services.supabase_client import get_service_client
    from datetime import date

    u = usuario_logado()
    usina_ids = u.get("usina_ids", [])

    partic = usinas_do_investidor(u["id"])
    cotas  = {p["usina_id"]: p.get("percentual", 0) for p in partic}

    # Todas as usinas do investidor (para o seletor)
    if usina_ids:
        all_usinas = usinas_do_usuario(usina_ids)
    else:
        all_usinas = [p["usinas"] for p in partic if p.get("usinas")]

    ativos = [{"id": us["id"], "nome": us["nome"], "tipo": "usina"} for us in all_usinas]

    # Filtro por ativo selecionado
    # Atualiza sessão apenas quando há seleção explícita no dropdown
    if "ativo_id" in request.args:
        ativo_id = request.args.get("ativo_id", "")
        ativo_selecionado = next((a for a in ativos if a["id"] == ativo_id), None)
        if ativo_selecionado:
            _sess["inv_ativo_id"]   = ativo_selecionado["id"]
            _sess["inv_ativo_nome"] = ativo_selecionado["nome"]
        else:
            _sess.pop("inv_ativo_id",   None)
            _sess.pop("inv_ativo_nome", None)
    else:
        # Sem seleção explícita: preserva sessão ou auto-seleciona se tiver uma só usina
        ativo_id = _sess.get("inv_ativo_id", "")
        if not ativo_id and len(all_usinas) == 1:
            ativo_id = all_usinas[0]["id"]
            _sess["inv_ativo_id"]   = ativo_id
            _sess["inv_ativo_nome"] = all_usinas[0].get("nome", "")

    ativo_selecionado = next((a for a in ativos if a["id"] == ativo_id), None)
    usinas = [us for us in all_usinas if us["id"] == ativo_id] if ativo_id else all_usinas
    partic_vis = [p for p in partic if not ativo_id or p["usina_id"] == ativo_id]

    kwp = sum(float(us.get("potencia_instalada_kwp") or 0) for us in usinas)

    # Total investido
    total_investido = sum(float(p.get("valor_investido_total") or 0) for p in partic_vis) or None
    if total_investido == 0:
        total_investido = None

    # Rendimento: créditos nas contas bancárias das usinas × cota
    rendimento_total = None
    rendimento_meses = []
    try:
        sb   = get_service_client()
        hoje = date.today()
        m6   = hoje.month - 6
        inicio = date(hoje.year if m6 > 0 else hoje.year - 1, m6 if m6 > 0 else m6 + 12, 1)
        por_mes: dict = {}
        total_rend = 0.0
        for us in usinas:
            razao = (us.get("razao_social") or us.get("nome") or "")
            razao_busca = razao[:8]
            razao_desc  = razao[:12]
            contas = sb.from_("contas_bancarias").select("id") \
                       .ilike("titular_nome", f"%{razao_busca}%").execute().data or []
            cota = cotas.get(us["id"], 1.0)
            for conta in contas:
                lanctos = sb.from_("lancamentos_bancarios") \
                             .select("valor,data_transacao") \
                             .eq("conta_bancaria_id", conta["id"]) \
                             .eq("tipo", "credito") \
                             .gte("data_transacao", str(inicio)) \
                             .ilike("descricao", f"%{razao_desc}%") \
                             .is_("deleted_at", "null") \
                             .execute().data or []
                for l in lanctos:
                    v = float(l["valor"]) * cota
                    total_rend += v
                    ym = str(l["data_transacao"])[:7]
                    por_mes[ym] = por_mes.get(ym, 0.0) + v
        if total_rend > 0:
            rendimento_total = round(total_rend, 2)
        for ym, v in sorted(por_mes.items()):
            mn = int(ym.split("-")[1]) - 1
            rendimento_meses.append({"mes": _MESES[mn], "valor": round(v, 2)})
    except Exception:
        pass

    # Faturas pendentes das usinas visíveis
    faturas_pendentes = []
    try:
        ids_vis = [us["id"] for us in usinas]
        if ids_vis:
            _q = sb.from_("v_faturas_completas") \
                   .select("id,numero_fatura,ref_mes_ano,data_vencimento,status,"
                           "valor_final_cobrado,valor_total_cobrado,valor_liquido,"
                           "codigo_uc,uc_apelido,usina_razao_social") \
                   .in_("status", ["Pendente", "Emitida", "Enviada", "Atrasada"]) \
                   .in_("usina_id", ids_vis) \
                   .order("data_vencimento", desc=False) \
                   .execute().data or []
            hoje_d = date.today()
            for f in _q:
                try:
                    y, m, d = str(f["data_vencimento"])[:10].split("-")
                    f["dias_vencimento"] = (date(int(y), int(m), int(d)) - hoje_d).days
                except Exception:
                    f["dias_vencimento"] = 0
                faturas_pendentes.append(f)
    except Exception:
        pass

    # Dados visuais — só busca se tiver usina selecionada e permissão
    from services.usina_service import (
        retorno_mensal_investidor, leituras_detalhadas,
        saldo_creditos_da_usina, pnl_da_usina,
    )
    perms     = u.get("permissions", [])
    all_perms = "all" in perms

    home_tabs = ["visao_geral"]
    if ativo_id:
        if all_perms or "retorno_mensal" in perms: home_tabs.append("retorno_mensal")
        if all_perms or "energia"        in perms: home_tabs.append("energia")
        if all_perms or "pnl"            in perms: home_tabs.append("pnl")
        if all_perms or "saldo_creditos" in perms: home_tabs.append("saldo_creditos")

    tab = request.args.get("tab", "visao_geral")
    if tab not in home_tabs:
        tab = "visao_geral"

    retorno_mensal = retorno_mensal_investidor(ativo_id) if ativo_id and "retorno_mensal" in home_tabs else []
    leituras_det   = leituras_detalhadas(ativo_id)       if ativo_id and "energia"        in home_tabs else []
    pnl            = pnl_da_usina(ativo_id)              if ativo_id and "pnl"            in home_tabs else []
    saldo_creditos = saldo_creditos_da_usina(ativo_id)   if ativo_id and "saldo_creditos" in home_tabs else []

    return render_template(
        "portal/home.html",
        usinas=usinas,
        all_usinas=all_usinas,
        ativos=ativos,
        cotas=cotas,
        kwp=kwp,
        total_investido=total_investido,
        rendimento_total=rendimento_total,
        rendimento_meses=rendimento_meses,
        faturas_pendentes=faturas_pendentes,
        ativo_id=ativo_id,
        ativo_selecionado=ativo_selecionado,
        usuario=u,
        tab=tab,
        home_tabs=home_tabs,
        retorno_mensal=retorno_mensal,
        leituras_det=leituras_det,
        pnl=pnl,
        saldo_creditos=saldo_creditos,
    )


@portal_bp.route("/usina/<usina_id>")
@requer_login
def usina_detalhe(usina_id):
    from datetime import date as _date
    from services.usina_service import (
        buscar_usina, usinas_do_investidor,
        leituras_da_usina, pnl_da_usina,
        listar_lancamentos, calcular_kpis,
        clientes_da_usina, listar_contas_da_usina,
        participacoes_da_usina,
        retorno_mensal_investidor, leituras_detalhadas, saldo_creditos_da_usina,
    )
    u = usuario_logado()
    usina_ids = u.get("usina_ids", [])
    partic = usinas_do_investidor(u["id"])

    if usina_ids:
        if usina_id not in usina_ids:
            abort(403)
    else:
        if usina_id not in [p["usina_id"] for p in partic]:
            abort(403)

    usina = buscar_usina(usina_id)
    if not usina:
        abort(404)

    _sess["inv_ativo_id"]   = usina_id
    _sess["inv_ativo_nome"] = usina.get("nome", "")

    perms     = u.get("permissions", [])
    all_perms = "all" in perms

    valid_tabs = []
    if all_perms or "socios" in perms:                                         valid_tabs.append("socios")
    if all_perms or "clientes" in perms:                                       valid_tabs.append("clientes")
    if all_perms or "extrato_bancario" in perms or "fluxo_de_caixa" in perms: valid_tabs.append("extrato")
    if all_perms or "dre" in perms:                                            valid_tabs.append("dre")
    if all_perms or "retorno_mensal" in perms:                                 valid_tabs.append("retorno_mensal")
    if all_perms or "energia" in perms:                                        valid_tabs.append("energia")
    if all_perms or "pnl" in perms:                                            valid_tabs.append("pnl")
    if all_perms or "saldo_creditos" in perms:                                 valid_tabs.append("saldo_creditos")

    tab = request.args.get("tab", "")
    if tab not in valid_tabs:
        tab = valid_tabs[0] if valid_tabs else "socios"

    contas      = listar_contas_da_usina(usina_id)
    conta_id    = request.args.get("conta_id") or (contas[0]["id"] if contas else None)
    conta_atual = next((c for c in contas if c["id"] == conta_id), contas[0] if contas else None)
    saldo_inicial = float(conta_atual["saldo_inicial"] or 0) if conta_atual else 0

    tem_extrato = all_perms or "extrato_bancario" in perms or "fluxo_de_caixa" in perms
    lancamentos = listar_lancamentos(usina_id, conta_id=conta_id) if tem_extrato else []

    _lanc_op = sorted(
        [l for l in lancamentos if l.get("data_transacao") and not l.get("_neutro")],
        key=lambda l: (l["data_transacao"], 0 if l.get("tipo") == "credito" else 1)
    )
    _acc, _dia_acc = saldo_inicial, {}
    for _l in _lanc_op:
        _v = abs(_l.get("valor") or 0)
        _acc += _v if _l.get("tipo") == "credito" else -_v
        _dia_acc[_l["data_transacao"][:10]] = round(_acc, 2)

    if _dia_acc:
        chart_fluxo_dates = ["Saldo ini."] + list(_dia_acc.keys())
        chart_fluxo_pts   = [round(saldo_inicial, 2)] + list(_dia_acc.values())
    else:
        chart_fluxo_dates, chart_fluxo_pts = [], []

    chart_meses = sorted({
        l["data_transacao"][:7] for l in lancamentos
        if l.get("data_transacao") and not l.get("_neutro")
    })
    leituras = leituras_da_usina(usina_id)

    if request.args.get("kpi_mes"):
        kpi_mes = request.args.get("kpi_mes")
    else:
        datas = [l["data_transacao"][:7] for l in lancamentos if l.get("data_transacao")]
        kpi_mes = max(datas) if datas else _date.today().strftime("%Y-%m")

    pnl          = pnl_da_usina(usina_id)
    clientes     = clientes_da_usina(usina_id) if (all_perms or "clientes" in perms) else []
    participacoes = participacoes_da_usina(usina_id) if (all_perms or "socios" in perms) else []
    num_ativos   = sum(1 for c in clientes if c.get("status") == "Ativo")

    # Dados visuais
    tem_retorno = all_perms or "retorno_mensal" in perms
    tem_energia = all_perms or "energia" in perms
    tem_pnl     = all_perms or "pnl" in perms
    tem_saldo   = all_perms or "saldo_creditos" in perms

    retorno_mensal  = retorno_mensal_investidor(usina_id) if tem_retorno else []
    leituras_det    = leituras_detalhadas(usina_id) if tem_energia else []
    saldo_creditos  = saldo_creditos_da_usina(usina_id) if tem_saldo else []

    return render_template(
        "portal/usina_detalhe.html",
        usina=usina,
        tab=tab,
        valid_tabs=valid_tabs,
        participacoes=participacoes,
        clientes=clientes,
        lancamentos=lancamentos,
        leituras=leituras,
        pnl=pnl,
        kpis=calcular_kpis(usina_id, kpi_mes, lancamentos, leituras, pnl, num_ativos),
        kpi_mes=kpi_mes,
        categorias=[],
        contas=contas,
        conta_id=conta_id,
        saldo_inicial=saldo_inicial,
        chart_meses=chart_meses,
        chart_fluxo_dates=chart_fluxo_dates,
        chart_fluxo_pts=chart_fluxo_pts,
        usuario=u,
        retorno_mensal=retorno_mensal,
        leituras_det=leituras_det,
        saldo_creditos=saldo_creditos,
    )


@portal_bp.route("/documentos")
@requer_login
def documentos():
    from services.usina_service import usinas_do_investidor, usinas_do_usuario, documentos_da_usina
    u = usuario_logado()
    usina_ids = u.get("usina_ids", [])

    if usina_ids:
        usinas = usinas_do_usuario(usina_ids)
        participacoes = [{"usina_id": us["id"], "usinas": us} for us in usinas]
        ids_permitidos = usina_ids
    else:
        participacoes = usinas_do_investidor(u["id"])
        ids_permitidos = [p["usina_id"] for p in participacoes]

    usina_id = request.args.get("usina_id")
    docs = []
    if usina_id and usina_id in ids_permitidos:
        docs = [d for d in documentos_da_usina(usina_id) if d.get("visivel_a_todos")]

    return render_template(
        "portal/documentos.html",
        participacoes=participacoes,
        usina_id_selecionada=usina_id,
        documentos=docs,
        usuario=u,
    )
