"""Entrada legada e compatível do Estratégia Curso Downloader.

A implementação vive no pacote ``estrategia_downloader``. Este arquivo é
mantido para que os comandos históricos com ``py`` continuem funcionando.
"""

from estrategia_downloader import app as _app
from estrategia_downloader.app import *  # noqa: F401,F403
from estrategia_downloader.downloads import (
    criar_sessao_download,  # noqa: F401
    detectar_extensao_resposta,  # noqa: F401
)
from estrategia_downloader.utils import formatar_duracao  # noqa: F401

# Auxiliares históricos começados por sublinhado não entram no import *.
_resolucao_do_botao = _app._resolucao_do_botao
time = _app.time


def __getattr__(nome):
    return getattr(_app, nome)


if __name__ == "__main__":
    raise SystemExit(_app.executar_com_tratamento_de_erros())
