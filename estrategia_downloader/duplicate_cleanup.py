"""Isola extras legados que duplicam conteúdo canônico certificado."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from .local_verification import (
    CERTIFICATE_FILE,
    CERTIFICATE_SCHEMA,
    verify_course_folder,
    write_certificate,
)


class DuplicateCleanupError(RuntimeError):
    """A limpeza não pôde prosseguir sem arriscar conteúdo."""


@dataclass(frozen=True, slots=True)
class DuplicateExtra:
    source: Path
    canonical: Path
    quarantine: Path
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class DuplicateCleanupPlan:
    course_folder: Path
    quarantine_folder: Path
    items: tuple[DuplicateExtra, ...]

    @property
    def total_bytes(self) -> int:
        return sum(item.size for item in self.items)


def _read_certificate(course_folder: Path) -> dict:
    path = course_folder / CERTIFICATE_FILE
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise DuplicateCleanupError(f"certificado ilegível: {path}") from error
    if not isinstance(value, dict):
        raise DuplicateCleanupError("certificado não contém um objeto JSON")
    if (
        value.get("schema_certificado") != CERTIFICATE_SCHEMA
        or value.get("ok") is not True
        or value.get("hashes_calculados") is not True
    ):
        raise DuplicateCleanupError(
            "a limpeza exige um certificado de integridade aprovado com hashes"
        )
    return value


def _safe_file(course_folder: Path, relative_value: object) -> Path:
    relative = Path(str(relative_value or ""))
    if not relative.parts or relative.is_absolute() or ".." in relative.parts:
        raise DuplicateCleanupError(f"caminho inseguro no certificado: {relative}")
    candidate = course_folder / relative
    if candidate.is_symlink():
        raise DuplicateCleanupError(f"link simbólico recusado: {relative}")
    path = candidate.resolve()
    if course_folder not in path.parents:
        raise DuplicateCleanupError(f"caminho fora do curso: {relative}")
    if not path.is_file() or path.is_symlink():
        raise DuplicateCleanupError(f"arquivo certificado ausente: {relative}")
    return path


def build_duplicate_cleanup_plan(
    course_folder: Path,
    *,
    quarantine_root: Path | None = None,
) -> DuplicateCleanupPlan:
    """Planeja somente extras cujo hash já existe em um arquivo canônico."""

    folder = Path(course_folder).resolve()
    if not folder.is_dir():
        raise DuplicateCleanupError(f"pasta de curso inexistente: {folder}")
    certificate = _read_certificate(folder)
    records = certificate.get("arquivos")
    if not isinstance(records, list):
        raise DuplicateCleanupError("certificado não contém a lista de arquivos")

    canonical_by_hash: dict[str, Path] = {}
    extras = []
    for record in records:
        if not isinstance(record, dict):
            raise DuplicateCleanupError("registro de arquivo inválido no certificado")
        digest = str(record.get("sha256") or "")
        if len(digest) != 64:
            raise DuplicateCleanupError("hash inválido no certificado")
        path = _safe_file(folder, record.get("caminho"))
        if record.get("origem") == "manifesto":
            canonical_by_hash.setdefault(digest, path)
        elif record.get("origem") == "extra_legado":
            extras.append((record, path, digest))
        else:
            raise DuplicateCleanupError("origem de arquivo inválida no certificado")

    root = (
        Path(quarantine_root).expanduser().resolve()
        if quarantine_root is not None
        else folder.parent / ".quarentena-duplicatas-estrategia"
    )
    quarantine_folder = root / folder.name
    if (
        root == folder
        or folder in root.parents
        or quarantine_folder == folder
        or folder in quarantine_folder.parents
    ):
        raise DuplicateCleanupError("a quarentena não pode ficar dentro do curso")

    items = []
    for record, source, digest in extras:
        canonical = canonical_by_hash.get(digest)
        if canonical is None:
            continue
        try:
            size = int(record["tamanho"])
        except (KeyError, TypeError, ValueError) as error:
            raise DuplicateCleanupError("tamanho inválido no certificado") from error
        relative = source.relative_to(folder)
        items.append(
            DuplicateExtra(
                source=source,
                canonical=canonical,
                quarantine=quarantine_folder / relative,
                size=size,
                sha256=digest,
            )
        )
    return DuplicateCleanupPlan(folder, quarantine_folder, tuple(items))


def _physical_key(path: Path) -> tuple[object, ...]:
    stat = path.stat()
    if stat.st_ino:
        return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns
    return "path", path.as_posix(), stat.st_size, stat.st_mtime_ns


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _validated_hash(path: Path, size: int, cache: dict[tuple, str]) -> str:
    try:
        if path.stat().st_size != size:
            raise DuplicateCleanupError(f"tamanho mudou desde o certificado: {path}")
        key = _physical_key(path)
        digest = cache.get(key)
        if digest is None:
            digest = _sha256(path)
            cache[key] = digest
        return digest
    except OSError as error:
        raise DuplicateCleanupError(f"não foi possível validar {path}") from error


def _rollback(moved: list[DuplicateExtra]) -> None:
    failures = []
    for item in reversed(moved):
        try:
            item.source.parent.mkdir(parents=True, exist_ok=True)
            item.quarantine.replace(item.source)
        except OSError as error:
            failures.append(f"{item.source}: {error}")
    if failures:
        raise DuplicateCleanupError(
            "a reversão da quarentena falhou: " + "; ".join(failures)
        )


def apply_duplicate_cleanup_plan(plan: DuplicateCleanupPlan) -> dict:
    """Aplica o plano, recertifica e restaura tudo se a validação falhar."""

    if not plan.items:
        return verify_course_folder(plan.course_folder)
    if plan.quarantine_folder.exists():
        raise DuplicateCleanupError(
            f"quarentena já existente: {plan.quarantine_folder}"
        )

    hash_cache: dict[tuple, str] = {}
    for item in plan.items:
        if item.quarantine.exists():
            raise DuplicateCleanupError(
                f"destino de quarentena já existe: {item.quarantine}"
            )
        source_hash = _validated_hash(item.source, item.size, hash_cache)
        canonical_hash = _validated_hash(item.canonical, item.size, hash_cache)
        if source_hash != item.sha256 or canonical_hash != item.sha256:
            raise DuplicateCleanupError(
                f"conteúdo mudou desde o certificado: {item.source}"
            )

    plan.quarantine_folder.mkdir(parents=True, exist_ok=False)
    if plan.quarantine_folder.stat().st_dev != plan.course_folder.stat().st_dev:
        plan.quarantine_folder.rmdir()
        raise DuplicateCleanupError("curso e quarentena precisam estar no mesmo volume")

    moved: list[DuplicateExtra] = []
    try:
        for item in plan.items:
            item.quarantine.parent.mkdir(parents=True, exist_ok=True)
            item.source.replace(item.quarantine)
            moved.append(item)
        report = verify_course_folder(plan.course_folder)
        if report.get("ok") is not True:
            codes = sorted(
                {
                    str(issue.get("codigo") or "desconhecido")
                    for issue in report.get("problemas", [])
                    if isinstance(issue, dict)
                }
            )
            raise DuplicateCleanupError(
                "recertificação recusou a árvore: " + ", ".join(codes)
            )
        write_certificate(plan.course_folder, report)
    except Exception:
        _rollback(moved)
        raise

    for parent in sorted(
        {item.source.parent for item in plan.items},
        key=lambda path: len(path.parts),
        reverse=True,
    ):
        current = parent
        while current != plan.course_folder:
            try:
                current.rmdir()
            except OSError:
                break
            current = current.parent
    return report
