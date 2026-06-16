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

def calcular_dre(usina_id: str, mes_inicio: str, mes_fim: str) -> dict:
    """
    Retorna {secao_id: valor} para todas as linhas e totalizadores.
    mes_inicio / mes_fim no formato YYYY-MM.
    """
    from services.conciliacao_service import listar_lancamentos
    from services.usina_service import listar_contas_da_usina

    contas = listar_contas_da_usina(usina_id)
    if not contas:
        return {}

    # Busca lançamentos conciliados do período
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
                        and not l.get("_rendimento")]

    # Soma por categoria_id
    soma_por_cat: dict = {}
    for l in lancamentos:
        cat = l.get("categorias_financeiras") or {}
        cid = cat.get("id")
        if not cid:
            continue
        valor = float(l.get("valor") or 0)
        if l.get("tipo") == "credito":
            soma_por_cat[cid] = soma_por_cat.get(cid, 0.0) + valor
        else:
            soma_por_cat[cid] = soma_por_cat.get(cid, 0.0) - abs(valor)

    # Busca estrutura DRE com vínculos
    secoes = listar_secoes_dre()

    # Calcula valor de cada linha (soma das categorias vinculadas)
    valor_por_secao: dict = {}
    for s in secoes:
        if s["tipo"] == "linha":
            total = sum(soma_por_cat.get(c["id"], 0.0) for c in s["categorias_vinculadas"])
            valor_por_secao[s["id"]] = total

    # Calcula totalizadores com base na formula_json
    # Ordena para garantir que totalizadores são calculados após suas dependências
    for s in secoes:
        if s["tipo"] == "totalizador" and s.get("formula_json"):
            total = 0.0
            for item in s["formula_json"]:
                sid = item.get("secao_id")
                op = item.get("op", "+")
                v = valor_por_secao.get(sid, 0.0)
                total += v if op == "+" else -v
            valor_por_secao[s["id"]] = total

        elif s["tipo"] == "categoria":
            # Soma das linhas filhas
            filhas = [x for x in secoes if x.get("parent_id") == s["id"]]
            total = sum(valor_por_secao.get(f["id"], 0.0) for f in filhas)
            valor_por_secao[s["id"]] = total

    return valor_por_secao
