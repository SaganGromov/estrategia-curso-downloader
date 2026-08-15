import io
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import estrategia_download_edge_any as app


class BotaoFake:
    def __init__(self, texto="", atributos=None):
        self.text = texto
        self.atributos = atributos or {}

    def get_attribute(self, nome):
        return self.atributos.get(nome)


class DriverFake:
    def get_cookies(self):
        return []

    def execute_script(self, _script):
        return "User-Agent de teste"


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

    def test_detecta_maior_resolucao_anunciada(self):
        botao = BotaoFake("Baixar 720p ou 1080p")
        self.assertEqual(app._resolucao_do_botao(botao), 1080)

    def test_detecta_qualidade_4k(self):
        self.assertEqual(app._resolucao_do_botao(BotaoFake("Download 4K")), 2160)

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
