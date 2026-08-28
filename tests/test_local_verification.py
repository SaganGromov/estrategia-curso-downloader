import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from estrategia_downloader.integrity import AUDIT_VERSION, INVENTORY_SCHEMA
from estrategia_downloader.local_verification import (
    CERTIFICATE_FILE,
    _structure_command,
    verify_course_folder,
    write_certificate,
)
from estrategia_downloader.lesson_markers import (
    NO_VIDEOS_MARKER,
    reconcile_no_video_markers,
)


class LocalVerificationTest(unittest.TestCase):
    def _course(self, root: Path) -> Path:
        course = root / "curso-id-123"
        (course / "aula_00" / "pdfs").mkdir(parents=True)
        (course / "aula_00" / "videos").mkdir(parents=True)
        (course / "aula_00" / "pdfs" / "PDF 01 - Apostila.pdf").write_bytes(b"pdf")
        (course / "aula_00" / "videos" / "Vídeo 02 - Parte 1.mp4").write_bytes(
            b"video"
        )
        records = [
            {
                "identidade": "pdf-id",
                "tipo": "pdf",
                "numero": 1,
                "titulo": "Apostila",
            },
            {
                "identidade": "video-id",
                "tipo": "video",
                "numero": 2,
                "titulo": "Parte 1",
            },
        ]
        inventory = {
            "schema": INVENTORY_SCHEMA,
            "versao_auditoria": AUDIT_VERSION,
            "curso_id": "123",
            "status": "completo",
            "atualizado_em": "2026-08-27T00:00:00+00:00",
            "aulas": {
                "aula_00_posicao_01": {
                    "nome": "Aula 00",
                    "passagens": 1,
                    "estavel": True,
                    "modo": "api",
                    "arquivos": records,
                }
            },
        }
        state = {
            "curso_id": "123",
            "status": "concluido",
            "resumo": {
                "falhas": 0,
                "falhas_descoberta": 0,
                "ocorrencias_confirmadas": 2,
                "ocorrencias_pendentes": 0,
                "versao_auditoria": AUDIT_VERSION,
                "aulas_confirmadas": 1,
                "recursos_unicos_manifesto": 2,
            },
        }
        (course / ".inventario_estrategia.json").write_text(
            json.dumps(inventory), encoding="utf-8"
        )
        (course / ".estado_estrategia.json").write_text(
            json.dumps(state), encoding="utf-8"
        )
        return course

    def test_certifies_every_manifest_occurrence_without_network(self):
        with tempfile.TemporaryDirectory() as temporary:
            course = self._course(Path(temporary))

            report = verify_course_folder(course, verify_structure=False)
            certificate = write_certificate(course, report)

            self.assertTrue(report["ok"])
            self.assertEqual(report["ocorrencias_manifesto"], 2)
            self.assertEqual(report["ocorrencias_localizadas"], 2)
            self.assertEqual(report["extras_legados"], 0)
            self.assertTrue(all(item["sha256"] for item in report["arquivos"]))
            self.assertEqual(certificate.name, CERTIFICATE_FILE)

    def test_accepts_marker_derived_from_legacy_full_api_inventory(self):
        with tempfile.TemporaryDirectory() as temporary:
            course = self._course(Path(temporary))
            inventory_path = course / ".inventario_estrategia.json"
            inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
            lesson = inventory["aulas"]["aula_00_posicao_01"]
            lesson["videos"] = []
            lesson["arquivos"] = [lesson["arquivos"][0]]
            inventory_path.write_text(json.dumps(inventory), encoding="utf-8")

            state_path = course / ".estado_estrategia.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["resumo"]["ocorrencias_confirmadas"] = 1
            state["resumo"]["recursos_unicos_manifesto"] = 1
            state_path.write_text(json.dumps(state), encoding="utf-8")
            (course / "aula_00" / "videos" / "Vídeo 02 - Parte 1.mp4").unlink()

            marker_report = reconcile_no_video_markers(
                course,
                "123",
                inventory["aulas"],
                assume_legacy_full=True,
            )
            self.assertEqual(marker_report["criados"], [
                f"aula_00/videos/{NO_VIDEOS_MARKER}"
            ])

            report = verify_course_folder(
                course,
                calculate_hashes=False,
                verify_structure=False,
            )

            self.assertTrue(report["ok"])

    def test_rejects_missing_resource_even_when_an_extra_file_exists(self):
        with tempfile.TemporaryDirectory() as temporary:
            course = self._course(Path(temporary))
            (course / "aula_00" / "videos" / "Vídeo 02 - Parte 1.mp4").unlink()
            (course / "aula_00" / "videos" / "arquivo-extra.mp4").write_bytes(b"x")

            report = verify_course_folder(
                course, calculate_hashes=False, verify_structure=False
            )

            self.assertFalse(report["ok"])
            self.assertIn(
                "missing_resource",
                {item["codigo"] for item in report["problemas"]},
            )
            self.assertEqual(report["extras_legados"], 1)

    def test_rejects_transient_file_and_does_not_write_certificate(self):
        with tempfile.TemporaryDirectory() as temporary:
            course = self._course(Path(temporary))
            (course / "aula_00" / "videos" / "incompleto.mp4.part").write_bytes(b"x")

            report = verify_course_folder(
                course, calculate_hashes=False, verify_structure=False
            )

            self.assertFalse(report["ok"])
            self.assertIn(
                "transient_file",
                {item["codigo"] for item in report["problemas"]},
            )
            with self.assertRaises(ValueError):
                write_certificate(course, report)

    def test_quick_check_without_hashes_cannot_be_certified(self):
        with tempfile.TemporaryDirectory() as temporary:
            course = self._course(Path(temporary))

            report = verify_course_folder(
                course, calculate_hashes=False, verify_structure=False
            )

            self.assertTrue(report["ok"])
            with self.assertRaises(ValueError):
                write_certificate(course, report)

    def test_images_are_fully_decoded_with_ffmpeg(self):
        image = Path("/tmp/imagem.jpg")

        self.assertEqual(
            _structure_command(image),
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(image),
                "-f",
                "null",
                "-",
            ],
        )

    def test_validation_issue_skips_wasted_hash_pass(self):
        with tempfile.TemporaryDirectory() as temporary:
            course = self._course(Path(temporary))

            with (
                patch(
                    "estrategia_downloader.local_verification._verify_structure",
                    return_value="estrutura inválida",
                ),
                patch(
                    "estrategia_downloader.local_verification._sha256"
                ) as calculate_hash,
            ):
                report = verify_course_folder(course)

            self.assertFalse(report["ok"])
            self.assertFalse(report["hashes_calculados"])
            calculate_hash.assert_not_called()

    def test_certifies_released_content_while_future_lesson_is_scheduled(self):
        with tempfile.TemporaryDirectory() as temporary:
            course = self._course(Path(temporary))
            inventory_path = course / ".inventario_estrategia.json"
            state_path = course / ".estado_estrategia.json"
            inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
            state = json.loads(state_path.read_text(encoding="utf-8"))
            release = (date.today() + timedelta(days=30)).isoformat()
            inventory["status"] = "aguardando_liberacao"
            inventory["metadados"] = {
                "liberacoes_futuras": [release],
                "proxima_liberacao": release,
            }
            inventory["aulas"]["aula_01_posicao_02"] = {
                "nome": "Aula 01",
                "passagens": 0,
                "estavel": True,
                "modo": "aguardando_liberacao",
                "liberacao": release,
                "materiais": [],
                "videos": [],
                "arquivos": [],
            }
            state["status"] = "aguardando_liberacao"
            state["resumo"]["aulas_aguardando_liberacao"] = 1
            inventory_path.write_text(json.dumps(inventory), encoding="utf-8")
            state_path.write_text(json.dumps(state), encoding="utf-8")

            report = verify_course_folder(course, verify_structure=False)

            self.assertTrue(report["ok"])
            self.assertEqual(report["status_inventario"], "aguardando_liberacao")
            self.assertEqual(report["aulas_confirmadas"], 1)
            self.assertEqual(report["aulas_aguardando_liberacao"], 1)
            self.assertEqual(report["liberacoes_futuras"], [release])
            self.assertEqual(report["ocorrencias_manifesto"], 2)

    def test_rejects_scheduled_inventory_once_release_is_due(self):
        with tempfile.TemporaryDirectory() as temporary:
            course = self._course(Path(temporary))
            inventory_path = course / ".inventario_estrategia.json"
            state_path = course / ".estado_estrategia.json"
            inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
            state = json.loads(state_path.read_text(encoding="utf-8"))
            release = (date.today() - timedelta(days=1)).isoformat()
            inventory["status"] = "aguardando_liberacao"
            inventory["metadados"] = {
                "liberacoes_futuras": [release],
                "proxima_liberacao": release,
            }
            inventory["aulas"]["aula_01_posicao_02"] = {
                "nome": "Aula 01",
                "passagens": 0,
                "estavel": True,
                "modo": "aguardando_liberacao",
                "liberacao": release,
                "materiais": [],
                "videos": [],
                "arquivos": [],
            }
            state["status"] = "aguardando_liberacao"
            state["resumo"]["aulas_aguardando_liberacao"] = 1
            inventory_path.write_text(json.dumps(inventory), encoding="utf-8")
            state_path.write_text(json.dumps(state), encoding="utf-8")

            report = verify_course_folder(course, verify_structure=False)

            self.assertFalse(report["ok"])
            self.assertIn(
                "scheduled_release",
                {item["codigo"] for item in report["problemas"]},
            )


if __name__ == "__main__":
    unittest.main()
