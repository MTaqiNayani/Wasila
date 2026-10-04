"""Read uploaded documents and pull out structured details.

Marksheets are supported first. Text PDFs are read with pypdf. When an OpenAI key is
set, the model structures the text (and reads photos and scanned pages); without a key,
a rule-based parser handles typed marksheets. The member always checks the result
before anything is saved.
"""

import base64
import io
import json
import logging
import os
import re
from dataclasses import dataclass, field

import openai
from dotenv import load_dotenv
from pypdf import PdfReader

load_dotenv()
log = logging.getLogger(__name__)

PDF_TYPES = {".pdf"}
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
TEXT_TYPES = {".txt"}
ALLOWED = PDF_TYPES | set(IMAGE_TYPES) | TEXT_TYPES

# (key, label) in display order.
MARKSHEET_FIELDS = [
    ("student_name", "Student name"),
    ("mother_name", "Mother's name"),
    ("seat_number", "Seat / roll number"),
    ("exam", "Examination"),
    ("board", "Board"),
    ("year", "Month & year"),
    ("school", "School / college"),
    ("total_marks", "Total marks"),
    ("max_marks", "Out of"),
    ("percentage", "Percentage"),
    ("result", "Result"),
]
FIELD_KEYS = [k for k, _ in MARKSHEET_FIELDS]


class ExtractError(Exception):
    pass


@dataclass
class Extraction:
    fields: dict[str, str] = field(default_factory=dict)
    subjects: list[dict] = field(default_factory=list)  # {"name", "marks", "max_marks"}
    raw_text: str = ""
    method: str = ""  # "ai" or "rules"


def extension(filename: str) -> str:
    return os.path.splitext(filename.lower())[1]


def read_text(filename: str, data: bytes) -> str:
    ext = extension(filename)
    if ext in PDF_TYPES:
        try:
            reader = PdfReader(io.BytesIO(data))
            return "\n".join(page.extract_text() or "" for page in reader.pages).strip()
        except Exception as e:  # pypdf raises many types for damaged files
            raise ExtractError("This PDF could not be read. Please upload a different copy.") from e
    if ext in TEXT_TYPES:
        return data.decode("utf-8", errors="replace").strip()
    return ""  # images have no text layer


# ---------- rule-based parser (no API key needed) ----------

# Checked in order; more specific labels first.
_LABELS = [
    ("mother_name", r"mother'?s?\s+name"),
    ("student_name", r"(?:name\s+of\s+(?:the\s+)?(?:candidate|student)|(?:candidate|student)'?s?\s+name|name)"),
    ("seat_number", r"(?:seat|roll|exam(?:ination)?)\s*(?:no\.?|number)"),
    ("board", r"board(?:\s+name)?"),
    ("exam", r"(?:name\s+of\s+(?:the\s+)?)?exam(?:ination)?(?:\s+name)?"),
    ("year", r"(?:month\s*(?:and|&)\s*year(?:\s+of\s+exam(?:ination)?)?|year(?:\s+of\s+passing)?|session)"),
    ("school", r"(?:name\s+of\s+(?:the\s+)?)?(?:school|college|institute)(?:\s+name)?"),
    ("total_marks", r"(?:grand\s+)?total(?:\s+marks)?(?:\s+obtained)?|marks\s+obtained"),
    ("percentage", r"percentage(?:\s+of\s+marks)?"),
    ("result", r"result"),
]
_LINE = {key: re.compile(rf"^\s*(?:{pattern})\s*[:\-–]\s*(.+?)\s*$", re.IGNORECASE) for key, pattern in _LABELS}
_NAME = r"[A-Za-z][A-Za-z .&()/,'-]{1,48}?"
_MARKS = r"(\d{1,3})\s*(?:/|out\s+of)\s*(\d{2,3})"
# "English   084 / 100" on one line ...
_SUBJECT = re.compile(rf"^\s*({_NAME})\s+{_MARKS}\s*$", re.IGNORECASE)
# ... or, as many PDFs come out, the name on one line and "084 / 100" on the next.
_MARKS_ONLY = re.compile(rf"^\s*{_MARKS}\s*$", re.IGNORECASE)
_NAME_ONLY = re.compile(rf"^\s*({_NAME})\s*$")
_NOT_SUBJECT = re.compile(r"total|percentage|grand|result|marks|subject|statement", re.IGNORECASE)


def _number(value: str) -> str:
    match = re.search(r"\d+(?:\.\d+)?", value or "")
    return match.group(0) if match else ""


