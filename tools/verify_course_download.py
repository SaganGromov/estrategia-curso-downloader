#!/usr/bin/env python3
"""Certifica offline um curso já reconciliado com a API."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from estrategia_downloader.local_verification import (  # noqa: E402
    verify_course_folder,
    write_certificate,
)


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Valida manifesto v4, estado, arquivos e estrutura de mídia sem "
            "reabrir o curso nem acessar a rede."
        )
    )
    parser.add_argument("course_folder", type=Path)
    parser.add_argument(
        "--no-hash",
        action="store_true",
        help="não calcula SHA-256 (mais rápido, mas não gera certificado)",
    )
    parser.add_argument(
        "--no-structure",
        action="store_true",
        help="não chama pdfinfo, ffprobe e identify",
    )
    parser.add_argument("--json", action="store_true", dest="as_json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = verify_course_folder(
        args.course_folder,
        calculate_hashes=not args.no_hash,
        verify_structure=not args.no_structure,
    )
    certificate = None
    if report["ok"] and not args.no_hash:
        certificate = write_certificate(args.course_folder, report)

    if args.as_json:
        output = dict(report)
        output.pop("arquivos", None)
        output["certificado"] = str(certificate) if certificate else None
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        print(f"Curso: {report['curso_id'] or '?'}")
        print(
            "Manifesto: "
            f"{report['ocorrencias_localizadas']}/"
            f"{report['ocorrencias_manifesto']} ocorrências localizadas"
        )
        print(
            f"Arquivos: {report['arquivos_fisicos']} físicos; "
            f"{report['extras_legados']} extras legados; "
            f"{report['bytes_logicos']} bytes lógicos"
        )
        if report["problemas"]:
            for issue in report["problemas"]:
                location = f" ({issue['caminho']})" if issue.get("caminho") else ""
                print(
                    f"ERRO [{issue['codigo']}]: {issue['descricao']}{location}",
                    file=sys.stderr,
                )
        elif certificate:
            print(f"Certificado: {certificate}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
