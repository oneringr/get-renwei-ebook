from __future__ import annotations

import argparse
import json
import math
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fitz
from fontTools.ttLib import TTFont
from pypdf import PdfReader
from pypdf.generic import ArrayObject, ContentStream


FONT_EXTS = {".ttf", ".ttc", ".otf"}
PREFERRED_OVERLAY_FONTS = [
    "SimSun",
    "SourceHanSansCN-Regular",
    "SourceHanSansCN-Normal",
    "Source Han Sans CN",
    "Microsoft YaHei",
    "MicrosoftYaHei",
]
MANUAL_ALIASES = {
    "FZZDXK--GBK1-0": "FZZDXJW--GB1-0",
    "FZLTDHJW--GB1-0": "FZLTXHJW--GB1-0",
    "FZLTZHJW--GB1-0": "FZLTXHJW--GB1-0",
    "FZSSK--GBK1-0": "FZHTK--GBK1-0",
    "FZSSJW--GB1-0": "FZLTXHJW--GB1-0",
    "FZBYFKSJW--GB1-0": "FZLTXHJW--GB1-0",
    "FZLTXIHK--GBK1-0": "FZLTXHK--GBK1-0",
    "Helvetica-Bold": "Arial Bold",
    "Arial-Black": "Arial-Black",
    "ArialMT": "ArialMT",
    "CenturyGothic": "CenturyGothic",
    "TimesNewRomanPSMT": "TimesNewRomanPSMT",
    "SimSun": "SimSun",
    "AdobeSongStd-Light": "AdobeSongStd-Light",
}
WESTERN_FONTS = {
    "Helvetica-Bold",
    "Arial-Black",
    "ArialMT",
    "CenturyGothic",
    "TimesNewRomanPSMT",
}
GARBLED_RANGES = (
    (0x0370, 0x03FF),  # Greek
    (0x0400, 0x04FF),  # Cyrillic
    (0x0900, 0x109F),  # Indic + Myanmar blocks often seen in this corruption
    (0x1780, 0x17FF),  # Khmer
)
FONT_CACHE: dict[str, fitz.Font] = {}


@dataclass
class FontFace:
    path: str
    font_number: int
    names: set[str]
    glyph_order: list[str]
    glyph_to_unicode: dict[str, int]


@dataclass
class FontMeta:
    resource_name: str
    basefont: str
    encoding: str
    subtype: str
    family: str | None
    ordering: str | None
    face: FontFace | None
    resolution_status: str
    resolved_name: str | None


@dataclass
class TextItem:
    page: int
    x: float
    y: float
    font_size: float
    text: str
    source: str
    vertical: bool = False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Repair an image-backed PDF by rebuilding a clean hidden text layer without OCR."
    )
    parser.add_argument("input_pdf", type=Path)
    parser.add_argument("font_dir", type=Path)
    parser.add_argument("output_pdf", type=Path)
    parser.add_argument(
        "--overrides",
        type=Path,
        default=None,
        help="Optional JSON file with page-specific text overrides. Format: {page: [{bbox,text,...}, ...]}",
    )
    return parser.parse_args()


def build_font_index(*roots: Path) -> tuple[dict[str, FontFace], dict[str, str]]:
    index: dict[str, FontFace] = {}
    path_by_name: dict[str, str] = {}
    for root in roots:
        if not root.exists():
            continue
        for path in root.iterdir():
            if not path.is_file() or path.suffix.lower() not in FONT_EXTS:
                continue
            max_faces = 8 if path.suffix.lower() == ".ttc" else 1
            for font_number in range(max_faces):
                try:
                    font = TTFont(str(path), fontNumber=font_number)
                except Exception:
                    break
                names: set[str] = set()
                for record in font["name"].names:
                    if record.nameID not in (1, 4, 6):
                        continue
                    try:
                        names.add(str(record.toUnicode()))
                    except Exception:
                        continue
                if not names:
                    continue
                glyph_to_unicode: dict[str, int] = {}
                for codepoint, glyph_name in (font.getBestCmap() or {}).items():
                    glyph_to_unicode.setdefault(glyph_name, codepoint)
                face = FontFace(
                    path=str(path),
                    font_number=font_number,
                    names=names,
                    glyph_order=font.getGlyphOrder(),
                    glyph_to_unicode=glyph_to_unicode,
                )
                for name in names:
                    index.setdefault(name, face)
                    path_by_name.setdefault(name, str(path))
    return index, path_by_name


