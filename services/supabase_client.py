from supabase import create_client, Client
from config import Config

_client: object = None
_service_client: object = None
_service_client2: object = None


def get_client() -> Client:
    """Cliente anon — respeita RLS, usado para operações do investidor."""
    global _client
    if _client is None:
        _client = create_client(Config.SUPABASE_URL, Config.SUPABASE_ANON_KEY)
    return _client


def get_service_client() -> Client:
    """Cliente service_role do projeto principal (portal)."""
    global _service_client
    if _service_client is None:
        _service_client = create_client(Config.SUPABASE_URL, Config.SUPABASE_SERVICE_KEY)
    return _service_client


def get_financeiro_client() -> Client:
    """Cliente service_role do projeto financeiro (segundo banco)."""
    global _service_client2
    if _service_client2 is None:
        _service_client2 = create_client(Config.SUPABASE2_URL, Config.SUPABASE2_SERVICE_KEY)
    return _service_client2
