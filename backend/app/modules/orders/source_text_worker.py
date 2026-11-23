"""Convert one stored file to UTF-8 text in a short-lived, time-limited worker."""
import csv
import sys
from contextlib import closing, redirect_stdout
from functools import cache
from io import StringIO
from math import ceil
from pathlib import Path
from zipfile import ZipFile

MAX_CHARS = 2_000_000
MAX_PIXELS = 20_000_000


@cache
def ocr_engine():
    from rapidocr_onnxruntime import RapidOCR

    return RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1)


def image_text(image):
    from PIL import ImageOps

    if image.width * image.height > MAX_PIXELS:
        raise ValueError("Image too large")
    lines, _ = ocr_engine()(ImageOps.exif_transpose(image).convert("RGB"))
    if not lines:
        raise ValueError("No readable text")
    return "\n".join(line[1] for line in lines)


def pdf_page_text(page, layer):
    import pypdfium2.raw as pdfium_c
    from PIL import ImageDraw

    native = layer.get_text_bounded()
    if native.strip() and next(page.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_IMAGE]), None) is None:
        return native

    width, height = page.get_size()
    if ceil(width * 2) * ceil(height * 2) > MAX_PIXELS:
        raise ValueError("Page too large")
    with closing(page.render(scale=2)) as bitmap:
        image = bitmap.to_pil().convert("RGB")
        if native.strip():
            # Mask text already captured by PDFium so OCR doesn't duplicate native lines.
            coords = bitmap.get_posconv(page)
            draw = ImageDraw.Draw(image)
            for index in range(layer.count_rects()):
                left, bottom, right, top = layer.get_rect(index)
                x1, y1 = coords.to_bitmap(left, bottom)
                x2, y2 = coords.to_bitmap(right, top)
                draw.rectangle((min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)), fill="white")
        if image.getextrema() == ((255, 255),) * 3:
            return native
        scanned = image_text(image)
    return f"{native}\n{scanned}" if native.strip() else scanned


def pdf_text(path):
    import pypdfium2 as pdfium

    output = StringIO()
    with closing(pdfium.PdfDocument(path)) as document:
        if len(document) > 100:
            raise ValueError("Too many pages")
        for index in range(len(document)):
            with closing(document[index]) as page, closing(page.get_textpage()) as layer:
                if layer.count_chars() > MAX_CHARS - output.tell():
                    raise ValueError("Too much text")
                text = pdf_page_text(page, layer)
                if not text.strip():
                    continue
                if output.tell():
                    output.write("\n\n")
                output.write(text)
                if output.tell() > MAX_CHARS:
                    raise ValueError("Too much text")
    return output.getvalue()


def write_rows(output, rows):
    writer = csv.writer(output, delimiter="\t", lineterminator="\n")
    for row in rows:
        if any(cell is not None and str(cell).strip() for cell in row):
            writer.writerow(row)
            if output.tell() > MAX_CHARS:
                raise ValueError("Too much text")


def spreadsheet_text(kind, path):
    output = StringIO()
    if kind == "csv":
        with path.open(encoding="utf-8-sig", newline="") as stream:
            write_rows(output, csv.reader(stream, strict=True))
    else:
        from openpyxl import load_workbook

        with ZipFile(path) as archive:
            if sum(entry.file_size for entry in archive.infolist()) > 64 * 1024 * 1024:
                raise ValueError("Expanded workbook too large")
        with path.open("rb") as stream, closing(load_workbook(stream, read_only=True)) as book:
            for sheet in book.worksheets:
                # Inspect actual cells even when a producer saved stale dimensions.
                sheet.reset_dimensions()
                start = output.tell()
                output.write(f"[{sheet.title}]\n")
                rows_start = output.tell()
                write_rows(output, sheet.values)
                if output.tell() == rows_start:
                    output.seek(start)
                    output.truncate()
    return output.getvalue()


def convert_file(kind, path):
    if kind == "pdf":
        return pdf_text(path)
    if kind in ("xlsx", "csv"):
        return spreadsheet_text(kind, path)
    if kind == "image":
        from PIL import Image

        with Image.open(path) as image:
            return image_text(image)
    raise ValueError("Unsupported source type")


if __name__ == "__main__":
    # Libraries can print diagnostics. Reserve stdout for complete results only.
    with redirect_stdout(sys.stderr):
        result = convert_file(sys.argv[1], Path(sys.argv[2]))
    if not result.strip() or "\x00" in result or len(result) > MAX_CHARS:
        raise ValueError("Unusable text")
    sys.stdout.buffer.write(result.encode("utf-8"))
