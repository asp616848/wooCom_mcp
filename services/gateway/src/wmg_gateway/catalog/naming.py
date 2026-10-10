"""Exposed tool names, titles and descriptions (DESIGN §5.5, D34).

Every tool is exposed as `<slug>__<sanitized original>` (max 64 chars), so names never change when servers are
added and two servers can both have an `echo` tool. Slugs contain no `_`, so `__` is an unambiguous separator.
"""

import hashlib
import re
from collections.abc import Iterable

MAX_NAME = 64
MAX_DESCRIPTION = 1024
_UNSAFE = re.compile(r"[^A-Za-z0-9_-]")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")  # keeps \t (collapsed below) and \n
_SPACES = re.compile(r"[ \t\r\f\v]+")


def sanitize(name: str) -> str:
    return _UNSAFE.sub("_", name)


def hash6(slug: str, original: str) -> str:
    return hashlib.sha256(f"{slug}\0{original}".encode()).hexdigest()[:6]


def _hashed(base: str, slug: str, original: str) -> str:
    return base[:57] + "_" + hash6(slug, original)


def exposed_names(slug: str, originals: Iterable[str]) -> dict[str, str]:
    """original name → exposed name for one server. Deterministic: originals are processed sorted."""
    result: dict[str, str] = {}
    taken: set[str] = set()
    for original in sorted(set(originals)):
        base = f"{slug}__{sanitize(original)}"
        exposed = base if len(base) <= MAX_NAME else _hashed(base, slug, original)
        if exposed in taken:  # only via sanitize collisions, e.g. "a.b" and "a_b"
            exposed = _hashed(base, slug, original)
        taken.add(exposed)
        result[original] = exposed
    return result


def exposed_description(slug: str, description: str | None) -> str:
    """`[slug] ` + upstream text with control chars (except newline) removed, spaces collapsed, capped."""
    text = _CONTROL.sub("", description or "")
    text = "\n".join(_SPACES.sub(" ", line).strip() for line in text.split("\n")).strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    full = f"[{slug}] {text}".rstrip()
    return full if len(full) <= MAX_DESCRIPTION else full[: MAX_DESCRIPTION - 1] + "…"


def exposed_title(server_name: str, title: str | None, original: str) -> str:
    return f"{server_name}: {title or original}"
