from typing import Optional
from services.supabase_client import get_service_client

_CORES = ["#E8621A", "#2563EB", "#16A34A", "#9333EA", "#EAB308", "#EC4899"]

# Descricoes que representam movimentos internos de fundo (não contam como receita/despesa operacional)
_DESCRICOES_NEUTRAS = ["RESGATE FUNDOS", "APLICACAO FUNDO", "APLICAÇÃO FUNDO"]

def _eh_neutro(l: dict) -> bool:
    desc = (l.get("descricao") or l.get("descricao_original") or "").upper()
    return any(kw in desc for kw in _DESCRICOES_NEUTRAS)


def auto_conciliar_neutros(usina_id: str) -> None:
    """Marca como conciliado (sem categoria) lançamentos neutros e transferências pendentes."""
    contas = listar_contas_da_usina(usina_id)
    if not contas:
        return
    sb = get_service_client()
    nomes = _nomes_transferencia()
    for conta in contas:
        conta_id = conta["id"]
        # fundos
        for kw in _DESCRICOES_NEUTRAS:
            try:
                sb.table("lancamentos_bancarios") \
                  .update({"conciliado": True, "categoria_id": None}) \
                  .eq("conta_bancaria_id", conta_id) \
                  .eq("conciliado", False) \
                  .ilike("descricao_original", f"%{kw}%") \
                  .execute()
            except Exception:
                pass
        # transferências entre contas
        try:
            pendentes = (
                sb.table("lancamentos_bancarios")
                .select("id, descricao_original, descricao")
                .eq("conta_bancaria_id", conta_id)
                .eq("conciliado", False)
                .execute()
            ).data or []
            ids_transf = [r["id"] for r in pendentes if _eh_transferencia(r, nomes)]
            for tid in ids_transf:
                sb.table("lancamentos_bancarios") \
                  .update({"conciliado": True, "categoria_id": None}) \
                  .eq("id", tid) \
                  .execute()
        except Exception:
            pass


def _nome(u: dict) -> str:
    return u.get("nome_fantasia") or u.get("razao_social") or ""


def _iniciais(nome: str) -> str:
    partes = nome.strip().split()
    if not partes:
        return "?"
    if len(partes) == 1:
        return partes[0][:2].upper()
    return (partes[0][0] + partes[-1][0]).upper()


def _enriquecer_socios(usinas: list) -> list:
    """Anexa lista de sócios (participacoes + investidores) a cada usina."""
    if not usinas:
        return usinas
    usina_ids = [u["id"] for u in usinas]
    partic_res = (
        get_service_client()
        .table("participacoes")
        .select("usina_id, percentual, investidores(id, nome)")
        .in_("usina_id", usina_ids)
        .execute()
    )
    socios_map: dict = {}
    for p in (partic_res.data or []):
        uid = p["usina_id"]
        inv = p.get("investidores") or {}
        nome = inv.get("nome", "?") if isinstance(inv, dict) else "?"
        socios_map.setdefault(uid, []).append({
            "percentual": float(p.get("percentual") or 0),
            "nome": nome,
        })
    for u in usinas:
        u["nome"] = _nome(u)
        socios = socios_map.get(u["id"], [])
        socios.sort(key=lambda s: s["percentual"], reverse=True)
        for i, s in enumerate(socios):
            s["cor"] = _CORES[i % len(_CORES)]
            s["iniciais"] = _iniciais(s["nome"])
        u["socios"] = socios
        u["maior_percentual"] = socios[0]["percentual"] if socios else 0
    return usinas


def usinas_do_usuario(usina_ids: list) -> list:
    """Busca usinas por IDs de acesso (user_metadata.usina_ids)."""
    if not usina_ids:
        return []
    sb = get_service_client()
    res = sb.table("usinas").select("*").in_("id", usina_ids).order("razao_social").execute()
    usinas = res.data or []
    for u in usinas:
        u["nome"] = _nome(u)
    return usinas


def listar_usinas() -> list:
    """Todas as usinas com sócios — admin dashboard."""
    sb = get_service_client()
    res = sb.table("usinas").select("*").order("razao_social").execute()
    return _enriquecer_socios(res.data or [])


