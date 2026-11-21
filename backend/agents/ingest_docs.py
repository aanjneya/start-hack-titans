"""Unstructured documents for the Ingest agent: PDFs, Word, Markdown, news JSON, scans (T2.5, T2.6, T2.13–T2.15).

Pipeline per document (Rossum-style): classify -> extract -> ground/validate -> store.
- Classification uses the folder + title words (cheap, deterministic); unknown -> "other".
- Order confirmations follow one supplier template -> parsed by rules (fast, free, repeatable); notes are kept.
- Every document also gets regex "facts" (POs, parts, dates, quantities, IBANs, revisions, risk words), so the
  other agents can work offline. With MOCK_MODE=false, Claude adds an English summary + key facts, each with a
  verbatim quote that must appear in the text (grounding), otherwise the fact is dropped.
- Scans (images / PDFs without text) are stored with what the file name tells us; reading them needs live Claude.
"""
from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel

from ..core import llm
from ..core.parsing import detect_language, parse_date, parse_number

PO_RE = re.compile(r"\b(45\d{8})\b")
PART_RE = re.compile(r"\b(CMP-[A-Z]{3}-\d{3})\b")
IBAN_RE = re.compile(r"\b([A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){3,7}(?:\s?[A-Z0-9]{1,3})?)\b")
ISO_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
EU_DATE_RE = re.compile(r"\b(\d{1,2}\.\d{1,2}\.\d{4})\b")
REV_RE = re.compile(r"\b(?:rev(?:ision)?|rewizj[ai]|版本|리비전)\.?\s*([A-Z])\b", re.I)
QTY_RE = re.compile(r"\b(\d{1,3}(?:[ ,.]\d{3})+|\d+)\s*(?:pcs|szt\.?|units|stück|件|개)\b", re.I)
RISK_WORDS = {
    "delay": r"delay|verspät|opóźni|延迟|지연|moved out|move out",
    "suspended": r"suspend|stopped|halt|wstrzym|暂停|중단",
    "export_control": r"export (?:licen[cs]e|control)|出口许可|export ban",
    "downtime": r"downtime|przest[oó]j|shutdown|maintenance",
    "short_shipment": r"short shipment|balance|offloaded|partial",
    "quality": r"defect|reject|AQL|concession|blocking",
    "bank_change": r"bank details|new account|IBAN|zmiana rachunku",
    "price_change": r"price (?:increase|change)|new price|\+\s?\d+\s?%",
    "rush_offer": r"rush|express|ekspres",
}

KIND_BY_FOLDER = {"order_confirmations": "order_confirmation", "notices": "notice", "contracts": "contract",
                  "receiving": "receiving", "news": "news", "context": "context"}
KIND_BY_TITLE = [
    ("shipping_advice", r"shipping advice|출하 통지"),
    ("order_confirmation", r"order (?:confirmation|acknowledg)|potwierdzenie zamówienia|订单确认|주문 확인"),
    ("offer", r"\boffer\b|oferta"),
    ("inspection", r"inspection report"),
    ("contract", r"agreement|contract no"),
    ("org_chart", r"org(?:anisation|anization)? chart|reporting\b"),
    ("policy", r"policy|approval limits"),
    ("memo", r"^memo|memo:"),
]


def classify(rel_path: str, text: str) -> str:
    parts = rel_path.split("/")
    head = "\n".join(text.strip().splitlines()[:4]).lower()
    for kind, pattern in KIND_BY_TITLE:
        if re.search(pattern, head, re.I):
            return kind
    for folder in reversed(parts[:-1]):
        if folder in KIND_BY_FOLDER:
            return KIND_BY_FOLDER[folder]
    return "other"


def facts(text: str) -> dict:
    """Cheap, deterministic facts every agent can use offline."""
    found = {
        "po_numbers": sorted(set(PO_RE.findall(text))),
        "parts": sorted(set(PART_RE.findall(text))),
        "dates": sorted({d for d in ISO_DATE_RE.findall(text)}
                        | {str(parse_date(d.replace(".", "/"), "dmy")) for d in EU_DATE_RE.findall(text)}),
        "quantities": sorted({int(q) for q in (parse_number(x) for x in QTY_RE.findall(text)) if q}),
        "ibans": sorted({re.sub(r"\s", "", i) for i in IBAN_RE.findall(text) if not i[:2].isdigit()}),
        "revisions": sorted({r.upper() for r in REV_RE.findall(text)}),
        "signals": sorted(k for k, pattern in RISK_WORDS.items() if re.search(pattern, text, re.I)),
    }
    return {k: v for k, v in found.items() if v}


# ---------------------------------------------------------------------------
# Order confirmation template (all six suppliers use it, in KO/ZH/PL/EN)
# ---------------------------------------------------------------------------
_OA_LINE = re.compile(
    r"(?m)^(\d{5})\n([A-Z0-9][A-Z0-9-]{3,})\n(.*?)\n([\d.,]+)\n([\d.,]+)\n([A-Z]{3})\n(\d{4}-\d{2}-\d{2})$", re.S)


