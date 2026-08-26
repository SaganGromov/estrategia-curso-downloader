#!/usr/bin/env python3
"""Lista IDs e títulos canônicos da conta sem persistir credenciais ou tokens."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

from estrategia_downloader.alerts import RecuperadorAlertas
from estrategia_downloader.app import DASHBOARD_COURSES_URL, do_login
from estrategia_downloader.browser import create_edge_driver
from estrategia_downloader.course_metadata import (
    create_course_api_session,
    list_accessible_courses,
)
from estrategia_downloader.downloads import criar_sessao_download
from estrategia_downloader.utils import sanitizar_texto


def parse_args():
    parser = argparse.ArgumentParser(
        description="Lista o catálogo autenticado como ID seguido do título exato."
    )
    parser.add_argument(
        "--submit-login",
        action="store_true",
        help="aciona Entrar após preencher credenciais vindas do ambiente",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    email = (os.getenv("ESTRATEGIA_EMAIL") or "").strip()
    password = os.getenv("ESTRATEGIA_PASSWORD") or ""
    if args.submit_login and (not email or not password):
        print(
            "--submit-login requer ESTRATEGIA_EMAIL e ESTRATEGIA_PASSWORD",
            file=sys.stderr,
        )
        return 2

    driver = None
    try:
        with tempfile.TemporaryDirectory(prefix="estrategia-catalogo-") as directory:
            driver = create_edge_driver(Path(directory))
            alertas = RecuperadorAlertas(driver)
            do_login(
                driver,
                email,
                password,
                alertas=alertas,
                submeter_automaticamente=args.submit_login,
            )
            password = None
            web_session = criar_sessao_download(driver, DASHBOARD_COURSES_URL)
            try:
                api_session = create_course_api_session(web_session)
            finally:
                web_session.close()
            try:
                courses = list_accessible_courses(api_session)
            finally:
                api_session.close()
            for course in courses:
                print(f"{course.course_id}\t{course.name}")
        return 0
    except Exception as error:
        message = sanitizar_texto(str(error)).split("Stacktrace:", 1)[0].strip()
        print(f"Não foi possível listar o catálogo: {message}", file=sys.stderr)
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
