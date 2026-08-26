import json
import unittest
from unittest.mock import Mock

import requests

from estrategia_downloader.course_metadata import (
    COURSE_ENDPOINT,
    CourseAccessError,
    CourseMetadataError,
    CourseNotFoundError,
    authenticate_api_session,
    create_course_api_session,
    extract_course_name,
    get_course_name,
)

COURSE_ID = "327532"
EXPECTED_NAME = (
    "BACEN (Analista - Área 2 - Economia e Finanças) Macroeconomia "
    "(Parte do Conhecimentos Específicos)"
)


def response(status=200, payload=None):
    result = Mock(spec=requests.Response)
    result.status_code = status
    result.ok = 200 <= status < 400
    result.json.return_value = payload
    if status >= 400:
        result.raise_for_status.side_effect = requests.HTTPError(f"HTTP {status}")
    else:
        result.raise_for_status.return_value = None
    return result


class CourseMetadataTest(unittest.TestCase):
    def test_extracts_exact_data_nome_without_normalizing(self):
        payload = {"data": {"id": 327532, "nome": EXPECTED_NAME}}
        self.assertEqual(extract_course_name(payload, COURSE_ID), EXPECTED_NAME)

    def test_rejects_a_response_for_a_different_course(self):
        payload = {"data": {"id": 111111, "nome": "Outro curso"}}
        with self.assertRaises(CourseMetadataError):
            extract_course_name(payload, COURSE_ID)

    def test_parameterizes_the_dashboard_course_id(self):
        session = Mock(spec=requests.Session)
        session.get.return_value = response(
            payload={"data": {"id": 123456, "nome": "Curso diferente"}}
        )

        self.assertEqual(get_course_name(session, "123456"), "Curso diferente")
        self.assertEqual(
            session.get.call_args.args[0],
            COURSE_ENDPOINT.format(course_id="123456"),
        )
        self.assertNotIn("headers", session.get.call_args.kwargs)

    def test_documents_unauthorized_and_missing_behaviour(self):
        session = Mock(spec=requests.Session)
        session.get.side_effect = [response(401), response(404)]
        with self.assertRaises(CourseAccessError):
            get_course_name(session, COURSE_ID)
        with self.assertRaises(CourseNotFoundError):
            get_course_name(session, COURSE_ID)

    def test_authenticates_from_dados_sessao_without_network_call(self):
        session = requests.Session()
        session.cookies.set(
            "dados_sessao",
            json.dumps(
                {
                    "token_type": "Bearer",
                    "access_token": "test-secret-token",
                    "session_id": "test-session",
                    "personificado": False,
                }
            ),
        )
        session.get = Mock()

        authenticate_api_session(session)

        session.get.assert_not_called()
        self.assertEqual(
            session.headers["Authorization"], "Bearer test-secret-token"
        )
        self.assertNotIn("Session", session.headers)
        self.assertNotIn("Personificado", session.headers)

    def test_authenticates_through_web_token_endpoint(self):
        session = requests.Session()
        session.get = Mock(
            return_value=response(
                payload={
                    "token_type": "Bearer",
                    "access_token": "temporary-token",
                    "session_id": "session-id",
                    "personificado": False,
                }
            )
        )

        authenticate_api_session(session)

        self.assertEqual(session.headers["Authorization"], "Bearer temporary-token")
        self.assertEqual(session.get.call_count, 1)

    def test_builds_a_minimal_bearer_only_api_session(self):
        web_session = requests.Session()
        web_session.headers["User-Agent"] = "Browser UA"
        web_session.headers["Referer"] = "https://example.test/course"
        web_session.cookies.set(
            "dados_sessao",
            json.dumps(
                {
                    "token_type": "Bearer",
                    "access_token": "minimal-token",
                }
            ),
        )

        api_session = create_course_api_session(web_session)

        self.assertEqual(
            dict(api_session.headers),
            {"Authorization": "Bearer minimal-token"},
        )
        self.assertEqual(len(api_session.cookies), 0)

    def test_token_errors_do_not_echo_response_or_credentials(self):
        session = requests.Session()
        session.get = Mock(return_value=response(401, {"token": "secret"}))

        with self.assertRaises(CourseAccessError) as caught:
            authenticate_api_session(session)

        self.assertNotIn("secret", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
