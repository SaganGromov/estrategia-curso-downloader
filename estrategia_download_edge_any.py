import argparse
import os
import re
import sys
import time
import traceback
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import requests
from selenium import webdriver
from selenium.common.exceptions import NoSuchWindowException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.edge.options import Options as EdgeOptions
from selenium.webdriver.edge.service import Service as EdgeService
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

# =========================================================
# CONFIGURAÇÕES BÁSICAS
# =========================================================
LOGIN_URL = "https://www.estrategiaconcursos.com.br/app/dashboard/cursos"

# Pasta inicial exibida no seletor do Windows. DOWNLOAD_DIR ainda pode ser
# definido no ambiente para mudar apenas o ponto de partida da janela.
DEFAULT_DOWNLOAD_DIR = Path(
    os.getenv("DOWNLOAD_DIR", r"C:\estrategia\downloads")
).resolve()

# Normalmente o Selenium Manager instala o driver automaticamente. Usuários
# avançados ainda podem informar um executável próprio nesta variável.
EDGE_DRIVER_PATH = os.getenv("ESTRATEGIA_EDGE_DRIVER")

# Tempo máximo para concluir login/2FA/captcha no Edge. Pode ser alterado, por
# exemplo, no PowerShell: $env:ESTRATEGIA_LOGIN_TIMEOUT = "900"
LOGIN_TIMEOUT = int(os.getenv("ESTRATEGIA_LOGIN_TIMEOUT", "600"))


def ler_argumentos():
    parser = argparse.ArgumentParser(
        description="Baixa vídeos e PDFs das aulas do Estratégia."
    )
    parser.add_argument(
        "--somente-pdfs",
        action="store_true",
        help="baixa todos os PDFs encontrados, sem procurar ou baixar vídeos",
    )
    parser.add_argument(
        "--somente-videos",
        action="store_true",
        help="procura e baixa somente vídeos, sem procurar PDFs",
    )
    parser.add_argument(
        "--curso-id",
        help="ID numérico do curso; evita a janela de seleção do curso",
    )
    parser.add_argument(
        "--pasta-curso",
        type=Path,
        help="pasta exata de um curso já baixado; evita o seletor e nova subpasta",
    )
    parser.add_argument(
        "--aula",
        type=int,
        action="append",
        dest="aulas",
        help="processa somente esta aula; pode ser usado mais de uma vez",
    )
    parser.add_argument(
        "--videos",
        help="números dos vídeos a processar, separados por vírgula (ex.: 16,17,18)",
    )
    parser.add_argument(
        "--tentativas-links",
        type=int,
        default=3,
        help="tentativas para fazer o link de cada vídeo aparecer (padrão: 3)",
    )
    parser.add_argument(
        "--organizar-por-aula",
        action="store_true",
        help="salva no layout aula_NN\\videos usado por versões empacotadas",
    )
    args = parser.parse_args()

    if args.somente_pdfs and args.somente_videos:
        parser.error("--somente-pdfs e --somente-videos não podem ser combinados")
    if args.curso_id and not extrair_curso_id(args.curso_id):
        parser.error("--curso-id deve conter um ID numérico válido")
    if args.tentativas_links < 1:
        parser.error("--tentativas-links deve ser maior que zero")

    args.videos_selecionados = None
    if args.videos:
        try:
            args.videos_selecionados = {
                int(valor.strip()) for valor in args.videos.split(",") if valor.strip()
            }
        except ValueError:
            parser.error("--videos deve conter somente números separados por vírgula")
        if not args.videos_selecionados or min(args.videos_selecionados) < 1:
            parser.error("--videos deve conter números maiores que zero")
        if not args.somente_videos:
            parser.error("--videos exige também --somente-videos")

    return args


def escolher_download_dir() -> Path:
    """Abre o seletor nativo para o usuário escolher a pasta desta execução."""
    try:
        import tkinter as tk
        from tkinter import filedialog

        raiz = tk.Tk()
        raiz.withdraw()
        raiz.attributes("-topmost", True)
        raiz.update()
        try:
            escolhido = filedialog.askdirectory(
                parent=raiz,
                title="Escolha onde salvar os vídeos e PDFs",
                initialdir=str(DEFAULT_DOWNLOAD_DIR),
                mustexist=False,
            )
        finally:
            raiz.destroy()
    except Exception as e:
        raise RuntimeError(
            f"Não foi possível abrir o seletor de pastas do Windows: {e}"
        ) from e

    if not escolhido:
        raise SystemExit("Seleção de pasta cancelada. Nenhum download foi iniciado.")

    download_dir = Path(escolhido).resolve()
    download_dir.mkdir(parents=True, exist_ok=True)
    print(f"📁 Pasta-base selecionada: {download_dir}")
    return download_dir


def pedir_credenciais():
    """Solicita apenas as credenciais que não vieram do ambiente."""
    email = (os.getenv("ESTRATEGIA_EMAIL") or "").strip()
    password = os.getenv("ESTRATEGIA_PASSWORD") or ""

    if email and password:
        print("🔐 Credenciais recebidas pelas variáveis de ambiente.")
        return email, password

    try:
        import tkinter as tk
        from tkinter import messagebox, simpledialog

        raiz = tk.Tk()
        raiz.withdraw()
        raiz.attributes("-topmost", True)
        raiz.update()
        try:
            while not email:
                resposta = simpledialog.askstring(
                    "Login do Estratégia",
                    "Informe o e-mail da sua conta:",
                    parent=raiz,
                )
                if resposta is None:
                    raise SystemExit(
                        "Login cancelado. Nenhuma credencial foi armazenada."
                    )
                email = resposta.strip()
                if not email:
                    messagebox.showerror(
                        "E-mail obrigatório", "Informe o e-mail da conta.", parent=raiz
                    )

            while not password:
                resposta = simpledialog.askstring(
                    "Login do Estratégia",
                    "Informe a senha (ela não será salva):",
                    show="*",
                    parent=raiz,
                )
                if resposta is None:
                    raise SystemExit(
                        "Login cancelado. Nenhuma credencial foi armazenada."
                    )
                password = resposta
                if not password:
                    messagebox.showerror(
                        "Senha obrigatória", "Informe a senha da conta.", parent=raiz
                    )
        finally:
            raiz.destroy()
    except SystemExit:
        raise
    except Exception as e:
        raise RuntimeError(f"Não foi possível solicitar as credenciais: {e}") from e

    print("🔐 Credenciais recebidas; a senha ficará somente na memória desta execução.")
    return email, password


def extrair_curso_id(valor: str):
    valor = valor.strip()
    if re.fullmatch(r"\d+", valor):
        return valor
    encontrado = re.search(r"/cursos/(\d+)(?=[/?#]|$)", valor)
    return encontrado.group(1) if encontrado else None


def pedir_curso_id() -> str:
    """Solicita o ID do curso em uma janela e valida a resposta."""
    try:
        import tkinter as tk
        from tkinter import messagebox, simpledialog

        raiz = tk.Tk()
        raiz.withdraw()
        raiz.attributes("-topmost", True)
        raiz.update()
        valor_inicial = os.getenv("ESTRATEGIA_CURSO_ID", "")
        try:
            while True:
                resposta = simpledialog.askstring(
                    "Curso do Estratégia",
                    "Informe o ID numérico do curso\n"
                    "(você também pode colar a URL completa):",
                    initialvalue=valor_inicial,
                    parent=raiz,
                )
                if resposta is None:
                    raise SystemExit(
                        "Seleção de curso cancelada. Nenhum download foi iniciado."
                    )

                curso_id = extrair_curso_id(resposta)
                if curso_id:
                    print(f"🎯 ID do curso selecionado: {curso_id}")
                    return curso_id

                valor_inicial = resposta
                messagebox.showerror(
                    "ID inválido",
                    "Digite somente o ID numérico ou cole uma URL que "
                    "contenha /cursos/ID/.",
                    parent=raiz,
                )
        finally:
            raiz.destroy()
    except SystemExit:
        raise
    except Exception as e:
        raise RuntimeError(f"Não foi possível solicitar o ID do curso: {e}") from e


def montar_curso_url(curso_id: str) -> str:
    return (
        f"https://www.estrategiaconcursos.com.br/app/dashboard/cursos/{curso_id}/aulas"
    )


def safe_filename(name: str) -> str:
    invalid = '<>:"/\\|?*'
    for ch in invalid:
        name = name.replace(ch, " ")
    name = " ".join(name.split())
    if len(name) > 140:
        name = name[:140]
    return name.strip()


def parse_num_aula(texto: str) -> int:
    if not texto:
        return 9999
    m = re.search(r"[Aa]ula\s+(\d+)", texto)
    if m:
        return int(m.group(1))
    m = re.match(r"\s*(\d+)", texto)
    if m:
        return int(m.group(1))
    return 9999


def create_edge_driver(download_path: Path):
    opts = EdgeOptions()
    opts.add_argument("--start-maximized")

    prefs = {
        "download.default_directory": str(download_path),
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": True,
    }
    opts.add_experimental_option("prefs", prefs)

    try:
        if EDGE_DRIVER_PATH:
            service = EdgeService(executable_path=EDGE_DRIVER_PATH)
            return webdriver.Edge(service=service, options=opts)

        print("🌐 Preparando o Edge e o driver automaticamente...")
        return webdriver.Edge(options=opts)
    except Exception as e:
        raise RuntimeError(
            "Não consegui iniciar o Microsoft Edge. Confirme que o Edge está "
            "instalado e que há acesso à internet para o Selenium Manager. "
            f"Detalhes: {e}"
        ) from e


def _elemento_visivel(driver, css_selector: str) -> bool:
    """Retorna True se ao menos um elemento do seletor estiver visível."""
    try:
        return any(
            el.is_displayed()
            for el in driver.find_elements(By.CSS_SELECTOR, css_selector)
        )
    except (NoSuchWindowException, WebDriverException):
        raise
    except Exception:
        return False


def _login_concluido(driver) -> bool:
    """Detecta o painel sem depender de ENTER no terminal."""
    url = (driver.current_url or "").lower()

    # Enquanto houver um campo de senha visível, ainda estamos no formulário.
    if _elemento_visivel(driver, "input[type='password'], input[name='password']"):
        return False

    # O painel autenticado normalmente contém links de cursos. A segunda
    # condição cobre contas cujo painel ainda esteja carregando ou esteja vazio.
    tem_links_de_curso = bool(
        driver.find_elements(By.CSS_SELECTOR, "a[href*='/app/dashboard/cursos/']")
    )
    esta_no_dashboard = "/app/dashboard" in url and "/login" not in url
    return tem_links_de_curso or esta_no_dashboard


def _painel_carregado(driver) -> bool:
    """Detecção estrita, usada antes de sabermos se houve formulário de login."""
    if _elemento_visivel(driver, "input[type='password'], input[name='password']"):
        return False
    return bool(
        driver.find_elements(By.CSS_SELECTOR, "a[href*='/app/dashboard/cursos/']")
    )


def do_login(driver, email: str, password: str):
    driver.get(LOGIN_URL)
    wait = WebDriverWait(driver, 30)

    # A página pode abrir diretamente no painel quando o perfil do Edge já tem
    # uma sessão válida.
    wait.until(
        lambda d: (
            _painel_carregado(d)
            or _elemento_visivel(d, "input[type='email'], input[name='email']")
        )
    )
    if _painel_carregado(driver):
        print("\n✅ Sessão do Estratégia já está autenticada.")
        return

    email_el = wait.until(
        EC.visibility_of_element_located(
            (By.CSS_SELECTOR, "input[type='email'], input[name='email']")
        )
    )

    email_el.clear()
    email_el.send_keys(email)

    pwd_el = wait.until(
        EC.visibility_of_element_located(
            (By.CSS_SELECTOR, "input[type='password'], input[name='password']")
        )
    )
    pwd_el.clear()
    pwd_el.send_keys(password)

    print("\n➡️ No Edge, clique em Entrar e conclua 2FA/captcha, se aparecer.")
    print("   Não é mais necessário apertar ENTER no PowerShell.")
    print(f"   Aguardando o painel por até {LOGIN_TIMEOUT} segundos...", flush=True)

    inicio = time.monotonic()
    proximo_aviso = 15
    while True:
        if _login_concluido(driver):
            print("✅ Login detectado; continuando automaticamente.\n", flush=True)
            return

        decorrido = int(time.monotonic() - inicio)
        if decorrido >= LOGIN_TIMEOUT:
            raise TimeoutError(
                "O login não foi concluído dentro do tempo limite. "
                "Confira no Edge se há mensagem de erro, captcha ou 2FA."
            )
        if decorrido >= proximo_aviso:
            print(f"   Ainda aguardando o login... ({decorrido}s)", flush=True)
            proximo_aviso += 15
        time.sleep(1)


def listar_aulas(driver, curso_url: str):
    driver.get(curso_url)
    wait = WebDriverWait(driver, 30)
    wait.until(lambda d: d.execute_script("return document.readyState") == "complete")

    try:
        wait.until(
            lambda d: d.find_elements(By.XPATH, "//a[contains(@href,'/aulas/')]")
        )
    except Exception:
        # Deixa a validação abaixo produzir uma mensagem mais útil que um
        # TimeoutException genérico.
        pass

    itens = driver.find_elements(By.XPATH, "//a[contains(@href,'/aulas/')]")
    if not itens:
        raise RuntimeError(
            "Nenhuma aula foi encontrada na página do curso. "
            f"URL atual: {driver.current_url!r}. Confira o ID informado e se a conta "
            "tem acesso a esse curso."
        )

    aulas = []
    vistos = set()

    for el in itens:
        txt = (el.text or "").strip()
        href = el.get_attribute("href") or ""
        if "/aulas/" not in href:
            continue
        if href in vistos:
            continue
        vistos.add(href)

        num = parse_num_aula(txt)
        aulas.append(
            {
                "num": num,
                "nome": txt or f"Aula {str(num).zfill(2)}",
                "href": href,
            }
        )

    aulas.sort(key=lambda x: x["num"])

    print(f"📚 Total de aulas identificadas: {len(aulas)}")
    for a in aulas:
        print(f"   • Aula {a['num']:02d}: {a['nome']}")
    return aulas


def obter_nome_curso(driver, curso_id: str) -> str:
    """Obtém um nome seguro para a subpasta a partir da página do curso."""
    candidatos = []
    seletores = (
        "[class*='CourseHeader'] h1",
        "[class*='CourseHeader'] [class*='title']",
        "[class*='course-header'] h1",
        "main h1",
        "h1",
    )
    for seletor in seletores:
        for elemento in driver.find_elements(By.CSS_SELECTOR, seletor):
            texto = (elemento.text or "").strip()
            if texto:
                candidatos.append(texto)

    try:
        meta_titulo = driver.find_elements(By.CSS_SELECTOR, "meta[property='og:title']")
        if meta_titulo:
            candidatos.append(meta_titulo[0].get_attribute("content") or "")
    except Exception:
        pass
    candidatos.append(driver.title or "")

    genericos = {"aulas", "meus cursos", "cursos", "dashboard", "estratégia concursos"}
    for candidato in candidatos:
        candidato = re.sub(
            r"\s*[|–—-]\s*Estratégia(?: Concursos)?.*$",
            "",
            candidato,
            flags=re.IGNORECASE,
        ).strip()
        nome = safe_filename(candidato)
        if len(nome) >= 3 and nome.lower() not in genericos:
            return nome[:100].rstrip(" .")

    return f"Curso {curso_id}"


def criar_pasta_do_curso(pasta_base: Path, driver, curso_id: str) -> Path:
    nome_curso = obter_nome_curso(driver, curso_id)
    pasta_curso = pasta_base / nome_curso
    pasta_curso.mkdir(parents=True, exist_ok=True)

    # Mantém também eventuais downloads iniciados pelo próprio Edge dentro da
    # mesma subpasta. Os downloads principais continuam sendo feitos via requests.
    try:
        driver.execute_cdp_cmd(
            "Page.setDownloadBehavior",
            {"behavior": "allow", "downloadPath": str(pasta_curso)},
        )
    except Exception:
        pass

    print(f"📚 Curso identificado: {nome_curso}")
    print(f"📁 Conteúdo será salvo em: {pasta_curso}")
    return pasta_curso


def expandir_opcoes_download(driver):
    headers = driver.find_elements(
        By.XPATH,
        "//strong[normalize-space()='Opções de download' or "
        "contains(normalize-space(.),'Opções de download')]",
    )
    if not headers:
        return False

    header = headers[0]

    container = header
    try:
        container = header.find_element(By.XPATH, "./ancestor::div[1]")
        container = container.find_element(By.XPATH, "./ancestor::div[1]")
    except Exception:
        pass

    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", container)
    time.sleep(0.25)
    try:
        container.click()
    except Exception:
        driver.execute_script("arguments[0].click();", container)

    time.sleep(0.35)
    return True


def _resolucao_do_botao(botao):
    """Retorna a altura da resolução anunciada por um link de download."""
    texto = " ".join(
        str(valor)
        for valor in (
            botao.text,
            botao.get_attribute("aria-label"),
            botao.get_attribute("title"),
            botao.get_attribute("download"),
            botao.get_attribute("href"),
        )
        if valor
    )
    resolucoes = [
        int(valor) for valor in re.findall(r"\b(\d{3,4})\s*p\b", texto, re.IGNORECASE)
    ]
    for rotulo, altura in (("8k", 4320), ("4k", 2160), ("2k", 1440)):
        if re.search(rf"\b{rotulo}\b", texto, re.IGNORECASE):
            resolucoes.append(altura)
    return max(resolucoes) if resolucoes else None


def iterar_videos_da_aula_atual(
    driver,
    aula_num: int,
    aula_nome: str,
    videos_selecionados=None,
    tentativas_links: int = 3,
):
    """Encontra e entrega cada vídeo assim que o respectivo link aparece."""
    wait = WebDriverWait(driver, 15)

    try:
        wait.until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, "span.VideoItem-info-title")
            )
        )
    except Exception:
        print(
            "   ⚠️ Não encontrei lista de vídeos nessa aula, pode ser aula só com PDF."
        )
        return

    videos = driver.find_elements(By.CSS_SELECTOR, "span.VideoItem-info-title")
    total = len(videos)
    print(f"   🔎 Vídeos encontrados: {total}")

    nomes_usados_nesta_aula = {}

    for idx in range(total):
        numero_video = idx + 1
        if videos_selecionados is not None and numero_video not in videos_selecionados:
            continue

        try:
            titulo_video = f"video_{numero_video}"
            botao_escolhido = None
            resolucao_escolhida = None

            for tentativa in range(1, tentativas_links + 1):
                videos = driver.find_elements(
                    By.CSS_SELECTOR, "span.VideoItem-info-title"
                )
                if idx >= len(videos):
                    break

                vid_el = videos[idx]
                titulo_video = (vid_el.text or titulo_video).strip()

                driver.execute_script(
                    "arguments[0].scrollIntoView({block:'center'});", vid_el
                )
                time.sleep(0.15)

                try:
                    clickable = vid_el.find_element(By.XPATH, "ancestor::a[1]")
                except Exception:
                    try:
                        clickable = vid_el.find_element(By.XPATH, "ancestor::button[1]")
                    except Exception:
                        clickable = vid_el

                try:
                    clickable.click()
                except Exception:
                    driver.execute_script("arguments[0].click();", clickable)

                time.sleep(0.9)
                ok = expandir_opcoes_download(driver)
                if ok:
                    candidatos = []
                    links_sem_resolucao = []
                    for botao in driver.find_elements(By.CSS_SELECTOR, "a[href]"):
                        if not botao.is_displayed():
                            continue
                        href_botao = botao.get_attribute("href") or ""
                        resolucao = _resolucao_do_botao(botao)
                        if resolucao:
                            candidatos.append((resolucao, botao))
                            continue

                        descricao = " ".join(
                            filter(
                                None,
                                [botao.text, botao.get_attribute("aria-label")],
                            )
                        ).lower()
                        if re.search(
                            r"\.mp4(?:$|[?#])", href_botao, re.IGNORECASE
                        ) or (
                            "vídeo" in descricao
                            and ("baixar" in descricao or "download" in descricao)
                        ):
                            links_sem_resolucao.append(botao)

                    if candidatos:
                        resolucao_escolhida, botao_escolhido = max(
                            candidatos, key=lambda item: item[0]
                        )
                    elif links_sem_resolucao:
                        botao_escolhido = links_sem_resolucao[0]

                if botao_escolhido is not None:
                    break
                if tentativa < tentativas_links:
                    print(
                        f"      ↪️ Vídeo {numero_video:02d}: o link ainda não "
                        f"apareceu; nova tentativa ({tentativa + 1}/{tentativas_links})."
                    )
                    time.sleep(2)

            if botao_escolhido is None:
                print(
                    f"      ⚠️ Vídeo {numero_video:02d}: nenhum link de download "
                    f"apareceu após {tentativas_links} tentativas."
                )
                continue

            if resolucao_escolhida:
                print(
                    f"      🎞️ Vídeo {numero_video:02d}: melhor qualidade "
                    f"disponível: {resolucao_escolhida}p."
                )
            else:
                print(
                    f"      ℹ️ Vídeo {numero_video:02d}: usando o link disponível; "
                    "a resolução não foi informada."
                )

            href = botao_escolhido.get_attribute("href")
            if not href:
                print(f"      ⚠️ Vídeo {numero_video:02d}: botão de download sem href")
                continue

            base_title = safe_filename(titulo_video)
            if base_title in nomes_usados_nesta_aula:
                c = nomes_usados_nesta_aula[base_title] + 1
                nomes_usados_nesta_aula[base_title] = c
                base_title = f"{base_title} ({c})"
            else:
                nomes_usados_nesta_aula[base_title] = 1

            print(f"      ✅ Vídeo {numero_video:02d}: {base_title} -> {href}")

            yield {
                "tipo": "video",
                "aula_num": aula_num,
                "aula_nome": safe_filename(aula_nome),
                "item_num": numero_video,
                "titulo": base_title,
                "extensao": ".mp4",
                "url": href,
            }
        except Exception as e:
            print(f"      ❌ Erro no vídeo {numero_video:02d}: {e}")


def _texto_link(elemento) -> str:
    partes = [
        elemento.text,
        elemento.get_attribute("download"),
        elemento.get_attribute("title"),
        elemento.get_attribute("aria-label"),
        elemento.get_attribute("data-original-title"),
        elemento.get_attribute("type"),
    ]
    return " ".join(str(parte).strip() for parte in partes if parte).strip()


def _parece_link_pdf(href: str, descricao: str) -> bool:
    href_normalizado = unquote(href).lower()
    descricao_normalizada = descricao.lower()
    return bool(
        re.search(r"\.pdf(?:$|[?#&])", href_normalizado)
        or "/pdf/" in href_normalizado
        or re.search(r"[?&](?:format|type|filetype)=pdf(?:&|$)", href_normalizado)
        or re.search(r"(?:^|[^a-z])pdf(?:[^a-z]|$)", descricao_normalizada)
    )


def _url_do_elemento(elemento, url_atual: str) -> str:
    for atributo in (
        "href",
        "src",
        "data",
        "data-href",
        "data-url",
        "data-download-url",
        "data-file-url",
    ):
        valor = (elemento.get_attribute(atributo) or "").strip()
        if valor and not valor.startswith(("javascript:", "data:", "#")):
            return urljoin(url_atual, valor)

    # Alguns botões guardam a URL diretamente no onclick.
    onclick = elemento.get_attribute("onclick") or ""
    candidato = re.search(
        r"['\"]([^'\"]*(?:\.pdf|/pdf/)[^'\"]*)['\"]", onclick, re.IGNORECASE
    )
    return urljoin(url_atual, candidato.group(1)) if candidato else ""


def _titulo_pdf(elemento, href: str, indice: int) -> str:
    titulo = _texto_link(elemento)
    nome_url = Path(unquote(urlparse(href).path)).name

    if not titulo or titulo.lower() in {"pdf", "baixar pdf", "download pdf", "baixar"}:
        titulo = nome_url or f"PDF {indice:02d}"

    titulo = re.sub(r"\.pdf$", "", titulo, flags=re.IGNORECASE)
    titulo = re.sub(r"\s+", " ", titulo).strip(" .-_")
    return safe_filename(titulo) or f"PDF {indice:02d}"


def iterar_pdfs_da_aula_atual(driver, aula_num: int, aula_nome: str):
    """Entrega todos os links de PDF visíveis na aula, sem duplicatas."""
    vistos = set()
    encontrados = []

    seletor = (
        "a[href], iframe[src], embed[src], object[data], [data-href], "
        "[data-url], [data-download-url], [data-file-url], [onclick]"
    )
    for elemento in driver.find_elements(By.CSS_SELECTOR, seletor):
        try:
            href = _url_do_elemento(elemento, driver.current_url)
            if not href:
                continue

            descricao = _texto_link(elemento)
            if not _parece_link_pdf(href, descricao) or href in vistos:
                continue

            vistos.add(href)
            encontrados.append((elemento, href))
        except Exception:
            continue

    print(f"   📄 PDFs encontrados: {len(encontrados)}")
    for indice, (elemento, href) in enumerate(encontrados, start=1):
        titulo = _titulo_pdf(elemento, href, indice)
        print(f"      ✅ PDF {indice:02d}: {titulo} -> {href}")
        yield {
            "tipo": "pdf",
            "aula_num": aula_num,
            "aula_nome": safe_filename(aula_nome),
            "item_num": indice,
            "titulo": titulo,
            "extensao": ".pdf",
            "url": href,
        }


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
    except Exception:
        pass
    sessao.headers["Referer"] = curso_url
    return sessao


def formatar_tamanho(total_bytes: int) -> str:
    valor = float(total_bytes)
    for unidade in ("B", "KB", "MB", "GB", "TB"):
        if valor < 1024 or unidade == "TB":
            return f"{valor:.1f} {unidade}"
        valor /= 1024
    return f"{valor:.1f} TB"


class GerenciadorDownloads:
    def __init__(
        self,
        download_dir: Path,
        driver,
        curso_url: str,
        max_tentativas=3,
        organizar_por_aula=False,
    ):
        self.download_dir = download_dir
        self.sessao = criar_sessao_download(driver, curso_url)
        self.max_tentativas = max_tentativas
        self.organizar_por_aula = organizar_por_aula
        self.nomes_globais = {}
        self.urls_processadas = set()
        self.encontrados = 0
        self.baixados = 0
        self.existentes = 0
        self.falhas = 0

    def _nome_destino(self, item) -> Path:
        tipo_nome = "Vídeo" if item["tipo"] == "video" else "PDF"
        if self.organizar_por_aula and item["aula_num"] != 0:
            subpasta_tipo = "videos" if item["tipo"] == "video" else "pdfs"
            subpasta = Path(f"aula_{item['aula_num']:02d}") / subpasta_tipo
            base_name = safe_filename(
                f"{tipo_nome} {item['item_num']:02d} - {item['titulo']}"
            )
            extensao = item["extensao"]
            chave = str(subpasta / base_name)
            quantidade = self.nomes_globais.get(chave, 0) + 1
            self.nomes_globais[chave] = quantidade
            sufixo = f" ({quantidade})" if quantidade > 1 else ""
            return subpasta / f"{base_name}{sufixo}{extensao}"

        origem = "Curso" if item["aula_num"] == 0 else f"Aula {item['aula_num']:02d}"
        base_name = safe_filename(
            f"{origem} - {tipo_nome} {item['item_num']:02d} - {item['titulo']}"
        )
        extensao = item["extensao"]

        quantidade = self.nomes_globais.get(base_name, 0) + 1
        self.nomes_globais[base_name] = quantidade
        if quantidade > 1:
            return Path(f"{base_name} ({quantidade}){extensao}")
        return Path(f"{base_name}{extensao}")

    def baixar(self, item) -> bool:
        url = item["url"]
        if url in self.urls_processadas:
            print("      ⏭️ Link repetido, ignorando.")
            return False
        self.urls_processadas.add(url)
        self.encontrados += 1

        final_name = self._nome_destino(item)
        destino = self.download_dir / final_name
        temporario = destino.with_suffix(destino.suffix + ".part")
        destino.parent.mkdir(parents=True, exist_ok=True)

        if destino.exists() and destino.stat().st_size > 0:
            self.existentes += 1
            print(f"      ⏭️ Já existe: {final_name}")
            return True

        for tentativa in range(1, self.max_tentativas + 1):
            print(
                f"      ⬇️ Arquivo encontrado #{self.encontrados}: {final_name} "
                f"(tentativa {tentativa}/{self.max_tentativas})",
                flush=True,
            )
            try:
                with self.sessao.get(url, stream=True, timeout=(30, 120)) as resposta:
                    resposta.raise_for_status()
                    content_type = (resposta.headers.get("Content-Type") or "").lower()
                    if "text/html" in content_type:
                        raise RuntimeError(
                            "o servidor devolveu HTML em vez do arquivo; o link pode "
                            "ter expirado ou a sessão pode não estar autorizada"
                        )

                    total = int(resposta.headers.get("Content-Length") or 0)
                    recebido = 0
                    ultimo_percentual = -10
                    ultimo_aviso = time.monotonic()
                    with open(temporario, "wb") as arquivo:
                        for chunk in resposta.iter_content(chunk_size=1024 * 512):
                            if not chunk:
                                continue
                            arquivo.write(chunk)
                            recebido += len(chunk)

                            agora = time.monotonic()
                            percentual = int(recebido * 100 / total) if total else 0
                            mostrar = (
                                total and percentual >= ultimo_percentual + 10
                            ) or agora - ultimo_aviso >= 5
                            if mostrar:
                                if total:
                                    progresso = (
                                        f"{percentual:3d}% "
                                        f"({formatar_tamanho(recebido)}/{formatar_tamanho(total)})"
                                    )
                                    ultimo_percentual = percentual
                                else:
                                    progresso = formatar_tamanho(recebido)
                                print(f"         Progresso: {progresso}", flush=True)
                                ultimo_aviso = agora

                temporario.replace(destino)
                self.baixados += 1
                print(
                    f"      ✅ Salvo ({formatar_tamanho(destino.stat().st_size)}): "
                    f"{destino}"
                )
                return True
            except Exception as e:
                print(f"      ❌ Erro ao baixar: {e}")
                if tentativa < self.max_tentativas:
                    time.sleep(3)

        self.falhas += 1
        print(f"      🚩 Falha definitiva depois de {self.max_tentativas} tentativas.")
        return False

    def resumo(self):
        print("\n🎉 Varredura e downloads concluídos.")
        print(f"   Arquivos únicos encontrados: {self.encontrados}")
        print(f"   Baixados nesta execução: {self.baixados}")
        print(f"   Já existentes: {self.existentes}")
        print(f"   Falhas: {self.falhas}")


def registrar_e_baixar(item, arquivo_links, gerenciador: GerenciadorDownloads):
    """Persiste o link imediatamente e inicia o download do item."""
    if item["url"] in gerenciador.urls_processadas:
        gerenciador.baixar(item)
        return

    titulo_log = item["titulo"].replace(";", ",")
    arquivo_links.write(
        f"{item['aula_num']:02d};{item['tipo']};"
        f"{item['item_num']:02d};{titulo_log};{item['url']}\n"
    )
    arquivo_links.flush()
    gerenciador.baixar(item)


def main():
    args = ler_argumentos()
    email, password = pedir_credenciais()
    curso_id = extrair_curso_id(args.curso_id) if args.curso_id else pedir_curso_id()
    curso_url = montar_curso_url(curso_id)
    if args.pasta_curso:
        pasta_base = args.pasta_curso.resolve()
        if not pasta_base.is_dir():
            raise RuntimeError(f"A pasta do curso não existe: {pasta_base}")
        print(f"📁 Pasta existente do curso: {pasta_base}")
    else:
        pasta_base = escolher_download_dir()
    driver = create_edge_driver(pasta_base)
    try:
        do_login(driver, email, password)

        aulas = listar_aulas(driver, curso_url)
        if args.aulas:
            numeros_solicitados = set(args.aulas)
            aulas = [aula for aula in aulas if aula["num"] in numeros_solicitados]
            numeros_encontrados = {aula["num"] for aula in aulas}
            faltantes = sorted(numeros_solicitados - numeros_encontrados)
            if faltantes:
                raise RuntimeError(
                    "Aulas solicitadas não encontradas no curso: "
                    + ", ".join(str(numero) for numero in faltantes)
                )
            print(
                "🎯 Filtro de aulas: "
                + ", ".join(f"Aula {aula['num']:02d}" for aula in aulas)
            )

        if args.pasta_curso:
            download_dir = pasta_base
        else:
            download_dir = criar_pasta_do_curso(pasta_base, driver, curso_id)
        gerenciador = GerenciadorDownloads(
            download_dir,
            driver,
            curso_url,
            organizar_por_aula=args.organizar_por_aula,
        )
        execucao_filtrada = bool(
            args.pasta_curso
            or args.aulas
            or args.videos_selecionados
            or args.somente_videos
        )
        nome_links = (
            "links_estrategia_conteudo_retry.txt"
            if execucao_filtrada
            else "links_estrategia_conteudo.txt"
        )
        out_txt = download_dir / nome_links

        if args.somente_pdfs:
            print(
                "\n📄 Modo somente PDFs ativado; nenhum vídeo será "
                "procurado ou baixado."
            )
        elif args.somente_videos:
            print("\n🎬 Modo somente vídeos ativado; nenhum PDF será procurado.")
            if args.videos_selecionados:
                print(
                    "   Vídeos selecionados: "
                    + ", ".join(
                        f"{numero:02d}" for numero in sorted(args.videos_selecionados)
                    )
                )
        else:
            print(
                "\n⬇️ Modo completo: todos os PDFs e vídeos serão procurados e baixados."
            )
        print("   O download começará assim que cada arquivo for localizado.")
        modo_links = "a" if execucao_filtrada else "w"
        escrever_cabecalho = not out_txt.exists() or out_txt.stat().st_size == 0
        with open(out_txt, modo_links, encoding="utf-8") as arquivo_links:
            if escrever_cabecalho:
                arquivo_links.write("aula;tipo;numero;titulo;url\n")
                arquivo_links.flush()

            # A página geral do curso às vezes contém apostilas ou materiais
            # que não reaparecem dentro de nenhuma aula.
            if not args.somente_videos and not args.aulas:
                print("\n➡️ Procurando PDFs gerais na página do curso...")
                for item in iterar_pdfs_da_aula_atual(
                    driver, 0, "Materiais gerais do curso"
                ):
                    registrar_e_baixar(item, arquivo_links, gerenciador)

            for posicao, aula in enumerate(aulas, start=1):
                num = aula["num"]
                nome = aula["nome"]
                href = aula["href"]

                driver.get(href)
                WebDriverWait(driver, 30).until(
                    lambda d: (
                        d.execute_script("return document.readyState") == "complete"
                    )
                )
                # O painel é uma SPA: o HTML pode estar "complete" antes de
                # React terminar de inserir materiais e vídeos.
                time.sleep(2.2)
                gerenciador.sessao.headers["Referer"] = href

                print(
                    f"\n➡️ Aula {posicao}/{len(aulas)}: {nome} "
                    f"(número identificado: {num:02d})"
                )

                fontes = []
                if not args.somente_videos:
                    fontes.append(iterar_pdfs_da_aula_atual(driver, num, nome))
                if not args.somente_pdfs:
                    fontes.append(
                        iterar_videos_da_aula_atual(
                            driver,
                            num,
                            nome,
                            videos_selecionados=args.videos_selecionados,
                            tentativas_links=args.tentativas_links,
                        )
                    )
                for fonte in fontes:
                    for item in fonte:
                        registrar_e_baixar(item, arquivo_links, gerenciador)

        print(f"\n✅ Links registrados continuamente em: {out_txt}")
        gerenciador.resumo()

        if (
            args.videos_selecionados
            and gerenciador.encontrados < len(args.videos_selecionados)
        ):
            raise RuntimeError(
                f"Foram localizados {gerenciador.encontrados} de "
                f"{len(args.videos_selecionados)} vídeos solicitados. "
                "Veja os avisos acima e execute o arquivo de tentativa novamente."
            )

        print("\n✅ Processo completo.")
    finally:
        # se quiser fechar o navegador no final:
        # driver.quit()
        pass


def executar_com_tratamento_de_erros() -> int:
    try:
        main()
        return 0
    except SystemExit as e:
        # argparse usa SystemExit(0) para --help; cancelamentos das janelas usam
        # uma mensagem e também são encerramentos normais.
        if isinstance(e.code, str):
            print(f"\nℹ️ {e.code}")
            return 0
        return int(e.code or 0)
    except KeyboardInterrupt:
        print("\nℹ️ Processo interrompido pelo usuário.")
        return 130
    except Exception as e:
        mensagem = str(e) or e.__class__.__name__
        print(f"\n❌ Não foi possível concluir: {mensagem}")
        if os.getenv("ESTRATEGIA_DEBUG") == "1":
            traceback.print_exc()
        try:
            import tkinter as tk
            from tkinter import messagebox

            raiz = tk.Tk()
            raiz.withdraw()
            raiz.attributes("-topmost", True)
            messagebox.showerror(
                "Estratégia Curso Downloader",
                f"Não foi possível concluir:\n\n{mensagem}\n\n"
                "Veja também a janela de progresso para mais detalhes.",
                parent=raiz,
            )
            raiz.destroy()
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(executar_com_tratamento_de_erros())
