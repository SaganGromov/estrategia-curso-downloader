import json
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import requests

from estrategia_downloader import app
from estrategia_downloader.collection import (
    COLLECTION_DIRECTORY_NAME,
    COLLECTION_MARKER,
)
from estrategia_downloader.course_metadata import CourseSummary
from estrategia_downloader.course_inventory import (
    CourseLesson,
    CourseSnapshot,
    extract_lesson_snapshot,
)
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

    def execute_script(self, _script):
        return "Test User Agent"

    def get_cookies(self):
        return []


class CourseDownloadManagerFake:
    def __init__(self, *_args, **_kwargs):
        self.sessao = requests.Session()
        self.urls_processadas = set()
        self.urls_concluidas = set()
        self.encontrados = 0
        self.baixados = 0
        self.existentes = 0
        self.falhas = 0
        self.falhas_descoberta = []

    def configurar_total_aulas(self, _total):
        return None

    def preparar_aula(self, _number):
        return None

    def iniciar_aula(self, _position):
        return None

    def concluir_aula(self):
        return None

    def baixar(self, item):
        key = app.resource_key(item["url"])
        if key not in self.urls_processadas:
            self.urls_processadas.add(key)
            self.encontrados += 1
            self.baixados += 1
        self.urls_concluidas.add(key)
        return True

    def registrar_falha_descoberta(self, description):
        if description not in self.falhas_descoberta:
            self.falhas_descoberta.append(description)
            self.falhas += 1

    def ocorrencias_pendentes(self):
        return set()

    def resumo(self):
        return None

    def resumo_dados(self):
        return summary(
            found=self.encontrados,
            downloaded=self.baixados,
            existing=self.existentes,
            failures=self.falhas,
            size=self.encontrados,
        )


class BulkDownloadTest(unittest.TestCase):
    def test_detects_only_valid_future_release_notices(self):
        driver = Mock()
        driver.execute_script.return_value = (
            "Disponível em 01/09/2099\n"
            "Disponivel em 08/09/2099\n"
            "Disponível em 31/02/2099\n"
            "Disponível em 01/01/2020"
        )

        releases = app.detectar_liberacoes_futuras(
            driver,
            hoje=date(2099, 8, 26),
        )

        self.assertEqual(releases, [date(2099, 9, 1), date(2099, 9, 8)])

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

    def test_scheduled_course_is_persisted_without_becoming_complete_or_failure(self):
        courses = [CourseSummary("100", "Curso Futuro")]
        scheduled = summary(found=0, downloaded=0, existing=0, size=0)
        scheduled.update(
            {
                "status_curso": "aguardando_liberacao",
                "liberacoes_futuras": ["2099-09-01"],
                "proxima_liberacao": "2099-09-01",
            }
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
                return_value=scheduled,
            ), \
            patch("sys.stdout"):
            result = app.executar_colecao_integral(
                DriverFake(),
                Mock(),
                panel,
                Path(directory),
            )

            root = Path(directory) / COLLECTION_DIRECTORY_NAME
            state = json.loads((root / COLLECTION_MARKER).read_text("utf-8"))
            self.assertEqual(
                state["cursos"]["100"]["status"],
                "aguardando_liberacao",
            )
            self.assertEqual(result["cursos_incompletos"], 0)
            self.assertEqual(result["cursos_aguardando_liberacao"], 1)

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
            patch.object(app, "create_course_api_session", return_value=Mock()), \
            patch.object(
                app,
                "get_course_snapshot",
                side_effect=RuntimeError("sem inventário da API"),
            ), \
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

    def test_future_only_course_gets_a_resumable_scheduled_marker(self):
        panel = PanelFake()
        driver = DriverFake()
        scheduled = CourseSnapshot(
            course_id="100",
            title="Curso Futuro",
            total_lessons=0,
            lessons=(),
            future_release_dates=(date(2099, 9, 1),),
        )
        with TemporaryDirectory() as directory, \
            patch.object(app, "create_course_api_session", return_value=Mock()), \
            patch.object(app, "get_course_snapshot", return_value=scheduled), \
            patch("sys.stdout"):
            destination = Path(directory) / "curso-id-100"
            destination.mkdir()

            result = app.executar_conteudo_curso(
                driver,
                Mock(),
                panel,
                "100",
                "Curso Futuro",
                destination,
                modo_reduzido=False,
                auditar_existentes=True,
            )

            state = json.loads((destination / ARQUIVO_ESTADO).read_text("utf-8"))
            inventory = json.loads(
                (destination / ".inventario_estrategia.json").read_text("utf-8")
            )
            self.assertEqual(result["status_curso"], "aguardando_liberacao")
            self.assertEqual(state["status"], "aguardando_liberacao")
            self.assertEqual(inventory["status"], "aguardando_liberacao")
            self.assertEqual(
                inventory["metadados"]["proxima_liberacao"],
                "2099-09-01",
            )

    def test_executor_uses_one_course_snapshot_and_one_call_per_lesson(self):
        lessons = (
            CourseLesson("20", 1, 0, "Aula 00", "https://site/aulas/20"),
            CourseLesson("21", 2, 1, "Aula 01", "https://site/aulas/21"),
        )
        course = CourseSnapshot("100", "Curso", 2, lessons)
        lesson_snapshots = [
            extract_lesson_snapshot(
                {
                    "data": {
                        "id": int(lesson.lesson_id),
                        "pdf": f"https://cdn.test/{lesson.lesson_id}.pdf",
                        "videos": [],
                    }
                },
                lesson,
            )
            for lesson in lessons
        ]
        panel = PanelFake()
        api_session = Mock()

        with TemporaryDirectory() as directory, \
            patch.object(app, "GerenciadorDownloads", CourseDownloadManagerFake), \
            patch.object(
                app,
                "create_course_api_session",
                return_value=api_session,
            ), \
            patch.object(app, "get_course_snapshot", return_value=course) as get_course, \
            patch.object(
                app,
                "get_lesson_snapshot",
                side_effect=lesson_snapshots,
            ) as get_lesson, \
            patch.object(
                app,
                "listar_aulas_auditadas",
                side_effect=AssertionError("DOM não deve ser consultado"),
            ) as dom_lessons, \
            patch.object(
                app,
                "auditar_e_baixar_aula",
                side_effect=AssertionError("DOM não deve ser auditado"),
            ) as dom_audit, \
            patch("sys.stdout"):
            destination = Path(directory) / "curso-id-100"
            destination.mkdir()

            result = app.executar_conteudo_curso(
                DriverFake(),
                Mock(),
                panel,
                "100",
                "Curso",
                destination,
                modo_reduzido=False,
                auditar_existentes=True,
            )

            inventory = json.loads(
                (destination / ".inventario_estrategia.json").read_text("utf-8")
            )
            self.assertEqual(get_course.call_count, 1)
            self.assertEqual(get_lesson.call_count, 2)
            self.assertEqual(
                [call.args[1].lesson_id for call in get_lesson.call_args_list],
                ["20", "21"],
            )
            dom_lessons.assert_not_called()
            dom_audit.assert_not_called()
            self.assertEqual(result["encontrados"], 2)
            self.assertEqual(
                {record["modo"] for record in inventory["aulas"].values()},
                {"api"},
            )
            self.assertEqual(
                {record["passagens"] for record in inventory["aulas"].values()},
                {1},
            )


if __name__ == "__main__":
    unittest.main()
