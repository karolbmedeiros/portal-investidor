from functools import wraps
from flask import redirect, url_for, abort
from services.auth_service import usuario_logado, is_admin


def requer_login(f):
    """Qualquer usuário autenticado pode acessar."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not usuario_logado():
            return redirect(url_for("auth.login_page"))
        return f(*args, **kwargs)
    return decorated


def requer_admin(f):
    """Apenas admins. Investidores recebem 403."""
    @wraps(f)
    def decorated(*args, **kwargs):
        u = usuario_logado()
        if not u:
            return redirect(url_for("auth.login_page"))
        if u["role"] != "admin":
            abort(403)
        return f(*args, **kwargs)
    return decorated
