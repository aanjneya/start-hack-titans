"""Read the citation preserved on a decision and, when available, its matching source passage."""
from sqlmodel import select

from ..core.models import Document


def read_source(db, proof):
    path = proof.get("path", "")
    passage = " ".join((proof.get("quote") or "").split())
    result = dict(evidence=proof, title=path.rsplit("/", 1)[-1], excerpt=None, published=None)
    if not path or "://" in path or len(passage) < 12:
        return result
    # One news file may contain several stories. Bind the passage to a unique stored document.
    rows = db.exec(select(Document).where(
        (Document.path == path) | Document.path.startswith(path + "#", autoescape=True))).all()
    matches = []
    for doc in rows:
        content = " ".join(doc.text.split())
        start = content.find(passage)
        if start >= 0:
            matches.append((doc, content, start))
    if len({content for _, content, _ in matches}) != 1:
        return result
    doc, content, start = matches[0]
    # Keep the buyer's screen focused on the cited passage and its immediate context.
    left, right = max(start - 170, 0), min(start + min(len(passage), 420) + 180, len(content))
    if left:
        boundary = content.find(" ", left, start)
        if boundary >= 0:
            left = boundary + 1
    if right < len(content):
        boundary = content.rfind(" ", max(start + 1, right - 40), right)
        if boundary > start:
            right = boundary
    result.update(title=doc.title or result["title"], published=doc.published_at.date().isoformat() if doc.published_at else None,
                  excerpt=("…" if left else "") + content[left:right] + ("…" if right < len(content) else ""))
    return result
