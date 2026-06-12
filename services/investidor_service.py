from services.supabase_client import get_service_client
from services.auth_service import usuario_logado, preview_investidor_id, is_admin


def get_investidor_id() -> object:
    """
    Retorna o ID do investidor ativo.
    Admin em modo preview → ID do investidor sendo visualizado.
    Investidor normal → seu próprio ID.
    """
    if is_admin():
        return preview_investidor_id()
    u = usuario_logado()
    return u["id"] if u else None


def listar_investidores() -> list[dict]:
    sb = get_service_client()
    res = sb.table("investidores").select("*").order("nome").execute()
    return res.data or []


def buscar_investidor(investidor_id: str) -> object:
    sb = get_service_client()
    res = sb.table("investidores").select("*").eq("id", investidor_id).maybe_single().execute()
    return res.data if res else None


def criar_investidor(nome: str, cpf: str, email: str, empresa_ids: list[str]) -> dict:
    from services.auth_service import criar_investidor_auth, enviar_convite
    sb = get_service_client()

    auth_res = criar_investidor_auth(email, nome)
    if not auth_res["ok"]:
        return auth_res

    user_id = auth_res["user_id"]

    sb.table("investidores").insert({
        "id": user_id,
        "nome": nome,
        "cpf": cpf,
        "email": email,
        "ativo": True,
    }).execute()

    for eid in empresa_ids:
        sb.table("investidor_empresas").insert({
            "investidor_id": user_id,
            "empresa_id": eid,
        }).execute()

    enviar_convite(email)
    return {"ok": True, "user_id": user_id}


def atualizar_investidor(investidor_id: str, dados: dict) -> dict:
    sb = get_service_client()
    campos = {k: v for k, v in dados.items() if k in ("nome", "cpf", "ativo")}
    sb.table("investidores").update(campos).eq("id", investidor_id).execute()

    if "empresa_ids" in dados:
        sb.table("investidor_empresas").delete().eq("investidor_id", investidor_id).execute()
        for eid in dados["empresa_ids"]:
            sb.table("investidor_empresas").insert({
                "investidor_id": investidor_id,
                "empresa_id": eid,
            }).execute()

    return {"ok": True}


def empresas_do_investidor(investidor_id: str) -> list[dict]:
    sb = get_service_client()
    res = (
        sb.table("investidor_empresas")
        .select("empresa_id, data_inicio, empresas(id, nome, tipo, descricao)")
        .eq("investidor_id", investidor_id)
        .execute()
    )
    return res.data or []
