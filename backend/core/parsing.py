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
    return (s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
             .replace("&quot;", '"').replace("&apos;", "'").replace("&#183;", "·"))
