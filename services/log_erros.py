"""Registro de falhas que o código decide não propagar.

Um `except Exception: pass` some com o erro: a tela não quebra, só devolve
menos dado do que devia, e ninguém fica sabendo. Foi assim que a lista de
contas a receber do investidor passou meses vazia por causa de um NameError.

Quando engolir a exceção é mesmo o certo — a página vale a pena sem aquele
pedaço —, chame `ignorado()` no lugar do `pass`: o comportamento é idêntico
para quem usa o sistema, e o traceback completo aparece no log da Vercel.
"""
import logging

_log = logging.getLogger("portal")


def ignorado(contexto: str) -> None:
    """Registra a exceção em curso e segue. Use dentro de um `except`."""
    _log.warning("falha ignorada: %s", contexto, exc_info=True)
