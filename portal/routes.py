from flask import Blueprint, render_template, request, redirect, url_for, abort, session as _sess
from middleware.auth_guard import requer_login
from services.auth_service import usuario_logado, is_admin, preview_investidor_id, refresh_session_permissions

portal_bp = Blueprint("portal", __name__, url_prefix="/portal")

_MESES = ["jan","fev","mar","abr","mai","jun","jul","ago","set","out","nov","dez"]


@portal_bp.route("/")
@portal_bp.route("/home")
@requer_login
def home():
    from services.usina_service import usinas_do_usuario, usinas_do_investidor
    from services.supabase_client import get_service_client
    from datetime import date

    refresh_session_permissions()
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

    # Empresas de carros acessíveis (slugs não-UUID em usina_ids)
    import re as _re
    _uuid_re = _re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', _re.I)
    carros_slugs = [i for i in usina_ids if not _uuid_re.match(str(i))]
    empresas_carros = []
    if carros_slugs:
        try:
            from services.veiculos_service import listar_empresas_veiculos
            todas_emp = listar_empresas_veiculos()
            empresas_carros = [e for e in todas_emp if e.get("slug") in carros_slugs]
        except Exception:
            pass

    # Mapa slug→empresa para lookup rápido
    carros_por_slug = {e["slug"]: e for e in empresas_carros}
    ativos_carros = [{"id": e["slug"], "nome": e["nome"], "tipo": "carros"} for e in empresas_carros]

    # Filtro por ativo selecionado
    # Atualiza sessão apenas quando há seleção explícita no dropdown
    if "ativo_id" in request.args:
        ativo_id = request.args.get("ativo_id", "")
        ativo_selecionado = (
            next((a for a in ativos if a["id"] == ativo_id), None) or
            next((a for a in ativos_carros if a["id"] == ativo_id), None)
        )
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

    ativo_selecionado = (
        next((a for a in ativos if a["id"] == ativo_id), None) or
        next((a for a in ativos_carros if a["id"] == ativo_id), None)
    )
    ativo_tipo = ativo_selecionado["tipo"] if ativo_selecionado else "usina"

    usinas = [us for us in all_usinas if us["id"] == ativo_id] if (ativo_id and ativo_tipo == "usina") else (all_usinas if ativo_tipo == "usina" else [])
    partic_vis = [p for p in partic if not ativo_id or p["usina_id"] == ativo_id]

    kwp = sum(float(us.get("potencia_instalada_kwp") or 0) for us in usinas)

    # Total investido
    total_investido = sum(float(p.get("valor_investido_total") or 0) for p in partic_vis) or None
    if total_investido == 0:
        total_investido = None

    empresa_carros_sel = carros_por_slug.get(ativo_id)

    # Permissões e aba ativa — calculados antes dos blocos pesados para que
    # cada bloco só busque dados da aba que será de fato exibida.
    u_perms   = u.get("permissions", [])
    all_perms = "all" in u_perms

    home_tabs = ["visao_geral"]
    if ativo_tipo == "carros" and empresa_carros_sel:
        home_tabs.append("extrato")
    if ativo_id:
        if (ativo_tipo != "carros") and (all_perms or "extrato_bancario" in u_perms or "fluxo_de_caixa" in u_perms): home_tabs.append("extrato")
        if all_perms or "benchmarks"       in u_perms:                                 home_tabs.append("benchmarks")
        if all_perms or "energia"          in u_perms or "saldo_creditos" in u_perms:   home_tabs.append("saldo_creditos")
        if all_perms or "pnl"              in u_perms:                                 home_tabs.append("pnl")
        if all_perms or "dre"              in u_perms:                                 home_tabs.append("dre")
        if all_perms or "clientes"         in u_perms:                                 home_tabs.append("clientes")

    tab = request.args.get("tab", "visao_geral")
    if tab not in home_tabs:
        tab = "visao_geral"

    # Rendimento: depende do tipo de ativo
    rendimento_total = None
    rendimento_meses = []

    valor_liquido_recebido = None
    motoristas_recebimentos = []
    recebimentos_por_mes_carros = []
    carros_veiculos_status = []
    carros_rentabilidade = None

    if ativo_tipo == "carros" and ativo_id in carros_por_slug:
        try:
            from services.veiculos_service import recebimentos_da_empresa, contratos_por_empresa
            from datetime import timedelta
            hoje = date.today()

            # Contratos da empresa (lido direto do Excel)
            _VALOR_SEMANA = {"TSW": 1200, "SSW": 800, "STX": 800}
            try:
                _contratos = contratos_por_empresa(empresa_carros_sel["nome"])
            except Exception:
                _contratos = []

            # Última segunda-feira (pagamentos já devidos)
            _ultima_seg = hoje - timedelta(days=hoje.weekday())

            def _contar_segundas(inicio_dt, ate):
                dias = (7 - inicio_dt.weekday()) % 7
                primeira = inicio_dt + timedelta(days=dias)
                if primeira > ate:
                    return 0
                ultima = ate - timedelta(days=ate.weekday())
                return (ultima - primeira).days // 7 + 1

            _prefixos_empresa = set()
            for ev in empresa_carros_sel.get("veiculos", []):
                _prefixos_empresa.add((ev.get("placa") or "").replace("-","")[:3].upper())

            total_liq = 0.0
            for c in _contratos:
                placa = str(c.get("placa") or "").replace("-","").upper()
                pref = placa[:3]
                if pref not in _prefixos_empresa:
                    continue
                valor_sem = _VALOR_SEMANA.get(pref, 0)
                inicio_raw = c.get("inicio") or ""
                inicio_dt = None
                for fmt in ["%d/%m/%Y", "%Y-%m-%d"]:
                    try:
                        from datetime import datetime as _dt
                        inicio_dt = _dt.strptime(inicio_raw, fmt).date()
                        break
                    except Exception:
                        pass
                if not inicio_dt:
                    continue
                n_seg = _contar_segundas(inicio_dt, _ultima_seg)
                pago = n_seg * valor_sem
                total_liq += pago
                motoristas_recebimentos.append({
                    "cliente": c.get("cliente") or "—",
                    "placa": c.get("placa") or "—",
                    "inicio": inicio_dt.strftime("%d/%m/%Y") if inicio_dt else "—",
                    "valor_locacao": float(c.get("valor_locacao") or 0),
                    "n_semanas": n_seg,
                    "valor_semana": valor_sem,
                    "valor_pago": pago,
                })

            motoristas_recebimentos.sort(key=lambda x: -x["valor_pago"])
            if total_liq > 0:
                valor_liquido_recebido = round(total_liq, 2)

            # Total investido da empresa de carros
            _ti = empresa_carros_sel.get("total_investido")
            if _ti:
                total_investido = _ti

            # Recebido bruto por mês (agrupa taxa_valor / 0.15)
            por_placa = recebimentos_da_empresa(empresa_carros_sel)
            por_mes: dict = {}
            for rows in por_placa.values():
                for row in rows:
                    ym = str(row.get("data_semana") or "")[:7]
                    if not ym:
                        continue
                    v = float(row.get("taxa_valor") or 0) / 0.15
                    por_mes[ym] = por_mes.get(ym, 0.0) + v
            recebimentos_por_mes_carros = [
                {"mes": _MESES[int(ym.split("-")[1]) - 1], "valor": round(v, 2)}
                for ym, v in sorted(por_mes.items())
            ]

            # Status dos veículos (ativo vs vago/manutenção)
            _semana_ref = max(
                (r["data_semana"] for rows in por_placa.values() for r in rows if r.get("data_semana")),
                default=None,
            )
            def _norm_placa(p):
                return (p or "").upper().replace("-", "")
            # Índice normalizado para lookup independente de hífen
            _por_placa_norm = {_norm_placa(k): v for k, v in por_placa.items()}
            carros_veiculos_status = []
            for v in empresa_carros_sel.get("veiculos", []):
                recs = _por_placa_norm.get(_norm_placa(v["placa"]), [])
                ultima = max((r["data_semana"] for r in recs if r.get("data_semana")), default=None)
                # buscar locatário no contrato
                locatario = next(
                    (c.get("cliente") for c in _contratos if _norm_placa(c.get("placa", "")) == _norm_placa(v["placa"])),
                    None,
                )
                _tem_contrato = locatario is not None
                carros_veiculos_status.append({
                    "placa":          v["placa"],
                    "modelo":         v.get("modelo") or "—",
                    "ativo":          bool(_tem_contrato and ultima and _semana_ref and ultima == _semana_ref),
                    "locatario":      locatario if _tem_contrato else None,
                    "em_manutencao":  not _tem_contrato,
                    "ultima_semana":  ultima,
                })
            _status_by_placa_p = {_norm_placa(v["placa"]): v["ativo"] for v in carros_veiculos_status}
            for _mr in motoristas_recebimentos:
                _mr["ativo"] = _status_by_placa_p.get(_norm_placa(_mr["placa"]), False)

            # Rentabilidade estimada BYD
            _BYD = {
                "nome":               "BYD Dolphin Mini (Elétrico)",
                "aluguel_semanal":    1_200.0,
                "pct_investidor":     0.85,
                "seguro_anual":       5_109.94,
                "manutencao_mensal":  258.0,
                "depreciacao_aa_pct": 0.078,
                "investimento":       102_000.0,
                "cdi_aa":             0.144,
                "poupanca_aa":        0.0617,
            }
            _b = _BYD
            _bruta_sem  = round(_b["aluguel_semanal"] * _b["pct_investidor"], 2)
            _bruta_mes  = round(_bruta_sem * 52 / 12, 2)
            _seg_mes    = round(_b["seguro_anual"] / 12, 2)
            _man_mes    = _b["manutencao_mensal"]
            _dep_mes    = round(_b["investimento"] * _b["depreciacao_aa_pct"] / 12, 2)
            _liq_mes    = round(_bruta_mes - _seg_mes - _man_mes - _dep_mes, 2)
            _margem     = round(_liq_mes / _bruta_mes * 100, 1)
            _ret_aa     = round(_liq_mes * 12 / _b["investimento"] * 100, 1)
            _payback    = round(_b["investimento"] / _liq_mes, 1)
            _oc_eq_pct  = round((_seg_mes + _man_mes + _dep_mes) / _bruta_mes * 100, 1)
            _oc_eq_sem  = round(_oc_eq_pct / 100 * 52, 1)  # semanas/ano para cobrir custos fixos
            # Yield sem depreciação (retorno bruto sobre o investimento)
            _yield_aa   = round((_bruta_mes - _seg_mes - _man_mes) * 12 / _b["investimento"] * 100, 1)
            # Rentabilidade real considerando vacância atual
            _n_ativos   = sum(1 for v in carros_veiculos_status if v["ativo"])
            _n_total    = len(carros_veiculos_status)
            _n_inat     = _n_total - _n_ativos
            _liq_real_mes = _n_ativos * _liq_mes - _n_inat * (_seg_mes + _man_mes)
            _liq_real_aa  = round(_liq_real_mes * 12 / (_n_total * _b["investimento"]) * 100, 1) if _n_total else 0
            carros_rentabilidade = {
                "nome":             _b["nome"],
                "aluguel_semanal":  _b["aluguel_semanal"],
                "pct_investidor":   int(_b["pct_investidor"] * 100),
                "bruta_semanal":    _bruta_sem,
                "bruta_mensal":     _bruta_mes,
                "seguro_anual":     _b["seguro_anual"],
                "seguro_mensal":    _seg_mes,
                "manutencao":       _man_mes,
                "depreciacao_pct":  int(_b["depreciacao_aa_pct"] * 100 * 10) / 10,
                "depreciacao_mes":  _dep_mes,
                "liquida_mensal":   _liq_mes,
                "margem":           _margem,
                "retorno_aa":       _ret_aa,
                "yield_aa":         _yield_aa,
                "liquida_real_aa":  _liq_real_aa,
                "n_ativos":         _n_ativos,
                "n_total":          _n_total,
                "payback":          _payback,
                "oc_eq_pct":        _oc_eq_pct,
                "oc_eq_sem":        _oc_eq_sem,
                "vs_cdi":           round(_ret_aa - _b["cdi_aa"] * 100, 1),
                "vs_poupanca":      round(_ret_aa - _b["poupanca_aa"] * 100, 1),
                "cdi_aa_pct":       round(_b["cdi_aa"] * 100, 1),
                "poupanca_aa_pct":  round(_b["poupanca_aa"] * 100, 2),
            }
        except Exception:
            pass
    else:
        # Rendimento: créditos nas contas bancárias das usinas × cota
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

    # Faturas pendentes de carros (lido direto do Excel)
    faturas_carros = []
    lancamentos_carros = []
    chart_fluxo_carros_dates = []
    chart_fluxo_carros_pts = []
    if ativo_tipo == "carros" and empresa_carros_sel:
        try:
            from services.veiculos_service import contas_receber_carros_excel
            faturas_carros = contas_receber_carros_excel(empresa_carros_sel["nome"])
        except Exception:
            pass
        try:
            import json as _json
            from services.veiculos_service import listar_lancamentos_carros
            lancamentos_carros = listar_lancamentos_carros(empresa_carros_sel["nome"])
            for _l in lancamentos_carros:
                obs = _l.get("observacoes") or ""
                if obs.startswith("["):
                    try:
                        _l["splits"] = _json.loads(obs)
                    except Exception:
                        _l["splits"] = None
                else:
                    _l["splits"] = None
            if lancamentos_carros:
                from services.veiculos_service import calcular_saldo_em as _calc_saldo_p
                from datetime import date as _dtp, timedelta as _tdp
                _primeira_p = min(r["data_transacao"] for r in lancamentos_carros if r.get("data_transacao"))
                _ancora_p = (_dtp.fromisoformat(_primeira_p) - _tdp(days=1)).isoformat()
                _sc = _calc_saldo_p(empresa_carros_sel["nome"], _ancora_p)
                chart_fluxo_carros_dates.append(_ancora_p)
                chart_fluxo_carros_pts.append(_sc)
                _dia_s: dict = {}
                for _l in sorted(lancamentos_carros, key=lambda x: x.get("data_transacao") or ""):
                    _sc += float(_l.get("valor") or 0)
                    _dia_s[_l["data_transacao"]] = round(_sc, 2)
                for _d, _s in sorted(_dia_s.items()):
                    chart_fluxo_carros_dates.append(_d)
                    chart_fluxo_carros_pts.append(_s)
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
    # (mesmo padrão do admin: todas as abas permitidas são pré-carregadas
    # numa única página, e a troca de aba é só client-side; a DRE é a
    # exceção que precisa de reload real, ver click handler no template).
    from services.usina_service import (
        retorno_mensal_investidor, leituras_detalhadas,
        saldo_creditos_da_usina, pnl_da_usina,
        clientes_da_usina,
        listar_lancamentos, listar_contas_da_usina,
        calcular_kpis, leituras_da_usina, rentabilidade_investidor,
    )
    from services.benchmark_service import comparativo_benchmarks

    _is_usina = ativo_tipo == "usina"
    benchmarks_data = {}
    if _is_usina and ativo_id and "benchmarks" in home_tabs:
        _rb = rentabilidade_investidor(ativo_id)
        _rm = retorno_mensal_investidor(ativo_id)
        benchmarks_data = comparativo_benchmarks(
            float(_rb.get("capital") or 0),
            str(_rb.get("data_desembolso") or ""),
            _rm,
        )
    leituras_det   = leituras_detalhadas(ativo_id)       if _is_usina and ativo_id and "saldo_creditos" in home_tabs else []
    pnl            = pnl_da_usina(ativo_id)              if _is_usina and ativo_id and ("pnl" in home_tabs or "dre" in home_tabs) else []
    saldo_creditos = saldo_creditos_da_usina(ativo_id)   if _is_usina and ativo_id and "saldo_creditos" in home_tabs else []
    clientes       = clientes_da_usina(ativo_id)         if _is_usina and ativo_id and "clientes"       in home_tabs else []

    tem_extrato = bool(_is_usina and ativo_id and "extrato" in home_tabs)
    tem_dre     = bool(_is_usina and ativo_id and "dre"     in home_tabs)
    contas        = listar_contas_da_usina(ativo_id) if (tem_extrato or tem_dre) else []
    conta_id      = request.args.get("conta_id") or (contas[0]["id"] if contas else None)
    conta_atual   = next((c for c in contas if c["id"] == conta_id), contas[0] if contas else None)
    saldo_inicial = float(conta_atual["saldo_inicial"] or 0) if conta_atual else 0.0
    lancamentos   = listar_lancamentos(ativo_id, conta_id=conta_id) if tem_extrato else []

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

    if request.args.get("kpi_mes"):
        kpi_mes = request.args.get("kpi_mes")
    else:
        _datas = [l["data_transacao"][:7] for l in lancamentos if l.get("data_transacao")]
        kpi_mes = max(_datas) if _datas else date.today().strftime("%Y-%m")
    leituras_raw   = leituras_da_usina(ativo_id) if tem_dre else []
    _num_ativos    = sum(1 for c in clientes if c.get("status") == "Ativo")
    kpis = calcular_kpis(ativo_id, kpi_mes, lancamentos, leituras_raw, pnl, _num_ativos) if tem_dre else {}

    dre_secoes = dre_valores = dre_lancs = dre_meses = dre_percentuais = dre_naturezas = None
    dre_mes_ini = dre_mes_fim = ""
    if tem_dre and tab == "dre":
        from services.dre_service import listar_secoes_dre, calcular_dre
        _ano_dre = date.today().year
        dre_mes_ini = request.args.get("dre_mes_ini") or f"{_ano_dre}-01"
        dre_mes_fim = request.args.get("dre_mes_fim") or f"{_ano_dre}-06"
        dre_secoes  = listar_secoes_dre()
        _dre        = calcular_dre(ativo_id, dre_mes_ini, dre_mes_fim)
        dre_valores = _dre["valores"]
        dre_lancs   = _dre["lancamentos"]
        dre_meses   = _dre["meses"]
        dre_percentuais = _dre["percentuais"]
        dre_naturezas   = _dre["naturezas"]

    return render_template(
        "portal/home.html",
        usinas=usinas,
        all_usinas=all_usinas,
        ativos=ativos,
        empresas_carros=empresas_carros,
        empresa_carros_sel=empresa_carros_sel,
        valor_liquido_recebido=valor_liquido_recebido,
        motoristas_recebimentos=motoristas_recebimentos,
        recebimentos_por_mes_carros=recebimentos_por_mes_carros,
        carros_veiculos_status=carros_veiculos_status,
        carros_rentabilidade=carros_rentabilidade,
        ativo_tipo=ativo_tipo,
        faturas_carros=faturas_carros,
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
        benchmarks=benchmarks_data,
        leituras_det=leituras_det,
        pnl=pnl,
        saldo_creditos=saldo_creditos,
        clientes=clientes,
        lancamentos=lancamentos,
        contas=contas,
        conta_id=conta_id,
        saldo_inicial=saldo_inicial,
        chart_fluxo_dates=chart_fluxo_dates,
        chart_fluxo_pts=chart_fluxo_pts,
        chart_meses=chart_meses,
        lancamentos_carros=lancamentos_carros,
        chart_fluxo_carros_dates=chart_fluxo_carros_dates,
        chart_fluxo_carros_pts=chart_fluxo_carros_pts,
        kpis=kpis,
        kpi_mes=kpi_mes,
        dre_secoes=dre_secoes,
        dre_valores=dre_valores,
        dre_lancs=dre_lancs,
        dre_meses=dre_meses,
        dre_percentuais=dre_percentuais,
        dre_naturezas=dre_naturezas,
        dre_mes_ini=dre_mes_ini,
        dre_mes_fim=dre_mes_fim,
        dados_clientes_carros=__import__('services.veiculos_service', fromlist=['dados_clientes_cons']).dados_clientes_cons(),
        naturezas_carros=__import__('services.veiculos_service', fromlist=['listar_naturezas_carros']).listar_naturezas_carros(),
    )


