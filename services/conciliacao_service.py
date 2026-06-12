"""
Serviço para a página global de Fluxo de Caixa / Conciliação.
Tabelas: lancamentos_bancarios, contas_bancarias, categorias_financeiras,
         centros_custo, ofx_importacoes  —  Supabase 1 (get_service_client).
"""
from typing import Optional
from services.supabase_client import get_service_client

_NEUTRAS = ["RESGATE FUNDOS", "APLICACAO FUNDO", "APLICAÇÃO FUNDO"]


def _eh_neutro(l: dict) -> bool:
    desc = (l.get("descricao") or l.get("descricao_original") or "").upper()
    return any(kw in desc for kw in _NEUTRAS)


# ── Dados de referência ───────────────────────────────────────────────────────

def listar_contas() -> list:
    try:
        return (
            get_service_client()
            .table("contas_bancarias")
            .select("id,apelido,titular_nome,banco,agencia,numero_conta,tipo,saldo_inicial,data_saldo_inicial")
            .eq("ativa", True)
            .order("titular_nome")
            .execute()
            .data or []
        )
    except Exception:
        return []


def listar_categorias() -> list:
    try:
        return (
            get_service_client()
            .table("categorias_financeiras")
            .select("id,nome,tipo")
            .eq("ativo", True)
            .order("tipo").order("nome")
            .execute()
            .data or []
        )
    except Exception:
        return []


def listar_centros_custo() -> list:
    try:
        return (
            get_service_client()
            .table("centros_custo")
            .select("id,nome")
            .order("nome")
            .execute()
            .data or []
        )
    except Exception:
        return []


# ── Lançamentos ───────────────────────────────────────────────────────────────

def listar_lancamentos(
    conta_id: str,
    mes: Optional[str] = None,
    tipo: Optional[str] = None,
    categoria_id: Optional[str] = None,
    centro_custo_id: Optional[str] = None,
    status: Optional[str] = None,   # "conciliado" | "pendente"
    limite: int = 600,
) -> list:
    if not conta_id:
        return []
    try:
        sb = get_service_client()
        q = (
            sb.table("lancamentos_bancarios")
            .select("*, categorias_financeiras(id,nome,tipo)")
            .eq("conta_bancaria_id", conta_id)
            .is_("deleted_at", "null")
        )
        if mes:
            q = q.gte("data_transacao", f"{mes}-01").lte("data_transacao", f"{mes}-31")
        if tipo in ("credito", "debito"):
            q = q.eq("tipo", tipo)
        if categoria_id:
            q = q.eq("categoria_id", categoria_id)
        if centro_custo_id:
            q = q.eq("centro_custo_id", centro_custo_id)
        if status == "conciliado":
            q = q.eq("conciliado", True)
        elif status == "pendente":
            q = q.eq("conciliado", False)

        rows = q.order("data_transacao", desc=True).order("created_at", desc=True).limit(limite).execute().data or []
        for l in rows:
            l["_neutro"]       = _eh_neutro(l)
            l["_transferencia"] = bool(l.get("is_transferencia"))
        return rows
    except Exception:
        return []


def meses_da_conta(conta_id: str) -> list:
    """Retorna lista de meses (YYYY-MM) com lançamentos, ordenados desc."""
    try:
        rows = (
            get_service_client()
            .table("lancamentos_bancarios")
            .select("data_transacao")
            .eq("conta_bancaria_id", conta_id)
            .is_("deleted_at", "null")
            .execute()
            .data or []
        )
        meses = sorted({r["data_transacao"][:7] for r in rows if r.get("data_transacao")}, reverse=True)
        return meses
    except Exception:
        return []


# ── KPIs ─────────────────────────────────────────────────────────────────────

def saldo_atual(conta_id: str, saldo_inicial: float) -> float:
    """saldo_inicial + soma dos valores signed (neg=débito, pos=crédito)."""
    try:
        rows = (
            get_service_client()
            .table("lancamentos_bancarios")
            .select("valor")
            .eq("conta_bancaria_id", conta_id)
            .is_("deleted_at", "null")
            .execute()
            .data or []
        )
        return round(saldo_inicial + sum(float(r.get("valor") or 0) for r in rows), 2)
    except Exception:
        return saldo_inicial


