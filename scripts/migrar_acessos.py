"""Copia permissions/usina_ids do user_metadata do Auth para usuario_acessos.

Rodar UMA vez, depois de criar a tabela e ANTES de publicar o código que lê
dela. Idempotente: rodar de novo só reescreve os mesmos valores.

    PYTHONPATH=. .venv/bin/python scripts/migrar_acessos.py          # simulação
    PYTHONPATH=. .venv/bin/python scripts/migrar_acessos.py --gravar # grava
"""
import sys

from services.supabase_client import get_service_client
from services.auth_service import gravar_acesso, ler_acesso


def main(gravar: bool) -> int:
    sb = get_service_client()
    res = sb.auth.admin.list_users()
    users = res if isinstance(res, list) else getattr(res, "users", res)

    migrados = 0
    for user in (users or []):
        meta = getattr(user, "user_metadata", None) or {}
        if not isinstance(meta, dict):
            continue
        perms = meta.get("permissions")
        usinas = meta.get("usina_ids")
        if perms is None and usinas is None:
            continue   # admin ou conta sem acesso configurado

        email = getattr(user, "email", "") or ""
        uid = getattr(user, "id", None)
        print(f"{email}")
        print(f"    permissions={perms or []}")
        print(f"    usina_ids={usinas or []}")
        if gravar:
            gravar_acesso(uid, usinas or [], perms or [])
            conf = ler_acesso(uid)
            ok = (sorted(conf["permissions"]) == sorted(perms or [])
                  and sorted(conf["usina_ids"]) == sorted(usinas or []))
            print(f"    gravado e conferido: {'OK' if ok else 'DIVERGENTE'}")
            if not ok:
                return 1
        migrados += 1

    print(f"\n{migrados} usuário(s) {'migrados' if gravar else 'a migrar'}.")
    if not gravar:
        print("Simulação — nada foi gravado. Use --gravar para aplicar.")
    return 0


if __name__ == "__main__":
    sys.exit(main("--gravar" in sys.argv))
