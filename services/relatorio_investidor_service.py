"""Relatório mensal de panorama publicado pelo admin para o investidor baixar.

Não confundir com services/relatorio_service.py, que gera um PDF automático da
usina. Aqui o arquivo (.pdf ou .docx) é produzido fora do sistema e apenas
publicado pelo admin — o investidor só faz download.
"""
import uuid
from datetime import date

from services.supabase_client import get_service_client

_BUCKET = "relatorios-investidor"
_TABELA = "relatorios_investidor"

_EXTENSOES = {".pdf": "application/pdf",
              ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}

_MESES_PT = ["", "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
             "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"]


# Primeiro mês com relatório. Antes disso nada foi publicado, então esses
# meses não entram na lista — nem no admin nem no portal.
MES_INICIAL = "2026-08"


def normalizar_tipo(ativo_tipo: str) -> str:
    """O admin usa 'carro' no filtro e o portal usa 'carros'; o modelo usa 'carros'."""
    return "carros" if str(ativo_tipo or "").startswith("carro") else "usina"


def rotulo_mes(ym: str) -> str:
    try:
        ano, mes = ym.split("-")
        return f"{_MESES_PT[int(mes)]}/{ano}"
    except Exception:
        return ym


def _extensao(nome: str) -> str:
    nome = (nome or "").lower()
    for ext in _EXTENSOES:
        if nome.endswith(ext):
            return ext
    return ""


def publicar(ativo_tipo: str, ativo_id: str, mes_referencia: str, nome_arquivo: str,
             conteudo: bytes, mime_type: str = None, user_id: str = None) -> dict:
    """Sobe o arquivo e registra o mês como disponível.

    Republicar o mesmo mês substitui o registro (unique ativo+mês) e apaga o
    arquivo anterior do storage, para não deixar órfão.
    """
    ativo_tipo = normalizar_tipo(ativo_tipo)
    ext = _extensao(nome_arquivo)
    if not ext:
        return {"ok": False, "erro": "Envie um arquivo .pdf ou .docx."}
    if not conteudo:
        return {"ok": False, "erro": "Arquivo vazio."}
    if not (ativo_id and mes_referencia):
        return {"ok": False, "erro": "Selecione o ativo e o mês de referência."}

    mes_referencia = str(mes_referencia)[:7]
    sb = get_service_client()
    anterior = buscar_do_mes(ativo_tipo, ativo_id, mes_referencia)
    storage_key = f"{ativo_tipo}/{ativo_id}/{mes_referencia}_{uuid.uuid4()}{ext}"

    try:
        sb.storage.from_(_BUCKET).upload(
            storage_key, conteudo, {"content-type": mime_type or _EXTENSOES[ext]}
        )
    except Exception as e:
        return {"ok": False, "erro": f"Falha ao enviar o arquivo: {e}"}

    try:
        sb.table(_TABELA).upsert({
            "ativo_tipo":      ativo_tipo,
            "ativo_id":        ativo_id,
            "mes_referencia":  mes_referencia,
            "storage_key":     storage_key,
            "nome_arquivo":    nome_arquivo,
            "mime_type":       mime_type or _EXTENSOES[ext],
            "file_size":       len(conteudo),
            "status":          "disponivel",
            "data_publicacao": "now()",
            "data_upload":     "now()",
            "uploaded_by":     user_id,
        }, on_conflict="ativo_tipo,ativo_id,mes_referencia").execute()
    except Exception as e:
        # Não deixa o arquivo órfão se o registro falhou.
        try:
            sb.storage.from_(_BUCKET).remove([storage_key])
        except Exception:
            pass
        return {"ok": False, "erro": str(e)}

    if anterior and anterior.get("storage_key") != storage_key:
        try:
            sb.storage.from_(_BUCKET).remove([anterior["storage_key"]])
        except Exception:
            pass

    return {"ok": True, "substituiu": bool(anterior)}


def buscar_do_mes(ativo_tipo: str, ativo_id: str, mes_referencia: str) -> dict:
    sb = get_service_client()
    try:
        res = (sb.table(_TABELA).select("*")
                 .eq("ativo_tipo", normalizar_tipo(ativo_tipo))
                 .eq("ativo_id", ativo_id)
                 .eq("mes_referencia", str(mes_referencia)[:7])
                 .execute().data or [])
        return res[0] if res else None
    except Exception:
        return None


