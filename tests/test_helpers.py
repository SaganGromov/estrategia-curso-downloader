import io
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import estrategia_download_edge_any as app
from interface_web import InterfaceWeb, extrair_id_interface


class BotaoFake:
    def __init__(self, texto="", atributos=None):
        self.text = texto
        self.atributos = atributos or {}

    def get_attribute(self, nome):
        return self.atributos.get(nome)


class DriverFake:
    def __init__(self):
        self.comandos_cdp = []

    def get_cookies(self):
        return []

    def execute_script(self, _script):
        return "User-Agent de teste"

    def execute_cdp_cmd(self, comando, parametros):
        self.comandos_cdp.append((comando, parametros))


class DriverMateriaisFake:
    current_url = "https://example.test/aula"

    def __init__(self):
        self.elementos = [
            BotaoFake(
                "Baixar Livro Eletrônico versão simplificada",
                {"href": "/simplificado"},
            ),
            BotaoFake(
                "Baixar Livro Eletrônico versão original",
                {"href": "/original"},
            ),
            BotaoFake(
                "Baixar Livro Eletrônico marcação dos aprovados",
                {"href": "/marcado"},
            ),
            BotaoFake("Baixar slides da aula", {"href": "/slides"}),
            BotaoFake("Baixar mapa mental", {"href": "/mapa"}),
        ]

    def find_elements(self, *_args):
        return self.elementos


class RespostaFake:
    headers = {"Content-Length": "6", "Content-Type": "application/pdf"}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size):
        self.chunk_size = chunk_size
        yield b"abc"
        yield b"def"


class SessaoFake:
    def get(self, *_args, **_kwargs):
        return RespostaFake()


