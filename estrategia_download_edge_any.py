import argparse
import os
import re
import sys
import time
import traceback
import unicodedata
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


def configurar_saida_terminal():
    """Evita falhas quando um console antigo não suporta algum símbolo Unicode."""
    for fluxo in (sys.stdout, sys.stderr):
        try:
            fluxo.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


configurar_saida_terminal()

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
        description="Baixa vídeos, PDFs, slides e outros materiais do Estratégia."
    )
    parser.add_argument(
        "--pdfs-e-slides",
        dest="pdfs_e_slides",
        action="store_true",
        help="baixa todos os PDFs e slides, sem vídeos ou mapas mentais",
    )
    parser.add_argument(
        "--somente-pdfs",
        dest="pdfs_e_slides",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    return parser.parse_args()


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


def montar_nome_pasta_curso(curso_id: str, unix_timestamp=None) -> str:
    """Monta um nome estável, rastreável e independente do HTML da página."""
    if unix_timestamp is None:
        unix_timestamp = int(time.time())
    return f"CURSO_ESTRATEGIA_{curso_id}_{int(unix_timestamp)}"


def criar_pasta_do_curso(
    pasta_base: Path, driver, curso_id: str, unix_timestamp=None
) -> Path:
    nome_pasta = montar_nome_pasta_curso(curso_id, unix_timestamp)
    pasta_curso = pasta_base / nome_pasta
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

    print(f"📚 ID do curso: {curso_id}")
    print(f"🗂️ Pasta desta execução: {nome_pasta}")
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


def iterar_videos_da_aula_atual(driver, aula_num: int, aula_nome: str):
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
        try:
            videos = driver.find_elements(By.CSS_SELECTOR, "span.VideoItem-info-title")
            if idx >= len(videos):
                break

            vid_el = videos[idx]
            titulo_video = (vid_el.text or f"video_{idx + 1}").strip()

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
            if not ok:
                print(
                    f"      ⚠️ Vídeo {idx + 1:02d}: não consegui abrir "
                    "'Opções de download'"
                )
                continue

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
                    filter(None, [botao.text, botao.get_attribute("aria-label")])
                ).lower()
                if re.search(r"\.mp4(?:$|[?#])", href_botao, re.IGNORECASE) or (
                    "vídeo" in descricao
                    and ("baixar" in descricao or "download" in descricao)
                ):
                    links_sem_resolucao.append(botao)

            if candidatos:
                resolucao_escolhida, botao_escolhido = max(
                    candidatos, key=lambda item: item[0]
                )
                print(
                    f"      🎞️ Vídeo {idx + 1:02d}: melhor qualidade disponível: "
                    f"{resolucao_escolhida}p."
                )
            elif links_sem_resolucao:
                botao_escolhido = links_sem_resolucao[0]
                print(
                    f"      ℹ️ Vídeo {idx + 1:02d}: usando o link disponível; "
                    "a resolução não foi informada."
                )
            else:
                print(f"      ⚠️ Vídeo {idx + 1:02d}: nenhum link de download apareceu")
                continue

            href = botao_escolhido.get_attribute("href")
            if not href:
                print(f"      ⚠️ Vídeo {idx + 1:02d}: botão de download sem href")
                continue

            base_title = safe_filename(titulo_video)
            if base_title in nomes_usados_nesta_aula:
                c = nomes_usados_nesta_aula[base_title] + 1
                nomes_usados_nesta_aula[base_title] = c
                base_title = f"{base_title} ({c})"
            else:
                nomes_usados_nesta_aula[base_title] = 1

            print(f"      ✅ Vídeo {idx + 1:02d}: {base_title} -> {href}")

            yield {
                "tipo": "video",
                "aula_num": aula_num,
                "aula_nome": safe_filename(aula_nome),
                "item_num": idx + 1,
                "titulo": base_title,
                "extensao": ".mp4",
                "url": href,
            }
        except Exception as e:
            print(f"      ❌ Erro no vídeo {idx + 1:02d}: {e}")


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


def _normalizar_texto(valor: str) -> str:
    decomposicao = unicodedata.normalize("NFKD", valor)
    return "".join(
        caractere for caractere in decomposicao if not unicodedata.combining(caractere)
    ).lower()


