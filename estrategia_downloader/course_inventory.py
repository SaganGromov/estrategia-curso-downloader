"""Inventário canônico de aulas e recursos obtido pela API da área do aluno."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import requests

from .config import HTTP_CONNECT_TIMEOUT, HTTP_READ_TIMEOUT
from .course_metadata import (
    API_BASE_URL,
    COURSE_ENDPOINT,
    CourseAccessError,
    CourseMetadataError,
    CourseNotFoundError,
)
from .utils import chave_deduplicacao_url, safe_filename

LESSON_ENDPOINT = API_BASE_URL + "/api/aluno/aula/{lesson_id}"
_DASHBOARD_LESSON_URL = (
    "https://www.estrategiaconcursos.com.br/app/dashboard/cursos/"
    "{course_id}/aulas/{lesson_id}"
)
_LESSON_RESOURCE_FIELDS = (
    (
        "pdf_simplificado",
        "pdf",
        "Baixar Livro Eletrônico versão simplificada novo",
        ".pdf",
    ),
    ("pdf", "pdf", "Baixar Livro Eletrônico versão original", ".pdf"),
    (
        "pdf_grifado",
        "pdf",
        "Baixar Livro Eletrônico marcação dos aprovados",
        ".pdf",
    ),
    ("resumo", "pdf", "Baixar Resumo", ".pdf"),
    ("slide", "slides", "Baixar Slides", ".pdf"),
    ("mapa_mental", "mapa_mental", "Baixar Mapa Mental", ".pdf"),
    ("audio", "material", "Áudio da aula", ".mp3"),
    ("thumbnail", "material", "Imagem da aula", ".jpg"),
)


class CourseInventoryError(CourseMetadataError):
    """A API respondeu, mas seu inventário não pôde ser provado completo."""


@dataclass(frozen=True, slots=True)
class CourseLesson:
    lesson_id: str
    position: int
    number: int
    name: str
    href: str
    release_date: date | None = None
    summary_resources: tuple[tuple[str, str, str, str, str], ...] = field(
        default=(),
        repr=False,
        compare=False,
    )


@dataclass(frozen=True, slots=True)
class CourseSnapshot:
    course_id: str
    title: str
    total_lessons: int
    lessons: tuple[CourseLesson, ...]
    future_release_dates: tuple[date, ...] = ()
    unexpected_url_fields: tuple[str, ...] = ()
    unresolved: tuple[str, ...] = ()
    ignored_ui_url_fields: tuple[str, ...] = ()
    lesson_summary_schema: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LessonSnapshot:
    lesson: CourseLesson
    materials: tuple[dict, ...]
    videos: tuple[dict, ...]
    video_identities: tuple[tuple[str, int, str], ...]
    unresolved: tuple[str, ...]
    unexpected_url_fields: tuple[str, ...]


def _numeric_id(value, description: str) -> str:
    text = str(value)
    if not re.fullmatch(r"\d+", text):
        raise CourseInventoryError(f"{description} não contém um ID numérico")
    return text


def _mapping_data(payload, description: str) -> Mapping:
    if not isinstance(payload, Mapping):
        raise CourseInventoryError(f"{description} não é um objeto JSON")
    data = payload.get("data")
    if not isinstance(data, Mapping):
        raise CourseInventoryError(f"{description} não contém data")
    return data


def _text(record: Mapping, *names: str) -> str:
    for name in names:
        value = record.get(name)
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())
    return ""


def _type_name(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, Mapping):
        return "object"
    if isinstance(value, list):
        return "list"
    if isinstance(value, str):
        return "string"
    if isinstance(value, (int, float)):
        return "number"
    return type(value).__name__


def _lesson_number(name: str, record: Mapping) -> int | None:
    match = re.search(r"\baula\s+(\d+)\b", name, flags=re.IGNORECASE)
    if match:
        return int(match.group(1))
    match = re.match(r"\s*(\d+)\b", name)
    if match:
        return int(match.group(1))
    for key in ("numero", "num", "numero_aula"):
        value = record.get(key)
        if isinstance(value, int) and value >= 0:
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return None


def _assign_lesson_numbers(candidates: list[int | None]) -> list[int]:
    assigned = []
    used = set()
    next_number = 0
    for candidate in candidates:
        number = candidate
        if number is None or number in used:
            while next_number in used:
                next_number += 1
            number = next_number
        assigned.append(number)
        used.add(number)
        next_number = max(next_number, number + 1)
    return assigned


_EXPLICIT_RELEASE_RE = re.compile(
    r"\bDispon(?:í|i)vel\s+em\s+([0-3]\d/[01]\d/\d{4})\b",
    flags=re.IGNORECASE,
)


def _release_dates(value) -> tuple[date, ...]:
    found = set()

    def visit(item) -> None:
        if isinstance(item, Mapping):
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)
        elif isinstance(item, str):
            for raw in _EXPLICIT_RELEASE_RE.findall(item):
                try:
                    parsed = datetime.strptime(raw, "%d/%m/%Y").date()
                except ValueError:
                    continue
                found.add(parsed)

    visit(value)
    return tuple(sorted(found))


def _future_release_dates(value, *, today: date | None = None) -> tuple[date, ...]:
    reference = today or date.today()
    return tuple(value for value in _release_dates(value) if value > reference)


def extract_course_snapshot(
    payload,
    course_id: str,
    *,
    today: date | None = None,
) -> CourseSnapshot:
    """Converte a resposta de curso em uma lista finita e verificável de aulas."""

    course_id = _numeric_id(course_id, "course_id")
    data = _mapping_data(payload, "o inventário do curso")
    returned_id = data.get("id")
    if returned_id is not None and str(returned_id) != course_id:
        raise CourseInventoryError("a API devolveu um curso diferente do solicitado")

    title = data.get("nome")
    if not isinstance(title, str) or not title.strip():
        raise CourseInventoryError("o inventário do curso não contém data.nome")

    raw_lessons = data.get("aulas")
    if raw_lessons is None and data.get("total_aulas") in {0, "0"}:
        raw_lessons = []
    if not isinstance(raw_lessons, list):
        raise CourseInventoryError("o inventário do curso não contém data.aulas")

    total = data.get("total_aulas")
    if isinstance(total, str) and total.isdigit():
        total = int(total)
    if not isinstance(total, int) or total < 0:
        raise CourseInventoryError("o inventário do curso não contém total_aulas válido")
    if total != len(raw_lessons):
        raise CourseInventoryError(
            "total_aulas diverge da quantidade de objetos em data.aulas "
            f"({total} != {len(raw_lessons)})"
        )

    records = []
    number_candidates = []
    seen_ids = set()
    consumed_url_fields = set()
    unresolved = []
    for position, record in enumerate(raw_lessons, start=1):
        if not isinstance(record, Mapping):
            raise CourseInventoryError(
                f"data.aulas[{position - 1}] não é um objeto"
            )
        lesson_id = _numeric_id(
            record.get("id", record.get("aula_id")),
            f"data.aulas[{position - 1}]",
        )
        if lesson_id in seen_ids:
            raise CourseInventoryError(f"a aula {lesson_id} aparece mais de uma vez")
        seen_ids.add(lesson_id)
        heading = _text(record, "nome", "titulo", "nome_aula")
        description = _text(record, "descricao", "ementa", "conteudo")
        if heading and description and description.casefold() not in heading.casefold():
            name = f"{heading}\n{description}"
        else:
            name = heading or description or f"Aula {position - 1:02d}"
        release_dates = _release_dates(record)
        if len(release_dates) > 1:
            raise CourseInventoryError(
                f"data.aulas[{position - 1}] contém datas de liberação conflitantes"
            )
        release_date = release_dates[0] if release_dates else None
        summary_resources = []
        for (
            field_name,
            kind,
            resource_title,
            fallback,
        ) in _LESSON_RESOURCE_FIELDS:
            resource_path = f"$.data.aulas[{position - 1}].{field_name}"
            raw_resource = record.get(field_name)
            resource_url = _http_url(raw_resource)
            if resource_url:
                consumed_url_fields.add(resource_path)
                summary_resources.append(
                    (field_name, kind, resource_title, fallback, resource_url)
                )
            elif (
                isinstance(raw_resource, str)
                and raw_resource.strip()
                and field_name != "resumo"
            ):
                unresolved.append(
                    f"{resource_path}: valor não contém URL HTTP utilizável"
                )
        records.append(
            (
                lesson_id,
                position,
                name,
                release_date,
                tuple(summary_resources),
            )
        )
        number_candidates.append(_lesson_number(name, record))

    numbers = _assign_lesson_numbers(number_candidates)
    lessons = tuple(
        CourseLesson(
            lesson_id=lesson_id,
            position=position,
            number=number,
            name=name,
            href=_DASHBOARD_LESSON_URL.format(
                course_id=course_id,
                lesson_id=lesson_id,
            ),
            release_date=release_date,
            summary_resources=summary_resources,
        )
        for (
            lesson_id,
            position,
            name,
            release_date,
            summary_resources,
        ), number in zip(
            records,
            numbers,
        )
    )
    all_url_fields = tuple(_iter_url_fields(data))
    ignored_ui_url_fields = tuple(
        sorted(
            path
            for path, _url in all_url_fields
            if path == "$.data.icone"
            or re.fullmatch(r"\$\.data\.professores\[\d+\]\.imagem", path)
        )
    )
    ignored_paths = set(ignored_ui_url_fields)
    return CourseSnapshot(
        course_id=course_id,
        title=title,
        total_lessons=total,
        lessons=lessons,
        future_release_dates=_future_release_dates(data, today=today),
        unexpected_url_fields=tuple(
            sorted(
                path
                for path, _url in all_url_fields
                if path not in consumed_url_fields and path not in ignored_paths
            )
        ),
        unresolved=tuple(unresolved),
        ignored_ui_url_fields=ignored_ui_url_fields,
        lesson_summary_schema=tuple(
            sorted(
                {
                    f"{key}:{_type_name(value)}"
                    for record in raw_lessons
                    for key, value in record.items()
                }
            )
        ),
    )


def _http_url(value) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    candidate = value.strip()
    if candidate.startswith("/"):
        candidate = urljoin(API_BASE_URL, candidate)
    parsed = urlparse(candidate)
    return candidate if parsed.scheme in {"http", "https"} and parsed.netloc else ""


def _extension(url: str, fallback: str) -> str:
    suffix = Path(unquote(urlparse(url).path)).suffix.lower()
    allowed = {
        ".pdf",
        ".ppt",
        ".pptx",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".zip",
        ".rar",
        ".png",
        ".jpg",
        ".jpeg",
        ".mp3",
        ".m4a",
        ".aac",
        ".ogg",
        ".mp4",
        ".webm",
        ".vtt",
        ".srt",
    }
    return suffix if suffix in allowed else fallback


def _resolution(label) -> int:
    values = re.findall(r"\d{3,4}", str(label))
    return max((int(value) for value in values), default=0)


def _iter_url_fields(value, path="$.data"):
    if isinstance(value, Mapping):
        for key, child in value.items():
            yield from _iter_url_fields(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _iter_url_fields(child, f"{path}[{index}]")
    elif _http_url(value):
        yield path, value


def extract_lesson_snapshot(payload, lesson: CourseLesson) -> LessonSnapshot:
    """Extrai uma única resposta canônica de aula, sem depender do DOM."""

    data = _mapping_data(payload, f"o inventário da aula {lesson.lesson_id}")
    returned_id = data.get("id")
    if returned_id is not None and str(returned_id) != lesson.lesson_id:
        raise CourseInventoryError("a API devolveu uma aula diferente da solicitada")

    materials = []
    videos = []
    video_identities = []
    unresolved = []
    consumed_paths = set()
    seen_material_urls = set()

    def add_material(path, value, kind, title, fallback):
        url = _http_url(value)
        if not url:
            if isinstance(value, str) and value.strip() and path.rsplit(".", 1)[-1] != "resumo":
                unresolved.append(f"{path}: valor não contém URL HTTP utilizável")
            return
        consumed_paths.add(path)
        key = chave_deduplicacao_url(url)
        if key in seen_material_urls:
            return
        seen_material_urls.add(key)
        materials.append(
            {
                "tipo": kind,
                "aula_num": lesson.number,
                "aula_nome": safe_filename(lesson.name),
                "item_num": len(materials) + 1,
                "titulo": safe_filename(title),
                "extensao": _extension(url, fallback),
                "url": url,
            }
        )

    for field_name, kind, title, fallback in _LESSON_RESOURCE_FIELDS:
        add_material(
            f"$.data.{field_name}",
            data.get(field_name),
            kind,
            title,
            fallback,
        )
    for field_name, kind, title, fallback, url in lesson.summary_resources:
        add_material(
            f"$.course.aulas.{field_name}",
            url,
            kind,
            title,
            fallback,
        )

    raw_videos = data.get("videos")
    if not isinstance(raw_videos, list):
        raise CourseInventoryError("o inventário da aula não contém data.videos")

    seen_video_ids = set()
    for position, record in enumerate(raw_videos, start=1):
        path = f"$.data.videos[{position - 1}]"
        if not isinstance(record, Mapping):
            raise CourseInventoryError(f"{path} não é um objeto")
        video_id = _numeric_id(record.get("id"), path)
        if video_id in seen_video_ids:
            raise CourseInventoryError(f"o vídeo {video_id} aparece mais de uma vez")
        seen_video_ids.add(video_id)
        title = _text(record, "titulo", "nome") or f"Vídeo {position:02d}"
        resolutions = record.get("resolucoes")
        if not isinstance(resolutions, Mapping):
            unresolved.append(f"{path}.resolucoes: mapa de qualidades ausente")
            resolutions = {}
        options = []
        for label, value in resolutions.items():
            option_url = _http_url(value)
            if option_url:
                consumed_paths.add(f"{path}.resolucoes.{label}")
                options.append((_resolution(label), str(label), option_url))
        if not options:
            unresolved.append(f"{path}: nenhum link de vídeo utilizável")
        else:
            _height, _label, url = max(options, key=lambda item: (item[0], item[1]))
            videos.append(
                {
                    "tipo": "video",
                    "aula_num": lesson.number,
                    "aula_nome": safe_filename(lesson.name),
                    "item_num": position,
                    "titulo": safe_filename(title),
                    "extensao": _extension(url, ".mp4"),
                    "url": url,
                }
            )
            video_identities.append((f"id={video_id}", position, title))

        per_video_fields = (
            ("resumo", "pdf", f"Baixar Resumo - {title}", ".pdf"),
            ("slide", "slides", f"Baixar Slides - {title}", ".pdf"),
            ("mapa_mental", "mapa_mental", f"Baixar Mapa Mental - {title}", ".pdf"),
            ("audio", "material", f"Áudio - {title}", ".mp3"),
            ("thumbnail", "material", f"Imagem - {title}", ".jpg"),
        )
        for field, kind, material_title, fallback in per_video_fields:
            add_material(
                f"{path}.{field}",
                record.get(field),
                kind,
                material_title,
                fallback,
            )

    unexpected = sorted(
        path
        for path, _url in _iter_url_fields(data)
        if path not in consumed_paths
    )
    return LessonSnapshot(
        lesson=lesson,
        materials=tuple(materials),
        videos=tuple(videos),
        video_identities=tuple(video_identities),
        unresolved=tuple(dict.fromkeys(unresolved)),
        unexpected_url_fields=tuple(unexpected),
    )


def _get_json(
    session: requests.Session,
    url: str,
    description: str,
    *,
    timeout=(HTTP_CONNECT_TIMEOUT, HTTP_READ_TIMEOUT),
):
    try:
        response = session.get(url, timeout=timeout)
    except requests.RequestException as error:
        raise CourseInventoryError(f"falha de rede ao consultar {description}") from error
    if response.status_code in {401, 403}:
        raise CourseAccessError(
            f"a API recusou {description} (HTTP {response.status_code})"
        )
    if response.status_code == 404:
        raise CourseNotFoundError(f"{description} não encontrado (HTTP 404)")
    try:
        response.raise_for_status()
        return response.json()
    except requests.RequestException as error:
        raise CourseInventoryError(
            f"a API falhou ao consultar {description} (HTTP {response.status_code})"
        ) from error
    except ValueError as error:
        raise CourseInventoryError(f"a API não devolveu JSON válido para {description}") from error


def get_course_snapshot(
    session: requests.Session,
    course_id: str,
    *,
    timeout=(HTTP_CONNECT_TIMEOUT, HTTP_READ_TIMEOUT),
    today: date | None = None,
) -> CourseSnapshot:
    course_id = _numeric_id(course_id, "course_id")
    payload = _get_json(
        session,
        COURSE_ENDPOINT.format(course_id=course_id),
        f"o curso {course_id}",
        timeout=timeout,
    )
    return extract_course_snapshot(payload, course_id, today=today)


def get_lesson_snapshot(
    session: requests.Session,
    lesson: CourseLesson,
    *,
    timeout=(HTTP_CONNECT_TIMEOUT, HTTP_READ_TIMEOUT),
) -> LessonSnapshot:
    payload = _get_json(
        session,
        LESSON_ENDPOINT.format(lesson_id=lesson.lesson_id),
        f"a aula {lesson.lesson_id}",
        timeout=timeout,
    )
    return extract_lesson_snapshot(payload, lesson)
