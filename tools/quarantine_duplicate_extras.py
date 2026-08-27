#!/usr/bin/env python3
"""Isola extras legados duplicados, com recertificação e reversão segura."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from estrategia_downloader.duplicate_cleanup import (  # noqa: E402
    DuplicateCleanupError,
    apply_duplicate_cleanup_plan,
    build_duplicate_cleanup_plan,
)


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Move para quarentena somente extras cujo SHA-256 coincide com "
            "um arquivo canônico certificado."
        )
    )
    parser.add_argument("course", nargs="+", type=Path, help="pasta de curso")
    parser.add_argument(
        "--quarantine-root",
        type=Path,
        help=(
            "raiz da quarentena no mesmo volume; por padrão usa uma pasta "
            "oculta ao lado do curso"
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="aplica as movimentações e recertifica; sem esta opção é dry-run",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        plans = [
            build_duplicate_cleanup_plan(
                course,
                quarantine_root=args.quarantine_root,
            )
            for course in args.course
        ]
        for plan in plans:
            print(f"Curso: {plan.course_folder}")
            print(f"Duplicatas certificadas: {len(plan.items)}")
            print(f"Bytes redundantes: {plan.total_bytes}")
            print(f"Quarentena: {plan.quarantine_folder}")
            if not args.apply:
                print("PLANO SOMENTE: nenhum arquivo foi alterado.")
                continue
            report = apply_duplicate_cleanup_plan(plan)
            print(
                "APLICADO: "
                f"{len(plan.items)} duplicata(s) isolada(s); "
                f"{report['arquivos_fisicos']} arquivo(s) recertificado(s), "
                f"{report['extras_legados']} extra(s) único(s) preservado(s)."
            )
        return 0
    except (DuplicateCleanupError, OSError, ValueError) as error:
        print(f"Limpeza interrompida com segurança: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
