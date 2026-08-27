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
    def __init__(self, respostas, cabecas=None):
        self.respostas = list(respostas)
        self.cabecas = list(cabecas or [])
        self.requisicoes = []
        self.requisicoes_head = []

    def get(self, url, **kwargs):
        self.requisicoes.append((url, kwargs))
        return self.respostas.pop(0)

    def head(self, url, **kwargs):
        self.requisicoes_head.append((url, kwargs))
        return self.cabecas.pop(0)


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


def item(url="https://arquivos.example/material.pdf?signature=secreta", **alteracoes):
    dados = {
        "tipo": "pdf",
        "aula_num": 1,
        "aula_nome": "Aula 01",
        "item_num": 1,
        "titulo": "Material",
        "extensao": ".pdf",
        "url": url,
    }
    dados.update(alteracoes)
    return dados


def caminho_pdf(pasta, *, parcial=False):
    caminho = Path(pasta) / "aula_01" / "pdfs" / "PDF 01 - Material.pdf"
    if parcial:
        caminho = caminho.with_suffix(".pdf.part")
        caminho.parent.mkdir(parents=True, exist_ok=True)
    return caminho


class DownloadsResumeTest(unittest.TestCase):
    def criar(
        self,
        pasta,
        respostas,
        *,
        painel=None,
        tentativas=1,
        auditar_existentes=False,
        cabecas=None,
    ):
        gerenciador = GerenciadorDownloads(
            Path(pasta),
            DriverFake(),
            "https://curso.example",
            tentativas,
            painel,
            auditar_existentes,
        )
        gerenciador.sessao = SessaoFake(respostas, cabecas)
        return gerenciador

    def test_transferencia_nova(self):
        with TemporaryDirectory() as pasta:
            gerenciador = self.criar(pasta, [RespostaFake(CONTEUDO)])
            with patch("sys.stdout", new_callable=io.StringIO):
                self.assertTrue(gerenciador.baixar(item()))
            self.assertEqual(caminho_pdf(pasta).read_bytes(), CONTEUDO)

    def test_retomada_206_anexa_no_offset_correto(self):
        with TemporaryDirectory() as pasta:
            parcial = caminho_pdf(pasta, parcial=True)
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
            parcial = caminho_pdf(pasta, parcial=True)
            parcial.write_bytes(b"lixo")
            gerenciador = self.criar(pasta, [RespostaFake(CONTEUDO)])
            with patch("sys.stdout", new_callable=io.StringIO):
                self.assertTrue(gerenciador.baixar(item()))
            self.assertEqual(parcial.with_suffix("").read_bytes(), CONTEUDO)

    def test_content_range_errado_nao_corrompe_parcial(self):
        with TemporaryDirectory() as pasta:
            parcial = caminho_pdf(pasta, parcial=True)
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
            parcial = caminho_pdf(pasta, parcial=True)
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
            destino = caminho_pdf(pasta)
            destino.parent.mkdir(parents=True)
            destino.write_bytes(CONTEUDO)
            gerenciador = self.criar(pasta, [])
            with patch("sys.stdout", new_callable=io.StringIO):
                self.assertTrue(gerenciador.baixar(item()))
            self.assertEqual(gerenciador.existentes, 1)
            self.assertEqual(len(gerenciador.urls_concluidas), 1)
            self.assertEqual(
                gerenciador.resumo_dados()["bytes_concluidos"],
                len(CONTEUDO),
            )
            self.assertEqual(gerenciador.resumo_dados()["volume"], "10.0 B")
            self.assertEqual(gerenciador.sessao.requisicoes, [])

    def test_auditoria_valida_existente_pelo_tamanho_remoto(self):
        with TemporaryDirectory() as pasta:
            destino = caminho_pdf(pasta)
            destino.parent.mkdir(parents=True)
            destino.write_bytes(CONTEUDO)
            cabeca = RespostaFake(b"", headers={"Content-Length": "10"})
            gerenciador = self.criar(
                pasta,
                [],
                auditar_existentes=True,
                cabecas=[cabeca],
            )

            with patch("sys.stdout", new_callable=io.StringIO):
                self.assertTrue(gerenciador.baixar(item()))

            self.assertEqual(gerenciador.existentes, 1)
            self.assertEqual(gerenciador.sessao.requisicoes, [])
            self.assertEqual(len(gerenciador.sessao.requisicoes_head), 1)

    def test_auditoria_retoma_arquivo_existente_truncado(self):
        with TemporaryDirectory() as pasta:
            destino = caminho_pdf(pasta)
            destino.parent.mkdir(parents=True)
            destino.write_bytes(CONTEUDO[:4])
            cabeca = RespostaFake(b"", headers={"Content-Length": "10"})
            resposta = RespostaFake(
                CONTEUDO[4:],
                status=206,
                headers={
                    "Content-Type": "application/pdf",
                    "Content-Length": "6",
                    "Content-Range": "bytes 4-9/10",
                },
            )
            gerenciador = self.criar(
                pasta,
                [resposta],
                auditar_existentes=True,
                cabecas=[cabeca],
            )

            with patch("sys.stdout", new_callable=io.StringIO):
                self.assertTrue(gerenciador.baixar(item()))

            self.assertEqual(destino.read_bytes(), CONTEUDO)
            self.assertEqual(
                gerenciador.sessao.requisicoes[0][1]["headers"]["Range"],
                "bytes=4-",
            )
            self.assertEqual(gerenciador.baixados, 1)

    def test_auditoria_confirma_existente_com_range_quando_head_falha(self):
        with TemporaryDirectory() as pasta:
            destino = caminho_pdf(pasta)
            destino.parent.mkdir(parents=True)
            destino.write_bytes(CONTEUDO)
            cabeca = RespostaFake(b"", status=405)
            resposta = RespostaFake(
                b"",
                status=416,
                headers={
                    "Content-Type": "application/pdf",
                    "Content-Range": "bytes */10",
                },
            )
            gerenciador = self.criar(
                pasta,
                [resposta],
                auditar_existentes=True,
                cabecas=[cabeca],
            )

            with patch("sys.stdout", new_callable=io.StringIO):
                self.assertTrue(gerenciador.baixar(item()))

            self.assertEqual(destino.read_bytes(), CONTEUDO)
            self.assertEqual(gerenciador.existentes, 1)
            self.assertEqual(gerenciador.baixados, 0)

    def test_auditoria_substitui_sobretamanho_e_remove_backup_apos_sucesso(self):
        with TemporaryDirectory() as pasta:
            destino = caminho_pdf(pasta)
            destino.parent.mkdir(parents=True)
            destino.write_bytes(CONTEUDO + b"lixo")
            cabeca = RespostaFake(b"", headers={"Content-Length": "10"})
            gerenciador = self.criar(
                pasta,
                [RespostaFake(CONTEUDO)],
                auditar_existentes=True,
                cabecas=[cabeca],
            )

            with patch("sys.stdout", new_callable=io.StringIO):
                self.assertTrue(gerenciador.baixar(item()))

            self.assertEqual(destino.read_bytes(), CONTEUDO)
            self.assertEqual(
                list(destino.parent.glob("*.pre-auditoria-*")),
                [],
            )

    def test_auditoria_preserva_backup_se_a_substituicao_falha(self):
        with TemporaryDirectory() as pasta:
            destino = caminho_pdf(pasta)
            destino.parent.mkdir(parents=True)
            conteudo_antigo = CONTEUDO + b"lixo"
            destino.write_bytes(conteudo_antigo)
            cabeca = RespostaFake(b"", headers={"Content-Length": "10"})
            gerenciador = self.criar(
                pasta,
                [RespostaFake(CONTEUDO, interromper=True)],
                auditar_existentes=True,
                cabecas=[cabeca],
            )

            with patch("sys.stdout", new_callable=io.StringIO):
                self.assertFalse(gerenciador.baixar(item()))

            backups = list(destino.parent.glob("*.pre-auditoria-*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), conteudo_antigo)

    def test_cancelamento_em_retomada_preserva_bytes_recebidos(self):
        with TemporaryDirectory() as pasta:
            parcial = caminho_pdf(pasta, parcial=True)
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

    def test_prepara_videos_e_pdfs_para_cada_aula(self):
        with TemporaryDirectory() as pasta:
            gerenciador = self.criar(pasta, [])
            for numero in (0, 1, 12):
                gerenciador.preparar_aula(numero)
                raiz = Path(pasta) / f"aula_{numero:02d}"
                self.assertTrue((raiz / "videos").is_dir())
                self.assertTrue((raiz / "pdfs").is_dir())

    def test_link_repetido_e_materializado_em_cada_aula_sem_novo_get(self):
        with TemporaryDirectory() as pasta:
            gerenciador = self.criar(pasta, [RespostaFake(CONTEUDO)])
            primeira = item(aula_num=1)
            segunda = item(aula_num=2)

            with patch("sys.stdout", new_callable=io.StringIO):
                self.assertTrue(gerenciador.baixar(primeira))
                self.assertTrue(gerenciador.baixar(segunda))

            origem = caminho_pdf(pasta)
            repeticao = (
                Path(pasta) / "aula_02" / "pdfs" / "PDF 01 - Material.pdf"
            )
            self.assertEqual(origem.read_bytes(), CONTEUDO)
            self.assertEqual(repeticao.read_bytes(), CONTEUDO)
            self.assertEqual(len(gerenciador.sessao.requisicoes), 1)
            self.assertEqual(gerenciador.encontrados, 1)
            self.assertEqual(gerenciador.ocorrencias_reutilizadas, 1)
            self.assertEqual(gerenciador.ocorrencias_pendentes(), set())

    def test_nova_passagem_da_mesma_ocorrencia_nao_cria_sufixo(self):
        with TemporaryDirectory() as pasta:
            gerenciador = self.criar(pasta, [RespostaFake(CONTEUDO)])

            with patch("sys.stdout", new_callable=io.StringIO):
                self.assertTrue(gerenciador.baixar(item()))
                self.assertTrue(gerenciador.baixar(item()))

            arquivos = list((Path(pasta) / "aula_01" / "pdfs").glob("*.pdf"))
            self.assertEqual(arquivos, [caminho_pdf(pasta)])
            self.assertEqual(gerenciador.ocorrencias_reutilizadas, 0)

    def test_repeticao_corrompida_e_substituida_pela_origem_validada(self):
        with TemporaryDirectory() as pasta:
            gerenciador = self.criar(pasta, [RespostaFake(CONTEUDO)])
            repeticao = (
                Path(pasta) / "aula_02" / "pdfs" / "PDF 01 - Material.pdf"
            )
            repeticao.parent.mkdir(parents=True)
            repeticao.write_bytes(b"conteudo incorreto")

            with patch("sys.stdout", new_callable=io.StringIO):
                self.assertTrue(gerenciador.baixar(item(aula_num=1)))
                self.assertTrue(gerenciador.baixar(item(aula_num=2)))

            self.assertEqual(repeticao.read_bytes(), CONTEUDO)
            self.assertEqual(list(repeticao.parent.glob("*.pre-auditoria-*")), [])

    def test_url_com_falha_pode_ser_recuperada_em_passagem_posterior(self):
        with TemporaryDirectory() as pasta:
            gerenciador = self.criar(
                pasta,
                [RespostaFake(b"", status=500), RespostaFake(CONTEUDO)],
            )

            with patch("sys.stdout", new_callable=io.StringIO):
                self.assertFalse(gerenciador.baixar(item()))
                self.assertTrue(gerenciador.baixar(item()))

            self.assertEqual(caminho_pdf(pasta).read_bytes(), CONTEUDO)
            self.assertEqual(len(gerenciador.sessao.requisicoes), 2)
            self.assertEqual(gerenciador.encontrados, 1)
            self.assertEqual(gerenciador.falhas, 0)
            self.assertEqual(gerenciador.ocorrencias_pendentes(), set())

    def test_organiza_video_pdf_e_anexo_em_subpastas(self):
        respostas = [
            RespostaFake(
                CONTEUDO,
                headers={"Content-Type": "video/mp4", "Content-Length": "10"},
            ),
            RespostaFake(CONTEUDO),
            RespostaFake(
                CONTEUDO,
                headers={
                    "Content-Type": "application/zip",
                    "Content-Length": "10",
                },
            ),
        ]
        with TemporaryDirectory() as pasta:
            gerenciador = self.criar(pasta, respostas)
            itens = [
                item(
                    "https://arquivos.example/video.mp4",
                    tipo="video",
                    aula_num=0,
                    titulo="Introdução",
                    extensao=".mp4",
                ),
                item(
                    "https://arquivos.example/slides.pdf",
                    tipo="slides",
                    aula_num=0,
                    item_num=2,
                    titulo="Apresentação",
                ),
                item(
                    "https://arquivos.example/anexo.zip",
                    tipo="material",
                    aula_num=0,
                    item_num=3,
                    titulo="Arquivos",
                    extensao=".zip",
                ),
            ]
            with patch("sys.stdout", new_callable=io.StringIO):
                for conteudo in itens:
                    self.assertTrue(gerenciador.baixar(conteudo))

            aula = Path(pasta) / "aula_00"
            self.assertTrue((aula / "videos" / "Vídeo 01 - Introdução.mp4").is_file())
            self.assertTrue(
                (aula / "pdfs" / "Slides 02 - Apresentação.pdf").is_file()
            )
            self.assertTrue(
                (aula / "outros_materiais" / "Material 03 - Arquivos.zip").is_file()
            )


if __name__ == "__main__":
    unittest.main()
