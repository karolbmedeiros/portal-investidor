import unicodedata
import re
import json
import os
from typing import Optional
from services.supabase_client import get_financeiro_client, get_service_client

_CLIENTES_CACHE: Optional[dict] = None

def listar_naturezas_carros() -> list:
    try:
        sb = get_financeiro_client()
        rows = sb.table("naturezas_carros").select("nome,tipo").order("nome").execute().data or []
        return rows  # lista de dicts {nome, tipo}
    except Exception:
        return []

def adicionar_natureza_carros(nome: str, tipo: str = "saida") -> dict:
    nome = nome.strip()
    if not nome or tipo not in ("entrada", "saida"):
        return {"ok": False, "erro": "Nome ou tipo inválido"}
    try:
        get_financeiro_client().table("naturezas_carros").insert({"nome": nome, "tipo": tipo}).execute()
        return {"ok": True}
    except Exception as e:
        err = str(e)
        if "duplicate" in err.lower() or "unique" in err.lower():
            return {"ok": False, "erro": "Já existe"}
        return {"ok": False, "erro": err}

def remover_natureza_carros(nome: str) -> dict:
    try:
        get_financeiro_client().table("naturezas_carros").delete().eq("nome", nome).execute()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "erro": str(e)}

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


_EMPRESA_CONTA = {
    "LUZ DIVINA EMPREENDIMENTOS LTDA":            "f02a7e50-de24-4db0-913a-cc4309b21b1a",
    "JOÃO PAULO SERVIÇOS EM CONSULTORIA LTDA":    "bcc0fb80-4960-44c8-aa68-2b3c9e2d6a06",
}

_CONVENIO_KEYWORDS = [
    "GELO E GELA",
    "JUAN E IVAN",
]

def calcular_saldo_em(empresa_nome: str, data_ate: str) -> float:
    """Soma todos os lançamentos da conta (sem filtros) até data_ate inclusive.
    Usado para obter o saldo real antes das transações carros começarem.
    """
    conta_id = _EMPRESA_CONTA.get(empresa_nome)
    if not conta_id:
        return 0.0
    try:
        sb = get_financeiro_client()
        rows = (
            sb.table("lancamentos_bancarios")
            .select("valor")
            .eq("conta_bancaria_id", conta_id)
            .lte("data_transacao", data_ate)
            .execute()
            .data or []
        )
        return round(sum(float(r.get("valor") or 0) for r in rows), 2)
    except Exception as e:
        print(f"[calcular_saldo_em] erro: {e}")
        return 0.0


def listar_lancamentos_carros(empresa_nome: str) -> list:
    conta_id = _EMPRESA_CONTA.get(empresa_nome)
    if not conta_id:
        return []
    try:
        sb = get_financeiro_client()
        rows = (
            sb.table("lancamentos_bancarios")
            .select("id,fitid,data_transacao,mes_competencia,descricao,descricao_original,valor,tipo,conciliado,observacoes")
            .eq("conta_bancaria_id", conta_id)
            .order("data_transacao", desc=True)
            .execute()
            .data or []
        )
        for r in rows:
            texto = ((r.get("descricao") or "") + (r.get("descricao_original") or "")).upper()
            r["_convenio"] = any(k in texto for k in _CONVENIO_KEYWORDS)
        return rows
    except Exception as e:
        print(f"[listar_lancamentos_carros] erro: {e}")
        return []


def classificar_lancamento_carros(lancamento_id: str, natureza: str = None, splits: list = None) -> dict:
    if not lancamento_id or (not natureza and not splits):
        return {"ok": False, "erro": "id e natureza/splits obrigatorios"}
    try:
        sb = get_financeiro_client()
        obs = json.dumps(splits, ensure_ascii=False) if splits else natureza
        sb.table("lancamentos_bancarios") \
          .update({"observacoes": obs, "conciliado": True}) \
          .eq("id", lancamento_id) \
          .execute()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "erro": str(e)}


