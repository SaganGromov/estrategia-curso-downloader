"""Certificação offline dos arquivos enumerados pelo inventário da API."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from pathlib import Path

from .integrity import AUDIT_VERSION, INVENTORY_FILE, INVENTORY_SCHEMA
from .lesson_markers import NO_VIDEOS_MARKER, reconcile_no_video_markers
from .utils import safe_filename

CERTIFICATE_FILE = ".certificado_integridade_estrategia.json"
CERTIFICATE_SCHEMA = 1
STATE_FILE = ".estado_estrategia.json"
LINK_MANIFEST = "links_estrategia_conteudo.txt"

_CONTROL_FILES = {
    CERTIFICATE_FILE,
    INVENTORY_FILE,
    STATE_FILE,
    LINK_MANIFEST,
    NO_VIDEOS_MARKER,
}
_TYPE_LAYOUT = {
    "video": ("Vídeo", ("videos",)),
    "pdf": ("PDF", ("pdfs",)),
    "slides": ("Slides", ("pdfs",)),
    "mapa_mental": ("Mapa Mental", ("pdfs",)),
    # A extensão real de um material é descoberta pela resposta HTTP. Em
    # especial, um material PDF termina em pdfs; áudio/imagem termina em
    # outros_materiais.
    "material": ("Material", ("outros_materiais", "pdfs")),
}
_MEDIA_EXTENSIONS = {".mp3", ".mp4", ".m4a", ".webm", ".ogg"}
_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}


def _read_object(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _is_transient(path: Path) -> bool:
    name = path.name
    return (
        name.endswith(".part")
        or name.endswith(".part.json")
        or name.endswith(".reutilizando")
        or ".pre-auditoria-" in name
        or name.endswith(".migracao.tmp")
        or name == f"{INVENTORY_FILE}.tmp"
        or name == f"{CERTIFICATE_FILE}.tmp"
    )


def _resource_base(record: dict) -> tuple[str, tuple[str, ...]] | None:
    resource_type = str(record.get("tipo") or "")
    layout = _TYPE_LAYOUT.get(resource_type)
    try:
        number = int(record["numero"])
    except (KeyError, TypeError, ValueError):
        return None
    title = safe_filename(str(record.get("titulo") or ""))
    if layout is None or number < 0 or not title:
        return None
    prefix, directories = layout
    return safe_filename(f"{prefix} {number:02d} - {title}"), directories


def _matching_files(
    file_index: dict[tuple[int, str, str], list[Path]],
    lesson_number: int,
    record: dict,
) -> list[Path]:
    layout = _resource_base(record)
    if layout is None:
        return []
    base, directories = layout
    matches = []
    for directory_name in directories:
        matches.extend(
            file_index.get((lesson_number, directory_name, base.casefold()), ())
        )
    return sorted(set(matches))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _physical_identity(path: Path, stat) -> tuple:
    """Agrupa hard links sem confiar em inode zero de alguns filesystems."""

    if stat.st_ino:
        return stat.st_dev, stat.st_ino
    return "path", path.as_posix()


def _structure_command(path: Path) -> list[str] | None:
    extension = path.suffix.casefold()
    if extension == ".pdf":
        return ["pdfinfo", str(path)]
    if extension in _MEDIA_EXTENSIONS:
        return [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ]
    if extension in _IMAGE_EXTENSIONS:
        return [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-f",
            "null",
            "-",
        ]
    return None


def _verify_structure(path: Path) -> str | None:
    command = _structure_command(path)
    if command is None:
        return None
    if shutil.which(command[0]) is None:
        return f"ferramenta estrutural ausente: {command[0]}"
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return f"validação estrutural falhou: {error}"
    if result.returncode != 0:
        detail = " ".join((result.stderr or result.stdout).split())[:240]
        return f"conteúdo estruturalmente inválido: {detail or result.returncode}"
    if path.suffix.casefold() in _MEDIA_EXTENSIONS:
        try:
            if float(result.stdout.strip()) <= 0:
                return "mídia sem duração positiva"
        except ValueError:
            return "mídia sem duração legível"
    return None


def _issue(code: str, description: str, path: Path | None = None) -> dict:
    value = {"codigo": code, "descricao": description}
    if path is not None:
        value["caminho"] = path.as_posix()
    return value


def verify_course_folder(
    course_folder: Path,
    *,
    calculate_hashes: bool = True,
    verify_structure: bool = True,
    structure_workers: int = 4,
) -> dict:
    """Compara um curso concluído com seu manifesto, sem acessar a rede."""

    folder = Path(course_folder).resolve()
    issues: list[dict] = []
    inventory = _read_object(folder / INVENTORY_FILE)
    state = _read_object(folder / STATE_FILE)

    if inventory.get("schema") != INVENTORY_SCHEMA:
        issues.append(_issue("inventory_schema", "schema do inventário incompatível"))
    if inventory.get("versao_auditoria") != AUDIT_VERSION:
        issues.append(
            _issue("audit_version", "versão da auditoria não é a versão atual")
        )
    inventory_status = inventory.get("status")
    scheduled = inventory_status == "aguardando_liberacao"
    if inventory_status not in {"completo", "aguardando_liberacao"}:
        issues.append(
            _issue("inventory_status", "inventário não possui um estado final")
        )
    expected_state = "aguardando_liberacao" if scheduled else "concluido"
    if state.get("status") != expected_state:
        issues.append(
            _issue("state_status", "estado do curso diverge do inventário")
        )

    course_id = str(inventory.get("curso_id") or state.get("curso_id") or "")
    if not course_id or (
        inventory.get("curso_id") is not None
        and state.get("curso_id") is not None
        and str(inventory["curso_id"]) != str(state["curso_id"])
    ):
        issues.append(_issue("course_id", "IDs de curso ausentes ou divergentes"))

    lessons = inventory.get("aulas")
    if not isinstance(lessons, dict):
        lessons = {}
        issues.append(_issue("lessons", "mapa de aulas ausente ou inválido"))

    expected_records: list[tuple[str, int, dict]] = []
    confirmed_lessons = 0
    scheduled_lessons = 0
    scheduled_dates: list[str] = []
    for lesson_key, lesson in sorted(lessons.items()):
        parts = str(lesson_key).split("_")
        try:
            lesson_number = int(parts[1]) if len(parts) >= 2 else -1
        except ValueError:
            lesson_number = -1
        if lesson_number < 0:
            issues.append(
                _issue("lesson_key", f"chave de aula inválida: {lesson_key}")
            )
            continue
        if not isinstance(lesson, dict):
            issues.append(_issue("lesson", f"aula inválida: {lesson_key}"))
            continue
        lesson_mode = lesson.get("modo")
        lesson_passes = lesson.get("passagens")
        if lesson_mode == "api" and lesson_passes == 1:
            confirmed_lessons += 1
        elif scheduled and lesson_mode in {
            "aguardando_liberacao",
            "resumo_api_aguardando_liberacao",
        }:
            scheduled_lessons += 1
            release = lesson.get("liberacao")
            try:
                release_date = date.fromisoformat(str(release))
            except ValueError:
                release_date = None
            if release_date is None or release_date <= date.today():
                issues.append(
                    _issue(
                        "scheduled_release",
                        f"{lesson_key} não possui liberação futura válida",
                    )
                )
            else:
                scheduled_dates.append(release_date.isoformat())
            expected_passes = (
                0 if lesson_mode == "aguardando_liberacao" else 1
            )
            if lesson_passes != expected_passes:
                issues.append(
                    _issue(
                        "lesson_mode",
                        f"{lesson_key} possui passagens incompatíveis com seu modo",
                    )
                )
        else:
            issues.append(
                _issue(
                    "lesson_mode",
                    f"{lesson_key} não foi enumerada por uma passagem da API",
                )
            )
        if lesson.get("estavel") is not True:
            issues.append(
                _issue("lesson_unstable", f"{lesson_key} possui campos não resolvidos")
            )
        records = lesson.get("arquivos")
        if not isinstance(records, list):
            issues.append(
                _issue("lesson_files", f"{lesson_key} não possui lista de arquivos")
            )
            continue
        if lesson_mode == "aguardando_liberacao" and records:
            issues.append(
                _issue(
                    "scheduled_files",
                    f"{lesson_key} possui arquivos antes da liberação",
                )
            )
        for record in records:
            if not isinstance(record, dict) or _resource_base(record) is None:
                issues.append(
                    _issue("resource_record", f"registro inválido em {lesson_key}")
                )
                continue
            expected_records.append((str(lesson_key), lesson_number, record))

    summary = state.get("resumo") if isinstance(state.get("resumo"), dict) else {}
    if summary.get("falhas") != 0 or summary.get("falhas_descoberta") != 0:
        issues.append(_issue("failures", "estado registra falhas de download/descoberta"))
    if summary.get("ocorrencias_pendentes") != 0:
        issues.append(_issue("pending", "estado registra ocorrências pendentes"))
    if summary.get("versao_auditoria") != AUDIT_VERSION:
        issues.append(_issue("state_audit_version", "estado não confirma auditoria atual"))

    identities = {
        str(record.get("identidade"))
        for _, _, record in expected_records
        if record.get("identidade")
    }
    expected_occurrences = len(expected_records)
    if summary.get("ocorrencias_confirmadas") != expected_occurrences:
        issues.append(
            _issue(
                "occurrence_count",
                "contagem de ocorrências do estado diverge do inventário",
            )
        )
    if summary.get("recursos_unicos_manifesto") != len(identities):
        issues.append(
            _issue(
                "unique_count",
                "contagem de recursos únicos diverge das identidades do inventário",
            )
        )
    if summary.get("aulas_confirmadas") != confirmed_lessons:
        issues.append(
            _issue("lesson_count", "contagem de aulas confirmadas diverge do inventário")
        )
    if summary.get("aulas_aguardando_liberacao", 0) != scheduled_lessons:
        issues.append(
            _issue(
                "scheduled_lesson_count",
                "contagem de aulas futuras diverge do inventário",
            )
        )
    if confirmed_lessons + scheduled_lessons != len(lessons):
        issues.append(
            _issue("total_lesson_count", "nem todas as aulas possuem estado final")
        )
    metadata = inventory.get("metadados")
    if scheduled:
        if not isinstance(metadata, dict):
            issues.append(
                _issue("scheduled_metadata", "metadados de liberação ausentes")
            )
        else:
            declared_dates = metadata.get("liberacoes_futuras")
            if declared_dates != sorted(set(scheduled_dates)):
                issues.append(
                    _issue(
                        "scheduled_dates",
                        "datas futuras do inventário divergem das aulas",
                    )
                )

    all_files = sorted(path for path in folder.rglob("*") if path.is_file())
    transients = [path for path in all_files if _is_transient(path)]
    for path in transients:
        issues.append(_issue("transient_file", "arquivo transitório presente", path))
    content_files = [
        path
        for path in all_files
        if path.name not in _CONTROL_FILES and not _is_transient(path)
    ]
    marker_report = reconcile_no_video_markers(
        folder,
        course_id,
        lessons,
        # Inventários finais da auditoria v4 anteriores à introdução de
        # ``videos_auditados`` foram produzidos por passagens integrais da API.
        # O verificador já rejeita acima qualquer aula API instável ou com
        # número de passagens diferente de um, portanto essa compatibilidade
        # não transforma inventários reduzidos em evidência de ausência.
        assume_legacy_full=True,
        apply=False,
    )
    for path in marker_report["criados"]:
        issues.append(
            _issue("missing_no_video_marker", "marcador de aula sem vídeos ausente", folder / path)
        )
    for path in marker_report["atualizados"]:
        issues.append(
            _issue("invalid_no_video_marker", "marcador de aula sem vídeos divergente", folder / path)
        )
    for path in marker_report["removidos"]:
        issues.append(
            _issue("stale_no_video_marker", "marcador de aula sem vídeos obsoleto", folder / path)
        )
    for path in marker_report["conflitos"]:
        issues.append(
            _issue("no_video_marker_conflict", "inventário sem vídeos conflita com conteúdo da pasta", folder / path)
        )
    file_index: dict[tuple[int, str, str], list[Path]] = {}
    for path in content_files:
        relative_parts = path.relative_to(folder).parts
        if len(relative_parts) != 3 or not relative_parts[0].startswith("aula_"):
            continue
        try:
            lesson_number = int(relative_parts[0][len("aula_") :])
        except ValueError:
            continue
        key = (lesson_number, relative_parts[1], path.stem.casefold())
        file_index.setdefault(key, []).append(path)

    matched: set[Path] = set()
    for lesson_key, lesson_number, record in expected_records:
        candidates = _matching_files(file_index, lesson_number, record)
        if not candidates:
            issues.append(
                _issue(
                    "missing_resource",
                    f"recurso {record.get('numero')} de {lesson_key} não localizado",
                )
            )
            continue
        if len(candidates) > 1:
            issues.append(
                _issue(
                    "ambiguous_resource",
                    f"recurso {record.get('numero')} de {lesson_key} possui "
                    f"{len(candidates)} candidatos",
                )
            )
            continue
        candidate = candidates[0]
        try:
            if candidate.stat().st_size <= 0:
                issues.append(_issue("empty_resource", "arquivo vazio", candidate))
                continue
        except OSError as error:
            issues.append(_issue("unreadable_resource", str(error), candidate))
            continue
        matched.add(candidate)

    structure_errors: dict[Path, str] = {}
    if verify_structure:
        unique_physical: dict[tuple, Path] = {}
        for path in content_files:
            stat = path.stat()
            unique_physical.setdefault(_physical_identity(path, stat), path)
        with ThreadPoolExecutor(max_workers=max(1, int(structure_workers))) as pool:
            results = pool.map(_verify_structure, unique_physical.values())
            for path, error in zip(unique_physical.values(), results, strict=True):
                if error:
                    structure_errors[path] = error
        for path, error in structure_errors.items():
            issues.append(_issue("structure", error, path))

    should_calculate_hashes = bool(calculate_hashes and not issues)
    hash_cache: dict[tuple, str] = {}
    file_records = []
    total_bytes = 0
    for path in content_files:
        try:
            stat = path.stat()
            total_bytes += stat.st_size
            identity = _physical_identity(path, stat)
            digest = ""
            if should_calculate_hashes:
                digest = hash_cache.get(identity, "")
                if not digest:
                    digest = _sha256(path)
                    hash_cache[identity] = digest
            file_records.append(
                {
                    "caminho": path.relative_to(folder).as_posix(),
                    "tamanho": stat.st_size,
                    "sha256": digest,
                    "origem": "manifesto" if path in matched else "extra_legado",
                }
            )
        except OSError as error:
            issues.append(_issue("unreadable_file", str(error), path))

    return {
        "schema_certificado": CERTIFICATE_SCHEMA,
        "curso_id": course_id,
        "versao_auditoria": inventory.get("versao_auditoria"),
        "status_inventario": inventory_status,
        "inventario_atualizado_em": inventory.get("atualizado_em"),
        "verificado_em": datetime.now(UTC).isoformat(),
        "ok": not issues,
        "recursos_unicos": len(identities),
        "aulas_confirmadas": confirmed_lessons,
        "aulas_aguardando_liberacao": scheduled_lessons,
        "liberacoes_futuras": sorted(set(scheduled_dates)),
        "ocorrencias_manifesto": expected_occurrences,
        "ocorrencias_localizadas": len(matched),
        "arquivos_fisicos": len(content_files),
        "extras_legados": len(set(content_files) - matched),
        "bytes_logicos": total_bytes,
        "hashes_calculados": should_calculate_hashes,
        "estrutura_verificada": bool(verify_structure),
        "problemas": issues,
        "arquivos": file_records,
    }


def write_certificate(course_folder: Path, report: dict) -> Path:
    """Grava atomicamente um relatório aprovado e deliberadamente sem URLs."""

    if report.get("ok") is not True or report.get("hashes_calculados") is not True:
        raise ValueError(
            "somente um relatório aprovado com hashes pode ser certificado"
        )
    folder = Path(course_folder).resolve()
    destination = folder / CERTIFICATE_FILE
    temporary = folder / f"{CERTIFICATE_FILE}.tmp"
    try:
        temporary.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(destination)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise
    return destination
