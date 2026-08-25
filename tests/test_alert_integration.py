import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from selenium import webdriver
from selenium.common.exceptions import UnexpectedAlertPresentException
from selenium.webdriver.common.by import By
from selenium.webdriver.edge.options import Options

from estrategia_downloader.alerts import RecuperadorAlertas
from estrategia_downloader.app import iterar_videos_da_aula_atual

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

    def test_links_de_cada_video_podem_aparecer_com_atraso(self):
        with TemporaryDirectory() as pasta:
            pagina = Path(pasta) / "videos-atrasados.html"
            pagina.write_text(
                """<!doctype html><meta charset='utf-8'>
                <div id='videos'></div>
                <div id='painel'>
                  <div><strong>Opções de download</strong></div>
                  <div id='links'></div>
                </div>
                <script>
                const videos = document.getElementById('videos');
                const links = document.getElementById('links');
                for (let n = 1; n <= 4; n++) {
                  const button = document.createElement('button');
                  button.innerHTML =
                    `<span class="VideoItem-info-title">Vídeo ${n}</span>`;
                  button.addEventListener('click', () => {
                    links.replaceChildren();
                    setTimeout(() => {
                      for (const quality of [480, 1080]) {
                        const anchor = document.createElement('a');
                        anchor.textContent = `Baixar ${quality}p`;
                        anchor.href = `https://cdn.invalid/video-${n}-${quality}.mp4`;
                        links.appendChild(anchor);
                      }
                    }, 700);
                  });
                  videos.appendChild(button);
                }
                </script>""",
                encoding="utf-8",
            )
            opcoes = Options()
            opcoes.add_argument("--headless=new")
            opcoes.unhandled_prompt_behavior = "dismiss and notify"
            driver = webdriver.Edge(options=opcoes)
            try:
                driver.get(pagina.as_uri())
                itens = list(
                    iterar_videos_da_aula_atual(
                        driver, 1, "Aula de teste", RecuperadorAlertas(driver)
                    )
                )
                self.assertEqual(len(itens), 4)
                for numero, item in enumerate(itens, start=1):
                    self.assertIn(f"video-{numero}-1080.mp4", item["url"])
            finally:
                driver.quit()


if __name__ == "__main__":
    unittest.main()
