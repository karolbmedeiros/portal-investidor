"""
Busca índices de mercado:
- BCB (api.bcb.gov.br): CDI, IPCA, Poupança
- Yahoo Finance: IBOV (^BVSP), IVVB11 (S&P 500 em BRL)
Cache diário no Supabase.
"""
import requests
from datetime import date, datetime, timedelta
from services.supabase_client import get_service_client

SERIES_BCB = {
    "cdi":      12,
    "ipca":     433,
    "poupanca": 195,
}

SERIES_YAHOO = {
    "ibov":   "^BVSP",
    "ivvb11": "IVVB11.SA",
}

BACEN_URL = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.{codigo}/dados"
YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
YAHOO_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}


# ── Cache helpers ─────────────────────────────────────────────────────────────

def _cache_key(nome: str, desde: str) -> str:
    return f"{nome}_{desde}"


def _cache_existe_hoje(chave: str) -> bool:
    sb = get_service_client()
    res = (
        sb.table("benchmark_cache")
        .select("id")
        .eq("indice", chave)
        .eq("data_cache", date.today().isoformat())
        .limit(1)
        .execute()
    )
    return bool(res.data)


def _salvar_cache(chave: str, dados):
    sb = get_service_client()
    sb.table("benchmark_cache").delete().eq("indice", chave).execute()
    sb.table("benchmark_cache").insert({
        "indice":     chave,
        "dados":      dados,
        "data_cache": date.today().isoformat(),
    }).execute()


def _ler_cache(chave: str):
    sb = get_service_client()
    res = (
        sb.table("benchmark_cache")
        .select("dados")
        .eq("indice", chave)
        .order("data_cache", desc=True)
        .limit(1)
        .execute()
    )
    return res.data[0]["dados"] if res.data else None


# ── BCB ───────────────────────────────────────────────────────────────────────

def _buscar_bcb(codigo: int, data_inicio: str, data_fim: str) -> list[dict]:
    try:
        res = requests.get(
            BACEN_URL.format(codigo=codigo),
            params={"formato": "json", "dataInicial": data_inicio, "dataFinal": data_fim},
            timeout=15,
        )
        res.raise_for_status()
        return res.json()
    except Exception:
        return []


def _fetch_bcb(nome: str, codigo: int, data_inicio_str: str, chave: str) -> list[dict]:
    if not _cache_existe_hoje(chave):
        dados = _buscar_bcb(codigo, data_inicio_str, date.today().strftime("%d/%m/%Y"))
        if dados:
            _salvar_cache(chave, dados)
    return _ler_cache(chave) or []


def _agrupar_cdi_mensal(dados: list[dict]) -> dict[str, float]:
    """Composta daily CDI rates by month → monthly rate."""
    mensal: dict[str, float] = {}
    for d in dados:
        try:
            dt = datetime.strptime(d["data"], "%d/%m/%Y")
            mes = dt.strftime("%Y-%m")
            r = float(d["valor"]) / 100
            mensal[mes] = mensal.get(mes, 1.0) * (1 + r)
        except Exception:
            pass
    return {m: v - 1 for m, v in mensal.items()}


def _agrupar_mensal_bcb(dados: list[dict]) -> dict[str, float]:
    mensal: dict[str, float] = {}
    for d in dados:
        try:
            dt = datetime.strptime(d["data"], "%d/%m/%Y")
            mes = dt.strftime("%Y-%m")
            mensal[mes] = float(d["valor"]) / 100
        except Exception:
            pass
    return mensal


# ── Yahoo Finance ─────────────────────────────────────────────────────────────

def _buscar_yahoo_mensal(ticker: str, d0: date) -> dict[str, float]:
    """
    Retorna {YYYY-MM: retorno_mensal} a partir de d0.
    Busca um mês antes de d0 para calcular o retorno do primeiro mês.
    """
    if d0.month == 1:
        base = date(d0.year - 1, 12, 1)
    else:
        base = date(d0.year, d0.month - 1, 1)

    period1 = int(datetime(base.year, base.month, 1, 0, 0, 0).timestamp())
    period2 = int(datetime.now().timestamp())

    try:
        res = requests.get(
            YAHOO_URL.format(ticker=ticker),
            params={"interval": "1mo", "period1": period1, "period2": period2},
            headers=YAHOO_HEADERS,
            timeout=15,
        )
        res.raise_for_status()
        result = res.json()["chart"]["result"][0]
        timestamps = result["timestamp"]
        closes     = result["indicators"]["quote"][0]["close"]

        prices: dict[str, float] = {}
        for ts, close in zip(timestamps, closes):
            if close is not None:
                mes = datetime.utcfromtimestamp(ts).strftime("%Y-%m")
                prices[mes] = close

        sorted_months = sorted(prices)
        returns: dict[str, float] = {}
        for i in range(1, len(sorted_months)):
            prev, curr = sorted_months[i - 1], sorted_months[i]
            if prices.get(prev) and prices.get(curr):
                returns[curr] = prices[curr] / prices[prev] - 1

        return returns
    except Exception:
        return {}


