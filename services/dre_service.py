from services.supabase_client import get_service_client


# ── Categorias financeiras (naturezas) ──────────────────────────────────────

def listar_categorias_financeiras() -> list:
    sb = get_service_client()
    res = sb.table("categorias_financeiras") \
            .select("id,nome,tipo,codigo,ordem,categoria_pai_id,ativo") \
            .eq("ativo", True) \
            .order("ordem", desc=False) \
            .order("nome", desc=False) \
            .execute()
    return res.data or []


def atualizar_categoria_financeira(cat_id: str, dados: dict) -> dict:
    sb = get_service_client()
    campos = {k: v for k, v in dados.items() if k in ("nome", "tipo", "codigo", "ordem", "categoria_pai_id")}
    try:
        sb.table("categorias_financeiras").update(campos).eq("id", cat_id).execute()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "erro": str(e)}


# ── DRE Seções ──────────────────────────────────────────────────────────────

def listar_secoes_dre() -> list:
    """Retorna todas as seções ordenadas, estruturadas hierarquicamente."""
    sb = get_service_client()
    res = sb.table("dre_secoes") \
            .select("id,nome,tipo,parent_id,ordem,formula_json") \
            .order("ordem", desc=False) \
            .execute()
    todas = res.data or []

    # Busca categorias vinculadas a cada linha
    ids_linhas = [s["id"] for s in todas if s["tipo"] == "linha"]
    vinculos: dict = {}
    if ids_linhas:
        rv = sb.table("dre_linha_categorias") \
               .select("linha_id,categoria_id,categorias_financeiras(id,nome,tipo)") \
               .in_("linha_id", ids_linhas) \
               .execute()
        for v in (rv.data or []):
            vinculos.setdefault(v["linha_id"], []).append(v["categorias_financeiras"])

    for s in todas:
        s["categorias_vinculadas"] = vinculos.get(s["id"], [])

    # Monta hierarquia: raízes + filhos
    raizes = [s for s in todas if not s["parent_id"]]
    filhos_por_pai: dict = {}
    for s in todas:
        if s["parent_id"]:
            filhos_por_pai.setdefault(s["parent_id"], []).append(s)

    resultado = []
    for r in raizes:
        resultado.append(r)
        for filho in filhos_por_pai.get(r["id"], []):
            filho["_nivel"] = 1
            resultado.append(filho)

    return resultado


def criar_secao_dre(nome: str, tipo: str, parent_id, formula_json=None) -> dict:
    sb = get_service_client()
    # Ordem = próximo número
    res = sb.table("dre_secoes").select("ordem").order("ordem", desc=True).limit(1).execute()
    proxima = ((res.data or [{}])[0].get("ordem") or 0) + 1
    row = {"nome": nome, "tipo": tipo, "parent_id": parent_id, "ordem": proxima}
    if formula_json is not None:
        row["formula_json"] = formula_json
    try:
        sb.table("dre_secoes").insert(row).execute()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "erro": str(e)}


def editar_secao_dre(secao_id: str, nome: str, formula_json=None) -> dict:
    sb = get_service_client()
    upd: dict = {"nome": nome}
    if formula_json is not None:
        upd["formula_json"] = formula_json
    try:
        sb.table("dre_secoes").update(upd).eq("id", secao_id).execute()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "erro": str(e)}


def excluir_secao_dre(secao_id: str) -> dict:
    sb = get_service_client()
    try:
        sb.table("dre_secoes").delete().eq("id", secao_id).execute()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "erro": str(e)}


def reordenar_secoes_dre(items: list) -> dict:
    """items = [{"id": "...", "ordem": 1}, ...]"""
    sb = get_service_client()
    try:
        for item in items:
            sb.table("dre_secoes").update({"ordem": item["ordem"]}).eq("id", item["id"]).execute()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "erro": str(e)}


# ── Vínculo linha ↔ categorias ──────────────────────────────────────────────

def vincular_categorias_linha(linha_id: str, categoria_ids: list) -> dict:
    sb = get_service_client()
    try:
        # Remove vínculos anteriores
        sb.table("dre_linha_categorias").delete().eq("linha_id", linha_id).execute()
        # Insere novos
        if categoria_ids:
            rows = [{"linha_id": linha_id, "categoria_id": cid} for cid in categoria_ids]
            sb.table("dre_linha_categorias").insert(rows).execute()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "erro": str(e)}


# ── Cálculo DRE ─────────────────────────────────────────────────────────────

def _meses_intervalo(mes_inicio: str, mes_fim: str) -> list:
    """Lista de YYYY-MM entre mes_inicio e mes_fim, inclusive."""
    ano_i, mi = int(mes_inicio[:4]), int(mes_inicio[5:7])
    ano_f, mf = int(mes_fim[:4]), int(mes_fim[5:7])
    meses = []
    a, m = ano_i, mi
    while (a, m) <= (ano_f, mf):
        meses.append(f"{a:04d}-{m:02d}")
        m += 1
        if m > 12:
            m = 1
            a += 1
    return meses


