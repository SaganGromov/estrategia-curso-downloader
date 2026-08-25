import io
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.common.by import By

from estrategia_downloader import app
from estrategia_downloader.errors import ConteudoIncompletoError


class ElementoFake:
    def __init__(self, texto, atributos=None):
        self.text = texto
        self.atributos = atributos or {}

    def get_attribute(self, nome):
        return self.atributos.get(nome)

    def find_element(self, *_args):
        raise NoSuchElementException()


class DriverListaLazyFake:
    def __init__(self):
        self.carregados = 1
        self.elementos = [
            ElementoFake(f"Aula {numero}", {"href": f"/aulas/{numero}"})
            for numero in range(1, 4)
        ]

    def find_elements(self, _by, seletor):
        if seletor.startswith("button"):
            return []
        return self.elementos[: self.carregados]

    def execute_script(self, script, *_args):
        if script.startswith("return Math.max"):
            return self.carregados * 100
        if script.startswith("window.scrollTo"):
            self.carregados = min(self.carregados + 1, len(self.elementos))
        return None


class DriverVideoFake:
    current_url = "https://example.test/aulas/1"


class CompletenessTest(unittest.TestCase):
    def test_lista_dinamica_rola_ate_revelar_todos_os_itens(self):
        driver = DriverListaLazyFake()
        itens = app._carregar_lista_dinamica(
            driver,
            By.CSS_SELECTOR,
            ".aula",
            atributos=("href",),
            max_rodadas=10,
            rodadas_estaveis=2,
            pausa=0,
        )
        self.assertEqual(len(itens), 3)

    def test_video_ausente_e_recuperado_depois_de_reabrir_a_aula(self):
        videos = [ElementoFake("Vídeo 1"), ElementoFake("Vídeo 2")]
        chamadas = {0: 0, 1: 0}

        def selecionar(_driver, indice, _alertas):
            chamadas[indice] += 1
            if indice == 1 and chamadas[indice] == 1:
                return "Vídeo 2", []
            return (
                f"Vídeo {indice + 1}",
                [(1080, f"https://cdn.test/video-{indice + 1}.mp4")],
            )

        with (
            patch.object(app, "_carregar_videos_da_aula", return_value=videos),
            patch.object(
                app, "_reabrir_aula_para_recuperar_videos", return_value=videos
            ) as reabrir,
            patch.object(app, "_selecionar_video_e_obter_opcoes", selecionar),
            patch("sys.stdout", new_callable=io.StringIO),
        ):
            falhas = []
            itens = list(
                app.iterar_videos_da_aula_atual(
                    DriverVideoFake(), 1, "Aula 1", registrar_falha=falhas.append
                )
            )

        self.assertEqual([item["item_num"] for item in itens], [1, 2])
        self.assertEqual(falhas, [])
        reabrir.assert_called_once()

    def test_auditoria_final_inclui_video_revelado_durante_downloads(self):
        primeiro = [ElementoFake("Vídeo 1")]
        completo = primeiro + [ElementoFake("Vídeo 2")]

        def selecionar(_driver, indice, _alertas):
            return (
                f"Vídeo {indice + 1}",
                [(1080, f"https://cdn.test/video-{indice + 1}.mp4")],
            )

        with (
            patch.object(
                app,
                "_carregar_videos_da_aula",
                side_effect=[primeiro, completo, completo],
            ),
            patch.object(
                app, "_reabrir_aula_para_recuperar_videos", return_value=completo
            ),
            patch.object(app, "_selecionar_video_e_obter_opcoes", selecionar),
            patch("sys.stdout", new_callable=io.StringIO),
        ):
            itens = list(
                app.iterar_videos_da_aula_atual(DriverVideoFake(), 1, "Aula 1")
            )

        self.assertEqual([item["item_num"] for item in itens], [1, 2])

    def test_video_visivel_sem_link_vira_falha_real_uma_unica_vez(self):
        videos = [ElementoFake("Vídeo disponível"), ElementoFake("Vídeo pendente")]

        def selecionar(_driver, indice, _alertas):
            if indice == 0:
                return "Vídeo disponível", [(720, "https://cdn.test/video-1.mp4")]
            return "Vídeo pendente", []

        with (
            patch.object(app, "_carregar_videos_da_aula", return_value=videos),
            patch.object(
                app, "_reabrir_aula_para_recuperar_videos", return_value=videos
            ),
            patch.object(app, "_selecionar_video_e_obter_opcoes", selecionar),
            patch("sys.stdout", new_callable=io.StringIO),
        ):
            falhas = []
            itens = list(
                app.iterar_videos_da_aula_atual(
                    DriverVideoFake(), 4, "Aula 4", registrar_falha=falhas.append
                )
            )

        self.assertEqual(len(itens), 1)
        self.assertEqual(len(falhas), 1)
        self.assertIn("vídeo 02", falhas[0])

    def test_material_anunciado_sem_url_vira_falha(self):
        elemento = ElementoFake("Baixar Livro Eletrônico versão original")
        driver = SimpleNamespace(current_url="https://example.test/aulas/2")
        with (
            patch.object(app, "_carregar_lista_dinamica", return_value=[elemento]),
            patch("sys.stdout", new_callable=io.StringIO),
        ):
            falhas = []
            itens = list(
                app.iterar_materiais_da_aula_atual(
                    driver, 2, "Aula 2", registrar_falha=falhas.append
                )
            )

        self.assertEqual(itens, [])
        self.assertEqual(len(falhas), 1)
        self.assertIn("material sem link", falhas[0])

    def test_execucao_com_pendencia_nao_pode_ser_marcada_completa(self):
        with self.assertRaises(ConteudoIncompletoError):
            app.garantir_curso_completo(SimpleNamespace(falhas=2))
        app.garantir_curso_completo(SimpleNamespace(falhas=0))


if __name__ == "__main__":
    unittest.main()