def salvar_dados_cliente(dados: dict) -> dict:
    """Persiste as alterações de um cliente no JSON local e invalida o cache."""
    global _CLIENTES_CACHE
    path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "static", "data", "clientes_carros.json")
    )
    try:
        try:
            with open(path, encoding="utf-8") as f:
                store = json.load(f)
        except Exception:
            store = {}

        placa = (dados.get("placa") or dados.get("ref_id") or "").upper().replace("-", "").replace(" ", "")
        if not placa:
            return {"ok": False, "erro": "Placa/ref_id obrigatório"}

        entrada = store.get(placa, {})
        campos = ["nome", "cpf", "telefone", "endereco", "cep", "placa",
                  "ano_modelo", "marca", "cor", "chassi", "num_motor",
                  "contrato_locacao", "contrato_comercial", "tipo_contrato",
                  "unidade", "inicio", "termino"]
        for c in campos:
            if c in dados:
                entrada[c] = dados[c]
        store[placa] = entrada

        with open(path, "w", encoding="utf-8") as f:
            json.dump(store, f, ensure_ascii=False, indent=2)

        _CLIENTES_CACHE = store
        return {"ok": True, "dados": entrada}
    except Exception as e:
        return {"ok": False, "erro": str(e)}


def upload_pdf_cliente(ref_id: str, nome_arquivo: str, conteudo: bytes,
                       mime_type: str = "application/pdf") -> dict:
    from services.supabase_client import get_service_client
    import uuid
    sb = get_service_client()
    fid = str(uuid.uuid4())
    caminho = f"clientes/{ref_id}/{fid}_{nome_arquivo}"
    try:
        sb.storage.from_("documentos-contratos").upload(caminho, conteudo, {"content-type": mime_type})
        url = sb.storage.from_("documentos-contratos").get_public_url(caminho)
        return {"ok": True, "url": url, "caminho": caminho}
    except Exception as e:
        return {"ok": False, "erro": str(e)}

_CONTRATOS_LOCACAO_PATH = "/Users/karol/Documents/Dashboard-Ativuz/planilhas/Contratos de Locação.xlsx"
_STORAGE_BUCKET = "contratos"
_STORAGE_CONTRATOS = "planilhas/Contratos de Locacao.xlsx"


def _planilha_path(local_path: str, storage_key: str) -> str:
    """Retorna caminho local se existir, senão baixa do Supabase Storage para /tmp."""
    if os.path.exists(local_path):
        return local_path
    tmp = os.path.join("/tmp", os.path.basename(storage_key))
    if os.path.exists(tmp):
        return tmp
    try:
        from services.supabase_client import get_service_client
        data = get_service_client().storage.from_(_STORAGE_BUCKET).download(storage_key)
        with open(tmp, "wb") as f:
            f.write(data)
        return tmp
    except Exception as e:
        print(f"[_planilha_path] falha ao baixar {storage_key}: {e}")
        return local_path  # retorna original para gerar erro legível


def upload_planilha(filename: str, conteudo: bytes) -> dict:
    """Sobe Excel para Supabase Storage (bucket contratos/planilhas/)."""
    key = f"planilhas/{filename}"
    try:
        sb = get_service_client()
        try:
            sb.storage.from_(_STORAGE_BUCKET).remove([key])
        except Exception:
            pass
        sb.storage.from_(_STORAGE_BUCKET).upload(key, conteudo, {"content-type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"})
        # invalida cache local em /tmp
        tmp = os.path.join("/tmp", filename)
        if os.path.exists(tmp):
            os.remove(tmp)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "erro": str(e)}

_UNIDADE_EMPRESA = {
    "JOÃO PAULO CONSÓRCIOS": "JOÃO PAULO SERVIÇOS EM CONSULTORIA LTDA",
    "LUZ DIVINA LTDA":       "LUZ DIVINA EMPREENDIMENTOS LTDA",
    "ATIVUZ VEÍCULOS":       "ATIVUZ VEÍCULOS",
    "AZ EMPREENDIMENTOS":    "AZ EMPREENDIMENTOS",
}


