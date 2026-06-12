from datetime import date, timedelta
from services.supabase_client import get_service_client


def saldo_atual(investidor_id: str, empresa_id: object = None) -> float:
    sb = get_service_client()
    q = (
        sb.table("saldos")
        .select("saldo")
        .eq("investidor_id", investidor_id)
        .order("data", desc=True)
        .limit(1)
    )
    if empresa_id:
        q = q.eq("empresa_id", empresa_id)
    res = q.execute()
    if res.data:
        return float(res.data[0]["saldo"])
    return 0.0


def resumo_investidor(investidor_id: str) -> dict:
    """Cards da home: saldo total, retorno total, retorno do mês."""
    sb = get_service_client()

    hoje = date.today()
    inicio_mes = hoje.replace(day=1)

    # Saldo total consolidado (último saldo de cada empresa)
    res_saldo = (
        sb.table("saldos")
        .select("empresa_id, saldo, data")
        .eq("investidor_id", investidor_id)
        .order("data", desc=True)
        .execute()
    )

    vistos = set()
    saldo_total = 0.0
    for row in (res_saldo.data or []):
        eid = row["empresa_id"]
        if eid not in vistos:
            saldo_total += float(row["saldo"])
            vistos.add(eid)

    # Retorno total e no mês via transações
    res_tx = (
        sb.table("transacoes")
        .select("valor, data, tipo")
        .eq("investidor_id", investidor_id)
        .execute()
    )
    retorno_total = 0.0
    retorno_mes = 0.0
    for tx in (res_tx.data or []):
        v = float(tx["valor"])
        if tx["tipo"] == "resultado":
            retorno_total += v
            tx_date = date.fromisoformat(tx["data"][:10])
            if tx_date >= inicio_mes:
                retorno_mes += v

    return {
        "saldo_total": saldo_total,
        "retorno_total": retorno_total,
        "retorno_mes": retorno_mes,
    }


def resumo_por_empresa(investidor_id: str) -> list[dict]:
    """Card de cada empresa na home."""
    from services.investidor_service import empresas_do_investidor
    sb = get_service_client()

    empresas = empresas_do_investidor(investidor_id)
    hoje = date.today()
    inicio_mes = hoje.replace(day=1)
    resultado = []

    for inv_emp in empresas:
        emp = inv_emp.get("empresas", {})
        eid = emp.get("id")
        if not eid:
            continue

        # Saldo atual da empresa
        res_s = (
            sb.table("saldos")
            .select("saldo")
            .eq("investidor_id", investidor_id)
            .eq("empresa_id", eid)
            .order("data", desc=True)
            .limit(1)
            .execute()
        )
        saldo = float(res_s.data[0]["saldo"]) if res_s.data else 0.0

        # Retorno do mês desta empresa
        res_tx = (
            sb.table("transacoes")
            .select("valor")
            .eq("investidor_id", investidor_id)
            .eq("empresa_id", eid)
            .eq("tipo", "resultado")
            .gte("data", inicio_mes.isoformat())
            .execute()
        )
        retorno_mes = sum(float(r["valor"]) for r in (res_tx.data or []))

        resultado.append({
            "empresa_id": eid,
            "nome": emp.get("nome", ""),
            "tipo": emp.get("tipo", ""),
            "saldo": saldo,
            "retorno_mes": retorno_mes,
            "positivo": retorno_mes >= 0,
        })

    return resultado


def evolucao_saldo(investidor_id: str, empresa_id: object = None, dias: int = 90) -> list[dict]:
    """Série temporal para gráficos."""
    sb = get_service_client()
    desde = (date.today() - timedelta(days=dias)).isoformat()

    q = (
        sb.table("saldos")
        .select("data, saldo, empresa_id")
        .eq("investidor_id", investidor_id)
        .gte("data", desde)
        .order("data")
    )
    if empresa_id:
        q = q.eq("empresa_id", empresa_id)

    res = q.execute()
    return res.data or []


def listar_transacoes(
    investidor_id: str,
    empresa_id: object = None,
    tipo: object = None,
    data_inicio: object = None,
    data_fim: object = None,
    busca: object = None,
    pagina: int = 1,
    por_pagina: int = 50,
) -> dict:
    sb = get_service_client()

    q = (
        sb.table("transacoes")
        .select("*, empresas(nome), categorias(nome, tipo)")
        .eq("investidor_id", investidor_id)
        .order("data", desc=True)
    )

    if empresa_id:
        q = q.eq("empresa_id", empresa_id)
    if tipo:
        q = q.eq("tipo", tipo)
    if data_inicio:
        q = q.gte("data", data_inicio)
    if data_fim:
        q = q.lte("data", data_fim)
    if busca:
        q = q.ilike("descricao", f"%{busca}%")

    offset = (pagina - 1) * por_pagina
    q = q.range(offset, offset + por_pagina - 1)

    res = q.execute()
    return {
        "transacoes": res.data or [],
        "pagina": pagina,
        "por_pagina": por_pagina,
    }


def resultado_empresa(investidor_id: str, empresa_id: str, data_inicio: str, data_fim: str) -> dict:
    """Receitas, custos e resultado líquido de uma empresa no período."""
    sb = get_service_client()

    res = (
        sb.table("transacoes")
        .select("tipo, valor, descricao, data, categorias(nome, tipo)")
        .eq("investidor_id", investidor_id)
        .eq("empresa_id", empresa_id)
        .gte("data", data_inicio)
        .lte("data", data_fim)
        .order("data", desc=True)
        .execute()
    )

    receitas = []
    custos = []
    total_receitas = 0.0
    total_custos = 0.0

    for tx in (res.data or []):
        v = float(tx["valor"])
        if tx["tipo"] == "receita":
            receitas.append(tx)
            total_receitas += v
        elif tx["tipo"] == "custo":
            custos.append(tx)
            total_custos += abs(v)

    return {
        "receitas": receitas,
        "custos": custos,
        "total_receitas": total_receitas,
        "total_custos": total_custos,
        "resultado_liquido": total_receitas - total_custos,
    }


def importar_csv(investidor_id: str, empresa_id: str, linhas: list[dict]) -> dict:
    """
    Cada linha deve ter: data, tipo, categoria_id, descricao, valor
    Valida antes de inserir. Retorna contagem de inseridos e erros.
    """
    sb = get_service_client()
    inseridos = 0
    erros = []
    campos_obrigatorios = {"data", "tipo", "descricao", "valor"}

    for i, linha in enumerate(linhas):
        faltando = campos_obrigatorios - set(linha.keys())
        if faltando:
            erros.append(f"Linha {i+1}: campos faltando — {faltando}")
            continue
        if linha["tipo"] not in ("receita", "custo", "resultado"):
            erros.append(f"Linha {i+1}: tipo inválido '{linha['tipo']}'")
            continue
        try:
            sb.table("transacoes").insert({
                "investidor_id": investidor_id,
                "empresa_id": empresa_id,
                "data": linha["data"],
                "tipo": linha["tipo"],
                "categoria_id": linha.get("categoria_id"),
                "descricao": linha["descricao"],
                "valor": float(linha["valor"]),
            }).execute()
            inseridos += 1
        except Exception as e:
            erros.append(f"Linha {i+1}: {e}")

    return {"inseridos": inseridos, "erros": erros}
