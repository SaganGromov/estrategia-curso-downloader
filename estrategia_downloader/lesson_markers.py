"""Marcadores locais derivados do inventário autenticado de cada aula."""

from __future__ import annotations

import re
from pathlib import Path

from .utils import safe_filename

NO_VIDEOS_MARKER = "SEM_VIDEOS_NESTA_AULA.txt"
_LESSON_KEY = re.compile(r"^aula_(\d+)_posicao_(\d+)$")


def _marker_text(course_id: str, lesson_key: str, lesson_name: str) -> str:
    return (
        "SEM VÍDEOS NESTA AULA\n\n"
        "A API autenticada da área do aluno informou zero vídeos para esta "
        "aula durante a auditoria integral registrada em "
        ".inventario_estrategia.json.\n\n"
        f"Curso ID: {course_id}\n"
        f"Aula: {safe_filename(lesson_name)}\n"
        f"Chave do inventário: {lesson_key}\n\n"
        "Este marcador não representa uma falha de download. Ele será removido "
        "automaticamente se uma auditoria posterior encontrar vídeos.\n"
    )


def _requires_marker(record: dict, *, assume_legacy_full: bool) -> bool:
    videos = record.get("videos")
    audited = record.get("videos_auditados")
    if audited is None and assume_legacy_full:
        audited = True
    return (
        record.get("modo") == "api"
        and record.get("estavel") is True
        and audited is True
        and isinstance(videos, list)
        and not videos
    )


def reconcile_no_video_markers(
    course_folder: Path,
    course_id: str,
    lessons: dict,
    *,
    assume_legacy_full: bool = False,
    apply: bool = True,
) -> dict:
    """Planeja ou aplica marcadores sem confundir ausência com falha.

    Inventários antigos não registravam se vídeos haviam sido incluídos. Eles só
    podem ser tratados como auditorias integrais quando o chamador declara isso
    explicitamente por ``assume_legacy_full``.
    """

    folder = Path(course_folder)
    if not folder.is_dir():
        raise ValueError(f"pasta de curso inexistente: {folder}")
    if not isinstance(lessons, dict):
        raise ValueError("aulas do inventário não formam um objeto")

    desired: dict[Path, str] = {}
    conflicts = []
    for lesson_key, raw_record in sorted(lessons.items()):
        match = _LESSON_KEY.fullmatch(str(lesson_key))
        if match is None or not isinstance(raw_record, dict):
            continue
        if not _requires_marker(
            raw_record,
            assume_legacy_full=assume_legacy_full,
        ):
            continue
        lesson_number = int(match.group(1))
        video_folder = folder / f"aula_{lesson_number:02d}" / "videos"
        marker = video_folder / NO_VIDEOS_MARKER
        if video_folder.exists():
            other_entries = sorted(
                item.name for item in video_folder.iterdir() if item != marker
            )
            if other_entries:
                conflicts.append(marker.relative_to(folder).as_posix())
                continue
        desired[marker] = _marker_text(
            str(course_id),
            str(lesson_key),
            str(raw_record.get("nome") or f"Aula {lesson_number:02d}"),
        )

    existing = set(folder.glob(f"aula_*/videos/{NO_VIDEOS_MARKER}"))
    created = []
    updated = []
    unchanged = []
    removed = []
    for marker, contents in desired.items():
        relative = marker.relative_to(folder).as_posix()
        try:
            current = marker.read_text(encoding="utf-8") if marker.is_file() else None
        except (OSError, UnicodeError):
            current = None
        if current == contents:
            unchanged.append(relative)
            continue
        if marker.exists():
            updated.append(relative)
        else:
            created.append(relative)
        if apply:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(contents, encoding="utf-8")

    for marker in sorted(existing - set(desired)):
        relative = marker.relative_to(folder).as_posix()
        removed.append(relative)
        if apply:
            marker.unlink()

    return {
        "curso_id": str(course_id),
        "esperados": len(desired),
        "criados": created,
        "atualizados": updated,
        "inalterados": unchanged,
        "removidos": removed,
        "conflitos": conflicts,
        "aplicado": bool(apply),
    }
