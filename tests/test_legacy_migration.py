import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from estrategia_downloader.collection import (
    COLLECTION_DIRECTORY_NAME,
    COLLECTION_MARKER,
)
from estrategia_downloader.resume import ARQUIVO_ESTADO
from tools.migrate_legacy_downloads import (
    MigrationError,
    _copy_and_verify,
    apply_migration,
    discover_legacy_downloads,
    load_catalog,
)


class LegacyMigrationTest(unittest.TestCase):
    def test_existing_different_file_is_never_overwritten(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            destination = root / "destination.mp4"
            source.write_bytes(b"arquivo legado")
            destination.write_bytes(b"destino")

            with self.assertRaises(MigrationError):
                _copy_and_verify(source, destination)

            self.assertEqual(destination.read_bytes(), b"destino")

    def _catalog(self, root: Path) -> Path:
        path = root / "catalog.tsv"
        path.write_text(
            "100\tDireito Administrativo — Curso Completo\n"
            "200\tEstatística e Finanças\n",
            encoding="utf-8",
        )
        return path

    def test_structured_copy_is_hash_verified_and_source_is_kept(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source_root = root / "old"
            source_root.mkdir()
            source = source_root / "CURSO_ESTRATEGIA_100_1234"
            (source / "aula_00" / "videos").mkdir(parents=True)
            content = b"video-exato"
            (source / "aula_00" / "videos" / "video.mp4").write_bytes(content)
            (source / "links_estrategia_conteudo.txt").write_text(
                "aula;tipo;numero;titulo;url\n"
                "00;video;01;Aula;https://cdn.invalid/v?signature=segredo\n",
                encoding="utf-8",
            )
            catalog = load_catalog(self._catalog(root))
            items = discover_legacy_downloads([source_root], catalog)

            apply_migration(items, root / "destination")

            renamed = source_root / (
                "direito-administrativo-curso-completo-id-100-1234"
            )
            canonical = (
                root
                / "destination"
                / COLLECTION_DIRECTORY_NAME
                / "direito-administrativo-curso-completo-id-100"
            )
            self.assertTrue(renamed.is_dir())
            self.assertEqual(
                (canonical / "aula_00" / "videos" / "video.mp4").read_bytes(),
                content,
            )
            for folder in (renamed, canonical):
                manifest = (folder / "links_estrategia_conteudo.txt").read_text(
                    encoding="utf-8"
                )
                self.assertNotIn("cdn.invalid", manifest)
                self.assertNotIn("segredo", manifest)
            state = json.loads(
                (
                    root
                    / "destination"
                    / COLLECTION_DIRECTORY_NAME
                    / COLLECTION_MARKER
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(state["cursos"]["100"]["status"], "incompleto")
            course_state = json.loads(
                (canonical / ARQUIVO_ESTADO).read_text(encoding="utf-8")
            )
            self.assertEqual(course_state["status"], "incompleto")
            self.assertEqual(
                course_state["resumo"]["arquivos_sha256_verificados"],
                2,
            )

    def test_flat_pdf_copy_is_named_partial_and_not_imported(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source_root = root / "old"
            source_root.mkdir()
            source = source_root / "CURSO_ESTRATEGIA_200_SEM_MAPAS_MENTAIS_PDFS"
            source.mkdir()
            (source / "apostila.pdf").write_bytes(b"pdf")
            catalog = load_catalog(self._catalog(root))
            items = discover_legacy_downloads([source_root], catalog)

            self.assertFalse(items[0].structured)
            apply_migration(items, root / "destination")

            self.assertTrue(
                (
                    source_root
                    / "estatistica-e-financas-id-200-pdfs-legado-sem-mapas-mentais-pdfs"
                ).is_dir()
            )
            collection = root / "destination" / COLLECTION_DIRECTORY_NAME
            state = json.loads((collection / COLLECTION_MARKER).read_text("utf-8"))
            self.assertEqual(state["cursos"], {})


if __name__ == "__main__":
    unittest.main()