def kpis_periodo(lancamentos: list) -> dict:
    """KPIs do período já filtrado (lista vinda de listar_lancamentos)."""
    op = [l for l in lancamentos if not l.get("_neutro") and not l.get("_transferencia")]
    entradas = sum(abs(float(l.get("valor") or 0)) for l in op if l.get("tipo") == "credito")
    saidas   = sum(abs(float(l.get("valor") or 0)) for l in op if l.get("tipo") == "debito")
    total    = len(lancamentos)
    conc     = sum(1 for l in lancamentos if l.get("conciliado"))
    pend     = total - conc
    return {
        "entradas":    round(entradas, 2),
        "saidas":      round(saidas, 2),
        "resultado":   round(entradas - saidas, 2),
        "total":       total,
        "conciliados": conc,
        "pendentes":   pend,
        "pct_conc":    round(conc / total * 100) if total else 0,
    }


def ultima_importacao(conta_id: str) -> Optional[dict]:
    try:
        rows = (
            get_service_client()
            .table("ofx_importacoes")
            .select("created_at,periodo_inicio,periodo_fim,total_importados,total_duplicados")
            .eq("conta_bancaria_id", conta_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
            .data or []
        )
        return rows[0] if rows else None
    except Exception:
        return None


# ── Writes ────────────────────────────────────────────────────────────────────

def conciliar(lancamento_id: str, categoria_id: Optional[str],
              observacao: Optional[str] = None) -> dict:
    try:
        upd: dict = {"conciliado": True}
        if categoria_id:
            upd["categoria_id"] = categoria_id
        if observacao:
            upd["observacoes"] = observacao
        get_service_client().table("lancamentos_bancarios").update(upd).eq("id", lancamento_id).execute()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "erro": str(e)}


def desconciliar(lancamento_id: str) -> dict:
    try:
        get_service_client().table("lancamentos_bancarios").update({
            "conciliado": False,
            "categoria_id": None,
            "observacoes": None,
        }).eq("id", lancamento_id).execute()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "erro": str(e)}


def importar_ofx(conta_id: str, conteudo: bytes, extensao: str,
                 importado_por=None) -> dict:
    from services.usina_service import (
        _parse_ofx, _parse_xlsx, _is_dup_bancario, _eh_neutro as _neu,
    )
    try:
        lancamentos = _parse_ofx(conteudo) if extensao.lower() == "ofx" else _parse_xlsx(conteudo)
    except Exception as e:
        return {"ok": False, "erro": str(e)}
    if not lancamentos:
        return {"ok": False, "erro": "Nenhum lançamento encontrado."}

    sb = get_service_client()
    inseridos = duplicados = 0
    ofx_id = None

    if extensao.lower() == "ofx":
        try:
            datas = [l["data_transacao"] for l in lancamentos]
            r = sb.table("ofx_importacoes").insert({
                "conta_bancaria_id": conta_id,
                "arquivo_nome": f"extrato.{extensao}",
                "periodo_inicio": min(datas),
                "periodo_fim": max(datas),
                "total_no_arquivo": len(lancamentos),
                "total_importados": 0,
                "total_duplicados": 0,
                "importado_por": importado_por,
            }).execute()
            ofx_id = (r.data or [{}])[0].get("id")
        except Exception:
            pass

    for l in lancamentos:
        fitid = l.get("fitid")
        if _is_dup_bancario(sb, conta_id, l["data_transacao"],
                            l["descricao_original"], l["valor"], fitid):
            duplicados += 1
            continue
        try:
            # Garante sinal correto (débito = negativo)
            val = abs(l["valor"]) if l["tipo"] == "credito" else -abs(l["valor"])
            row: dict = {
                "conta_bancaria_id": conta_id,
                "data_transacao":    l["data_transacao"],
                "mes_competencia":   l["data_transacao"][:8] + "01",
                "descricao_original": l["descricao_original"],
                "descricao":         l["descricao_original"],
                "valor":             val,
                "tipo":              l["tipo"],
                "conciliado":        _neu(l),
            }
            if fitid:
                row["fitid"] = fitid
            if ofx_id:
                row["ofx_importacao_id"] = ofx_id
            sb.table("lancamentos_bancarios").insert(row).execute()
            inseridos += 1
        except Exception:
            pass

    if ofx_id:
        try:
            sb.table("ofx_importacoes").update({
                "total_importados": inseridos,
                "total_duplicados": duplicados,
            }).eq("id", ofx_id).execute()
        except Exception:
            pass

    return {"ok": True, "inseridos": inseridos, "total": len(lancamentos)}
