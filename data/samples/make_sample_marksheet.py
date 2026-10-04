"""Create a SAMPLE marksheet PDF with made-up data, for demos and tests.

Usage: python data/samples/make_sample_marksheet.py   (needs `pip install reportlab`)
"""

from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

OUT = Path(__file__).with_name("sample_ssc_marksheet.pdf")

DETAILS = [
    ("Name of Candidate", "AARIZ HUSSAIN MERCHANT"),
    ("Mother's Name", "ZAINAB"),
    ("Seat No", "M123456"),
    ("Examination", "Secondary School Certificate (SSC)"),
    ("Board", "Maharashtra State Board of Secondary and Higher Secondary Education"),
    ("Month & Year", "March 2026"),
    ("School", "Sample High School, Mumbai"),
]
SUBJECTS = [
    ("English", 84),
    ("Hindi", 78),
    ("Marathi", 75),
    ("Mathematics", 92),
    ("Science and Technology", 88),
    ("Social Sciences", 81),
]


def main() -> None:
    c = canvas.Canvas(str(OUT), pagesize=A4)
    width, height = A4
    y = height - 60

    c.setFont("Helvetica-Bold", 14)
    c.drawCentredString(width / 2, y, "STATEMENT OF MARKS - SAMPLE, NOT A REAL DOCUMENT")
    y -= 40

    c.setFont("Helvetica", 11)
    for label, value in DETAILS:
        c.drawString(60, y, f"{label} : {value}")
        y -= 20

    y -= 15
    c.setFont("Helvetica-Bold", 11)
    c.drawString(60, y, "Subject")
    c.drawString(380, y, "Marks Obtained / Max")
    y -= 20
    c.setFont("Helvetica", 11)
    for name, marks in SUBJECTS:
        c.drawString(60, y, name)
        c.drawString(380, y, f"{marks:03d} / 100")
        y -= 18

    total = sum(m for _, m in SUBJECTS)
    y -= 15
    c.setFont("Helvetica-Bold", 11)
    c.drawString(60, y, f"Total Marks : {total} / {len(SUBJECTS) * 100}")
    y -= 20
    c.drawString(60, y, f"Percentage : {100 * total / (len(SUBJECTS) * 100):.2f}%")
    y -= 20
    c.drawString(60, y, "Result : PASS WITH DISTINCTION")
    c.save()
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
