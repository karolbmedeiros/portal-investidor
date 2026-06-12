from flask import Blueprint, render_template, request, redirect, url_for, abort
from middleware.auth_guard import requer_login
from services.auth_service import usuario_logado, is_admin, preview_investidor_id

portal_bp = Blueprint("portal", __name__, url_prefix="/portal")


@portal_bp.route("/")
@portal_bp.route("/home")
@requer_login
def home():
    from services.usina_service import usinas_do_usuario, usinas_do_investidor
    u = usuario_logado()
    usina_ids = u.get("usina_ids", [])

    partic = usinas_do_investidor(u["id"])
    cotas = {p["usina_id"]: p.get("percentual", 0) for p in partic}

    if usina_ids:
        usinas = usinas_do_usuario(usina_ids)
    else:
        usinas = [p["usinas"] for p in partic if p.get("usinas")]

    return render_template("portal/home.html", usinas=usinas, cotas=cotas, usuario=u)


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
