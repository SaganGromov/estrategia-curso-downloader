#!/usr/bin/env python3
"""Inspeciona, sem persistir segredos, as fontes remotas de uma aula.

O utilitário existe para investigar falhas de cobertura da descoberta DOM. Ele
mantém corpos JSON somente em memória e imprime apenas endpoint sanitizado,
tipos, chaves e contagens estruturais.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from estrategia_downloader.alerts import RecuperadorAlertas  # noqa: E402
from estrategia_downloader.app import do_login, listar_aulas, montar_curso_url  # noqa: E402
from estrategia_downloader.browser import create_edge_driver  # noqa: E402
from estrategia_downloader.utils import sanitizar_texto  # noqa: E402
from tools.probe_course_api import (  # noqa: E402
    _case_insensitive_header,
    _walk_json,
    capture_course_traffic,
)

INTERESTING_TERMS = (
    "aula",
    "lesson",
    "material",
    "arquivo",
    "file",
    "pdf",
    "slide",
    "mapa",
    "video",
    "curso",
    "course",
)


def _safe_endpoint(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _json_inventory(payload) -> tuple[Counter, list[str]]:
    keys = Counter()
    structures = []
    for path, value in _walk_json(payload):
        if isinstance(value, dict):
            for key in value:
                normalized = str(key).casefold()
                if any(term in normalized for term in INTERESTING_TERMS):
                    keys[str(key)] += 1
            if any(term in path.casefold() for term in INTERESTING_TERMS):
                structures.append(f"{path}: object[{len(value)}]")
        elif isinstance(value, list) and any(
            term in path.casefold() for term in INTERESTING_TERMS
        ):
            structures.append(f"{path}: list[{len(value)}]")
    return keys, list(dict.fromkeys(structures))


def _type_name(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "list"
    if isinstance(value, str):
        return "string"
    if isinstance(value, (int, float)):
        return "number"
    return type(value).__name__


def _json_shape(payload, *, max_depth: int = 4) -> list[str]:
    """Descreve chaves e tipos sem incluir qualquer valor da resposta."""

    lines = []

    def visit(value, path: str, depth: int) -> None:
        if depth > max_depth:
            return
        if isinstance(value, dict):
            fields = ", ".join(
                f"{key}:{_type_name(child)}" for key, child in value.items()
            )
            lines.append(f"{path}: object{{{fields}}}")
            for key, child in value.items():
                if isinstance(child, (dict, list)):
                    visit(child, f"{path}.{key}", depth + 1)
        elif isinstance(value, list):
            lines.append(f"{path}: list[{len(value)}]")
            if value:
                visit(value[0], f"{path}[0]", depth + 1)

    visit(payload, "$", 0)
    return lines


def _report(records) -> None:
    candidates = []
    for record in records:
        if record.resource_type not in {"Fetch", "XHR"}:
            continue
        keys, structures = _json_inventory(record.json_body)
        path = urlsplit(record.url).path.casefold()
        interesting_path = any(term in path for term in INTERESTING_TERMS)
        if record.json_body is None or (not keys and not interesting_path):
            continue
        candidates.append((record, keys, structures))

    print(f"Fetch/XHR JSON relevantes: {len(candidates)}")
    for index, (record, keys, structures) in enumerate(candidates, 1):
        content_type = _case_insensitive_header(
            record.response_headers, "Content-Type"
        )
        print(f"\nCandidato {index}")
        print(f"Method: {record.method or 'UNKNOWN'}")
        print(f"Endpoint: {_safe_endpoint(record.url)}")
        print(f"Status: {record.status if record.status is not None else 'UNKNOWN'}")
        print(f"Response type: {content_type or record.mime_type or 'UNKNOWN'}")
        print(
            "Relevant keys: "
            + (", ".join(f"{key}({count})" for key, count in keys.most_common()) or "none")
        )
        for structure in structures[:80]:
            print(f"Structure: {structure}")
        for shape in _json_shape(record.json_body)[:80]:
            print(f"Schema: {shape}")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Captura o inventário remoto de uma aula sem expor segredos."
    )
    parser.add_argument("course_id")
    parser.add_argument(
        "--lesson-position",
        type=int,
        default=-1,
        help="posição humana da aula; -1 escolhe a última",
    )
    parser.add_argument("--capture-seconds", type=float, default=15)
    parser.add_argument("--submit-login", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.course_id.isdigit():
        print("course_id deve conter somente dígitos", file=sys.stderr)
        return 2
    email = (os.getenv("ESTRATEGIA_EMAIL") or "").strip()
    password = os.getenv("ESTRATEGIA_PASSWORD") or ""
    driver = None
    try:
        with tempfile.TemporaryDirectory(prefix="estrategia-inventory-probe-") as directory:
            driver = create_edge_driver(Path(directory), performance_logging=True)
            alerts = RecuperadorAlertas(driver)
            do_login(
                driver,
                email,
                password,
                alertas=alerts,
                submeter_automaticamente=args.submit_login,
            )
            password = None
            course_url = montar_curso_url(args.course_id)
            lessons = listar_aulas(driver, course_url, alerts)
            if not lessons:
                raise RuntimeError("o curso não expôs aulas numeradas")
            position = args.lesson_position
            if position == -1:
                position = len(lessons)
            if not 1 <= position <= len(lessons):
                raise ValueError(
                    f"posição {position} fora do intervalo 1..{len(lessons)}"
                )
            lesson = lessons[position - 1]
            print(f"Curso: {args.course_id}")
            print(f"Aulas DOM: {len(lessons)}")
            print(f"Posição auditada: {position}")
            print(f"Número de pasta: aula_{lesson['num']:02d}")
            print(f"Lesson endpoint: {_safe_endpoint(lesson['href'])}")
            records = capture_course_traffic(
                driver,
                lesson["href"],
                max(args.capture_seconds, 1),
                alerts,
            )
            _report(records)
        return 0
    except Exception as error:
        message = sanitizar_texto(str(error)).split("Stacktrace:", 1)[0].strip()
        print(f"Probe failed: {message}", file=sys.stderr)
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
