import io
import unittest

from estrategia_downloader import app
from estrategia_downloader.course_inventory import (
    CourseLesson,
    extract_lesson_snapshot,
)
from estrategia_downloader.integrity import resource_key


class DownloadManagerFake:
    def __init__(self):
        self.urls_processadas = set()
        self.urls_concluidas = set()
        self.items = []
        self.discovery_failures = []

    def baixar(self, item):
        key = resource_key(item["url"])
        self.urls_processadas.add(key)
        self.urls_concluidas.add(key)
        self.items.append(item)
        return True

    def registrar_falha_descoberta(self, description):
        self.discovery_failures.append(description)


class ApiDownloadTest(unittest.TestCase):
    def test_downloads_each_resource_from_one_authoritative_snapshot(self):
        lesson = CourseLesson("20", 1, 5, "Aula 05", "https://site/aula")
        snapshot = extract_lesson_snapshot(
            {
                "data": {
                    "id": 20,
                    "pdf": "https://cdn.test/book.pdf",
                    "videos": [
                        {
                            "id": 30,
                            "titulo": "Parte 1",
                            "resolucoes": {
                                "480p": "https://cdn.test/video-480.mp4",
                                "720p": "https://cdn.test/video-720.mp4",
                            },
                        }
                    ],
                }
            },
            lesson,
        )
        manager = DownloadManagerFake()

        manifest = app.auditar_e_baixar_snapshot_api(
            snapshot,
            io.StringIO(),
            manager,
            incluir_videos=True,
        )

        self.assertEqual(len(manager.items), 2)
        self.assertEqual(manager.items[1]["url"], "https://cdn.test/video-720.mp4")
        self.assertEqual(manifest["passagens"], 1)
        self.assertEqual(manifest["modo"], "api")
        self.assertTrue(manifest["estavel"])
        self.assertEqual(len(manifest["materiais"]), 1)
        self.assertEqual(len(manifest["videos"]), 1)
        self.assertEqual(len(manifest["arquivos"]), 2)
        self.assertEqual(manager.discovery_failures, [])

    def test_unknown_api_url_blocks_completion_without_exposing_its_value(self):
        lesson = CourseLesson("20", 1, 0, "Aula 00", "https://site/aula")
        snapshot = extract_lesson_snapshot(
            {
                "data": {
                    "id": 20,
                    "videos": [],
                    "new_resource": "https://cdn.test/private.bin?token=secret",
                }
            },
            lesson,
        )
        manager = DownloadManagerFake()

        manifest = app.auditar_e_baixar_snapshot_api(
            snapshot,
            io.StringIO(),
            manager,
            incluir_videos=True,
        )

        self.assertFalse(manifest["estavel"])
        self.assertEqual(len(manager.discovery_failures), 1)
        self.assertIn("$.data.new_resource", manager.discovery_failures[0])
        self.assertNotIn("secret", manager.discovery_failures[0])


if __name__ == "__main__":
    unittest.main()
