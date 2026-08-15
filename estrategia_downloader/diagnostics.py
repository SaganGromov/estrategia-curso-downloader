import json
import os
import platform
import sys
from datetime import UTC, datetime

import requests
import selenium

from . import __version__
from .utils import sanitizar_texto


def _erro_sanitizado(erro: Exception | None) -> dict | None:
    if erro is None:
        return None
    mensagem = sanitizar_texto(str(erro)).split("Stacktrace:", 1)[0].strip()
    return {"tipo": erro.__class__.__name__, "mensagem": mensagem[:1500]}


def criar_diagnostico(
    *,
    fase: str,
    logs: list[str],
    erro: Exception | None = None,
    browser: dict | None = None,
) -> str:
    dados = {
        "aplicacao": {
            "versao": __version__,
            "commit": os.getenv("ESTRATEGIA_APP_COMMIT", "não informado"),
        },
        "gerado_em": datetime.now(UTC).isoformat(),
        "sistema": {
            "windows": platform.platform(),
            "arquitetura": platform.machine(),
        },
        "python": {
            "versao": platform.python_version(),
            "executavel": sys.executable,
        },
        "componentes": {
            "selenium": selenium.__version__,
            "requests": requests.__version__,
        },
        "browser": browser or {"nome": "Microsoft Edge", "versao": "desconhecida"},
        "fase": fase,
        "erro": _erro_sanitizado(erro),
        "logs_recentes": [sanitizar_texto(linha)[:2000] for linha in logs[-100:]],
        "privacidade": (
            "Senhas, cookies, cabeçalhos de autorização, token da interface e "
            "parâmetros sensíveis de URLs não são incluídos."
        ),
    }
    return json.dumps(dados, ensure_ascii=False, indent=2)
