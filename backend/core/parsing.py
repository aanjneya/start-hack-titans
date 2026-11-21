"""Low-level readers shared by agents (ported from the 15-min drop watcher).

These only turn bytes into text/rows. Mapping columns to our tables is the Ingest agent's job.
"""
from __future__ import annotations

import csv
import io
import re
import zipfile
from pathlib import Path, PurePosixPath

TEXT_EXT = {".csv", ".tsv", ".txt", ".md", ".json", ".jsonl", ".xml", ".yaml", ".yml", ".log", ".eml", ".html"}


def safe_rel(path: str) -> Path:
    """Reject absolute paths and '..' from untrusted file lists."""
    p = PurePosixPath(path)
    if p.is_absolute() or ".." in p.parts or not p.parts:
        raise ValueError(f"unsafe path: {path!r}")
    return Path(*p.parts)


def decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-16", "cp1252", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def read_csv(data: bytes | str) -> tuple[list[str], list[dict[str, str]], str]:
    """Sniff delimiter (, ; tab |) and return (header, rows as dicts, delimiter)."""
    text = decode(data) if isinstance(data, bytes) else data
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
        delim = dialect.delimiter
    except csv.Error:
        delim = ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delim)
    rows = [{(k or "").strip(): (v or "").strip() for k, v in row.items()} for row in reader]
    return [h.strip() for h in (reader.fieldnames or [])], rows, delim


def office_text(data: bytes) -> str | None:
    """Crude stdlib text dump of .xlsx/.docx ('a | b | c' per spreadsheet row, one line per paragraph)."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return None
    names = z.namelist()
    if "word/document.xml" in names:
        xml = z.read("word/document.xml").decode("utf-8", "replace")
        paras = re.findall(r"<w:p[ >].*?</w:p>", xml, re.S)
        return "\n".join("".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", p)) for p in paras)
    if "xl/workbook.xml" in names:
        return "\n".join(" | ".join(r) for sheet in xlsx_rows(data).values() for r in sheet)
    return None


def xlsx_rows(data: bytes) -> dict[str, list[list[str]]]:
    """All sheets of an .xlsx as {sheet_file: [[cell, ...], ...]} using shared strings."""
    z = zipfile.ZipFile(io.BytesIO(data))
    names = z.namelist()
    shared: list[str] = []
    if "xl/sharedStrings.xml" in names:
        sx = z.read("xl/sharedStrings.xml").decode("utf-8", "replace")
        shared = ["".join(re.findall(r"<t[^>]*>([^<]*)</t>", si)) for si in re.findall(r"<si>.*?</si>", sx, re.S)]
    sheets: dict[str, list[list[str]]] = {}
    for name in sorted(n for n in names if n.startswith("xl/worksheets/sheet")):
        sx = z.read(name).decode("utf-8", "replace")
        rows: list[list[str]] = []
        for row in re.findall(r"<row[^>]*>.*?</row>", sx, re.S):
            cells = []
            for attrs, body in re.findall(r"<c([^>]*)>(.*?)</c>", row, re.S):
                v = re.search(r"<v>([^<]*)</v>", body) or re.search(r"<t[^>]*>([^<]*)</t>", body)
                val = v.group(1) if v else ""
                if 't="s"' in attrs and val.isdigit() and int(val) < len(shared):
                    val = shared[int(val)]
                cells.append(_unescape(val))
            rows.append(cells)
        sheets[name.rsplit("/", 1)[-1]] = rows
    return sheets


def pdf_text(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def file_text(path: Path) -> str | None:
    """Best-effort plain text of any supported file; None for binaries we can't read."""
    data = path.read_bytes()
    ext = path.suffix.lower()
    if ext in TEXT_EXT:
        return decode(data)
    if ext in {".xlsx", ".docx"}:
        return office_text(data)
    if ext == ".pdf":
        try:
            return pdf_text(data)
        except Exception:
            return None
    return None


def _unescape(s: str) -> str:
    """XML/HTML entities incl. numeric ones ('&#8211;' -> '–', '&amp;' -> '&')."""
    import html

    return html.unescape(s)


# ---------------------------------------------------------------------------
# Value parsing (locale-aware, never guesses: returns None if it can't parse)
# ---------------------------------------------------------------------------
_THOUSANDS_DOT = re.compile(r"^-?\d{1,3}(\.\d{3})+$")  # 640.000
_THOUSANDS_COMMA = re.compile(r"^-?\d{1,3}(,\d{3})+$")  # 48,000


