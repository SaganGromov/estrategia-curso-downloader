import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from estrategia_downloader.duplicate_cleanup import (
    DuplicateCleanupError,
    apply_duplicate_cleanup_plan,
    build_duplicate_cleanup_plan,
)
from estrategia_downloader.integrity import AUDIT_VERSION, INVENTORY_SCHEMA
from estrategia_downloader.local_verification import (
    CERTIFICATE_FILE,
    verify_course_folder,
    write_certificate,
)


class DuplicateCleanupTest(unittest.TestCase):
    def _course(self, root: Path) -> Path:
        course = root / "curso-id-100"
        canonical = course / "aula_00" / "pdfs" / "PDF 01 - Principal.txt"
        duplicate = course / "aula_9999" / "pdfs" / "legado.txt"
        unique = course / "aula_9999" / "pdfs" / "unico.txt"
        canonical.parent.mkdir(parents=True)
        duplicate.parent.mkdir(parents=True)
        canonical.write_bytes(b"conteudo")
        duplicate.write_bytes(b"conteudo")
        unique.write_bytes(b"unico")
        inventory = {
            "schema": INVENTORY_SCHEMA,
            "versao_auditoria": AUDIT_VERSION,
            "curso_id": "100",
            "status": "completo",
            "aulas": {
                "aula_00_posicao_01": {
                    "modo": "api",
                    "passagens": 1,
                    "estavel": True,
                    "arquivos": [
                        {
                            "identidade": "pdf-principal",
                            "tipo": "pdf",
                            "numero": 1,
                            "titulo": "Principal",
                        }
                    ],
                }
            },
        }
        state = {
            "curso_id": "100",
            "status": "concluido",
            "resumo": {
                "versao_auditoria": AUDIT_VERSION,
                "falhas": 0,
                "falhas_descoberta": 0,
                "ocorrencias_pendentes": 0,
                "ocorrencias_confirmadas": 1,
                "recursos_unicos_manifesto": 1,
                "aulas_confirmadas": 1,
            },
        }
        (course / ".inventario_estrategia.json").write_text(
            json.dumps(inventory), encoding="utf-8"
        )
        (course / ".estado_estrategia.json").write_text(
            json.dumps(state), encoding="utf-8"
        )
        report = verify_course_folder(course)
        self.assertTrue(report["ok"])
        write_certificate(course, report)
        return course

    def test_quarantines_only_checksum_duplicate_extras_and_recertifies(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            course = self._course(root)
            quarantine = root / "quarantine"

            plan = build_duplicate_cleanup_plan(
                course, quarantine_root=quarantine
            )
            self.assertEqual(len(plan.items), 1)
            self.assertEqual(plan.total_bytes, len(b"conteudo"))

            report = apply_duplicate_cleanup_plan(plan)

            self.assertTrue(report["ok"])
            self.assertEqual(report["extras_legados"], 1)
            self.assertFalse(
                (course / "aula_9999" / "pdfs" / "legado.txt").exists()
            )
            self.assertTrue(
                (
                    quarantine
                    / course.name
                    / "aula_9999"
                    / "pdfs"
                    / "legado.txt"
                ).is_file()
            )
            self.assertTrue(
                (course / "aula_9999" / "pdfs" / "unico.txt").is_file()
            )
            certificate = json.loads(
                (course / CERTIFICATE_FILE).read_text(encoding="utf-8")
            )
            self.assertEqual(certificate["extras_legados"], 1)

    def test_refuses_changed_duplicate_before_moving_anything(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            course = self._course(root)
            plan = build_duplicate_cleanup_plan(
                course, quarantine_root=root / "quarantine"
            )
            plan.items[0].source.write_bytes(b"alterado")

            with self.assertRaises(DuplicateCleanupError):
                apply_duplicate_cleanup_plan(plan)

            self.assertTrue(plan.items[0].source.is_file())
            self.assertFalse(plan.quarantine_folder.exists())

    def test_rolls_back_every_move_when_recertification_fails(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            course = self._course(root)
            plan = build_duplicate_cleanup_plan(
                course, quarantine_root=root / "quarantine"
            )

            with patch(
                "estrategia_downloader.duplicate_cleanup.verify_course_folder",
                return_value={
                    "ok": False,
                    "problemas": [{"codigo": "missing_resource"}],
                },
            ), self.assertRaises(DuplicateCleanupError):
                apply_duplicate_cleanup_plan(plan)

            self.assertTrue(plan.items[0].source.is_file())
            self.assertFalse(plan.items[0].quarantine.exists())


if __name__ == "__main__":
    unittest.main()
