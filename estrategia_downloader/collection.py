"""Coleção idempotente usada pelo modo integral de todos os cursos."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from .course_metadata import CourseSummary
from .utils import sanitizar_texto, slug_nome_curso

COLLECTION_DIRECTORY_NAME = "estrategia-cursos-completos"
COLLECTION_MARKER = ".estrategia_colecao.json"
COLLECTION_KIND = "estrategia-cursos-completos"
COLLECTION_SCHEMA = 1
COURSE_STATUS = {"pendente", "em_andamento", "completo", "incompleto"}


class CollectionError(RuntimeError):
    """A coleção local não pôde ser interpretada com segurança."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def course_folder_name(course: CourseSummary) -> str:
    """Nome determinístico: título humano e ID, sem timestamp de execução."""

    return f"{slug_nome_curso(course.name)}-id-{course.course_id}"


def resolve_collection_root(selected_folder: Path) -> Path:
    """Aceita a própria coleção ou cria uma subpasta previsível na base."""

    selected = Path(selected_folder).expanduser().resolve()
    if (selected / COLLECTION_MARKER).is_file():
        return selected
    if selected.name == COLLECTION_DIRECTORY_NAME or selected.name.startswith(
        f"{COLLECTION_DIRECTORY_NAME}-"
    ):
        return selected
    return selected / COLLECTION_DIRECTORY_NAME


def _new_state() -> dict:
    created = _now()
    return {
        "schema": COLLECTION_SCHEMA,
        "tipo": COLLECTION_KIND,
        "criado_em": created,
        "atualizado_em": created,
        "cursos": {},
    }


def _validate_state(value) -> dict:
    if not isinstance(value, Mapping):
        raise CollectionError("o marcador da coleção não é um objeto JSON")
    if value.get("schema") != COLLECTION_SCHEMA:
        raise CollectionError("a versão do marcador da coleção não é suportada")
    if value.get("tipo") != COLLECTION_KIND:
        raise CollectionError("a pasta escolhida pertence a outro tipo de coleção")
    courses = value.get("cursos")
    if not isinstance(courses, Mapping):
        raise CollectionError("o marcador da coleção não contém um catálogo válido")
    return dict(value)


def load_collection(root: Path) -> dict:
    marker = Path(root) / COLLECTION_MARKER
    if not marker.is_file():
        return _new_state()
    try:
        value = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise CollectionError("não foi possível ler o marcador da coleção") from error
    return _validate_state(value)


def save_collection(root: Path, state: dict) -> None:
    """Persiste o catálogo atomicamente, sem credenciais ou URLs de conteúdo."""

    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    value = _validate_state(state)
    value["atualizado_em"] = _now()
    marker = root / COLLECTION_MARKER
    temporary = root / f"{COLLECTION_MARKER}.tmp"
    try:
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(marker)
    except OSError as error:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise CollectionError("não foi possível gravar o marcador da coleção") from error


def open_collection(selected_folder: Path) -> tuple[Path, dict, bool]:
    """Retorna raiz, estado e se uma coleção anterior foi detectada."""

    root = resolve_collection_root(selected_folder)
    existed = (root / COLLECTION_MARKER).is_file()
    state = load_collection(root)
    if not existed:
        save_collection(root, state)
    return root, state, existed


def _recorded_folder(root: Path, state: dict, course_id: str) -> Path | None:
    record = state.get("cursos", {}).get(course_id)
    if not isinstance(record, Mapping):
        return None
    folder_name = record.get("pasta")
    expected = re.compile(
        rf"^[a-z0-9]+(?:-[a-z0-9]+)*-id-{re.escape(course_id)}$"
    )
    if (
        not isinstance(folder_name, str)
        or Path(folder_name).name != folder_name
        or not expected.fullmatch(folder_name)
    ):
        raise CollectionError(f"pasta inválida registrada para o curso {course_id}")
    candidate = root / folder_name
    return candidate if candidate.is_dir() else None


def _discovered_folders(root: Path, course_id: str) -> list[Path]:
    pattern = re.compile(rf"^[a-z0-9]+(?:-[a-z0-9]+)*-id-{re.escape(course_id)}$")
    try:
        return sorted(
            child
            for child in root.iterdir()
            if child.is_dir() and pattern.fullmatch(child.name)
        )
    except OSError as error:
        raise CollectionError("não foi possível examinar as pastas da coleção") from error


def ensure_course_folder(
    root: Path,
    state: dict,
    course: CourseSummary,
) -> Path:
    """Reutiliza a pasta do ID e a renomeia se o título canônico mudou."""

    root = Path(root)
    desired = root / course_folder_name(course)
    recorded = _recorded_folder(root, state, course.course_id)
    discovered = _discovered_folders(root, course.course_id)
    candidates = list(dict.fromkeys([item for item in (recorded, *discovered) if item]))
    if len(candidates) > 1:
        raise CollectionError(
            f"há mais de uma pasta candidata para o curso {course.course_id}"
        )

    if candidates:
        current = candidates[0]
        if current != desired:
            if desired.exists():
                raise CollectionError(
                    f"a pasta canônica do curso {course.course_id} já existe"
                )
            current.rename(desired)
    else:
        desired.mkdir(parents=True, exist_ok=False)

    courses = state.setdefault("cursos", {})
    previous = courses.get(course.course_id)
    record = dict(previous) if isinstance(previous, Mapping) else {}
    record.update(
        {
            "id": course.course_id,
            "nome": course.name,
            "pasta": desired.name,
            "status": record.get("status", "pendente"),
        }
    )
    courses[course.course_id] = record
    return desired


def update_course_status(
    state: dict,
    course: CourseSummary,
    folder: Path,
    status: str,
    *,
    summary: dict | None = None,
    error: str = "",
) -> None:
    if status not in COURSE_STATUS:
        raise ValueError(f"status de curso inválido: {status}")
    courses = state.setdefault("cursos", {})
    previous = courses.get(course.course_id)
    record = dict(previous) if isinstance(previous, Mapping) else {}
    record.update(
        {
            "id": course.course_id,
            "nome": course.name,
            "pasta": Path(folder).name,
            "status": status,
            "ultima_auditoria": _now(),
            "resumo": summary or {},
        }
    )
    if error:
        record["erro"] = sanitizar_texto(error)[:500]
    else:
        record.pop("erro", None)
    courses[course.course_id] = record
