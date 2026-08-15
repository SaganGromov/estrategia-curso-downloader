import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from selenium import webdriver
from selenium.common.exceptions import UnexpectedAlertPresentException
from selenium.webdriver.common.by import By
from selenium.webdriver.edge.options import Options

from estrategia_downloader.alerts import RecuperadorAlertas

ALERTA = (
    "The virtual assistant you're trying to interact with has not yet been "
    "deployed or deployed using incorrect configurations. Please check back later."
)


@unittest.skipUnless(
    os.getenv("ESTRATEGIA_SELENIUM_INTEGRATION") == "1" and os.name == "nt",
    "integração real com Edge é executada no workflow dedicado",
)
class AlertSeleniumIntegrationTest(unittest.TestCase):
    def test_alerta_real_e_recuperado(self):
        with TemporaryDirectory() as pasta:
            pagina = Path(pasta) / "alerta.html"
            pagina.write_text(
                "<!doctype html><button id='ok'>continuou</button>"
                f"<script>alert({ALERTA!r})</script>",
                encoding="utf-8",
            )
            opcoes = Options()
            opcoes.add_argument("--headless=new")
            opcoes.unhandled_prompt_behavior = "dismiss and notify"
            driver = webdriver.Edge(options=opcoes)
            try:
                driver.get(pagina.as_uri())
                recuperador = RecuperadorAlertas(driver)

                def ler_botao():
                    try:
                        return driver.find_element(By.ID, "ok").text
                    except UnexpectedAlertPresentException:
                        raise

                self.assertEqual(
                    recuperador.executar_leitura(
                        ler_botao, descricao="ler DOM após alerta"
                    ),
                    "continuou",
                )
            finally:
                driver.quit()


if __name__ == "__main__":
    unittest.main()