def classificar_material(href: str, descricao: str):
    """Classifica cartões mesmo quando URL e texto não contêm a extensão."""
    texto = _normalizar_texto(f"{descricao} {unquote(href)}")
    acao_download = "baixar" in texto or "download" in texto

    if "mapa mental" in texto or "mapa-mental" in texto:
        return "mapa_mental"
    if re.search(r"\bslides?\b", texto) or ("apresentacao" in texto and acao_download):
        return "slides"

    sinais_pdf = (
        r"\.pdf(?:$|[?#&])",
        r"/pdf/",
        r"[?&](?:format|type|filetype)=pdf(?:&|$)",
        r"\bpdf\b",
        r"livro (?:eletronico|digital)",
        r"versao (?:simplificada|original)",
        r"marcacao dos aprovados",
        r"apostila",
        r"aula em texto",
        r"material escrito",
    )
    if any(re.search(padrao, texto) for padrao in sinais_pdf):
        return "pdf"

    extensoes = r"\.(?:pptx?|docx?|xlsx?|zip|rar|png|jpe?g)(?:$|[?#&])"
    if (
        re.search(extensoes, texto)
        or (acao_download and not re.search(r"\b\d{3,4}\s*p\b", texto))
        or re.search(r"/(?:materiais?|arquivos?|files?)/", texto)
    ):
        return "material"
    return None


def _url_do_elemento(elemento, url_atual: str) -> str:
    atributos = (
        "href",
        "src",
        "data",
        "data-href",
        "data-url",
        "data-download-url",
        "data-file-url",
    )

    def procurar_atributos(alvo):
        for atributo in atributos:
            valor = (alvo.get_attribute(atributo) or "").strip()
            if valor and not valor.startswith(("javascript:", "data:", "#")):
                return urljoin(url_atual, valor)
        return ""

    url = procurar_atributos(elemento)
    if url:
        return url

    for xpath in ("./ancestor::a[@href][1]", ".//a[@href][1]"):
        try:
            relacionado = elemento.find_element(By.XPATH, xpath)
            url = procurar_atributos(relacionado)
            if url:
                return url
        except Exception:
            continue

    # Alguns botões guardam a URL diretamente no onclick.
    onclick = elemento.get_attribute("onclick") or ""
    candidato = re.search(r"['\"]((?:https?://|/)[^'\"]+)['\"]", onclick, re.IGNORECASE)
    return urljoin(url_atual, candidato.group(1)) if candidato else ""


def _titulo_material(elemento, href: str, indice: int, tipo: str) -> str:
    titulo = _texto_link(elemento)
    nome_url = Path(unquote(urlparse(href).path)).name
    rotulo = {
        "pdf": "PDF",
        "slides": "Slides",
        "mapa_mental": "Mapa Mental",
        "material": "Material",
    }[tipo]

    if not titulo or _normalizar_texto(titulo) in {
        "pdf",
        "baixar pdf",
        "download pdf",
        "baixar",
        "download",
    }:
        titulo = nome_url or f"{rotulo} {indice:02d}"

    titulo = re.sub(
        r"\.(?:pdf|pptx?|docx?|xlsx?|zip|rar|png|jpe?g)$",
        "",
        titulo,
        flags=re.IGNORECASE,
    )
    titulo = re.sub(r"\s+", " ", titulo).strip(" .-_")
    return safe_filename(titulo) or f"{rotulo} {indice:02d}"


def _extensao_material(tipo: str, href: str) -> str:
    extensao = Path(unquote(urlparse(href).path)).suffix.lower()
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
    }
    if extensao in permitidas:
        return extensao
    return ".bin" if tipo == "material" else ".pdf"


