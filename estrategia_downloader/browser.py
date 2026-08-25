import os
from pathlib import Path

from selenium import webdriver
from selenium.common.exceptions import SessionNotCreatedException, WebDriverException
from selenium.webdriver.edge.options import Options as EdgeOptions
from selenium.webdriver.edge.service import Service as EdgeService

from .config import EDGE_DRIVER_PATH


class BrowserStartupError(RuntimeError):
    pass


def localizar_edge() -> Path | None:
    candidatos = []
    for variavel in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
        raiz = os.getenv(variavel)
        if raiz:
            candidatos.append(
                Path(raiz) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
            )
    return next((caminho for caminho in candidatos if caminho.is_file()), None)


def create_edge_driver(download_path: Path):
    opts = EdgeOptions()
    opts.add_argument("--start-maximized")
    opts.unhandled_prompt_behavior = "dismiss and notify"
    opts.add_experimental_option(
        "prefs",
        {
            "download.default_directory": str(download_path),
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
            "safebrowsing.enabled": True,
        },
    )

    if not localizar_edge():
        raise BrowserStartupError(
            "Microsoft Edge não foi encontrado. Instale o Edge pelo site oficial "
            "da Microsoft e execute iniciar.bat novamente."
        )

    try:
        if EDGE_DRIVER_PATH:
            service = EdgeService(executable_path=EDGE_DRIVER_PATH)
            return webdriver.Edge(service=service, options=opts)
        print("🌐 Preparando o Edge; o Selenium Manager cuidará do driver...")
        return webdriver.Edge(options=opts)
    except (SessionNotCreatedException, WebDriverException) as erro:
        raise BrowserStartupError(
            "Não foi possível abrir o Microsoft Edge. Feche janelas travadas do "
            "Edge, confirme a conexão com a internet e tente novamente. O "
            "Selenium Manager prepara o driver automaticamente."
        ) from erro


def diagnostico_browser(driver) -> dict:
    capacidades = getattr(driver, "capabilities", {}) or {}
    opcoes_edge = capacidades.get("ms:edgeOptions", {}) or {}
    return {
        "nome": capacidades.get("browserName", "Microsoft Edge"),
        "versao": capacidades.get("browserVersion", "desconhecida"),
        "driver": opcoes_edge.get("msedgedriverVersion", "gerenciado pelo Selenium"),
    }