def usinas_do_investidor(investidor_id: str) -> list:
    """Participações do investidor com dados da usina embutidos."""
    sb = get_service_client()
    res = (
        sb.table("participacoes")
        .select("*, usinas(*)")
        .eq("investidor_id", investidor_id)
        .execute()
    )
    participacoes = res.data or []
    for p in participacoes:
        if p.get("usinas"):
            p["usinas"]["nome"] = _nome(p["usinas"])
    return participacoes


def buscar_usina(usina_id: str) -> Optional[dict]:
    sb = get_service_client()
    res = sb.table("usinas").select("*").eq("id", usina_id).maybe_single().execute()
    data = res.data
    if data:
        data["nome"] = _nome(data)
    return data


def participacoes_da_usina(usina_id: str) -> list:
    sb = get_service_client()
    res = (
        sb.table("participacoes")
        .select("*, investidores(id, nome, email, cpf_cnpj)")
        .eq("usina_id", usina_id)
        .execute()
    )
    data = res.data or []
    for i, p in enumerate(data):
        p["cor"] = _CORES[i % len(_CORES)]
        inv = p.get("investidores") or {}
        nome = inv.get("nome", "?") if isinstance(inv, dict) else "?"
        p["iniciais"] = _iniciais(nome)
    return data


def distribuicoes_da_usina(usina_id: str, limite: int = 24) -> list:
    sb = get_service_client()
    res = (
        sb.table("distribuicoes")
        .select("*")
        .eq("usina_id", usina_id)
        .order("ref_mes_ano", desc=True)
        .limit(limite)
        .execute()
    )
    return res.data or []


def leituras_da_usina(usina_id: str, limite: int = 12) -> list:
    """Leituras mensais via contratos → usina (leituras_mensais não tem usina_id direto)."""
    sb = get_service_client()
    contratos_res = (
        sb.table("contratos")
        .select("id")
        .eq("usina_id", usina_id)
        .execute()
    )
    contrato_ids = [c["id"] for c in (contratos_res.data or [])]
    if not contrato_ids:
        return []
    res = (
        sb.table("leituras_mensais")
        .select("ref_mes_ano, kwh_compensado, kwh_faturado_cosern, valor_fatura_cosern_real, status_pagamento_cosern")
        .in_("contrato_id", contrato_ids)
        .order("ref_mes_ano", desc=True)
        .limit(limite)
        .execute()
    )
    return res.data or []


