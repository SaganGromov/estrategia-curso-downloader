import requests
from selenium.common.exceptions import (
    TimeoutException,
    UnexpectedAlertPresentException,
    WebDriverException,
)

from .alerts import AlertaDesconhecidoError, AlertaRecorrenteError
from .browser import BrowserStartupError
from .utils import DestinoInvalidoError, EspacoInsuficienteError, sanitizar_texto


class ConteudoIncompletoError(RuntimeError):
    """A página anunciou conteúdo que não pôde ser localizado ou baixado."""


def mensagem_usuario_para_erro(erro: Exception) -> str:
    if isinstance(
        erro,
        (
            BrowserStartupError,
            AlertaDesconhecidoError,
            AlertaRecorrenteError,
            DestinoInvalidoError,
            EspacoInsuficienteError,
            ConteudoIncompletoError,
        ),
    ):
        return str(erro)
    if isinstance(erro, UnexpectedAlertPresentException):
        texto = sanitizar_texto(getattr(erro, "alert_text", "") or "mensagem sem texto")
        return (
            "O site exibiu uma mensagem inesperada e o programa não conseguiu "
            f"continuar com segurança. Mensagem do site: {texto!r}. Os arquivos "
            "já baixados foram preservados."
        )
    if isinstance(erro, TimeoutException):
        return (
            "O site demorou mais que o esperado para responder. Verifique a "
            "conexão, a janela de login e tente novamente."
        )
    if isinstance(erro, requests.ConnectionError):
        return (
            "Não foi possível conectar ao servidor. Verifique a internet e tente "
            "novamente; os arquivos completos já baixados foram preservados."
        )
    if isinstance(erro, requests.Timeout):
        return (
            "O servidor demorou demais para enviar o arquivo. O trecho parcial foi "
            "preservado e poderá ser retomado automaticamente."
        )
    if isinstance(erro, requests.HTTPError):
        status = getattr(getattr(erro, "response", None), "status_code", "?")
        return (
            f"O servidor recusou um download (HTTP {status}). A sessão pode ter "
            "expirado ou o material pode estar temporariamente indisponível."
        )
    if isinstance(erro, WebDriverException):
        return (
            "O Microsoft Edge deixou de responder à automação. Os arquivos já "
            "baixados foram preservados. Feche janelas travadas do Edge e tente "
            "novamente."
        )
    if isinstance(erro, PermissionError):
        return (
            "O Windows negou acesso a um arquivo ou pasta. Escolha um destino "
            "dentro do seu perfil e confirme que o arquivo não está aberto."
        )
    mensagem = sanitizar_texto(str(erro)).split("Stacktrace:", 1)[0].strip()
    return mensagem or erro.__class__.__name__
