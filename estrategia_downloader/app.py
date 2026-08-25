import argparse
import os
import re
import sys
import time
import traceback
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

from selenium.common.exceptions import (
    ElementClickInterceptedException,
    ElementNotInteractableException,
    NoSuchElementException,
    NoSuchWindowException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from interface_web import DownloadCancelado, InterfaceWeb, SaidaPainel

from .alerts import RecuperadorAlertas
from .browser import create_edge_driver, diagnostico_browser
from .config import (
    DISCOVERY_MAX_ROUNDS,
    DISCOVERY_SCROLL_PAUSE,
    DISCOVERY_STABLE_ROUNDS,
    LOGIN_TIMEOUT,
    LOGIN_URL,
    SELENIUM_SHORT_WAIT,
    SELENIUM_WAIT_TIMEOUT,
    VIDEO_OPTIONS_TIMEOUT,
    VIDEO_RECOVERY_PASSES,
    VIDEO_SELECTION_RETRIES,
    pasta_download_padrao,
)
from .diagnostics import criar_diagnostico
from .discovery import classificar_material as classificar_material_puro
from .downloads import (
    GerenciadorDownloads as GerenciadorDownloadsNovo,
)
from .errors import ConteudoIncompletoError, mensagem_usuario_para_erro
from .utils import (
    chave_deduplicacao_url,
    formatar_tamanho,
    normalizar_texto,
    safe_filename,
    sanitizar_url,
    verificar_destino,
)


def configurar_saida_terminal():
    """Evita falhas quando um console antigo não suporta algum símbolo Unicode."""
    for fluxo in (sys.stdout, sys.stderr):
        try:
            fluxo.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


configurar_saida_terminal()

DEFAULT_DOWNLOAD_DIR = pasta_download_padrao()
VIDEO_TITLE_SELECTOR = (
    "span.VideoItem-info-title, [class*='VideoItem-info-title'], "
    "[class*='VideoItem'] [class*='title']"
)


def ler_argumentos():
    parser = argparse.ArgumentParser(
        description="Baixa vídeos, PDFs, slides e outros materiais do Estratégia."
    )
    parser.add_argument(
        "--pdfs-e-slides",
        dest="pdfs_e_slides",
        action="store_true",
        help="baixa PDFs, slides e mapas mentais, sem vídeos",
    )
    parser.add_argument(
        "--somente-pdfs",
        dest="pdfs_e_slides",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    return parser.parse_args()


def tipos_permitidos_modo_reduzido():
    """Mapas mentais são materiais PDF e fazem parte do modo reduzido."""
    return {"pdf", "slides", "mapa_mental"}


def extrair_curso_id(valor: str):
    valor = valor.strip()
    if re.fullmatch(r"\d+", valor):
        return valor
    encontrado = re.search(r"/cursos/(\d+)(?=[/?#]|$)", valor)
    return encontrado.group(1) if encontrado else None


def montar_curso_url(curso_id: str) -> str:
    return (
        f"https://www.estrategiaconcursos.com.br/app/dashboard/cursos/{curso_id}/aulas"
    )


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


def _elemento_visivel(driver, css_selector: str) -> bool:
    """Retorna True se ao menos um elemento do seletor estiver visível."""
    try:
        return any(
            el.is_displayed()
            for el in driver.find_elements(By.CSS_SELECTOR, css_selector)
        )
    except StaleElementReferenceException:
        return False
    except (NoSuchWindowException, WebDriverException):
        raise


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


def _executar_selenium(alertas, operacao, descricao: str):
    if alertas is None:
        return operacao()
    return alertas.executar_leitura(operacao, descricao=descricao)


def _assinatura_elementos(elementos, atributos=()) -> tuple:
    """Cria uma assinatura ordenada para detectar listas React ainda crescendo."""
    assinatura = []
    for elemento in elementos:
        try:
            valores = [(elemento.text or "").strip()]
            valores.extend((elemento.get_attribute(nome) or "").strip() for nome in atributos)
            assinatura.append(tuple(valores))
        except StaleElementReferenceException:
            assinatura.append(("<stale>",))
    return tuple(assinatura)


def _clicar_controle_carregar_mais(driver, alertas=None) -> bool:
    """Aciona um controle explícito de paginação sem clicar em links de conteúdo."""
    controles = _executar_selenium(
        alertas,
        lambda: driver.find_elements(
            By.CSS_SELECTOR,
            "button, [role='button'], a[class*='load'], a[class*='Load']",
        ),
        "procurar controle para carregar mais conteúdo",
    )
    padrao = re.compile(
        r"^(?:carregar|mostrar) mais(?: aulas| videos| itens| conteudo)?$|"
        r"^ver mais (?:aulas|videos|itens|conteudo)$|^mais aulas$"
    )
    for controle in controles:
        try:
            texto = normalizar_texto(
                " ".join(
                    filter(
                        None,
                        (
                            controle.text,
                            controle.get_attribute("aria-label"),
                            controle.get_attribute("title"),
                        ),
                    )
                )
            )
            if not padrao.fullmatch(texto):
                continue
            if not controle.is_displayed() or (
                hasattr(controle, "is_enabled") and not controle.is_enabled()
            ):
                continue

            def clicar(elemento=controle):
                try:
                    elemento.click()
                except (ElementClickInterceptedException, ElementNotInteractableException):
                    driver.execute_script("arguments[0].click();", elemento)

            _executar_selenium(alertas, clicar, "carregar mais conteúdo")
            return True
        except (NoSuchElementException, StaleElementReferenceException):
            continue
    return False


def _carregar_lista_dinamica(
    driver,
    by,
    seletor: str,
    *,
    atributos=(),
    alertas=None,
    descricao="conteúdo dinâmico",
    max_rodadas=DISCOVERY_MAX_ROUNDS,
    rodadas_estaveis=DISCOVERY_STABLE_ROUNDS,
    pausa=DISCOVERY_SCROLL_PAUSE,
):
    """Rola/pagina até a lista e a altura da página ficarem realmente estáveis."""
    assinatura_anterior = None
    altura_anterior = None
    estaveis = 0

    for _rodada in range(max_rodadas):
        elementos = _executar_selenium(
            alertas,
            lambda: driver.find_elements(by, seletor),
            f"ler {descricao}",
        )
        assinatura = _assinatura_elementos(elementos, atributos)
        altura = _executar_selenium(
            alertas,
            lambda: driver.execute_script(
                "return Math.max(document.body.scrollHeight, "
                "document.documentElement.scrollHeight);"
            ),
            f"medir {descricao}",
        )
        clicou = _clicar_controle_carregar_mais(driver, alertas)

        if elementos:
            ultimo = elementos[-1]
            _executar_selenium(
                alertas,
                lambda elemento=ultimo: driver.execute_script(
                    "arguments[0].scrollIntoView({block:'end'});", elemento
                ),
                f"rolar até o fim de {descricao}",
            )
        _executar_selenium(
            alertas,
            lambda: driver.execute_script(
                "window.scrollTo(0, Math.max(document.body.scrollHeight, "
                "document.documentElement.scrollHeight));"
            ),
            f"rolar a página de {descricao}",
        )

        if (
            not clicou
            and assinatura == assinatura_anterior
            and altura == altura_anterior
        ):
            estaveis += 1
        else:
            estaveis = 0
        assinatura_anterior = assinatura
        altura_anterior = altura

        if estaveis >= rodadas_estaveis:
            break
        time.sleep(pausa)

    return _executar_selenium(
        alertas,
        lambda: driver.find_elements(by, seletor),
        f"confirmar lista completa de {descricao}",
    )


def do_login(
    driver,
    email: str,
    password: str,
    verificar_cancelamento=None,
    alertas=None,
):
    if alertas is None:
        driver.get(LOGIN_URL)
    else:
        alertas.navegar(LOGIN_URL, descricao="abrir a página de login")
    wait = WebDriverWait(driver, SELENIUM_WAIT_TIMEOUT)

    # A página pode abrir diretamente no painel quando o perfil do Edge já tem
    # uma sessão válida.
    _executar_selenium(
        alertas,
        lambda: wait.until(
            lambda d: (
                _painel_carregado(d)
                or _elemento_visivel(d, "input[type='email'], input[name='email']")
            )
        ),
        "aguardar a página de login",
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
        if verificar_cancelamento is not None:
            verificar_cancelamento()
        if _executar_selenium(
            alertas, lambda: _login_concluido(driver), "verificar o login"
        ):
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


def listar_aulas(driver, curso_url: str, alertas=None):
    if alertas is None:
        driver.get(curso_url)
    else:
        alertas.navegar(curso_url, descricao="abrir a página do curso")
    wait = WebDriverWait(driver, SELENIUM_WAIT_TIMEOUT)
    _executar_selenium(
        alertas,
        lambda: wait.until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        ),
        "aguardar o carregamento do curso",
    )

    try:
        _executar_selenium(
            alertas,
            lambda: wait.until(
                lambda d: d.find_elements(By.XPATH, "//a[contains(@href,'/aulas/')]")
            ),
            "localizar as aulas",
        )
    except TimeoutException:
        # Deixa a validação abaixo produzir uma mensagem mais útil que um
        # TimeoutException genérico.
        pass

    itens = _carregar_lista_dinamica(
        driver,
        By.XPATH,
        "//a[contains(@href,'/aulas/')]",
        atributos=("href",),
        alertas=alertas,
        descricao="aulas do curso",
    )
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


def aguardar_conteudo_aula(driver, alertas=None, timeout=SELENIUM_WAIT_TIMEOUT):
    """Aguarda a SPA parar de alterar os blocos relevantes da aula."""
    ultima_assinatura = None
    ultima_mudanca = time.monotonic()

    def conteudo_estavel(navegador):
        nonlocal ultima_assinatura, ultima_mudanca
        assinatura = navegador.execute_script(
            """
            const loading = [...document.querySelectorAll(
              "[class*='loading'],[class*='Loading'],[aria-busy='true']"
            )].filter(el => {
              const s = getComputedStyle(el);
              return s.display !== 'none' && s.visibility !== 'hidden';
            }).length;
            const relevant = document.querySelectorAll(
              "span.VideoItem-info-title,a[href],button,[data-url],[data-href]"
            ).length;
            return [document.readyState, loading, relevant, document.body.scrollHeight];
            """
        )
        agora = time.monotonic()
        if assinatura != ultima_assinatura:
            ultima_assinatura = assinatura
            ultima_mudanca = agora
            return False
        return (
            assinatura[0] == "complete"
            and assinatura[1] == 0
            and (agora - ultima_mudanca >= 0.8)
        )

    return _executar_selenium(
        alertas,
        lambda: WebDriverWait(driver, timeout, poll_frequency=0.2).until(
            conteudo_estavel
        ),
        "aguardar o conteúdo da aula",
    )


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
    except WebDriverException:
        pass

    print(f"📚 ID do curso: {curso_id}")
    print(f"🗂️ Pasta desta execução: {nome_pasta}")
    print(f"📁 Conteúdo será salvo em: {pasta_curso}")
    return pasta_curso


def _assinatura_opcoes_video(opcoes) -> frozenset:
    return frozenset((resolucao or 0, href) for resolucao, href in opcoes)


def expandir_opcoes_download(driver, alertas=None, opcoes_anteriores=None):
    if alertas is not None:
        alertas.resolver_pendente(permitir_desconhecido=True)
    # Algumas aulas já deixam as qualidades expandidas automaticamente. Nesse
    # caso, clicar no cabeçalho fecharia justamente o painel que queremos ler.
    opcoes_visiveis = _coletar_opcoes_video(driver)
    assinatura_anterior = _assinatura_opcoes_video(opcoes_anteriores or [])
    if opcoes_visiveis and (
        not assinatura_anterior
        or _assinatura_opcoes_video(opcoes_visiveis) != assinatura_anterior
    ):
        return True

    headers = _executar_selenium(
        alertas,
        lambda: driver.find_elements(
            By.XPATH,
            "//strong[normalize-space()='Opções de download' or "
            "contains(normalize-space(.),'Opções de download')]",
        ),
        "localizar opções de download do vídeo",
    )
    if not headers:
        return False

    header = headers[0]

    container = header
    try:
        container = header.find_element(By.XPATH, "./ancestor::div[1]")
        container = container.find_element(By.XPATH, "./ancestor::div[1]")
    except (NoSuchElementException, StaleElementReferenceException):
        pass

    def clicar_opcoes():
        driver.execute_script(
            "arguments[0].scrollIntoView({block:'center'});", container
        )
        try:
            container.click()
        except (ElementClickInterceptedException, ElementNotInteractableException):
            driver.execute_script("arguments[0].click();", container)

    _executar_selenium(alertas, clicar_opcoes, "abrir opções de download do vídeo")
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
            botao.get_attribute("data-quality"),
            botao.get_attribute("data-resolution"),
            botao.get_attribute("data-label"),
            botao.get_attribute("value"),
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


def _coletar_opcoes_video(driver):
    """Lê URLs de vídeo expostas como href ou atributos data-* visíveis."""
    elementos = driver.find_elements(
        By.CSS_SELECTOR,
        "a[href], [data-href], [data-url], [data-download-url], "
        "[data-file-url], [data-video-url], [data-src], [data-download], "
        "button[data-quality], button[data-resolution], button[onclick], "
        "[role='button'][onclick]",
    )
    opcoes = []
    vistos = set()
    for elemento in elementos:
        try:
            if not elemento.is_displayed():
                continue
            href = _url_do_elemento(elemento, driver.current_url)
            if (
                not href
                or urlparse(href).scheme not in {"http", "https"}
                or href in vistos
            ):
                continue
            resolucao = _resolucao_do_botao(elemento)
            descricao = normalizar_texto(_texto_link(elemento))
            parece_video = bool(
                re.search(r"\.mp4(?:$|[?#])", href, re.IGNORECASE)
                or resolucao
                or (
                    ("video" in descricao or "qualidade" in descricao)
                    and ("baixar" in descricao or "download" in descricao)
                )
            )
            if not parece_video:
                continue
            vistos.add(href)
            opcoes.append((resolucao, href))
        except (NoSuchElementException, StaleElementReferenceException):
            continue
    return opcoes


def _aguardar_opcoes_video(
    driver,
    alertas=None,
    timeout=VIDEO_OPTIONS_TIMEOUT,
    opcoes_anteriores=None,
):
    assinatura_anterior = _assinatura_opcoes_video(opcoes_anteriores or [])

    def opcoes_atualizadas(navegador):
        opcoes = _coletar_opcoes_video(navegador)
        if not opcoes:
            return False
        if (
            assinatura_anterior
            and _assinatura_opcoes_video(opcoes) == assinatura_anterior
        ):
            return False
        return opcoes

    return _executar_selenium(
        alertas,
        lambda: WebDriverWait(driver, timeout, poll_frequency=0.25).until(
            opcoes_atualizadas
        ),
        "aguardar os links de qualidade do vídeo selecionado",
    )


def _selecionar_video_e_obter_opcoes(driver, indice: int, alertas=None):
    titulo_video = f"video_{indice + 1}"
    opcoes_antes_da_selecao = _coletar_opcoes_video(driver) if indice else []
    for tentativa in range(1, VIDEO_SELECTION_RETRIES + 1):
        if alertas is not None:
            alertas.resolver_pendente(permitir_desconhecido=True)
        videos = _executar_selenium(
            alertas,
            lambda: driver.find_elements(By.CSS_SELECTOR, VIDEO_TITLE_SELECTOR),
            "reler vídeos da aula",
        )
        if indice >= len(videos):
            return titulo_video, []

        vid_el = videos[indice]
        titulo_video = (vid_el.text or titulo_video).strip()
        _executar_selenium(
            alertas,
            lambda elemento=vid_el: driver.execute_script(
                "arguments[0].scrollIntoView({block:'center'});", elemento
            ),
            "posicionar o vídeo na tela",
        )
        try:
            clickable = vid_el.find_element(By.XPATH, "ancestor::a[1]")
        except NoSuchElementException:
            try:
                clickable = vid_el.find_element(By.XPATH, "ancestor::button[1]")
            except NoSuchElementException:
                clickable = vid_el

        def clicar_video(elemento=clickable):
            try:
                elemento.click()
            except (
                ElementClickInterceptedException,
                ElementNotInteractableException,
            ):
                driver.execute_script("arguments[0].click();", elemento)

        _executar_selenium(alertas, clicar_video, "selecionar o vídeo")
        try:
            _executar_selenium(
                alertas,
                lambda: WebDriverWait(driver, SELENIUM_SHORT_WAIT).until(
                    EC.presence_of_element_located(
                        (
                            By.XPATH,
                            "//strong[contains(normalize-space(.),"
                            "'Opções de download')]",
                        )
                    )
                ),
                "aguardar opções do vídeo selecionado",
            )
            if not expandir_opcoes_download(driver, alertas, opcoes_antes_da_selecao):
                raise TimeoutException("painel de opções não apareceu")
            return titulo_video, _aguardar_opcoes_video(
                driver,
                alertas,
                opcoes_anteriores=opcoes_antes_da_selecao,
            )
        except TimeoutException:
            if tentativa < VIDEO_SELECTION_RETRIES:
                print(
                    f"      ↪️ Vídeo {indice + 1:02d}: as qualidades ainda "
                    f"não apareceram; tentando novamente "
                    f"({tentativa + 1}/{VIDEO_SELECTION_RETRIES})."
                )
                continue
            return titulo_video, []
    return titulo_video, []


def _carregar_videos_da_aula(driver, alertas=None):
    return _carregar_lista_dinamica(
        driver,
        By.CSS_SELECTOR,
        VIDEO_TITLE_SELECTOR,
        atributos=("data-video-id", "id"),
        alertas=alertas,
        descricao="vídeos da aula",
    )


def _reabrir_aula_para_recuperar_videos(driver, url_aula: str, alertas=None):
    if alertas is None:
        driver.get(url_aula)
    else:
        alertas.resolver_pendente(permitir_desconhecido=True)
        alertas.navegar(url_aula, descricao="reabrir a aula com vídeos pendentes")
    aguardar_conteudo_aula(driver, alertas)
    return _carregar_videos_da_aula(driver, alertas)


def iterar_videos_da_aula_atual(
    driver,
    aula_num: int,
    aula_nome: str,
    alertas=None,
    registrar_falha=None,
):
    """Entrega todos os vídeos e reabre a aula para recuperar links ausentes."""
    videos = _carregar_videos_da_aula(driver, alertas)
    if not videos:
        print(
            "   ℹ️ Não encontrei lista de vídeos nessa aula; ela pode conter "
            "somente material escrito."
        )
        return

    total = len(videos)
    print(f"   🔎 Vídeos encontrados após estabilizar a página: {total}")
    url_aula = driver.current_url
    pendentes = set(range(total))
    titulos = {}
    nomes_usados_nesta_aula = {}

    for passagem in range(1, VIDEO_RECOVERY_PASSES + 1):
        if passagem > 1:
            print(
                f"   🔄 Recuperação de vídeos pendentes "
                f"({passagem}/{VIDEO_RECOVERY_PASSES}): reabrindo a aula."
            )
            videos = _reabrir_aula_para_recuperar_videos(
                driver, url_aula, alertas
            )
            if len(videos) > total:
                novos = set(range(total, len(videos)))
                pendentes.update(novos)
                total = len(videos)
                print(f"   ➕ A página revelou mais vídeos; novo total: {total}")

        for idx in sorted(pendentes):
            if idx < len(videos):
                try:
                    titulos[idx] = (videos[idx].text or f"video_{idx + 1}").strip()
                except StaleElementReferenceException:
                    pass
            try:
                titulo_video, opcoes = _selecionar_video_e_obter_opcoes(
                    driver, idx, alertas
                )
                titulos[idx] = titulo_video
                candidatos = [
                    (resolucao, href)
                    for resolucao, href in opcoes
                    if resolucao
                ]
                links_sem_resolucao = [
                    href for resolucao, href in opcoes if not resolucao
                ]

                if candidatos:
                    resolucao_escolhida, href = max(
                        candidatos, key=lambda item: item[0]
                    )
                    print(
                        f"      🎞️ Vídeo {idx + 1:02d}: melhor qualidade "
                        f"disponível: {resolucao_escolhida}p."
                    )
                elif links_sem_resolucao:
                    href = links_sem_resolucao[0]
                    print(
                        f"      ℹ️ Vídeo {idx + 1:02d}: usando o link disponível; "
                        "a resolução não foi informada."
                    )
                else:
                    continue

                base_title = safe_filename(titulo_video)
                if base_title in nomes_usados_nesta_aula:
                    quantidade = nomes_usados_nesta_aula[base_title] + 1
                    nomes_usados_nesta_aula[base_title] = quantidade
                    base_title = f"{base_title} ({quantidade})"
                else:
                    nomes_usados_nesta_aula[base_title] = 1

                print(
                    f"      ✅ Vídeo {idx + 1:02d}: {base_title} -> "
                    f"{sanitizar_url(href)}"
                )
                pendentes.remove(idx)
                yield {
                    "tipo": "video",
                    "aula_num": aula_num,
                    "aula_nome": safe_filename(aula_nome),
                    "item_num": idx + 1,
                    "titulo": base_title,
                    "extensao": ".mp4",
                    "url": href,
                }
            except (
                NoSuchElementException,
                StaleElementReferenceException,
                ElementNotInteractableException,
                TimeoutException,
            ) as erro:
                print(
                    f"      ↪️ Vídeo {idx + 1:02d} continuará pendente nesta "
                    f"passagem: {erro}"
                )

        if not pendentes:
            break

    for idx in sorted(pendentes):
        titulo = safe_filename(titulos.get(idx, f"video_{idx + 1}"))
        descricao = (
            f"Aula {aula_num:02d}, vídeo {idx + 1:02d} ({titulo}): "
            f"nenhum link apareceu após {VIDEO_RECOVERY_PASSES} passagens"
        )
        print(f"      ⚠️ {descricao}.")
        if registrar_falha is not None:
            registrar_falha(descricao)


def _texto_link(elemento) -> str:
    partes = [
        elemento.text,
        elemento.get_attribute("download"),
        elemento.get_attribute("title"),
        elemento.get_attribute("aria-label"),
        elemento.get_attribute("data-original-title"),
        elemento.get_attribute("data-label"),
        elemento.get_attribute("class"),
        elemento.get_attribute("type"),
    ]
    return " ".join(str(parte).strip() for parte in partes if parte).strip()


def _normalizar_texto(valor: str) -> str:
    return normalizar_texto(valor)


def classificar_material(href: str, descricao: str):
    return classificar_material_puro(href, descricao)


def _url_do_elemento(elemento, url_atual: str) -> str:
    atributos = (
        "href",
        "src",
        "data",
        "data-href",
        "data-url",
        "data-download-url",
        "data-file-url",
        "data-video-url",
        "data-src",
        "data-download",
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
        except (NoSuchElementException, StaleElementReferenceException):
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
    alertas=None,
    registrar_falha=None,
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
    elementos = _carregar_lista_dinamica(
        driver,
        By.CSS_SELECTOR,
        seletor,
        atributos=(
            "href",
            "src",
            "data",
            "data-href",
            "data-url",
            "data-download-url",
            "data-file-url",
        ),
        alertas=alertas,
        descricao="materiais da aula",
    )
    for elemento in elementos:
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
        except (NoSuchElementException, StaleElementReferenceException):
            continue

    print(f"   📚 Materiais encontrados: {len(encontrados)}")
    for descricao in sorted(sem_url):
        print(f"      ⚠️ Material reconhecido, mas sem URL acessível: {descricao}")
        if registrar_falha is not None:
            registrar_falha(
                f"Aula {aula_num:02d}, material sem link acessível: {descricao}"
            )

    rotulos = {
        "pdf": "PDF",
        "slides": "Slides",
        "mapa_mental": "Mapa Mental",
        "material": "Material",
    }
    for indice, (elemento, href, tipo) in enumerate(encontrados, start=1):
        titulo = _titulo_material(elemento, href, indice, tipo)
        print(
            f"      ✅ {rotulos[tipo]} {indice:02d}: {titulo} -> {sanitizar_url(href)}"
        )
        yield {
            "tipo": tipo,
            "aula_num": aula_num,
            "aula_nome": safe_filename(aula_nome),
            "item_num": indice,
            "titulo": titulo,
            "extensao": _extensao_material(tipo, href),
            "url": href,
        }


GerenciadorDownloads = GerenciadorDownloadsNovo


def registrar_e_baixar(item, arquivo_links, gerenciador: GerenciadorDownloads):
    """Persiste o link imediatamente e inicia o download do item."""
    if chave_deduplicacao_url(item["url"]) in gerenciador.urls_processadas:
        gerenciador.baixar(item)
        return

    titulo_log = item["titulo"].replace(";", ",")
    arquivo_links.write(
        f"{item['aula_num']:02d};{item['tipo']};"
        f"{item['item_num']:02d};{titulo_log};{sanitizar_url(item['url'])}\n"
    )
    arquivo_links.flush()
    gerenciador.baixar(item)


def garantir_curso_completo(gerenciador: GerenciadorDownloads):
    """Impede que uma execução com qualquer pendência seja anunciada como sucesso."""
    if not gerenciador.falhas:
        return
    raise ConteudoIncompletoError(
        "A varredura terminou com "
        f"{gerenciador.falhas} pendência(s). Os arquivos concluídos foram "
        "preservados, mas o curso não será marcado como completo. Consulte "
        "os itens 🚩 no diagnóstico e execute novamente para recuperá-los."
    )


def executar_download(args, configuracao, painel: InterfaceWeb):
    email = configuracao["email"]
    password = configuracao["password"]
    curso_id = configuracao["curso_id"]
    pasta_base = configuracao["pasta_base"]
    curso_url = montar_curso_url(curso_id)
    espaco_inicial = verificar_destino(pasta_base)
    painel.atualizar(espaco_disponivel=formatar_tamanho(espaco_inicial))
    painel.atualizar(status="login", fase="Abrindo o Edge para autenticação")
    driver = None
    browser_info = None
    try:
        driver = create_edge_driver(pasta_base)
        browser_info = diagnostico_browser(driver)
        alertas = RecuperadorAlertas(
            driver,
            painel=painel,
            verificar_cancelamento=painel.verificar_cancelamento,
        )
        painel.atualizar(
            fase="Aguardando autenticação no Edge",
            instrucao_login=(
                "Uma janela de login foi aberta. Clique em Entrar e conclua "
                "captcha ou verificação em duas etapas, se solicitado."
            ),
        )
        do_login(
            driver,
            email,
            password,
            painel.verificar_cancelamento,
            alertas,
        )
        password = None
        configuracao["password"] = ""

        painel.atualizar(
            status="baixando",
            fase="Login concluído. Localizando as aulas do curso",
            instrucao_login="",
        )
        painel.verificar_cancelamento()
        aulas = listar_aulas(driver, curso_url, alertas)
        download_dir = criar_pasta_do_curso(pasta_base, driver, curso_id)
        painel.atualizar(pasta_destino=str(download_dir))
        gerenciador = GerenciadorDownloads(
            download_dir, driver, curso_url, painel=painel
        )
        gerenciador.configurar_total_aulas(len(aulas))
        # A estrutura é previsível mesmo para aulas sem um determinado tipo de
        # conteúdo. ``aula_00`` também abriga os materiais gerais do curso.
        gerenciador.preparar_aula(0)
        for aula in aulas:
            gerenciador.preparar_aula(aula["num"])
        out_txt = download_dir / "links_estrategia_conteudo.txt"

        modo_reduzido = configuracao["modo_reduzido"]
        tipos_permitidos = tipos_permitidos_modo_reduzido() if modo_reduzido else None
        if modo_reduzido:
            print(
                "\n📄 Modo PDFs + slides ativado: PDFs, slides e mapas mentais "
                "serão baixados; vídeos e outros materiais serão ignorados."
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
            painel.verificar_cancelamento()
            print("\n➡️ Procurando materiais gerais na página do curso...")
            for item in iterar_materiais_da_aula_atual(
                driver,
                0,
                "Materiais gerais do curso",
                tipos_permitidos,
                alertas,
                gerenciador.registrar_falha_descoberta,
            ):
                registrar_e_baixar(item, arquivo_links, gerenciador)

            for posicao, aula in enumerate(aulas, start=1):
                painel.verificar_cancelamento()
                gerenciador.iniciar_aula(posicao)
                num = aula["num"]
                nome = aula["nome"]
                href = aula["href"]

                painel.atualizar(fase=f"Abrindo a aula {posicao} de {len(aulas)}")
                alertas.resolver_pendente(permitir_desconhecido=True)
                alertas.navegar(href, descricao=f"abrir a aula {posicao}")
                aguardar_conteudo_aula(driver, alertas)
                gerenciador.sessao.headers["Referer"] = href

                print(
                    f"\n➡️ Aula {posicao}/{len(aulas)}: {nome} "
                    f"(número identificado: {num:02d})"
                )

                fontes = [
                    iterar_materiais_da_aula_atual(
                        driver,
                        num,
                        nome,
                        tipos_permitidos,
                        alertas,
                        gerenciador.registrar_falha_descoberta,
                    )
                ]
                if not modo_reduzido:
                    fontes.append(
                        iterar_videos_da_aula_atual(
                            driver,
                            num,
                            nome,
                            alertas,
                            gerenciador.registrar_falha_descoberta,
                        )
                    )
                for fonte in fontes:
                    for item in fonte:
                        painel.verificar_cancelamento()
                        registrar_e_baixar(item, arquivo_links, gerenciador)
                gerenciador.concluir_aula()

        print(f"\n✅ Links registrados continuamente em: {out_txt}")
        gerenciador.resumo()
        garantir_curso_completo(gerenciador)
        print("\n✅ Processo completo.")
        return {
            "resumo": gerenciador.resumo_dados(),
            "browser": browser_info,
        }
    finally:
        password = None
        configuracao["password"] = ""
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass


def main():
    args = ler_argumentos()
    painel = InterfaceWeb(
        modo_reduzido=args.pdfs_e_slides,
        pasta_inicial=DEFAULT_DOWNLOAD_DIR,
        email_inicial=(os.getenv("ESTRATEGIA_EMAIL") or "").strip(),
        senha_inicial=os.getenv("ESTRATEGIA_PASSWORD") or "",
        curso_inicial=os.getenv("ESTRATEGIA_CURSO_ID") or "",
    )
    url = painel.iniciar()
    print(f"🌐 Interface aberta no Edge: {url}")
    try:
        configuracao = painel.aguardar_configuracao()
    except BaseException:
        painel.parar()
        raise

    stdout_original = sys.stdout
    stderr_original = sys.stderr
    sys.stdout = SaidaPainel(stdout_original, painel)
    sys.stderr = SaidaPainel(stderr_original, painel)
    codigo_saida = 0
    try:
        resultado = executar_download(args, configuracao, painel)
        painel.definir_resumo(resultado["resumo"])
        painel.definir_diagnostico(
            criar_diagnostico(
                fase="Concluído",
                logs=painel.estado()["logs"],
                browser=resultado["browser"],
            )
        )
        painel.finalizar("concluido", "Downloads e varredura concluídos")
    except DownloadCancelado as e:
        print(f"\nℹ️ {e}")
        painel.definir_diagnostico(
            criar_diagnostico(
                fase="Cancelado",
                logs=painel.estado()["logs"],
            )
        )
        painel.finalizar("cancelado", "Download cancelado", str(e))
    except Exception as e:
        codigo_saida = 1
        mensagem = mensagem_usuario_para_erro(e)
        print(f"\n❌ Não foi possível concluir: {mensagem}")
        if os.getenv("ESTRATEGIA_DEBUG") == "1":
            traceback.print_exc()
        painel.definir_diagnostico(
            criar_diagnostico(
                fase=painel.estado()["fase"],
                logs=painel.estado()["logs"],
                erro=e,
            )
        )
        painel.finalizar("erro", "Não foi possível concluir", mensagem)
    finally:
        sys.stdout = stdout_original
        sys.stderr = stderr_original

    print("ℹ️ O resultado permanece aberto no Edge. Use 'Encerrar interface' ao sair.")
    try:
        painel.aguardar_encerramento()
    finally:
        painel.parar()
    return codigo_saida


def executar_com_tratamento_de_erros() -> int:
    try:
        return int(main() or 0)
    except SystemExit as e:
        # argparse usa SystemExit(0) para --help; fechar o painel antes de iniciar
        # também é um encerramento normal.
        if isinstance(e.code, str):
            print(f"\nℹ️ {e.code}")
            return 0
        return int(e.code or 0)
    except KeyboardInterrupt:
        print("\nℹ️ Processo interrompido pelo usuário.")
        return 130
    except Exception as e:
        mensagem = mensagem_usuario_para_erro(e)
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
