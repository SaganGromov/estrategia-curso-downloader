#!/usr/bin/env python3
"""Captura e reproduz, com segurança, a API de metadados de cursos.

Este é um utilitário de investigação. Ele não grava logs de rede, cookies,
tokens, corpos de resposta ou cabeçalhos de autenticação em disco.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests
from selenium.common.exceptions import WebDriverException

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from estrategia_downloader.app import do_login, montar_curso_url  # noqa: E402
from estrategia_downloader.alerts import RecuperadorAlertas  # noqa: E402
from estrategia_downloader.browser import create_edge_driver  # noqa: E402
from estrategia_downloader.course_metadata import (  # noqa: E402
    COURSE_ENDPOINT,
    COURSE_LIST_ENDPOINT,
    REQUEST_TOKEN_URL,
    CourseMetadataError,
    authenticate_api_session,
    extract_course_name,
    get_course_name,
)
from estrategia_downloader.downloads import criar_sessao_download  # noqa: E402
from estrategia_downloader.utils import sanitizar_texto  # noqa: E402

CAPTURE_HEADER_NAMES = (
    "Authorization",
    "Cookie",
    "Origin",
    "Referer",
    "Session",
    "Personificado",
    "User-Agent",
    "X-CSRF-Token",
    "X-XSRF-TOKEN",
)
AUTH_HEADER_NAMES = (
    "Authorization",
    "Session",
    "Personificado",
    "X-CSRF-Token",
    "X-XSRF-TOKEN",
)
ID_KEYS = {
    "id",
    "courseid",
    "course_id",
    "cursoid",
    "curso_id",
    "idcurso",
    "id_curso",
}
TITLE_KEYS = {
    "name",
    "nome",
    "title",
    "titulo",
    "coursename",
    "course_name",
    "nomecurso",
    "nome_curso",
}


@dataclass
class TrafficRecord:
    request_id: str
    method: str = ""
    url: str = ""
    request_headers: dict = field(default_factory=dict)
    has_request_body: bool = False
    resource_type: str = ""
    status: int | None = None
    mime_type: str = ""
    response_headers: dict = field(default_factory=dict)
    json_body: object | None = None
    title_hits: list[tuple[str, str]] = field(default_factory=list)
    id_hits: int = 0
    score: int = 0


def _case_insensitive_header(headers: dict, name: str):
    wanted = name.casefold()
    return next(
        (value for key, value in headers.items() if key.casefold() == wanted),
        None,
    )


def _walk_json(value, path="$", seen=None):
    if seen is None:
        seen = set()
    if isinstance(value, (dict, list)):
        marker = id(value)
        if marker in seen:
            return
        seen.add(marker)
    yield path, value
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _walk_json(child, f"{path}.{key}", seen)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_json(child, f"{path}[{index}]", seen)


def _matching_titles(payload, course_id: str) -> list[tuple[str, str]]:
    hits = []
    for path, value in _walk_json(payload):
        if not isinstance(value, dict):
            continue
        normalized = {str(key).casefold(): child for key, child in value.items()}
        identifiers = [normalized[key] for key in ID_KEYS if key in normalized]
        if not any(str(identifier) == course_id for identifier in identifiers):
            continue
        for key in TITLE_KEYS:
            title = normalized.get(key)
            if isinstance(title, str) and title.strip():
                hits.append((f"{path}.{key}", title))
    return list(dict.fromkeys(hits))


def _count_id_hits(payload, course_id: str) -> int:
    return sum(
        1
        for _path, value in _walk_json(payload)
        if isinstance(value, (str, int)) and str(value) == course_id
    )


def _decode_performance_entry(entry):
    try:
        outer = json.loads(entry["message"])
        return outer["message"]["method"], outer["message"].get("params", {})
    except (KeyError, TypeError, ValueError):
        return "", {}


def _read_response_body(driver, request_id: str):
    try:
        result = driver.execute_cdp_cmd(
            "Network.getResponseBody", {"requestId": request_id}
        )
        body = result.get("body", "")
        if result.get("base64Encoded"):
            body = base64.b64decode(body).decode("utf-8", errors="replace")
        return json.loads(body)
    except (ValueError, TypeError, KeyError, WebDriverException):
        return None


def _process_performance_entries(driver, entries, records, extra_headers):
    for entry in entries:
        method, params = _decode_performance_entry(entry)
        request_id = params.get("requestId")
        if not request_id:
            continue

        if method == "Network.requestWillBeSentExtraInfo":
            extra_headers[request_id] = params.get("headers") or {}
            if request_id in records:
                records[request_id].request_headers.update(extra_headers[request_id])
            continue

        if method == "Network.requestWillBeSent":
            request = params.get("request") or {}
            record = records.setdefault(request_id, TrafficRecord(request_id))
            record.method = request.get("method") or ""
            record.url = request.get("url") or ""
            record.request_headers.update(request.get("headers") or {})
            record.request_headers.update(extra_headers.get(request_id, {}))
            record.has_request_body = bool(request.get("hasPostData"))
            record.resource_type = params.get("type") or record.resource_type
            continue

        if method == "Network.responseReceived":
            response = params.get("response") or {}
            record = records.setdefault(request_id, TrafficRecord(request_id))
            record.status = int(response.get("status", 0))
            record.mime_type = response.get("mimeType") or ""
            record.response_headers = response.get("headers") or {}
            record.resource_type = params.get("type") or record.resource_type
            continue

        if method == "Network.loadingFinished" and request_id in records:
            record = records[request_id]
            if record.resource_type in {"Fetch", "XHR"}:
                record.json_body = _read_response_body(driver, request_id)


def capture_course_traffic(driver, course_url: str, seconds: float, alertas=None):
    driver.execute_cdp_cmd(
        "Network.enable",
        {
            "maxTotalBufferSize": 100_000_000,
            "maxResourceBufferSize": 20_000_000,
            "maxPostDataSize": 1_000_000,
        },
    )
    driver.get_log("performance")
    if alertas is None:
        driver.get(course_url)
    else:
        alertas.navegar(course_url, descricao="abrir o curso para captura de rede")

    records = {}
    extra_headers = {}
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if alertas is not None:
            alertas.resolver_pendente(permitir_desconhecido=True)
        entries = driver.get_log("performance")
        _process_performance_entries(driver, entries, records, extra_headers)
        time.sleep(0.25)

    entries = driver.get_log("performance")
    _process_performance_entries(driver, entries, records, extra_headers)
    for request_id, record in records.items():
        if (
            record.resource_type in {"Fetch", "XHR"}
            and record.json_body is None
            and record.status is not None
        ):
            record.json_body = _read_response_body(driver, request_id)
    return list(records.values())


def rank_records(records, course_id: str):
    ranked = []
    for record in records:
        if record.resource_type not in {"Fetch", "XHR"}:
            continue
        score = 20
        if record.json_body is not None:
            score += 30
            record.title_hits = _matching_titles(record.json_body, course_id)
            record.id_hits = _count_id_hits(record.json_body, course_id)
            score += min(record.id_hits, 5) * 8
            score += len(record.title_hits) * 100
        split = urlsplit(record.url)
        if split.hostname == "api.estrategiaconcursos.com.br":
            score += 25
        if course_id in record.url:
            score += 40
        if "/api/aluno/curso" in split.path:
            score += 35
        if "json" in record.mime_type.casefold():
            score += 15
        record.score = score
        ranked.append(record)
    return sorted(ranked, key=lambda item: item.score, reverse=True)


def _header_presence(headers: dict) -> str:
    def state(name):
        present = _case_insensitive_header(headers, name) is not None
        return "PRESENT" if present else "ABSENT"

    return ", ".join(
        f"{name}={state(name)}"
        for name in CAPTURE_HEADER_NAMES
    )


def _redact_all_query_values(url: str) -> str:
    """Preserva a anatomia do endpoint sem imprimir PII ou valores opacos."""

    try:
        parts = urlsplit(url)
        query = urlencode(
            [
                (name, "REMOVIDO")
                for name, _value in parse_qsl(parts.query, keep_blank_values=True)
            ]
        )
        return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))
    except (TypeError, ValueError):
        return "[URL inválida]"


def report_candidates(ranked):
    print("\n=== Captura Fetch/XHR classificada ===")
    relevant = [
        record
        for record in ranked
        if record.title_hits
        or record.id_hits
        or "/api/aluno/curso" in urlsplit(record.url).path
    ]
    if not relevant:
        print("Nenhuma resposta Fetch/XHR foi capturada.")
        return
    print(f"Fetch/XHR captured: {len(ranked)}")
    print(f"Course-metadata candidates reported: {len(relevant)}")
    for index, record in enumerate(relevant[:10], 1):
        content_type = _case_insensitive_header(
            record.response_headers, "Content-Type"
        )
        print(f"\nCandidato {index} (score {record.score})")
        print(f"Method: {record.method or 'UNKNOWN'}")
        print(f"Endpoint: {_redact_all_query_values(record.url)}")
        print(f"Resource type: {record.resource_type or 'UNKNOWN'}")
        print(f"Status: {record.status if record.status is not None else 'UNKNOWN'}")
        print(f"Response type: {content_type or record.mime_type or 'UNKNOWN'}")
        print(f"Request body: {'PRESENT' if record.has_request_body else 'ABSENT'}")
        print(f"Headers: {_header_presence(record.request_headers)}")
        print(f"Course-ID JSON matches: {record.id_hits}")
        for path, title in record.title_hits[:5]:
            print(f"Title match: {path} = {title}")


def _copy_cookies(source: requests.Session) -> requests.Session:
    target = requests.Session()
    for cookie in source.cookies:
        target.cookies.set_cookie(cookie)
    return target


def _safe_get_result(
    session,
    url: str,
    course_id: str,
    *,
    accept_json: bool = True,
):
    try:
        headers = {"Accept": "application/json"} if accept_json else {}
        response = session.get(
            url,
            headers=headers,
            timeout=(15, 45),
            allow_redirects=False,
        )
    except requests.RequestException as error:
        return {"error": type(error).__name__}
    result = {
        "status": response.status_code,
        "type": (response.headers.get("Content-Type") or "").split(";", 1)[0],
    }
    if response.ok:
        try:
            result["title"] = extract_course_name(response.json(), course_id)
        except (ValueError, CourseMetadataError):
            pass
    return result


def _print_replay(label: str, result: dict):
    if "error" in result:
        print(f"{label}: ERROR {result['error']}")
        return
    suffix = f", title={result['title']}" if "title" in result else ""
    print(
        f"{label}: HTTP {result['status']} {result.get('type') or 'unknown'}{suffix}"
    )


def _selected_headers(source: dict, names) -> dict:
    selected = {}
    for name in names:
        value = _case_insensitive_header(source, name)
        if value is not None:
            selected[name] = value
    return selected


def _auth_variants(api_session: requests.Session, ranked):
    auth = _selected_headers(api_session.headers, AUTH_HEADER_NAMES)
    captured = next(
        (
            _selected_headers(record.request_headers, AUTH_HEADER_NAMES)
            for record in ranked
            if "/api/aluno/curso" in urlsplit(record.url).path
        ),
        {},
    )
    for name in ("Session", "Personificado"):
        if name in captured:
            auth[name] = captured[name]
    authorization = {
        key: value for key, value in auth.items() if key == "Authorization"
    }
    session_header = {key: value for key, value in auth.items() if key == "Session"}
    personificado = {
        key: value for key, value in auth.items() if key == "Personificado"
    }
    return (
        ("Bearer only", authorization),
        ("Bearer + Session", {**authorization, **session_header}),
        ("Bearer + Personificado", {**authorization, **personificado}),
        ("Bearer + Session + Personificado", auth),
    )


def _authenticate_and_record_source(session: requests.Session) -> str:
    requested_web_token = False
    original_get = session.get

    def tracked_get(url, *args, **kwargs):
        nonlocal requested_web_token
        if url == REQUEST_TOKEN_URL:
            requested_web_token = True
        return original_get(url, *args, **kwargs)

    session.get = tracked_get
    try:
        authenticate_api_session(session)
    finally:
        session.get = original_get
    return "/oauth/token/" if requested_web_token else "dados_sessao cookie"


def _course_pairs(payload):
    pairs = []
    for _path, value in _walk_json(payload):
        if not isinstance(value, dict):
            continue
        identifier = value.get("id")
        title = value.get("nome") or value.get("name")
        if identifier is not None and isinstance(title, str) and title.strip():
            pairs.append((str(identifier), title))
    return list(dict.fromkeys(pairs))


def reproduce_with_requests(driver, course_id: str, course_url: str, ranked):
    endpoint = COURSE_ENDPOINT.format(course_id=course_id)
    browser_session = criar_sessao_download(driver, course_url)

    print("\n=== Reprodução e minimização com requests ===")
    _print_replay("Anonymous", _safe_get_result(requests.Session(), endpoint, course_id))
    cookies_only = _copy_cookies(browser_session)
    _print_replay("Cookies only", _safe_get_result(cookies_only, endpoint, course_id))

    cookies_user_agent = _copy_cookies(browser_session)
    if "User-Agent" in browser_session.headers:
        cookies_user_agent.headers["User-Agent"] = browser_session.headers["User-Agent"]
    _print_replay(
        "Cookies + User-Agent",
        _safe_get_result(cookies_user_agent, endpoint, course_id),
    )

    cookies_referer = _copy_cookies(browser_session)
    cookies_referer.headers["Referer"] = course_url
    _print_replay(
        "Cookies + Referer", _safe_get_result(cookies_referer, endpoint, course_id)
    )
    _print_replay(
        "Cookies + User-Agent + Referer",
        _safe_get_result(browser_session, endpoint, course_id),
    )

    api_session = _copy_cookies(browser_session)
    token_source = ""
    try:
        credential_origin = _authenticate_and_record_source(api_session)
        token_source = f"cookies only -> {credential_origin}"
    except CourseMetadataError:
        api_session = browser_session
        try:
            credential_origin = _authenticate_and_record_source(api_session)
            token_source = (
                "cookies + User-Agent + Referer -> " + credential_origin
            )
        except CourseMetadataError:
            captured = next(
                (
                    _selected_headers(record.request_headers, AUTH_HEADER_NAMES)
                    for record in ranked
                    if record.url.startswith(COURSE_LIST_ENDPOINT)
                    and _case_insensitive_header(
                        record.request_headers, "Authorization"
                    )
                ),
                None,
            )
            if not captured:
                raise
            api_session = _copy_cookies(browser_session)
            api_session.headers.update(captured)
            token_source = "captured browser request"

    print(f"Credential source: {token_source} (value not displayed)")
    authorization = _selected_headers(api_session.headers, ("Authorization",))
    minimal = requests.Session()
    minimal.headers.clear()
    minimal.headers.update(authorization)
    _print_replay(
        "Authorization only, no explicit Accept/User-Agent",
        _safe_get_result(minimal, endpoint, course_id, accept_json=False),
    )
    for label, headers in _auth_variants(api_session, ranked):
        variant = requests.Session()
        variant.headers.update(headers)
        _print_replay(label, _safe_get_result(variant, endpoint, course_id))

    exact_name = get_course_name(api_session, course_id)
    print("Independent requests replay: YES")
    print("Title path: response['data']['nome']")
    print(f"Verified course: {course_id}")
    print(f"Returned: {exact_name}")

    list_response = api_session.get(
        COURSE_LIST_ENDPOINT,
        headers={"Accept": "application/json"},
        timeout=(15, 45),
    )
    print("\n=== Course-list hypothesis ===")
    print(
        f"GET {COURSE_LIST_ENDPOINT}: HTTP {list_response.status_code} "
        f"{(list_response.headers.get('Content-Type') or '').split(';', 1)[0]}"
    )
    pairs = []
    if list_response.ok:
        try:
            pairs = _course_pairs(list_response.json())
        except ValueError:
            pass
    target_from_list = next((name for key, name in pairs if key == course_id), None)
    print(f"Target mapping present in list: {'YES' if target_from_list else 'NO'}")
    if target_from_list:
        agreement = "YES" if target_from_list == exact_name else "NO"
        print(f"List title equals detail title: {agreement}")

    second = next(((key, name) for key, name in pairs if key != course_id), None)
    print("\n=== Additional-course test ===")
    if second is None:
        print("No second accessible course was returned by the list endpoint.")
    else:
        second_id, list_name = second
        try:
            detail_name = get_course_name(api_session, second_id)
            print(f"Second course ID: {second_id}")
            print(f"Returned: {detail_name}")
            print(
                "List/detail title agreement: "
                f"{'YES' if list_name == detail_name else 'NO'}"
            )
        except CourseMetadataError as error:
            print(f"Second-course detail lookup failed: {sanitizar_texto(str(error))}")

    invalid_id = "999999999999"
    invalid_url = COURSE_ENDPOINT.format(course_id=invalid_id)
    invalid = _safe_get_result(api_session, invalid_url, invalid_id)
    print("\n=== Invalid-ID test ===")
    _print_replay(invalid_id, invalid)
    return exact_name


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Captura o Fetch/XHR da página de um curso e o reproduz com requests."
        )
    )
    parser.add_argument("course_id", help="ID numérico exibido na URL do dashboard")
    parser.add_argument(
        "--capture-seconds",
        type=float,
        default=12,
        help="segundos de tráfego coletados após abrir o curso (padrão: 12)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.course_id.isdigit():
        print("course_id deve conter somente dígitos", file=sys.stderr)
        return 2

    email = (os.getenv("ESTRATEGIA_EMAIL") or "").strip()
    password = os.getenv("ESTRATEGIA_PASSWORD") or ""
    course_url = montar_curso_url(args.course_id)
    driver = None
    try:
        with tempfile.TemporaryDirectory(prefix="estrategia-api-probe-") as directory:
            print("=== Inicialização segura ===")
            print("Performance logging: ENABLED")
            print("Persistent traffic dump: DISABLED")
            print("Secret values in report: DISABLED")
            if not email or not password:
                print(
                    "Credenciais não vieram do ambiente; preencha os campos no "
                    "Edge e conclua Entrar/2FA/captcha manualmente."
                )
            driver = create_edge_driver(
                Path(directory), performance_logging=True
            )
            alertas = RecuperadorAlertas(driver)
            do_login(driver, email, password, alertas=alertas)
            password = None

            print("\nLogin detected. Clearing pre-course performance entries.")
            try:
                records = capture_course_traffic(
                    driver,
                    course_url,
                    max(args.capture_seconds, 1),
                    alertas,
                )
            except Exception as capture_error:
                safe_capture_error = sanitizar_texto(
                    str(capture_error)
                ).split("Stacktrace:", 1)[0].strip()
                print(f"Traffic capture failed: {safe_capture_error}")
                print("Continuing with the independent requests replay.")
                records = []
            ranked = rank_records(records, args.course_id)
            report_candidates(ranked)
            reproduce_with_requests(
                driver, args.course_id, course_url, ranked
            )
        return 0
    except Exception as error:
        safe_error = sanitizar_texto(str(error)).split("Stacktrace:", 1)[0].strip()
        print(
            f"Probe failed: {safe_error}",
            file=sys.stderr,
        )
        if os.getenv("ESTRATEGIA_DEBUG") == "1":
            import traceback

            traceback.print_exc()
        return 1
    finally:
        password = None
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