def listar_meses(ativo_tipo: str, ativo_id: str, meses: int = 12) -> list:
    """Janela dos últimos `meses` meses, do mais recente para o mais antigo.

    Nunca vai antes de MES_INICIAL: não houve relatório antes disso, e listar
    esses meses como "indisponível" sugeriria uma pendência que não existe.

    Fonte única das telas do admin e do investidor: cada item traz o estado do
    mês, disponível só quando existe registro publicado.
    """
    if not ativo_id:
        return []
    ativo_tipo = normalizar_tipo(ativo_tipo)

    hoje = date.today()
    ym_list = []
    ano, mes = hoje.year, hoje.month
    for _ in range(meses):
        ym = f"{ano:04d}-{mes:02d}"
        if ym < MES_INICIAL:
            break
        ym_list.append(ym)
        mes -= 1
        if mes == 0:
            mes, ano = 12, ano - 1

    if not ym_list:
        return []

    sb = get_service_client()
    try:
        publicados = {
            r["mes_referencia"]: r
            for r in (sb.table(_TABELA).select("*")
                        .eq("ativo_tipo", ativo_tipo)
                        .eq("ativo_id", ativo_id)
                        .gte("mes_referencia", ym_list[-1])
                        .execute().data or [])
            if r.get("status") == "disponivel"
        }
    except Exception:
        publicados = {}

    return [{
        "ym":         ym,
        "rotulo":     rotulo_mes(ym),
        "disponivel": ym in publicados,
        "relatorio":  publicados.get(ym),
    } for ym in ym_list]


def buscar(rel_id: str) -> dict:
    sb = get_service_client()
    try:
        res = sb.table(_TABELA).select("*").eq("id", rel_id).execute().data or []
        return res[0] if res else None
    except Exception:
        return None


def baixar(rel_id: str) -> dict:
    """Bytes do arquivo. O chamador nunca vê a key do storage."""
    rel = buscar(rel_id)
    if not rel or rel.get("status") != "disponivel":
        return {"ok": False, "erro": "Relatório indisponível."}
    try:
        conteudo = get_service_client().storage.from_(_BUCKET).download(rel["storage_key"])
    except Exception as e:
        return {"ok": False, "erro": f"Falha ao ler o arquivo: {e}"}
    return {
        "ok": True,
        "conteudo":  conteudo,
        "nome":      rel.get("nome_arquivo") or "relatorio",
        "mime_type": rel.get("mime_type") or "application/octet-stream",
        "relatorio": rel,
    }


def remover(rel_id: str) -> dict:
    """Despublica o mês: apaga arquivo e registro, o mês volta a indisponível."""
    rel = buscar(rel_id)
    if not rel:
        return {"ok": False, "erro": "Relatório não encontrado."}
    sb = get_service_client()
    try:
        sb.storage.from_(_BUCKET).remove([rel["storage_key"]])
    except Exception:
        pass  # registro sai de qualquer forma; arquivo órfão não expõe nada
    try:
        sb.table(_TABELA).delete().eq("id", rel_id).execute()
    except Exception as e:
        return {"ok": False, "erro": str(e)}
    return {"ok": True}


def pode_acessar(usuario: dict, ativo_tipo: str, ativo_id: str) -> bool:
    """Admin vê tudo; investidor só os ativos vinculados a ele.

    usina_ids do user_metadata guarda tanto uuid de usina quanto slug de carro,
    então a checagem é a mesma para os dois tipos. Usinas ainda aceitam o
    vínculo por participação, como faz o resto do portal.
    """
    if not usuario:
        return False
    if usuario.get("role") == "admin":
        return True

    ativo_tipo = normalizar_tipo(ativo_tipo)
    if ativo_id in (usuario.get("usina_ids") or []):
        return True

    if ativo_tipo == "usina":
        from services.usina_service import usinas_do_investidor
        try:
            return any(p.get("usina_id") == ativo_id
                       for p in usinas_do_investidor(usuario["id"]))
        except Exception:
            return False
    return False
