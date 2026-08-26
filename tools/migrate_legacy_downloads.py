#!/usr/bin/env python3
"""Renomeia acervos legados e importa cursos estruturados sem apagar a origem."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from estrategia_downloader.collection import (
    ensure_course_folder,
    open_collection,
    save_collection,
    update_course_status,
)
from estrategia_downloader.course_metadata import CourseSummary
from estrategia_downloader.resume import ARQUIVO_ESTADO, salvar_estado_execucao
from estrategia_downloader.utils import formatar_tamanho, slug_nome_curso

LEGACY_PATTERN = re.compile(r"^CURSO_ESTRATEGIA_(\d+)_(.+)$")
TIMESTAMP_PATTERN = re.compile(r"^\d+$")
LINK_MANIFEST = "links_estrategia_conteudo.txt"
COPY_CHUNK_SIZE = 4 * 1024 * 1024


class MigrationError(RuntimeError):
    """O plano de migração não pôde ser executado sem ambiguidade."""


@dataclass(frozen=True, slots=True)
class LegacyDownload:
    source: Path
    course: CourseSummary
    suffix: str
    structured: bool
    renamed: Path


def load_catalog(path: Path) -> dict[str, CourseSummary]:
    """Lê o TSV ``ID<TAB>título`` produzido por list_course_catalog.py."""

    courses: dict[str, CourseSummary] = {}
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise MigrationError("não foi possível ler o catálogo UTF-8") from error
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        parts = line.split("\t", 1)
        if len(parts) != 2 or not parts[0].isdigit() or not parts[1].strip():
            raise MigrationError(f"linha {number} inválida no catálogo")
        course_id, title = parts
        if course_id in courses and courses[course_id].name != title:
            raise MigrationError(f"títulos conflitantes para o curso {course_id}")
        courses[course_id] = CourseSummary(course_id, title)
    if not courses:
        raise MigrationError("o catálogo está vazio")
    return courses


def _has_lesson_tree(folder: Path) -> bool:
    try:
        return any(
            item.is_dir() and re.fullmatch(r"aula_\d+", item.name)
            for item in folder.iterdir()
        )
    except OSError as error:
        raise MigrationError(f"não foi possível examinar {folder}") from error


def _partial_suffix(value: str) -> str:
    if TIMESTAMP_PATTERN.fullmatch(value):
        return f"pdfs-legado-{value}"
    cleaned = slug_nome_curso(value, limite=60)
    if not cleaned.startswith("pdfs"):
        cleaned = f"pdfs-legado-{cleaned}"
    return cleaned


def renamed_folder_name(
    course: CourseSummary,
    original_suffix: str,
    *,
    structured: bool,
) -> str:
    base = f"{slug_nome_curso(course.name)}-id-{course.course_id}"
    if structured:
        suffix = (
            original_suffix
            if TIMESTAMP_PATTERN.fullmatch(original_suffix)
            else slug_nome_curso(original_suffix, limite=60)
        )
    else:
        suffix = _partial_suffix(original_suffix)
    return f"{base}-{suffix}"


def discover_legacy_downloads(
    roots: list[Path],
    catalog: dict[str, CourseSummary],
) -> list[LegacyDownload]:
    found = []
    seen_sources = set()
    for root_value in roots:
        root = Path(root_value).expanduser().resolve()
        if not root.is_dir():
            raise MigrationError(f"raiz inexistente: {root}")
        try:
            children = sorted(root.iterdir())
        except OSError as error:
            raise MigrationError(f"não foi possível listar a raiz {root}") from error
        for child in children:
            match = LEGACY_PATTERN.fullmatch(child.name)
            if not match or not child.is_dir():
                continue
            source = child.resolve()
            if source in seen_sources:
                continue
            seen_sources.add(source)
            course_id, suffix = match.groups()
            course = catalog.get(course_id)
            if course is None:
                raise MigrationError(
                    f"o curso legado {course_id} não está no catálogo autenticado"
                )
            structured = _has_lesson_tree(source)
            renamed = source.with_name(
                renamed_folder_name(
                    course,
                    suffix,
                    structured=structured,
                )
            )
            if renamed.exists() and renamed != source:
                raise MigrationError(f"o destino da renomeação já existe: {renamed}")
            found.append(
                LegacyDownload(source, course, suffix, structured, renamed)
            )
    return sorted(found, key=lambda item: str(item.source).casefold())


def _files(folder: Path):
    for path in sorted(folder.rglob("*")):
        if path.is_symlink():
            raise MigrationError(f"link simbólico não será importado: {path}")
        if path.is_file():
            yield path


def tree_stats(folder: Path) -> tuple[int, int]:
    count = 0
    size = 0
    for path in _files(folder):
        count += 1
        size += path.stat().st_size
    return count, size


def scrub_link_manifest(folder: Path) -> int:
    """Remove integralmente URLs de todos os manifestos encontrados."""

    changed = 0
    for manifest in sorted(folder.rglob(LINK_MANIFEST)):
        try:
            lines = manifest.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as error:
            raise MigrationError(f"não foi possível ler {manifest}") from error
        sanitized = []
        for index, line in enumerate(lines):
            parts = line.split(";", 4)
            if len(parts) == 5:
                value = (
                    "origem"
                    if index == 0 and parts[:4] == ["aula", "tipo", "numero", "titulo"]
                    else "[URL omitida por segurança]"
                )
                line = ";".join((*parts[:4], value))
            sanitized.append(line)
        new_text = "\n".join(sanitized) + ("\n" if lines else "")
        try:
            old_text = manifest.read_text(encoding="utf-8", errors="replace")
            if new_text != old_text:
                temporary = manifest.with_name(f"{manifest.name}.migracao.tmp")
                temporary.write_text(new_text, encoding="utf-8")
                temporary.replace(manifest)
                changed += 1
        except OSError as error:
            raise MigrationError(f"não foi possível sanitizar {manifest}") from error
    return changed


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(COPY_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_and_verify(source: Path, destination: Path) -> bool:
    """Copia atomicamente e confirma tamanho e SHA-256; retorna se copiou."""

    source_size = source.stat().st_size
    copied = False
    if destination.exists() and not destination.is_file():
        raise MigrationError(f"o destino não é um arquivo regular: {destination}")
    if destination.is_file() and destination.stat().st_size != source_size:
        raise MigrationError(
            "a coleção já possui um arquivo de tamanho diferente; "
            f"nenhum deles foi sobrescrito: {destination}"
        )
    if not destination.is_file():
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f"{destination.name}.legacy-copy.part")
        source_hash = hashlib.sha256()
        with source.open("rb") as input_stream, temporary.open("wb") as output_stream:
            while chunk := input_stream.read(COPY_CHUNK_SIZE):
                source_hash.update(chunk)
                output_stream.write(chunk)
            output_stream.flush()
            os.fsync(output_stream.fileno())
        if temporary.stat().st_size != source_size:
            raise MigrationError(f"cópia truncada de {source}")
        shutil.copystat(source, temporary)
        temporary.replace(destination)
        expected_hash = source_hash.hexdigest()
        copied = True
    else:
        expected_hash = _sha256(source)

    if _sha256(destination) != expected_hash:
        raise MigrationError(f"SHA-256 divergente após copiar {source}")
    return copied


def import_structured(source: Path, destination: Path) -> dict:
    files = list(_files(source))
    total_size = sum(path.stat().st_size for path in files)
    free = shutil.disk_usage(destination.parent).free
    existing_size = sum(
        path.stat().st_size
        for path in files
        if (destination / path.relative_to(source)).is_file()
        and (destination / path.relative_to(source)).stat().st_size
        == path.stat().st_size
    )
    required = max(total_size - existing_size, 0)
    if free < required + 16 * 1024 * 1024:
        raise MigrationError(
            "espaço insuficiente para importar o acervo: "
            f"disponível {formatar_tamanho(free)}, "
            f"necessário {formatar_tamanho(required)}"
        )

    copied = 0
    verified = 0
    copied_bytes = 0
    for index, source_file in enumerate(files, start=1):
        relative = source_file.relative_to(source)
        destination_file = destination / relative
        was_copied = _copy_and_verify(source_file, destination_file)
        copied += int(was_copied)
        copied_bytes += source_file.stat().st_size if was_copied else 0
        verified += 1
        if index == 1 or index == len(files) or index % 25 == 0:
            print(
                f"      {index}/{len(files)} arquivos verificados "
                f"({formatar_tamanho(copied_bytes)} copiados)",
                flush=True,
            )
    return {
        "arquivos_origem": len(files),
        "bytes_origem": total_size,
        "arquivos_copiados": copied,
        "bytes_copiados": copied_bytes,
        "arquivos_sha256_verificados": verified,
    }


def print_plan(items: list[LegacyDownload], collection: Path | None) -> None:
    structured = sum(item.structured for item in items)
    print(f"Acervos legados encontrados: {len(items)}")
    print(f"Estruturados e importáveis: {structured}")
    print(f"Parciais somente de PDFs: {len(items) - structured}")
    if collection is not None:
        print(f"Coleção escolhida: {collection}")
    for item in items:
        kind = "estruturado" if item.structured else "PDFs parciais"
        print(f"- [{kind}] {item.source}")
        print(f"  -> {item.renamed}")


def apply_migration(
    items: list[LegacyDownload],
    selected_collection: Path | None,
) -> None:
    collection_root = None
    collection_state = None
    if selected_collection is not None:
        collection_root, collection_state, _existed = open_collection(
            selected_collection
        )

    for position, item in enumerate(items, start=1):
        print(
            f"\n[{position}/{len(items)}] {item.course.course_id} — "
            f"{item.course.name}"
        )
        changed = scrub_link_manifest(item.source)
        print(f"   Manifestos com URLs removidas: {changed}")

        if item.structured and collection_root is not None:
            destination = ensure_course_folder(
                collection_root,
                collection_state,
                item.course,
            )
            print(f"   Importando e verificando em: {destination}")
            summary = import_structured(item.source, destination)
            scrub_link_manifest(destination)
            salvar_estado_execucao(
                destination,
                item.course.course_id,
                "incompleto",
                summary,
            )
            update_course_status(
                collection_state,
                item.course,
                destination,
                "incompleto",
                summary=summary,
                error="acervo legado importado; aguarda auditoria remota",
            )
            save_collection(collection_root, collection_state)

        item.source.rename(item.renamed)
        if item.structured and not (item.renamed / ARQUIVO_ESTADO).is_file():
            salvar_estado_execucao(
                item.renamed,
                item.course.course_id,
                "incompleto",
                {"migrado_para_nome_descritivo": True},
            )
        print(f"   Fonte renomeada para: {item.renamed}")


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Planeja ou executa a migração não destrutiva de pastas "
            "CURSO_ESTRATEGIA_<ID>_*"
        )
    )
    parser.add_argument("--catalog", required=True, type=Path)
    parser.add_argument("--root", action="append", required=True, type=Path)
    parser.add_argument(
        "--collection",
        type=Path,
        help="base da coleção que receberá cópias estruturadas verificadas",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="aplica o plano; sem esta opção nada é alterado",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        catalog = load_catalog(args.catalog)
        items = discover_legacy_downloads(args.root, catalog)
        if not items:
            raise MigrationError("nenhum acervo legado foi encontrado")
        print_plan(items, args.collection)
        if not args.apply:
            print("\nPLANO SOMENTE: nenhuma pasta ou arquivo foi alterado.")
            return 0
        apply_migration(items, args.collection)
        print("\nMigração concluída; nenhuma pasta de origem foi apagada.")
        return 0
    except (MigrationError, OSError) as error:
        print(f"Migração interrompida com segurança: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
