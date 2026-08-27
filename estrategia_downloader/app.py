import argparse
import os
import re
import sys
import time
import traceback
from datetime import date, datetime
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
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from interface_web import DownloadCancelado, InterfaceWeb, SaidaPainel

from .alerts import RecuperadorAlertas
from .browser import create_edge_driver, diagnostico_browser
from .collection import (
    CollectionError,
    ensure_course_folder,
    invalidate_legacy_completions,
    open_collection,
    save_collection,
    update_course_status,
)
from .config import (
    CONTENT_STABILITY_SECONDS,
    DISCOVERY_MAX_ROUNDS,
    DISCOVERY_SCROLL_PAUSE,
    DISCOVERY_STABLE_ROUNDS,
    INVENTORY_EMPTY_STABLE_OBSERVATIONS,
    INVENTORY_MAX_PASSES,
    INVENTORY_STABLE_OBSERVATIONS,
    LOGIN_TIMEOUT,
    LOGIN_URL,
    SELENIUM_SHORT_WAIT,
    SELENIUM_WAIT_TIMEOUT,
    VIDEO_OPTIONS_TIMEOUT,
    VIDEO_RECOVERY_PASSES,
    VIDEO_SELECTION_RETRIES,
    pasta_download_padrao,
)
from .course_metadata import (
    create_course_api_session,
    get_course_name,
    list_accessible_courses,
)
from .course_inventory import (
    CourseInventoryError,
    LessonSnapshot,
    extract_lesson_snapshot,
    get_course_snapshot,
    get_lesson_snapshot,
)
from .diagnostics import criar_diagnostico
from .discovery import classificar_material as classificar_material_puro
from .downloads import (
    GerenciadorDownloads as GerenciadorDownloadsNovo,
    criar_sessao_download,
)
from .errors import (
    ColecaoIncompletaError,
    ConteudoIncompletoError,
    ProcessamentoCursoError,
    mensagem_usuario_para_erro,
)
from .integrity import (
    AUDIT_VERSION,
    fingerprint,
    load_inventory_lessons,
    resource_key,
    safe_resource_record,
    safe_video_record,
    save_inventory,
)
from .resume import localizar_pasta_retomavel, salvar_estado_execucao
from .utils import (
    EspacoInsuficienteError,
    chave_deduplicacao_url,
    formatar_duracao,
    formatar_tamanho,
    normalizar_texto,
    safe_filename,
    sanitizar_texto,
    sanitizar_url,
    slug_nome_curso,
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
DASHBOARD_COURSES_URL = "https://www.estrategiaconcursos.com.br/app/dashboard/cursos"
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


def atribuir_numeros_aulas(aulas: list[dict]) -> list[dict]:
    """Substitui números ausentes/duplicados por posições sequenciais estáveis."""

    ordenadas = sorted(
        enumerate(aulas),
        key=lambda item: (
            item[1]["num"] == 9999,
            item[1]["num"],
            item[0],
        ),
    )
    usados = set()
    proximo = 0
    resultado = []
    for _ordem, original in ordenadas:
        aula = dict(original)
        numero = aula["num"]
        if numero == 9999 or numero in usados:
            while proximo in usados:
                proximo += 1
            numero = proximo
        aula["num"] = numero
        usados.add(numero)
        proximo = max(proximo, numero + 1)
        resultado.append(aula)
    return resultado


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


def _preencher_campo_login(driver, elemento, valor: str, descricao: str) -> None:
    """Preenche e confirma um campo controlado por React sem revelar o valor."""

    elemento.clear()
    elemento.send_keys(valor)
    if (elemento.get_attribute("value") or "") == valor:
        return

    driver.execute_script(
        """
        const input = arguments[0];
        const value = arguments[1];
        const setter = Object.getOwnPropertyDescriptor(
          HTMLInputElement.prototype, 'value'
        ).set;
        setter.call(input, value);
        input.dispatchEvent(new Event('input', {bubbles: true}));
        input.dispatchEvent(new Event('change', {bubbles: true}));
        input.dispatchEvent(new Event('blur', {bubbles: true}));
        """,
        elemento,
        valor,
    )
    if (elemento.get_attribute("value") or "") != valor:
        raise RuntimeError(
            f"o campo de {descricao} não manteve o valor informado; "
            "o formulário de login pode ter mudado"
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
    *,
    submeter_automaticamente: bool = False,
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

    _preencher_campo_login(driver, email_el, email, "e-mail")

    pwd_el = wait.until(
        EC.visibility_of_element_located(
            (By.CSS_SELECTOR, "input[type='password'], input[name='password']")
        )
    )
    _preencher_campo_login(driver, pwd_el, password, "senha")

    if submeter_automaticamente:
        # Opção destinada a execuções locais explicitamente autorizadas. O
        # fluxo padrão continua aguardando o clique do usuário e qualquer
        # captcha/2FA apresentado pelo site continua intacto.
        botoes = driver.find_elements(
            By.CSS_SELECTOR,
            "button[type='submit'], input[type='submit']",
        )
        botao = next(
            (
                item
                for item in botoes
                if item.is_displayed() and item.is_enabled()
            ),
            None,
        )
        if botao is not None:
            botao.click()
        else:
            pwd_el.send_keys(Keys.ENTER)

    if submeter_automaticamente:
        print("\n➡️ Login enviado; conclua 2FA/captcha no Edge, se aparecer.")
    else:
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
        pagina_esperada = urlparse(curso_url).path.rstrip("/")
        pagina_atual = urlparse(driver.current_url).path.rstrip("/")
        if pagina_atual != pagina_esperada:
            raise RuntimeError(
                "A página do curso não permaneceu no endereço solicitado. "
                f"URL atual: {driver.current_url!r}. Confira se a conta ainda "
                "possui acesso."
            )
        print(
            "📚 Nenhuma aula numerada foi encontrada; a página geral ainda será "
            "auditada em busca de materiais."
        )
        return []

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

    aulas = atribuir_numeros_aulas(aulas)

    print(f"📚 Total de aulas identificadas: {len(aulas)}")
    for a in aulas:
        print(f"   • Aula {a['num']:02d}: {a['nome']}")
    return aulas


def _assinatura_aulas(aulas: list[dict]) -> tuple:
    return tuple(
        (
            urlparse(aula["href"]).path.rstrip("/"),
            normalizar_texto(aula["nome"]),
        )
        for aula in aulas
    )


def listar_aulas_auditadas(driver, curso_url: str, alertas=None):
    """Aceita a lista de aulas somente após leituras independentes idênticas."""

    assinatura_anterior = None
    observacoes = 0
    aulas = []
    for passagem in range(1, INVENTORY_MAX_PASSES + 1):
        aulas = listar_aulas(driver, curso_url, alertas)
        assinatura = _assinatura_aulas(aulas)
        observacoes = observacoes + 1 if assinatura == assinatura_anterior else 1
        necessarias = (
            INVENTORY_STABLE_OBSERVATIONS
            if aulas
            else INVENTORY_EMPTY_STABLE_OBSERVATIONS
        )
        print(
            f"   🔁 Inventário de aulas {passagem}/{INVENTORY_MAX_PASSES}: "
            f"{len(aulas)} aula(s), estabilidade {observacoes}/{necessarias}."
        )
        if observacoes >= necessarias:
            return aulas
        assinatura_anterior = assinatura

    raise ConteudoIncompletoError(
        "a lista de aulas não permaneceu idêntica em leituras independentes; "
        "o curso não pode ser marcado como completo"
    )


def detectar_liberacoes_futuras(driver, *, hoje: date | None = None) -> list[date]:
    """Extrai somente avisos explícitos de liberação posteriores ao dia atual."""

    texto = driver.execute_script(
        "return document.body ? (document.body.innerText || '') : '';"
    )
    if not isinstance(texto, str):
        return []
    referencia = hoje or date.today()
    datas = set()
    for valor in re.findall(
        r"\bDispon(?:í|i)vel\s+em\s+([0-3]\d/[01]\d/\d{4})\b",
        texto,
        flags=re.IGNORECASE,
    ):
        try:
            liberacao = datetime.strptime(valor, "%d/%m/%Y").date()
        except ValueError:
            continue
        if liberacao > referencia:
            datas.add(liberacao)
    return sorted(datas)


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
            and (agora - ultima_mudanca >= CONTENT_STABILITY_SECONDS)
        )

    return _executar_selenium(
        alertas,
        lambda: WebDriverWait(driver, timeout, poll_frequency=0.2).until(
            conteudo_estavel
        ),
        "aguardar o conteúdo da aula",
    )


def montar_nome_pasta_curso(
    curso_id: str,
    nome_curso: str,
    unix_timestamp=None,
) -> str:
    """Monta um nome descritivo, portável e rastreável para a execução."""
    if unix_timestamp is None:
        unix_timestamp = int(time.time())
    slug = slug_nome_curso(nome_curso)
    return f"{slug}-id-{curso_id}-{int(unix_timestamp)}"


def configurar_destino_edge(driver, pasta: Path) -> None:
    """Direciona downloads ocasionais iniciados pelo Edge para a pasta atual."""

    try:
        driver.execute_cdp_cmd(
            "Page.setDownloadBehavior",
            {"behavior": "allow", "downloadPath": str(pasta)},
        )
    except WebDriverException:
        pass


def criar_pasta_do_curso(
    pasta_base: Path,
    driver,
    curso_id: str,
    nome_curso: str,
    unix_timestamp=None,
) -> Path:
    pasta_curso = (
        localizar_pasta_retomavel(pasta_base, curso_id)
        if unix_timestamp is None
        else None
    )
    retomando = pasta_curso is not None
    if pasta_curso is None:
        nome_pasta = montar_nome_pasta_curso(
            curso_id,
            nome_curso,
            unix_timestamp,
        )
        pasta_curso = pasta_base / nome_pasta
        pasta_curso.mkdir(parents=True, exist_ok=True)
    else:
        nome_pasta = pasta_curso.name

    if not salvar_estado_execucao(pasta_curso, curso_id, "em_andamento"):
        print("⚠️ Não foi possível gravar o marcador de retomada nesta pasta.")

    # Mantém também eventuais downloads iniciados pelo próprio Edge dentro da
    # mesma subpasta. Os downloads principais continuam sendo feitos via requests.
    configurar_destino_edge(driver, pasta_curso)

    print(f"📚 ID do curso: {curso_id}")
    print(f"📖 Curso: {nome_curso}")
    if retomando:
        print(f"🔄 Retomando a execução incompleta: {nome_pasta}")
    else:
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
    ignorar_posicoes=None,
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
    posicoes_confirmadas = {
        int(posicao) - 1
        for posicao in (ignorar_posicoes or ())
        if 1 <= int(posicao) <= total
    }
    pendentes = set(range(total)) - posicoes_confirmadas
    titulos = {}

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
            if passagem >= VIDEO_RECOVERY_PASSES:
                break
            auditoria = _carregar_videos_da_aula(driver, alertas)
            if len(auditoria) <= total:
                break
            novos = set(range(total, len(auditoria)))
            pendentes.update(novos)
            total = len(auditoria)
            videos = auditoria
            print(
                "   ➕ A auditoria final revelou mais vídeos; "
                f"novo total: {total}"
            )

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


def _texto_humano_elemento(elemento) -> str:
    """Texto anunciado ao usuário, sem classes CSS ou tipo do elemento."""

    partes = [
        elemento.text,
        elemento.get_attribute("download"),
        elemento.get_attribute("title"),
        elemento.get_attribute("aria-label"),
        elemento.get_attribute("data-original-title"),
        elemento.get_attribute("data-label"),
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
    # Classes CSS e estados transitórios (por exemplo ``LessonButton`` e
    # ``Baixado``) não pertencem ao título canônico e mudam após um clique.
    candidatos = [
        elemento.text,
        elemento.get_attribute("download"),
        elemento.get_attribute("title"),
        elemento.get_attribute("aria-label"),
        elemento.get_attribute("data-original-title"),
        elemento.get_attribute("data-label"),
    ]
    titulo = next(
        (
            " ".join(str(valor).split())
            for valor in candidatos
            if valor and str(valor).strip()
        ),
        "",
    )
    titulo = re.sub(
        r"(?:\s+(?:baixado|lessonbutton))+\s*$",
        "",
        titulo,
        flags=re.IGNORECASE,
    )
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


def coletar_materiais_da_aula_atual(
    driver,
    aula_num: int,
    aula_nome: str,
    tipos_permitidos=None,
    alertas=None,
):
    """Retorna materiais e anúncios ainda sem URL após estabilizar a página."""

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
                descricao_humana = _texto_humano_elemento(elemento)
                if descricao_humana:
                    sem_url.add(" ".join(descricao_humana.split())[:160])
                continue
            if href in vistos:
                continue

            vistos.add(href)
            encontrados.append((elemento, href, tipo))
        except (NoSuchElementException, StaleElementReferenceException):
            continue

    rotulos = {
        "pdf": "PDF",
        "slides": "Slides",
        "mapa_mental": "Mapa Mental",
        "material": "Material",
    }
    itens = []
    for indice, (elemento, href, tipo) in enumerate(encontrados, start=1):
        titulo = _titulo_material(elemento, href, indice, tipo)
        itens.append({
            "tipo": tipo,
            "aula_num": aula_num,
            "aula_nome": safe_filename(aula_nome),
            "item_num": indice,
            "titulo": titulo,
            "extensao": _extensao_material(tipo, href),
            "url": href,
            "rotulo": rotulos[tipo],
        })
    return itens, sem_url


def iterar_materiais_da_aula_atual(
    driver,
    aula_num: int,
    aula_nome: str,
    tipos_permitidos=None,
    alertas=None,
    registrar_falha=None,
):
    """Entrega PDFs, slides, mapas mentais e outros materiais sem duplicatas."""

    itens, sem_url = coletar_materiais_da_aula_atual(
        driver,
        aula_num,
        aula_nome,
        tipos_permitidos,
        alertas,
    )
    print(f"   📚 Materiais encontrados: {len(itens)}")
    for descricao in sorted(sem_url):
        print(f"      ⚠️ Material reconhecido, mas sem URL acessível: {descricao}")
        if registrar_falha is not None:
            registrar_falha(
                f"Aula {aula_num:02d}, material sem link acessível: {descricao}"
            )
    for item in itens:
        print(
            f"      ✅ {item.pop('rotulo')} {item['item_num']:02d}: "
            f"{item['titulo']} -> {sanitizar_url(item['url'])}"
        )
        yield item


GerenciadorDownloads = GerenciadorDownloadsNovo


def registrar_e_baixar(item, arquivo_links, gerenciador: GerenciadorDownloads):
    """Registra metadados sem a URL temporária e inicia o download do item."""
    if chave_deduplicacao_url(item["url"]) in gerenciador.urls_processadas:
        return gerenciador.baixar(item)

    titulo_log = item["titulo"].replace(";", ",")
    arquivo_links.write(
        f"{item['aula_num']:02d};{item['tipo']};"
        f"{item['item_num']:02d};{titulo_log};[URL omitida por segurança]\n"
    )
    arquivo_links.flush()
    return gerenciador.baixar(item)


def auditar_e_baixar_snapshot_api(
    snapshot: LessonSnapshot,
    arquivo_links,
    gerenciador: GerenciadorDownloads,
    *,
    tipos_permitidos=None,
    incluir_videos: bool,
) -> dict:
    """Baixa exatamente os recursos enumerados por uma resposta de aula."""

    materiais_manifesto = {}
    videos_manifesto = {}
    arquivos_manifesto = {}

    for descricao in snapshot.unresolved:
        gerenciador.registrar_falha_descoberta(
            f"{snapshot.lesson.name}: {descricao}"
        )
    for caminho in snapshot.unexpected_url_fields:
        gerenciador.registrar_falha_descoberta(
            f"{snapshot.lesson.name}: campo de URL não classificado em {caminho}"
        )

    materiais = [
        item
        for item in snapshot.materials
        if tipos_permitidos is None or item["tipo"] in tipos_permitidos
    ]
    videos = list(snapshot.videos) if incluir_videos else []
    identidades_video = snapshot.video_identities if incluir_videos else ()

    if incluir_videos and len(videos) != len(identidades_video):
        gerenciador.registrar_falha_descoberta(
            f"{snapshot.lesson.name}: a API anunciou "
            f"{len(identidades_video)} vídeo(s), mas forneceu "
            f"{len(videos)} arquivo(s) utilizável(is)"
        )

    for item in materiais:
        chave = resource_key(item["url"])
        registro = safe_resource_record(item)
        materiais_manifesto[chave] = registro
        arquivos_manifesto[chave] = registro
        registrar_e_baixar(dict(item), arquivo_links, gerenciador)

    for identidade, posicao, titulo in identidades_video:
        videos_manifesto[identidade] = safe_video_record(
            identidade,
            posicao,
            titulo,
        )
    for item in videos:
        chave = resource_key(item["url"])
        arquivos_manifesto[chave] = safe_resource_record(item)
        registrar_e_baixar(dict(item), arquivo_links, gerenciador)

    recursos_pendentes = set(arquivos_manifesto) - gerenciador.urls_concluidas
    if recursos_pendentes:
        gerenciador.registrar_falha_descoberta(
            f"{snapshot.lesson.name}: {len(recursos_pendentes)} recurso(s) "
            "enumerado(s) pela API não tiveram arquivo local validado"
        )

    return {
        "nome": safe_filename(snapshot.lesson.name),
        "passagens": 1,
        "estavel": not snapshot.unresolved and not snapshot.unexpected_url_fields,
        "modo": "api",
        "materiais": sorted(
            materiais_manifesto.values(), key=lambda item: item["identidade"]
        ),
        "videos": sorted(
            videos_manifesto.values(), key=lambda item: item["identidade"]
        ),
        "arquivos": sorted(
            arquivos_manifesto.values(), key=lambda item: item["identidade"]
        ),
    }


def _inventario_videos_dom(driver, alertas=None):
    """Lê a identidade estrutural dos vídeos sem abrir links assinados."""

    registros = []
    for indice, elemento in enumerate(
        _carregar_videos_da_aula(driver, alertas), start=1
    ):
        try:
            titulo = (elemento.text or f"Vídeo {indice:02d}").strip()
            identificador = (
                elemento.get_attribute("data-video-id")
                or elemento.get_attribute("data-id")
                or ""
            )
        except StaleElementReferenceException:
            titulo = f"Vídeo {indice:02d}"
            identificador = ""
        identidade = (
            f"posicao={indice}|id={identificador}"
            if identificador
            else f"posicao={indice}|titulo={normalizar_texto(titulo)}"
        )
        registros.append(
            {
                "chave": identidade,
                "numero": indice,
                "titulo": safe_filename(titulo),
                "identificador": identificador,
            }
        )
    return registros


def _atualizar_estabilidade(
    anterior: frozenset | None,
    atual: frozenset,
    uniao: set,
    observacoes: int,
) -> tuple[frozenset, int]:
    if set(atual) != uniao:
        return atual, 0
    if atual == anterior:
        return atual, observacoes + 1
    return atual, 1


def _navegar_para_auditoria(driver, alertas, href: str, descricao: str) -> None:
    if alertas is None:
        driver.get(href)
    else:
        alertas.resolver_pendente(permitir_desconhecido=True)
        alertas.navegar(href, descricao=descricao)
    aguardar_conteudo_aula(driver, alertas)


def auditar_e_baixar_aula(
    driver,
    alertas,
    arquivo_links,
    gerenciador: GerenciadorDownloads,
    *,
    href: str,
    aula_num: int,
    aula_nome: str,
    tipos_permitidos=None,
    incluir_videos: bool,
    permitir_vazio: bool,
    exigir_convergencia: bool = True,
) -> dict:
    """Reconcilia inventários independentes e baixa a união observada."""

    todos_materiais = set()
    todos_videos = set()
    materiais_manifesto = {}
    videos_manifesto = {}
    arquivos_manifesto = {}
    videos_confirmados = set()
    assinatura_material_anterior = None
    assinatura_video_anterior = None
    estabilidade_material = 0
    estabilidade_video = 0
    falhas_video = []
    sem_url_final = set()
    convergiu = False
    passagem = 0
    for passagem in range(1, INVENTORY_MAX_PASSES + 1):
        if passagem > 1:
            _navegar_para_auditoria(
                driver,
                alertas,
                href,
                f"reauditar {aula_nome} (passagem {passagem})",
            )
            gerenciador.sessao.headers["Referer"] = href

        itens_material, sem_url = coletar_materiais_da_aula_atual(
            driver,
            aula_num,
            aula_nome,
            tipos_permitidos,
            alertas,
        )
        sem_url_final = set(sem_url)
        chaves_materiais = {resource_key(item["url"]) for item in itens_material}
        todos_materiais.update(chaves_materiais)
        assinatura_material = frozenset(
            chaves_materiais
            | {f"sem-url:{fingerprint(descricao)}" for descricao in sem_url}
        )

        print(
            f"   📚 Materiais encontrados na passagem {passagem}: "
            f"{len(itens_material)}"
        )
        for descricao in sorted(sem_url):
            print(
                "      ↪️ Material anunciado ainda sem URL; será rechecado: "
                f"{descricao}"
            )
        for item_original in itens_material:
            item = dict(item_original)
            rotulo = item.pop("rotulo")
            chave = resource_key(item["url"])
            materiais_manifesto[chave] = safe_resource_record(item)
            arquivos_manifesto[chave] = safe_resource_record(item)
            print(
                f"      ✅ {rotulo} {item['item_num']:02d}: {item['titulo']} -> "
                f"{sanitizar_url(item['url'])}"
            )
            registrar_e_baixar(item, arquivo_links, gerenciador)

        registros_video = (
            _inventario_videos_dom(driver, alertas) if incluir_videos else []
        )
        chaves_video = {registro["chave"] for registro in registros_video}
        todos_videos.update(chaves_video)
        for registro in registros_video:
            videos_manifesto[registro["chave"]] = safe_video_record(
                registro["chave"], registro["numero"], registro["titulo"]
            )

        precisa_resolver_videos = bool(
            incluir_videos
            and (
                passagem == 1
                or not chaves_video.issubset(videos_confirmados)
                or falhas_video
            )
        )
        falhas_video = []
        if precisa_resolver_videos:
            registros_por_posicao = {
                registro["numero"]: registro for registro in registros_video
            }
            posicoes_confirmadas = {
                registro["numero"]
                for registro in registros_video
                if registro["chave"] in videos_confirmados
            }
            for item in iterar_videos_da_aula_atual(
                driver,
                aula_num,
                aula_nome,
                alertas,
                falhas_video.append,
                ignorar_posicoes=posicoes_confirmadas,
            ):
                chave = resource_key(item["url"])
                arquivos_manifesto[chave] = safe_resource_record(item)
                concluido = registrar_e_baixar(
                    item, arquivo_links, gerenciador
                )
                registro = registros_por_posicao.get(item["item_num"])
                if concluido and registro is not None:
                    videos_confirmados.add(registro["chave"])

        assinatura_video = frozenset(chaves_video)
        assinatura_material_anterior, estabilidade_material = (
            _atualizar_estabilidade(
                assinatura_material_anterior,
                assinatura_material,
                todos_materiais,
                estabilidade_material,
            )
        )
        assinatura_video_anterior, estabilidade_video = _atualizar_estabilidade(
            assinatura_video_anterior,
            assinatura_video,
            todos_videos,
            estabilidade_video,
        )
        alvo_material = (
            INVENTORY_STABLE_OBSERVATIONS
            if todos_materiais
            else INVENTORY_EMPTY_STABLE_OBSERVATIONS
        )
        alvo_video = (
            INVENTORY_STABLE_OBSERVATIONS
            if todos_videos
            else INVENTORY_EMPTY_STABLE_OBSERVATIONS
        )
        print(
            f"   🔁 Reconciliação {passagem}/{INVENTORY_MAX_PASSES}: "
            f"materiais {estabilidade_material}/{alvo_material}; "
            f"vídeos {estabilidade_video}/{alvo_video}."
        )
        if (
            estabilidade_material >= alvo_material
            and estabilidade_video >= alvo_video
            and not falhas_video
            and todos_videos.issubset(videos_confirmados)
        ):
            convergiu = True
            break

    if exigir_convergencia and not convergiu:
        gerenciador.registrar_falha_descoberta(
            f"{aula_nome}: o inventário remoto não convergiu após "
            f"{INVENTORY_MAX_PASSES} passagens"
        )
    if exigir_convergencia:
        for descricao in sorted(sem_url_final):
            gerenciador.registrar_falha_descoberta(
                f"Aula {aula_num:02d}, material sem link acessível: {descricao}"
            )
        for descricao in falhas_video:
            gerenciador.registrar_falha_descoberta(descricao)

    videos_sem_arquivo = todos_videos - videos_confirmados
    if exigir_convergencia and videos_sem_arquivo:
        gerenciador.registrar_falha_descoberta(
            f"{aula_nome}: {len(videos_sem_arquivo)} vídeo(s) anunciado(s) "
            "não tiveram arquivo confirmado"
        )
    recursos_esperados = set(arquivos_manifesto)
    recursos_pendentes = recursos_esperados - gerenciador.urls_concluidas
    if recursos_pendentes:
        gerenciador.registrar_falha_descoberta(
            f"{aula_nome}: {len(recursos_pendentes)} recurso(s) remoto(s) "
            "não tiveram arquivo local validado"
        )
    if (
        exigir_convergencia
        and not permitir_vazio
        and not todos_materiais
        and not todos_videos
    ):
        gerenciador.registrar_falha_descoberta(
            f"{aula_nome}: aula numerada vazia; não é seguro afirmar que "
            "nenhum conteúdo está faltando"
        )

    return {
        "nome": safe_filename(aula_nome),
        "passagens": passagem,
        "estavel": convergiu,
        "modo": "estrito" if exigir_convergencia else "oportunistico",
        "materiais": sorted(
            materiais_manifesto.values(), key=lambda item: item["identidade"]
        ),
        "videos": sorted(
            videos_manifesto.values(), key=lambda item: item["identidade"]
        ),
        "arquivos": sorted(
            arquivos_manifesto.values(), key=lambda item: item["identidade"]
        ),
    }


def garantir_curso_completo(
    gerenciador: GerenciadorDownloads,
    *,
    catalogo_remoto_vazio: bool = False,
):
    """Impede que uma execução com qualquer pendência seja anunciada como sucesso."""
    ocorrencias_pendentes = gerenciador.ocorrencias_pendentes()
    if ocorrencias_pendentes:
        raise ConteudoIncompletoError(
            f"{len(ocorrencias_pendentes)} ocorrência(s) de conteúdo não "
            "tiveram um arquivo confirmado na pasta da respectiva aula"
        )
    if gerenciador.encontrados <= 0 and not catalogo_remoto_vazio:
        raise ConteudoIncompletoError(
            "nenhum arquivo foi encontrado; sem uma fonte remota que confirme "
            "um catálogo vazio, o curso não será marcado como completo"
        )
    if not gerenciador.falhas:
        return
    raise ConteudoIncompletoError(
        "A varredura terminou com "
        f"{gerenciador.falhas} pendência(s). Os arquivos concluídos foram "
        "preservados, mas o curso não será marcado como completo. Consulte "
        "os itens 🚩 no diagnóstico e execute novamente para recuperá-los."
    )


def obter_nome_curso_autenticado(driver, curso_url: str, curso_id: str) -> str:
    """Consulta o título pela API usando apenas a autenticação copiada do Edge."""
    sessao_web = criar_sessao_download(driver, curso_url)
    sessao_api = None
    try:
        sessao_api = create_course_api_session(sessao_web)
        return get_course_name(sessao_api, curso_id)
    finally:
        if sessao_api is not None:
            sessao_api.close()
        sessao_web.close()


def obter_catalogo_cursos_autenticado(driver):
    """Obtém o catálogo inteiro pela API e fecha imediatamente as sessões."""

    sessao_web = criar_sessao_download(driver, DASHBOARD_COURSES_URL)
    sessao_api = None
    try:
        sessao_api = create_course_api_session(sessao_web)
        return list_accessible_courses(sessao_api)
    finally:
        if sessao_api is not None:
            sessao_api.close()
        sessao_web.close()


def executar_conteudo_curso(
    driver,
    alertas,
    painel: InterfaceWeb,
    curso_id: str,
    nome_curso: str,
    download_dir: Path,
    *,
    modo_reduzido: bool,
    auditar_existentes: bool = False,
) -> dict:
    """Baixa o inventário finito declarado pelas APIs de curso e aula."""

    curso_url = montar_curso_url(curso_id)
    configurar_destino_edge(driver, download_dir)
    painel.atualizar(
        pasta_destino=str(download_dir),
        curso_nome=nome_curso,
        fase="Localizando as aulas e materiais do curso",
    )
    if not salvar_estado_execucao(download_dir, curso_id, "em_andamento"):
        print("⚠️ Não foi possível gravar o marcador inicial deste curso.")

    gerenciador = None
    sessao_api = None
    inventario_aulas = load_inventory_lessons(download_dir, curso_id)
    concluido = False
    try:
        save_inventory(
            download_dir,
            curso_id,
            "em_andamento",
            inventario_aulas,
        )
        gerenciador = GerenciadorDownloads(
            download_dir,
            driver,
            curso_url,
            painel=painel,
            auditar_existentes=auditar_existentes,
        )
        sessao_api = create_course_api_session(gerenciador.sessao)
        curso = get_course_snapshot(sessao_api, curso_id)
        if curso.title != nome_curso:
            raise CourseInventoryError(
                "o título canônico do curso diverge do título usado para "
                "identificar sua pasta"
            )
        aulas = list(curso.lessons)
        print(
            f"📚 A API confirmou {curso.total_lessons} aula(s) única(s) "
            f"para o curso {curso_id}."
        )
        for caminho in curso.unexpected_url_fields:
            gerenciador.registrar_falha_descoberta(
                f"curso {curso_id}: campo de URL não classificado em {caminho}"
            )
        for descricao in curso.unresolved:
            gerenciador.registrar_falha_descoberta(
                f"curso {curso_id}: {descricao}"
            )
        hoje = date.today()
        aulas_disponiveis = []
        aulas_futuras = []
        aulas_bloqueadas = []
        for aula in aulas:
            data_futura = (
                aula.release_date is not None and aula.release_date > hoje
            )
            if aula.is_available is False:
                if data_futura:
                    aulas_futuras.append(aula)
                else:
                    aulas_bloqueadas.append(aula)
                    gerenciador.registrar_falha_descoberta(
                        f"{aula.name}: API marcou a aula como indisponível sem "
                        "uma data futura válida"
                    )
            elif data_futura:
                aulas_futuras.append(aula)
            else:
                aulas_disponiveis.append(aula)
        chaves_atuais = {
            f"aula_{aula.number:02d}_posicao_{aula.position:02d}"
            for aula in aulas
        }
        inventario_aulas = {
            chave: valor
            for chave, valor in inventario_aulas.items()
            if chave in chaves_atuais
        }
        save_inventory(
            download_dir,
            curso_id,
            "em_andamento",
            inventario_aulas,
        )
        gerenciador.configurar_total_aulas(len(aulas))
        for aula in aulas:
            gerenciador.preparar_aula(aula.number)
        out_txt = download_dir / "links_estrategia_conteudo.txt"

        tipos_permitidos = (
            tipos_permitidos_modo_reduzido() if modo_reduzido else None
        )
        if modo_reduzido:
            print(
                "\n📄 Modo PDFs + slides ativado: PDFs, slides e mapas mentais "
                "serão baixados; vídeos e outros materiais serão ignorados."
            )
        else:
            print(
                "\n⬇️ Modo completo: vídeos, PDFs, slides, mapas mentais e "
                "demais materiais serão enumerados pela API e baixados."
            )
        if auditar_existentes:
            print("   🔎 Arquivos já presentes também terão o tamanho auditado.")
        print("   O download começará assim que cada arquivo for localizado.")

        with open(out_txt, "w", encoding="utf-8") as arquivo_links:
            arquivo_links.write("aula;tipo;numero;titulo;origem\n")
            arquivo_links.flush()

            ids_futuros = {aula.lesson_id for aula in aulas_futuras}
            ids_bloqueados = {aula.lesson_id for aula in aulas_bloqueadas}
            for aula in aulas:
                chave_aula = (
                    f"aula_{aula.number:02d}_posicao_{aula.position:02d}"
                )
                if aula.lesson_id in ids_bloqueados:
                    inventario_aulas[chave_aula] = {
                        "nome": safe_filename(aula.name),
                        "passagens": 0,
                        "estavel": False,
                        "modo": "indisponivel_inconsistente",
                        "materiais": [],
                        "videos": [],
                        "arquivos": [],
                    }
                    continue
                aula_futura = aula.lesson_id in ids_futuros
                if (
                    aula_futura
                    and not aula.summary_resources
                    and not aula.summary_videos
                ):
                    inventario_aulas[chave_aula] = {
                        "nome": safe_filename(aula.name),
                        "passagens": 0,
                        "estavel": True,
                        "modo": "aguardando_liberacao",
                        "liberacao": aula.release_date.isoformat(),
                        "materiais": [],
                        "videos": [],
                        "arquivos": [],
                    }
                    continue

                painel.verificar_cancelamento()
                gerenciador.iniciar_aula(aula.position)

                painel.atualizar(
                    fase=(
                        f"Inventariando a aula {aula.position} de {len(aulas)} "
                        "pela API"
                    )
                )
                if aula_futura:
                    snapshot = extract_lesson_snapshot(
                        {"data": {"id": int(aula.lesson_id), "videos": []}},
                        aula,
                    )
                else:
                    snapshot = get_lesson_snapshot(sessao_api, aula)
                gerenciador.sessao.headers["Referer"] = aula.href

                print(
                    f"\n➡️ Aula {aula.position}/{len(aulas)}: {aula.name} "
                    f"(pasta identificada: aula_{aula.number:02d}; "
                    f"{'resumo da API' if aula_futura else '1 snapshot da API'})"
                )
                inventario_aulas[chave_aula] = auditar_e_baixar_snapshot_api(
                    snapshot,
                    arquivo_links,
                    gerenciador,
                    tipos_permitidos=tipos_permitidos,
                    incluir_videos=not modo_reduzido,
                )
                if aula_futura:
                    inventario_aulas[chave_aula].update(
                        {
                            "modo": "resumo_api_aguardando_liberacao",
                            "liberacao": aula.release_date.isoformat(),
                        }
                    )
                save_inventory(
                    download_dir,
                    curso_id,
                    "em_andamento",
                    inventario_aulas,
                )
                gerenciador.concluir_aula()

        print(f"\n✅ Links registrados continuamente em: {out_txt}")
        gerenciador.resumo()
        resumo = gerenciador.resumo_dados()
        identidades_manifesto = {
            arquivo["identidade"]
            for aula in inventario_aulas.values()
            for arquivo in aula.get("arquivos", [])
        }
        resumo.update(
            {
                "versao_auditoria": AUDIT_VERSION,
                "aulas_confirmadas": len(aulas_disponiveis),
                "aulas_aguardando_liberacao": len(aulas_futuras),
                "recursos_unicos_manifesto": len(identidades_manifesto),
            }
        )
        garantir_curso_completo(
            gerenciador,
            catalogo_remoto_vazio=not identidades_manifesto,
        )
        datas = [valor.isoformat() for valor in curso.future_release_dates]
        if datas:
            resumo.update(
                {
                    "status_curso": "aguardando_liberacao",
                    "liberacoes_futuras": datas,
                    "proxima_liberacao": datas[0],
                }
            )
            metadata = {
                "liberacoes_futuras": datas,
                "proxima_liberacao": datas[0],
            }
            save_inventory(
                download_dir,
                curso_id,
                "aguardando_liberacao",
                inventario_aulas,
                metadata=metadata,
            )
            if not salvar_estado_execucao(
                download_dir,
                curso_id,
                "aguardando_liberacao",
                resumo,
            ):
                raise RuntimeError(
                    "não foi possível atualizar o marcador de liberação futura"
                )
            concluido = True
            print(
                f"\n🗓️ Conteúdo atualmente disponível do curso {curso_id} "
                f"foi auditado; próxima liberação em {datas[0]}."
            )
            return resumo
        save_inventory(download_dir, curso_id, "completo", inventario_aulas)
        if not salvar_estado_execucao(download_dir, curso_id, "concluido", resumo):
            raise RuntimeError("não foi possível atualizar o marcador final do curso")
        concluido = True
        print(f"\n✅ Curso {curso_id} auditado sem pendências.")
        return resumo
    except DownloadCancelado:
        raise
    except Exception as erro:
        resumo = gerenciador.resumo_dados() if gerenciador is not None else {}
        raise ProcessamentoCursoError(curso_id, erro, resumo) from erro
    finally:
        if not concluido:
            resumo = gerenciador.resumo_dados() if gerenciador is not None else {}
            try:
                save_inventory(
                    download_dir,
                    curso_id,
                    "incompleto",
                    inventario_aulas,
                )
            except OSError:
                print("⚠️ Não foi possível atualizar o inventário deste curso.")
            if not salvar_estado_execucao(
                download_dir,
                curso_id,
                "incompleto",
                resumo,
            ):
                print("⚠️ Não foi possível atualizar o marcador de retomada.")
        if sessao_api is not None:
            sessao_api.close()
        if gerenciador is not None:
            gerenciador.sessao.close()


def _resumo_colecao(resumos: list[dict], falhas_cursos: int, inicio: float) -> dict:
    encontrados = sum(int(item.get("encontrados", 0)) for item in resumos)
    baixados = sum(int(item.get("baixados", 0)) for item in resumos)
    existentes = sum(int(item.get("existentes", 0)) for item in resumos)
    falhas_itens = sum(int(item.get("falhas", 0)) for item in resumos)
    bytes_concluidos = sum(int(item.get("bytes_concluidos", 0)) for item in resumos)
    cursos_agendados = sum(
        item.get("status_curso") == "aguardando_liberacao" for item in resumos
    )
    return {
        "encontrados": encontrados,
        "baixados": baixados,
        "existentes": existentes,
        "falhas": falhas_itens + falhas_cursos,
        "cursos_total": len(resumos),
        "cursos_incompletos": falhas_cursos,
        "cursos_aguardando_liberacao": cursos_agendados,
        "bytes_concluidos": bytes_concluidos,
        "volume": formatar_tamanho(bytes_concluidos),
        "tempo": formatar_duracao(time.monotonic() - inicio),
        "velocidade_media": "calculada individualmente por curso",
    }


def executar_colecao_integral(
    driver,
    alertas,
    painel: InterfaceWeb,
    pasta_base: Path,
    *,
    pastas_extras: tuple[Path, ...] = (),
    selecionar_curso=None,
) -> dict:
    """Audita sequencialmente todos os cursos retornados para a conta."""

    painel.atualizar(fase="Consultando o catálogo completo da conta")
    catalogo = obter_catalogo_cursos_autenticado(driver)
    cursos = (
        [curso for curso in catalogo if selecionar_curso(curso)]
        if selecionar_curso is not None
        else catalogo
    )
    if not cursos:
        raise CollectionError("o filtro informado não selecionou nenhum curso")
    colecoes = []
    for selecionada in (pasta_base, *pastas_extras):
        raiz, estado, existente = open_collection(selecionada)
        invalidos = invalidate_legacy_completions(estado)
        if invalidos:
            save_collection(raiz, estado)
            print(
                f"⚠️ {len(invalidos)} conclusão(ões) legada(s) foram "
                "reclassificadas como incompletas e serão reavaliadas."
            )
        if any(item["raiz"] == raiz for item in colecoes):
            raise CollectionError(f"a coleção foi informada mais de uma vez: {raiz}")
        colecoes.append(
            {
                "raiz": raiz,
                "estado": estado,
                "existente": existente,
            }
        )
    raiz = colecoes[0]["raiz"]
    colecao_existente = any(item["existente"] for item in colecoes)
    painel.atualizar(
        pasta_destino=str(raiz),
        total_cursos=len(cursos),
        curso_atual=0,
    )
    if colecao_existente:
        print(f"\n🔄 Coleção anterior detectada: {raiz}")
        print("   Todos os cursos serão reauditados; somente lacunas serão baixadas.")
    else:
        print(f"\n🗂️ Nova coleção integral criada: {raiz}")
    if len(colecoes) > 1:
        print(f"💽 Volumes disponíveis para a coleção: {len(colecoes)}")
        for item in colecoes:
            print(f"   • {item['raiz']}")
    print(f"📚 Cursos acessíveis encontrados no catálogo: {len(catalogo)}")
    if len(cursos) != len(catalogo):
        print(f"🎯 Cursos selecionados para esta execução: {len(cursos)}")

    inicio = time.monotonic()
    resumos = []
    falhas = []
    for posicao, curso in enumerate(cursos, start=1):
        painel.verificar_cancelamento()
        painel.atualizar(
            curso_atual=posicao,
            curso_nome=curso.name,
            fase=f"Preparando o curso {posicao} de {len(cursos)}",
            encontrados=0,
            baixados=0,
            existentes=0,
            falhas=0,
            aula_atual=0,
            total_aulas=0,
        )
        print("\n" + "=" * 72)
        print(f"📚 Curso {posicao}/{len(cursos)} — {curso.course_id}: {curso.name}")
        candidatas = [
            item
            for item in colecoes
            if curso.course_id in item["estado"].get("cursos", {})
        ]
        if len(candidatas) > 1:
            raise CollectionError(
                f"o curso {curso.course_id} está registrado em mais de um volume"
            )
        if candidatas:
            colecao = candidatas[0]
        else:
            colecao = max(
                colecoes,
                key=lambda item: verificar_destino(item["raiz"]),
            )
        raiz_curso = colecao["raiz"]
        estado = colecao["estado"]
        pasta_curso = ensure_course_folder(raiz_curso, estado, curso)
        painel.atualizar(pasta_destino=str(pasta_curso))
        print(f"💽 Volume escolhido para o curso: {raiz_curso}")
        update_course_status(estado, curso, pasta_curso, "em_andamento")
        save_collection(raiz_curso, estado)

        try:
            resumo = executar_conteudo_curso(
                driver,
                alertas,
                painel,
                curso.course_id,
                curso.name,
                pasta_curso,
                modo_reduzido=False,
                auditar_existentes=True,
            )
        except DownloadCancelado:
            update_course_status(
                estado,
                curso,
                pasta_curso,
                "incompleto",
                error="download cancelado pelo usuário",
            )
            save_collection(raiz_curso, estado)
            raise
        except ProcessamentoCursoError as erro:
            resumo = erro.resumo
            resumos.append(resumo)
            falhas.append(curso.course_id)
            update_course_status(
                estado,
                curso,
                pasta_curso,
                "incompleto",
                summary=resumo,
                error=str(erro.causa),
            )
            save_collection(raiz_curso, estado)
            print(
                f"\n🚩 Curso {curso.course_id} permanece incompleto: "
                f"{sanitizar_texto(str(erro.causa))}"
            )
            if isinstance(erro.causa, (EspacoInsuficienteError, NoSuchWindowException)):
                painel.definir_resumo(
                    _resumo_colecao(resumos, len(falhas), inicio)
                )
                raise erro.causa
            continue

        resumos.append(resumo)
        status_curso = resumo.get("status_curso", "completo")
        update_course_status(
            estado,
            curso,
            pasta_curso,
            status_curso,
            summary=resumo,
        )
        save_collection(raiz_curso, estado)

    resumo_final = _resumo_colecao(resumos, len(falhas), inicio)
    painel.atualizar(
        encontrados=resumo_final["encontrados"],
        baixados=resumo_final["baixados"],
        existentes=resumo_final["existentes"],
        falhas=resumo_final["falhas"],
    )
    if falhas:
        raise ColecaoIncompletaError(
            "A coleção foi inteiramente percorrida, mas "
            f"{len(falhas)} curso(s) ainda possuem pendências: "
            + ", ".join(falhas),
            resumo_final,
        )
    if resumo_final["cursos_aguardando_liberacao"]:
        print(
            "\n🎉 Todo o conteúdo atualmente disponível foi auditado; "
            f"{resumo_final['cursos_aguardando_liberacao']} curso(s) aguardam "
            "datas de liberação anunciadas pelo portal."
        )
    else:
        print("\n🎉 Todos os cursos do catálogo foram auditados sem pendências.")
    return resumo_final


def executar_download(args, configuracao, painel: InterfaceWeb):
    email = configuracao["email"]
    password = configuracao["password"]
    curso_id = configuracao["curso_id"]
    pasta_base = configuracao["pasta_base"]
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

        painel.atualizar(status="baixando", instrucao_login="")
        painel.verificar_cancelamento()
        if configuracao["modo_integral"]:
            resumo = executar_colecao_integral(
                driver,
                alertas,
                painel,
                pasta_base,
            )
            return {"resumo": resumo, "browser": browser_info}

        curso_url = montar_curso_url(curso_id)
        painel.atualizar(fase="Login concluído. Identificando o curso")
        nome_curso = obter_nome_curso_autenticado(driver, curso_url, curso_id)
        download_dir = criar_pasta_do_curso(
            pasta_base,
            driver,
            curso_id,
            nome_curso,
        )
        resumo = executar_conteudo_curso(
            driver,
            alertas,
            painel,
            curso_id,
            nome_curso,
            download_dir,
            modo_reduzido=configuracao["modo_reduzido"],
        )
        print("\n✅ Processo completo.")
        return {
            "resumo": resumo,
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
        resumo_erro = getattr(e, "resumo", None)
        if isinstance(resumo_erro, dict) and resumo_erro:
            painel.definir_resumo(resumo_erro)
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
