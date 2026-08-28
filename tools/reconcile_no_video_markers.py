#!/usr/bin/env python3
"""Reconcilia marcadores de aulas sem vídeos a partir do inventário seguro."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from estrategia_downloader.integrity import INVENTORY_FILE  # noqa: E402
from estrategia_downloader.lesson_markers import (  # noqa: E402
    reconcile_no_video_markers,
)


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Cria marcadores somente quando o inventário autenticado confirma "
            "zero vídeos em uma aula liberada."
        )
    )
    parser.add_argument("--root", action="append", required=True, type=Path)
    parser.add_argument("--include-regex")
    parser.add_argument("--assume-legacy-full", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--json", action="store_true", dest="as_json")
    return parser.parse_args()


def _course_folders(root: Path):
    root = root.expanduser().resolve()
    if (root / INVENTORY_FILE).is_file():
        yield root
        return
    if not root.is_dir():
        raise ValueError(f"raiz inexistente: {root}")
    for child in sorted(root.iterdir()):
        if child.is_dir() and (child / INVENTORY_FILE).is_file():
            yield child


def main() -> int:
    args = parse_args()
    pattern = re.compile(args.include_regex) if args.include_regex else None
    reports = []
    failures = []
    for root in args.root:
        try:
            folders = list(_course_folders(root))
        except ValueError as error:
            failures.append(str(error))
            continue
        for folder in folders:
            if pattern and pattern.search(folder.name) is None:
                continue
            try:
                inventory = json.loads(
                    (folder / INVENTORY_FILE).read_text(encoding="utf-8")
                )
                report = reconcile_no_video_markers(
                    folder,
                    str(inventory["curso_id"]),
                    inventory["aulas"],
                    assume_legacy_full=args.assume_legacy_full,
                    apply=args.apply,
                )
                report["pasta"] = str(folder)
                reports.append(report)
            except (KeyError, OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
                failures.append(f"{folder}: {error}")

    output = {
        "aplicado": bool(args.apply),
        "cursos": len(reports),
        "esperados": sum(item["esperados"] for item in reports),
        "criados": sum(len(item["criados"]) for item in reports),
        "atualizados": sum(len(item["atualizados"]) for item in reports),
        "removidos": sum(len(item["removidos"]) for item in reports),
        "conflitos": sum(len(item["conflitos"]) for item in reports),
        "falhas": failures,
        "relatorios": reports,
    }
    if args.as_json:
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        print(
            f"Cursos: {output['cursos']}; marcadores esperados: "
            f"{output['esperados']}; criados: {output['criados']}; "
            f"atualizados: {output['atualizados']}; removidos: "
            f"{output['removidos']}; conflitos: {output['conflitos']}."
        )
        for failure in failures:
            print(f"ERRO: {failure}", file=sys.stderr)
    return 1 if failures or output["conflitos"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
