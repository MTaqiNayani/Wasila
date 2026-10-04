"""Member documents saved to the profile (Qdrant `documents` collection).

Each record belongs to one member (OneID) and is only ever read with a filter on
that OneID. Only the details the member checked and saved are stored, never the file.
"""

import uuid
from datetime import datetime, timezone

from qdrant_client import models

from core import db
from core.extract import MARKSHEET_FIELDS
from core.security import mask

DOC_TYPES = {"marksheet": "Marksheet"}


def _owner_filter(oneid: str) -> models.Filter:
    return models.Filter(must=[models.FieldCondition(key="oneid", match=models.MatchValue(value=oneid))])


def summary_text(doc: dict) -> str:
    """One readable block per document, used for the chat and for the search vector."""
    labels = dict(MARKSHEET_FIELDS)
    lines = [f"{DOC_TYPES.get(doc['doc_type'], doc['doc_type'])} ({doc.get('filename', '')})"]
    lines += [f"{labels.get(k, k)}: {v}" for k, v in doc["fields"].items() if v]
    if doc.get("subjects"):
        marks = ", ".join(
            f"{s['name']} {s['marks']}" + (f"/{s['max_marks']}" if s.get("max_marks") else "")
            for s in doc["subjects"]
        )
        lines.append(f"Subjects: {marks}")
    return mask("\n".join(lines))


def save_document(oneid: str, doc_type: str, fields: dict, subjects: list[dict], filename: str, client=None) -> str:
    client = client or db.get_client()
    db.ensure_collection(db.DOCUMENTS, client)
    doc_id = str(uuid.uuid4())
    doc = {
        "oneid": oneid,
        "doc_type": doc_type,
        "filename": filename,
        "fields": {k: mask(str(v)) for k, v in fields.items()},
        "subjects": subjects,
        "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    client.upsert(
        collection_name=db.DOCUMENTS,
        points=[models.PointStruct(id=doc_id, vector=db.embed([summary_text(doc)])[0], payload=doc)],
    )
    return doc_id


def list_documents(oneid: str, client=None) -> list[dict]:
    client = client or db.get_client()
    if not client.collection_exists(db.DOCUMENTS):
        return []
    points, _ = client.scroll(
        collection_name=db.DOCUMENTS, scroll_filter=_owner_filter(oneid), limit=100, with_payload=True
    )
    docs = [{"id": str(p.id), **p.payload} for p in points]
    return sorted(docs, key=lambda d: d.get("saved_at", ""), reverse=True)


def get_document(oneid: str, doc_id: str, client=None) -> dict | None:
    return next((d for d in list_documents(oneid, client) if d["id"] == doc_id), None)


def delete_document(oneid: str, doc_id: str, client=None) -> bool:
    client = client or db.get_client()
    if not get_document(oneid, doc_id, client):  # only the owner can delete
        return False
    client.delete(collection_name=db.DOCUMENTS, points_selector=models.PointIdsList(points=[doc_id]))
    return True


def details_for_chat(oneid: str, client=None) -> str:
    """The member's saved details, for pre-filling applications in the helpdesk chat."""
    return "\n\n".join(summary_text(d) for d in list_documents(oneid, client))
