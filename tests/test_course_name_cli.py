import io
import unittest
from unittest.mock import Mock, patch

import course_name

EXPECTED_NAME = (
    "BACEN (Analista - Área 2 - Economia e Finanças) Macroeconomia "
    "(Parte do Conhecimentos Específicos)"
)


class CourseNameCliTest(unittest.TestCase):
    def test_stdout_contains_only_the_exact_api_title(self):
        driver = Mock()
        with (
            patch("sys.argv", ["course_name.py", "327532"]),
            patch("sys.stdout", new_callable=io.StringIO) as stdout,
            patch("sys.stderr", new_callable=io.StringIO),
            patch.object(course_name, "create_edge_driver", return_value=driver),
            patch.object(course_name, "do_login"),
            patch.object(course_name, "criar_sessao_download", return_value=Mock()),
            patch.object(course_name, "create_course_api_session", return_value=Mock()),
            patch.object(course_name, "get_course_name", return_value=EXPECTED_NAME),
        ):
            self.assertEqual(course_name.main(), 0)

        self.assertEqual(stdout.getvalue(), EXPECTED_NAME + "\n")
        driver.quit.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
