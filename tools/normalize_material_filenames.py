#!/usr/bin/env python3
"""Remove rótulos transitórios de nomes antigos sem perder conteúdo."""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from dataclasses import dataclass
from pathlib import Path

TRANSIENT_SUFFIX = re.compile(
    r"(?:\s+(?:baixado|lessonbutton))+\s*$",
    re.IGNORECASE,
)
HASH_CHUNK_SIZE = 4 * 1024 * 1024


class FilenameNormalizationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RenamePlan:
    source: Path
    destination: Path
    duplicate: bool


def normalized_name(name: str) -> str:
    path = Path(name)
    stem = TRANSIENT_SUFFIX.sub("", path.stem).rstrip(" .")
    return f"{stem}{path.suffix}" if stem else name


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(HASH_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def build_plan(roots: list[Path]) -> list[RenamePlan]:
    groups: dict[Path, list[Path]] = {}
    for root_value in roots:
        root = Path(root_value).expanduser().resolve()
        if not root.is_dir():
            raise FilenameNormalizationError(f"raiz inexistente: {root}")
        for source in sorted(root.rglob("*")):
            if not source.is_file() or source.is_symlink():
                continue
            destination = source.with_name(normalized_name(source.name))
            if destination == source:
                continue
            groups.setdefault(destination, []).append(source)

    plan = []
    for destination, sources in sorted(groups.items(), key=lambda item: str(item[0])):
        reference = destination if destination.is_file() else sources[0]
        if destination.exists() and not destination.is_file():
            raise FilenameNormalizationError(
                f"destino existente não é arquivo regular: {destination}"
            )
        reference_size = reference.stat().st_size
        reference_hash = _sha256(reference)
        for source in sources:
            if source.stat().st_size != reference_size:
                raise FilenameNormalizationError(
                    f"colisão com tamanhos diferentes: {destination}"
                )
            if _sha256(source) != reference_hash:
                raise FilenameNormalizationError(
                    f"colisão com conteúdo diferente: {destination}"
                )

        if destination.is_file():
            plan.extend(RenamePlan(source, destination, True) for source in sources)
        else:
            plan.append(RenamePlan(sources[0], destination, False))
            plan.extend(
                RenamePlan(source, destination, True) for source in sources[1:]
            )
    return plan


def apply_plan(plan: list[RenamePlan]) -> tuple[int, int, int]:
    renamed = 0
    duplicates = 0
    duplicate_bytes = 0
    for item in plan:
        if item.duplicate:
            duplicate_bytes += item.source.stat().st_size
            item.source.unlink()
            duplicates += 1
        else:
            item.source.rename(item.destination)
            renamed += 1
    return renamed, duplicates, duplicate_bytes


def parse_args():
    parser = argparse.ArgumentParser(
        description="Planeja ou normaliza nomes contendo Baixado/LessonButton."
    )
    parser.add_argument("--root", action="append", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        plan = build_plan(args.root)
        duplicate_count = sum(item.duplicate for item in plan)
        print(f"Nomes transitórios encontrados: {len(plan)}")
        print(f"Renomeações sem colisão: {len(plan) - duplicate_count}")
        print(f"Duplicatas idênticas por SHA-256: {duplicate_count}")
        if not args.apply:
            print("PLANO SOMENTE: nenhum arquivo foi alterado.")
            return 0
        renamed, duplicates, duplicate_bytes = apply_plan(plan)
        print(f"Arquivos renomeados: {renamed}")
        print(f"Duplicatas idênticas consolidadas: {duplicates}")
        print(f"Bytes redundantes removidos: {duplicate_bytes}")
        return 0
    except (FilenameNormalizationError, OSError) as error:
        print(f"Normalização interrompida com segurança: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
