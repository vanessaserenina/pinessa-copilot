from contextlib import closing
from io import BytesIO

import pypdfium2 as pdfium
from PIL import Image, ImageDraw, ImageFont
import pytest

from app.modules.orders.source_text_worker import convert_file


def order_image():
    image = Image.new("RGB", (780, 160), "white")
    draw = ImageDraw.Draw(image)
    draw.text((35, 40), "ORDER 12345", fill="black", font=ImageFont.load_default(size=58))
    return image


@pytest.mark.parametrize("size,positions,lines", [
    ((1250, 240), [(30, 30), (30, 81)],
     ["Customer: TEST Larch Hotel. PO TEST-NEW-3.", "6 cases of blue clips, SKU CLIP-B."]),
    ((950, 600), [(90, 65), (90, 135), (90, 320)],
     ["Customer: Oak Hotel", "PO ACORN-42", "4 cases of red clips, SKU CLIP-R."]),
    ((1100, 420), [(60, 30), (150, 135), (150, 235)],
     ["Order 1248", "3 cases of blue clips", "Blue clips, SKU CLIP-B."]),
])
def test_printed_scan_preserves_header_words_and_lines(tmp_path, size, positions, lines):
    page = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(page)
    for position, line in zip(positions, lines, strict=True):
        draw.text(position, line, fill="black", font=ImageFont.load_default(size=32))
    path = tmp_path / "printed.pdf"
    page.save(path, format="PDF", resolution=144)
    assert convert_file("pdf", path).splitlines() == lines


def text_page():
    # A single-page PDF with a native Helvetica text layer.
    content = b"BT /F1 12 Tf 36 110 Td (Native SKU 4321) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 150] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    pdf = BytesIO(b"%PDF-1.4\n")
    pdf.seek(0, 2)
    offsets = []
    for i, obj in enumerate(objects, 1):
        offsets.append(pdf.tell())
        pdf.write(f"{i} 0 obj\n".encode() + obj + b"\nendobj\n")
    start = pdf.tell()
    pdf.write(b"xref\n0 6\n0000000000 65535 f \n")
    for offset in offsets:
        pdf.write(f"{offset:010d} 00000 n \n".encode())
    pdf.write(f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode())
    return pdf.getvalue()


def test_pdf_text_layer_bypasses_ocr(tmp_path, monkeypatch):
    path = tmp_path / "order"
    path.write_bytes(text_page())

    def reject_ocr(image):
        raise AssertionError("Native text should be extracted directly")

    monkeypatch.setattr("app.modules.orders.source_text_worker.image_text", reject_ocr)
    assert convert_file("pdf", path) == "Native SKU 4321"


def test_native_and_scanned_pages_in_order(tmp_path):
    path = tmp_path / "order"
    scan = BytesIO()
    order_image().save(scan, format="PDF")
    with closing(pdfium.PdfDocument(text_page())) as document:
        with closing(pdfium.PdfDocument(scan.getvalue())) as scanned:
            document.import_pages(scanned)
            document.save(path)
    native, ocr = convert_file("pdf", path).split("\n\n")
    assert native == "Native SKU 4321"
    assert "ORDER12345" in ocr.replace(" ", "")


def test_native_and_scanned_text_on_same_page(tmp_path):
    path = tmp_path / "mixed.pdf"
    with closing(pdfium.PdfDocument(text_page())) as document, closing(document[0]) as page:
        image = pdfium.PdfImage.new(document)
        with closing(pdfium.PdfBitmap.from_pil(order_image())) as bitmap:
            image.set_bitmap(bitmap)
            image.set_matrix(pdfium.PdfMatrix(300, 0, 0, 60, 10, 10))
            page.insert_obj(image)
            page.gen_content()
            document.save(path)
    text = convert_file("pdf", path)
    assert text.splitlines()[0] == "Native SKU 4321"
    assert text.replace(" ", "").splitlines()[1:] == ["ORDER12345"]


def test_blank_pages_preserve_readable_text(tmp_path):
    path = tmp_path / "with-blanks.pdf"
    white_scan = BytesIO()
    Image.new("RGB", (400, 150), "white").save(white_scan, format="PDF")
    with closing(pdfium.PdfDocument(text_page())) as document:
        document.new_page(400, 150, index=0).close()
        with closing(pdfium.PdfDocument(white_scan.getvalue())) as scanned:
            document.import_pages(scanned)
            document.save(path)
    assert convert_file("pdf", path) == "Native SKU 4321"
