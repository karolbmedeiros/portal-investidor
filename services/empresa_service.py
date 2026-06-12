from services.supabase_client import get_service_client

_CORES_SOCIOS = ["#E8621A", "#2563EB", "#16A34A", "#9333EA", "#EAB308", "#EC4899"]


def _nome_usina(u: dict) -> str:
    return u.get("nome_fantasia") or u.get("razao_social") or ""


def _iniciais(nome: str) -> str:
    partes = nome.strip().split()
    if not partes:
        return "?"
    if len(partes) == 1:
        return partes[0][:2].upper()
    return (partes[0][0] + partes[-1][0]).upper()


# ── Listagem básica (usada em dropdowns de documentos / importar) ────────────

def listar_empresas() -> list[dict]:
    sb = get_service_client()
    res = sb.table("usinas").select("id, razao_social, nome_fantasia, cnpj, status").order("razao_social").execute()
    usinas = res.data or []
    for u in usinas:
        u["nome"] = _nome_usina(u)
    return usinas


def buscar_empresa(empresa_id: str) -> dict:
    sb = get_service_client()
    res = sb.table("usinas").select("*").eq("id", empresa_id).maybe_single().execute()
    data = res.data
    if data:
        data["nome"] = _nome_usina(data)
    return data


# ── Listagem completa com sócios (usada na página de cards) ─────────────────

def listar_usinas_com_socios() -> list[dict]:
    sb = get_service_client()

    usinas_res = sb.table("usinas").select("*").order("razao_social").execute()
    usinas = usinas_res.data or []
    if not usinas:
        return []

    usina_ids = [u["id"] for u in usinas]

    partic_res = (
        sb.table("participacoes")
        .select("usina_id, percentual, investidores(id, nome)")
        .in_("usina_id", usina_ids)
        .execute()
    )
    participacoes = partic_res.data or []

    socios_por_usina: dict[str, list] = {}
    for p in participacoes:
        uid = p["usina_id"]
        inv = p.get("investidores") or {}
        nome = inv.get("nome", "?") if isinstance(inv, dict) else "?"
        socios_por_usina.setdefault(uid, []).append({
            "percentual": float(p.get("percentual") or 0),
            "nome": nome,
        })

    for u in usinas:
        u["nome"] = _nome_usina(u)
        socios = socios_por_usina.get(u["id"], [])
        socios.sort(key=lambda s: s["percentual"], reverse=True)
        for i, s in enumerate(socios):
            s["cor"] = _CORES_SOCIOS[i % len(_CORES_SOCIOS)]
            s["iniciais"] = _iniciais(s["nome"])
        u["socios"] = socios
        u["maior_percentual"] = socios[0]["percentual"] if socios else 0

    return usinas


# ── Criação / edição (mantidas, operam na tabela usinas) ────────────────────

def criar_empresa(nome: str, tipo: str, descricao: str) -> dict:
    sb = get_service_client()
    res = sb.table("usinas").insert({
        "razao_social": nome,
        "modalidade_gd": tipo or "GD I",
        "observacoes": descricao,
        "status": "Ativa",
    }).execute()
    return {"ok": True, "empresa": res.data[0] if res.data else None}


def atualizar_empresa(empresa_id: str, dados: dict) -> dict:
    sb = get_service_client()
    campos: dict = {}
    if "nome" in dados:
        campos["razao_social"] = dados["nome"]
    if "tipo" in dados:
        campos["modalidade_gd"] = dados["tipo"]
    if "descricao" in dados:
        campos["observacoes"] = dados["descricao"]
    if campos:
        sb.table("usinas").update(campos).eq("id", empresa_id).execute()
    return {"ok": True}


def listar_categorias(empresa_id: str) -> list[dict]:
    sb = get_service_client()
    try:
        res = (
            sb.table("categorias")
            .select("*")
            .eq("empresa_id", empresa_id)
            .order("tipo")
            .execute()
        )
        return res.data or []
    except Exception:
        return []


def criar_categoria(empresa_id: str, nome: str, tipo: str) -> dict:
    sb = get_service_client()
    try:
        sb.table("categorias").insert({
            "empresa_id": empresa_id,
            "nome": nome,
            "tipo": tipo,
        }).execute()
    except Exception:
        pass
    return {"ok": True}