def _fetch_yahoo(nome: str, ticker: str, d0: date, chave: str) -> dict[str, float]:
    if not _cache_existe_hoje(chave):
        dados = _buscar_yahoo_mensal(ticker, d0)
        if dados:
            _salvar_cache(chave, dados)
    cached = _ler_cache(chave)
    return cached if isinstance(cached, dict) else {}


# ── Comparativo principal ─────────────────────────────────────────────────────

def comparativo_benchmarks(capital: float, data_desembolso: str, retorno_mensal: list) -> dict:
    """
    Compara o investimento com CDI, IPCA, Poupança, IBOV e IVVB11.
    data_desembolso: 'YYYY-MM-DD'
    """
    if not capital or not data_desembolso or not retorno_mensal:
        return {}

    try:
        d0 = datetime.strptime(data_desembolso[:10], "%Y-%m-%d").date()
    except Exception:
        return {}

    desde          = d0.strftime("%Y-%m")
    data_inicio_bcb = d0.strftime("%d/%m/%Y")

    # BCB
    cdi_raw  = _fetch_bcb("cdi",      SERIES_BCB["cdi"],      data_inicio_bcb, _cache_key("cdi",      desde))
    ipca_raw = _fetch_bcb("ipca",     SERIES_BCB["ipca"],     data_inicio_bcb, _cache_key("ipca",     desde))
    poup_raw = _fetch_bcb("poupanca", SERIES_BCB["poupanca"], data_inicio_bcb, _cache_key("poupanca", desde))

    cdi_mes  = _agrupar_cdi_mensal(cdi_raw)
    ipca_mes = _agrupar_mensal_bcb(ipca_raw)
    poup_mes = _agrupar_mensal_bcb(poup_raw)

    # Yahoo Finance
    ibov_mes   = _fetch_yahoo("ibov",   SERIES_YAHOO["ibov"],   d0, _cache_key("ibov",   desde))
    ivvb11_mes = _fetch_yahoo("ivvb11", SERIES_YAHOO["ivvb11"], d0, _cache_key("ivvb11", desde))

    meses     = sorted(r["ref_mes_ano"][:7] for r in retorno_mensal if r.get("ref_mes_ano"))
    lucro_mes = {r["ref_mes_ano"][:7]: float(r.get("valor_distribuido") or 0) for r in retorno_mensal}

    series = []
    inv    = capital
    cdi_v  = capital
    ipc_v  = capital
    pou_v  = capital
    ibov_v = capital
    ivvb_v = capital

    for mes in meses:
        inv    += lucro_mes.get(mes, 0)
        cdi_v  *= 1 + cdi_mes.get(mes,   0)
        ipc_v  *= 1 + ipca_mes.get(mes,  0)
        pou_v  *= 1 + poup_mes.get(mes,  0)
        ibov_v *= 1 + ibov_mes.get(mes,  0)
        ivvb_v *= 1 + ivvb11_mes.get(mes, 0)
        series.append({
            "mes":          mes,
            "investimento": round(inv,    2),
            "cdi":          round(cdi_v,  2),
            "ipca":         round(ipc_v,  2),
            "poupanca":     round(pou_v,  2),
            "ibov":         round(ibov_v, 2),
            "ivvb11":       round(ivvb_v, 2),
        })

    if not series:
        return {}

    ult = series[-1]
    def pct(v): return round((v / capital - 1) * 100, 2) if capital else 0

    return {
        "capital": capital,
        "n_meses": len(series),
        "series":  series,
        "final": {k: ult[k] for k in ("investimento", "cdi", "ipca", "poupanca", "ibov", "ivvb11")},
        "ganho": {k: round(ult[k] - capital, 2) for k in ("investimento", "cdi", "ipca", "poupanca", "ibov", "ivvb11")},
        "pct":   {k: pct(ult[k])               for k in ("investimento", "cdi", "ipca", "poupanca", "ibov", "ivvb11")},
    }


def get_benchmarks(dias: int = 365) -> dict:
    data_fim    = date.today()
    data_inicio = data_fim - timedelta(days=dias)
    desde = data_inicio.strftime("%Y-%m")
    resultado = {}
    for nome, codigo in SERIES_BCB.items():
        chave = _cache_key(nome, desde)
        resultado[nome] = _fetch_bcb(nome, codigo, data_inicio.strftime("%d/%m/%Y"), chave)
    return resultado
