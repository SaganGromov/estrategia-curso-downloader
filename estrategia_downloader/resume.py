import json
import re
from datetime import UTC, datetime
from pathlib import Path

ARQUIVO_ESTADO = ".estado_estrategia.json"
STATUS_RETOMAVEIS = {"em_andamento", "incompleto"}


def _ler_estado(pasta: Path) -> dict | None:
    caminho = pasta / ARQUIVO_ESTADO
    if not caminho.is_file():
        return None
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return dados if isinstance(dados, dict) else None


def _parece_execucao_legada(pasta: Path) -> bool:
    if (pasta / "links_estrategia_conteudo.txt").is_file():
        return True
    try:
        return any(
            item.is_dir() and re.fullmatch(r"aula_\d+", item.name)
            for item in pasta.iterdir()
        )
    except OSError:
        return False


def localizar_pasta_retomavel(pasta_base: Path, curso_id: str) -> Path | None:
    """Localiza a execução incompleta mais recente do mesmo curso."""
    curso_id_escapado = re.escape(str(curso_id))
    padrao_legado = re.compile(rf"^CURSO_ESTRATEGIA_{curso_id_escapado}_(\d+)$")
    padrao_descritivo = re.compile(
        rf"^[a-z0-9]+(?:-[a-z0-9]+)*-id-{curso_id_escapado}-(\d+)$"
    )
    candidatas = []
    try:
        itens = list(pasta_base.iterdir())
    except OSError:
        return None

    for pasta in itens:
        correspondencia = padrao_descritivo.fullmatch(pasta.name)
        pasta_legada = False
        if correspondencia is None:
            correspondencia = padrao_legado.fullmatch(pasta.name)
            pasta_legada = correspondencia is not None
        if not correspondencia or not pasta.is_dir():
            continue
        estado = _ler_estado(pasta)
        if estado is not None:
            if str(estado.get("curso_id")) != str(curso_id):
                continue
            if estado.get("status") not in STATUS_RETOMAVEIS:
                continue
        elif not pasta_legada or not _parece_execucao_legada(pasta):
            continue
        candidatas.append((int(correspondencia.group(1)), pasta))

    if not candidatas:
        return None
    return max(candidatas, key=lambda item: item[0])[1]


def salvar_estado_execucao(
    pasta: Path,
    curso_id: str,
    status: str,
    resumo: dict | None = None,
) -> bool:
    """Persiste atomicamente o estado necessário para uma retomada futura."""
    dados = {
        "curso_id": str(curso_id),
        "status": status,
        "atualizado_em": datetime.now(UTC).isoformat(),
        "resumo": resumo or {},
    }
    destino = pasta / ARQUIVO_ESTADO
    temporario = pasta / f"{ARQUIVO_ESTADO}.tmp"
    try:
        temporario.write_text(
            json.dumps(dados, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporario.replace(destino)
        return True
    except OSError:
        try:
            temporario.unlink(missing_ok=True)
        except OSError:
            pass
        return False
