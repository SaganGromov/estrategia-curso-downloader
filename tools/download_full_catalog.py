#!/usr/bin/env python3
"""Executa o modo integral por terminal, com filtro e volumes adicionais."""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from estrategia_downloader.alerts import RecuperadorAlertas  # noqa: E402
from estrategia_downloader.app import do_login, executar_colecao_integral  # noqa: E402
from estrategia_downloader.browser import create_edge_driver  # noqa: E402
from estrategia_downloader.errors import ColecaoIncompletaError  # noqa: E402
from estrategia_downloader.utils import sanitizar_texto, verificar_destino  # noqa: E402


class TerminalPanel:
    """Adaptador mínimo para reutilizar o orquestrador sem abrir o painel web."""

    def __init__(self):
        self.values = {}
        self.summary = None

    def atualizar(self, **values):
        self.values.update(values)

    def verificar_cancelamento(self):
        return None

    def definir_resumo(self, summary):
        self.summary = summary


def parse_args():
    parser = argparse.ArgumentParser(
        description="Baixa e audita cursos do catálogo autenticado."
    )
    parser.add_argument(
        "--destination",
        required=True,
        type=Path,
        help="pasta-base principal ou uma coleção já existente",
    )
    parser.add_argument(
        "--spillover",
        action="append",
        default=[],
        type=Path,
        help="raiz adicional com nome único; pode ser repetida",
    )
    parser.add_argument(
        "--include-regex",
        action="append",
        default=[],
        help="processa apenas IDs/títulos correspondentes; pode ser repetida",
    )
    parser.add_argument(
        "--exclude-regex",
        action="append",
        default=[],
        help="exclui IDs/títulos correspondentes; pode ser repetida",
    )
    parser.add_argument(
        "--submit-login",
        action="store_true",
        help="aciona Entrar após preencher credenciais vindas do ambiente",
    )
    return parser.parse_args()


def _course_selector(includes: list[str], excludes: list[str]):
    if not includes and not excludes:
        return None
    try:
        include_patterns = [
            re.compile(value, re.IGNORECASE) for value in includes
        ]
        exclude_patterns = [
            re.compile(value, re.IGNORECASE) for value in excludes
        ]
    except re.error as error:
        raise ValueError(f"expressão de filtro inválida: {error}") from error

    def selected(course):
        value = f"{course.course_id}\t{course.name}"
        included = not include_patterns or any(
            pattern.search(value) for pattern in include_patterns
        )
        excluded = any(pattern.search(value) for pattern in exclude_patterns)
        return included and not excluded

    return selected


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
    panel = TerminalPanel()
    try:
        verificar_destino(args.destination)
        for path in args.spillover:
            verificar_destino(path)
        selector = _course_selector(args.include_regex, args.exclude_regex)
        driver = create_edge_driver(args.destination)
        alerts = RecuperadorAlertas(
            driver,
            painel=panel,
            verificar_cancelamento=panel.verificar_cancelamento,
        )
        do_login(
            driver,
            email,
            password,
            panel.verificar_cancelamento,
            alerts,
            submeter_automaticamente=args.submit_login,
        )
        password = None
        summary = executar_colecao_integral(
            driver,
            alerts,
            panel,
            args.destination,
            pastas_extras=tuple(args.spillover),
            selecionar_curso=selector,
        )
        panel.definir_resumo(summary)
        print("\n✅ Escopo solicitado auditado sem pendências.")
        return 0
    except ColecaoIncompletaError as error:
        panel.definir_resumo(error.resumo)
        print(
            f"\n🚩 Escopo percorrido, mas ainda incompleto: {error}",
            file=sys.stderr,
        )
        return 1
    except KeyboardInterrupt:
        print("\nOperação interrompida; o estado retomável foi preservado.", file=sys.stderr)
        return 130
    except Exception as error:
        message = sanitizar_texto(str(error)).split("Stacktrace:", 1)[0].strip()
        print(f"\nNão foi possível concluir: {message}", file=sys.stderr)
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