def parse_number(value, decimal_comma: bool | None = None) -> float | None:
    """'48,000' -> 48000, '1,5200' (decimal comma) -> 1.52, '640.000' -> 640000, '6.11' -> 6.11."""
    if value is None:
        return None
    s = str(value).strip().replace(" ", "").replace(" ", "").replace("'", "")
    s = re.sub(r"(szt\.?|pcs|pc|eur|usd|cny|inr|€|\$)$", "", s, flags=re.I)
    if not s or s in {"-", "—"}:
        return None
    if decimal_comma is True:
        s = s.replace(".", "").replace(",", ".")
    elif decimal_comma is False:
        s = s.replace(",", "")
    elif _THOUSANDS_DOT.match(s) and not re.match(r"^-?0\.", s):  # '0.125' is never 125
        s = s.replace(".", "")
    elif _THOUSANDS_COMMA.match(s):
        s = s.replace(",", "")
    elif "," in s and "." not in s:
        s = s.replace(",", ".")  # lone decimal comma: 1,52
    elif "," in s and "." in s:
        s = s.replace(",", "") if s.rfind(".") > s.rfind(",") else s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def detect_date_order(values) -> str:
    """'ymd' for ISO, else 'mdy' or 'dmy' by looking for a part > 12 in a column of a/b/yyyy dates."""
    first_big = second_big = False
    for v in values:
        m = re.match(r"^\s*(\d{1,2})[./-](\d{1,2})[./-](\d{4})", str(v or ""))
        if not m:
            continue
        a, b = int(m.group(1)), int(m.group(2))
        first_big |= a > 12
        second_big |= b > 12
    if second_big and not first_big:
        return "mdy"
    if first_big and not second_big:
        return "dmy"
    return "dmy"  # European default when ambiguous


def parse_date(value, order: str = "ymd"):
    """Returns datetime.date or None. Accepts ISO dates/datetimes and a/b/yyyy with the given order."""
    from datetime import date, datetime

    if value is None:
        return None
    s = str(value).strip()
    if not s or s.startswith("9999"):
        return None
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = re.match(r"^(\d{1,2})[./-](\d{1,2})[./-](\d{4})$", s)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        month, day = (a, b) if order == "mdy" else (b, a)
        try:
            return date(y, month, day)
        except ValueError:
            return None
    try:
        return datetime.fromisoformat(s).date()
    except ValueError:
        return None


def parse_datetime(value, tz: str = "Europe/Berlin"):
    """Timezone-aware datetime; naive values get `tz` (portal times are Europe/Berlin per the API docs)."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    if value is None or not str(value).strip():
        return None
    try:
        dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        d = parse_date(value)
        if not d:
            return None
        dt = datetime(d.year, d.month, d.day)
    return dt if dt.tzinfo else dt.replace(tzinfo=ZoneInfo(tz))


def norm_header(h: str) -> str:
    """'Container/AWB ' -> 'container_awb', 'Day-one demand' -> 'day_one_demand'."""
    h = _unescape(str(h or "")).strip().lower()
    h = re.sub(r"[^\w]+", "_", h, flags=re.UNICODE)
    return h.strip("_")


def norm_name(name: str) -> str:
    """Company names without legal forms/punctuation: 'Karton Nowak Sp. z o.o.' -> 'karton nowak'."""
    s = _unescape(str(name or "")).lower()
    s = re.sub(r"\b(sp\.?\s*z\s*o\.?\s*o\.?|co\.?,?\s*ltd\.?|ltd\.?|inc\.?|gmbh|pvt\.?|llc|a/s|s\.a\.|ag)\b", " ", s)
    s = re.sub(r"[^\w]+", " ", s, flags=re.UNICODE)
    return re.sub(r"\s+", " ", s).strip()


def flatten_json(obj, prefix: str = "") -> dict:
    """{'supplier': {'contact': {'email': x}}} -> {'supplier.contact.email': x}."""
    out: dict = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.update(flatten_json(v, f"{prefix}{k}."))
    elif isinstance(obj, list):
        out[prefix[:-1]] = obj
    else:
        out[prefix[:-1]] = obj
    return out


def detect_language(text: str) -> str:
    """Very small script/keyword detector: ko, zh, pl, de, en."""
    if re.search(r"[가-힯]", text):
        return "ko"
    if re.search(r"[一-鿿]", text):
        return "zh"
    if re.search(r"[ąćęłńóśźż]", text, re.I) or re.search(r"\b(zamówienia|szt|umowy|przestoj)\b", text, re.I):
        return "pl"
    if re.search(r"\b(und|der|die|das|mit|für)\b", text):
        return "de"
    return "en"
