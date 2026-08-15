import json
import re
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests

from .config import (
    DISK_SAFETY_MARGIN,
    DOWNLOAD_CHUNK_SIZE,
    HTTP_CONNECT_TIMEOUT,
    HTTP_READ_TIMEOUT,
    MAX_DOWNLOAD_RETRIES,
    RETRY_DELAY_SECONDS,
)
from .utils import (
    EspacoInsuficienteError,
    chave_deduplicacao_url,
    espaco_disponivel,
    formatar_duracao,
    formatar_tamanho,
    safe_filename,
    sanitizar_texto,
)

CONTENT_RANGE_RE = re.compile(r"bytes\s+(\d+)-(\d+)/(\d+|\*)", re.I)
CONTENT_RANGE_COMPLETE_RE = re.compile(r"bytes\s+\*/(\d+)", re.I)


class RespostaDownloadInvalida(RuntimeError):
    pass


def criar_sessao_download(driver, curso_url: str) -> requests.Session:
    """Reaproveita no requests a autenticação feita pelo usuário no Edge."""
    sessao = requests.Session()
    for cookie in driver.get_cookies():
        kwargs = {"path": cookie.get("path", "/")}
        if cookie.get("domain"):
            kwargs["domain"] = cookie["domain"]
        sessao.cookies.set(cookie["name"], cookie["value"], **kwargs)

    try:
        user_agent = driver.execute_script("return navigator.userAgent")
        if user_agent:
            sessao.headers["User-Agent"] = user_agent
    except (AttributeError, TypeError):
        pass
    sessao.headers["Referer"] = curso_url
    return sessao


def detectar_extensao_resposta(resposta, url: str, fallback: str) -> str:
    disposition = resposta.headers.get("Content-Disposition") or ""
    nomes = re.findall(
        r"filename\*?=(?:UTF-8''|\")?([^\";]+)",
        disposition,
        flags=re.IGNORECASE,
    )
    candidatos = nomes + [unquote(urlparse(url).path)]
    permitidas = {
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
        ".mp4",
    }
    for candidato in candidatos:
        extensao = Path(unquote(candidato).strip()).suffix.lower()
        if extensao in permitidas:
            return extensao

    content_type = (resposta.headers.get("Content-Type") or "").split(";", 1)[0]
    por_tipo = {
        "application/pdf": ".pdf",
        "application/vnd.ms-powerpoint": ".ppt",
        "application/vnd.openxmlformats-officedocument."
        "presentationml.presentation": ".pptx",
        "application/msword": ".doc",
        "application/vnd.openxmlformats-officedocument."
        "wordprocessingml.document": ".docx",
        "application/zip": ".zip",
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "video/mp4": ".mp4",
    }
    return por_tipo.get(content_type.lower(), fallback)


def _validar_resposta_binaria(resposta, tipo_esperado: str):
    content_type = (resposta.headers.get("Content-Type") or "").lower()
    disposition = (resposta.headers.get("Content-Disposition") or "").lower()
    if "text/html" in content_type and "attachment" not in disposition:
        raise RespostaDownloadInvalida(
            "o servidor devolveu uma página HTML em vez do arquivo; o link pode "
            "ter expirado ou a sessão pode não estar autorizada"
        )
    if getattr(resposta, "status_code", 200) == 204:
        raise RespostaDownloadInvalida("o servidor devolveu uma resposta vazia")
    if tipo_esperado == "video" and content_type.startswith("text/"):
        raise RespostaDownloadInvalida("a resposta recebida não contém um vídeo")


def _ler_metadados(caminho: Path) -> dict:
    try:
        return json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}


def _gravar_metadados(caminho: Path, resposta, total: int):
    dados = {
        "etag": resposta.headers.get("ETag") or "",
        "last_modified": resposta.headers.get("Last-Modified") or "",
        "total": total,
    }
    try:
        caminho.write_text(json.dumps(dados), encoding="utf-8")
    except OSError:
        # O arquivo auxiliar melhora If-Range, mas não é necessário para que
        # uma resposta 206 corretamente validada possa ser retomada.
        pass


