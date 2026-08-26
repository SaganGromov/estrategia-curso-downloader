import os
from pathlib import Path

LOGIN_URL = "https://www.estrategiaconcursos.com.br/app/dashboard/cursos"
LOGIN_TIMEOUT = int(os.getenv("ESTRATEGIA_LOGIN_TIMEOUT", "600"))
EDGE_DRIVER_PATH = os.getenv("ESTRATEGIA_EDGE_DRIVER")

SELENIUM_WAIT_TIMEOUT = 30
SELENIUM_SHORT_WAIT = 15
CONTENT_STABILITY_SECONDS = 2.0
DISCOVERY_MAX_ROUNDS = 40
DISCOVERY_STABLE_ROUNDS = 4
DISCOVERY_SCROLL_PAUSE = 0.5
INVENTORY_MAX_PASSES = 12
INVENTORY_STABLE_OBSERVATIONS = 3
INVENTORY_EMPTY_STABLE_OBSERVATIONS = 4
VIDEO_OPTIONS_TIMEOUT = 25
VIDEO_SELECTION_RETRIES = 3
VIDEO_RECOVERY_PASSES = 3
HTTP_CONNECT_TIMEOUT = 30
HTTP_READ_TIMEOUT = 120
MAX_DOWNLOAD_RETRIES = 3
RETRY_DELAY_SECONDS = 3
DOWNLOAD_CHUNK_SIZE = 512 * 1024
PROGRESS_UPDATE_INTERVAL = 1.0
DISK_SAFETY_MARGIN = 16 * 1024 * 1024
ALERT_RECOVERY_ATTEMPTS = 3


def pasta_download_padrao() -> Path:
    configurada = (os.getenv("DOWNLOAD_DIR") or "").strip()
    if configurada:
        return Path(configurada).expanduser().resolve()

    # O identificador é o Known Folder "Downloads" do Windows. A consulta ao
    # registro respeita redirecionamentos feitos pelo próprio usuário/OneDrive.
    if os.name == "nt":
        try:
            import winreg

            chave = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                "Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\"
                "User Shell Folders",
            )
            try:
                valor, _ = winreg.QueryValueEx(
                    chave, "{374DE290-123F-4565-9164-39C4925E467B}"
                )
            finally:
                winreg.CloseKey(chave)
            return Path(os.path.expandvars(valor)) / "Estrategia"
        except (OSError, ValueError):
            pass
    return Path.home() / "Downloads" / "Estrategia"
