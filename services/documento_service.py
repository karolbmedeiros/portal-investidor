import uuid
from services.supabase_client import get_service_client

BUCKET = "documentos"
CATEGORIAS_VALIDAS = ["contratos", "relatorios", "notas_fiscais", "apolices", "outros"]


def listar_documentos(empresa_id: str, investidor_id: object = None) -> list[dict]:
    sb = get_service_client()
    q = (
        sb.table("documentos")
        .select("*")
        .eq("empresa_id", empresa_id)
        .order("created_at", desc=True)
    )
    if investidor_id:
        # Documentos públicos para a empresa OU específicos para este investidor
        q = q.or_(f"visibilidade.eq.todos,investidor_id.eq.{investidor_id}")
    res = q.execute()
    return res.data or []


def upload_documento(
    empresa_id: str,
    nome: str,
    categoria: str,
    conteudo: bytes,
    mime_type: str,
    visibilidade: str = "todos",
    investidor_id: object = None,
) -> dict:
    if categoria not in CATEGORIAS_VALIDAS:
        return {"ok": False, "erro": f"Categoria inválida. Use: {CATEGORIAS_VALIDAS}"}

    sb = get_service_client()
    arquivo_id = str(uuid.uuid4())
    caminho = f"{empresa_id}/{categoria}/{arquivo_id}_{nome}"

    try:
        sb.storage.from_(BUCKET).upload(caminho, conteudo, {"content-type": mime_type})
        url = sb.storage.from_(BUCKET).get_public_url(caminho)

        sb.table("documentos").insert({
            "empresa_id": empresa_id,
            "nome": nome,
            "categoria": categoria,
            "url": url,
            "caminho_storage": caminho,
            "mime_type": mime_type,
            "visibilidade": visibilidade,
            "investidor_id": investidor_id,
        }).execute()

        return {"ok": True, "url": url}
    except Exception as e:
        return {"ok": False, "erro": str(e)}


def excluir_documento(doc_id: str) -> dict:
    sb = get_service_client()
    doc = sb.table("documentos").select("caminho_storage").eq("id", doc_id).single().execute()
    if not doc.data:
        return {"ok": False, "erro": "Documento não encontrado."}

    try:
        sb.storage.from_(BUCKET).remove([doc.data["caminho_storage"]])
        sb.table("documentos").delete().eq("id", doc_id).execute()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "erro": str(e)}


def url_download(caminho: str) -> str:
    sb = get_service_client()
    return sb.storage.from_(BUCKET).create_signed_url(caminho, 300)["signedURL"]