class GerenciadorDownloads:
    def __init__(
        self,
        download_dir: Path,
        driver,
        curso_url: str,
        max_tentativas: int = MAX_DOWNLOAD_RETRIES,
        painel=None,
    ):
        self.download_dir = download_dir
        self.sessao = criar_sessao_download(driver, curso_url)
        self.max_tentativas = max_tentativas
        self.nomes_globais = {}
        self.urls_processadas = set()
        self.encontrados = 0
        self.baixados = 0
        self.existentes = 0
        self.falhas = 0
        self.bytes_baixados = 0
        self.bytes_transferidos = 0
        self.bytes_existentes = 0
        self.bytes_falhos_conhecidos = 0
        self.inicio_downloads = None
        self.total_aulas = 0
        self.aula_atual = 0
        self.bytes_inicio_aula = 0
        self.tamanhos_aulas_concluidas = []
        self._ultimo_progresso = 0.0
        self._largura_progresso = 0
        self._progresso_ativo = False
        self._item_atual = "Aguardando o primeiro arquivo"
        self.painel = painel

    def _verificar_cancelamento(self):
        if self.painel is not None:
            self.painel.verificar_cancelamento()

    def _sincronizar_contadores(self):
        if self.painel is not None:
            self.painel.atualizar(
                encontrados=self.encontrados,
                baixados=self.baixados,
                existentes=self.existentes,
                falhas=self.falhas,
                bytes_baixados=self.bytes_baixados,
            )

    def configurar_total_aulas(self, total_aulas: int):
        self.total_aulas = total_aulas
        if self.painel is not None:
            self.painel.atualizar(total_aulas=total_aulas)

    def iniciar_aula(self, posicao: int):
        self._verificar_cancelamento()
        self.aula_atual = posicao
        self.bytes_inicio_aula = self._bytes_logicos_conhecidos()
        if self.painel is not None:
            self.painel.atualizar(
                aula_atual=posicao,
                fase=f"Procurando e baixando o conteúdo da aula {posicao}",
            )

    def concluir_aula(self):
        tamanho = self._bytes_logicos_conhecidos() - self.bytes_inicio_aula
        if tamanho > 0:
            self.tamanhos_aulas_concluidas.append(tamanho)

    def _bytes_prontos(self) -> int:
        return self.bytes_baixados + self.bytes_existentes

    def _bytes_logicos_conhecidos(self) -> int:
        return self._bytes_prontos() + self.bytes_falhos_conhecidos

    def _velocidade_media(self, agora: float, transferido_atual: int = 0) -> float:
        if self.inicio_downloads is None:
            return 0.0
        decorrido = max(agora - self.inicio_downloads, 0.001)
        return (self.bytes_transferidos + transferido_atual) / decorrido

    def _eta_curso(self, total_conhecido: int, pronto: int, velocidade: float):
        if not self.tamanhos_aulas_concluidas or not self.aula_atual or not velocidade:
            return None
        media_aula = sum(self.tamanhos_aulas_concluidas) / len(
            self.tamanhos_aulas_concluidas
        )
        conhecido_na_aula = max(total_conhecido - self.bytes_inicio_aula, 0)
        restante_aula = max(media_aula - conhecido_na_aula, 0)
        aulas_futuras = max(self.total_aulas - self.aula_atual, 0)
        restante = (
            max(total_conhecido - pronto, 0)
            + restante_aula
            + media_aula * aulas_futuras
        )
        return restante / velocidade

    def _mostrar_progresso(
        self,
        recebido_total: int,
        total_item: int,
        inicio_item: float,
        transferido_atual: int,
        *,
        final: bool = False,
    ):
        agora = time.monotonic()
        if not final and agora - self._ultimo_progresso < 0.5:
            return
        self._ultimo_progresso = agora
        decorrido_item = max(agora - inicio_item, 0.001)
        velocidade_item = transferido_atual / decorrido_item
        restante_item = max(total_item - recebido_total, 0) if total_item else None
        eta_item = (
            restante_item / velocidade_item if velocidade_item and total_item else None
        )

        pronto = self._bytes_prontos() + recebido_total
        total_conhecido = (
            self._bytes_logicos_conhecidos() + total_item if total_item else 0
        )
        velocidade_media = self._velocidade_media(agora, transferido_atual)
        percentual_item = (
            min(recebido_total * 100 / total_item, 100) if total_item else 0
        )
        percentual_total = (
            min(pronto * 100 / total_conhecido, 100) if total_conhecido else 0
        )
        eta_conhecido = (
            max(total_conhecido - pronto, 0) / velocidade_media
            if total_conhecido and velocidade_media and not self.falhas
            else None
        )
        eta_curso = self._eta_curso(total_conhecido, pronto, velocidade_media)
        eta_curso_texto = (
            formatar_duracao(eta_curso)
            if eta_curso is not None
            else ("indisponível" if self.falhas else "calculando")
        )
        if self.painel is not None:
            self.painel.atualizar(
                item={
                    "nome": self._item_atual,
                    "status": "baixando",
                    "percentual": percentual_item,
                    "recebido": formatar_tamanho(recebido_total),
                    "total": formatar_tamanho(total_item) if total_item else "?",
                    "velocidade": f"{formatar_tamanho(int(velocidade_item))}/s",
                    "eta": formatar_duracao(eta_item),
                },
                total={
                    "percentual": percentual_total,
                    "pronto": formatar_tamanho(pronto),
                    "conhecido": formatar_tamanho(total_conhecido)
                    if total_conhecido
                    else "?",
                    "velocidade": f"{formatar_tamanho(int(velocidade_media))}/s",
                    "eta": formatar_duracao(eta_conhecido),
                    "curso_eta": eta_curso_texto,
                },
            )
        item_total = formatar_tamanho(total_item) if total_item else "?"
        linha = (
            f"         Item #{self.encontrados} {percentual_item:5.1f}% "
            f"{formatar_tamanho(recebido_total)}/{item_total} "
            f"{formatar_tamanho(int(velocidade_item))}/s "
            f"ETA {formatar_duracao(eta_item)} | "
            f"Conhecido {self.baixados + self.existentes + (1 if final else 0)}/"
            f"{self.encontrados} {percentual_total:5.1f}% "
            f"{formatar_tamanho(pronto)}/"
            f"{formatar_tamanho(total_conhecido) if total_conhecido else '?'} | "
            f"Curso aula {self.aula_atual}/{self.total_aulas} ETA~ {eta_curso_texto}"
        )
        self._largura_progresso = max(self._largura_progresso, len(linha))
        print(
            f"\r{linha.ljust(self._largura_progresso)}",
            end="\n" if final else "",
            flush=True,
        )
        self._progresso_ativo = not final

    def _encerrar_linha_progresso(self):
        if self._progresso_ativo:
            print()
            self._progresso_ativo = False

    def _nome_destino(self, item) -> str:
        tipo_nome = {
            "video": "Vídeo",
            "pdf": "PDF",
            "slides": "Slides",
            "mapa_mental": "Mapa Mental",
            "material": "Material",
        }.get(item["tipo"], "Material")
        origem = "Curso" if item["aula_num"] == 0 else f"Aula {item['aula_num']:02d}"
        base = safe_filename(
            f"{origem} - {tipo_nome} {item['item_num']:02d} - {item['titulo']}"
        )
        quantidade = self.nomes_globais.get(base, 0) + 1
        self.nomes_globais[base] = quantidade
        if quantidade > 1:
            base = f"{base} ({quantidade})"
        return f"{base}{item['extensao']}"

    def _preparar_resposta(self, resposta, parcial: int):
        """Retorna (modo, total, offset), sem jamais anexar um 200 completo."""
        status = getattr(resposta, "status_code", 200)
        if parcial and status == 416:
            match = CONTENT_RANGE_COMPLETE_RE.fullmatch(
                (resposta.headers.get("Content-Range") or "").strip()
            )
            if match and int(match.group(1)) == parcial:
                return "completo", parcial, parcial
            raise RespostaDownloadInvalida(
                "o servidor recusou a faixa parcial existente"
            )

        resposta.raise_for_status()
        if status == 206:
            match = CONTENT_RANGE_RE.fullmatch(
                (resposta.headers.get("Content-Range") or "").strip()
            )
            if not match:
                raise RespostaDownloadInvalida("resposta 206 sem Content-Range válido")
            inicio, fim = int(match.group(1)), int(match.group(2))
            total = 0 if match.group(3) == "*" else int(match.group(3))
            if inicio != parcial or fim < inicio or (total and fim >= total):
                raise RespostaDownloadInvalida(
                    "Content-Range inconsistente "
                    f"(esperado início {parcial}, recebido {inicio})"
                )
            return "ab", total, parcial

        # Range ignorado: reinício seguro em vez de anexar o corpo completo.
        total = int(resposta.headers.get("Content-Length") or 0)
        return "wb", total, 0

    def baixar(self, item) -> bool:
        self._verificar_cancelamento()
        url = item["url"]
        chave = chave_deduplicacao_url(url)
        if chave in self.urls_processadas:
            print("      ⏭️ Link repetido, ignorando.")
            return False
        self.urls_processadas.add(chave)
        self.encontrados += 1
        final_name = self._nome_destino(item)
        self._item_atual = final_name
        self._sincronizar_contadores()
        destino = self.download_dir / final_name
        temporario = destino.with_suffix(destino.suffix + ".part")
        metadados_path = temporario.with_suffix(temporario.suffix + ".json")

        if destino.exists() and destino.stat().st_size > 0:
            tamanho = destino.stat().st_size
            self.existentes += 1
            self.bytes_existentes += tamanho
            self._sincronizar_contadores()
            print(f"      ⏭️ Já existe: {final_name}")
            return True

        ultimo_total = 0
        for tentativa in range(1, self.max_tentativas + 1):
            self._verificar_cancelamento()
            parcial = temporario.stat().st_size if temporario.exists() else 0
            headers = {}
            if parcial:
                headers["Range"] = f"bytes={parcial}-"
                metadados = _ler_metadados(metadados_path)
                if metadados.get("etag") or metadados.get("last_modified"):
                    headers["If-Range"] = (
                        metadados.get("etag") or metadados["last_modified"]
                    )
            print(
                f"      ⬇️ Arquivo encontrado #{self.encontrados}: {final_name} "
                f"(tentativa {tentativa}/{self.max_tentativas}"
                f"{f', retomando de {formatar_tamanho(parcial)}' if parcial else ''})",
                flush=True,
            )
            transferido = 0
            try:
                inicio_item = time.monotonic()
                if self.inicio_downloads is None:
                    self.inicio_downloads = inicio_item
                self._ultimo_progresso = 0
                with self.sessao.get(
                    url,
                    stream=True,
                    timeout=(HTTP_CONNECT_TIMEOUT, HTTP_READ_TIMEOUT),
                    headers=headers,
                ) as resposta:
                    _validar_resposta_binaria(resposta, item["tipo"])
                    modo, total, offset = self._preparar_resposta(resposta, parcial)
                    ultimo_total = total
                    if modo == "completo":
                        temporario.replace(destino)
                        metadados_path.unlink(missing_ok=True)
                        recebido_total = total
                        transferido = 0
                    else:
                        extensao = detectar_extensao_resposta(
                            resposta, url, destino.suffix
                        )
                        if extensao != destino.suffix.lower() and offset == 0:
                            destino = destino.with_suffix(extensao)
                            temporario = destino.with_suffix(destino.suffix + ".part")
                            metadados_path = temporario.with_suffix(
                                temporario.suffix + ".json"
                            )
                            final_name = destino.name
                            self._item_atual = final_name
                        restante = max(total - offset, 0) if total else 0
                        livre = espaco_disponivel(self.download_dir)
                        if restante and livre < restante + DISK_SAFETY_MARGIN:
                            raise EspacoInsuficienteError(livre, restante)
                        _gravar_metadados(metadados_path, resposta, total)
                        recebido_total = offset
                        with open(temporario, modo) as arquivo:
                            for chunk in resposta.iter_content(
                                chunk_size=DOWNLOAD_CHUNK_SIZE
                            ):
                                self._verificar_cancelamento()
                                if not chunk:
                                    continue
                                arquivo.write(chunk)
                                transferido += len(chunk)
                                recebido_total = offset + transferido
                                self._mostrar_progresso(
                                    recebido_total, total, inicio_item, transferido
                                )
                        if total and recebido_total != total:
                            raise RespostaDownloadInvalida(
                                f"arquivo incompleto: esperado {total} bytes, "
                                f"recebido {recebido_total}"
                            )
                        if recebido_total <= 0:
                            raise RespostaDownloadInvalida(
                                "o arquivo recebido está vazio"
                            )
                        self._mostrar_progresso(
                            recebido_total, total, inicio_item, transferido, final=True
                        )
                        temporario.replace(destino)
                        metadados_path.unlink(missing_ok=True)

                self.baixados += 1
                self.bytes_baixados += destino.stat().st_size
                self.bytes_transferidos += transferido
                self._sincronizar_contadores()
                if self.painel is not None:
                    tamanho_final = destino.stat().st_size
                    self.painel.atualizar(
                        item={
                            "nome": self._item_atual,
                            "status": "concluído",
                            "percentual": 100,
                            "recebido": formatar_tamanho(tamanho_final),
                            "total": formatar_tamanho(tamanho_final),
                            "velocidade": "0 B/s",
                            "eta": "00:00",
                        }
                    )
                print(
                    "      ✅ Salvo "
                    f"({formatar_tamanho(destino.stat().st_size)}): {destino}"
                )
                return True
            except EspacoInsuficienteError:
                self._encerrar_linha_progresso()
                raise
            except Exception as erro:
                # O cancelamento do painel não deriva de uma classe deste módulo;
                # dê a ele precedência antes de tratar a tentativa como falha.
                self._encerrar_linha_progresso()
                self._verificar_cancelamento()
                self.bytes_transferidos += transferido
                print(f"      ❌ Erro ao baixar: {sanitizar_texto(str(erro))}")
                if tentativa < self.max_tentativas:
                    time.sleep(RETRY_DELAY_SECONDS)

        self.falhas += 1
        self.bytes_falhos_conhecidos += ultimo_total
        self._sincronizar_contadores()
        print(f"      🚩 Falha definitiva depois de {self.max_tentativas} tentativas.")
        return False

    def resumo_dados(self) -> dict:
        decorrido = (
            time.monotonic() - self.inicio_downloads
            if self.inicio_downloads is not None
            else 0
        )
        velocidade_media = int(self.bytes_transferidos / max(decorrido, 0.001))
        return {
            "encontrados": self.encontrados,
            "baixados": self.baixados,
            "existentes": self.existentes,
            "falhas": self.falhas,
            "volume": formatar_tamanho(self.bytes_baixados),
            "tempo": formatar_duracao(decorrido),
            "velocidade_media": (
                f"{formatar_tamanho(velocidade_media)}/s" if decorrido else "0 B/s"
            ),
        }

    def resumo(self):
        dados = self.resumo_dados()
        print("\n🎉 Varredura e downloads concluídos.")
        print(f"   Arquivos únicos encontrados: {dados['encontrados']}")
        print(f"   Baixados nesta execução: {dados['baixados']}")
        print(f"   Já existentes: {dados['existentes']}")
        print(f"   Falhas: {dados['falhas']}")
        print(f"   Volume concluído: {dados['volume']}")
        print(f"   Tempo desde o primeiro download: {dados['tempo']}")
        print(f"   Velocidade média da rede: {dados['velocidade_media']}")
