import copy
import json
import os
import queue
import re
import secrets
import shutil
import subprocess
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from estrategia_downloader.utils import (
    formatar_tamanho,
    sanitizar_texto,
    verificar_destino,
)


class DownloadCancelado(Exception):
    """Interrupção cooperativa solicitada pela interface local."""


def selecionar_pasta_nativa(pasta_inicial: Path) -> Path | None:
    """Abre o seletor de diretórios do Windows sem aceitar caminho digitado."""
    import tkinter as tk
    from tkinter import filedialog

    raiz = tk.Tk()
    raiz.withdraw()
    raiz.attributes("-topmost", True)
    raiz.update()
    try:
        escolhido = filedialog.askdirectory(
            parent=raiz,
            title="Escolha onde salvar o conteúdo do curso",
            initialdir=str(pasta_inicial),
            mustexist=False,
        )
    finally:
        raiz.destroy()

    if not escolhido:
        return None
    pasta = Path(escolhido).resolve()
    pasta.mkdir(parents=True, exist_ok=True)
    return pasta


def extrair_id_interface(valor: str) -> str | None:
    valor = valor.strip()
    if re.fullmatch(r"\d+", valor):
        return valor
    encontrado = re.search(r"/cursos/(\d+)(?=[/?#]|$)", valor)
    return encontrado.group(1) if encontrado else None


def _encontrar_edge() -> str | None:
    executavel = shutil.which("msedge") or shutil.which("msedge.exe")
    if executavel:
        return executavel

    candidatos = []
    for variavel in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
        raiz = os.getenv(variavel)
        if raiz:
            candidatos.append(
                Path(raiz) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
            )
    return str(next((item for item in candidatos if item.is_file()), "")) or None