def listar_contratos_excel(unidades: list) -> list:
    """Lê Contratos de Locação.xlsx e retorna contratos ativos das unidades informadas."""
    import openpyxl
    try:
        wb = openpyxl.load_workbook(_planilha_path(_CONTRATOS_LOCACAO_PATH, _STORAGE_CONTRATOS), data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
    except Exception as e:
        print(f"[listar_contratos_excel] erro: {e}")
        return []

    resultado = []
    for r in rows[5:]:
        if not r[6]:  # Cliente
            continue
        cliente = str(r[6]).strip()
        if "SEGCOMP" in cliente.upper():
            continue
        unidade = str(r[54] or "").strip()
        if unidades and unidade not in unidades:
            continue
        placa_raw = r[59] or r[61] or ""
        placa = str(placa_raw).strip().upper().replace("-", "")
        inicio = r[30]
        if hasattr(inicio, "date"):
            inicio = inicio.date()
        resultado.append({
            "cliente":       cliente,
            "placa":         placa,
            "placa_fmt":     str(placa_raw).strip().upper(),
            "modelo":        str(r[36] or "").strip(),
            "inicio":        inicio.strftime("%d/%m/%Y") if inicio else "",
            "unidade":       unidade,
            "situacao":      str(r[46] or "").strip(),
            "valor_locacao": float(r[57] or r[58] or 0),
        })
    return resultado


def contratos_por_empresa(empresa_nome: str) -> list:
    """Contrato vigente de cada veículo da empresa, da tabela contratos_locacao.

    Antes vinha de Contratos de Locação.xlsx, que envelhece no storage — ela
    ainda dava TSW-3H91 para a locatária anterior, muito depois de o carro ter
    sido repassado. A tabela é atualizada a cada contrato novo.

    A empresa é determinada pelo prefixo da placa (_PREFIXO_EMPRESA), já que a
    tabela não guarda a unidade. Uma placa pode ter vários contratos ao longo do
    tempo; vale o mais recente.
    """
    try:
        sb = get_financeiro_client()
        linhas = (
            sb.table("contratos_locacao")
            .select("locatario_nome,veiculo_placa,veiculo_modelo,contrato_inicio,"
                    "valor_semanal,criado_em")
            .eq("deletado", False)
            .order("criado_em", desc=False)
            .execute()
            .data or []
        )
    except Exception as e:
        print(f"[contratos_por_empresa] erro: {e}")
        return []

    vigente: dict = {}
    for linha in linhas:
        placa = _norm(linha.get("veiculo_placa"))
        if not placa or _PREFIXO_EMPRESA.get(placa[:3], (None,))[0] != empresa_nome:
            continue
        vigente[placa] = linha            # ordem crescente: fica o mais recente

    resultado = []
    # Contratos antigos, anteriores à tabela, só existem na planilha. Entram
    # apenas para placas que a tabela não cobre — a tabela sempre vence.
    unidades = [u for u, e in _UNIDADE_EMPRESA.items() if e == empresa_nome]
    for antigo in listar_contratos_excel(unidades):
        placa = _norm(antigo.get("placa"))
        if placa and placa not in vigente:
            antigo["cliente"] = _alias_motorista(antigo.get("cliente"))
            resultado.append(antigo)

    for placa, linha in vigente.items():
        valor = str(linha.get("valor_semanal") or "0").replace(".", "").replace(",", ".")
        try:
            valor = float(valor)
        except ValueError:
            valor = 0.0
        resultado.append({
            "cliente":       _alias_motorista(linha.get("locatario_nome")),
            "placa":         placa,
            "placa_fmt":     str(linha.get("veiculo_placa") or "").strip().upper(),
            "modelo":        str(linha.get("veiculo_modelo") or "").strip(),
            "inicio":        str(linha.get("contrato_inicio") or "").strip(),
            "unidade":       _EMPRESA_INFO.get(empresa_nome, {}).get("unidade", ""),
            "situacao":      "EM ANDAMENTO",
            "valor_locacao": valor,
        })
    return resultado


def contas_receber_carros_excel(empresa_nome: str) -> list:
    """Compat: mantido para chamadas antigas. Lê da tabela, não mais do Excel."""
    return contas_receber_empresa(empresa_nome)


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
    """Faturas em aberto da empresa na tabela contas_receber_frota.

    Fonte única de contas a receber de carros — a tabela é alimentada pelo
    Dashboard-Ativuz a partir da planilha CONTAS-A-RECEBER.xlsx.
    """
    from datetime import date, datetime

    unidades = [u for u, e in _UNIDADE_EMPRESA.items() if e == empresa_nome]
    if not unidades:
        return []
    try:
        sb = get_financeiro_client()
        # A tabela acumula todos os snapshots (upsert por cliente|vencimento, sem
        # delete), então faturas já quitadas continuam lá. Só o último lote
        # importado representa o que está de fato em aberto.
        ultimo = (
            sb.table("contas_receber_frota")
            .select("atualizado_em")
            .order("atualizado_em", desc=True)
            .limit(1)
            .execute()
            .data
        )
        if not ultimo:
            return []
        corte = ultimo[0]["atualizado_em"][:10]  # dia da última importação

        rows = (
            sb.table("contas_receber_frota")
            .select("numero_documento,cliente,data_vencimento,valor,situacao,"
                    "tipo_fatura,faixa_vencimento,unidade")
            .in_("unidade", unidades)
            .gte("atualizado_em", corte)
            .order("data_vencimento")
            .execute()
            .data or []
        )
    except Exception as e:
        print(f"[contas_receber_empresa] erro: {e}")
        return []

    hoje = date.today()
    for r in rows:
        venc = r.get("data_vencimento")
        try:
            r["dias_vencimento"] = (datetime.strptime(venc[:10], "%Y-%m-%d").date() - hoje).days
        except Exception:
            r["dias_vencimento"] = None
        r["valor"] = float(r.get("valor") or 0)
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# Extratos Asaas  →  lançamentos da conta da frota
#
# A tabela `asaas_extratos` (Supabase 2) é alimentada pelo Dashboard-Ativuz: uma
# linha por importação de extrato, com as transações num JSON. O portal lê de
# `lancamentos_bancarios`, uma linha por transação. A sincronização abaixo
# converte de um formato para o outro, mantendo cada frota na sua conta.
#
# Idempotente: `tx_id` da Asaas vira `fitid`, então reimportar o mesmo extrato
# (ou extratos com períodos sobrepostos) não duplica lançamento.
# ─────────────────────────────────────────────────────────────────────────────

_EMPRESA_FROTA = {
    "LUZ DIVINA EMPREENDIMENTOS LTDA":            "luz-divina",
    "JOÃO PAULO SERVIÇOS EM CONSULTORIA LTDA":    "joao-paulo",
}

_FROTA_CONTA = {
    "luz-divina": _EMPRESA_CONTA["LUZ DIVINA EMPREENDIMENTOS LTDA"],
    "joao-paulo": _EMPRESA_CONTA["JOÃO PAULO SERVIÇOS EM CONSULTORIA LTDA"],
}

# categoria da Asaas → natureza de carros (tabela naturezas_carros).
# Categoria fora deste mapa entra sem natureza, para classificação manual.
_CATEGORIA_NATUREZA = {
    "aluguel":              "Locação",
    "adesao":               "Locação",
    "devolucao_aluguel":    "Locação",
    "caucao":               "Caução",
    "devolucao_caucao":     "Caução",
    "repasse_investidor":   "Repasse",
    "taxa_asaas":           "Taxa ASAAS",
    "taxa_ativuz":          "Taxa administrativa",
    "reembolso_manutencao": "Reembolso de Manutenção",
    "ipva":                 "IPVA",
}


def _data_asaas_iso(valor: str) -> str:
    """'DD/MM/YYYY' → 'YYYY-MM-DD'. As datas vêm como texto no JSON, então
    qualquer ordenação precisa da conversão antes."""
    partes = (valor or "").split("/")
    if len(partes) != 3:
        return ""
    d, m, a = partes
    return f"{a}-{m.zfill(2)}-{d.zfill(2)}"


def transacoes_asaas(frota: str) -> list:
    """Transações de uma frota, unidas por tx_id entre todos os extratos dela.

    Os extratos têm períodos sobrepostos (a mesma transação aparece em mais de
    uma importação), por isso a união é por tx_id — vence a carga mais recente.
    """
    try:
        sb = get_financeiro_client()
        extratos = (
            sb.table("asaas_extratos")
            .select("frota,created_at,transacoes")
            .eq("frota", frota)
            .order("created_at", desc=False)
            .execute()
            .data or []
        )
    except Exception as e:
        print(f"[transacoes_asaas] erro: {e}")
        return []

    por_tx = {}
    for ext in extratos:
        bruto = ext.get("transacoes")
        itens = json.loads(bruto) if isinstance(bruto, str) else (bruto or [])
        for item in itens:
            tx_id = str(item.get("tx_id") or "").strip()
            if tx_id:
                por_tx[tx_id] = item          # carga mais recente sobrescreve

    return sorted(por_tx.values(),
                  key=lambda i: _data_asaas_iso(i.get("data")),
                  reverse=True)


def sincronizar_extratos_asaas(frota: str = None) -> dict:
    """Copia as transações de `asaas_extratos` para `lancamentos_bancarios`.

    Cada frota vai para a conta bancária dela — luz-divina e joao-paulo nunca
    se misturam. Só insere o que ainda não existe (comparação por fitid), então
    pode rodar quantas vezes for preciso.
    """
    frotas = [frota] if frota else list(_FROTA_CONTA)
    sb = get_financeiro_client()
    resultado = {"ok": True, "frotas": {}}

    for slug in frotas:
        conta_id = _FROTA_CONTA.get(slug)
        if not conta_id:
            resultado["frotas"][slug] = {"erro": "frota desconhecida"}
            resultado["ok"] = False
            continue

        transacoes = transacoes_asaas(slug)
        if not transacoes:
            resultado["frotas"][slug] = {"total": 0, "inseridos": 0, "existentes": 0}
            continue

        try:
            ja_existem = {
                r["fitid"] for r in (
                    sb.table("lancamentos_bancarios")
                    .select("fitid")
                    .eq("conta_bancaria_id", conta_id)
                    .not_.is_("fitid", "null")
                    .execute()
                    .data or []
                )
            }
        except Exception as e:
            resultado["frotas"][slug] = {"erro": str(e)}
            resultado["ok"] = False
            continue

        novos = []
        for item in transacoes:
            tx_id = str(item.get("tx_id") or "").strip()
            data  = _data_asaas_iso(item.get("data"))
            if not tx_id or not data or tx_id in ja_existem:
                continue

            valor     = float(item.get("valor") or 0)
            descricao = (item.get("descricao") or "").strip()
            natureza  = _CATEGORIA_NATUREZA.get(item.get("categoria"))
            novos.append({
                "conta_bancaria_id":  conta_id,
                "fitid":              tx_id,
                "data_transacao":     data,
                "mes_competencia":    data[:7] + "-01",
                "descricao":          descricao,
                "descricao_original": descricao,
                "valor":              valor,
                "tipo":               "credito" if valor >= 0 else "debito",
                "observacoes":        natureza,
                "conciliado":         bool(natureza),
            })

        inseridos = 0
        for i in range(0, len(novos), 200):
            lote = novos[i:i + 200]
            try:
                sb.table("lancamentos_bancarios").insert(lote).execute()
                inseridos += len(lote)
            except Exception as e:
                print(f"[sincronizar_extratos_asaas] {slug} lote {i}: {e}")
                resultado["ok"] = False

        resultado["frotas"][slug] = {
            "total":      len(transacoes),
            "inseridos":  inseridos,
            "existentes": len(transacoes) - len(novos),
        }

    return resultado


# Naturezas que representam dinheiro entrando pela locação. Repasse fica de
# fora: é saída para o investidor, não recebimento da frota.
_NATUREZAS_RECEBIMENTO = ("Locação", "Conveniência", "Juros de Locação (atraso)")


def recebido_por_mes(empresa_nome: str) -> list:
    """Total recebido por mês, direto do extrato da conta da frota.

    Substitui a leitura de `sob_adm_recebimentos`, que era preenchida à mão e
    parou em 22/06 — e que estimava o recebido dividindo a taxa por 0.15, em vez
    de usar o valor real do crédito.

    Retorna [{"mes": "2026-07", "valor": 12345.67}, ...] em ordem cronológica.
    """
    # Os lançamentos do seed original nunca foram classificados à mão, então a
    # natureza vem da categoria da Asaas quando `observacoes` está vazio — senão
    # o gráfico ficaria com meses faltando.
    frota = _EMPRESA_FROTA.get(empresa_nome)
    categoria_por_tx = {
        str(t.get("tx_id")): t.get("categoria")
        for t in (transacoes_asaas(frota) if frota else [])
    }

    por_mes: dict = {}
    for lanc in listar_lancamentos_carros(empresa_nome):
        natureza = lanc.get("observacoes") or _CATEGORIA_NATUREZA.get(
            categoria_por_tx.get(str(lanc.get("fitid")))
        )
        if natureza not in _NATUREZAS_RECEBIMENTO:
            continue
        valor = float(lanc.get("valor") or 0)
        mes   = (lanc.get("data_transacao") or "")[:7]
        if valor <= 0 or not mes:
            continue
        por_mes[mes] = por_mes.get(mes, 0.0) + valor

    return [{"mes": m, "valor": round(v, 2)} for m, v in sorted(por_mes.items())]


_CATEGORIAS_ALUGUEL = ("aluguel", "adesao", "devolucao_aluguel")
_CATEGORIAS_CAUCAO  = ("caucao", "devolucao_caucao")

# Caução embutida no primeiro crédito, por contrato. Na luz-divina são R$ 3.000
# por contrato; na joao-paulo a caução não é embutida — vem só das faturas
# mapeadas em _ASAAS_FATURAS_CAUCAO.
_CAUCAO_CONTRATO = {
    "luz-divina": 3000.0,
    "joao-paulo": 0.0,
}

# O campo `motorista` do extrato repete a mesma pessoa com grafias diferentes.
# Chave em minúsculas → nome canônico.
_ASAAS_ALIAS_MOTORISTA = {
    "67.009.261 elionilson c. barbosa": "ELIONILSON CORDEIRO BARBOSA",
    "tenielle glauciana souza da silva": "TANIELLE GLAUCIANA SOUZA DA SILVA",
}


def _alias_motorista(nome: str) -> str:
    """Nome canônico do motorista. Aplicado ao extrato e ao contrato, senão a
    mesma pessoa não casa entre as duas fontes."""
    nome = (nome or "").strip()
    return _ASAAS_ALIAS_MOTORISTA.get(nome.lower(), nome.upper())

# Faturas que são caução mas entram no extrato como aluguel, às vezes cobradas
# por outra empresa. Número da fatura → motorista real.
_ASAAS_FATURAS_CAUCAO = {
    "767966354": "ELIONILSON CORDEIRO BARBOSA",   # caução do Polo cobrada via Ativuz
}

# Faturas pagas por terceiro: o extrato registra o pagador no lugar do motorista.
# Número da fatura → motorista real.
_ASAAS_FATURAS_MOTORISTA = {
    "894432964": "JOSE PEREIRA JUNIOR",           # paga por Andrier Oliveira Cachina
}


def _motorista_canonico(nome: str, descricao: str = "") -> tuple:
    """(nome canônico, é_caucao) para uma transação.

    Resolve a grafia pelo alias e, quando a fatura está mapeada, devolve o
    motorista real dela em vez de quem aparece na cobrança — seja porque é
    caução cobrada por outra empresa, seja porque um terceiro pagou.
    """
    import re as _re
    fatura = _re.search(r"fatura nr\.\s*(\d+)", descricao or "", _re.I)
    numero = fatura.group(1) if fatura else None
    if numero in _ASAAS_FATURAS_CAUCAO:
        return _ASAAS_FATURAS_CAUCAO[numero], True
    if numero in _ASAAS_FATURAS_MOTORISTA:
        return _ASAAS_FATURAS_MOTORISTA[numero], False

    return _alias_motorista(nome), False


def _segunda_da_semana(data_iso: str) -> str:
    """Segunda-feira da semana de uma data ISO — a locação é semanal, então a
    semana é a unidade de contagem."""
    from datetime import date as _d, timedelta as _td
    try:
        a, m, d = (int(p) for p in data_iso.split("-"))
        dia = _d(a, m, d)
        return (dia - _td(days=dia.weekday())).isoformat()
    except Exception:
        return ""


def recebido_por_motorista(empresa_nome: str) -> list:
    """Quanto cada motorista pagou, direto do extrato da frota.

    Separa aluguéis de caução e conta as semanas distintas com pagamento de
    aluguel. Retorna ordenado por total, maior primeiro.
    """
    frota = _EMPRESA_FROTA.get(empresa_nome)
    if not frota:
        return []

    por_motorista: dict = {}
    for tx in transacoes_asaas(frota):
        nome, fatura_caucao = _motorista_canonico(tx.get("motorista"), tx.get("descricao"))
        if not nome:
            continue
        categoria = tx.get("categoria")
        valor     = float(tx.get("valor") or 0)
        registro  = por_motorista.setdefault(
            nome, {"cliente": nome, "alugueis": 0.0, "caucao": 0.0, "semanas": set()}
        )
        if fatura_caucao:
            registro["caucao"] += valor
        elif categoria in _CATEGORIAS_ALUGUEL:
            registro["alugueis"] += valor
            semana = _segunda_da_semana(_data_asaas_iso(tx.get("data")))
            if semana and valor > 0:
                registro["semanas"].add(semana)
        elif categoria in _CATEGORIAS_CAUCAO:
            registro["caucao"] += valor

    resultado = []
    for reg in por_motorista.values():
        # Convênio não é motorista — entra no extrato, mas não na tabela por
        # motorista (mesma regra de _CONVENIO_KEYWORDS usada no extrato).
        if any(k in reg["cliente"] for k in _CONVENIO_KEYWORDS):
            continue

        # A caução raramente vem com categoria própria: na maioria dos contratos
        # ela está embutida no primeiro crédito, como adesão. Como é valor fixo
        # por contrato, separa-se do total.
        embutida   = _CAUCAO_CONTRATO.get(frota, 0.0)
        bruto      = reg["alugueis"]
        ja_contada = reg["caucao"]
        tem_caucao = embutida > 0 and bruto >= embutida

        reg["n_semanas"]  = len(reg.pop("semanas"))
        reg["alugueis"]   = round(bruto - embutida if tem_caucao else bruto, 2)
        reg["caucao"]     = round((embutida if tem_caucao else 0) + ja_contada, 2)
        reg["valor_pago"] = round(reg["alugueis"] + reg["caucao"], 2)
        resultado.append(reg)

    resultado.sort(key=lambda r: -r["valor_pago"])
    return resultado


_CONTAS_RECEBER_PATH = "/Users/karol/Documents/Dashboard-Ativuz/planilhas/CONTAS-A-RECEBER.xlsx"
_STORAGE_CONTAS_RECEBER = "planilhas/CONTAS-A-RECEBER.xlsx"

# Colunas da planilha CONTAS-A-RECEBER.xlsx (cabeçalho na linha 5, dados da 6 em diante)
_COL_CR = {
    "data_competencia": 2,
    "data_vencimento":  3,
    "dias_vencimento":  5,
    "numero_documento": 9,
    "cliente":          11,
    "cliente_razao":    12,
    "situacao":         13,
    "tipo_fatura":      16,
    "unidade":          17,
    "valor":            18,
}


def _faixa_vencimento(dias) -> str:
    """Faixa a partir dos dias para o vencimento — a planilha traz um texto
    próprio ('VENCIDO ENTRE 31 A 90'), mas a tabela usa estas faixas."""
    try:
        dias = int(dias)
    except (TypeError, ValueError):
        return ""
    if dias > 0:
        return "A vencer"
    if dias == 0:
        return "Vence hoje"
    atraso = -dias
    if atraso <= 7:
        return "1-7 dias"
    if atraso <= 15:
        return "8-15 dias"
    if atraso <= 30:
        return "16-30 dias"
    return "Mais de 30 dias"


def _situacao_vencimento(dias) -> str:
    try:
        dias = int(dias)
    except (TypeError, ValueError):
        return ""
    return "A VENCER" if dias > 0 else ("HOJE" if dias == 0 else "VENCIDO")


def importar_contas_receber_frota(caminho: str = None) -> dict:
    """Lê CONTAS-A-RECEBER.xlsx e grava um novo snapshot em contas_receber_frota.

    O upsert usa a chave natural da tabela (documento|vencimento), então
    reimportar a mesma planilha atualiza as linhas em vez de duplicá-las. Faturas
    que saíram da planilha ficam com o `atualizado_em` antigo e somem da tela,
    porque `contas_receber_empresa` só considera o último lote.
    """
    import openpyxl
    from datetime import datetime as _dt

    caminho = caminho or _planilha_path(_CONTAS_RECEBER_PATH, _STORAGE_CONTAS_RECEBER)
    try:
        ws = openpyxl.load_workbook(caminho, data_only=True).active
        linhas = [r for r in ws.iter_rows(min_row=6, values_only=True) if any(r)]
    except Exception as e:
        return {"ok": False, "erro": f"falha ao ler a planilha: {e}"}

    def _data(valor):
        if hasattr(valor, "date"):
            return valor.date().isoformat()
        texto = str(valor or "")[:10]
        return texto if len(texto) == 10 else None

    agora = _dt.utcnow().isoformat()
    registros = []
    for linha in linhas:
        def col(nome):
            indice = _COL_CR[nome]
            return linha[indice] if indice < len(linha) else None

        vencimento = _data(col("data_vencimento"))
        if not vencimento:
            continue
        dias = col("dias_vencimento")
        numero  = str(col("numero_documento") or "").strip()
        cliente = str(col("cliente") or col("cliente_razao") or "").strip()
        registros.append({
            # Chave natural usada pela tabela: documento (ou cliente, quando a
            # fatura não tem número) + vencimento. Garante upsert, não duplicata.
            "id":               f"{numero or cliente}|{vencimento}",
            "numero_documento": numero,
            "cliente":          cliente,
            "data_competencia": _data(col("data_competencia")),
            "data_vencimento":  vencimento,
            "dias_vencimento":  int(dias) if str(dias or "").lstrip("-").isdigit() else None,
            "faixa_vencimento": _faixa_vencimento(dias),
            "situacao":         _situacao_vencimento(dias) or str(col("situacao") or "").strip(),
            "tipo_fatura":      str(col("tipo_fatura") or "").strip(),
            "unidade":          str(col("unidade") or "").strip(),
            "valor":            float(col("valor") or 0),
            "atualizado_em":    agora,
        })

    if not registros:
        return {"ok": False, "erro": "planilha sem linhas válidas"}

    # Faturas sem número podem repetir a chave natural (mesmo cliente, mesmo
    # vencimento). Vence a última, como num upsert linha a linha.
    por_id = {r["id"]: r for r in registros}
    colisoes = len(registros) - len(por_id)
    registros = list(por_id.values())

    sb = get_financeiro_client()
    inseridos = 0
    for i in range(0, len(registros), 200):
        lote = registros[i:i + 200]
        try:
            sb.table("contas_receber_frota").upsert(lote).execute()
            inseridos += len(lote)
        except Exception as e:
            return {"ok": False, "erro": str(e), "inseridos": inseridos}

    return {"ok": True, "lidos": len(linhas), "gravados": inseridos,
            "colisoes_de_chave": colisoes,
            "total": round(sum(r["valor"] for r in registros), 2)}
