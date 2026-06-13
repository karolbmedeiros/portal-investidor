from flask import session
from services.supabase_client import get_client, get_service_client


def _role_do_email(email: str) -> str:
    """Determina role pelo domínio: @admin → admin, qualquer outro → investidor."""
    return "admin" if email.lower().endswith("@admin") else "investidor"


def login(email: str, password: str) -> dict:
    """
    Autentica com Supabase Auth.
    Aceita username simples ou e-mail completo com @admin / @investidor.
    Se apenas username, tenta @admin primeiro, depois @investidor.
    """
    try:
        sb = get_client()

        if "@" in email:
            candidatos = [email]
        else:
            candidatos = [f"{email}@admin", f"{email}@investidor"]

        user = None
        email_usado = None
        for tentativa in candidatos:
            try:
                res = sb.auth.sign_in_with_password({"email": tentativa, "password": password})
                if res.user:
                    user = res.user
                    email_usado = tentativa
                    break
            except Exception:
                continue

        if not user:
            return {"ok": False, "erro": "Usuário ou senha incorretos."}

        role = _role_do_email(email_usado)
        meta = user.user_metadata or {}

        session.permanent = True
        session["user_id"]     = user.id
        session["email"]       = email_usado
        session["role"]        = role
        session["nome"]        = meta.get("nome", email)
        session["username"]    = meta.get("username", email)
        session["usina_ids"]   = meta.get("usina_ids", [])
        session["permissions"] = meta.get("permissions", ["all"] if role == "admin" else [])

        return {"ok": True, "role": role}

    except Exception as e:
        msg = str(e)
        if "Invalid login" in msg or "invalid_credentials" in msg:
            return {"ok": False, "erro": "Usuário ou senha incorretos."}
        return {"ok": False, "erro": "Erro ao fazer login. Tente novamente."}


def logout():
    session.clear()


def usuario_logado() -> object:
    if "user_id" not in session:
        return None
    return {
        "id": session["user_id"],
        "email": session["email"],
        "role": session["role"],
        "nome": session["nome"],
        "usina_ids": session.get("usina_ids", []),
        "permissions": session.get("permissions", ["all"]),
    }


def is_admin() -> bool:
    u = usuario_logado()
    return u is not None and u["role"] == "admin"


def refresh_session_permissions():
    """Re-fetches user metadata from Supabase and updates session permissions/usina_ids."""
    uid = session.get("user_id")
    if not uid:
        return
    # Admin sempre tem acesso total — não re-busca do banco
    if session.get("role") == "admin":
        session["permissions"] = ["all"]
        return
    try:
        sb = get_service_client()
        user = sb.auth.admin.get_user_by_id(uid).user
        if user:
            meta = user.user_metadata or {}
            session["permissions"] = meta.get("permissions", [])
            session["usina_ids"]   = meta.get("usina_ids", [])
    except Exception:
        pass


def preview_investidor_id() -> object:
    """Admin pode visualizar o portal como um investidor específico."""
    return session.get("preview_investidor_id")


def set_preview(investidor_id: str):
    session["preview_investidor_id"] = investidor_id


def clear_preview():
    session.pop("preview_investidor_id", None)


def solicitar_reset(email: str) -> dict:
    try:
        sb = get_client()
        sb.auth.reset_password_for_email(email)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "erro": str(e)}


def ativar_conta(token: str, nova_senha: str) -> dict:
    try:
        sb = get_client()
        res = sb.auth.verify_otp({"token_hash": token, "type": "email"})
        if not res.user:
            return {"ok": False, "erro": "Link inválido ou expirado."}
        sb.auth.update_user({"password": nova_senha})
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "erro": str(e)}


def criar_usuario_admin(username: str, senha: str, nome: str,
                        usina_ids: list, permissions: list,
                        tipo: str = "investidor") -> dict:
    """
    Cria usuário com username/senha.
    tipo="investidor" → email username@investidor, acesso limitado ao portal
    tipo="admin"      → email username@admin, acesso total ao painel
    """
    email_interno = f"{username}@{tipo}"
    try:
        sb = get_service_client()
        meta = {"nome": nome, "username": username}
        if tipo == "investidor":
            meta["usina_ids"]   = usina_ids
            meta["permissions"] = permissions

        res = sb.auth.admin.create_user({
            "email": email_interno,
            "password": senha,
            "email_confirm": True,
            "user_metadata": meta,
        })
        return {"ok": True, "user_id": res.user.id}
    except Exception as e:
        return {"ok": False, "erro": str(e)}


def atualizar_acesso_usuario(investidor_id: str, usina_ids: list, permissions: list) -> dict:
    """Atualiza usinas de acesso e permissões de seção de um investidor existente."""
    try:
        sb = get_service_client()
        meta = sb.auth.admin.get_user_by_id(investidor_id).user.user_metadata or {}
        meta["usina_ids"] = usina_ids
        meta["permissions"] = permissions
        sb.auth.admin.update_user_by_id(investidor_id, {"user_metadata": meta})
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "erro": str(e)}


def listar_usuarios_com_acesso() -> list:
    """Lista todos os investidores com metadados de acesso (usina_ids, permissions)."""
    try:
        sb = get_service_client()
        res = sb.auth.admin.list_users()
        users = res if isinstance(res, list) else getattr(res, "users", res)
        resultado = []
        for user in (users or []):
            meta = getattr(user, "user_metadata", None) or {}
            if not isinstance(meta, dict):
                meta = {}
            email = getattr(user, "email", "")
            if not email.endswith("@investidor"):
                continue
            resultado.append({
                "id": getattr(user, "id", None),
                "email": email,
                "nome": meta.get("nome", email),
                "username": meta.get("username", ""),
                "usina_ids": meta.get("usina_ids", []),
                "permissions": meta.get("permissions", []),
                "ativo": True,
            })
        return sorted(resultado, key=lambda u: u["nome"])
    except Exception:
        return []


def criar_investidor_auth(email: str, nome: str) -> dict:
    """Admin cria conta do investidor via service_role (sem precisar de senha inicial)."""
    try:
        sb = get_service_client()
        res = sb.auth.admin.create_user({
            "email": email,
            "email_confirm": False,
            "user_metadata": {"role": "investidor", "nome": nome},
        })
        return {"ok": True, "user_id": res.user.id}
    except Exception as e:
        return {"ok": False, "erro": str(e)}


def enviar_convite(email: str) -> dict:
    """Envia link de ativação para o investidor definir sua senha."""
    try:
        sb = get_service_client()
        sb.auth.admin.invite_user_by_email(email)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "erro": str(e)}
