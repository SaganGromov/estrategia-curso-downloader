import io
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from estrategia_downloader.course_metadata import CourseSummary
from tools import download_full_catalog
from tools.download_full_catalog import _course_selector


class FullCatalogCliTest(unittest.TestCase):
    def test_includes_bacen_and_banco_central_but_not_tcdf(self):
        selector = _course_selector(["BACEN|Banco Central"], [])

        self.assertTrue(selector(CourseSummary("1", "BACEN - Finanças")))
        self.assertTrue(
            selector(CourseSummary("2", "Mentoria Projeto Banco Central"))
        )
        self.assertFalse(selector(CourseSummary("3", "TCDF - Finanças")))

    def test_exclusion_selects_only_the_remaining_catalog(self):
        selector = _course_selector([], ["BACEN|Banco Central|TCDF"])

        self.assertFalse(selector(CourseSummary("1", "BACEN - Finanças")))
        self.assertFalse(selector(CourseSummary("2", "TCDF - Direito")))
        self.assertTrue(selector(CourseSummary("3", "Estratégia Cast")))

    def test_main_removes_credentials_from_process_environment(self):
        driver = Mock()
        args = SimpleNamespace(
            destination=Path("/tmp/colecao"),
            spillover=[],
            include_regex=[],
            exclude_regex=[],
            submit_login=True,
        )
        with (
            patch.dict(
                "os.environ",
                {
                    "ESTRATEGIA_EMAIL": "pessoa@example.test",
                    "ESTRATEGIA_PASSWORD": "segredo",
                },
            ),
            patch("sys.stdout", new_callable=io.StringIO),
            patch.object(download_full_catalog, "parse_args", return_value=args),
            patch.object(download_full_catalog, "verificar_destino"),
            patch.object(
                download_full_catalog,
                "create_edge_driver",
                return_value=driver,
            ),
            patch.object(
                download_full_catalog,
                "RecuperadorAlertas",
                return_value=Mock(),
            ),
            patch.object(download_full_catalog, "do_login"),
            patch.object(
                download_full_catalog,
                "executar_colecao_integral",
                return_value={},
            ),
        ):
            self.assertEqual(download_full_catalog.main(), 0)
            self.assertNotIn("ESTRATEGIA_EMAIL", os.environ)
            self.assertNotIn("ESTRATEGIA_PASSWORD", os.environ)

        driver.quit.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