def find_overlay_font(font_paths: dict[str, str]) -> str:
    for name in PREFERRED_OVERLAY_FONTS:
        if name in font_paths:
            return font_paths[name]
    for fallback in ("simsun.ttc", "SourceHanSansCN-Regular.otf", "arial.ttf"):
        for path in font_paths.values():
            if Path(path).name.lower() == fallback.lower():
                return path
    raise RuntimeError("No usable overlay font found in the provided font directory or system fonts.")


def load_overrides(path: Path | None) -> dict[str, list[dict[str, Any]]]:
    if path is None:
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_face(
    basefont: str,
    family: str | None,
    font_index: dict[str, FontFace],
) -> tuple[FontFace | None, str, str | None]:
    if basefont in font_index:
        return font_index[basefont], "exact", basefont
    alias = MANUAL_ALIASES.get(basefont)
    if alias and alias in font_index:
        return font_index[alias], "alias", alias
    if family and family in font_index:
        return font_index[family], "family", family
    if basefont.endswith("--GB1-0"):
        for fallback in (
            "FZLTXHJW--GB1-0",
            "FZLTHJW--GB1-0",
            "FZHTJW--GB1-0",
            "FZZZHONGJW--GB1-0",
            "FZZZHUNHJW--GB1-0",
        ):
            if fallback in font_index:
                return font_index[fallback], "fallback-gb1", fallback
    if basefont.endswith("--GBK1-0"):
        for fallback in (
            "FZHTK--GBK1-0",
            "FZLTZHK--GBK1-0",
            "FZLTXHK--GBK1-0",
            "SimSun",
        ):
            if fallback in font_index:
                return font_index[fallback], "fallback-gbk", fallback
    return None, "missing", None


def extract_font_meta(page: Any, font_index: dict[str, FontFace]) -> dict[str, FontMeta]:
    resources = page.get("/Resources")
    if resources is None or "/Font" not in resources:
        return {}
    fonts = resources["/Font"]
    out: dict[str, FontMeta] = {}
    for name, ref in fonts.items():
        obj = ref.get_object()
        basefont = str(obj.get("/BaseFont", "")).lstrip("/").split("+", 1)[-1]
        subtype = str(obj.get("/Subtype", "")).lstrip("/")
        encoding = str(obj.get("/Encoding", "")).lstrip("/")
        family = None
        ordering = None
        descriptor = obj.get("/FontDescriptor")
        descendant = None
        if "/DescendantFonts" in obj:
            descendant_fonts = obj.get("/DescendantFonts")
            if isinstance(descendant_fonts, ArrayObject) and len(descendant_fonts) > 0:
                descendant = descendant_fonts[0]
            else:
                try:
                    resolved = descendant_fonts.get_object()
                    if isinstance(resolved, ArrayObject) and len(resolved) > 0:
                        descendant = resolved[0]
                except Exception:
                    descendant = None
            if not encoding:
                encoding = str(obj.get("/Encoding", "")).lstrip("/")
            if descendant is not None:
                cid_info = descendant.get("/CIDSystemInfo")
                if cid_info:
                    ordering = str(cid_info.get("/Ordering", ""))
                descriptor = descendant.get("/FontDescriptor", descriptor)
        if descriptor and descriptor.get("/FontFamily"):
            family = str(descriptor.get("/FontFamily"))
        face, status, resolved_name = resolve_face(basefont, family, font_index)
        out[str(name)] = FontMeta(
            resource_name=str(name),
            basefont=basefont,
            encoding=encoding,
            subtype=subtype,
            family=family,
            ordering=ordering,
            face=face,
            resolution_status=status,
            resolved_name=resolved_name,
        )
    return out


