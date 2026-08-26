import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tools.normalize_material_filenames import (
    FilenameNormalizationError,
    apply_plan,
    build_plan,
    normalized_name,
)


class FilenameNormalizationTest(unittest.TestCase):
    def test_removes_only_transient_suffixes(self):
        self.assertEqual(
            normalized_name("PDF original Baixado LessonButton.pdf"),
            "PDF original.pdf",
        )
        self.assertEqual(normalized_name("Conteúdo Baixado hoje.pdf"), "Conteúdo Baixado hoje.pdf")

    def test_renames_and_consolidates_only_hash_identical_duplicates(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Slides LessonButton.pdf").write_bytes(b"slides")
            (root / "PDF.pdf").write_bytes(b"igual")
            (root / "PDF Baixado LessonButton.pdf").write_bytes(b"igual")

            plan = build_plan([root])
            renamed, duplicates, duplicate_bytes = apply_plan(plan)

            self.assertEqual((renamed, duplicates, duplicate_bytes), (1, 1, 5))
            self.assertEqual((root / "Slides.pdf").read_bytes(), b"slides")
            self.assertEqual((root / "PDF.pdf").read_bytes(), b"igual")
            self.assertFalse((root / "PDF Baixado LessonButton.pdf").exists())

    def test_two_legacy_variants_can_share_a_new_destination(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "PDF LessonButton.pdf").write_bytes(b"igual")
            (root / "PDF Baixado LessonButton.pdf").write_bytes(b"igual")

            plan = build_plan([root])
            renamed, duplicates, _bytes = apply_plan(plan)

            self.assertEqual((renamed, duplicates), (1, 1))
            self.assertEqual((root / "PDF.pdf").read_bytes(), b"igual")

    def test_refuses_a_same_name_with_different_content(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "PDF.pdf").write_bytes(b"primeiro")
            (root / "PDF LessonButton.pdf").write_bytes(b"segundo-")

            with self.assertRaises(FilenameNormalizationError):
                build_plan([root])

            self.assertEqual((root / "PDF.pdf").read_bytes(), b"primeiro")


if __name__ == "__main__":
    unittest.main()