def parse_marksheet(text: str) -> Extraction:
    fields: dict[str, str] = {}
    subjects: list[dict] = []
    previous = ""  # last line, when it could be a subject name
    for line in text.splitlines():
        name, marks = None, None
        if (m := _SUBJECT.match(line)) and not _NOT_SUBJECT.search(m.group(1)):
            name, marks = m.group(1), m.groups()[1:]
        elif (m := _MARKS_ONLY.match(line)) and previous:
            name, marks = previous, m.groups()
        if name:
            subjects.append({"name": name.strip(), "marks": marks[0].lstrip("0") or "0", "max_marks": marks[1]})
            previous = ""
            continue
        m = _NAME_ONLY.match(line)
        previous = m.group(1) if m and not _NOT_SUBJECT.search(line) else ""
        for key, pattern in _LINE.items():
            if key in fields:
                continue
            match = pattern.match(line)
            if match:
                fields[key] = match.group(1).strip()
                break

    if total := fields.get("total_marks"):
        parts = re.findall(r"\d+(?:\.\d+)?", total)
        if parts:
            fields["total_marks"] = parts[0]
            if len(parts) > 1:
                fields.setdefault("max_marks", parts[1])
    if fields.get("percentage"):
        fields["percentage"] = _number(fields["percentage"])
    return finish(Extraction(fields=fields, subjects=subjects, raw_text=text, method="rules"))


def finish(extraction: Extraction) -> Extraction:
    """Fill totals and percentage from the subjects when the document leaves them out."""
    f, subjects = extraction.fields, extraction.subjects
    try:
        if subjects and not f.get("total_marks"):
            f["total_marks"] = str(sum(int(float(s["marks"])) for s in subjects))
        if subjects and not f.get("max_marks") and all(s.get("max_marks") for s in subjects):
            f["max_marks"] = str(sum(int(float(s["max_marks"])) for s in subjects))
        if not f.get("percentage") and f.get("total_marks") and f.get("max_marks"):
            f["percentage"] = f"{100 * float(f['total_marks']) / float(f['max_marks']):.2f}"
    except (ValueError, ZeroDivisionError):
        pass
    extraction.fields = {k: str(f.get(k) or "").strip() for k in FIELD_KEYS}
    return extraction


# ---------- AI extraction (OpenAI) ----------

_NULLABLE = {"type": ["string", "null"]}
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": FIELD_KEYS + ["subjects"],
    "properties": {
        **{k: _NULLABLE for k in FIELD_KEYS},
        "subjects": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "marks", "max_marks"],
                "properties": {"name": {"type": "string"}, "marks": _NULLABLE, "max_marks": _NULLABLE},
            },
        },
    },
}

EXTRACT_PROMPT = """You read Indian school and board marksheets (SSC, HSC, CBSE, ICSE and similar) and \
copy their details exactly as printed. Use null for anything that is not on the document; never guess. \
Give marks and totals as plain numbers, the percentage without the % sign, and the month and year as \
printed (for example "March 2026")."""


def ai_available() -> bool:
    return bool(os.getenv("OPENAI_API_KEY"))


def ai_extract(text: str = "", image: tuple[str, bytes] | None = None, llm=None) -> Extraction:
    content = []
    if text:
        content.append({"type": "input_text", "text": f"Marksheet text:\n\n{text}"})
    if image:
        media_type, data = image
        url = f"data:{media_type};base64,{base64.b64encode(data).decode()}"
        content.append({"type": "input_image", "image_url": url})
        content.append({"type": "input_text", "text": "Read the marksheet in this image."})

    llm = llm or openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY") or None)
    response = llm.responses.create(
        model=os.getenv("OPENAI_MODEL") or "gpt-5.4-mini",
        instructions=EXTRACT_PROMPT,
        input=[{"role": "user", "content": content}],
        text={"format": {"type": "json_schema", "name": "marksheet", "schema": SCHEMA, "strict": True}},
        store=False,
    )
    data = json.loads(response.output_text)
    subjects = [
        {"name": s["name"], "marks": _number(s.get("marks") or ""), "max_marks": _number(s.get("max_marks") or "")}
        for s in data.get("subjects", [])
        if s.get("name")
    ]
    fields = {k: data.get(k) or "" for k in FIELD_KEYS}
    for k in ("total_marks", "max_marks", "percentage"):
        fields[k] = _number(fields[k])
    return finish(Extraction(fields=fields, subjects=subjects, raw_text=text, method="ai"))


# ---------- entry point ----------


def extract_marksheet(filename: str, data: bytes, llm=None) -> Extraction:
    ext = extension(filename)
    if ext not in ALLOWED:
        raise ExtractError("Please upload a PDF, a photo (JPG or PNG) or a text file.")

    text = read_text(filename, data)
    image = (IMAGE_TYPES[ext], data) if ext in IMAGE_TYPES else None

    if llm is not None or ai_available():
        try:
            return ai_extract(text=text, image=image, llm=llm)
        except (openai.OpenAIError, ValueError, KeyError) as e:
            log.warning("AI extraction failed, falling back to rules: %s", e)

    if not text:
        raise ExtractError(
            "No text could be read from this file. Photos and scanned pages need the AI reader "
            "(set OPENAI_API_KEY); for now, please upload a typed PDF."
        )
    return parse_marksheet(text)
