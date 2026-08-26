import unittest

from estrategia_downloader.utils import (
    chave_deduplicacao_url,
    safe_filename,
    sanitizar_texto,
    sanitizar_url,
    slug_nome_curso,
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

    def test_slug_do_curso_e_ascii_legivel_e_sem_espacos(self):
        self.assertEqual(
            slug_nome_curso("Língua Portuguesa — Revisão (Parte 2)"),
            "lingua-portuguesa-revisao-parte-2",
        )

    def test_slug_do_curso_respeita_limite_sem_hifen_final(self):
        self.assertEqual(slug_nome_curso("Curso muito longo", limite=10), "curso-muit")
        with self.assertRaises(ValueError):
            slug_nome_curso("東京")

    def test_url_sanitizada_remove_todos_os_valores_de_query(self):
        url = (
            "https://cdn.example/a.pdf?signature=segredo&expires=123&pagina=2&"
            "clienteId=41099099&expiration=amanha#token"
        )
        segura = sanitizar_url(url)
        self.assertNotIn("segredo", segura)
        self.assertNotIn("123", segura)
        self.assertNotIn("pagina=2", segura)
        self.assertNotIn("41099099", segura)
        self.assertNotIn("amanha", segura)
        self.assertEqual(segura.count("REMOVIDO"), 5)
        self.assertNotIn("#token", segura)

    def test_url_sanitizada_remove_identificadores_pessoais(self):
        url = (
            "https://analytics.example/event?email=pessoa%40example.com&"
            "firstname=Nome&lastname=Sobrenome&custom_user_id=123"
        )
        segura = sanitizar_url(url)
        self.assertNotIn("pessoa", segura)
        self.assertNotIn("Nome", segura)
        self.assertNotIn("Sobrenome", segura)
        self.assertNotIn("123", segura)
        self.assertEqual(segura.count("REMOVIDO"), 4)

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