def operand_bytes(obj: Any) -> bytes | None:
    if hasattr(obj, "original_bytes"):
        return obj.original_bytes
    try:
        return bytes(obj)
    except Exception:
        return None


def looks_garbled(text: str) -> bool:
    score = 0
    interesting = 0
    for ch in text:
        if ch.isspace() or ch.isascii():
            continue
        interesting += 1
        cp = ord(ch)
        for start, end in GARBLED_RANGES:
            if start <= cp <= end:
                score += 1
                break
    return interesting > 0 and score / interesting > 0.35


def decode_with_glyph_map(raw: bytes, meta: FontMeta) -> str:
    if meta.face is None:
        return ""
    if meta.encoding in {"Identity-H", "Identity-V"}:
        chars: list[str] = []
        for idx in range(0, len(raw), 2):
            chunk = raw[idx : idx + 2]
            if len(chunk) != 2:
                continue
            glyph_id = int.from_bytes(chunk, "big")
            if glyph_id >= len(meta.face.glyph_order):
                chars.append("?")
                continue
            glyph_name = meta.face.glyph_order[glyph_id]
            codepoint = meta.face.glyph_to_unicode.get(glyph_name)
            chars.append(chr(codepoint) if codepoint else "?")
        return "".join(chars)

    chars = []
    for value in raw:
        if meta.basefont in WESTERN_FONTS:
            chars.append(bytes([value]).decode("cp1252", errors="replace"))
            continue
        if meta.face and value < len(meta.face.glyph_order):
            glyph_name = meta.face.glyph_order[value]
            codepoint = meta.face.glyph_to_unicode.get(glyph_name)
            chars.append(chr(codepoint) if codepoint else chr(value))
        else:
            chars.append(chr(value))
    return "".join(chars)


def decode_text(raw: bytes | None, meta: FontMeta) -> str:
    if not raw:
        return ""
    if meta.encoding == "WinAnsiEncoding":
        cp_text = raw.decode("cp1252", errors="replace")
        if meta.basefont in WESTERN_FONTS or not looks_garbled(cp_text):
            return cp_text
    if meta.face is not None:
        mapped = decode_with_glyph_map(raw, meta)
        if mapped and not looks_garbled(mapped):
            return mapped
        if mapped:
            return mapped
    if len(raw) % 2 == 0:
        try:
            utf16 = raw.decode("utf-16-be")
            if utf16:
                return utf16
        except Exception:
            pass
    return raw.decode("latin1", errors="replace")


def merge_tj_array(parts: ArrayObject) -> bytes:
    chunks: list[bytes] = []
    for part in parts:
        if isinstance(part, (int, float)):
            continue
        raw = operand_bytes(part)
        if raw:
            chunks.append(raw)
    return b"".join(chunks)


