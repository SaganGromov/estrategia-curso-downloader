from datetime import date
import unittest
from unittest.mock import Mock

import requests

from estrategia_downloader.course_inventory import (
    LESSON_ENDPOINT,
    CourseInventoryError,
    CourseLesson,
    extract_course_snapshot,
    extract_lesson_snapshot,
    get_lesson_snapshot,
)
from estrategia_downloader.course_metadata import CourseAccessError


def response(status=200, payload=None):
    result = Mock(spec=requests.Response)
    result.status_code = status
    result.json.return_value = payload
    if status >= 400:
        result.raise_for_status.side_effect = requests.HTTPError(f"HTTP {status}")
    else:
        result.raise_for_status.return_value = None
    return result


class CourseInventoryTest(unittest.TestCase):
    def test_extracts_unique_lessons_and_requires_exact_total(self):
        snapshot = extract_course_snapshot(
            {
                "data": {
                    "id": 327530,
                    "nome": "Curso completo",
                    "total_aulas": 2,
                    "aulas": [
                        {
                            "id": 3163814,
                            "nome": "Aula 00",
                            "descricao": "Introdução",
                        },
                        {"id": 3163819, "nome": "Aula 01"},
                    ],
                }
            },
            "327530",
        )

        self.assertEqual(snapshot.total_lessons, 2)
        self.assertEqual([item.lesson_id for item in snapshot.lessons], ["3163814", "3163819"])
        self.assertEqual([item.number for item in snapshot.lessons], [0, 1])
        self.assertIn("Introdução", snapshot.lessons[0].name)
        self.assertTrue(snapshot.lessons[1].href.endswith("/aulas/3163819"))

        with self.assertRaisesRegex(CourseInventoryError, "total_aulas diverge"):
            extract_course_snapshot(
                {
                    "data": {
                        "id": 327530,
                        "nome": "Curso",
                        "total_aulas": 2,
                        "aulas": [{"id": 1, "nome": "Aula 00"}],
                    }
                },
                "327530",
            )

    def test_extracts_explicit_future_release_dates(self):
        snapshot = extract_course_snapshot(
            {
                "data": {
                    "id": 398810,
                    "nome": "Curso futuro",
                    "total_aulas": 0,
                    "aulas": [],
                    "aviso": "Aula 00: Disponível em 01/09/2026",
                }
            },
            "398810",
            today=date(2026, 8, 27),
        )
        self.assertEqual(snapshot.future_release_dates, (date(2026, 9, 1),))

    def test_attaches_an_explicit_release_date_to_its_lesson_id(self):
        snapshot = extract_course_snapshot(
            {
                "data": {
                    "id": 398810,
                    "nome": "Curso futuro",
                    "total_aulas": 2,
                    "aulas": [
                        {
                            "id": 40,
                            "nome": "Aula 00",
                            "data_publicacao": "2026-09-01T03:00:00.000Z",
                            "is_disponivel": False,
                        },
                        {
                            "id": 41,
                            "nome": "Aula 01",
                            "is_disponivel": True,
                        },
                    ],
                }
            },
            "398810",
            today=date(2026, 8, 27),
        )

        self.assertEqual(snapshot.lessons[0].release_date, date(2026, 9, 1))
        self.assertFalse(snapshot.lessons[0].is_available)
        self.assertIsNone(snapshot.lessons[1].release_date)
        self.assertTrue(snapshot.lessons[1].is_available)
        self.assertEqual(snapshot.future_release_dates, (date(2026, 9, 1),))
        self.assertIn("data_publicacao:string", snapshot.lesson_summary_schema)
        self.assertIn("id:number", snapshot.lesson_summary_schema)

    def test_course_snapshot_reports_unknown_urls_without_their_values(self):
        snapshot = extract_course_snapshot(
            {
                "data": {
                    "id": 100,
                    "nome": "Curso",
                    "total_aulas": 0,
                    "aulas": [],
                    "new_resource": "https://cdn.test/file?token=secret",
                    "icone": "https://cdn.test/icon.jpg",
                }
            },
            "100",
        )

        self.assertEqual(
            snapshot.unexpected_url_fields,
            ("$.data.new_resource",),
        )
        self.assertEqual(snapshot.ignored_ui_url_fields, ("$.data.icone",))
        self.assertNotIn("secret", repr(snapshot))

    def test_reconciles_summary_materials_and_classifies_professor_portrait(self):
        summary_pdf = "https://cdn.test/book.pdf?expiration=1"
        snapshot = extract_course_snapshot(
            {
                "data": {
                    "id": 100,
                    "nome": "Curso",
                    "total_aulas": 1,
                    "aulas": [
                        {
                            "id": 20,
                            "nome": "Aula 00",
                            "pdf": summary_pdf,
                            "pdf_simplificado": "https://cdn.test/simple.pdf",
                        }
                    ],
                    "professores": [
                        {"imagem": "https://cdn.test/professor.jpg"}
                    ],
                }
            },
            "100",
        )

        self.assertEqual(snapshot.unexpected_url_fields, ())
        self.assertEqual(snapshot.unresolved, ())
        self.assertEqual(snapshot.title, "Curso")
        self.assertEqual(
            snapshot.ignored_ui_url_fields,
            ("$.data.professores[0].imagem",),
        )
        self.assertNotIn(summary_pdf, repr(snapshot))

        lesson_snapshot = extract_lesson_snapshot(
            {
                "data": {
                    "id": 20,
                    "pdf": summary_pdf.replace("expiration=1", "expiration=2"),
                    "pdf_simplificado": "https://cdn.test/simple.pdf",
                    "videos": [],
                }
            },
            snapshot.lessons[0],
        )
        self.assertEqual(len(lesson_snapshot.materials), 2)

    def test_lesson_snapshot_selects_highest_video_and_all_known_files(self):
        lesson = CourseLesson("3163819", 1, 5, "Aula 05", "https://site/aula")
        shared_slide = "https://cdn.test/slide.pdf?expiration=1"
        snapshot = extract_lesson_snapshot(
            {
                "data": {
                    "id": 3163819,
                    "pdf_simplificado": "https://cdn.test/simple.pdf",
                    "pdf": "https://cdn.test/original.pdf",
                    "pdf_grifado": "https://cdn.test/marked.pdf",
                    "videos": [
                        {
                            "id": 90,
                            "titulo": "Parte 1",
                            "resumo": "https://cdn.test/resumo.pdf",
                            "slide": shared_slide,
                            "mapa_mental": "https://cdn.test/mapa.pdf",
                            "audio": "https://cdn.test/audio.mp3",
                            "thumbnail": "https://cdn.test/thumb.jpg",
                            "resolucoes": {
                                "720p": "https://cdn.test/v90-720.mp4",
                                "1080p": "https://cdn.test/v90-1080.mp4",
                            },
                        },
                        {
                            "id": 91,
                            "titulo": "Parte 2",
                            "resumo": "texto que não é arquivo",
                            "slide": shared_slide.replace("expiration=1", "expiration=2"),
                            "mapa_mental": "",
                            "audio": "",
                            "thumbnail": None,
                            "resolucoes": {"480p": "https://cdn.test/v91.mp4"},
                        },
                    ],
                }
            },
            lesson,
        )

        self.assertEqual(len(snapshot.videos), 2)
        self.assertEqual(snapshot.videos[0]["url"], "https://cdn.test/v90-1080.mp4")
        self.assertEqual(snapshot.video_identities[0], ("id=90", 1, "Parte 1"))
        self.assertEqual(len(snapshot.materials), 8)
        self.assertEqual(sum(item["tipo"] == "slides" for item in snapshot.materials), 1)
        self.assertIn(".mp3", {item["extensao"] for item in snapshot.materials})
        self.assertEqual(snapshot.unresolved, ())
        self.assertEqual(snapshot.unexpected_url_fields, ())

    def test_missing_video_url_and_unknown_url_field_block_completeness(self):
        lesson = CourseLesson("10", 1, 0, "Aula 00", "https://site/aula")
        snapshot = extract_lesson_snapshot(
            {
                "data": {
                    "videos": [
                        {
                            "id": 20,
                            "titulo": "Sem fonte",
                            "resolucoes": {},
                            "arquivo_novo": "https://cdn.test/new.bin",
                        }
                    ]
                }
            },
            lesson,
        )

        self.assertEqual(snapshot.videos, ())
        self.assertTrue(any("nenhum link" in item for item in snapshot.unresolved))
        self.assertEqual(
            snapshot.unexpected_url_fields,
            ("$.data.videos[0].arquivo_novo",),
        )

    def test_lesson_request_uses_id_and_reports_access_denial(self):
        lesson = CourseLesson("3163819", 1, 5, "Aula 05", "https://site/aula")
        session = Mock(spec=requests.Session)
        session.get.return_value = response(403)

        with self.assertRaises(CourseAccessError):
            get_lesson_snapshot(session, lesson)

        self.assertEqual(
            session.get.call_args.args[0],
            LESSON_ENDPOINT.format(lesson_id="3163819"),
        )


if __name__ == "__main__":
    unittest.main()
