"""Generate three clearly synthetic PDF fixtures; no vendor specifications are asserted."""
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen.canvas import Canvas
from PIL import Image, ImageDraw, ImageFont
from tempfile import TemporaryDirectory

OUT = Path(__file__).resolve().parents[1] / "examples"
OUT.mkdir(exist_ok=True)
SAMPLES = {
    "synthetic_ssd.pdf": ("SYNTHETIC SSD SPECIFICATION", [
        "Vendor: Demo Storage; Model: SYN-SSD-1; FOR SOFTWARE TESTING ONLY.",
        "Capacity: 512 GB", "Interface: NVMe 1.4", "TBW: 300 TB", "DWPD: 0.32"]),
    "synthetic_emmc.pdf": ("SYNTHETIC eMMC SPECIFICATION", [
        "Vendor: Demo Storage; Model: SYN-EMMC-1; FOR SOFTWARE TESTING ONLY.",
        "Capacity: 64 GB", "Interface: eMMC 5.1", "Device Life Time Estimation A: supported",
        "PRE_EOL_INFO: supported"]),
    "synthetic_raw_nand.pdf": ("SYNTHETIC RAW NAND SPECIFICATION", [
        "Vendor: Demo Storage; Model: SYN-NAND-1; FOR SOFTWARE TESTING ONLY.",
        "Capacity: 128 Gb", "Page Size: 16 KB", "P/E Cycle: 3000 cycles",
        "ECC Requirement: 8 bit per 1 KB"]),
}
for filename, (title, lines) in SAMPLES.items():
    c = Canvas(str(OUT / filename), pagesize=A4)
    c.setTitle(title)
    c.setFont("Helvetica-Bold", 17)
    c.drawString(50, 790, title)
    c.setFont("Helvetica", 11)
    for i, line in enumerate(lines):
        c.drawString(50, 740 - i * 28, line)
    c.setFont("Helvetica-Oblique", 10)
    c.drawString(50, 75, "Synthetic fixture. Values are invented and are not vendor claims.")
    c.showPage()
    c.save()
    print(OUT / filename)

# A single-page image-only PDF exercises the OCR route.
image = Image.new("RGB", (1800, 2400), "white")
draw = ImageDraw.Draw(image)
try:
    font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 58)
except OSError:
    font = ImageFont.load_default(size=58)
for i, line in enumerate(("SYNTHETIC SCANNED SSD SPECIFICATION", "Capacity: 256 GB",
                           "TBW: 180 TB", "FOR SOFTWARE TESTING ONLY")):
    draw.text((120, 160 + 140 * i), line, font=font, fill="black")
with TemporaryDirectory() as folder:
    png = Path(folder) / "page.png"
    image.save(png)
    pdf = OUT / "synthetic_scanned_ssd.pdf"
    c = Canvas(str(pdf), pagesize=A4)
    c.drawImage(str(png), 0, 0, width=A4[0], height=A4[1])
    c.showPage()
    c.save()
    print(pdf)
