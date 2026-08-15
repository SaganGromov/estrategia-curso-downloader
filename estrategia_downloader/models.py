from typing import Literal, TypedDict

TipoMaterial = Literal["video", "pdf", "slides", "mapa_mental", "material"]


class DownloadItem(TypedDict):
    tipo: TipoMaterial
    aula_num: int
    aula_nome: str
    item_num: int
    titulo: str
    extensao: str
    url: str


class Aula(TypedDict):
    num: int
    nome: str
    href: str
