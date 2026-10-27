import json
from pathlib import Path

from app.resume import extract_pdf_text, parse_resume

text = Path(r"F:\software_dev\Job searcher\data\_resume_dump.txt").read_text(encoding="utf-8")
parsed = parse_resume(text)
print("NAME", parsed["name"])
print("CONTACT", parsed["contact"])
print("TITLES", parsed["titles"])
print("SKILLS", json.dumps(parsed["skills"], ensure_ascii=False))
print("CITY", parsed["city"], "MODE", parsed["work_mode"])

simple = """
Nawroze Test
nawroze.test@example.com

Skills
Python, SQL, Excel

Experience
Software Engineer
Example Studio
2022 - 2024
"""
print("SIMPLE", parse_resume(simple))

joined = "Nawroze Test nawroze.test@example.com Skills Python, SQL, Excel Experience Software Engineer 2022 - 2024"
print("JOINED", parse_resume(joined))


def pdf_bytes(lines):
    # Each item is (x, y, text). y is PDF coordinates (larger is higher).
    commands = ["BT", "/F1 12 Tf"]
    for x, y, value in lines:
        safe = value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        commands.append(f"1 0 0 1 {x} {y} Tm ({safe}) Tj")
    commands.append("ET")
    stream = "\n".join(commands).encode()
    objects = []
    objects.append(b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n")
    objects.append(b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n")
    objects.append(
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >> endobj\n"
    )
    objects.append(b"4 0 obj << /Length " + str(len(stream)).encode() + b" >> stream\n" + stream + b"\nendstream\nendobj\n")
    objects.append(b"5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n")
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objects:
        offsets.append(len(out))
        out.extend(obj)
    xref = len(out)
    out.extend(f"xref\n0 {len(offsets)}\n".encode())
    out.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        out.extend(f"{offset:010d} 00000 n \n".encode())
    out.extend(f"trailer << /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode())
    return bytes(out)


ordered = pdf_bytes([
    (72, 740, "Nawroze Test"),
    (72, 720, "nawroze.test@example.com"),
    (72, 680, "Skills"),
    (72, 660, "Python, SQL, Excel"),
    (72, 620, "Experience"),
    (72, 600, "Software Engineer"),
    (72, 580, "2022 - 2024"),
])
# Name is drawn last in the stream but sits at the top of the page.
reordered = pdf_bytes([
    (72, 680, "Skills"),
    (72, 660, "Python, SQL, Excel"),
    (72, 620, "Experience"),
    (72, 600, "Software Engineer"),
    (72, 580, "2022 - 2024"),
    (72, 740, "Nawroze Test"),
    (72, 720, "nawroze.test@example.com"),
])
blank = pdf_bytes([])
Path(r"F:\software_dev\Job searcher\data\_sample_resume.pdf").write_bytes(ordered)
print("ORDERED TEXT:")
print(extract_pdf_text(ordered))
print("ORDERED PARSE", parse_resume(extract_pdf_text(ordered)))
print("REORDERED TEXT:")
print(extract_pdf_text(reordered))
print("REORDERED PARSE", parse_resume(extract_pdf_text(reordered)))
print("BLANK", repr(extract_pdf_text(blank)))
