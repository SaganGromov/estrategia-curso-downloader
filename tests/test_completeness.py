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

    def test_lista_de_aulas_precisa_repetir_o_inventario_completo(self):
        primeira = [{"num": 1, "nome": "Aula 1", "href": "/aulas/1"}]
        completa = primeira + [
            {"num": 2, "nome": "Aula 2", "href": "/aulas/2"}
        ]
        with (
            patch.object(
                app,
                "listar_aulas",
                side_effect=[primeira, completa, completa, completa],
            ) as listar,
            patch.object(app, "INVENTORY_MAX_PASSES", 4),
            patch.object(app, "INVENTORY_STABLE_OBSERVATIONS", 3),
            patch("sys.stdout", new_callable=io.StringIO),
        ):
            aulas = app.listar_aulas_auditadas(
                SimpleNamespace(), "https://site.test/cursos/10"
            )

        self.assertEqual(aulas, completa)
        self.assertEqual(listar.call_count, 4)

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

    def test_videos_ja_confirmados_sao_pulados_na_nova_passagem(self):
        videos = [
            ElementoFake("Vídeo 1"),
            ElementoFake("Vídeo 2"),
            ElementoFake("Vídeo 3"),
        ]
        selecionados = []

        def selecionar(_driver, indice, _alertas):
            selecionados.append(indice)
            return (
                f"Vídeo {indice + 1}",
                [(720, f"https://cdn.test/video-{indice + 1}.mp4")],
            )

        with (
            patch.object(app, "_carregar_videos_da_aula", return_value=videos),
            patch.object(app, "_selecionar_video_e_obter_opcoes", selecionar),
            patch("sys.stdout", new_callable=io.StringIO),
        ):
            itens = list(
                app.iterar_videos_da_aula_atual(
                    DriverVideoFake(),
                    1,
                    "Aula 1",
                    ignorar_posicoes={1, 3},
                )
            )

        self.assertEqual([item["item_num"] for item in itens], [2])
        self.assertEqual(selecionados, [1])

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

    def test_icone_decorativo_sem_url_nao_vira_falha(self):
        elemento = ElementoFake("", {"class": "icon-download"})
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
        self.assertEqual(falhas, [])

    def test_execucao_com_pendencia_nao_pode_ser_marcada_completa(self):
        with self.assertRaises(ConteudoIncompletoError):
            app.garantir_curso_completo(
                SimpleNamespace(falhas=2, encontrados=2)
            )
        app.garantir_curso_completo(SimpleNamespace(falhas=0, encontrados=2))

    def test_execucao_sem_arquivo_nao_pode_ser_marcada_completa(self):
        with self.assertRaisesRegex(ConteudoIncompletoError, "nenhum arquivo"):
            app.garantir_curso_completo(
                SimpleNamespace(falhas=0, encontrados=0)
            )

    def test_pdf_tardio_e_incluido_antes_de_confirmar_a_aula(self):
        pdf = {
            "tipo": "pdf",
            "aula_num": 5,
            "aula_nome": "Aula 05",
            "item_num": 1,
            "titulo": "Livro Eletrônico",
            "extensao": ".pdf",
            "url": "https://api.test/pdf/55?signature=temporaria",
            "rotulo": "PDF",
        }
        video = {
            "tipo": "video",
            "aula_num": 5,
            "aula_nome": "Aula 05",
            "item_num": 1,
            "titulo": "Balanço de Pagamentos",
            "extensao": ".mp4",
            "url": "https://cdn.test/video/99.mp4?Expires=1",
        }
        registro_video = {
            "chave": "posicao=1|id=99",
            "numero": 1,
            "titulo": "Balanço de Pagamentos",
        }
        gerenciador = SimpleNamespace(
            sessao=SimpleNamespace(headers={}),
            urls_processadas=set(),
            urls_concluidas=set(),
            falhas_registradas=[],
        )
        gerenciador.registrar_falha_descoberta = (
            gerenciador.falhas_registradas.append
        )

        def baixar(item, _arquivo, manager):
            chave = app.resource_key(item["url"])
            manager.urls_processadas.add(chave)
            manager.urls_concluidas.add(chave)
            return True

        with (
            patch.object(
                app,
                "coletar_materiais_da_aula_atual",
                side_effect=[([], set()), ([pdf], set()), ([pdf], set())],
            ),
            patch.object(
                app,
                "_inventario_videos_dom",
                return_value=[registro_video],
            ),
            patch.object(
                app,
                "iterar_videos_da_aula_atual",
                return_value=iter([video]),
            ) as iterar_videos,
            patch.object(app, "registrar_e_baixar", side_effect=baixar),
            patch.object(app, "_navegar_para_auditoria"),
            patch.object(app, "INVENTORY_MAX_PASSES", 3),
            patch.object(app, "INVENTORY_STABLE_OBSERVATIONS", 2),
            patch.object(app, "INVENTORY_EMPTY_STABLE_OBSERVATIONS", 3),
            patch("sys.stdout", new_callable=io.StringIO),
        ):
            inventario = app.auditar_e_baixar_aula(
                SimpleNamespace(),
                None,
                io.StringIO(),
                gerenciador,
                href="https://site.test/aulas/5",
                aula_num=5,
                aula_nome="Aula 05",
                incluir_videos=True,
                permitir_vazio=False,
            )

        self.assertTrue(inventario["estavel"])
        self.assertEqual(len(inventario["materiais"]), 1)
        self.assertEqual(len(inventario["videos"]), 1)
        self.assertEqual(gerenciador.falhas_registradas, [])
        iterar_videos.assert_called_once()

    def test_video_repetido_no_curso_reutiliza_id_ja_confirmado(self):
        registro_video = {
            "chave": "posicao=1|id=99",
            "numero": 1,
            "titulo": "Vídeo repetido",
            "identificador": "99",
        }
        gerenciador = SimpleNamespace(
            sessao=SimpleNamespace(headers={}),
            urls_processadas=set(),
            urls_concluidas=set(),
            falhas_registradas=[],
        )
        gerenciador.registrar_falha_descoberta = (
            gerenciador.falhas_registradas.append
        )

        with (
            patch.object(
                app,
                "coletar_materiais_da_aula_atual",
                return_value=([], set()),
            ),
            patch.object(
                app,
                "_inventario_videos_dom",
                return_value=[registro_video],
            ),
            patch.object(
                app,
                "iterar_videos_da_aula_atual",
                return_value=iter([]),
            ) as iterar_videos,
            patch.object(app, "_navegar_para_auditoria"),
            patch.object(app, "INVENTORY_MAX_PASSES", 3),
            patch.object(app, "INVENTORY_STABLE_OBSERVATIONS", 3),
            patch.object(app, "INVENTORY_EMPTY_STABLE_OBSERVATIONS", 3),
            patch("sys.stdout", new_callable=io.StringIO),
        ):
            inventario = app.auditar_e_baixar_aula(
                SimpleNamespace(),
                None,
                io.StringIO(),
                gerenciador,
                href="https://site.test/aulas/2",
                aula_num=2,
                aula_nome="Aula 02",
                incluir_videos=True,
                permitir_vazio=False,
                ids_video_confirmados_curso={"99"},
            )

        self.assertTrue(inventario["estavel"])
        self.assertEqual(len(inventario["videos"]), 1)
        self.assertEqual(gerenciador.falhas_registradas, [])
        iterar_videos.assert_called_once()
        self.assertEqual(
            iterar_videos.call_args.kwargs["ignorar_posicoes"], {1}
        )

    def test_rota_geral_instavel_e_oportunistica_nao_cria_falha(self):
        gerenciador = SimpleNamespace(
            sessao=SimpleNamespace(headers={}),
            urls_processadas=set(),
            urls_concluidas=set(),
            falhas_registradas=[],
        )
        gerenciador.registrar_falha_descoberta = (
            gerenciador.falhas_registradas.append
        )
        with (
            patch.object(
                app,
                "coletar_materiais_da_aula_atual",
                return_value=([], set()),
            ),
            patch.object(app, "_inventario_videos_dom", return_value=[]),
            patch.object(
                app,
                "iterar_videos_da_aula_atual",
                return_value=iter([]),
            ),
            patch.object(app, "_navegar_para_auditoria"),
            patch.object(app, "INVENTORY_MAX_PASSES", 2),
            patch("sys.stdout", new_callable=io.StringIO),
        ):
            inventario = app.auditar_e_baixar_aula(
                SimpleNamespace(),
                None,
                io.StringIO(),
                gerenciador,
                href="https://site.test/cursos/10",
                aula_num=0,
                aula_nome="Materiais gerais",
                incluir_videos=True,
                permitir_vazio=True,
                exigir_convergencia=False,
            )

        self.assertEqual(inventario["modo"], "oportunistico")
        self.assertEqual(gerenciador.falhas_registradas, [])


if __name__ == "__main__":
    unittest.main()
