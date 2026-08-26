#!/usr/bin/env python3
"""Imprime o título exato de um curso usando a API da área do aluno."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

from estrategia_downloader.alerts import RecuperadorAlertas
from estrategia_downloader.app import do_login, montar_curso_url
from estrategia_downloader.browser import create_edge_driver
from estrategia_downloader.course_metadata import (
    create_course_api_session,
    get_course_name,
)
from estrategia_downloader.downloads import criar_sessao_download
from estrategia_downloader.utils import sanitizar_texto


def parse_args():
    parser = argparse.ArgumentParser(
        description="Obtém pela API o título exato de um curso do Estratégia."
    )
    parser.add_argument("course_id", help="ID numérico exibido na URL do dashboard")
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
        with tempfile.TemporaryDirectory(prefix="estrategia-course-name-") as directory:
            with redirect_stdout(sys.stderr):
                if not email or not password:
                    print(
                        "Preencha o login no Edge e conclua captcha/2FA, se houver."
                    )
                driver = create_edge_driver(Path(directory))
                alertas = RecuperadorAlertas(driver)
                do_login(driver, email, password, alertas=alertas)
                password = None
                web_session = criar_sessao_download(driver, course_url)
                try:
                    api_session = create_course_api_session(web_session)
                finally:
                    web_session.close()
                driver.quit()
                driver = None
                try:
                    name = get_course_name(api_session, args.course_id)
                finally:
                    api_session.close()
            print(name)
        return 0
    except Exception as error:
        safe_error = sanitizar_texto(str(error)).split("Stacktrace:", 1)[0].strip()
        print(f"Não foi possível obter o título: {safe_error}", file=sys.stderr)
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