def calcular_dre(usina_id: str, mes_inicio: str, mes_fim: str) -> dict:
    """
    Retorna {"meses": [...], "valores": {secao_id: {mes: float}},
              "percentuais": {secao_id: {mes: float}},
              "lancamentos": {secao_id: {mes: [...]}},
              "naturezas": {secao_id: [{"id","nome","valores","percentuais"}]}}.
    mes_inicio / mes_fim no formato YYYY-MM — uma coluna por mês do intervalo.
    "percentuais" e os percentuais de "naturezas" são sempre em relação ao
    valor da seção "RECEITA OPERACIONAL BRUTA" no mesmo mês (base = 100%).
    """
    from services.usina_service import listar_lancamentos, listar_contas_da_usina

    meses = _meses_intervalo(mes_inicio, mes_fim)
    contas = listar_contas_da_usina(usina_id)
    if not contas or not meses:
        return {"meses": meses, "valores": {}, "percentuais": {}, "lancamentos": {}, "naturezas": {}}

    data_ini = mes_inicio + "-01"
    import calendar
    ano, mes = int(mes_fim[:4]), int(mes_fim[5:7])
    ultimo_dia = calendar.monthrange(ano, mes)[1]
    data_fim = f"{mes_fim}-{ultimo_dia:02d}"

    lancamentos = []
    for conta in contas:
        lancs = listar_lancamentos(usina_id, conta_id=conta["id"])
        lancamentos += [l for l in lancs
                        if l.get("conciliado")
                        and l.get("data_transacao")
                        and data_ini <= l["data_transacao"][:10] <= data_fim
                        and not l.get("_neutro")
                        and not l.get("_rendimento")
                        and l.get("categorias_financeiras")]

    # Soma e lista de lançamentos por categoria_id, quebrado por mês
    soma_por_cat: dict = {}   # cid -> {mes: valor}
    lancs_por_cat: dict = {}  # cid -> {mes: [...]}
    for l in lancamentos:
        cat = l.get("categorias_financeiras") or {}
        cid = cat.get("id")
        if not cid:
            continue
        mes_l = (l.get("data_transacao") or "")[:7]
        valor = float(l.get("valor") or 0)
        contrib = valor if l.get("tipo") == "credito" else -abs(valor)
        soma_por_cat.setdefault(cid, {})
        soma_por_cat[cid][mes_l] = soma_por_cat[cid].get(mes_l, 0.0) + contrib
        lancs_por_cat.setdefault(cid, {}).setdefault(mes_l, []).append({
            "data": (l.get("data_transacao") or "")[:10],
            "descricao": l.get("descricao") or l.get("descricao_original") or "",
            "valor": contrib,
        })

    secoes = listar_secoes_dre()

    valor_por_secao: dict = {}      # secao_id -> {mes: valor}
    lancs_por_secao: dict = {}      # secao_id -> {mes: [...]}
    naturezas_por_secao: dict = {}  # secao_id -> [{"id","nome","valores":{mes:valor}}]

    for s in secoes:
        if s["tipo"] == "linha":
            valor_por_secao[s["id"]] = {}
            lancs_por_secao[s["id"]] = {}
            naturezas_por_secao[s["id"]] = [
                {
                    "id": c["id"],
                    "nome": c["nome"],
                    "valores": {mes_l: soma_por_cat.get(c["id"], {}).get(mes_l, 0.0) for mes_l in meses},
                }
                for c in s["categorias_vinculadas"]
            ]
            for mes_l in meses:
                total = sum(soma_por_cat.get(c["id"], {}).get(mes_l, 0.0) for c in s["categorias_vinculadas"])
                valor_por_secao[s["id"]][mes_l] = total
                items = []
                for c in s["categorias_vinculadas"]:
                    items += lancs_por_cat.get(c["id"], {}).get(mes_l, [])
                lancs_por_secao[s["id"]][mes_l] = sorted(items, key=lambda x: x["data"])

    for s in secoes:
        if s["tipo"] == "totalizador" and s.get("formula_json"):
            valor_por_secao[s["id"]] = {}
            for mes_l in meses:
                total = 0.0
                for item in s["formula_json"]:
                    sid = item.get("secao_id")
                    op = item.get("op", "+")
                    v = valor_por_secao.get(sid, {}).get(mes_l, 0.0)
                    total += v if op == "+" else -v
                valor_por_secao[s["id"]][mes_l] = total

        elif s["tipo"] == "categoria":
            filhas = [x for x in secoes if x.get("parent_id") == s["id"]]
            valor_por_secao[s["id"]] = {}
            for mes_l in meses:
                total = sum(valor_por_secao.get(f["id"], {}).get(mes_l, 0.0) for f in filhas)
                valor_por_secao[s["id"]][mes_l] = total

    # Base dos percentuais: seção "RECEITA OPERACIONAL BRUTA" = 100% do mês
    base_secao = next(
        (s for s in secoes if (s.get("nome") or "").strip().upper() == "RECEITA OPERACIONAL BRUTA"),
        None,
    )
    base_por_mes = valor_por_secao.get(base_secao["id"], {}) if base_secao else {}

    def _pct(valor: float, mes_l: str) -> float:
        base = base_por_mes.get(mes_l, 0.0)
        return round(valor / base * 100, 2) if base else 0.0

    percentual_por_secao = {
        sid: {mes_l: _pct(vals.get(mes_l, 0.0), mes_l) for mes_l in meses}
        for sid, vals in valor_por_secao.items()
    }
    for naturezas in naturezas_por_secao.values():
        for nat in naturezas:
            nat["percentuais"] = {mes_l: _pct(nat["valores"].get(mes_l, 0.0), mes_l) for mes_l in meses}

    return {
        "meses": meses,
        "valores": valor_por_secao,
        "percentuais": percentual_por_secao,
        "lancamentos": lancs_por_secao,
        "naturezas": naturezas_por_secao,
    }