def parse_text_items(reader: PdfReader, page_number: int, font_index: dict[str, FontFace]) -> list[TextItem]:
    page = reader.pages[page_number - 1]
    page_height = float(page.mediabox.height)
    meta_by_resource = extract_font_meta(page, font_index)
    content = ContentStream(page.get_contents(), reader)
    items: list[TextItem] = []
    current_font = None
    current_tf_size = 10.0
    text_matrix = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]
    line_matrix = text_matrix[:]
    leading = 0.0

    def emit(raw: bytes | None) -> None:
        nonlocal text_matrix
        if current_font is None:
            return
        meta = meta_by_resource.get(current_font)
        if meta is None:
            return
        text = decode_text(raw, meta).replace("\x00", "")
        if not text or not text.strip():
            return
        scale = max(abs(text_matrix[0]), abs(text_matrix[3]), 1.0)
        font_size = max(6.0, current_tf_size * scale)
        y_top = page_height - float(text_matrix[5])
        items.append(
            TextItem(
                page=page_number,
                x=float(text_matrix[4]),
                y=y_top,
                font_size=font_size,
                text=text,
                source="auto",
                vertical=meta.encoding.endswith("-V"),
            )
        )

    for operands, operator in content.operations:
        if operator == b"BT":
            text_matrix = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]
            line_matrix = text_matrix[:]
            leading = 0.0
        elif operator == b"Tf":
            current_font = str(operands[0])
            current_tf_size = float(operands[1])
        elif operator == b"Tm":
            text_matrix = [float(value) for value in operands]
            line_matrix = text_matrix[:]
        elif operator == b"Td":
            line_matrix[4] += float(operands[0])
            line_matrix[5] += float(operands[1])
            text_matrix = line_matrix[:]
        elif operator == b"TD":
            tx = float(operands[0])
            ty = float(operands[1])
            leading = -ty
            line_matrix[4] += tx
            line_matrix[5] += ty
            text_matrix = line_matrix[:]
        elif operator == b"TL":
            leading = float(operands[0])
        elif operator == b"T*":
            line_matrix[5] -= leading
            text_matrix = line_matrix[:]
        elif operator == b"'":
            line_matrix[5] -= leading
            text_matrix = line_matrix[:]
            emit(operand_bytes(operands[0]))
        elif operator == b'"':
            line_matrix[5] -= leading
            text_matrix = line_matrix[:]
            emit(operand_bytes(operands[2]))
        elif operator == b"Tj":
            emit(operand_bytes(operands[0]))
        elif operator == b"TJ":
            emit(merge_tj_array(operands[0]))
    return items


def estimate_width(item: TextItem) -> float:
    return max(item.font_size * 0.6, item.font_size * len(item.text) * 0.55)


def same_line(left: TextItem, right: TextItem) -> bool:
    if left.vertical or right.vertical:
        return False
    return abs(left.y - right.y) <= max(1.8, min(left.font_size, right.font_size) * 0.35)


def clone_items(items: list[TextItem]) -> list[TextItem]:
    return [
        TextItem(
            page=item.page,
            x=item.x,
            y=item.y,
            font_size=item.font_size,
            text=item.text,
            source=item.source,
            vertical=item.vertical,
        )
        for item in items
    ]


def group_items_by_line(items: list[TextItem]) -> list[list[TextItem]]:
    if not items:
        return []
    sortable = sorted(clone_items(items), key=lambda item: (round(item.y, 1), item.x))
    lines: list[list[TextItem]] = [[sortable[0]]]
    for item in sortable[1:]:
        if same_line(lines[-1][-1], item):
            lines[-1].append(item)
        else:
            lines.append([item])
    for line in lines:
        line.sort(key=lambda item: item.x)
    return lines


def is_ascii_run_text(text: str) -> bool:
    return bool(text) and all(ord(ch) < 128 and not ch.isspace() for ch in text)


def preferred_advance(text: str, font_size: float) -> float:
    if not text:
        return max(2.0, font_size * 0.7)
    if is_ascii_run_text(text):
        return max(2.0, font_size * 0.72 * len(text))
    if len(text) == 1:
        ch = text[0]
        if unicodedata.east_asian_width(ch) in {"W", "F"}:
            return max(2.0, font_size)
        if unicodedata.category(ch).startswith("P"):
            return max(2.0, font_size * 0.7)
    return max(2.0, font_size * 0.9 * len(text))


def merge_items(items: list[TextItem]) -> list[TextItem]:
    if not items:
        return []
    sortable = sorted(clone_items(items), key=lambda item: (round(item.y, 1), item.x))
    merged: list[TextItem] = []
    current = sortable[0]
    current_end_x = current.x + estimate_width(current)

    for item in sortable[1:]:
        if same_line(item, current) and item.x >= current.x:
            gap = item.x - current_end_x
            spacer = ""
            if gap > max(item.font_size * 1.2, 8):
                spacer = "    "
            elif gap > max(item.font_size * 0.45, 3):
                spacer = " "
            current.text += spacer + item.text
            current.font_size = max(current.font_size, item.font_size)
            current_end_x = max(current_end_x, item.x + estimate_width(item))
            continue
        merged.append(current)
        current = item
        current_end_x = current.x + estimate_width(current)

    merged.append(current)

    deduped: list[TextItem] = []
    for item in merged:
        if deduped:
            prev = deduped[-1]
            if (
                prev.text == item.text
                and abs(prev.x - item.x) < 0.5
                and abs(prev.y - item.y) < 0.5
            ):
                continue
        deduped.append(item)
    return deduped