def pnl_da_usina(usina_id: str, limite: int = 24) -> list:
    sb = get_service_client()

    # Mapeamento categoria_id → coluna custo_*
    _CAT_COSERN    = {"0a71e18a-e7f5-4b88-a37a-1c4ff15d57e1", "9fdfa3c2-7987-42cd-bd83-298061319268"}
    _CAT_ALUGUEL   = {"f3bea762-6139-4fba-9e4f-f997b85988d9"}
    _CAT_CONTAB    = {"873088b5-85ad-4474-9223-9c41f982cff4"}
    _CAT_IMPOSTOS  = {"303b2701-5f8a-4393-ac71-df35d536d946"}
    _CAT_ADMIN     = {"b35c7cda-7285-40ea-8d3a-b54f5966497f"}
    _CAT_EMPRST    = {"d64de0ff-9ae9-49b5-9709-9910dd3f02cc", "20681d4b-e184-4184-9569-fe2f41e61347",
                      "24f512b4-b736-4139-975a-12ef9cb212d0", "622be2cb-22a2-4ce1-9034-5b9624ff15cc"}
    _CAT_OUTROS    = {"9c5c82b4-a8be-4996-9a2d-30722a1479e0", "ef112b4c-a28f-4c5c-840b-c1e27cd1dfb4"}
    # Excluídos de despesas: Repasse Investidor, Aporte, Recebimento de Fatura, Rendimento, Receita de Locação
    _CAT_EXCLUIR   = {"269c12d6-bd47-4386-9ea8-8952c50591c6", "e6c37f6a-37af-4ead-b2c6-7fcc0f12a30e",
                      "f2738e97-9f34-436a-b915-88a28c4cfb7d", "7914bbd1-383e-459e-918b-8563ee795c8a",
                      "e879abc7-4d19-4709-9e77-ea2bb88ba742", "12440b52-0d3c-4c9c-943f-a2408c7ea0ae"}

    def _custo_col(cat_id: Optional[str]) -> Optional[str]:
        if not cat_id:
            return None
        if cat_id in _CAT_COSERN:   return "custo_cosern"
        if cat_id in _CAT_ALUGUEL:  return "custo_aluguel"
        if cat_id in _CAT_CONTAB:   return "custo_contabilidade"
        if cat_id in _CAT_IMPOSTOS: return "custo_impostos"
        if cat_id in _CAT_ADMIN:    return "custo_taxa_admin"
        if cat_id in _CAT_EMPRST:   return "custo_emprestimo"
        if cat_id in _CAT_OUTROS:   return "custo_outros"
        if cat_id in _CAT_EXCLUIR:  return None
        return "custo_outros"  # categorias desconhecidas vão para outros

    # Despesas da view (meses fechados com kwh_faturado, qtd_faturas)
    view_rows = {
        str(r["ref_mes_ano"])[:7]: r
        for r in (sb.table("v_pnl_usina_mensal")
                    .select("*")
                    .eq("usina_id", usina_id)
                    .order("ref_mes_ano", desc=True)
                    .limit(limite)
                    .execute().data or [])
        if r.get("ref_mes_ano")
    }

    # Receita = créditos bancários de mesma titularidade
    usina_row = (sb.table("usinas")
                   .select("razao_social,nome_fantasia")
                   .eq("id", usina_id).execute().data or [{}])[0]
    razao = (usina_row.get("razao_social") or usina_row.get("nome_fantasia") or "")
    razao_titular = razao[:8]
    razao_desc    = razao[:12]

    contas = (sb.from_("contas_bancarias").select("id")
                .ilike("titular_nome", f"%{razao_titular}%")
                .execute().data or [])

    receita_por_mes: dict = {}
    # Despesas calculadas dos lançamentos (para meses fora da view)
    desp_por_mes: dict = {}  # mes → {custo_*: valor}

    for conta in contas:
        all_lancs = (sb.table("lancamentos_bancarios")
                       .select("mes_competencia,data_transacao,valor,tipo,descricao,categoria_id,conciliado")
                       .eq("conta_bancaria_id", conta["id"])
                       .eq("conciliado", True)
                       .execute().data or [])
        for l in all_lancs:
            mes = (l.get("mes_competencia") or l.get("data_transacao") or "")[:7]
            if not mes:
                continue
            if l.get("tipo") == "credito":
                desc = (l.get("descricao") or "").upper()
                if razao_desc.upper() in desc:
                    receita_por_mes[mes] = receita_por_mes.get(mes, 0) + abs(l.get("valor") or 0)
            elif l.get("tipo") == "debito":
                col = _custo_col(l.get("categoria_id"))
                if col:
                    if mes not in desp_por_mes:
                        desp_por_mes[mes] = {
                            "custo_emprestimo": 0, "custo_aluguel": 0, "custo_contabilidade": 0,
                            "custo_impostos": 0, "custo_cosern": 0, "custo_taxa_admin": 0,
                            "custo_seguro": 0, "custo_outros": 0,
                        }
                    desp_por_mes[mes][col] = desp_por_mes[mes].get(col, 0) + abs(l.get("valor") or 0)

    todos_meses = sorted(set(list(view_rows.keys()) + list(receita_por_mes.keys())), reverse=True)[:limite]
    result = []
    for mes in todos_meses:
        if mes in view_rows:
            base = dict(view_rows[mes])
        else:
            # Mês ainda não na view: calcula despesas dos lançamentos
            custos = desp_por_mes.get(mes, {})
            total_desp_lanc = round(sum(custos.values()), 2)
            base = {
                "usina_id": usina_id, "usina": razao,
                "ref_mes_ano": mes + "-01",
                "total_despesas": total_desp_lanc,
                "kwh_faturado": 0, "qtd_faturas": 0,
                "custo_emprestimo":    round(custos.get("custo_emprestimo", 0), 2),
                "custo_aluguel":       round(custos.get("custo_aluguel", 0), 2),
                "custo_contabilidade": round(custos.get("custo_contabilidade", 0), 2),
                "custo_impostos":      round(custos.get("custo_impostos", 0), 2),
                "custo_cosern":        round(custos.get("custo_cosern", 0), 2),
                "custo_taxa_admin":    round(custos.get("custo_taxa_admin", 0), 2),
                "custo_seguro":        0,
                "custo_outros":        round(custos.get("custo_outros", 0), 2),
            }

        receita    = round(receita_por_mes.get(mes, 0), 2)
        total_desp = round(base.get("total_despesas") or 0, 2)
        resultado  = round(receita - total_desp, 2)
        base["receita_bruta"]     = receita
        base["resultado_liquido"] = resultado
        base["margem_liquida"]    = round(resultado / receita, 4) if receita else None
        result.append(base)

    return result


