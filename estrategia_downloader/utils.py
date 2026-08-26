import re
import shutil
import tempfile
import unicodedata
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{numero}" for numero in range(1, 10)),
    *(f"LPT{numero}" for numero in range(1, 10)),
}
SENSITIVE_QUERY_PARTS = (
    "token",
    "access_token",
    "authorization",
    "auth",
    "signature",
    "sig",
    "key",
    "credential",
    "policy",
    "expires",
    "x-amz-",
    "x-goog-",
    "email",
    "firstname",
    "lastname",
    "user_id",
    "userid",
    "custom_user_id",
)
URL_PATTERN = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
SECRET_TEXT_PATTERN = re.compile(
    r"(?i)\b(password|senha|authorization|cookie|ecdsession|"
    r"x-interface-token)\b\s*[:=]\s*([^\s,;]+)"
)


class DestinoInvalidoError(RuntimeError):
    pass


class EspacoInsuficienteError(RuntimeError):
    def __init__(self, disponivel: int, necessario: int):
        self.disponivel = max(int(disponivel), 0)
        self.necessario = max(int(necessario), 0)
        super().__init__(
            "Espaço insuficiente no disco. "
            f"Disponível: {formatar_tamanho(self.disponivel)}; "
            f"necessário para o arquivo atual: {formatar_tamanho(self.necessario)}."
        )


def normalizar_texto(valor: str) -> str:
    decomposicao = unicodedata.normalize("NFKD", valor)
    return "".join(
        caractere for caractere in decomposicao if not unicodedata.combining(caractere)
    ).lower()


def safe_filename(nome: str, limite: int = 140, fallback: str = "sem nome") -> str:
    """Preserva títulos legíveis e neutraliza regras especiais do Windows."""
    nome = unicodedata.normalize("NFC", str(nome or ""))
    nome = "".join(
        " " if ord(char) < 32 or char in '<>:"/\\|?*' else char for char in nome
    )
    nome = re.sub(r"\s+", " ", nome).strip(" .")
    if not nome:
        nome = fallback

    sufixo = Path(nome).suffix if len(Path(nome).suffix) <= 12 else ""
    base = nome[: -len(sufixo)] if sufixo else nome
    if base.upper() in WINDOWS_RESERVED_NAMES:
        base = f"_{base}"
    espaco_base = max(limite - len(sufixo), 1)
    base = base[:espaco_base].rstrip(" .") or fallback
    nome = f"{base}{sufixo}".rstrip(" .")
    return nome or fallback


def slug_nome_curso(nome: str, limite: int = 120) -> str:
    """Converte o título canônico em um componente de pasta portável e legível."""
    ascii_name = (
        unicodedata.normalize("NFKD", str(nome or ""))
        .encode("ascii", "ignore")
        .decode("ascii")
        .lower()
    )
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name).strip("-")
    if not slug:
        raise ValueError("o nome do curso não contém caracteres utilizáveis")
    return slug[: max(int(limite), 1)].rstrip("-")


def parametro_sensivel(nome: str) -> bool:
    normalizado = normalizar_texto(nome).replace("-", "_")
    return any(
        normalizado == parte.replace("-", "_")
        or normalizado.startswith(parte.replace("-", "_"))
        for parte in SENSITIVE_QUERY_PARTS
    )


def sanitizar_url(url: str) -> str:
    try:
        partes = urlsplit(url)
        consulta = [
            (nome, "REMOVIDO" if parametro_sensivel(nome) else valor)
            for nome, valor in parse_qsl(partes.query, keep_blank_values=True)
        ]
        return urlunsplit(
            (partes.scheme, partes.netloc, partes.path, urlencode(consulta), "")
        )
    except (TypeError, ValueError):
        return "[URL inválida]"


def chave_deduplicacao_url(url: str) -> str:
    try:
        partes = urlsplit(url)
        consulta = sorted(
            (nome, valor)
            for nome, valor in parse_qsl(partes.query, keep_blank_values=True)
            if not parametro_sensivel(nome)
        )
        return urlunsplit(
            (
                partes.scheme.lower(),
                partes.netloc.lower(),
                partes.path,
                urlencode(consulta),
                "",
            )
        )
    except (TypeError, ValueError):
        return url


def sanitizar_texto(texto: str) -> str:
    sem_segredos = SECRET_TEXT_PATTERN.sub(r"\1=[REMOVIDO]", str(texto))
    return URL_PATTERN.sub(lambda match: sanitizar_url(match.group(0)), sem_segredos)


def formatar_tamanho(total_bytes: int) -> str:
    valor = float(max(total_bytes, 0))
    for unidade in ("B", "KB", "MB", "GB", "TB"):
        if valor < 1024 or unidade == "TB":
            return f"{valor:.1f} {unidade}"
        valor /= 1024
    return f"{valor:.1f} TB"


def formatar_duracao(segundos) -> str:
    if segundos is None or segundos < 0:
        return "--:--"
    total = int(segundos + 0.5)
    horas, resto = divmod(total, 3600)
    minutos, segundos = divmod(resto, 60)
    if horas:
        return f"{horas:02d}:{minutos:02d}:{segundos:02d}"
    return f"{minutos:02d}:{segundos:02d}"


def verificar_destino(pasta: Path) -> int:
    try:
        pasta.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix=".estrategia-", dir=pasta):
            pass
        return shutil.disk_usage(pasta).free
    except OSError as erro:
        raise DestinoInvalidoError(
            f"A pasta '{pasta}' não pode ser usada para downloads: {erro}"
        ) from erro


def espaco_disponivel(pasta: Path) -> int:
    return shutil.disk_usage(pasta).free
