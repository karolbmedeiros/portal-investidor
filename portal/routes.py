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
    ativo_id = request.args.get("ativo_id", "")
    ativo_selecionado = next((a for a in ativos if a["id"] == ativo_id), None)

    # Persiste seleção na sessão (igual ao admin)
    if ativo_selecionado:
        _sess["inv_ativo_id"]   = ativo_selecionado["id"]
        _sess["inv_ativo_nome"] = ativo_selecionado["nome"]
    elif not ativo_id:
        _sess.pop("inv_ativo_id",   None)
        _sess.pop("inv_ativo_nome", None)

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
    )


@portal_bp.route("/usina/<usina_id>")
@requer_login
def usina_detalhe(usina_id):
    from services.usina_service import (
        buscar_usina, usinas_do_investidor,
        distribuicoes_da_usina, leituras_da_usina,
        pnl_da_usina, documentos_da_usina,
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

    minha_part = next((p for p in partic if p["usina_id"] == usina_id), None)

    return render_template(
        "portal/usina_detalhe.html",
        usina=buscar_usina(usina_id),
        minha_participacao=minha_part,
        distribuicoes=distribuicoes_da_usina(usina_id),
        leituras=leituras_da_usina(usina_id),
        pnl=pnl_da_usina(usina_id),
        documentos=documentos_da_usina(usina_id),
        usuario=u,
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
