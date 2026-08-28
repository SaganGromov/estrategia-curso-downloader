import tempfile
import unittest
from pathlib import Path

from estrategia_downloader.lesson_markers import (
    NO_VIDEOS_MARKER,
    reconcile_no_video_markers,
)


class NoVideoMarkerTest(unittest.TestCase):
    def _record(self, **changes):
        record = {
            "nome": "Aula 00 Introdução",
            "modo": "api",
            "estavel": True,
            "videos_auditados": True,
            "videos": [],
        }
        record.update(changes)
        return record

    def test_creates_human_readable_marker_only_for_confirmed_empty_video_list(self):
        with tempfile.TemporaryDirectory() as temporary:
            course = Path(temporary) / "curso-id-123"
            course.mkdir()

            report = reconcile_no_video_markers(
                course,
                "123",
                {"aula_00_posicao_01": self._record()},
            )

            marker = course / "aula_00" / "videos" / NO_VIDEOS_MARKER
            self.assertTrue(marker.is_file())
            self.assertIn("não representa uma falha", marker.read_text("utf-8"))
            self.assertEqual(len(report["criados"]), 1)

    def test_does_not_mark_reduced_future_or_unstable_lesson(self):
        with tempfile.TemporaryDirectory() as temporary:
            course = Path(temporary) / "curso-id-123"
            course.mkdir()
            lessons = {
                "aula_00_posicao_01": self._record(videos_auditados=False),
                "aula_01_posicao_02": self._record(modo="aguardando_liberacao"),
                "aula_02_posicao_03": self._record(estavel=False),
            }

            report = reconcile_no_video_markers(course, "123", lessons)

            self.assertEqual(report["esperados"], 0)
            self.assertFalse(any(course.rglob(NO_VIDEOS_MARKER)))

    def test_removes_stale_marker_when_a_later_inventory_contains_video(self):
        with tempfile.TemporaryDirectory() as temporary:
            course = Path(temporary) / "curso-id-123"
            course.mkdir()
            key = "aula_00_posicao_01"
            reconcile_no_video_markers(course, "123", {key: self._record()})

            report = reconcile_no_video_markers(
                course,
                "123",
                {key: self._record(videos=[{"identidade": "x"}])},
            )

            self.assertEqual(len(report["removidos"]), 1)
            self.assertFalse(any(course.rglob(NO_VIDEOS_MARKER)))

    def test_reports_conflict_instead_of_labeling_nonempty_video_folder(self):
        with tempfile.TemporaryDirectory() as temporary:
            course = Path(temporary) / "curso-id-123"
            video_folder = course / "aula_00" / "videos"
            video_folder.mkdir(parents=True)
            (video_folder / "video-legado.mp4").write_bytes(b"video")

            report = reconcile_no_video_markers(
                course,
                "123",
                {"aula_00_posicao_01": self._record()},
            )

            self.assertEqual(len(report["conflitos"]), 1)
            self.assertFalse((video_folder / NO_VIDEOS_MARKER).exists())

    def test_legacy_inventory_requires_explicit_full_mode_assumption(self):
        with tempfile.TemporaryDirectory() as temporary:
            course = Path(temporary) / "curso-id-123"
            course.mkdir()
            record = self._record()
            record.pop("videos_auditados")

            dry = reconcile_no_video_markers(
                course,
                "123",
                {"aula_00_posicao_01": record},
                apply=False,
            )
            applied = reconcile_no_video_markers(
                course,
                "123",
                {"aula_00_posicao_01": record},
                assume_legacy_full=True,
            )

            self.assertEqual(dry["esperados"], 0)
            self.assertEqual(applied["esperados"], 1)


if __name__ == "__main__":
    unittest.main()
