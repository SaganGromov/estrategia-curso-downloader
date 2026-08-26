"""Manifesto seguro e identidades usadas pela auditoria exaustiva."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from .utils import chave_deduplicacao_url, safe_filename

INVENTORY_FILE = ".inventario_estrategia.json"
INVENTORY_SCHEMA = 1
AUDIT_VERSION = 2


def resource_key(url: str) -> str:
    """Identidade em memória, removendo parâmetros transitórios da URL."""

    return chave_deduplicacao_url(url)


def fingerprint(value: str) -> str:
    """Hash persistível que não revela a URL/ID opaco usado como entrada."""

    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def safe_resource_record(item: dict) -> dict:
    """Representação persistível de um recurso, deliberadamente sem URL."""

    return {
        "identidade": fingerprint(resource_key(item["url"])),
        "tipo": str(item["tipo"]),
        "numero": int(item["item_num"]),
        "titulo": safe_filename(item["titulo"]),
    }


def safe_video_record(identity: str, position: int, title: str) -> dict:
    """Representação segura de uma entrada da lista de vídeos da SPA."""

    return {
        "identidade": fingerprint(identity),
        "numero": int(position),
        "titulo": safe_filename(title),
    }


def load_inventory_lessons(folder: Path, course_id: str) -> dict:
    """Recupera checkpoints compatíveis sem confiar neles como conclusão."""

    source = Path(folder) / INVENTORY_FILE
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    if not isinstance(value, dict):
        return {}
    if value.get("schema") != INVENTORY_SCHEMA:
        return {}
    if value.get("versao_auditoria") != AUDIT_VERSION:
        return {}
    if str(value.get("curso_id")) != str(course_id):
        return {}
    lessons = value.get("aulas")
    if not isinstance(lessons, dict):
        return {}
    return {
        str(key): lesson
        for key, lesson in lessons.items()
        if isinstance(key, str) and isinstance(lesson, dict)
    }


def save_inventory(
    folder: Path,
    course_id: str,
    status: str,
    lessons: dict,
    *,
    metadata: dict | None = None,
) -> None:
    """Grava atomicamente o inventário sem cookies, tokens ou URLs."""

    value = {
        "schema": INVENTORY_SCHEMA,
        "versao_auditoria": AUDIT_VERSION,
        "curso_id": str(course_id),
        "status": str(status),
        "atualizado_em": datetime.now(UTC).isoformat(),
        "aulas": lessons,
    }
    if metadata:
        value["metadados"] = metadata
    destination = Path(folder) / INVENTORY_FILE
    temporary = Path(folder) / f"{INVENTORY_FILE}.tmp"
    try:
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(destination)
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise
