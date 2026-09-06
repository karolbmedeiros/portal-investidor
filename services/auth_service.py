from flask import session
from services.supabase_client import get_client, get_service_client
from services.log_erros import ignorado


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
        if role == "admin":
            session["usina_ids"]   = []
            session["permissions"] = ["all"]
        else:
            _acesso = ler_acesso(user.id)
            session["usina_ids"]   = _acesso["usina_ids"]
            session["permissions"] = _acesso["permissions"]

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


# ── Acesso do investidor (tabela usuario_acessos) ────────────────────────────
# Permissões e usinas visíveis moram numa tabela comum, não no user_metadata
# do Auth. A home do portal relê o acesso a cada carregamento para que uma
# revogação feita no admin valha na hora; pela API admin do Auth isso custava
# ~0,7 s por requisição, contra ~0,2 s de uma consulta comum.
_TABELA_ACESSOS = "usuario_acessos"


def ler_acesso(user_id: str) -> dict:
    """{"permissions": [...], "usina_ids": [...]} — vazios quando não há linha."""
    vazio = {"permissions": [], "usina_ids": []}
    if not user_id:
        return vazio
    try:
        res = (
            get_service_client()
            .table(_TABELA_ACESSOS)
            .select("permissions, usina_ids")
            .eq("user_id", user_id)
            .maybe_single()
            .execute()
        )
        if res and res.data:
            return {
                "permissions": res.data.get("permissions") or [],
                "usina_ids":   res.data.get("usina_ids") or [],
            }
    except Exception:
        ignorado("leitura do acesso do usuário")
    return vazio


def gravar_acesso(user_id: str, usina_ids: list, permissions: list) -> None:
    """Grava o acesso do usuário. Levanta em caso de erro — quem chama reporta."""
    from datetime import datetime, timezone
    get_service_client().table(_TABELA_ACESSOS).upsert({
        "user_id":     user_id,
        "permissions": list(permissions or []),
        "usina_ids":   list(usina_ids or []),
        "updated_at":  datetime.now(timezone.utc).isoformat(),
    }).execute()


def refresh_session_permissions():
    """Relê permissões e usinas do usuário a cada requisição (uma vez por requisição).

    Antes havia um cache de 5 minutos aqui. Ele valia uma consulta a menos, mas
    fazia o acesso salvo no admin demorar até 5 minutos para valer — e, o que é
    pior, mantinha um acesso REVOGADO funcionando por esse tempo. Como a sessão
    do investidor é um cookie assinado no navegador dele, o servidor não tem como
    alcançá-la no momento em que o admin salva; a única forma de a revogação valer
    na hora é não confiar na cópia guardada.

    Custa um round-trip, e só na home do portal — o único lugar que chama isto.
    Numa página que já faz dezenas de consultas, não muda nada perceptível.

    Falha de rede não derruba o acesso de propósito: em erro a sessão continua
    com o que tinha, para um soluço do Supabase não deslogar todo mundo.
    """
    uid = session.get("user_id")
    if not uid:
        return
    if session.get("role") == "admin":
        session["permissions"] = ["all"]
        return

    # uma leitura por requisição, mesmo que a função seja chamada mais de uma vez
    try:
        from flask import g
        if getattr(g, "_perms_lidas", False):
            return
        g._perms_lidas = True
    except Exception:
        pass   # fora de contexto de requisição: relê sem memoizar

    acesso = ler_acesso(uid)
    session["permissions"] = acesso["permissions"]
    session["usina_ids"]   = acesso["usina_ids"]


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

        res = sb.auth.admin.create_user({
            "email": email_interno,
            "password": senha,
            "email_confirm": True,
            "user_metadata": meta,
        })
        if tipo == "investidor":
            gravar_acesso(res.user.id, usina_ids, permissions)
        return {"ok": True, "user_id": res.user.id}
    except Exception as e:
        return {"ok": False, "erro": str(e)}


def atualizar_acesso_usuario(investidor_id: str, usina_ids: list, permissions: list) -> dict:
    """Atualiza usinas de acesso e permissões de seção de um investidor existente."""
    try:
        gravar_acesso(investidor_id, usina_ids, permissions)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "erro": str(e)}


def listar_usuarios_com_acesso() -> list:
    """Lista todos os investidores com metadados de acesso (usina_ids, permissions)."""
    try:
        sb = get_service_client()
        res = sb.auth.admin.list_users()
        users = res if isinstance(res, list) else getattr(res, "users", res)

        # uma consulta só para todos os acessos, em vez de uma por usuário
        acessos = {}
        try:
            for row in (sb.table(_TABELA_ACESSOS)
                          .select("user_id, permissions, usina_ids")
                          .execute().data or []):
                acessos[row["user_id"]] = row
        except Exception:
            ignorado("acessos para a lista de usuários do admin")

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
                "usina_ids": (acessos.get(getattr(user, "id", None)) or {}).get("usina_ids") or [],
                "permissions": (acessos.get(getattr(user, "id", None)) or {}).get("permissions") or [],
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
