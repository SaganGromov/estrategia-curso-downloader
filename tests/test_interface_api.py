import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import requests

from interface_web import InterfaceWeb


class InterfaceApiTest(unittest.TestCase):
    def abrir(self, pasta, reduzido=False):
        painel = InterfaceWeb(modo_reduzido=reduzido, pasta_inicial=Path(pasta))
        with patch("interface_web.abrir_interface_no_edge"):
            url = painel.iniciar()
        sessao = requests.Session()
        resposta = sessao.get(url, timeout=3)
        self.assertEqual(resposta.status_code, 200)
        self.assertNotIn("token=", resposta.url)
        base = resposta.url.rstrip("/")
        return painel, sessao, base

    def post(self, sessao, base, caminho, dados=None):
        return sessao.post(
            f"{base}{caminho}",
            headers={"X-Estrategia-Request": "1"},
            json=dados or {},
            timeout=3,
        )

    def test_estado_sem_autorizacao_e_negado_e_cookie_autoriza(self):
        with TemporaryDirectory() as pasta:
            painel, sessao, base = self.abrir(pasta)
            try:
                self.assertEqual(
                    requests.get(f"{base}/api/state", timeout=3).status_code, 403
                )
                self.assertEqual(
                    sessao.get(f"{base}/api/state", timeout=3).status_code, 200
                )
            finally:
                painel.parar()

    def test_formulario_invalido_retorna_mensagem_clara(self):
        with TemporaryDirectory() as pasta:
            painel, sessao, base = self.abrir(pasta)
            try:
                resposta = self.post(sessao, base, "/api/start", {"curso": "x"})
                self.assertEqual(resposta.status_code, 400)
                self.assertIn("e-mail", resposta.json()["erro"])
            finally:
                painel.parar()

    def test_modo_do_dashboard_prevalece_e_senha_some_do_estado(self):
        with TemporaryDirectory() as pasta:
            painel, sessao, base = self.abrir(pasta)
            try:
                resposta = self.post(
                    sessao,
                    base,
                    "/api/start",
                    {
                        "email": "ana@example.invalid",
                        "senha": "não-persistir",
                        "curso": "https://site.invalid/app/dashboard/cursos/393267/aulas",
                        "modo": "reduzido",
                    },
                )
                self.assertEqual(resposta.status_code, 202)
                configuracao = painel.aguardar_configuracao()
                self.assertTrue(configuracao["modo_reduzido"])
                self.assertNotIn("não-persistir", str(painel.estado()))
            finally:
                painel.parar()

    def test_cancelamento_e_cooperativo(self):
        with TemporaryDirectory() as pasta:
            painel, sessao, base = self.abrir(pasta)
            try:
                painel.atualizar(status="baixando")
                resposta = self.post(sessao, base, "/api/cancel")
                self.assertEqual(resposta.status_code, 202)
                self.assertIn("Cancelamento", painel.estado()["fase"])
                with self.assertRaises(Exception):
                    painel.verificar_cancelamento()
            finally:
                painel.parar()

    def test_abrir_pasta_antes_do_login_e_rejeitado(self):
        with TemporaryDirectory() as pasta:
            painel, sessao, base = self.abrir(pasta)
            try:
                resposta = self.post(sessao, base, "/api/open-folder")
                self.assertEqual(resposta.status_code, 400)
                self.assertIn("ainda não", resposta.json()["erro"])
            finally:
                painel.parar()

    def test_shutdown_na_configuracao_e_estados_finais(self):
        with TemporaryDirectory() as pasta:
            painel, sessao, base = self.abrir(pasta, reduzido=True)
            try:
                painel.atualizar(encontrados=5, baixados=4, falhas=1)
                painel.finalizar("erro", "Falha compreensível", "Sem acesso")
                estado = painel.estado()
                self.assertEqual(estado["status"], "erro")
                self.assertEqual(estado["resumo"]["encontrados"], 5)
                resposta = self.post(sessao, base, "/api/shutdown")
                self.assertEqual(resposta.status_code, 200)
            finally:
                painel.parar()

    def test_pasta_padrao_ja_esta_disponivel_e_gravavel(self):
        with TemporaryDirectory() as pasta:
            painel = InterfaceWeb(modo_reduzido=False, pasta_inicial=Path(pasta))
            estado = painel.estado()
            self.assertEqual(estado["pasta_base"], str(Path(pasta).resolve()))
            self.assertNotEqual(estado["espaco_disponivel"], "calculando")


if __name__ == "__main__":
    unittest.main()
