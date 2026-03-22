from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    from pypdf import PdfReader, PdfWriter
except Exception as exc:  # pragma: no cover - startup guard
    print(f"Missing dependency: {exc}", file=sys.stderr)
    print("Install with: python -m pip install pypdf", file=sys.stderr)
    raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Merge one or more PDF files into a single PDF.')
    parser.add_argument('--output-pdf', required=True)
    parser.add_argument('--output-metadata', required=True)
    parser.add_argument('--password', default='')
    parser.add_argument('inputs', nargs='+')
    return parser.parse_args()


def ensure_reader(path: Path, password: str) -> tuple[PdfReader, bool]:
    reader = PdfReader(str(path))
    was_encrypted = bool(reader.is_encrypted)
    if was_encrypted:
        if not password:
            raise RuntimeError(f'PDF password required for {path.name}')
        result = reader.decrypt(password)
        if result == 0:
            raise RuntimeError(f'Incorrect PDF password for {path.name}')
    return reader, was_encrypted


def merge_pdfs(input_paths: list[Path], output_pdf: Path, password: str) -> tuple[int, list[dict[str, int | str | bool]], list[dict[str, int | str | bool]]]:
    writer = PdfWriter()
    page_sources: list[dict[str, int | str | bool]] = []
    input_details: list[dict[str, int | str | bool]] = []
    total_pages = 0

    for segment_number, input_path in enumerate(input_paths, start=1):
        print(f'[{segment_number}/{len(input_paths)}] Reading {input_path.name}', flush=True)
        reader, was_encrypted = ensure_reader(input_path, password)
        page_count = len(reader.pages)

        input_details.append(
            {
                'segment': segment_number,
                'file': input_path.name,
                'path': str(input_path),
                'page_count': page_count,
                'encrypted': was_encrypted,
            }
        )

        for page_index, page in enumerate(reader.pages, start=1):
            writer.add_page(page)
            total_pages += 1
            page_sources.append(
                {
                    'segment': segment_number,
                    'segment_file': input_path.name,
                    'segment_page': page_index,
                    'merged_page': total_pages,
                }
            )

    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    with output_pdf.open('wb') as handle:
        writer.write(handle)

    return total_pages, page_sources, input_details


def main() -> None:
    started_at = time.time()
    args = parse_args()

    input_paths = [Path(value).resolve() for value in args.inputs]
    output_pdf = Path(args.output_pdf).resolve()
    output_metadata = Path(args.output_metadata).resolve()

    total_pages, page_sources, input_details = merge_pdfs(input_paths, output_pdf, args.password)

    metadata = {
        'created_at': datetime.now(timezone.utc).isoformat(),
        'output_pdf': str(output_pdf),
        'output_pdf_name': output_pdf.name,
        'password_used': bool(args.password),
        'input_count': len(input_paths),
        'input_files': [str(path) for path in input_paths],
        'input_details': input_details,
        'total_pages': total_pages,
        'page_sources': page_sources,
        'duration_seconds': round(time.time() - started_at, 3),
    }

    output_metadata.parent.mkdir(parents=True, exist_ok=True)
    output_metadata.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')

    print(f'Merged PDF written to: {output_pdf}', flush=True)
    print(f'Processing metadata written to: {output_metadata}', flush=True)


if __name__ == '__main__':
    main()