def iterar_materiais_da_aula_atual(
    driver,
    aula_num: int,
    aula_nome: str,
    tipos_permitidos=None,
):
    """Entrega PDFs, slides, mapas mentais e outros materiais sem duplicatas."""
    vistos = set()
    encontrados = []
    sem_url = set()

    seletor = (
        "a, iframe[src], embed[src], object[data], [data-href], "
        "[data-url], [data-download-url], [data-file-url], [onclick], "
        "button, [role='button'], [class*='download'], [class*='Download']"
    )
    for elemento in driver.find_elements(By.CSS_SELECTOR, seletor):
        try:
            descricao = _texto_link(elemento)
            href = _url_do_elemento(elemento, driver.current_url)
            tipo = classificar_material(href, descricao)
            if not tipo or (tipos_permitidos and tipo not in tipos_permitidos):
                continue
            if not href:
                if descricao:
                    sem_url.add(" ".join(descricao.split())[:160])
                continue
            if href in vistos:
                continue

            vistos.add(href)
            encontrados.append((elemento, href, tipo))
        except Exception:
            continue

    print(f"   📚 Materiais encontrados: {len(encontrados)}")
    for descricao in sorted(sem_url):
        print(f"      ⚠️ Material reconhecido, mas sem URL acessível: {descricao}")

    rotulos = {
        "pdf": "PDF",
        "slides": "Slides",
        "mapa_mental": "Mapa Mental",
        "material": "Material",
    }
    for indice, (elemento, href, tipo) in enumerate(encontrados, start=1):
        titulo = _titulo_material(elemento, href, indice, tipo)
        print(f"      ✅ {rotulos[tipo]} {indice:02d}: {titulo} -> {href}")
        yield {
            "tipo": tipo,
            "aula_num": aula_num,
            "aula_nome": safe_filename(aula_nome),
            "item_num": indice,
            "titulo": titulo,
            "extensao": _extensao_material(tipo, href),
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


def formatar_duracao(segundos) -> str:
    if segundos is None or segundos < 0:
        return "--:--"
    total = int(segundos + 0.5)
    horas, resto = divmod(total, 3600)
    minutos, segundos = divmod(resto, 60)
    if horas:
        return f"{horas:02d}:{minutos:02d}:{segundos:02d}"
    return f"{minutos:02d}:{segundos:02d}"


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


class GerenciadorDownloads:
    def __init__(self, download_dir: Path, driver, curso_url: str, max_tentativas=3):
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

    def configurar_total_aulas(self, total_aulas: int):
        self.total_aulas = total_aulas

    def iniciar_aula(self, posicao: int):
        self.aula_atual = posicao
        self.bytes_inicio_aula = self._bytes_logicos_conhecidos()

    def concluir_aula(self):
        tamanho = self._bytes_logicos_conhecidos() - self.bytes_inicio_aula
        if tamanho > 0:
            self.tamanhos_aulas_concluidas.append(tamanho)

    def _bytes_prontos(self) -> int:
        return self.bytes_baixados + self.bytes_existentes

    def _bytes_logicos_conhecidos(self) -> int:
        return self._bytes_prontos() + self.bytes_falhos_conhecidos

    def _velocidade_media(self, agora: float, recebido_atual: int = 0) -> float:
        if self.inicio_downloads is None:
            return 0.0
        decorrido = max(agora - self.inicio_downloads, 0.001)
        return (self.bytes_baixados + recebido_atual) / decorrido

    def _eta_curso(self, total_conhecido: int, pronto: int, velocidade: float):
        if (
            not self.tamanhos_aulas_concluidas
            or not self.aula_atual
            or not velocidade
            or self.falhas
        ):
            return None

        media_aula = sum(self.tamanhos_aulas_concluidas) / len(
            self.tamanhos_aulas_concluidas
        )
        conhecido_na_aula = max(total_conhecido - self.bytes_inicio_aula, 0)
        restante_aula_atual = max(media_aula - conhecido_na_aula, 0)
        aulas_futuras = max(self.total_aulas - self.aula_atual, 0)
        restante_estimado = (
            max(total_conhecido - pronto, 0)
            + restante_aula_atual
            + media_aula * aulas_futuras
        )
        return restante_estimado / velocidade

    def _mostrar_progresso(
        self,
        recebido: int,
        total_item: int,
        inicio_item: float,
        *,
        final=False,
    ):
        agora = time.monotonic()
        if not final and agora - self._ultimo_progresso < 1:
            return
        self._ultimo_progresso = agora

        decorrido_item = max(agora - inicio_item, 0.001)
        velocidade_item = recebido / decorrido_item
        restante_item = max(total_item - recebido, 0) if total_item else None
        eta_item = (
            restante_item / velocidade_item if velocidade_item and total_item else None
        )

        pronto_base = self._bytes_prontos()
        total_base = self._bytes_logicos_conhecidos()
        pronto = pronto_base + recebido
        total_conhecido = total_base + total_item if total_item else 0
        velocidade_media = self._velocidade_media(agora, recebido)

        if total_item:
            percentual_item = min(recebido * 100 / total_item, 100)
            item_texto = (
                f"Item #{self.encontrados} {percentual_item:5.1f}% "
                f"{formatar_tamanho(recebido)}/{formatar_tamanho(total_item)}"
            )
        else:
            item_texto = f"Item #{self.encontrados} {formatar_tamanho(recebido)}/?"

        item_texto += (
            f" {formatar_tamanho(int(velocidade_item))}/s "
            f"ETA {formatar_duracao(eta_item)}"
        )

        arquivos_prontos = self.baixados + self.existentes + (1 if final else 0)
        if total_conhecido:
            percentual_total = min(pronto * 100 / total_conhecido, 100)
            eta_conhecido = (
                (total_conhecido - pronto) / velocidade_media
                if velocidade_media and not self.falhas
                else None
            )
            total_texto = (
                f"Conhecido {arquivos_prontos}/{self.encontrados} "
                f"{percentual_total:5.1f}% {formatar_tamanho(pronto)}/"
                f"{formatar_tamanho(total_conhecido)} "
                f"média {formatar_tamanho(int(velocidade_media))}/s "
                f"ETA {formatar_duracao(eta_conhecido)}"
            )
        else:
            total_texto = (
                f"Conhecido {arquivos_prontos}/{self.encontrados} "
                f"{formatar_tamanho(pronto)} baixados; tamanho em descoberta "
                f"média {formatar_tamanho(int(velocidade_media))}/s ETA --:--"
            )

        eta_curso = self._eta_curso(total_conhecido, pronto, velocidade_media)
        if self.total_aulas:
            if eta_curso is not None:
                eta_curso_texto = formatar_duracao(eta_curso)
            elif self.falhas:
                eta_curso_texto = "indisponível"
            else:
                eta_curso_texto = "calculando"
            curso_texto = (
                f"Curso aula {self.aula_atual}/{self.total_aulas} "
                f"ETA~ {eta_curso_texto}"
            )
        else:
            curso_texto = "Curso ETA~ calculando"

        linha = f"         {item_texto} | {total_texto} | {curso_texto}"
        self._largura_progresso = max(self._largura_progresso, len(linha))
        fim = "\n" if final else ""
        print(f"\r{linha.ljust(self._largura_progresso)}", end=fim, flush=True)
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
        base_name = safe_filename(
            f"{origem} - {tipo_nome} {item['item_num']:02d} - {item['titulo']}"
        )
        extensao = item["extensao"]

        quantidade = self.nomes_globais.get(base_name, 0) + 1
        self.nomes_globais[base_name] = quantidade
        if quantidade > 1:
            return f"{base_name} ({quantidade}){extensao}"
        return f"{base_name}{extensao}"

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

        if destino.exists() and destino.stat().st_size > 0:
            self.existentes += 1
            self.bytes_existentes += destino.stat().st_size
            print(f"      ⏭️ Já existe: {final_name}")
            return True

        ultimo_total = 0
        for tentativa in range(1, self.max_tentativas + 1):
            print(
                f"      ⬇️ Arquivo encontrado #{self.encontrados}: {final_name} "
                f"(tentativa {tentativa}/{self.max_tentativas})",
                flush=True,
            )
            try:
                inicio_item = time.monotonic()
                if self.inicio_downloads is None:
                    self.inicio_downloads = inicio_item
                self._ultimo_progresso = 0
                with self.sessao.get(url, stream=True, timeout=(30, 120)) as resposta:
                    resposta.raise_for_status()
                    content_type = (resposta.headers.get("Content-Type") or "").lower()
                    if "text/html" in content_type:
                        raise RuntimeError(
                            "o servidor devolveu HTML em vez do arquivo; o link pode "
                            "ter expirado ou a sessão pode não estar autorizada"
                        )

                    extensao_real = detectar_extensao_resposta(
                        resposta, url, destino.suffix
                    )
                    if extensao_real != destino.suffix.lower():
                        destino = destino.with_suffix(extensao_real)
                        temporario = destino.with_suffix(destino.suffix + ".part")
                        final_name = destino.name
                        print(
                            f"      ℹ️ Formato detectado pelo servidor: {extensao_real}"
                        )
                        if destino.exists() and destino.stat().st_size > 0:
                            self.existentes += 1
                            self.bytes_existentes += destino.stat().st_size
                            print(f"      ⏭️ Já existe: {final_name}")
                            return True

                    total = int(resposta.headers.get("Content-Length") or 0)
                    ultimo_total = total
                    recebido = 0
                    with open(temporario, "wb") as arquivo:
                        for chunk in resposta.iter_content(chunk_size=1024 * 512):
                            if not chunk:
                                continue
                            arquivo.write(chunk)
                            recebido += len(chunk)
                            self._mostrar_progresso(recebido, total, inicio_item)

                self._mostrar_progresso(recebido, total, inicio_item, final=True)

                temporario.replace(destino)
                self.baixados += 1
                self.bytes_baixados += destino.stat().st_size
                print(
                    f"      ✅ Salvo ({formatar_tamanho(destino.stat().st_size)}): "
                    f"{destino}"
                )
                return True
            except Exception as e:
                self._encerrar_linha_progresso()
                print(f"      ❌ Erro ao baixar: {e}")
                if tentativa < self.max_tentativas:
                    time.sleep(3)

        self.falhas += 1
        self.bytes_falhos_conhecidos += ultimo_total
        print(f"      🚩 Falha definitiva depois de {self.max_tentativas} tentativas.")
        return False

    def resumo(self):
        print("\n🎉 Varredura e downloads concluídos.")
        print(f"   Arquivos únicos encontrados: {self.encontrados}")
        print(f"   Baixados nesta execução: {self.baixados}")
        print(f"   Já existentes: {self.existentes}")
        print(f"   Falhas: {self.falhas}")
        print(
            f"   Volume baixado nesta execução: {formatar_tamanho(self.bytes_baixados)}"
        )
        if self.inicio_downloads is not None:
            decorrido = time.monotonic() - self.inicio_downloads
            velocidade = self.bytes_baixados / max(decorrido, 0.001)
            print(f"   Tempo desde o primeiro download: {formatar_duracao(decorrido)}")
            print(f"   Velocidade média efetiva: {formatar_tamanho(int(velocidade))}/s")


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
    curso_id = pedir_curso_id()
    curso_url = montar_curso_url(curso_id)
    pasta_base = escolher_download_dir()
    driver = create_edge_driver(pasta_base)
    try:
        do_login(driver, email, password)

        aulas = listar_aulas(driver, curso_url)
        download_dir = criar_pasta_do_curso(pasta_base, driver, curso_id)
        gerenciador = GerenciadorDownloads(download_dir, driver, curso_url)
        gerenciador.configurar_total_aulas(len(aulas))
        out_txt = download_dir / "links_estrategia_conteudo.txt"

        tipos_permitidos = {"pdf", "slides"} if args.pdfs_e_slides else None
        if args.pdfs_e_slides:
            print(
                "\n📄 Modo PDFs + slides ativado; vídeos, mapas mentais e "
                "outros materiais não serão baixados."
            )
        else:
            print(
                "\n⬇️ Modo completo: vídeos, PDFs, slides, mapas mentais e "
                "demais materiais serão procurados e baixados."
            )
        print("   O download começará assim que cada arquivo for localizado.")
        with open(out_txt, "w", encoding="utf-8") as arquivo_links:
            arquivo_links.write("aula;tipo;numero;titulo;url\n")
            arquivo_links.flush()

            # A página geral do curso às vezes contém apostilas ou materiais
            # que não reaparecem dentro de nenhuma aula.
            print("\n➡️ Procurando materiais gerais na página do curso...")
            for item in iterar_materiais_da_aula_atual(
                driver,
                0,
                "Materiais gerais do curso",
                tipos_permitidos,
            ):
                registrar_e_baixar(item, arquivo_links, gerenciador)

            for posicao, aula in enumerate(aulas, start=1):
                gerenciador.iniciar_aula(posicao)
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

                fontes = [
                    iterar_materiais_da_aula_atual(driver, num, nome, tipos_permitidos)
                ]
                if not args.pdfs_e_slides:
                    fontes.append(iterar_videos_da_aula_atual(driver, num, nome))
                for fonte in fontes:
                    for item in fonte:
                        registrar_e_baixar(item, arquivo_links, gerenciador)
                gerenciador.concluir_aula()

        print(f"\n✅ Links registrados continuamente em: {out_txt}")
        gerenciador.resumo()

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
