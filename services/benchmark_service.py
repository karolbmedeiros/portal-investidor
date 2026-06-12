"""
Busca índices de mercado da API pública do Banco Central (api.bcb.gov.br).
Faz cache diário no Supabase para não repetir chamadas.
"""
import requests
from datetime import date, timedelta
from services.supabase_client import get_service_client

# Códigos das séries do BACEN
SERIES = {
    "selic": 11,
    "cdi": 12,
    "ipca": 433,
    "poupanca": 195,
}

BACEN_URL = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.{codigo}/dados"


def _buscar_bacen(codigo: int, data_inicio: str, data_fim: str) -> list[dict]:
    try:
        res = requests.get(
            BACEN_URL.format(codigo=codigo),
            params={"formato": "json", "dataInicial": data_inicio, "dataFinal": data_fim},
            timeout=10,
        )
        res.raise_for_status()
        return res.json()
    except Exception:
        return []


def _cache_existe_hoje(nome: str) -> bool:
    sb = get_service_client()
    hoje = date.today().isoformat()
    res = (
        sb.table("benchmark_cache")
        .select("id")
        .eq("indice", nome)
        .eq("data_cache", hoje)
        .limit(1)
        .execute()
    )
    return bool(res.data)


def _salvar_cache(nome: str, dados: list[dict]):
    sb = get_service_client()
    hoje = date.today().isoformat()
    # Remove cache anterior deste índice
    sb.table("benchmark_cache").delete().eq("indice", nome).execute()
    # Insere novo
    sb.table("benchmark_cache").insert({
        "indice": nome,
        "dados": dados,
        "data_cache": hoje,
    }).execute()


def _ler_cache(nome: str) -> list[dict]:
    sb = get_service_client()
    res = (
        sb.table("benchmark_cache")
        .select("dados")
        .eq("indice", nome)
        .order("data_cache", desc=True)
        .limit(1)
        .execute()
    )
    if res.data:
        return res.data[0]["dados"]
    return []


def get_benchmarks(dias: int = 365) -> dict[str, list[dict]]:
    """
    Retorna dados dos últimos N dias para cada índice.
    Usa cache diário — só chama o BACEN se não houver cache de hoje.
    """
    data_fim = date.today()
    data_inicio = data_fim - timedelta(days=dias)
    resultado = {}

    for nome, codigo in SERIES.items():
        if not _cache_existe_hoje(nome):
            dados = _buscar_bacen(
                codigo,
                data_inicio.strftime("%d/%m/%Y"),
                data_fim.strftime("%d/%m/%Y"),
            )
            if dados:
                _salvar_cache(nome, dados)
        resultado[nome] = _ler_cache(nome)

    return resultado