def parse_order_confirmation(text: str) -> Optional[dict]:
    """Header + line items + free-text notes. None if the text doesn't follow the template."""
    oa = re.search(r"\b(OA\d{6,})\b", text)
    po = re.search(r"Your order (\d{10})(?: of (\d{4}-\d{2}-\d{2}))?", text)
    lines = []
    last_end = 0
    for m in _OA_LINE.finditer(text):
        item, material, desc, qty, price, cur, confirmed = m.groups()
        lines.append({
            "po_line": int(item) // 10 if int(item) % 10 == 0 else int(item),
            "material_id": material,  # may be the supplier's own part number -> resolved by Ingest
            "description": " ".join(desc.split()),
            "quantity": parse_number(qty, decimal_comma=False), "unit_price": parse_number(price),
            "currency": cur, "delivery_date": confirmed, "quote": m.group(0).replace("\n", " | "),
        })
        last_end = m.end()
    if not (oa and po and lines):
        return None
    notes = text[last_end:].strip()
    incoterm = re.search(r"Incoterms ([A-Z]{3}[^·\n]*)", text)
    payment = re.search(r"Payment\s*\n?\s*(NET\s?\d+)", text)
    # a revision mentioned next to a part in the notes belongs to that line
    for line in lines:
        near = re.search(rf"{line['material_id']}[^\n]{{0,120}}?\brev\.?\s*([A-Z])\b", notes, re.I)
        line["revision"] = near.group(1).upper() if near else ""
    ibans = facts(text).get("ibans", [])
    return {
        "confirmation_id": oa.group(1), "po_number": po.group(1), "order_date": po.group(2) or "",
        "incoterm": (incoterm.group(1).strip() if incoterm else ""), "payment": payment.group(1) if payment else "",
        "lines": lines, "notes": notes, "bank_iban": ibans[0] if ibans else "",
    }


# ---------------------------------------------------------------------------
# Claude: summary + grounded key facts (live mode only; MOCK returns None)
# ---------------------------------------------------------------------------
class KeyFact(BaseModel):
    name: str  # e.g. "delivery_date", "quantity_shipped", "price_increase_pct", "bank_iban"
    value: str
    part_id: str = ""
    po_number: str = ""
    quote: str  # verbatim text from the document


class DocExtraction(BaseModel):
    title_en: str
    summary_en: str  # 1-3 sentences, English
    facts: list[KeyFact]


def llm_extract(text: str, kind: str, rel_path: str, agent: str) -> Optional[dict]:
    """English summary + grounded facts. Facts whose quote isn't in the text are dropped (T2.14)."""
    if not text.strip():
        return None
    prompt = (
        f"Document kind: {kind}. File: {rel_path}. It may be in Korean, Chinese, Polish, German or English.\n"
        "Give an English title, a 1-3 sentence English summary for a procurement team preparing a launch, and the "
        "key facts (dates, quantities, prices, part numbers, PO numbers, bank details, delays, conditions). "
        "Every fact needs a verbatim quote copied exactly from the document.\n\n---\n" + text[:20000]
    )
    result = llm.extract(prompt, DocExtraction, agent=agent, task=f"ingest.doc.{kind}")
    if result is None:
        return None
    flat = " ".join(text.split())
    grounded = [f.model_dump() for f in result.facts if f.quote and " ".join(f.quote.split()) in flat]
    return {"title_en": result.title_en, "summary_en": result.summary_en, "facts": grounded,
            "dropped_ungrounded": len(result.facts) - len(grounded)}


def scan_info(rel_path: str) -> dict:
    """What a scan's file name tells us, e.g. 20260907_WZ-260906-369.jpg -> date + delivery note number."""
    name = rel_path.rsplit("/", 1)[-1]
    out: dict = {}
    m = re.match(r"(\d{8})_([A-Z]{2,3}-\d{6}-\d{3})", name)
    if m:
        d = m.group(1)
        out = {"scan_date": f"{d[:4]}-{d[4:6]}-{d[6:]}", "delivery_note": m.group(2)}
    for key, rx in (("po_numbers", PO_RE), ("parts", PART_RE)):
        hits = rx.findall(name)
        if hits:
            out[key] = hits
    return out


def language(text: str) -> str:
    return detect_language(text) if text else ""


# ---------------------------------------------------------------------------
# Scans (photos of delivery notes, packing lists, image-only PDFs): Claude vision OCR
# ---------------------------------------------------------------------------
class ScanLine(BaseModel):
    part_number: str  # exactly as printed (our CMP-… or the supplier's own number)
    description: str = ""
    quantity: Optional[float] = None  # plain number, no thousands separators
    unit: str = ""
    packages: Optional[float] = None


class ScanExtraction(BaseModel):
    document_type: str  # delivery_note | packing_list | inspection_report | other
    document_number: str = ""
    document_date: str = ""  # YYYY-MM-DD
    supplier_name: str = ""
    po_numbers: list[str] = []
    lines: list[ScanLine] = []
    carrier_reference: str = ""
    remarks: str = ""  # printed remarks field, translated to English
    handwritten_notes: str = ""  # any handwriting except the signature (e.g. "2 cartons damaged"), in English
    received_by: str = ""
    language: str = ""  # ISO code of the printed document
    summary_en: str = ""  # one English sentence


SCAN_PROMPT = (
    "This is a scanned document received at a distribution centre (delivery note, packing list or similar). "
    "It may be Polish, Korean, Chinese, German or English. Transcribe exactly what is printed: document type, "
    "number, date (as YYYY-MM-DD), supplier, purchase order numbers, every line (part number exactly as printed, "
    "description, quantity as a plain number without thousands separators, unit, packages), carrier reference, "
    "the remarks field and ANY handwritten notes other than the signature (translate both to English), who "
    "received it, and a one-sentence English summary. Leave a field empty if it is not on the document; never guess."
)


def ocr_scan(path, rel_path: str, agent: str) -> Optional[ScanExtraction]:
    """Claude vision on one scan (live mode). MOCK mode -> None unless a mock is registered."""
    return llm.extract(f"{SCAN_PROMPT}\nFile: {rel_path}", ScanExtraction, agent=agent, task="ingest.scan_ocr",
                       files=[path])
