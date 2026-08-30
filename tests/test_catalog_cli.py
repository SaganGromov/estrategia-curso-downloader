import io
import os
import unittest
from unittest.mock import Mock, patch

from estrategia_downloader.course_metadata import CourseSummary
from tools import list_course_catalog


class CatalogCliTest(unittest.TestCase):
    def test_stdout_contains_only_id_and_exact_title(self):
        driver = Mock()
        web_session = Mock()
        api_session = Mock()
        courses = [
            CourseSummary("20", "Curso Dois"),
            CourseSummary("10", "Curso Um — completo"),
        ]
        with (
            patch("sys.argv", ["list_course_catalog.py", "--submit-login"]),
            patch.dict(
                "os.environ",
                {
                    "ESTRATEGIA_EMAIL": "pessoa@example.test",
                    "ESTRATEGIA_PASSWORD": "segredo",
                },
            ),
            patch("sys.stdout", new_callable=io.StringIO) as stdout,
            patch("sys.stderr", new_callable=io.StringIO),
            patch.object(
                list_course_catalog,
                "create_edge_driver",
                return_value=driver,
            ),
            patch.object(list_course_catalog, "do_login"),
            patch.object(
                list_course_catalog,
                "criar_sessao_download",
                return_value=web_session,
            ),
            patch.object(
                list_course_catalog,
                "create_course_api_session",
                return_value=api_session,
            ),
            patch.object(
                list_course_catalog,
                "list_accessible_courses",
                return_value=courses,
            ),
        ):
            self.assertEqual(list_course_catalog.main(), 0)
            self.assertNotIn("ESTRATEGIA_EMAIL", os.environ)
            self.assertNotIn("ESTRATEGIA_PASSWORD", os.environ)

        self.assertEqual(
            stdout.getvalue(),
            "20\tCurso Dois\n10\tCurso Um — completo\n",
        )
        driver.quit.assert_called_once_with()
        web_session.close.assert_called_once_with()
        api_session.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
