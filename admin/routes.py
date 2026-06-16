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

    # ── Dados de carros ──────────────────────────────────────────────────────
    _MESES_ADM = ["jan","fev","mar","abr","mai","jun","jul","ago","set","out","nov","dez"]
    empresa_carros_sel       = None
    valor_liquido_recebido   = None
    motoristas_recebimentos  = []
    recebimentos_por_mes_carros = []
    carros_veiculos_status   = []
    carros_rentabilidade     = None
    faturas_carros           = []
    lancamentos_carros       = []
    _tab_carro               = "visao_geral"

    if ativo_id and ativo_tipo == "carro":
        _emp_c = next((e for e in all_carros if e["slug"] == ativo_id), None)
        if _emp_c:
            empresa_carros_sel = _emp_c
            _ti = _emp_c.get("total_investido")
            if _ti:
                total_investido = _ti
            try:
                from services.veiculos_service import recebimentos_da_empresa, contas_receber_empresa
                from services.supabase_client import get_financeiro_client
                from datetime import timedelta, date as _date_c
                _VALOR_SEMANA = {"TSW": 1200, "SSW": 800, "STX": 800}
                from services.veiculos_service import contratos_por_empresa
                _contratos = contratos_por_empresa(_emp_c["nome"])
                _hoje_c      = _date_c.today()
                _ultima_seg  = _hoje_c - timedelta(days=_hoje_c.weekday())
                def _contar_seg(ini, ate):
                    dias = (7 - ini.weekday()) % 7
                    prim = ini + timedelta(days=dias)
                    if prim > ate: return 0
                    ult  = ate - timedelta(days=ate.weekday())
                    return (ult - prim).days // 7 + 1
                _pref_emp = {(v.get("placa") or "").replace("-","")[:3].upper()
                             for v in _emp_c.get("veiculos", [])}
                _total_liq = 0.0
                from datetime import datetime as _dt_c
                for _c in _contratos:
                    _placa_c = str(_c.get("placa") or "").replace("-","").upper()
                    _pref_c  = _placa_c[:3]
                    if _pref_c not in _pref_emp: continue
                    _vsem = _VALOR_SEMANA.get(_pref_c, 0)
                    _ini_raw = _c.get("inicio") or ""
                    _ini_dt  = None
                    for _fmt in ["%d/%m/%Y", "%Y-%m-%d"]:
                        try: _ini_dt = _dt_c.strptime(_ini_raw, _fmt).date(); break
                        except Exception: pass
                    if not _ini_dt: continue
                    _nseg = _contar_seg(_ini_dt, _ultima_seg)
                    _pago = _nseg * _vsem
                    _total_liq += _pago
                    motoristas_recebimentos.append({
                        "cliente":      _c.get("cliente") or "—",
                        "placa":        _c.get("placa")   or "—",
                        "inicio":       _ini_dt.strftime("%d/%m/%Y"),
                        "valor_locacao": float(_c.get("valor_locacao") or 0),
                        "n_semanas":    _nseg,
                        "valor_semana": _vsem,
                        "valor_pago":   _pago,
                    })
                motoristas_recebimentos.sort(key=lambda x: -x["valor_pago"])
                if _total_liq > 0:
                    valor_liquido_recebido = round(_total_liq, 2)
                _por_placa = recebimentos_da_empresa(_emp_c)
                _por_mes_c: dict = {}
                for _rows in _por_placa.values():
                    for _row in _rows:
                        _ym = str(_row.get("data_semana") or "")[:7]
                        if not _ym: continue
                        _por_mes_c[_ym] = _por_mes_c.get(_ym, 0.0) + float(_row.get("taxa_valor") or 0) / 0.15
                recebimentos_por_mes_carros = [
                    {"mes": _MESES_ADM[int(_ym.split("-")[1]) - 1], "valor": round(_v, 2)}
                    for _ym, _v in sorted(_por_mes_c.items())
                ]
                _sem_ref = max(
                    (_r["data_semana"] for _rs in _por_placa.values() for _r in _rs if _r.get("data_semana")),
                    default=None,
                )
                def _norm(p): return (p or "").upper().replace("-", "")
                _pp_norm = {_norm(k): v for k, v in _por_placa.items()}
                for _veh in _emp_c.get("veiculos", []):
                    _recs = _pp_norm.get(_norm(_veh["placa"]), [])
                    _ult  = max((_r["data_semana"] for _r in _recs if _r.get("data_semana")), default=None)
                    _loc  = next((_c.get("cliente") for _c in _contratos
                                  if _norm(_c.get("placa","")) == _norm(_veh["placa"])), None)
                    _tem_contrato = _loc is not None
                    _ativo = bool(_tem_contrato and _ult and _sem_ref and _ult == _sem_ref)
                    carros_veiculos_status.append({
                        "placa":   _veh["placa"],
                        "modelo":  _veh.get("modelo") or "—",
                        "ativo":   _ativo,
                        "locatario": _loc if _tem_contrato else None,
                        "em_manutencao": not _tem_contrato,
                        "ultima_semana": _ult,
                    })
                _status_by_placa = {_norm(v["placa"]): v["ativo"] for v in carros_veiculos_status}
                for _mr in motoristas_recebimentos:
                    _mr["ativo"] = _status_by_placa.get(_norm(_mr["placa"]), False)

                # Complementa com ex-motoristas presentes no extrato mas não na planilha
                import re as _re
                _pat_cob = _re.compile(r'cobran[çc]a recebida - fatura nr\.\s*\d+\s+(.+)', _re.I)
                _CONV = ["GELO E GELA", "JUAN E IVAN"]
                _nomes_ativos = {_mr["cliente"].upper() for _mr in motoristas_recebimentos}
                _extras: dict = {}
                for _lc in lancamentos_carros:
                    if _lc.get("_convenio") or _lc.get("tipo") != "credito":
                        continue
                    _desc = (_lc.get("descricao") or _lc.get("descricao_original") or "")
                    _m = _pat_cob.match(_desc)
                    if not _m:
                        continue
                    _nome = _m.group(1).strip()
                    if any(c in _nome.upper() for c in _CONV):
                        continue
                    if _nome.upper() in _nomes_ativos:
                        continue
                    _extras[_nome] = _extras.get(_nome, 0.0) + float(_lc.get("valor") or 0)
                for _nome, _total in _extras.items():
                    motoristas_recebimentos.append({
                        "cliente":      _nome,
                        "placa":        "—",
                        "inicio":       "—",
                        "valor_locacao": 0,
                        "n_semanas":    0,
                        "valor_semana": 0,
                        "valor_pago":   round(_total, 2),
                        "ativo":        False,
                    })
                motoristas_recebimentos.sort(key=lambda x: -x["valor_pago"])
                _BYD = {"nome":"BYD Dolphin Mini (Elétrico)","aluguel_semanal":1200.0,
                        "pct_investidor":0.85,"seguro_anual":5109.94,"manutencao_mensal":258.0,
                        "depreciacao_aa_pct":0.078,"investimento":102000.0,"cdi_aa":0.144,"poupanca_aa":0.0617}
                _b = _BYD
                _bsem = round(_b["aluguel_semanal"]*_b["pct_investidor"],2)
                _bmes = round(_bsem*52/12,2)
                _smes = round(_b["seguro_anual"]/12,2)
                _mmes = _b["manutencao_mensal"]
                _dmes = round(_b["investimento"]*_b["depreciacao_aa_pct"]/12,2)
                _lmes = round(_bmes-_smes-_mmes-_dmes,2)
                _n_at = sum(1 for _v in carros_veiculos_status if _v["ativo"])
                _n_to = len(carros_veiculos_status)
                _n_in = _n_to - _n_at
                _lreal = _n_at*_lmes - _n_in*(_smes+_mmes)
                carros_rentabilidade = {
                    "nome": _b["nome"], "aluguel_semanal": _b["aluguel_semanal"],
                    "pct_investidor": int(_b["pct_investidor"]*100),
                    "bruta_semanal": _bsem, "bruta_mensal": _bmes,
                    "seguro_anual": _b["seguro_anual"], "seguro_mensal": _smes,
                    "manutencao": _mmes,
                    "depreciacao_pct": int(_b["depreciacao_aa_pct"]*100*10)/10,
                    "depreciacao_mes": _dmes, "liquida_mensal": _lmes,
                    "margem":       round(_lmes/_bmes*100, 1),
                    "retorno_aa":   round(_lmes*12/_b["investimento"]*100, 1),
                    "yield_aa":     round((_bmes-_smes-_mmes)*12/_b["investimento"]*100, 1),
                    "liquida_real_aa": round(_lreal*12/(_n_to*_b["investimento"])*100,1) if _n_to else 0,
                    "n_ativos": _n_at, "n_total": _n_to,
                    "payback":  round(_b["investimento"]/_lmes,1),
                    "oc_eq_pct": round((_smes+_mmes+_dmes)/_bmes*100,1),
                    "oc_eq_sem": round((_smes+_mmes+_dmes)/_bmes*52,1),
                    "vs_cdi":     round(_lmes*12/_b["investimento"]*100 - _b["cdi_aa"]*100, 1),
                    "vs_poupanca":round(_lmes*12/_b["investimento"]*100 - _b["poupanca_aa"]*100, 1),
                    "cdi_aa_pct": round(_b["cdi_aa"]*100,1),
                    "poupanca_aa_pct": round(_b["poupanca_aa"]*100,2),
                }
                from services.veiculos_service import contas_receber_carros_excel
                faturas_carros = contas_receber_carros_excel(_emp_c["nome"])
            except Exception:
                pass

        # Lançamentos bancários da empresa de carros
        try:
            import json as _json
            from services.veiculos_service import listar_lancamentos_carros
            lancamentos_carros = listar_lancamentos_carros(_emp_c["nome"] if _emp_c else "")

            def _sugerir_natureza(l):
                if l.get("observacoes") or l.get("_convenio"):
                    return None
                desc = (l.get("descricao") or l.get("descricao_original") or "").upper()
                tipo = l.get("tipo", "")
                if tipo == "credito" and "COBRAN" in desc and "RECEBIDA" in desc:
                    return "Locação"
                if tipo == "debito":
                    if "ATIVUZ DO BRASIL" in desc:
                        return "Taxa administrativa"
                    if "LUZ DIVINA LOCACAO" in desc or "LUZ DIVINA LOCA" in desc:
                        return "Transferência"
                    if "SEGURO BYD" in desc or ("SEGURO" in desc and "TSW" in desc):
                        return "Seguro"
                    if "WHATSAPP" in desc or "NOTIFICA" in desc:
                        return "Taxa Asaas"
                    if "TAXA DO PIX" in desc or "TAXA DE PIX" in desc or "TAXA DE BOLETO" in desc:
                        return "Taxa Asaas"
                return None

            for _l in lancamentos_carros:
                obs = _l.get("observacoes") or ""
                if obs.startswith("["):
                    try:
                        _l["splits"] = _json.loads(obs)
                    except Exception:
                        _l["splits"] = None
                else:
                    _l["splits"] = None
                _l["sugestao_natureza"] = _sugerir_natureza(_l)
        except Exception:
            lancamentos_carros = []

        _tab_carro = request.args.get("tab", "visao_geral")
        if _tab_carro not in ("visao_geral","retorno_mensal","clientes","extrato"):
            _tab_carro = "visao_geral"

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

    # ── Dados analíticos da usina (tabs inline) ──────────────────────────────
    usina_obj = None
    tab = _tab_carro if ativo_tipo == "carro" else "visao_geral"
    contas = conta_id = conta_atual = kpis = kpi_mes = pnl_data = clientes_data = categorias = None
    saldo_inicial = 0.0
    chart_meses = []
    chart_fluxo_dates = []
    chart_fluxo_pts = []

    # Gráfico de fluxo para carros — um ponto por dia (saldo acumulado ao fim do dia)
    # Ponto inicial: saldo real da conta no dia anterior ao primeiro lançamento carros
    if lancamentos_carros:
        from services.veiculos_service import calcular_saldo_em as _calc_saldo
        from datetime import date as _dt, timedelta as _td
        _emp_nome_c = _emp_c["nome"] if _emp_c else ""
        _primeira_data = min(r["data_transacao"] for r in lancamentos_carros if r.get("data_transacao"))
        _ancora = (_dt.fromisoformat(_primeira_data) - _td(days=1)).isoformat()
        _saldo_c = _calc_saldo(_emp_nome_c, _ancora)
        chart_fluxo_dates.append(_ancora)
        chart_fluxo_pts.append(_saldo_c)
        _dia_saldo: dict = {}
        for _l in sorted(lancamentos_carros, key=lambda x: x.get("data_transacao") or ""):
            _saldo_c += float(_l.get("valor") or 0)
            _dia_saldo[_l["data_transacao"]] = round(_saldo_c, 2)
        for _d, _s in sorted(_dia_saldo.items()):
            chart_fluxo_dates.append(_d)
            chart_fluxo_pts.append(_s)

    leituras_data = []
    lancamentos_data = []
    retorno_mensal_data = []
    rentabilidade_data = {}
    benchmarks_data = {}
    leituras_det_data = []
    saldo_creditos_data = []

    if ativo_id and ativo_tipo == "usina":
        from datetime import date as _date2
        from services.usina_service import (
            clientes_da_usina, listar_contas_da_usina, listar_lancamentos,
            leituras_da_usina, pnl_da_usina, calcular_kpis, listar_categorias,
            retorno_mensal_investidor, leituras_detalhadas, saldo_creditos_da_usina,
            auto_conciliar_neutros, rentabilidade_investidor,
        )
        usina_obj = next((u for u in all_usinas if u["id"] == ativo_id), None)
        auto_conciliar_neutros(ativo_id)
        contas = listar_contas_da_usina(ativo_id)
        conta_id = request.args.get("conta_id") or (contas[0]["id"] if contas else None)
        conta_atual = next((c for c in contas if c["id"] == conta_id), contas[0] if contas else None)
        saldo_inicial = float(conta_atual["saldo_inicial"] or 0) if conta_atual else 0.0
        lancamentos_data = listar_lancamentos(ativo_id, conta_id=conta_id)
        _lanc_op = sorted(
            [l for l in lancamentos_data if l.get("data_transacao") and not l.get("_neutro")],
            key=lambda l: (l["data_transacao"], 0 if l.get("tipo") == "credito" else 1)
        )
        _acc, _dia_acc = saldo_inicial, {}
        for _lx in _lanc_op:
            _v = abs(_lx.get("valor") or 0)
            _acc += _v if _lx.get("tipo") == "credito" else -_v
            _dia_acc[_lx["data_transacao"][:10]] = round(_acc, 2)
        if _dia_acc:
            chart_fluxo_dates = ["Saldo ini."] + list(_dia_acc.keys())
            chart_fluxo_pts   = [round(saldo_inicial, 2)] + list(_dia_acc.values())
        chart_meses = sorted({
            l["data_transacao"][:7] for l in lancamentos_data
            if l.get("data_transacao") and not l.get("_neutro")
        })
        leituras_data   = leituras_da_usina(ativo_id)
        pnl_data        = pnl_da_usina(ativo_id)
        clientes_data   = clientes_da_usina(ativo_id)
        categorias      = listar_categorias()
        num_ativos      = sum(1 for c in clientes_data if c.get("status") == "Ativo")
        datas_lanc = [l["data_transacao"][:7] for l in lancamentos_data if l.get("data_transacao")]
        kpi_mes = request.args.get("kpi_mes") or (max(datas_lanc) if datas_lanc else _date2.today().strftime("%Y-%m"))
        kpis            = calcular_kpis(ativo_id, kpi_mes, lancamentos_data, leituras_data, pnl_data, num_ativos)
        from services.benchmark_service import comparativo_benchmarks
        retorno_mensal_data = retorno_mensal_investidor(ativo_id)
        rentabilidade_data  = rentabilidade_investidor(ativo_id)
        benchmarks_data = comparativo_benchmarks(
            float(rentabilidade_data.get("capital") or 0),
            str(rentabilidade_data.get("data_desembolso") or ""),
            retorno_mensal_data,
        )
        leituras_det_data   = leituras_detalhadas(ativo_id)
        saldo_creditos_data = saldo_creditos_da_usina(ativo_id)
        _valid_tabs = ("visao_geral","socios","clientes","extrato","dre","retorno_mensal","benchmarks","pnl","saldo_creditos")
        tab = request.args.get("tab", "visao_geral")
        if tab not in _valid_tabs:
            tab = "visao_geral"

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
        usina=usina_obj,
        tab=tab,
        contas=contas or [],
        conta_id=conta_id,
        saldo_inicial=saldo_inicial,
        chart_meses=chart_meses,
        chart_fluxo_dates=chart_fluxo_dates,
        chart_fluxo_pts=chart_fluxo_pts,
        lancamentos=lancamentos_data,
        leituras=leituras_data,
        pnl=pnl_data or [],
        clientes=clientes_data or [],
        categorias=categorias or [],
        kpis=kpis or {},
        kpi_mes=kpi_mes or "",
        retorno_mensal=retorno_mensal_data,
        rentabilidade=rentabilidade_data,
        benchmarks=benchmarks_data,
        leituras_det=leituras_det_data,
        saldo_creditos=saldo_creditos_data,
        participacoes=_parts if (ativo_id and ativo_tipo == "usina") else [],
        empresa_carros_sel=empresa_carros_sel,
        valor_liquido_recebido=valor_liquido_recebido,
        motoristas_recebimentos=motoristas_recebimentos,
        recebimentos_por_mes_carros=recebimentos_por_mes_carros,
        carros_veiculos_status=carros_veiculos_status,
        carros_rentabilidade=carros_rentabilidade,
        faturas_carros=faturas_carros,
        lancamentos_carros=lancamentos_carros,
        dados_clientes_carros=__import__('services.veiculos_service', fromlist=['dados_clientes_cons']).dados_clientes_cons(),
        naturezas_carros=__import__('services.veiculos_service', fromlist=['listar_naturezas_carros']).listar_naturezas_carros(),
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
        retorno_mensal_investidor, leituras_detalhadas, saldo_creditos_da_usina,
        rentabilidade_investidor,
    )
    from services.benchmark_service import comparativo_benchmarks
    usina = buscar_usina(usina_id)
    if not usina:
        abort(404)

    tab = request.args.get("tab", "socios")
    _valid_tabs = ("socios", "clientes", "extrato", "dre",
                   "retorno_mensal", "benchmarks", "energia", "pnl", "saldo_creditos")
    if tab not in _valid_tabs:
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

    retorno_mensal = retorno_mensal_investidor(usina_id) if tab in ("retorno_mensal", "benchmarks") else []
    rentabilidade  = rentabilidade_investidor(usina_id)  if tab in ("retorno_mensal", "benchmarks") else {}
    _bm_rm = retorno_mensal or retorno_mensal_investidor(usina_id)
    _bm_rb = rentabilidade  or rentabilidade_investidor(usina_id)
    benchmarks = comparativo_benchmarks(
        float(_bm_rb.get("capital") or 0),
        str(_bm_rb.get("data_desembolso") or ""),
        _bm_rm,
    ) if tab == "benchmarks" else {}
    leituras_det   = leituras_detalhadas(usina_id)       if tab == "energia"        else []
    saldo_creditos = saldo_creditos_da_usina(usina_id)   if tab == "saldo_creditos" else []

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
        retorno_mensal=retorno_mensal,
        rentabilidade=rentabilidade,
        benchmarks=benchmarks,
        leituras_det=leituras_det,
        saldo_creditos=saldo_creditos,
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
    from services.veiculos_service import listar_empresas_veiculos, listar_naturezas_carros
    return render_template(
        "admin/configuracoes.html",
        investidores=auth_service.listar_usuarios_com_acesso(),
        usinas=listar_usinas(),
        empresas_veiculos=listar_empresas_veiculos(),
        categorias=listar_categorias(),
        cats_em_uso=categorias_em_uso(),
        naturezas_carros=listar_naturezas_carros(),
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


@admin_bp.route("/configuracoes/upload-planilha", methods=["POST"])
@requer_admin
def upload_planilha_carros():
    from services.veiculos_service import upload_planilha
    arquivo = request.files.get("planilha")
    if not arquivo or not arquivo.filename:
        flash("Selecione um arquivo.", "erro")
        return redirect(url_for("admin.configuracoes") + "#planilhas-carros")
    res = upload_planilha(arquivo.filename, arquivo.read())
    flash(f"'{arquivo.filename}' atualizado." if res["ok"] else f"Erro: {res.get('erro')}", "sucesso" if res["ok"] else "erro")
    return redirect(url_for("admin.configuracoes") + "#planilhas-carros")


@admin_bp.route("/configuracoes/natureza-carros/nova", methods=["POST"])
@requer_admin
def natureza_carros_nova():
    from services.veiculos_service import adicionar_natureza_carros
    nome = request.form.get("nome", "").strip()
    tipo = request.form.get("tipo", "saida")
    if not nome:
        flash("Informe o nome da natureza.", "erro")
        return redirect(url_for("admin.configuracoes") + "#naturezas-carros")
    res = adicionar_natureza_carros(nome, tipo)
    flash("Natureza adicionada." if res["ok"] else f"Erro: {res.get('erro')}", "sucesso" if res["ok"] else "erro")
    return redirect(url_for("admin.configuracoes") + "#naturezas-carros")


@admin_bp.route("/configuracoes/natureza-carros/excluir", methods=["POST"])
@requer_admin
def natureza_carros_excluir():
    from services.veiculos_service import remover_natureza_carros
    nome = request.form.get("nome", "").strip()
    res = remover_natureza_carros(nome)
    flash("Natureza removida." if res["ok"] else f"Erro: {res.get('erro')}", "sucesso" if res["ok"] else "erro")
    return redirect(url_for("admin.configuracoes") + "#naturezas-carros")


@admin_bp.route("/configuracoes/novo-usuario", methods=["POST"])
@requer_admin
def novo_usuario():
    username    = request.form.get("username", "").strip()
    senha       = request.form.get("senha", "").strip()
    nome        = request.form.get("nome", "").strip()
    tipo        = request.form.get("tipo", "investidor")  # "admin" ou "investidor"
    usina_ids   = request.form.getlist("usina_ids")
    permissions = request.form.getlist("permissions")

    if not all([username, senha, nome]):
        flash("Preencha todos os campos.", "erro")
        return redirect(url_for("admin.configuracoes"))

    if tipo == "admin":
        res = auth_service.criar_usuario_admin(username, senha, nome, [], [], tipo="admin")
    else:
        if not permissions:
            permissions = ["visao_geral", "socios"]
        res = auth_service.criar_usuario_admin(username, senha, nome, usina_ids, permissions, tipo="investidor")

    flash(
        f"Usuário '{username}@{tipo}' criado." if res["ok"] else f"Erro: {res['erro']}",
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


# ── Upload PDF de cliente ─────────────────────────────────────────────────────

@admin_bp.route("/clientes/upload-pdf", methods=["POST"])
@requer_admin
def upload_pdf_cliente():
    from flask import jsonify
    from services.veiculos_service import upload_pdf_cliente as _upload
    ref_id   = request.form.get("ref_id", "sem-ref")
    arquivo  = request.files.get("arquivo")
    if not arquivo:
        return jsonify({"ok": False, "erro": "Nenhum arquivo enviado"})
    resultado = _upload(ref_id, arquivo.filename, arquivo.read(), arquivo.mimetype)
    return jsonify(resultado)


# ── Classificar lançamento de carro (natureza) ───────────────────────────────

@admin_bp.route("/carros/classificar-lancamento", methods=["POST"])
@requer_admin
def classificar_lancamento_carro():
    from flask import jsonify
    from services.veiculos_service import classificar_lancamento_carros
    dados = request.get_json(force=True, silent=True) or {}
    resultado = classificar_lancamento_carros(dados.get("id"), dados.get("natureza"), dados.get("splits"))
    return jsonify(resultado)


# ── Salvar dados do cliente ───────────────────────────────────────────────────

@admin_bp.route("/clientes/salvar", methods=["POST"])
@requer_admin
def salvar_cliente():
    from flask import jsonify
    from services.veiculos_service import salvar_dados_cliente
    dados = request.get_json(force=True, silent=True) or {}
    resultado = salvar_dados_cliente(dados)
    return jsonify(resultado)