@portal_bp.route("/carros")
@requer_login
def carros():
    from services.veiculos_service import listar_empresas_veiculos, recebimentos_da_empresa
    u = usuario_logado()
    perms     = u.get("permissions", [])
    all_perms = "all" in perms

    if not all_perms and "carros" not in perms:
        abort(403)

    usina_ids = u.get("usina_ids", [])
    todas = listar_empresas_veiculos()
    # Filtra só as empresas que o investidor tem acesso (pelo slug)
    if usina_ids:
        empresas = [e for e in todas if e.get("slug") in usina_ids]
    else:
        empresas = todas

    return render_template(
        "portal/carros.html",
        empresas_veiculos=empresas,
        usuario=u,
    )


@portal_bp.route("/carros/classificar-lancamento", methods=["POST"])
@requer_login
def classificar_lancamento_carro():
    from flask import request as _req, jsonify
    from services.veiculos_service import classificar_lancamento_carros
    dados = _req.get_json() or {}
    return jsonify(classificar_lancamento_carros(dados.get("id"), dados.get("natureza"), dados.get("splits")))


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


@portal_bp.route("/clientes/upload-pdf", methods=["POST"])
@requer_login
def upload_pdf_cliente():
    from flask import jsonify
    from services.veiculos_service import upload_pdf_cliente as _upload
    ref_id   = request.form.get("ref_id", "sem-ref")
    arquivo  = request.files.get("arquivo")
    if not arquivo:
        return jsonify({"ok": False, "erro": "Nenhum arquivo enviado"})
    resultado = _upload(ref_id, arquivo.filename, arquivo.read(), arquivo.mimetype)
    return jsonify(resultado)
