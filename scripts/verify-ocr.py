"""Run inside the worker: docker compose ... exec -T worker python - < scripts/verify-ocr.py."""

import io

from app.documents import parse_pdf
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

image = Image.new("RGB", (1700, 2200), "white")
draw = ImageDraw.Draw(image)
font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 42)
for y, line in enumerate(
    [
        "FICTIONAL SCANNED APPLICATION",
        "Legal insured name: Fictional OCR Company LLC",
        "Business operations: Office consulting",
    ]
):
    draw.text((70, 150 + y * 100), line, font=font, fill="black")
output = io.BytesIO()
page = canvas.Canvas(output, pagesize=(612, 792))
page.drawImage(ImageReader(image), 0, 0, width=612, height=792)
page.save()
blocks, pages = parse_pdf(output.getvalue())
assert pages == 1 and any("Fictional OCR Company LLC" in block.text for block in blocks)
assert all(block.locator["method"] == "ocr" for block in blocks)
print("Scanned synthetic PDF OCR passed with page/line references.")
