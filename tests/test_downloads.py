import io
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import requests

from estrategia_downloader.downloads import GerenciadorDownloads

CONTEUDO = b"abcdefghij"


class DriverFake:
    def get_cookies(self):
        return []

    def execute_script(self, _script):
        return "Agente de teste"


class RespostaFake:
    def __init__(self, corpo, *, status=200, headers=None, interromper=False):
        self.corpo = corpo
        self.status_code = status
        self.headers = headers or {
            "Content-Type": "application/pdf",
            "Content-Length": str(len(corpo)),
        }
        self.interromper = interromper

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def raise_for_status(self):
        if self.status_code >= 400:
            erro = requests.HTTPError(f"HTTP {self.status_code}")
            erro.response = self
            raise erro

    def iter_content(self, chunk_size):
        meio = max(len(self.corpo) // 2, 1)
        yield self.corpo[:meio]
        if self.interromper:
            raise requests.ConnectionError("conexão interrompida")
        yield self.corpo[meio:]


class SessaoFake:
    def __init__(self, respostas):
        self.respostas = list(respostas)
        self.requisicoes = []

    def get(self, url, **kwargs):
        self.requisicoes.append((url, kwargs))
        return self.respostas.pop(0)


class PainelFake:
    def __init__(self, cancelar_na_verificacao=None):
        self.atualizacoes = []
        self.verificacoes = 0
        self.cancelar_na_verificacao = cancelar_na_verificacao

    def atualizar(self, **dados):
        self.atualizacoes.append(dados)

    def verificar_cancelamento(self):
        self.verificacoes += 1
        if (
            self.cancelar_na_verificacao
            and self.verificacoes >= self.cancelar_na_verificacao
        ):
            raise Cancelado("cancelado")


class Cancelado(Exception):
    pass


def item(url="https://arquivos.example/material.pdf?signature=secreta"):
    return {
        "tipo": "pdf",
        "aula_num": 1,
        "item_num": 1,
        "titulo": "Material",
        "extensao": ".pdf",
        "url": url,
    }


class DownloadsResumeTest(unittest.TestCase):
    def criar(self, pasta, respostas, *, painel=None, tentativas=1):
        gerenciador = GerenciadorDownloads(
            Path(pasta), DriverFake(), "https://curso.example", tentativas, painel
        )
        gerenciador.sessao = SessaoFake(respostas)
        return gerenciador

    def test_transferencia_nova(self):
        with TemporaryDirectory() as pasta:
            gerenciador = self.criar(pasta, [RespostaFake(CONTEUDO)])
            with patch("sys.stdout", new_callable=io.StringIO):
                self.assertTrue(gerenciador.baixar(item()))
            self.assertEqual(
                (Path(pasta) / "Aula 01 - PDF 01 - Material.pdf").read_bytes(),
                CONTEUDO,
            )

    def test_retomada_206_anexa_no_offset_correto(self):
        with TemporaryDirectory() as pasta:
            parcial = Path(pasta) / "Aula 01 - PDF 01 - Material.pdf.part"
            parcial.write_bytes(CONTEUDO[:4])
            resposta = RespostaFake(
                CONTEUDO[4:],
                status=206,
                headers={
                    "Content-Type": "application/pdf",
                    "Content-Length": "6",
                    "Content-Range": "bytes 4-9/10",
                },
            )
            painel = PainelFake()
            gerenciador = self.criar(pasta, [resposta], painel=painel)
            with patch("sys.stdout", new_callable=io.StringIO):
                self.assertTrue(gerenciador.baixar(item()))
            self.assertEqual(
                gerenciador.sessao.requisicoes[0][1]["headers"]["Range"], "bytes=4-"
            )
            self.assertEqual(parcial.with_suffix("").read_bytes(), CONTEUDO)
            recebidos = [
                atualizacao["item"]["recebido"]
                for atualizacao in painel.atualizacoes
                if "item" in atualizacao
            ]
            self.assertIn("7.0 B", recebidos)

    def test_servidor_ignora_range_e_reinicia_do_zero(self):
        with TemporaryDirectory() as pasta:
            parcial = Path(pasta) / "Aula 01 - PDF 01 - Material.pdf.part"
            parcial.write_bytes(b"lixo")
            gerenciador = self.criar(pasta, [RespostaFake(CONTEUDO)])
            with patch("sys.stdout", new_callable=io.StringIO):
                self.assertTrue(gerenciador.baixar(item()))
            self.assertEqual(parcial.with_suffix("").read_bytes(), CONTEUDO)

    def test_content_range_errado_nao_corrompe_parcial(self):
        with TemporaryDirectory() as pasta:
            parcial = Path(pasta) / "Aula 01 - PDF 01 - Material.pdf.part"
            parcial.write_bytes(CONTEUDO[:4])
            resposta = RespostaFake(
                CONTEUDO[4:],
                status=206,
                headers={
                    "Content-Type": "application/pdf",
                    "Content-Range": "bytes 5-9/10",
                },
            )
            gerenciador = self.criar(pasta, [resposta])
            with patch("sys.stdout", new_callable=io.StringIO):
                self.assertFalse(gerenciador.baixar(item()))
            self.assertEqual(parcial.read_bytes(), CONTEUDO[:4])

    def test_nova_interrupcao_preserva_parcial_valido(self):
        with TemporaryDirectory() as pasta:
            parcial = Path(pasta) / "Aula 01 - PDF 01 - Material.pdf.part"
            parcial.write_bytes(CONTEUDO[:4])
            resposta = RespostaFake(
                CONTEUDO[4:],
                status=206,
                headers={
                    "Content-Type": "application/pdf",
                    "Content-Range": "bytes 4-9/10",
                },
                interromper=True,
            )
            gerenciador = self.criar(pasta, [resposta])
            with patch("sys.stdout", new_callable=io.StringIO):
                self.assertFalse(gerenciador.baixar(item()))
            self.assertEqual(parcial.read_bytes(), CONTEUDO[:7])

    def test_arquivo_completo_existente_e_pulado(self):
        with TemporaryDirectory() as pasta:
            destino = Path(pasta) / "Aula 01 - PDF 01 - Material.pdf"
            destino.write_bytes(CONTEUDO)
            gerenciador = self.criar(pasta, [])
            with patch("sys.stdout", new_callable=io.StringIO):
                self.assertTrue(gerenciador.baixar(item()))
            self.assertEqual(gerenciador.existentes, 1)
            self.assertEqual(gerenciador.sessao.requisicoes, [])

    def test_cancelamento_em_retomada_preserva_bytes_recebidos(self):
        with TemporaryDirectory() as pasta:
            parcial = Path(pasta) / "Aula 01 - PDF 01 - Material.pdf.part"
            parcial.write_bytes(CONTEUDO[:4])
            resposta = RespostaFake(
                CONTEUDO[4:],
                status=206,
                headers={
                    "Content-Type": "application/pdf",
                    "Content-Range": "bytes 4-9/10",
                },
            )
            painel = PainelFake(cancelar_na_verificacao=4)
            gerenciador = self.criar(pasta, [resposta], painel=painel)
            with patch("sys.stdout", new_callable=io.StringIO):
                with self.assertRaises(Cancelado):
                    gerenciador.baixar(item())
            self.assertEqual(parcial.read_bytes(), CONTEUDO[:7])


if __name__ == "__main__":
    unittest.main()
