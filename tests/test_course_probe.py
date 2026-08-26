import unittest

from tools.probe_course_api import _redact_all_query_values


class CourseProbePrivacyTest(unittest.TestCase):
    def test_preserves_endpoint_without_query(self):
        endpoint = (
            "https://api.estrategiaconcursos.com.br/api/aluno/curso/327532"
        )
        self.assertEqual(_redact_all_query_values(endpoint), endpoint)

    def test_redacts_every_query_value_in_network_report(self):
        url = (
            "https://analytics.example/event?email=pessoa%40example.com&"
            "user_id=123&url=https%3A%2F%2Fexample.com%2Fprivate"
        )
        safe = _redact_all_query_values(url)
        self.assertNotIn("pessoa", safe)
        self.assertNotIn("123", safe)
        self.assertNotIn("private", safe)
        self.assertEqual(safe.count("REMOVIDO"), 3)


if __name__ == "__main__":
    unittest.main()
