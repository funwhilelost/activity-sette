"""Small helpers for namespace-agnostic XML parsing."""

from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def read_xml(path: Path) -> tuple[ET.Element, str]:
    """Parse an XML file, tolerating BOMs and leading whitespace. Returns (root, sha256)."""
    data = path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    cleaned = data.lstrip(b"\xef\xbb\xbf").lstrip()
    return ET.fromstring(cleaned), sha


def child(el: ET.Element | None, name: str) -> ET.Element | None:
    if el is None:
        return None
    for c in el:
        if local(c.tag) == name:
            return c
    return None


def children(el: ET.Element, name: str) -> list[ET.Element]:
    return [c for c in el if local(c.tag) == name]


def find_path(el: ET.Element | None, *names: str) -> ET.Element | None:
    for n in names:
        el = child(el, n)
        if el is None:
            return None
    return el


def text(el: ET.Element | None, *names: str) -> str | None:
    el = find_path(el, *names)
    if el is None or el.text is None:
        return None
    t = el.text.strip()
    return t or None


def descendant_text(el: ET.Element, name: str) -> str | None:
    for d in el.iter():
        if local(d.tag) == name and d.text and d.text.strip():
            return d.text.strip()
    return None


def to_float(s: str | None) -> float | None:
    if s is None:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def to_int(s: str | None) -> int | None:
    f = to_float(s)
    return None if f is None else int(round(f))


def parse_time(s: str | None) -> datetime | None:
    """Parse ISO-8601 timestamps; naive values are assumed to be UTC."""
    if not s:
        return None
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