def abrir_interface_no_edge(url: str) -> None:
    executavel = _encontrar_edge()
    if executavel:
        subprocess.Popen(
            [executavel, "--new-window", url],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return
    if not webbrowser.open_new(url):
        raise RuntimeError("Não foi possível abrir a interface no Microsoft Edge.")


class _ServidorLocal(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class InterfaceWeb:
    def __init__(
        self,
        *,
        modo_reduzido: bool,
        pasta_inicial: Path,
        email_inicial: str = "",
        senha_inicial: str = "",
        curso_inicial: str = "",
    ):
        self.modo_reduzido = modo_reduzido
        self.pasta_inicial = pasta_inicial
        self.email_inicial = email_inicial
        self._senha_inicial = senha_inicial
        self.curso_inicial = curso_inicial
        self.token = secrets.token_urlsafe(32)
        self._sessao_web = secrets.token_urlsafe(32)
        self._lock = threading.RLock()
        self._configuracao = queue.Queue(maxsize=1)
        self._encerrar = threading.Event()
        self._cancelar = threading.Event()
        self._pasta_selecionada = None
        self._diagnostico = ""
        self._servidor = None
        self._thread = None
        self._assets = Path(__file__).resolve().parent / "interface"
        self._estado = {
            "status": "configuracao",
            "fase": "Preencha os dados para começar",
            "modo": (
                "PDFs, slides e mapas mentais" if modo_reduzido else "Conteúdo completo"
            ),
            "modo_reduzido": modo_reduzido,
            "modo_integral": False,
            "email_inicial": email_inicial,
            "curso_inicial": curso_inicial,
            "pasta_base": "",
            "pasta_destino": "",
            "espaco_disponivel": "calculando",
            "aula_atual": 0,
            "total_aulas": 0,
            "curso_atual": 0,
            "total_cursos": 0,
            "curso_nome": "",
            "encontrados": 0,
            "baixados": 0,
            "existentes": 0,
            "falhas": 0,
            "bytes_baixados": 0,
            "item": {
                "nome": "Aguardando o primeiro arquivo",
                "status": "aguardando",
                "percentual": 0,
                "recebido": "0 B",
                "total": "?",
                "velocidade": "0 B/s",
                "eta": "--:--",
            },
            "total": {
                "percentual": 0,
                "pronto": "0 B",
                "conhecido": "?",
                "velocidade": "0 B/s",
                "eta": "calculando",
                "curso_eta": "calculando",
            },
            "logs": [],
            "erro": "",
            "aviso": "",
            "instrucao_login": "",
            "resumo": {},
            "diagnostico_disponivel": False,
        }
        try:
            self._definir_pasta(pasta_inicial)
        except (OSError, RuntimeError) as erro:
            self._estado["erro"] = str(erro)

    def iniciar(self) -> str:
        if not (self._assets / "index.html").is_file():
            raise RuntimeError("Os arquivos da interface web não foram encontrados.")

        interface = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def _autorizado(self):
                consulta = parse_qs(urlparse(self.path).query)
                cookies = {}
                for parte in (self.headers.get("Cookie") or "").split(";"):
                    if "=" in parte:
                        nome, valor = parte.strip().split("=", 1)
                        cookies[nome] = valor
                recebido = (
                    self.headers.get("X-Interface-Token")
                    or (consulta.get("token", [""])[0])
                    or cookies.get("ECDSESSION", "")
                )
                return secrets.compare_digest(recebido, interface.token) or (
                    secrets.compare_digest(recebido, interface._sessao_web)
                )

            def _cabecalhos(
                self, status, tipo="application/json; charset=utf-8", extras=None
            ):
                self.send_response(status)
                self.send_header("Content-Type", tipo)
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'self'; script-src 'self'; style-src 'self'; "
                    "connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'",
                )
                for nome, valor in extras or []:
                    self.send_header(nome, valor)
                self.end_headers()

            def _json(self, status, dados):
                conteudo = json.dumps(dados, ensure_ascii=False).encode("utf-8")
                self._cabecalhos(status)
                self.wfile.write(conteudo)

            def _ler_json(self):
                tamanho = min(int(self.headers.get("Content-Length") or 0), 65536)
                if not tamanho:
                    return {}
                return json.loads(self.rfile.read(tamanho).decode("utf-8"))

            def do_GET(self):
                caminho = urlparse(self.path).path
                consulta = parse_qs(urlparse(self.path).query)
                arquivos = {
                    "/": ("index.html", "text/html; charset=utf-8"),
                    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
                    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                }
                # CSS e JavaScript não contêm dados da execução; liberar esses
                # dois assets permite que o navegador os carregue sem repetir o
                # token da URL. A página e toda API continuam protegidas.
                if caminho in {"/styles.css", "/app.js"}:
                    nome, tipo = arquivos[caminho]
                    conteudo = (interface._assets / nome).read_bytes()
                    self._cabecalhos(200, tipo)
                    self.wfile.write(conteudo)
                    return
                token_bootstrap = consulta.get("token", [""])[0]
                if caminho == "/" and secrets.compare_digest(
                    token_bootstrap, interface.token
                ):
                    self._cabecalhos(
                        303,
                        extras=[
                            ("Location", "/"),
                            (
                                "Set-Cookie",
                                "ECDSESSION="
                                f"{interface._sessao_web}; HttpOnly; SameSite=Strict; "
                                "Path=/",
                            ),
                        ],
                    )
                    return
                if not self._autorizado():
                    self._json(403, {"erro": "Acesso local não autorizado."})
                    return
                if caminho == "/api/state":
                    self._json(200, interface.estado())
                    return
                if caminho == "/api/diagnostic":
                    if not interface._diagnostico:
                        self._json(404, {"erro": "Diagnóstico ainda indisponível."})
                    else:
                        self._json(200, {"diagnostico": interface._diagnostico})
                    return
                if caminho not in arquivos:
                    self._json(404, {"erro": "Recurso não encontrado."})
                    return
                nome, tipo = arquivos[caminho]
                conteudo = (interface._assets / nome).read_bytes()
                self._cabecalhos(200, tipo)
                self.wfile.write(conteudo)

            def do_POST(self):
                caminho = urlparse(self.path).path
                if not self._autorizado():
                    self._json(403, {"erro": "Acesso local não autorizado."})
                    return
                if self.headers.get("X-Estrategia-Request") != "1":
                    self._json(403, {"erro": "Requisição local inválida."})
                    return
                try:
                    if caminho == "/api/select-folder":
                        pasta = selecionar_pasta_nativa(interface.pasta_inicial)
                        if pasta is not None:
                            interface._definir_pasta(pasta)
                        self._json(
                            200,
                            {"pasta": str(pasta or interface._pasta_selecionada or "")},
                        )
                        return
                    if caminho == "/api/start":
                        dados = self._ler_json()
                        configuracao = interface._validar_configuracao(dados)
                        interface._iniciar_download(configuracao)
                        self._json(202, {"ok": True})
                        return
                    if caminho == "/api/cancel":
                        interface.solicitar_cancelamento()
                        self._json(202, {"ok": True})
                        return
                    if caminho == "/api/open-folder":
                        interface.abrir_pasta_destino()
                        self._json(200, {"ok": True})
                        return
                    if caminho == "/api/shutdown":
                        interface.solicitar_encerramento()
                        self._json(200, {"ok": True})
                        return
                    self._json(404, {"erro": "Ação não encontrada."})
                except ValueError as erro:
                    self._json(400, {"erro": str(erro)})
                except Exception as erro:
                    interface.registrar_log(f"❌ Falha na interface: {erro}")
                    self._json(500, {"erro": str(erro)})

        self._servidor = _ServidorLocal(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(
            target=self._servidor.serve_forever,
            name="interface-web-local",
            daemon=True,
        )
        self._thread.start()
        porta = self._servidor.server_address[1]
        url = f"http://127.0.0.1:{porta}/?token={self.token}"
        abrir_interface_no_edge(url)
        return url

    def _definir_pasta(self, pasta: Path):
        with self._lock:
            if self._estado["status"] != "configuracao":
                raise ValueError("A pasta não pode ser alterada após o início.")
            pasta = Path(pasta).expanduser().resolve()
            livre = verificar_destino(pasta)
            self._pasta_selecionada = pasta
            self.pasta_inicial = pasta
            self._estado["pasta_base"] = str(pasta)
            self._estado["espaco_disponivel"] = formatar_tamanho(livre)
            self._estado["erro"] = ""

    def _validar_configuracao(self, dados):
        with self._lock:
            if self._estado["status"] != "configuracao":
                raise ValueError("O download já foi iniciado.")
            pasta = self._pasta_selecionada
        email = str(dados.get("email") or self.email_inicial).strip()
        senha = str(dados.get("senha") or self._senha_inicial)
        modo = str(
            dados.get("modo") or ("reduzido" if self.modo_reduzido else "completo")
        )
        modo_integral = modo == "integral"
        curso_id = (
            None
            if modo_integral
            else extrair_id_interface(str(dados.get("curso") or ""))
        )
        if not email:
            raise ValueError("Informe o e-mail da conta.")
        if not senha:
            raise ValueError("Informe a senha da conta.")
        if not modo_integral and not curso_id:
            raise ValueError("Informe um ID numérico ou uma URL válida do curso.")
        if pasta is None:
            raise ValueError("Escolha a pasta-base usando o botão da interface.")
        if modo not in {"completo", "reduzido", "integral"}:
            raise ValueError("Selecione um modo de download válido.")
        return {
            "email": email,
            "password": senha,
            "curso_id": curso_id,
            "pasta_base": pasta,
            "modo_reduzido": modo == "reduzido",
            "modo_integral": modo_integral,
        }

    def _iniciar_download(self, configuracao):
        with self._lock:
            if self._estado["status"] != "configuracao":
                raise ValueError("O download já foi iniciado.")
            self._estado["status"] = "preparando"
            self._estado["fase"] = "Preparando o Microsoft Edge"
            self._estado["email_inicial"] = ""
            self.modo_reduzido = configuracao["modo_reduzido"]
            self._estado["modo_reduzido"] = self.modo_reduzido
            self._estado["modo_integral"] = configuracao["modo_integral"]
            if configuracao["modo_integral"]:
                self._estado["modo"] = "Modo bombado — todos os cursos"
            elif self.modo_reduzido:
                self._estado["modo"] = "PDFs, slides e mapas mentais"
            else:
                self._estado["modo"] = "Conteúdo completo"
            self._senha_inicial = ""
        self._configuracao.put_nowait(configuracao)

    def aguardar_configuracao(self):
        configuracao = self._configuracao.get()
        if configuracao is None:
            raise SystemExit("Interface encerrada antes do início.")
        return configuracao

    def atualizar(self, **campos):
        with self._lock:
            self._estado.update(campos)

    def estado(self):
        with self._lock:
            return copy.deepcopy(self._estado)

    def registrar_log(self, mensagem: str):
        mensagem = sanitizar_texto(mensagem).strip()
        if not mensagem:
            return
        with self._lock:
            self._estado["logs"].append(mensagem)
            self._estado["logs"] = self._estado["logs"][-400:]

    def solicitar_cancelamento(self):
        with self._lock:
            if self._estado["status"] in {"preparando", "login", "baixando"}:
                self._estado["fase"] = "Cancelamento solicitado…"
                self._cancelar.set()

    def verificar_cancelamento(self):
        if self._cancelar.is_set():
            raise DownloadCancelado("Download cancelado pelo usuário.")

    def finalizar(self, status: str, fase: str, erro: str = ""):
        with self._lock:
            self._estado.update(status=status, fase=fase, erro=erro)
            if (
                status in {"concluido", "cancelado", "erro"}
                and not self._estado["resumo"]
            ):
                self._estado["resumo"] = {
                    "encontrados": self._estado["encontrados"],
                    "baixados": self._estado["baixados"],
                    "existentes": self._estado["existentes"],
                    "falhas": self._estado["falhas"],
                    "volume": formatar_tamanho(self._estado["bytes_baixados"]),
                    "tempo": "--:--",
                }

    def definir_resumo(self, resumo: dict):
        with self._lock:
            self._estado["resumo"] = copy.deepcopy(resumo)

    def definir_diagnostico(self, diagnostico: str):
        with self._lock:
            self._diagnostico = diagnostico
            self._estado["diagnostico_disponivel"] = bool(diagnostico)

    def abrir_pasta_destino(self):
        with self._lock:
            destino = self._estado.get("pasta_destino")
        if not destino or not Path(destino).is_dir():
            raise ValueError("A pasta de destino ainda não está disponível.")
        if hasattr(os, "startfile"):
            os.startfile(destino)
        else:
            subprocess.Popen(["xdg-open", destino])

    def solicitar_encerramento(self):
        with self._lock:
            status = self._estado["status"]
        if status in {"preparando", "login", "baixando"}:
            raise ValueError("Cancele o download antes de encerrar a interface.")
        if status == "configuracao":
            try:
                self._configuracao.put_nowait(None)
            except queue.Full:
                pass
        self._encerrar.set()

    def aguardar_encerramento(self):
        self._encerrar.wait()

    def parar(self):
        if self._servidor is not None:
            self._servidor.shutdown()
            self._servidor.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)


class SaidaPainel:
    """Duplica o texto do terminal no painel sem registrar cada atualização `\r`."""

    def __init__(self, original, painel: InterfaceWeb):
        self.original = original
        self.painel = painel
        self._buffer = ""
        self._lock = threading.Lock()

    @property
    def encoding(self):
        return getattr(self.original, "encoding", "utf-8")

    def write(self, texto):
        with self._lock:
            retorno = self.original.write(texto)
            if "\r" in texto:
                linha = texto.rsplit("\r", 1)[-1].strip()
                if linha:
                    self.painel.atualizar(linha_progresso=linha)
                return retorno
            self._buffer += texto
            while "\n" in self._buffer:
                linha, self._buffer = self._buffer.split("\n", 1)
                self.painel.registrar_log(linha)
            return retorno

    def flush(self):
        self.original.flush()

    def isatty(self):
        return False
