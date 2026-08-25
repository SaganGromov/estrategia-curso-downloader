import unittest

from estrategia_downloader.utils import (
    chave_deduplicacao_url,
    safe_filename,
    sanitizar_texto,
    sanitizar_url,
)


class UtilsTest(unittest.TestCase):
    def test_filename_remove_caracteres_invalidos_e_finais(self):
        self.assertEqual(safe_filename('<Aula>: "01" / teste?* . '), "Aula 01 teste")

    def test_filename_preserva_unicode_e_acentos(self):
        self.assertEqual(
            safe_filename("Língua Portuguesa — revisão"), "Língua Portuguesa — revisão"
        )

    def test_filename_protege_nomes_reservados_windows(self):
        for nome in ("CON", "PRN", "AUX", "NUL", "COM1", "COM9", "LPT1", "LPT9"):
            with self.subTest(nome=nome):
                self.assertTrue(safe_filename(nome).startswith("_"))

    def test_filename_vazio_pontuacao_e_longo(self):
        self.assertEqual(safe_filename("...   "), "sem nome")
        self.assertLessEqual(len(safe_filename("á" * 500 + ".pdf")), 140)

    def test_url_sanitizada_remove_parametros_sensiveis(self):
        url = "https://cdn.example/a.pdf?signature=segredo&expires=123&pagina=2#token"
        segura = sanitizar_url(url)
        self.assertNotIn("segredo", segura)
        self.assertNotIn("123", segura)
        self.assertIn("pagina=2", segura)
        self.assertNotIn("#token", segura)

    def test_deduplicacao_ignora_assinatura_temporaria(self):
        primeira = "https://cdn.example/a.pdf?signature=um&pagina=2"
        segunda = "https://cdn.example/a.pdf?signature=dois&pagina=2"
        self.assertEqual(
            chave_deduplicacao_url(primeira), chave_deduplicacao_url(segunda)
        )

    def test_texto_de_log_sanitiza_urls(self):
        texto = sanitizar_texto("baixando https://cdn.example/a?token=segredo agora")
        self.assertNotIn("segredo", texto)


if __name__ == "__main__":
    unittest.main()
