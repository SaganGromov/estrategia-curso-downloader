"""Consulta direta dos metadados de curso usados pela área do aluno."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from urllib.parse import unquote

import requests

from .config import HTTP_CONNECT_TIMEOUT, HTTP_READ_TIMEOUT

API_BASE_URL = "https://api.estrategiaconcursos.com.br"
COURSE_ENDPOINT = API_BASE_URL + "/api/aluno/curso/{course_id}"
COURSE_LIST_ENDPOINT = API_BASE_URL + "/api/aluno/curso"
REQUEST_TOKEN_URL = "https://www.estrategiaconcursos.com.br/oauth/token/"
SESSION_COOKIE_NAME = "dados_sessao"


class CourseMetadataError(RuntimeError):
    """A API respondeu, mas não forneceu metadados de curso utilizáveis."""


class CourseAccessError(CourseMetadataError):
    """A sessão não está autenticada ou não pode acessar o curso."""


class CourseNotFoundError(CourseMetadataError):
    """O ID informado não corresponde a um curso acessível."""


def _validate_course_id(course_id: str) -> str:
    value = str(course_id)
    if not re.fullmatch(r"\d+", value):
        raise ValueError("course_id deve conter somente dígitos")
    return value


def _json_cookie_value(value: str):
    candidates = (value, unquote(value), value.strip('"'))
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except (TypeError, ValueError):
            continue
        if isinstance(data, Mapping):
            return data
    return None


def _token_from_session_cookie(session: requests.Session):
    for cookie in session.cookies:
        if cookie.name != SESSION_COOKIE_NAME:
            continue
        data = _json_cookie_value(cookie.value)
        if data and data.get("access_token"):
            return data
    return None


def _apply_api_credentials(session: requests.Session, data: Mapping) -> None:
    token_type = data.get("token_type")
    access_token = data.get("access_token")
    if not isinstance(token_type, str) or not isinstance(access_token, str):
        raise CourseAccessError(
            "a sessão web não forneceu uma credencial válida para a API"
        )
    if not token_type or not access_token:
        raise CourseAccessError(
            "a sessão web não forneceu uma credencial válida para a API"
        )

    session.headers["Authorization"] = f"{token_type} {access_token}"


def authenticate_api_session(
    session: requests.Session,
    *,
    timeout=(HTTP_CONNECT_TIMEOUT, HTTP_READ_TIMEOUT),
) -> requests.Session:
    """Obtém o bearer da mesma forma que a SPA, sem revelar seu valor.

    ``session`` deve conter os cookies copiados do Edge autenticado. A SPA usa
    primeiro o cookie ``dados_sessao`` quando ele existe; caso contrário, pede
    uma credencial temporária ao endpoint web ``/oauth/token/``.
    """

    data = _token_from_session_cookie(session)
    if data is None:
        try:
            response = session.get(REQUEST_TOKEN_URL, timeout=timeout)
        except requests.RequestException as error:
            raise CourseAccessError(
                "não foi possível obter a credencial temporária da API"
            ) from error
        if response.status_code in {401, 403}:
            raise CourseAccessError(
                "os cookies da sessão web não foram aceitos pelo endpoint de token"
            )
        try:
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError) as error:
            raise CourseAccessError(
                "o endpoint de token não devolveu uma credencial válida"
            ) from error

    if not isinstance(data, Mapping):
        raise CourseAccessError(
            "o endpoint de token não devolveu uma credencial válida"
        )
    _apply_api_credentials(session, data)
    return session


def create_course_api_session(
    web_session: requests.Session,
    *,
    timeout=(HTTP_CONNECT_TIMEOUT, HTTP_READ_TIMEOUT),
) -> requests.Session:
    """Cria uma sessão mínima, com somente o bearer exigido pelo curso."""

    bootstrap = requests.Session()
    for cookie in web_session.cookies:
        bootstrap.cookies.set_cookie(cookie)
    try:
        authenticate_api_session(bootstrap, timeout=timeout)
        authorization = bootstrap.headers["Authorization"]
    finally:
        bootstrap.close()

    api_session = requests.Session()
    api_session.headers.clear()
    api_session.headers["Authorization"] = authorization
    return api_session


def extract_course_name(payload, course_id: str) -> str:
    """Extrai ``data.nome`` sem normalizar o título fornecido pela API."""

    course_id = _validate_course_id(course_id)
    if not isinstance(payload, Mapping):
        raise CourseMetadataError("a resposta da API não é um objeto JSON")
    course = payload.get("data")
    if not isinstance(course, Mapping):
        raise CourseMetadataError("a resposta da API não contém data")

    returned_id = course.get("id")
    if returned_id is not None and str(returned_id) != course_id:
        raise CourseMetadataError(
            "a API devolveu metadados de um ID diferente do solicitado"
        )

    name = course.get("nome")
    if not isinstance(name, str) or not name.strip():
        raise CourseMetadataError("a resposta da API não contém data.nome")
    return name


def get_course_name(
    session: requests.Session,
    course_id: str,
    *,
    timeout=(HTTP_CONNECT_TIMEOUT, HTTP_READ_TIMEOUT),
) -> str:
    """Mapeia o ID da URL do dashboard para o título canônico do curso."""

    course_id = _validate_course_id(course_id)
    response = session.get(
        COURSE_ENDPOINT.format(course_id=course_id),
        timeout=timeout,
    )
    if response.status_code in {401, 403}:
        raise CourseAccessError(
            f"a API recusou o acesso ao curso {course_id} "
            f"(HTTP {response.status_code})"
        )
    if response.status_code == 404:
        raise CourseNotFoundError(f"curso {course_id} não encontrado (HTTP 404)")
    try:
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as error:
        raise CourseMetadataError(
            f"a API falhou ao consultar o curso {course_id} "
            f"(HTTP {response.status_code})"
        ) from error
    except ValueError as error:
        raise CourseMetadataError("a API não devolveu JSON válido") from error
    return extract_course_name(payload, course_id)


obter_nome_curso = get_course_name
