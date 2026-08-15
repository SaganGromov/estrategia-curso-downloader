import json
import unittest

from estrategia_downloader.diagnostics import criar_diagnostico


class SecurityDiagnosticsTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
