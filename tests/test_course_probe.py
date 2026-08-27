import unittest

from tools.probe_course_api import _redact_all_query_values
from tools.probe_course_inventory import _json_shape


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

    def test_inventory_shape_reports_types_without_scalar_values(self):
        payload = {
            "data": {
                "id": 123,
                "private_url": "https://signed.invalid/file?token=secret",
                "videos": [
                    {
                        "id": 456,
                        "titulo": "Título privado",
                        "resolucoes": {"720p": "https://signed.invalid/video"},
                    }
                ],
            }
        }

        report = "\n".join(_json_shape(payload))

        self.assertIn("id:number", report)
        self.assertIn("private_url:string", report)
        self.assertIn("videos:list", report)
        self.assertIn("resolucoes:object", report)
        self.assertIn("720p:string", report)
        self.assertNotIn("secret", report)
        self.assertNotIn("Título privado", report)
        self.assertNotIn("signed.invalid", report)


if __name__ == "__main__":
    unittest.main()
