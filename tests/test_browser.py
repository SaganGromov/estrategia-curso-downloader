import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from estrategia_downloader.browser import create_edge_driver


class BrowserPerformanceLoggingTest(unittest.TestCase):
    @patch("estrategia_downloader.browser.EDGE_DRIVER_PATH", "")
    @patch("estrategia_downloader.browser.webdriver.Edge")
    @patch(
        "estrategia_downloader.browser.localizar_edge",
        return_value=Path("C:/Program Files/Microsoft/Edge/msedge.exe"),
    )
    def test_edge_uses_its_ms_logging_namespace(
        self, _find_edge, webdriver_edge
    ):
        with TemporaryDirectory() as directory:
            create_edge_driver(Path(directory), performance_logging=True)

        options = webdriver_edge.call_args.kwargs["options"]
        capabilities = options.to_capabilities()
        self.assertEqual(
            capabilities["ms:loggingPrefs"],
            {"performance": "ALL"},
        )
        self.assertEqual(
            capabilities["ms:edgeOptions"]["perfLoggingPrefs"],
            {"enableNetwork": True, "enablePage": True},
        )


if __name__ == "__main__":
    unittest.main()
