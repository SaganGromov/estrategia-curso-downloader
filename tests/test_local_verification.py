import json
import tempfile
import unittest
from pathlib import Path

from estrategia_downloader.integrity import AUDIT_VERSION, INVENTORY_SCHEMA
from estrategia_downloader.local_verification import (
    CERTIFICATE_FILE,
    verify_course_folder,
    write_certificate,
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


if __name__ == "__main__":
    unittest.main()
