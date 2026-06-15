import unicodedata
import re
import json
import os
from typing import Optional
from services.supabase_client import get_financeiro_client

_CLIENTES_CACHE: Optional[dict] = None

def dados_clientes_cons() -> dict:
    """Dict keyed by normalized plate (no dash/space, uppercase)."""
    global _CLIENTES_CACHE
    if _CLIENTES_CACHE is not None:
        return _CLIENTES_CACHE
    path = os.path.join(os.path.dirname(__file__), "..", "static", "data", "clientes_carros.json")
    try:
        with open(os.path.abspath(path), encoding="utf-8") as f:
            _CLIENTES_CACHE = json.load(f)
    except Exception:
        _CLIENTES_CACHE = {}
    return _CLIENTES_CACHE


def upload_pdf_cliente(ref_id: str, nome_arquivo: str, conteudo: bytes,
                       mime_type: str = "application/pdf") -> dict:
    from services.supabase_client import get_service_client
    import uuid
    sb = get_service_client()
    fid = str(uuid.uuid4())
    caminho = f"clientes/{ref_id}/{fid}_{nome_arquivo}"
    try:
        sb.storage.from_("documentos").upload(caminho, conteudo, {"content-type": mime_type})
        url = sb.storage.from_("documentos").get_public_url(caminho)
        return {"ok": True, "url": url, "caminho": caminho}
    except Exception as e:
        return {"ok": False, "erro": str(e)}

_EMPRESA_INFO = {
    "LUZ DIVINA EMPREENDIMENTOS LTDA": {
        "cnpj":            "48.284.349/0001-29",
        "pix":             None,
        "unidade":         "LUZ DIVINA LTDA",
        "total_investido": 408_000.00,
    },
    "JOÃO PAULO SERVIÇOS EM CONSULTORIA LTDA": {
        "cnpj":            "24.954.506/0001-06",
        "pix":             "4d9e79bf-e7f3-4298-86b2-66613980b90b",
        "unidade":         "JOÃO PAULO CONSÓRCIOS",
        "total_investido": None,
    },
}

_MODELO_EMPRESA = {
    "DOLPHIN MINI": "LUZ DIVINA EMPREENDIMENTOS LTDA",
    "POLO TRACK":   "JOÃO PAULO SERVIÇOS EM CONSULTORIA LTDA",
}

_PREFIXO_EMPRESA = {
    "TSW": ("LUZ DIVINA EMPREENDIMENTOS LTDA", "BYD Dolphin Mini"),
    "SSW": ("JOÃO PAULO SERVIÇOS EM CONSULTORIA LTDA", "VW Polo Track"),
    "STX": ("JOÃO PAULO SERVIÇOS EM CONSULTORIA LTDA", "VW Polo Track"),
}


def _norm(placa):
    return (placa or "").upper().replace("-", "").replace(" ", "")


def _slugify(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def listar_empresas_veiculos():
    sb = get_financeiro_client()

    try:
        all_recs = (
            sb.table("sob_adm_recebimentos")
            .select("placa,taxa_valor,data_semana,recebido")
            .execute()
            .data or []
        )
    except Exception as e:
        print(f"[veiculos_service] erro recebimentos: {e}")
        return []

    if not all_recs:
        return []

    placa_to_info = {}
    try:
        contratos = (
            sb.table("contratos_locacao")
            .select("veiculo_placa,veiculo_modelo,veiculo_marca")
            .eq("deletado", False)
            .execute()
            .data or []
        )
        for c in contratos:
            key = _norm(c["veiculo_placa"])
            placa_to_info[key] = {
                "modelo": (c.get("veiculo_modelo") or "").strip(),
                "marca":  (c.get("veiculo_marca") or "").strip(),
            }
    except Exception:
        pass

    semana_atual = max(r["data_semana"] for r in all_recs)
    empresas = {}

    for rec in all_recs:
        placa = rec["placa"]
        normed = _norm(placa)
        empresa = None
        modelo = placa

        info = placa_to_info.get(normed)
        if info:
            modelo_upper = info["modelo"].upper()
            for kw, emp in _MODELO_EMPRESA.items():
                if kw in modelo_upper:
                    empresa = emp
                    modelo = info["modelo"]
                    break
        else:
            prefix = normed[:3]
            if prefix in _PREFIXO_EMPRESA:
                empresa, modelo = _PREFIXO_EMPRESA[prefix]

        if not empresa:
            continue

        if empresa not in empresas:
            info_emp = _EMPRESA_INFO.get(empresa, {})
            empresas[empresa] = {
                "nome":            empresa,
                "slug":            _slugify(empresa),
                "cnpj":            info_emp.get("cnpj"),
                "pix":             info_emp.get("pix"),
                "total_investido": info_emp.get("total_investido"),
                "veiculos": {},
                "receita_semanal": 0.0,
            }

        if placa not in empresas[empresa]["veiculos"]:
            empresas[empresa]["veiculos"][placa] = {"placa": placa, "modelo": modelo}

        if rec.get("data_semana") == semana_atual and rec.get("recebido"):
            empresas[empresa]["receita_semanal"] += float(rec.get("taxa_valor") or 0)

    result = []
    for emp in empresas.values():
        emp["veiculos"] = list(emp["veiculos"].values())
        result.append(emp)

    return result


def buscar_empresa_veiculos(slug):
    return next((e for e in listar_empresas_veiculos() if e["slug"] == slug), None)


def recebimentos_da_empresa(empresa):
    """Returns per-vehicle payment history. empresa is the dict from buscar_empresa_veiculos."""
    sb = get_financeiro_client()
    placas = [v["placa"] for v in empresa["veiculos"]]
    if not placas:
        return {}

    try:
        rows = (
            sb.table("sob_adm_recebimentos")
            .select("*")
            .in_("placa", placas)
            .order("data_semana", desc=False)
            .execute()
            .data or []
        )
    except Exception as e:
        print(f"[veiculos_service] erro historico: {e}")
        return {}

    # Group by placa → list of weekly records
    por_placa = {}
    for row in rows:
        p = row["placa"]
        if p not in por_placa:
            por_placa[p] = []
        por_placa[p].append(row)

    return por_placa


def contas_receber_empresa(empresa_nome: str) -> list:
    """Faturas em aberto da empresa na tabela contas_receber_frota."""
    info = _EMPRESA_INFO.get(empresa_nome, {})
    unidade = info.get("unidade")
    if not unidade:
        return []
    try:
        sb = get_financeiro_client()
        res = (
            sb.table("contas_receber_frota")
            .select("numero_documento,cliente,data_vencimento,valor,situacao,tipo_fatura,dias_vencimento,faixa_vencimento")
            .eq("unidade", unidade)
            .order("data_vencimento")
            .execute()
        )
        return res.data or []
    except Exception as e:
        print(f"[contas_receber_empresa] erro: {e}")
        return []
