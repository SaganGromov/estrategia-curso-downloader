import re
from html.parser import HTMLParser
from urllib.parse import unquote, urljoin

from .utils import normalizar_texto


def classificar_material(href: str, descricao: str) -> str | None:
    """Classifica materiais por sinais sem depender de uma única marcação DOM."""
    texto = normalizar_texto(f"{descricao} {unquote(href)}")
    acao_download = "baixar" in texto or "download" in texto
    if "mapa mental" in texto or "mapa-mental" in texto:
        return "mapa_mental"
    if re.search(r"\bslides?\b", texto) or ("apresentacao" in texto and acao_download):
        return "slides"
    sinais_pdf = (
        r"\.pdf(?:$|[?#&])",
        r"/pdf/",
        r"[?&](?:format|type|filetype)=pdf(?:&|$)",
        r"livro (?:eletronico|digital)",
        r"versao (?:simplificada|original)",
        r"marcacao dos aprovados",
        r"apostila",
        r"aula em texto",
        r"material escrito",
    )
    if any(re.search(padrao, texto) for padrao in sinais_pdf):
        return "pdf"
    if acao_download and re.search(r"\bpdfs?\b", texto):
        return "pdf"
    extensoes = r"\.(?:pptx?|docx?|xlsx?|zip|rar|png|jpe?g)(?:$|[?#&])"
    if (
        re.search(extensoes, texto)
        or (acao_download and not re.search(r"\b\d{3,4}\s*p\b", texto))
        or re.search(r"/(?:materiais?|arquivos?|files?)/", texto)
    ):
        return "material"
    return None


class _ParserLinks(HTMLParser):
    ATRIBUTOS_URL = (
        "href",
        "src",
        "data",
        "data-href",
        "data-url",
        "data-download-url",
        "data-file-url",
    )

    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.pilha = []
        self.candidatos = []

    def handle_starttag(self, tag, attrs):
        atributos = dict(attrs)
        url = next(
            (atributos.get(nome) for nome in self.ATRIBUTOS_URL if atributos.get(nome)),
            "",
        )
        registro = {
            "tag": tag,
            "attrs": atributos,
            "url": urljoin(self.base_url, url)
            if url and not url.startswith(("#", "javascript:", "data:"))
            else "",
            "texto": [],
        }
        self.pilha.append(registro)

    def handle_data(self, data):
        for registro in self.pilha:
            registro["texto"].append(data)

    def handle_endtag(self, tag):
        for indice in range(len(self.pilha) - 1, -1, -1):
            registro = self.pilha[indice]
            if registro["tag"] != tag:
                continue
            del self.pilha[indice:]
            descricao = " ".join(registro["texto"])
            descricao += " " + " ".join(
                registro["attrs"].get(nome, "")
                for nome in ("title", "aria-label", "download", "class")
            )
            tipo = classificar_material(registro["url"], descricao)
            if tipo:
                self.candidatos.append(
                    {
                        "tipo": tipo,
                        "url": registro["url"],
                        "descricao": " ".join(descricao.split()),
                    }
                )
            break


def extrair_candidatos_html(html: str, base_url: str) -> list[dict]:
    """Parser puro usado pelos fixtures; a coleta real continua via Selenium."""
    parser = _ParserLinks(base_url)
    parser.feed(html)
    unicos = []
    vistos = set()
    for item in parser.candidatos:
        chave = (item["tipo"], item["url"], item["descricao"])
        if chave not in vistos:
            vistos.add(chave)
            unicos.append(item)
    return unicos


class _ParserAncora(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.atual = None
        self.ancoras = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.atual = {"attrs": dict(attrs), "texto": []}

    def handle_data(self, data):
        if self.atual is not None:
            self.atual["texto"].append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self.atual is not None:
            href = self.atual["attrs"].get("href", "")
            self.ancoras.append(
                {
                    "url": urljoin(self.base_url, href),
                    "texto": " ".join(" ".join(self.atual["texto"]).split()),
                }
            )
            self.atual = None


def extrair_aulas_html(html: str, base_url: str) -> list[dict]:
    parser = _ParserAncora(base_url)
    parser.feed(html)
    aulas = []
    vistos = set()
    for ancora in parser.ancoras:
        if "/aulas/" not in ancora["url"] or ancora["url"] in vistos:
            continue
        vistos.add(ancora["url"])
        encontrado = re.search(r"(?:aula\s+)?(\d+)", ancora["texto"], re.I)
        aulas.append(
            {
                "num": int(encontrado.group(1)) if encontrado else 9999,
                "nome": ancora["texto"],
                "href": ancora["url"],
            }
        )
    return sorted(aulas, key=lambda aula: aula["num"])


def extrair_opcoes_video_html(html: str, base_url: str) -> list[dict]:
    parser = _ParserAncora(base_url)
    parser.feed(html)
    opcoes = []
    for ancora in parser.ancoras:
        resolucoes = [
            int(valor)
            for valor in re.findall(
                r"\b(\d{3,4})\s*p\b", ancora["texto"], re.IGNORECASE
            )
        ]
        if resolucoes:
            opcoes.append({"resolucao": max(resolucoes), "url": ancora["url"]})
    return sorted(opcoes, key=lambda opcao: opcao["resolucao"], reverse=True)