class HelpersTest(unittest.TestCase):
    def test_extrai_id_numerico(self):
        self.assertEqual(app.extrair_curso_id("393267"), "393267")

    def test_extrai_id_de_url(self):
        url = "https://example.test/app/dashboard/cursos/123456/aulas"
        self.assertEqual(app.extrair_curso_id(url), "123456")

    def test_rejeita_id_invalido(self):
        self.assertIsNone(app.extrair_curso_id("curso desconhecido"))

    def test_monta_url_do_curso(self):
        self.assertTrue(app.montar_curso_url("123456").endswith("/cursos/123456/aulas"))

    def test_monta_nome_timestampado_da_pasta_do_curso(self):
        with patch.object(app.time, "time", return_value=1_723_680_000.987):
            self.assertEqual(
                app.montar_nome_pasta_curso("393267"),
                "CURSO_ESTRATEGIA_393267_1723680000",
            )

    def test_cria_subpasta_com_id_e_timestamp(self):
        with TemporaryDirectory() as diretorio:
            driver = DriverFake()
            with patch("sys.stdout", new_callable=io.StringIO):
                pasta = app.criar_pasta_do_curso(
                    Path(diretorio), driver, "393267", 1_723_680_000
                )

            self.assertTrue(pasta.is_dir())
            self.assertEqual(pasta.name, "CURSO_ESTRATEGIA_393267_1723680000")
            self.assertEqual(
                driver.comandos_cdp,
                [
                    (
                        "Page.setDownloadBehavior",
                        {"behavior": "allow", "downloadPath": str(pasta)},
                    )
                ],
            )

    def test_detecta_maior_resolucao_anunciada(self):
        botao = BotaoFake("Baixar 720p ou 1080p")
        self.assertEqual(app._resolucao_do_botao(botao), 1080)

    def test_detecta_qualidade_4k(self):
        self.assertEqual(app._resolucao_do_botao(BotaoFake("Download 4K")), 2160)

    def test_classifica_livro_eletronico_simplificado_como_pdf(self):
        descricao = "Baixar Livro Eletrônico versão simplificada"
        self.assertEqual(app.classificar_material("", descricao), "pdf")

    def test_classifica_marcacao_dos_aprovados_como_pdf(self):
        descricao = "Baixar Livro Eletrônico marcação dos aprovados"
        self.assertEqual(app.classificar_material("", descricao), "pdf")

    def test_classifica_slides_e_mapa_mental(self):
        self.assertEqual(
            app.classificar_material("", "Baixar slides da aula"), "slides"
        )
        self.assertEqual(
            app.classificar_material("", "Baixar mapa mental"), "mapa_mental"
        )

    def test_resolucao_de_video_nao_vira_material_generico(self):
        self.assertIsNone(app.classificar_material("https://x/video", "Baixar 1080p"))

    def test_detecta_extensao_real_de_slides(self):
        resposta = RespostaFake()
        resposta.headers = {
            "Content-Disposition": 'attachment; filename="aula.pptx"',
            "Content-Type": "application/octet-stream",
        }
        self.assertEqual(
            app.detectar_extensao_resposta(resposta, "https://x/download", ".bin"),
            ".pptx",
        )

    def test_flag_pdfs_e_slides(self):
        with patch.object(sys, "argv", ["programa", "--pdfs-e-slides"]):
            self.assertTrue(app.ler_argumentos().pdfs_e_slides)

    def test_modo_padrao_e_completo(self):
        with patch.object(sys, "argv", ["programa"]):
            self.assertFalse(app.ler_argumentos().pdfs_e_slides)

    def test_modo_reduzido_inclui_mapas_mentais(self):
        self.assertEqual(
            app.tipos_permitidos_modo_reduzido(),
            {"pdf", "slides", "mapa_mental"},
        )

    def test_coleta_todas_as_variantes_mostradas_nos_cartoes(self):
        with patch("sys.stdout", new_callable=io.StringIO):
            materiais = list(
                app.iterar_materiais_da_aula_atual(DriverMateriaisFake(), 1, "Aula 1")
            )
        self.assertEqual(len(materiais), 5)
        self.assertEqual(
            [item["tipo"] for item in materiais],
            ["pdf", "pdf", "pdf", "slides", "mapa_mental"],
        )

    def test_modo_reduzido_coleta_pdfs_slides_e_mapas_mentais(self):
        with patch("sys.stdout", new_callable=io.StringIO):
            materiais = list(
                app.iterar_materiais_da_aula_atual(
                    DriverMateriaisFake(),
                    1,
                    "Aula 1",
                    app.tipos_permitidos_modo_reduzido(),
                )
            )
        self.assertEqual(len(materiais), 5)
        self.assertIn("mapa_mental", {item["tipo"] for item in materiais})

    def test_interface_extrai_id_de_url(self):
        self.assertEqual(
            extrair_id_interface(
                "https://example.test/app/dashboard/cursos/987654/aulas"
            ),
            "987654",
        )

    def test_interface_local_exige_token_para_estado(self):
        with TemporaryDirectory() as diretorio:
            painel = InterfaceWeb(
                modo_reduzido=True,
                pasta_inicial=Path(diretorio),
            )
            with patch("interface_web.abrir_interface_no_edge"):
                url = painel.iniciar()
            try:
                import requests

                self.assertEqual(requests.get(url, timeout=3).status_code, 200)
                endereco = url.split("/?", 1)[0]
                self.assertEqual(
                    requests.get(f"{endereco}/api/state", timeout=3).status_code,
                    403,
                )
                estado = requests.get(
                    f"{endereco}/api/state?token={painel.token}", timeout=3
                ).json()
                self.assertEqual(estado["status"], "configuracao")
                self.assertIn("mapas mentais", estado["modo"])
            finally:
                painel.parar()

    def test_interface_recebe_formulario_sem_expor_senha_no_estado(self):
        with TemporaryDirectory() as diretorio:
            pasta = Path(diretorio)
            painel = InterfaceWeb(modo_reduzido=False, pasta_inicial=pasta)
            with patch("interface_web.abrir_interface_no_edge"):
                url = painel.iniciar()
            try:
                painel._definir_pasta(pasta)
                import requests

                resposta = requests.post(
                    f"{url.split('/?', 1)[0]}/api/start?token={painel.token}",
                    headers={
                        "X-Interface-Token": painel.token,
                        "X-Estrategia-Request": "1",
                    },
                    json={
                        "email": "teste@example.test",
                        "senha": "segredo",
                        "curso": "393267",
                    },
                    timeout=3,
                )
                self.assertEqual(resposta.status_code, 202)
                configuracao = painel.aguardar_configuracao()
                self.assertEqual(configuracao["curso_id"], "393267")
                self.assertTrue(configuracao["pasta_base"].samefile(pasta))
                self.assertNotIn("segredo", str(painel.estado()))
            finally:
                painel.parar()

    def test_limpa_nome_para_windows(self):
        self.assertEqual(
            app.safe_filename("Aula: 01 / introdução?"), "Aula 01 introdução"
        )

    def test_formata_duracao(self):
        self.assertEqual(app.formatar_duracao(65), "01:05")
        self.assertEqual(app.formatar_duracao(3661), "01:01:01")
        self.assertEqual(app.formatar_duracao(None), "--:--")

    def test_download_atualiza_estatisticas_cumulativas(self):
        with TemporaryDirectory() as diretorio:
            gerenciador = app.GerenciadorDownloads(
                Path(diretorio), DriverFake(), "https://example.test/curso"
            )
            gerenciador.sessao = SessaoFake()
            item = {
                "tipo": "pdf",
                "aula_num": 1,
                "item_num": 1,
                "titulo": "Material",
                "extensao": ".pdf",
                "url": "https://example.test/material.pdf",
            }

            with patch("sys.stdout", new_callable=io.StringIO) as saida:
                self.assertTrue(gerenciador.baixar(item))
            progresso = saida.getvalue()
            self.assertIn("Item #1", progresso)
            self.assertIn("Conhecido 1/1", progresso)
            self.assertIn("/s", progresso)
            self.assertIn("ETA", progresso)
            self.assertEqual(gerenciador.baixados, 1)
            self.assertEqual(gerenciador.bytes_baixados, 6)
            self.assertEqual(
                (Path(diretorio) / "Aula 01 - PDF 01 - Material.pdf").read_bytes(),
                b"abcdef",
            )


if __name__ == "__main__":
    unittest.main()
