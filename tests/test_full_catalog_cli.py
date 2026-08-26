import unittest

from estrategia_downloader.course_metadata import CourseSummary
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


if __name__ == "__main__":
    unittest.main()