def retorno_mensal_investidor(usina_id: str, investidor_id: str = None) -> list:
    sb = get_service_client()
    q = (
        sb.table("v_retorno_investidor_mensal")
        .select("ref_mes_ano,resultado_usina,participacao_lucro,valor_distribuido,percentual,investidor")
        .eq("usina_id", usina_id)
        .order("ref_mes_ano")
    )
    if investidor_id:
        q = q.eq("investidor_id", investidor_id)
    return q.execute().data or []


def leituras_detalhadas(usina_id: str) -> list:
    sb = get_service_client()
    contratos_res = (
        sb.table("contratos")
        .select("id,numero_contrato")
        .eq("usina_id", usina_id)
        .execute()
    )
    contrato_ids = [c["id"] for c in (contratos_res.data or [])]
    if not contrato_ids:
        return []
    res = (
        sb.table("leituras_mensais")
        .select(
            "ref_mes_ano,contrato_id,kwh_compensado,kwh_consumido,kwh_faturado_cosern,"
            "saldo_creditos_kwh,tarifa_cheia,tarifa_compensacao_cheia,"
            "valor_fatura_simulada,valor_fatura_cosern_real"
        )
        .in_("contrato_id", contrato_ids)
        .order("ref_mes_ano")
        .execute()
    )
    contratos_map = {c["id"]: c.get("numero_contrato", "") for c in (contratos_res.data or [])}
    for l in (res.data or []):
        l["numero_contrato"] = contratos_map.get(l["contrato_id"], "")
    return res.data or []


def saldo_creditos_da_usina(usina_id: str) -> list:
    sb = get_service_client()
    contratos_res = (
        sb.table("contratos")
        .select("id,numero_contrato")
        .eq("usina_id", usina_id)
        .execute()
    )
    contrato_ids = [c["id"] for c in (contratos_res.data or [])]
    if not contrato_ids:
        return []
    res = (
        sb.table("v_saldo_creditos")
        .select("*")
        .in_("contrato_id", contrato_ids)
        .execute()
    )
    contratos_map = {c["id"]: c.get("numero_contrato", "") for c in (contratos_res.data or [])}
    for s in (res.data or []):
        s["numero_contrato"] = contratos_map.get(s["contrato_id"], "")
    return res.data or []


def documentos_da_usina(usina_id: str) -> list:
    sb = get_service_client()
    res = (
        sb.table("portal_documentos")
        .select("*")
        .eq("usina_id", usina_id)
        .order("created_at", desc=True)
        .execute()
    )
    return res.data or []


def atualizar_usina(usina_id: str, dados: dict) -> dict:
    sb = get_service_client()
    _permitidos = {
        "razao_social", "nome_fantasia", "cnpj", "banco", "agencia",
        "conta_corrente", "status", "modalidade_gd", "potencia_instalada_kwp",
        "geracao_media_mensal_kwh", "data_inicio_operacao", "observacoes",
        "valor_aluguel_terra", "valor_contabilidade", "valor_taxa_administracao",
        "pix_chave", "pix_tipo", "pix_titular_nome",
    }
    campos = {k: v for k, v in dados.items() if k in _permitidos}
    if campos:
        sb.table("usinas").update(campos).eq("id", usina_id).execute()
    return {"ok": True}


def adicionar_distribuicao(usina_id: str, dados: dict) -> dict:
    sb = get_service_client()
    try:
        sb.table("distribuicoes").insert({
            "usina_id": usina_id,
            "ref_mes_ano": dados.get("ref_mes_ano"),
            "valor": float(dados.get("valor") or 0),
            "data_pagamento": dados.get("data_pagamento") or None,
            "observacoes": dados.get("observacoes") or None,
        }).execute()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "erro": str(e)}


