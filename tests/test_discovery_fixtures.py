import unittest
from pathlib import Path

from estrategia_downloader.discovery import (
    extrair_aulas_html,
    extrair_candidatos_html,
    extrair_opcoes_video_html,
)

FIXTURES = Path(__file__).parent / "fixtures"


class DiscoveryFixturesTest(unittest.TestCase):
    def test_fixture_da_pagina_do_curso_encontra_aulas_sem_duplicar(self):
        aulas = extrair_aulas_html(
            (FIXTURES / "course_page.html").read_text(encoding="utf-8"),
            "https://fixture.invalid",
        )
        self.assertEqual([aula["num"] for aula in aulas], [1, 2])

    def test_fixture_cobre_variantes_de_material(self):
        itens = extrair_candidatos_html(
            (FIXTURES / "lesson_materials.html").read_text(encoding="utf-8"),
            "https://fixture.invalid/aula/1",
        )
        tipos = [item["tipo"] for item in itens]
        self.assertGreaterEqual(tipos.count("pdf"), 3)
        self.assertIn("slides", tipos)
        self.assertIn("mapa_mental", tipos)
        self.assertIn("material", tipos)

    def test_fixture_resolve_urls_relativas_e_data_attributes(self):
        itens = extrair_candidatos_html(
            (FIXTURES / "lesson_materials.html").read_text(encoding="utf-8"),
            "https://fixture.invalid/aula/1",
        )
        urls = {item["url"] for item in itens}
        self.assertIn("https://fixture.invalid/safe/simplificado", urls)
        self.assertIn("https://fixture.invalid/safe/mapa.pdf", urls)
        self.assertFalse(any(url.startswith("javascript:") for url in urls))
        self.assertNotIn("https://fixture.invalid/marketing", urls)

    def test_link_de_resolucao_nao_vira_material(self):
        itens = extrair_candidatos_html(
            (FIXTURES / "video_options.html").read_text(encoding="utf-8"),
            "https://fixture.invalid/aula/1",
        )
        self.assertFalse(any("video-" in item["url"] for item in itens))

    def test_fixture_escolhe_maior_resolucao_anunciada(self):
        opcoes = extrair_opcoes_video_html(
            (FIXTURES / "video_options.html").read_text(encoding="utf-8"),
            "https://fixture.invalid/aula/1",
        )
        self.assertEqual(opcoes[0]["resolucao"], 1080)
        self.assertTrue(opcoes[0]["url"].endswith("video-1080.mp4"))


if __name__ == "__main__":
    unittest.main()
