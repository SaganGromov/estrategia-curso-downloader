import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]


class BootstrapTest(unittest.TestCase):
    def test_metadados_python_estao_fixos_e_com_hash(self):
        config = json.loads((ROOT / "bootstrap-config.json").read_text("utf-8"))
        self.assertEqual(config["pythonVersion"], "3.12.10")
        for arquitetura in ("x64", "arm64", "x86"):
            instalador = config["installers"][arquitetura]
            self.assertTrue(instalador["url"].startswith("https://www.python.org/"))
            self.assertRegex(instalador["sha256"], r"^[0-9a-f]{64}$")

    def test_launcher_nao_depende_de_python_global(self):
        launcher = (ROOT / "iniciar.bat").read_text("utf-8").lower()
        self.assertIn("bootstrap.ps1", launcher)
        self.assertNotIn("where py", launcher)
        self.assertNotIn("pip install", launcher)

    def test_bootstrap_valida_assinatura_hash_edge_e_arquivos(self):
        bootstrap = (ROOT / "bootstrap.ps1").read_text("utf-8")
        self.assertIn("Get-AuthenticodeSignature", bootstrap)
        self.assertIn("Get-FileHash", bootstrap)
        self.assertIn("Get-EdgePath", bootstrap)
        self.assertIn("estrategia_downloader\\downloads.py", bootstrap)
        self.assertIn("estrategia_downloader\\resume.py", bootstrap)
        self.assertIn("Alguns arquivos do aplicativo nao foram encontrados", bootstrap)

    def test_bootstrap_tem_reparo_e_estado_por_hash(self):
        bootstrap = (ROOT / "bootstrap.ps1").read_text("utf-8")
        self.assertIn("Test-ApplicationEnvironment", bootstrap)
        self.assertIn("New-ApplicationEnvironment", bootstrap)
        self.assertIn("requirementsSha256", bootstrap)
        self.assertIn("Ambiente ausente ou danificado; recriando", bootstrap)
        self.assertIn("Nao foi possivel instalar os componentes", bootstrap)

    def test_launchers_compartilham_mesmo_bootstrap(self):
        reduzido = (ROOT / "iniciar_pdfs_e_slides.bat").read_text("utf-8").lower()
        self.assertIn("iniciar.bat", reduzido)
        self.assertIn("--pdfs-e-slides", reduzido)

    def test_lock_tem_apenas_versoes_exatas(self):
        linhas = [
            linha.strip()
            for linha in (ROOT / "requirements.lock.txt")
            .read_text("utf-8")
            .splitlines()
            if linha.strip() and not linha.startswith("#")
        ]
        self.assertIn("requests==2.34.2", linhas)
        self.assertIn("selenium==4.47.0", linhas)
        self.assertTrue(all(linha.count("==") == 1 for linha in linhas))

    @unittest.skipUnless(sys.platform == "win32", "validação PowerShell é Windows-only")
    def test_validacao_funciona_em_caminho_com_espacos_e_acentos(self):
        with TemporaryDirectory(prefix="Estratégia Curso (2) ") as temporario:
            copia = Path(temporario) / "Aplicação extraída"
            shutil.copytree(
                ROOT, copia, ignore=shutil.ignore_patterns(".git", "__pycache__")
            )
            ambiente = os.environ.copy()
            ambiente["ESTRATEGIA_BOOTSTRAP_NO_DIALOG"] = "1"
            resultado = subprocess.run(
                [
                    "powershell.exe",
                    "-NoLogo",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(copia / "bootstrap.ps1"),
                    "--validate-bootstrap",
                ],
                capture_output=True,
                text=True,
                encoding="cp1252",
                errors="replace",
                env=ambiente,
                timeout=30,
            )
            self.assertEqual(
                resultado.returncode, 0, resultado.stdout + resultado.stderr
            )
            self.assertIn(
                "Bootstrap e arquivos obrigatorios validados", resultado.stdout
            )

            launcher = subprocess.run(
                [
                    "cmd.exe",
                    "/d",
                    "/c",
                    "call",
                    str(copia / "iniciar.bat"),
                    "--validate-bootstrap",
                ],
                cwd=Path(os.environ.get("SystemRoot", r"C:\Windows")),
                capture_output=True,
                text=True,
                encoding="cp1252",
                errors="replace",
                env=ambiente,
                timeout=30,
            )
            self.assertEqual(launcher.returncode, 0, launcher.stdout + launcher.stderr)


if __name__ == "__main__":
    unittest.main()
