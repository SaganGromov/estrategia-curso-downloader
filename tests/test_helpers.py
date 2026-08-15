import unittest

import estrategia_download_edge_any as app


class BotaoFake:
    def __init__(self, texto="", atributos=None):
        self.text = texto
        self.atributos = atributos or {}

    def get_attribute(self, nome):
        return self.atributos.get(nome)


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


if __name__ == "__main__":
    unittest.main()