def build_precise_items(items: list[TextItem], page_rect: fitz.Rect) -> list[dict[str, Any]]:
    positioned: list[dict[str, Any]] = []
    for line in group_items_by_line(items):
        index = 0
        while index < len(line):
            start = index
            current = line[index]
            index += 1
            if is_ascii_run_text(current.text):
                while index < len(line):
                    next_item = line[index]
                    prev_item = line[index - 1]
                    if not is_ascii_run_text(next_item.text):
                        break
                    if not same_line(prev_item, next_item):
                        break
                    if abs(next_item.font_size - current.font_size) > 0.6:
                        break
                    if next_item.x - prev_item.x > max(prev_item.font_size * 0.95, 8.0):
                        break
                    index += 1
            group = line[start:index]
            first = group[0]
            font_size = max(item.font_size for item in group)
            positioned.append(
                {
                    "point": [first.x, first.y],
                    "text": "".join(item.text for item in group),
                    "fontsize": font_size,
                }
            )
    return positioned


def collect_pdf_fonts(doc: fitz.Document) -> list[str]:
    names: set[str] = set()
    for page in doc:
        for font in page.get_fonts(full=True):
            names.add(font[3].split("+", 1)[-1])
    return sorted(names)


def build_mapping_report(doc: fitz.Document, font_index: dict[str, FontFace]) -> dict[str, Any]:
    report: dict[str, Any] = {}
    for name in collect_pdf_fonts(doc):
        face, status, resolved_name = resolve_face(name, None, font_index)
        entry: dict[str, Any] = {"status": status, "resolved_name": resolved_name, "path": None}
        if face is not None:
            entry["path"] = face.path
        report[name] = entry
    return report


def get_overlay_font(font_path: str) -> fitz.Font:
    font = FONT_CACHE.get(font_path)
    if font is None:
        font = fitz.Font(fontfile=font_path)
        FONT_CACHE[font_path] = font
    return font


def fit_textbox(page: fitz.Page, rect: fitz.Rect, text: str, font_path: str, initial_size: float) -> None:
    rect = fitz.Rect(rect)
    rect.x0 = max(0, min(rect.x0, page.rect.width - 2))
    rect.x1 = max(rect.x0 + 2, min(rect.x1, page.rect.width))
    rect.y0 = max(0, min(rect.y0, page.rect.height - 2))
    rect.y1 = max(rect.y0 + 2, min(rect.y1, page.rect.height))
    if (
        not all(math.isfinite(v) for v in (rect.x0, rect.y0, rect.x1, rect.y1))
        or rect.is_empty
    ):
        page.insert_text(
            fitz.Point(max(0, min(rect.x0, page.rect.width - 2)), max(6, min(rect.y1, page.rect.height - 2))),
            text,
            fontname="overlay",
            fontfile=font_path,
            fontsize=max(4.0, initial_size),
            render_mode=3,
            overlay=True,
        )
        return

    font = get_overlay_font(font_path)
    width = max(2.0, rect.width)
    height = max(2.0, rect.height)
    line_height_factor = max(1.0, font.ascender - font.descender)
    font_size = max(4.0, initial_size)
    font_size = min(font_size, height / line_height_factor)

    text_width = font.text_length(text, fontsize=font_size)
    if text_width > width and text_width > 0:
        font_size = max(4.0, font_size * (width / text_width))
        font_size = min(font_size, height / line_height_factor)

    baseline = rect.y0 + font.ascender * font_size
    max_baseline = rect.y1 + font.descender * font_size
    baseline = min(baseline, max_baseline)
    baseline = max(font_size, min(baseline, page.rect.height - 2))
    page.insert_text(
        fitz.Point(rect.x0, baseline),
        text,
        fontname="overlay",
        fontfile=font_path,
        fontsize=font_size,
        render_mode=3,
        overlay=True,
    )