def upload_documento(usina_id: str, nome: str, categoria: str,
                     conteudo: bytes, mime_type: str,
                     user_id: Optional[str] = None) -> dict:
    import uuid
    sb = get_service_client()
    arquivo_id = str(uuid.uuid4())
    caminho = f"{usina_id}/{categoria}/{arquivo_id}_{nome}"
    try:
        sb.storage.from_("documentos").upload(
            caminho, conteudo, {"content-type": mime_type}
        )
        url = sb.storage.from_("documentos").get_public_url(caminho)
        sb.table("portal_documentos").insert({
            "usina_id": usina_id,
            "nome": nome,
            "categoria": categoria,
            "file_url": url,
            "file_size": len(conteudo),
            "uploaded_by": user_id,
            "visivel_a_todos": True,
        }).execute()
        return {"ok": True, "url": url}
    except Exception as e:
        return {"ok": False, "erro": str(e)}


def listar_categorias() -> list:
    try:
        sb = get_service_client()
        res = (
            sb.table("categorias_financeiras")
            .select("id, nome, tipo")
            .eq("ativo", True)
            .order("tipo")
            .order("nome")
            .execute()
        )
        return res.data or []
    except Exception:
        return []


def categorias_em_uso() -> set:
    """Returns set of categoria_ids referenced by at least one lancamento."""
    try:
        sb = get_service_client()
        res = (
            sb.table("lancamentos_bancarios")
            .select("categoria_id")
            .not_.is_("categoria_id", "null")
            .execute()
        )
        return {r["categoria_id"] for r in (res.data or [])}
    except Exception:
        return set()


def criar_categoria(nome: str, tipo: str) -> dict:
    try:
        sb = get_service_client()
        sb.table("categorias_financeiras").insert({
            "nome": nome.strip(),
            "tipo": tipo,
            "ativo": True,
        }).execute()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "erro": str(e)}


def deletar_categoria(cat_id: str) -> dict:
    em_uso = categorias_em_uso()
    if cat_id in em_uso:
        return {"ok": False, "em_uso": True}
    try:
        sb = get_service_client()
        sb.table("categorias_financeiras").update({"ativo": False}).eq("id", cat_id).execute()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "erro": str(e)}


def _conta_bancaria_da_usina(usina_id: str) -> Optional[str]:
    """Retorna a primeira conta bancária da usina (compat. com código legado)."""
    contas = listar_contas_da_usina(usina_id)
    return contas[0]["id"] if contas else None


def listar_contas_da_usina(usina_id: str) -> list:
    sb = get_service_client()
    try:
        uc_res = (
            sb.table("usina_contas")
            .select("conta_bancaria_id")
            .eq("usina_id", usina_id)
            .execute()
        )
        ids = [r["conta_bancaria_id"] for r in (uc_res.data or [])]
        if not ids:
            return []
        cb_res = (
            sb.table("contas_bancarias")
            .select("id, apelido, titular_nome, banco, agencia, numero_conta, tipo, saldo_inicial, data_saldo_inicial")
            .in_("id", ids)
            .eq("ativa", True)
            .execute()
        )
        return cb_res.data or []
    except Exception:
        return []


def _nomes_transferencia() -> set:
    """Conjunto de nomes de entidades conhecidas para detectar transferências entre contas."""
    sb = get_service_client()
    nomes: set = set()
    try:
        for row in (sb.table("contas_bancarias").select("titular_nome").execute().data or []):
            n = (row.get("titular_nome") or "").strip().upper()
            if n:
                nomes.add(n)
    except Exception:
        pass
    try:
        for row in (sb.table("usinas").select("razao_social,nome_fantasia").execute().data or []):
            for campo in ("razao_social", "nome_fantasia"):
                n = (row.get(campo) or "").strip().upper()
                if n:
                    nomes.add(n)
    except Exception:
        pass
    try:
        for row in (sb.table("investidores").select("nome").execute().data or []):
            n = (row.get("nome") or "").strip().upper()
            if n:
                nomes.add(n)
    except Exception:
        pass
    return nomes


