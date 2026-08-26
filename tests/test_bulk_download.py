import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from estrategia_downloader import app
from estrategia_downloader.collection import (
    COLLECTION_DIRECTORY_NAME,
    COLLECTION_MARKER,
)
from estrategia_downloader.course_metadata import CourseSummary
from estrategia_downloader.errors import (
    ColecaoIncompletaError,
    ProcessamentoCursoError,
    mensagem_usuario_para_erro,
)
from estrategia_downloader.resume import ARQUIVO_ESTADO


def summary(*, found=3, downloaded=2, existing=1, failures=0, size=30):
    return {
        "encontrados": found,
        "baixados": downloaded,
        "existentes": existing,
        "falhas": failures,
        "bytes_concluidos": size,
        "volume": f"{size}.0 B",
        "tempo": "00:01",
        "velocidade_media": "1.0 B/s",
    }


class PanelFake:
    def __init__(self):
        self.updates = []
        self.final_summary = None

    def atualizar(self, **fields):
        self.updates.append(fields)

    def verificar_cancelamento(self):
        return None

    def definir_resumo(self, value):
        self.final_summary = value


class DriverFake:
    current_url = "https://example.invalid"

    def __init__(self):
        self.commands = []

    def execute_cdp_cmd(self, command, parameters):
        self.commands.append((command, parameters))


class BulkDownloadTest(unittest.TestCase):
    def test_course_wrapper_preserves_the_original_user_facing_error(self):
        error = ProcessamentoCursoError(
            "100",
            PermissionError("negado"),
            summary(),
        )
        self.assertIn("Windows negou acesso", mensagem_usuario_para_erro(error))

    def test_processes_every_catalog_course_and_persists_completion(self):
        courses = [
            CourseSummary("100", "Curso Um"),
            CourseSummary("200", "Curso Dois"),
        ]
        panel = PanelFake()
        with TemporaryDirectory() as directory, \
            patch.object(
                app,
                "obter_catalogo_cursos_autenticado",
                return_value=courses,
            ), \
            patch.object(
                app,
                "executar_conteudo_curso",
                side_effect=[summary(size=30), summary(size=40)],
            ) as process, \
            patch("sys.stdout"):
            result = app.executar_colecao_integral(
                DriverFake(),
                Mock(),
                panel,
                Path(directory),
            )

            root = Path(directory) / COLLECTION_DIRECTORY_NAME
            state = json.loads((root / COLLECTION_MARKER).read_text("utf-8"))
            self.assertEqual(process.call_count, 2)
            self.assertEqual(result["cursos_total"], 2)
            self.assertEqual(result["bytes_concluidos"], 70)
            self.assertEqual(
                {record["status"] for record in state["cursos"].values()},
                {"completo"},
            )
            self.assertTrue((root / "curso-um-id-100").is_dir())
            self.assertTrue((root / "curso-dois-id-200").is_dir())

    def test_continues_after_one_course_failure_then_reports_incomplete(self):
        courses = [
            CourseSummary("100", "Curso Um"),
            CourseSummary("200", "Curso Dois"),
        ]
        first_summary = summary(failures=1)
        failure = ProcessamentoCursoError(
            "100",
            RuntimeError("material sem link"),
            first_summary,
        )
        panel = PanelFake()
        with TemporaryDirectory() as directory, \
            patch.object(
                app,
                "obter_catalogo_cursos_autenticado",
                return_value=courses,
            ), \
            patch.object(
                app,
                "executar_conteudo_curso",
                side_effect=[failure, summary()],
            ) as process, \
            patch("sys.stdout"):
            with self.assertRaises(ColecaoIncompletaError) as caught:
                app.executar_colecao_integral(
                    DriverFake(),
                    Mock(),
                    panel,
                    Path(directory),
                )

            root = Path(directory) / COLLECTION_DIRECTORY_NAME
            state = json.loads((root / COLLECTION_MARKER).read_text("utf-8"))
            self.assertEqual(process.call_count, 2)
            self.assertEqual(state["cursos"]["100"]["status"], "incompleto")
            self.assertEqual(state["cursos"]["200"]["status"], "completo")
            self.assertEqual(caught.exception.resumo["cursos_incompletos"], 1)

    def test_filter_and_spillover_only_process_the_selected_course(self):
        courses = [
            CourseSummary("100", "BACEN - Curso"),
            CourseSummary("200", "TCDF - Curso"),
        ]
        panel = PanelFake()
        with TemporaryDirectory() as directory, \
            patch.object(
                app,
                "obter_catalogo_cursos_autenticado",
                return_value=courses,
            ), \
            patch.object(
                app,
                "executar_conteudo_curso",
                return_value=summary(),
            ) as process, \
            patch.object(
                app,
                "verificar_destino",
                side_effect=lambda path: 1000 if str(path).endswith("-e") else 10,
            ), \
            patch("sys.stdout"):
            base = Path(directory)
            extra = base / "estrategia-cursos-completos-e"
            result = app.executar_colecao_integral(
                DriverFake(),
                Mock(),
                panel,
                base / "principal",
                pastas_extras=(extra,),
                selecionar_curso=lambda course: course.name.startswith("BACEN"),
            )

            state = json.loads((extra / COLLECTION_MARKER).read_text("utf-8"))
            self.assertEqual(process.call_count, 1)
            self.assertEqual(process.call_args.args[3], "100")
            self.assertEqual(result["cursos_total"], 1)
            self.assertEqual(set(state["cursos"]), {"100"})

    def test_course_state_is_incomplete_when_discovery_fails_before_downloads(self):
        panel = PanelFake()
        with TemporaryDirectory() as directory, \
            patch.object(app, "listar_aulas", side_effect=RuntimeError("sem aulas")), \
            patch("sys.stdout"):
            destination = Path(directory) / "curso-id-100"
            destination.mkdir()
            with self.assertRaises(ProcessamentoCursoError):
                app.executar_conteudo_curso(
                    DriverFake(),
                    Mock(),
                    panel,
                    "100",
                    "Curso",
                    destination,
                    modo_reduzido=False,
                    auditar_existentes=True,
                )

            state = json.loads((destination / ARQUIVO_ESTADO).read_text("utf-8"))
            self.assertEqual(state["status"], "incompleto")
            self.assertEqual(state["curso_id"], "100")


if __name__ == "__main__":
    unittest.main()
