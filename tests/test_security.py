import json
import io
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock

from estrategia_downloader.app import registrar_e_baixar
from estrategia_downloader.diagnostics import criar_diagnostico
from estrategia_downloader.integrity import safe_resource_record, save_inventory


class SecurityDiagnosticsTest(unittest.TestCase):
    def test_manifest_does_not_persist_the_temporary_download_url(self):
        manager = Mock()
        manager.urls_processadas = set()
        output = io.StringIO()
        registrar_e_baixar(
            {
                "aula_num": 1,
                "tipo": "video",
                "item_num": 2,
                "titulo": "Explicação",
                "url": "https://cdn.invalid/video?signature=nao-persistir",
            },
            output,
            manager,
        )

        self.assertNotIn("cdn.invalid", output.getvalue())
        self.assertNotIn("nao-persistir", output.getvalue())
        self.assertIn("URL omitida", output.getvalue())
        manager.baixar.assert_called_once()

    def test_diagnostico_remove_segredos_e_stacktrace(self):
        erro = RuntimeError(
            "falhou https://cdn.invalid/a?signature=secreta Stacktrace: 0x123"
        )
        relatorio = criar_diagnostico(
            fase="teste",
            logs=[
                "password=segredo",
                "Cookie: sessao-privada",
                "X-Interface-Token=token-local",
                "https://cdn.invalid/a?access_token=abc&pagina=2",
            ],
            erro=erro,
        )
        self.assertNotIn("segredo", relatorio)
        self.assertNotIn("sessao-privada", relatorio)
        self.assertNotIn("token-local", relatorio)
        self.assertNotIn("secreta", relatorio)
        self.assertNotIn("0x123", relatorio)
        self.assertNotIn('"abc"', relatorio)
        self.assertIn("REMOVIDO", relatorio)
        self.assertEqual(json.loads(relatorio)["fase"], "teste")

    def test_inventario_persiste_hash_sem_url_ou_assinatura(self):
        item = {
            "tipo": "pdf",
            "item_num": 1,
            "titulo": "Livro Eletrônico",
            "url": "https://api.invalid/pdf/10?signature=segredo&Expires=20",
        }
        registro = safe_resource_record(item)

        with TemporaryDirectory() as directory:
            save_inventory(
                Path(directory),
                "327530",
                "completo",
                {"aula_05": {"arquivos": [registro]}},
            )
            texto = (Path(directory) / ".inventario_estrategia.json").read_text(
                encoding="utf-8"
            )

        self.assertNotIn("api.invalid", texto)
        self.assertNotIn("segredo", texto)
        self.assertNotIn("Expires", texto)
        self.assertEqual(len(registro["identidade"]), 64)


if __name__ == "__main__":
    unittest.main()
