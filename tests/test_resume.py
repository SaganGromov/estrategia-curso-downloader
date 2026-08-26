import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from estrategia_downloader.resume import (
    ARQUIVO_ESTADO,
    localizar_pasta_retomavel,
    salvar_estado_execucao,
)


class ResumeTest(unittest.TestCase):
    def test_reaproveita_execucao_legada_mais_recente(self):
        with TemporaryDirectory() as diretorio:
            base = Path(diretorio)
            antiga = base / "CURSO_ESTRATEGIA_327532_100"
            recente = base / "CURSO_ESTRATEGIA_327532_200"
            outro_curso = base / "CURSO_ESTRATEGIA_999999_300"
            for pasta in (antiga, recente, outro_curso):
                pasta.mkdir()
                (pasta / "links_estrategia_conteudo.txt").write_text(
                    "aula;tipo;numero;titulo;url\n", encoding="utf-8"
                )

            self.assertEqual(localizar_pasta_retomavel(base, "327532"), recente)

    def test_execucao_concluida_nao_e_reutilizada(self):
        with TemporaryDirectory() as diretorio:
            base = Path(diretorio)
            pasta = base / "CURSO_ESTRATEGIA_327532_200"
            pasta.mkdir()
            self.assertTrue(
                salvar_estado_execucao(pasta, "327532", "concluido", {"falhas": 0})
            )

            self.assertIsNone(localizar_pasta_retomavel(base, "327532"))

    def test_execucao_incompleta_e_reutilizada(self):
        with TemporaryDirectory() as diretorio:
            base = Path(diretorio)
            pasta = base / "CURSO_ESTRATEGIA_327532_200"
            pasta.mkdir()
            self.assertTrue(
                salvar_estado_execucao(
                    pasta, "327532", "incompleto", {"falhas": 3}
                )
            )

            self.assertEqual(localizar_pasta_retomavel(base, "327532"), pasta)
            estado = json.loads((pasta / ARQUIVO_ESTADO).read_text(encoding="utf-8"))
            self.assertEqual(estado["status"], "incompleto")
            self.assertEqual(estado["resumo"]["falhas"], 3)

    def test_reaproveita_pasta_descritiva_incompleta_mais_recente(self):
        with TemporaryDirectory() as diretorio:
            base = Path(diretorio)
            antiga = base / "macroeconomia-id-327532-100"
            recente = base / "macroeconomia-avancada-id-327532-200"
            for pasta in (antiga, recente):
                pasta.mkdir()
                self.assertTrue(
                    salvar_estado_execucao(pasta, "327532", "em_andamento")
                )

            self.assertEqual(localizar_pasta_retomavel(base, "327532"), recente)

    def test_pasta_descritiva_sem_marcador_nao_e_assumida_como_retornavel(self):
        with TemporaryDirectory() as diretorio:
            base = Path(diretorio)
            (base / "macroeconomia-id-327532-200").mkdir()

            self.assertIsNone(localizar_pasta_retomavel(base, "327532"))

    def test_marcador_de_outro_curso_nunca_e_reutilizado(self):
        with TemporaryDirectory() as diretorio:
            base = Path(diretorio)
            pasta = base / "CURSO_ESTRATEGIA_327532_200"
            pasta.mkdir()
            self.assertTrue(salvar_estado_execucao(pasta, "999999", "incompleto"))

            self.assertIsNone(localizar_pasta_retomavel(base, "327532"))


if __name__ == "__main__":
    unittest.main()
