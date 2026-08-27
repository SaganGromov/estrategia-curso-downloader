#!/usr/bin/env python3
"""Valida o inventário de um curso pela API sem baixar arquivos."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from estrategia_downloader.alerts import RecuperadorAlertas  # noqa: E402
from estrategia_downloader.app import do_login, montar_curso_url  # noqa: E402
from estrategia_downloader.browser import create_edge_driver  # noqa: E402
from estrategia_downloader.course_inventory import (  # noqa: E402
    get_course_snapshot,
    get_lesson_snapshot,
)
from estrategia_downloader.course_metadata import (  # noqa: E402
    create_course_api_session,
)
from estrategia_downloader.downloads import criar_sessao_download  # noqa: E402
from estrategia_downloader.utils import sanitizar_texto  # noqa: E402


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Valida uma resposta de curso e uma resposta por aula, sem DOM e "
            "sem transferir os arquivos enumerados."
        )
    )
    parser.add_argument("course_id")
    parser.add_argument("--submit-login", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.course_id.isdigit():
        print("course_id deve conter somente dígitos", file=sys.stderr)
        return 2

    email = (os.getenv("ESTRATEGIA_EMAIL") or "").strip()
    password = os.getenv("ESTRATEGIA_PASSWORD") or ""
    if args.submit_login and (not email or not password):
        print(
            "--submit-login requer ESTRATEGIA_EMAIL e ESTRATEGIA_PASSWORD",
            file=sys.stderr,
        )
        return 2

    driver = None
    web_session = None
    api_session = None
    try:
        with tempfile.TemporaryDirectory(prefix="estrategia-api-check-") as directory:
            driver = create_edge_driver(Path(directory))
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
            web_session = criar_sessao_download(driver, course_url)
            api_session = create_course_api_session(web_session)
            course = get_course_snapshot(api_session, args.course_id)

            print(f"Course ID: {course.course_id}")
            print(f"Title: {course.title}")
            print(f"Declared lessons: {course.total_lessons}")
            print(f"Unique lesson IDs: {len(course.lessons)}")
            issues = list(course.unexpected_url_fields)
            for path in course.unexpected_url_fields:
                print(f"Unknown course URL field: {path}")

            total_materials = 0
            total_videos = 0
            for lesson in course.lessons:
                snapshot = get_lesson_snapshot(api_session, lesson)
                total_materials += len(snapshot.materials)
                total_videos += len(snapshot.videos)
                print(
                    f"Lesson {lesson.position}/{course.total_lessons}: "
                    f"id={lesson.lesson_id}; folder=aula_{lesson.number:02d}; "
                    f"materials={len(snapshot.materials)}; "
                    f"videos={len(snapshot.videos)}; "
                    f"unresolved={len(snapshot.unresolved)}; "
                    f"unknown_url_fields={len(snapshot.unexpected_url_fields)}"
                )
                for description in snapshot.unresolved:
                    print(f"Unresolved: {description}")
                for path in snapshot.unexpected_url_fields:
                    print(f"Unknown lesson URL field: {path}")
                issues.extend(snapshot.unresolved)
                issues.extend(snapshot.unexpected_url_fields)

            print(f"Total materials: {total_materials}")
            print(f"Total videos: {total_videos}")
            print(f"Inventory issues: {len(issues)}")
            return 1 if issues else 0
    except Exception as error:
        message = sanitizar_texto(str(error)).split("Stacktrace:", 1)[0].strip()
        print(f"API inventory check failed: {message}", file=sys.stderr)
        return 1
    finally:
        password = None
        if api_session is not None:
            api_session.close()
        if web_session is not None:
            web_session.close()
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
