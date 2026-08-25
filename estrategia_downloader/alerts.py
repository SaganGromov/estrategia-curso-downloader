import re
import unicodedata
from collections import Counter
from collections.abc import Callable

from selenium.common.exceptions import (
    NoAlertPresentException,
    UnexpectedAlertPresentException,
)

from .config import ALERT_RECOVERY_ATTEMPTS

KNOWN_ASSISTANT_FRAGMENTS = (
    "virtual assistant",
    "not yet been deployed",
    "incorrect configuration",
    "check back later",
)


class AlertaDesconhecidoError(RuntimeError):
    def __init__(self, texto: str):
        self.texto = texto
        super().__init__(
            "O site exibiu repetidamente uma mensagem inesperada e o programa "
            "não conseguiu continuar com segurança. Os arquivos já baixados "
            f"foram preservados. Mensagem do site: {texto!r}"
        )


class AlertaRecorrenteError(RuntimeError):
    pass


def normalizar_alerta(texto: str) -> str:
    decomposicao = unicodedata.normalize("NFKD", texto or "")
    sem_acentos = "".join(
        char for char in decomposicao if not unicodedata.combining(char)
    )
    return re.sub(r"\s+", " ", sem_acentos).strip().lower()


def classificar_alerta(texto: str) -> str:
    normalizado = normalizar_alerta(texto)
    ocorrencias = sum(
        fragmento in normalizado for fragmento in KNOWN_ASSISTANT_FRAGMENTS
    )
    return "assistente_virtual" if ocorrencias >= 2 else "desconhecido"


class RecuperadorAlertas:
    def __init__(self, driver, painel=None, verificar_cancelamento=None):
        self.driver = driver
        self.painel = painel
        self.verificar_cancelamento = verificar_cancelamento
        self.ocorrencias = Counter()

    def _cancelamento(self):
        if self.verificar_cancelamento is not None:
            self.verificar_cancelamento()

    def _texto_e_dispensar(self, excecao=None) -> str:
        texto = getattr(excecao, "alert_text", "") or ""
        try:
            alerta = self.driver.switch_to.alert
            if not texto:
                texto = alerta.text or ""
            alerta.dismiss()
        except NoAlertPresentException:
            # "dismiss and notify" pode já ter fechado o prompt antes de lançar
            # UnexpectedAlertPresentException. O alert_text ainda é confiável.
            pass
        return re.sub(r"\s+", " ", texto).strip()[:500] or "(sem texto)"

    def _registrar(self, texto: str, tipo: str):
        chave = (tipo, normalizar_alerta(texto))
        self.ocorrencias[chave] += 1
        quantidade = self.ocorrencias[chave]
        if tipo == "assistente_virtual":
            if quantidade == 1:
                mensagem = (
                    "AVISO: O site exibiu uma mensagem do assistente virtual. "
                    "Ela não interfere no download e foi fechada automaticamente."
                )
            elif quantidade <= 3 or quantidade % 5 == 0:
                mensagem = (
                    "AVISO: Mensagem do assistente virtual ignorada novamente "
                    f"({quantidade} ocorrências)."
                )
            else:
                return quantidade
        else:
            mensagem = f"AVISO: O site exibiu uma mensagem inesperada: {texto}"
        print(mensagem)
        if self.painel is not None:
            self.painel.atualizar(
                aviso=mensagem,
                alertas_ignorados=sum(self.ocorrencias.values()),
                fase="Retomando após mensagem do site…",
            )
        return quantidade

    def resolver_pendente(self, *, permitir_desconhecido=False) -> bool:
        self._cancelamento()
        try:
            alerta = self.driver.switch_to.alert
            texto = re.sub(r"\s+", " ", alerta.text or "").strip()[:500]
            alerta.dismiss()
        except NoAlertPresentException:
            return False
        tipo = classificar_alerta(texto)
        quantidade = self._registrar(texto, tipo)
        if tipo == "desconhecido" and (not permitir_desconhecido or quantidade > 1):
            raise AlertaDesconhecidoError(texto)
        self._cancelamento()
        return True

    def executar_leitura(
        self,
        operacao: Callable,
        *,
        descricao: str,
        tentativas: int = ALERT_RECOVERY_ATTEMPTS,
    ):
        desconhecidos = 0
        total_execucoes = tentativas + 1
        for tentativa in range(1, total_execucoes + 1):
            self._cancelamento()
            self.resolver_pendente(permitir_desconhecido=desconhecidos == 0)
            try:
                return operacao()
            except UnexpectedAlertPresentException as erro:
                texto = self._texto_e_dispensar(erro)
                tipo = classificar_alerta(texto)
                self._registrar(texto, tipo)
                if tipo == "desconhecido":
                    desconhecidos += 1
                    if desconhecidos > 1:
                        raise AlertaDesconhecidoError(texto) from erro
                if tentativa == total_execucoes:
                    raise AlertaRecorrenteError(
                        "O site continuou exibindo a mesma mensagem e o programa "
                        "não conseguiu retomar a operação com segurança. Os "
                        "arquivos já baixados foram preservados."
                    ) from erro
                print(
                    f"   ↪️ Retentando: {descricao} ({tentativa + 1}/{total_execucoes})"
                )
        raise RuntimeError(f"Não foi possível concluir: {descricao}")

    def navegar(self, url: str, *, descricao: str):
        return self.executar_leitura(
            lambda: self.driver.get(url),
            descricao=descricao,
        )
