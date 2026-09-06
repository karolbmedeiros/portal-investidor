"""Clientes Supabase.

Um cliente carrega um httpx.Client em HTTP/2, e uma conexão HTTP/2 não pode
ser dirigida por várias threads ao mesmo tempo — os streams se embaralham e a
leitura falha com ReadError [Errno 35]. Como algumas telas disparam consultas
em paralelo, cada thread recebe o seu próprio cliente (threading.local).

Continuam sendo criados uma vez por thread, não por chamada; combinados com um
ThreadPoolExecutor reaproveitado, o custo de handshake é pago uma vez só.
"""
import threading

from supabase import create_client, Client
from config import Config

_local = threading.local()


def _obter(attr: str, url: str, key: str) -> Client:
    cliente = getattr(_local, attr, None)
    if cliente is None:
        cliente = create_client(url, key)
        setattr(_local, attr, cliente)
    return cliente


def get_client() -> Client:
    """Cliente anon — respeita RLS, usado para operações do investidor."""
    return _obter("cliente", Config.SUPABASE_URL, Config.SUPABASE_ANON_KEY)


def get_service_client() -> Client:
    """Cliente service_role do projeto principal (portal)."""
    return _obter("service", Config.SUPABASE_URL, Config.SUPABASE_SERVICE_KEY)


def get_financeiro_client() -> Client:
    """Cliente service_role do projeto financeiro (segundo banco)."""
    return _obter("financeiro", Config.SUPABASE2_URL, Config.SUPABASE2_SERVICE_KEY)