def _eh_transferencia(l: dict, nomes: set) -> bool:
    desc = (l.get("descricao") or l.get("descricao_original") or "").upper()
    return any(nome in desc for nome in nomes if len(nome) > 4)


def clientes_da_usina(usina_id: str) -> list:
    """Clientes da usina via contratos → ucs → clientes."""
    try:
        sb = get_service_client()
        res = (
            sb.table("contratos")
            .select("id, numero_contrato, percentual_desconto, dia_vencimento, status, data_inicio_compensacao, ucs(id, codigo_uc, apelido, endereco, classificacao, clientes(id, razao_social, nome_fantasia, cpf_cnpj, email, telefone, status))")
            .eq("usina_id", usina_id)
            .order("status")
            .execute()
        )
        resultado = []
        for c in (res.data or []):
            uc = c.get("ucs") or {}
            cli = (uc.get("clientes") or {}) if isinstance(uc, dict) else {}
            resultado.append({
                "id": c.get("id"),
                "numero_contrato": c.get("numero_contrato"),
                "percentual_desconto": c.get("percentual_desconto"),
                "dia_vencimento": c.get("dia_vencimento"),
                "status": c.get("status"),
                "data_inicio_compensacao": c.get("data_inicio_compensacao"),
                "codigo_uc": uc.get("codigo_uc") or "—" if isinstance(uc, dict) else "—",
                "apelido_uc": uc.get("apelido") or "" if isinstance(uc, dict) else "",
                "classificacao_uc": uc.get("classificacao") or "" if isinstance(uc, dict) else "",
                "endereco_uc": uc.get("endereco") or "" if isinstance(uc, dict) else "",
                "nome_cliente": (cli.get("nome_fantasia") or cli.get("razao_social") or "—") if isinstance(cli, dict) else "—",
                "cpf_cnpj": cli.get("cpf_cnpj") or "—" if isinstance(cli, dict) else "—",
                "email_cliente": cli.get("email") or "" if isinstance(cli, dict) else "",
                "status_cliente": cli.get("status") or "" if isinstance(cli, dict) else "",
            })
        return resultado
    except Exception as e:
        return []


def listar_lancamentos(usina_id: str, conta_id: Optional[str] = None) -> list:
    if not conta_id:
        contas = listar_contas_da_usina(usina_id)
        conta_id = contas[0]["id"] if contas else None
    if not conta_id:
        return []
    try:
        sb = get_service_client()
        res = (
            sb.table("lancamentos_bancarios")
            .select("*, categorias_financeiras(id, nome, tipo)")
            .eq("conta_bancaria_id", conta_id)
            .order("data_transacao", desc=True)
            .limit(500)
            .execute()
        )
        lancamentos = res.data or []
        nomes = _nomes_transferencia()
        for l in lancamentos:
            l["_transferencia"] = _eh_transferencia(l, nomes)
            l["_neutro"] = _eh_neutro(l)
            # Lançamento com categoria de receita/despesa explícita nunca é transferência
            cat = l.get("categorias_financeiras") or {}
            if isinstance(cat, dict) and cat.get("tipo") in ("receita", "despesa"):
                l["_transferencia"] = False
        return lancamentos
    except Exception:
        return []


def criar_lancamento(usina_id: str, dados: dict) -> dict:
    conta_id = _conta_bancaria_da_usina(usina_id)
    if not conta_id:
        return {"ok": False, "erro": "Conta bancária não configurada para esta usina."}
    sb = get_service_client()
    try:
        row = {
            "conta_bancaria_id": conta_id,
            "data_transacao": dados["data"],
            "mes_competencia": dados["data"][:8] + "01",
            "descricao": str(dados.get("descricao", "")).strip(),
            "descricao_original": str(dados.get("descricao", "")).strip(),
            "valor": abs(float(dados.get("valor") or 0)),
            "tipo": dados.get("tipo", "debito"),
            "conciliado": True,
            "observacoes": dados.get("observacao") or None,
        }
        if dados.get("categoria_id"):
            row["categoria_id"] = dados["categoria_id"]
        sb.table("lancamentos_bancarios").insert(row).execute()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "erro": str(e)}


