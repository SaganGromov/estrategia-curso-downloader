import io
import unittest
from unittest.mock import patch

from selenium.common.exceptions import (
    NoAlertPresentException,
    UnexpectedAlertPresentException,
)

from estrategia_downloader.alerts import (
    AlertaDesconhecidoError,
    RecuperadorAlertas,
    classificar_alerta,
)
from interface_web import DownloadCancelado

ASSISTANT_ALERT = (
    "The virtual assistant you’re trying to interact with has not yet been "
    "deployed or deployed using incorrect configurations. Please check back later."
)


class AlertaFake:
    def __init__(self, texto):
        self.text = texto
        self.dispensado = False

    def dismiss(self):
        self.dispensado = True


class SwitchSemAlerta:
    @property
    def alert(self):
        raise NoAlertPresentException()


class SwitchComAlertas:
    def __init__(self, textos):
        self.alertas = [AlertaFake(texto) for texto in textos]

    @property
    def alert(self):
        if not self.alertas:
            raise NoAlertPresentException()
        return self.alertas.pop(0)


class DriverAlertaFake:
    def __init__(self, switch=None):
        self.switch_to = switch or SwitchSemAlerta()


class AlertsTest(unittest.TestCase):
    def test_classifica_alerta_do_assistente_com_variacao(self):
        self.assertEqual(classificar_alerta(ASSISTANT_ALERT), "assistente_virtual")
        self.assertEqual(
            classificar_alerta(
                "Virtual Assistant NOT YET BEEN DEPLOYED. Check back later."
            ),
            "assistente_virtual",
        )

    def test_alerta_auto_dispensado_reexecuta_operacao(self):
        driver = DriverAlertaFake()
        recuperador = RecuperadorAlertas(driver)
        chamadas = 0

        def operacao():
            nonlocal chamadas
            chamadas += 1
            if chamadas == 1:
                raise UnexpectedAlertPresentException(alert_text=ASSISTANT_ALERT)
            return "continuou"

        with patch("sys.stdout", new_callable=io.StringIO) as saida:
            resultado = recuperador.executar_leitura(
                operacao, descricao="abrir a próxima aula"
            )
        self.assertEqual(resultado, "continuou")
        self.assertEqual(chamadas, 2)
        self.assertIn("assistente virtual", saida.getvalue())

    def test_alerta_pendente_e_fechado_antes_da_navegacao(self):
        switch = SwitchComAlertas([ASSISTANT_ALERT])
        driver = DriverAlertaFake(switch)
        recuperador = RecuperadorAlertas(driver)
        with patch("sys.stdout", new_callable=io.StringIO):
            self.assertTrue(recuperador.resolver_pendente())
        self.assertFalse(switch.alertas)

    def test_tres_alertas_benignos_nao_impedem_continuacao(self):
        driver = DriverAlertaFake()
        recuperador = RecuperadorAlertas(driver)
        chamadas = 0

        def operacao():
            nonlocal chamadas
            chamadas += 1
            if chamadas <= 3:
                raise UnexpectedAlertPresentException(alert_text=ASSISTANT_ALERT)
            return 12

        with patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(
                recuperador.executar_leitura(
                    operacao, descricao="transição para aula 12"
                ),
                12,
            )
        self.assertEqual(chamadas, 4)

    def test_alerta_desconhecido_recorrente_nao_e_ocultado(self):
        driver = DriverAlertaFake()
        recuperador = RecuperadorAlertas(driver)

        def operacao():
            raise UnexpectedAlertPresentException(alert_text="Confirme uma ação")

        with patch("sys.stdout", new_callable=io.StringIO):
            with self.assertRaises(AlertaDesconhecidoError) as contexto:
                recuperador.executar_leitura(operacao, descricao="ler materiais")
        self.assertIn("Confirme uma ação", str(contexto.exception))

    def test_cancelamento_prevalece_sobre_recuperacao(self):
        driver = DriverAlertaFake()

        def cancelar():
            raise DownloadCancelado("cancelado")

        recuperador = RecuperadorAlertas(driver, verificar_cancelamento=cancelar)
        with self.assertRaises(DownloadCancelado):
            recuperador.executar_leitura(lambda: True, descricao="qualquer operação")

    def test_regressao_aula_11_alerta_e_continua_na_aula_12(self):
        driver = DriverAlertaFake()
        recuperador = RecuperadorAlertas(driver)
        concluidos = {"aula11-pdf", "aula11-slides", "aula11-mapa"}
        chamadas = 0

        def navegar_aula_12():
            nonlocal chamadas
            chamadas += 1
            if chamadas == 1:
                raise UnexpectedAlertPresentException(alert_text=ASSISTANT_ALERT)
            return "aula-12-aberta"

        with patch("sys.stdout", new_callable=io.StringIO) as saida:
            self.assertEqual(
                recuperador.executar_leitura(
                    navegar_aula_12, descricao="abrir a aula 12"
                ),
                "aula-12-aberta",
            )
        self.assertEqual(chamadas, 2)
        self.assertEqual(concluidos, {"aula11-pdf", "aula11-slides", "aula11-mapa"})
        self.assertNotIn("Stacktrace", saida.getvalue())

    def test_alerta_ao_localizar_materiais_repete_leitura_segura(self):
        recuperador = RecuperadorAlertas(DriverAlertaFake())
        chamadas = 0

        def localizar():
            nonlocal chamadas
            chamadas += 1
            if chamadas == 1:
                raise UnexpectedAlertPresentException(alert_text=ASSISTANT_ALERT)
            return ["pdf", "slides", "mapa"]

        with patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(
                recuperador.executar_leitura(
                    localizar, descricao="localizar materiais"
                ),
                ["pdf", "slides", "mapa"],
            )


if __name__ == "__main__":
    unittest.main()