def build_page_items(
    page_number: int,
    auto_items: list[TextItem],
    overrides: dict[str, list[dict[str, Any]]],
    page_rect: fitz.Rect,
) -> list[dict[str, Any]]:
    if str(page_number) in overrides:
        return overrides[str(page_number)]
    return build_precise_items(auto_items, page_rect)


def rebuild_pdf(
    input_pdf: Path,
    output_pdf: Path,
    page_items: dict[int, list[dict[str, Any]]],
    overlay_font: str,
) -> None:
    src = fitz.open(input_pdf)
    dst = fitz.open()
    metadata = src.metadata or {}
    if metadata:
        dst.set_metadata(metadata)

    for page_number, src_page in enumerate(src, start=1):
        dst_page = dst.new_page(width=src_page.rect.width, height=src_page.rect.height)
        images = src_page.get_images(full=True)
        if len(images) != 1:
            pix = src_page.get_pixmap(alpha=False)
            dst_page.insert_image(dst_page.rect, pixmap=pix)
        else:
            image_xref = images[0][0]
            image_bytes = src.extract_image(image_xref)["image"]
            dst_page.insert_image(dst_page.rect, stream=image_bytes)

        dst_page.insert_font(fontname="overlay", fontfile=overlay_font)
        writer = fitz.TextWriter(dst_page.rect)
        writer_font = get_overlay_font(overlay_font)
        has_point_items = False
        for item in page_items.get(page_number, []):
            text = item["text"]
            if not text.strip():
                continue
            if "bbox" in item:
                rect = fitz.Rect(item["bbox"])
                font_size = item.get("fontsize") or max(6.0, (rect.y1 - rect.y0) * 0.9)
                fit_textbox(dst_page, rect, text, overlay_font, font_size)
            else:
                point = fitz.Point(item["point"])
                font_size = max(4.0, float(item.get("fontsize", 10.0)))
                writer.append(
                    point,
                    text,
                    font=writer_font,
                    fontsize=font_size,
                )
                has_point_items = True
        if has_point_items:
            writer.write_text(dst_page, render_mode=3, overlay=True)

    dst.save(output_pdf, garbage=4, deflate=True)
    dst.close()
    src.close()


def write_json(path: Path, payload: Any) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main() -> None:
    args = parse_args()
    if not args.input_pdf.exists():
        raise SystemExit(f"Input PDF not found: {args.input_pdf}")

    font_index, font_paths = build_font_index(args.font_dir, Path("C:/Windows/Fonts"), Path("."))
    overlay_font = find_overlay_font(font_paths)
    overrides = load_overrides(args.overrides)

    src_doc = fitz.open(args.input_pdf)
    mapping_report = build_mapping_report(src_doc, font_index)
    page_rects = {page_number: page.rect for page_number, page in enumerate(src_doc, start=1)}
    src_doc.close()

    reader = PdfReader(str(args.input_pdf))
    page_items: dict[int, list[dict[str, Any]]] = {}
    page_report: dict[str, Any] = {}
    for page_number in range(1, len(reader.pages) + 1):
        auto_items = parse_text_items(reader, page_number, font_index)
        merged = merge_items(auto_items)
        page_items[page_number] = build_page_items(
            page_number,
            auto_items,
            overrides,
            page_rects[page_number],
        )
        page_report[str(page_number)] = {
            "override": str(page_number) in overrides,
            "auto_items": len(auto_items),
            "merged_items": len(merged),
            "final_items": len(page_items[page_number]),
        }

    rebuild_pdf(args.input_pdf, args.output_pdf, page_items, overlay_font)

    mapping_path = write_json(args.output_pdf.with_suffix(".font_mapping.json"), mapping_report)
    page_report_path = write_json(args.output_pdf.with_suffix(".page_report.json"), page_report)

    print(f"Overlay font: {overlay_font}")
    print(f"Output PDF: {args.output_pdf}")
    print(f"Font mapping report: {mapping_path}")
    print(f"Page report: {page_report_path}")


if __name__ == "__main__":
    main()
