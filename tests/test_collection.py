import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from estrategia_downloader.collection import (
    COLLECTION_DIRECTORY_NAME,
    COLLECTION_MARKER,
    CollectionError,
    course_folder_name,
    ensure_course_folder,
    open_collection,
    save_collection,
    update_course_status,
)
from estrategia_downloader.course_metadata import CourseSummary


class CollectionTest(unittest.TestCase):
    def test_creates_a_deterministic_collection_inside_the_selected_base(self):
        with TemporaryDirectory() as directory:
            root, state, existed = open_collection(Path(directory))

            self.assertFalse(existed)
            self.assertEqual(root.name, COLLECTION_DIRECTORY_NAME)
            self.assertTrue((root / COLLECTION_MARKER).is_file())
            self.assertEqual(state["cursos"], {})

    def test_detects_when_the_selected_folder_is_already_the_collection(self):
        with TemporaryDirectory() as directory:
            root, _state, _existed = open_collection(Path(directory))
            detected_root, _detected_state, existed = open_collection(root)

            self.assertTrue(existed)
            self.assertEqual(detected_root, root)

    def test_accepts_a_uniquely_named_spillover_collection(self):
        with TemporaryDirectory() as directory:
            selected = Path(directory) / "estrategia-cursos-completos-e"
            root, _state, existed = open_collection(selected)

            self.assertFalse(existed)
            self.assertEqual(root, selected)
            self.assertTrue((root / COLLECTION_MARKER).is_file())

    def test_course_folder_has_title_and_id_but_no_run_timestamp(self):
        course = CourseSummary("327532", "BACEN Área 2 — Macroeconomia")
        self.assertEqual(
            course_folder_name(course),
            "bacen-area-2-macroeconomia-id-327532",
        )

    def test_reuses_and_renames_a_course_when_its_canonical_title_changes(self):
        with TemporaryDirectory() as directory:
            root, state, _existed = open_collection(Path(directory))
            old = CourseSummary("327532", "Macroeconomia antiga")
            old_folder = ensure_course_folder(root, state, old)
            (old_folder / "arquivo.pdf").write_bytes(b"conteudo")
            save_collection(root, state)

            new = CourseSummary("327532", "Macroeconomia atualizada")
            new_folder = ensure_course_folder(root, state, new)

            self.assertFalse(old_folder.exists())
            self.assertEqual(
                new_folder.name,
                "macroeconomia-atualizada-id-327532",
            )
            self.assertEqual((new_folder / "arquivo.pdf").read_bytes(), b"conteudo")

    def test_refuses_ambiguous_course_folders(self):
        with TemporaryDirectory() as directory:
            root, state, _existed = open_collection(Path(directory))
            (root / "primeiro-id-327532").mkdir()
            (root / "segundo-id-327532").mkdir()

            with self.assertRaises(CollectionError):
                ensure_course_folder(
                    root,
                    state,
                    CourseSummary("327532", "Macroeconomia"),
                )

    def test_refuses_a_recorded_folder_outside_the_collection(self):
        with TemporaryDirectory() as directory:
            root, state, _existed = open_collection(Path(directory))
            state["cursos"]["327532"] = {"pasta": ".."}

            with self.assertRaises(CollectionError):
                ensure_course_folder(
                    root,
                    state,
                    CourseSummary("327532", "Macroeconomia"),
                )

    def test_status_is_persisted_without_sensitive_url_values(self):
        with TemporaryDirectory() as directory:
            root, state, _existed = open_collection(Path(directory))
            course = CourseSummary("327532", "Macroeconomia")
            folder = ensure_course_folder(root, state, course)
            update_course_status(
                state,
                course,
                folder,
                "incompleto",
                summary={"falhas": 1},
                error="falha em https://cdn.test/a.pdf?token=segredo",
            )
            save_collection(root, state)

            persisted = json.loads(
                (root / COLLECTION_MARKER).read_text(encoding="utf-8")
            )
            record = persisted["cursos"]["327532"]
            self.assertEqual(record["status"], "incompleto")
            self.assertEqual(record["resumo"], {"falhas": 1})
            self.assertNotIn("segredo", record["erro"])


if __name__ == "__main__":
    unittest.main()
