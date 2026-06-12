from flask import Blueprint, render_template, request, redirect, url_for, flash
from services.auth_service import (
    login, logout, usuario_logado,
    solicitar_reset, ativar_conta,
)

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


@auth_bp.route("/login", methods=["GET", "POST"])
def login_page():
    if usuario_logado():
        return _redirecionar_por_role()

    erro = None
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        senha = request.form.get("senha", "")
        res = login(email, senha)
        if res["ok"]:
            return _redirecionar_por_role()
        erro = res["erro"]

    return render_template("auth/login.html", erro=erro)


@auth_bp.route("/logout")
def logout_page():
    logout()
    return redirect(url_for("auth.login_page"))


@auth_bp.route("/reset", methods=["GET", "POST"])
def reset_page():
    if usuario_logado():
        return _redirecionar_por_role()

    enviado = False
    erro = None
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        res = solicitar_reset(email)
        if res["ok"]:
            enviado = True
        else:
            erro = res["erro"]

    return render_template("auth/reset.html", enviado=enviado, erro=erro)


@auth_bp.route("/ativar", methods=["GET", "POST"])
def ativar_page():
    token = request.args.get("token", "")
    erro = None
    sucesso = False

    if request.method == "POST":
        senha = request.form.get("senha", "")
        confirmacao = request.form.get("confirmacao", "")
        if senha != confirmacao:
            erro = "As senhas não coincidem."
        elif len(senha) < 8:
            erro = "A senha deve ter pelo menos 8 caracteres."
        else:
            res = ativar_conta(token, senha)
            if res["ok"]:
                sucesso = True
            else:
                erro = res["erro"]

    return render_template("auth/ativar.html", token=token, erro=erro, sucesso=sucesso)


def _redirecionar_por_role():
    u = usuario_logado()
    if u and u["role"] == "admin":
        return redirect(url_for("admin.dashboard"))
    return redirect(url_for("portal.home"))
