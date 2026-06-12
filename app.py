import os
from flask import Flask, redirect, url_for, render_template
from config import Config


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    # ── Registrar blueprints ──────────────────────────────────────────────────
    from auth.routes import auth_bp
    from admin.routes import admin_bp
    from portal.routes import portal_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(portal_bp)

    # ── Raiz ──────────────────────────────────────────────────────────────────
    @app.route("/")
    def index():
        from services.auth_service import usuario_logado
        u = usuario_logado()
        if not u:
            return redirect(url_for("auth.login_page"))
        if u["role"] == "admin":
            return redirect(url_for("admin.dashboard"))
        return redirect(url_for("portal.home"))

    # ── Erros ─────────────────────────────────────────────────────────────────
    @app.errorhandler(403)
    def forbidden(e):
        return render_template("erros/403.html"), 403

    @app.errorhandler(404)
    def not_found(e):
        return render_template("erros/404.html"), 404

    @app.errorhandler(500)
    def server_error(e):
        return render_template("erros/500.html"), 500

    # ── Filtros Jinja2 ────────────────────────────────────────────────────────
    @app.template_filter("brl")
    def brl_filter(value):
        try:
            v = float(value or 0)
            formatted = f"{abs(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            return f"R$ {formatted}" if v >= 0 else f"- R$ {formatted}"
        except (TypeError, ValueError):
            return "R$ 0,00"

    @app.template_filter("fmtdata")
    def fmtdata_filter(value):
        try:
            from datetime import date
            parts = str(value)[:10].split("-")
            return f"{parts[2]}/{parts[1]}/{parts[0]}"
        except Exception:
            return str(value)

    @app.template_filter("to_date")
    def to_date_filter(value):
        from datetime import date
        try:
            y, m, d = str(value)[:10].split("-")
            return date(int(y), int(m), int(d))
        except Exception:
            return date.today()

    # ── Context processor: injeta usuário e today em todos os templates ───────
    @app.context_processor
    def inject_usuario():
        from datetime import date
        from services.auth_service import usuario_logado, preview_investidor_id, is_admin
        u = usuario_logado()
        return {
            "usuario": u,
            "em_preview": is_admin() and preview_investidor_id() is not None,
            "today": date.today(),
        }

    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_ENV") == "development", port=5001)