def conciliar_lancamento(lancamento_id: str, categoria_id: str,
                         observacao: Optional[str] = None,
                         comprovante_url: Optional[str] = None) -> dict:
    sb = get_service_client()
    try:
        upd: dict = {"conciliado": True}
        if categoria_id:
            upd["categoria_id"] = categoria_id
        if observacao:
            upd["observacoes"] = observacao
        sb.table("lancamentos_bancarios").update(upd).eq("id", lancamento_id).execute()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "erro": str(e)}


def _is_dup_bancario(sb, conta_id: str, data: str, descricao: str,
                     valor: float, fitid: Optional[str] = None) -> bool:
    if fitid:
        res = (
            sb.table("lancamentos_bancarios")
            .select("id")
            .eq("conta_bancaria_id", conta_id)
            .eq("fitid", fitid)
            .limit(1)
            .execute()
        )
        return bool(res.data)
    res = (
        sb.table("lancamentos_bancarios")
        .select("id")
        .eq("conta_bancaria_id", conta_id)
        .eq("data_transacao", data)
        .eq("descricao_original", descricao)
        .eq("valor", valor)
        .limit(1)
        .execute()
    )
    return bool(res.data)


def _parse_xlsx(conteudo: bytes) -> list:
    try:
        import openpyxl
    except ImportError:
        raise Exception("openpyxl não instalado.")
    from io import BytesIO
    wb = openpyxl.load_workbook(BytesIO(conteudo), data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []

    def norm(s):
        s = str(s or "").strip().lower()
        for a, b in [("ç","c"),("ã","a"),("á","a"),("é","e"),("ó","o"),("ú","u")]:
            s = s.replace(a, b)
        return s

    headers = [norm(c) for c in rows[0]]

    def idx(*names):
        for n in names:
            if n in headers:
                return headers.index(n)
        return None

    i_data = idx("data", "dt", "date", "data transacao")
    i_desc = idx("descricao", "historico", "memo", "descricao/historico", "descricao original")
    i_val  = idx("valor")
    i_cred = idx("credito", "entrada", "credit")
    i_deb  = idx("debito", "saida", "debit")

    resultado = []
    for row in rows[1:]:
        if not any(row):
            continue
        try:
            dv  = row[i_data] if i_data is not None else row[0]
            dv2 = row[i_desc] if i_desc is not None else row[1]
            if dv is None or dv2 is None:
                continue
            data_str = dv.strftime("%Y-%m-%d") if hasattr(dv, "strftime") else str(dv)[:10]
            if i_cred is not None and i_deb is not None:
                cred = float(row[i_cred] or 0)
                deb  = float(row[i_deb]  or 0)
                valor, tipo = (cred, "credito") if cred else (deb, "debito")
            elif i_val is not None:
                v = float(row[i_val] or 0)
                valor, tipo = abs(v), ("credito" if v >= 0 else "debito")
            else:
                v = float(row[2] or 0)
                valor, tipo = abs(v), ("credito" if v >= 0 else "debito")
            if not valor:
                continue
            resultado.append({
                "data_transacao": data_str,
                "descricao_original": str(dv2).strip(),
                "valor": round(valor, 2),
                "tipo": tipo,
            })
        except Exception:
            continue
    return resultado


def _parse_ofx(conteudo: bytes) -> list:
    try:
        from ofxparse import OfxParser
    except ImportError:
        raise Exception("ofxparse não instalado.")
    from io import BytesIO
    ofx = OfxParser.parse(BytesIO(conteudo))
    resultado = []
    for acc in getattr(ofx, "accounts", []):
        stmt = getattr(acc, "statement", None)
        if not stmt:
            continue
        for t in getattr(stmt, "transactions", []):
            v = float(t.amount)
            resultado.append({
                "data_transacao": t.date.strftime("%Y-%m-%d"),
                "descricao_original": (getattr(t, "memo", "") or getattr(t, "payee", "") or "").strip(),
                "valor": round(abs(v), 2),
                "tipo": "credito" if v >= 0 else "debito",
                "fitid": getattr(t, "id", None) or None,
            })
    return resultado


def importar_extrato(usina_id: str, conteudo: bytes, extensao: str,
                     importado_por: Optional[str] = None) -> dict:
    conta_id = _conta_bancaria_da_usina(usina_id)
    if not conta_id:
        return {"ok": False, "erro": "Conta bancária não configurada para esta usina."}
    try:
        lancamentos = _parse_ofx(conteudo) if extensao.lower() == "ofx" \
                      else _parse_xlsx(conteudo)
    except Exception as e:
        return {"ok": False, "erro": str(e)}
    if not lancamentos:
        return {"ok": False, "erro": "Nenhum lançamento encontrado no arquivo."}

    sb = get_service_client()
    inseridos = 0
    duplicados = 0

    ofx_id = None
    if extensao.lower() == "ofx" and lancamentos:
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
            if r.data:
                ofx_id = r.data[0]["id"]
        except Exception:
            pass

    for l in lancamentos:
        fitid = l.get("fitid")
        if _is_dup_bancario(sb, conta_id, l["data_transacao"],
                            l["descricao_original"], l["valor"], fitid):
            duplicados += 1
            continue
        try:
            neutro = _eh_neutro(l)
            row = {
                "conta_bancaria_id": conta_id,
                "data_transacao": l["data_transacao"],
                "mes_competencia": l["data_transacao"][:8] + "01",
                "descricao_original": l["descricao_original"],
                "descricao": l["descricao_original"],
                "valor": l["valor"],
                "tipo": l["tipo"],
                "conciliado": neutro,
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


def _mes_anterior(mes: str) -> str:
    y, m = int(mes[:4]), int(mes[5:7])
    m -= 1
    if m == 0:
        m, y = 12, y - 1
    return f"{y:04d}-{m:02d}"


def calcular_kpis(usina_id: str, mes: str, lancamentos: list,
                  leituras: list, pnl: list, num_contratos_ativos: int = 0) -> dict:
    def fil_lanc(lst, m):
        return [l for l in lst if (l.get("data_transacao") or "")[:7] == m]

    def fil_leit(lst, m):
        return [l for l in lst if (l.get("ref_mes_ano") or "")[:7] == m]

    def _operacional(l):
        return not l.get("_neutro") and not l.get("_transferencia") and not _eh_neutro(l)

    mes_ant  = _mes_anterior(mes)
    lanc_mes = [l for l in fil_lanc(lancamentos, mes) if _operacional(l)]
    lanc_ant = [l for l in fil_lanc(lancamentos, mes_ant) if _operacional(l)]
    leit_mes = fil_leit(leituras, mes)

    def soma(lst, tipo=None, apenas_conciliado=True):
        return sum(
            abs(l.get("valor") or 0) for l in lst
            if (not apenas_conciliado or l.get("conciliado"))
            and (tipo is None or l.get("tipo") == tipo)
        )

    receita   = soma(lanc_mes, "credito",  apenas_conciliado=False)
    saidas    = soma(lanc_mes, "debito",   apenas_conciliado=False)
    resultado = receita - saidas
    rec_ant   = soma(lanc_ant, "credito",  apenas_conciliado=False)
    sai_ant   = soma(lanc_ant, "debito",   apenas_conciliado=False)
    res_ant   = rec_ant - sai_ant

    def var(atual, anterior):
        if not anterior:
            return None
        return round((atual - anterior) / abs(anterior) * 100, 1)

    geracao_kwh = sum(l.get("kwh_compensado") or 0 for l in leit_mes)
    num_clientes = num_contratos_ativos or len(leit_mes)
    inadimplencia = 0.0

    return {
        "receita_total":     round(receita, 2),
        "total_saidas":      round(saidas, 2),
        "resultado_liquido": round(resultado, 2),
        "rentabilidade":     round(resultado / receita * 100, 1) if receita else 0,
        "geracao_kwh":       round(geracao_kwh, 0),
        "num_clientes":      num_clientes,
        "ticket_medio":      round(receita / num_clientes, 2) if num_clientes else 0,
        "distribuido":       0.0,
        "a_distribuir":      round(resultado, 2),
        "inadimplencia":     inadimplencia,
        "receita_var":       var(receita, rec_ant),
        "saidas_var":        var(saidas, sai_ant),
        "resultado_var":     var(resultado, res_ant),
    }


def excluir_documento(doc_id: str) -> dict:
    sb = get_service_client()
    try:
        sb.table("portal_documentos").delete().eq("id", doc_id).execute()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "erro": str(e)}
