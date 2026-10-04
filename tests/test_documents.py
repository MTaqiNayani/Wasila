import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from qdrant_client import QdrantClient

import app as wasila
from core import extract, profile, rag

SAMPLE_PDF = Path(__file__).resolve().parent.parent / "data" / "samples" / "sample_ssc_marksheet.pdf"


# ---------- extraction ----------


def test_rules_read_sample_marksheet(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = extract.extract_marksheet("marksheet.pdf", SAMPLE_PDF.read_bytes())
    assert result.method == "rules"
    assert result.fields["student_name"] == "AARIZ HUSSAIN MERCHANT"
    assert result.fields["seat_number"] == "M123456"
    assert result.fields["year"] == "March 2026"
    assert (result.fields["total_marks"], result.fields["max_marks"], result.fields["percentage"]) == ("498", "600", "83.00")
    assert len(result.subjects) == 6 and result.subjects[3] == {"name": "Mathematics", "marks": "92", "max_marks": "100"}


def test_rules_compute_totals_from_subjects():
    result = extract.parse_marksheet("Name : A B\nEnglish   84 / 100\nMaths  90 out of 100")
    assert (result.fields["total_marks"], result.fields["max_marks"], result.fields["percentage"]) == ("174", "200", "87.00")


def test_unsupported_and_unreadable_files(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(extract.ExtractError):
        extract.extract_marksheet("virus.exe", b"x")
    with pytest.raises(extract.ExtractError, match="AI reader"):
        extract.extract_marksheet("photo.jpg", b"\xff\xd8")


def test_ai_extraction_uses_schema_and_image():
    calls = []
    reply = {k: None for k in extract.FIELD_KEYS} | {
        "student_name": "Sara",
        "percentage": "91.5%",
        "subjects": [{"name": "Science", "marks": "95", "max_marks": "100"}],
    }

    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(output_text=json.dumps(reply))

    llm = SimpleNamespace(responses=SimpleNamespace(create=create))
    result = extract.extract_marksheet("photo.png", b"\x89PNG", llm=llm)
    assert result.method == "ai" and result.fields["student_name"] == "Sara"
    assert result.fields["percentage"] == "91.5"
    assert calls[0]["text"]["format"]["strict"] is True
    assert any(part["type"] == "input_image" for part in calls[0]["input"][0]["content"])


# ---------- saved documents ----------


@pytest.fixture
def qdrant():
    return QdrantClient(":memory:")


def test_documents_are_private_to_their_owner(qdrant):
    fields = {"student_name": "Aariz", "percentage": "83.00", "exam": "SSC"}
    subjects = [{"name": "English", "marks": "84", "max_marks": "100"}]
    doc_id = profile.save_document("ME", "marksheet", fields, subjects, "m.pdf", client=qdrant)
    profile.save_document("OTHER", "marksheet", {"student_name": "Someone else"}, [], "o.pdf", client=qdrant)

    mine = profile.list_documents("ME", client=qdrant)
    assert [d["id"] for d in mine] == [doc_id]
    details = profile.details_for_chat("ME", client=qdrant)
    assert "Aariz" in details and "English 84/100" in details and "Someone else" not in details

    assert not profile.delete_document("OTHER", doc_id, client=qdrant)  # not theirs
    assert profile.delete_document("ME", doc_id, client=qdrant)
    assert profile.list_documents("ME", client=qdrant) == []


def test_member_details_reach_the_prompt():
    messages = rag.build_messages("11th admission?", [], [], member_details="Percentage: 83.00")
    assert "<member_details>\nPercentage: 83.00\n</member_details>" in messages[-1]["content"]
    assert "<member_details>" not in rag.build_messages("hi", [], [])[-1]["content"]


# ---------- pages ----------


@pytest.fixture
def client(monkeypatch, qdrant):
    monkeypatch.setattr(profile.db, "get_client", lambda: qdrant)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    wasila.app.config["TESTING"] = True
    with wasila.app.test_client() as c:
        yield c


def csrf(client, path):
    return re.search(r'name="csrf" value="([^"]+)"', client.get(path).get_data(as_text=True)).group(1)


def test_upload_check_save_and_use_in_chat(client, monkeypatch):
    client.post("/login", data={"account": "member", "csrf": csrf(client, "/login")})
    token = csrf(client, "/documents")

    with SAMPLE_PDF.open("rb") as f:
        client.post("/documents/upload", data={"csrf": token, "file": (f, "ssc.pdf")})
    page = client.get("/documents").get_data(as_text=True)
    assert "Check the details" in page and 'value="AARIZ HUSSAIN MERCHANT"' in page

    form = {"csrf": token, "student_name": "Aariz Merchant", "percentage": "83.00", "exam": "SSC",
            "subject_name": ["English", ""], "subject_marks": ["84", ""], "subject_max": ["100", ""]}
    res = client.post("/documents/save", data=form)
    assert "saved=1" in res.headers["Location"]
    page = client.get(res.headers["Location"]).get_data(as_text=True)
    assert "Aariz Merchant" in page and "Saved just now" in page

    seen = {}
    monkeypatch.setattr(
        wasila.rag, "answer_question",
        lambda q, h, voice=False, member_details="": seen.setdefault("details", member_details) and rag.Answer(text="ok"),
    )
    client.post("/api/ask", json={"question": "11th science admission"}, headers={"X-CSRF-Token": token})
    assert "Aariz Merchant" in seen["details"] and "English 84/100" in seen["details"]


def test_bad_upload_shows_error(client):
    client.post("/login", data={"account": "member", "csrf": csrf(client, "/login")})
    token = csrf(client, "/documents")
    from io import BytesIO
    client.post("/documents/upload", data={"csrf": token, "file": (BytesIO(b"x"), "a.exe")})
    assert "Please upload a PDF" in client.get("/documents").get_data(as_text=True)
