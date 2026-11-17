"""Utility functions used by :mod:`generate`.

These helpers were originally defined inside :mod:`generate.py`.  They
have been extracted into this module so that other parts of the
application – such as tests or future rendering code – can import
them without pulling in the entire SVG generation logic.

Only the helpers that are independent of the card‑building logic are
included.  The module re‑exports all public helpers via ``__all__``.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Iterable
import json
from datetime import datetime

# ---------------------------------------------------------------------------
# Format helpers
# ---------------------------------------------------------------------------
def fmt_in(v: float | int | str, *, w: int | None = None, p: int = 2) -> str:
    """Return a human‑readable numeric string.

    * ``v`` – value to format (int, float or a string that
      represents a number).
    * ``w`` – optional width – if provided the string will be
      padded to this width with spaces.
    * ``p`` – number of decimal places for floats.
    """
    if isinstance(v, str):
        v = int(v) if v.isdigit() else float(v)
    v = math.ceil(v) if isinstance(v, int) else round(v, p)
    s = f"{v}"
    return s if w is None else f"{s:>{w}}"


def plain(txt: str, *, max: int = 80) -> str:
    """Truncate a string to ``max`` characters, appending an ellipsis.

    The function preserves the original case and does not perform any
    additional formatting.
    """
    return (txt[:max] + "…") if len(txt) > max else txt


def signed(v: str) -> str:
    """Return a string with a sign prefix.

    ``v`` should be a numeric string.  The function prefixes ``+`` for
    positive values and leaves negative values untouched.
    """
    v = v.replace("+", "")
    return v if v.startswith("-") else f"+{v}"


def pretty(v: str | int | float | None, *, pct: bool = False, w: int | None = None) -> str:
    """Return a nicely formatted representation.

    * If ``v`` is ``None`` an empty string is returned.
    * For numeric values ``fmt_in`` is used, optionally adding a
      percent sign.
    * For strings the value is converted to *Title* case with
      underscores replaced by spaces.
    """
    if v is None:
        return ""
    if isinstance(v, (int, float)):
        if pct:
            return fmt_in(v, w=w, p=2) + "%"
        return fmt_in(v, w=w)
    # Treat everything else as a string
    s = str(v).replace("_", " ").title()
    return s


def fit_size(x: int, w: int, h: int) -> tuple[int, int]:
    """Return a width/height pair that fits inside ``(w, h)``.

    The function preserves the aspect ratio of ``x`` while ensuring
    neither dimension exceeds the provided bounds.
    """
    if w < h:
        return w, int(h * (w / x))
    return int(w * (h / x)), h


def est_width(txt: str, *, size: int = 24, h: int = 28, w: int = 1700) -> int:
    """Estimate how many pixels the string will occupy.

    This is a very coarse approximation – it simply multiplies the
    number of characters by a typical width.
    """
    txt = txt.strip()
    if not txt:
        return 0
    width = int((len(txt) * 0.8 + 4) * size * (h / 28))
    return min(width, w)


def wrap_lines(txt: str, *, w: int = 45) -> list[str]:
    """Wrap ``txt`` into a list of lines no longer than ``w``.

    Lines are broken on word boundaries when possible.
    """
    words = txt.split()
    lines: list[str] = []
    while words:
        line = words[0]
        words = words[1:]
        while words and len(line) + 1 + len(words[0]) <= w:
            line += " " + words[0]
            words = words[1:]
        lines.append(line)
    return lines


def t(a: int, *a2: int) -> str:
    """Return a comma‑separated string of ``a`` and ``a2``.

    The helper is used to create small SVG attribute groups such as
    ``transform="translate(...)"``.
    """
    return ",".join(map(str, (a, *a2)))


def runs(l: int, *r: int) -> str:
    """Return an ``x,y,z`` string used for SVG group transformations.

    The function returns a string of three comma‑separated numbers.
    """
    return ",".join(map(str, (l, *r)))


def r(a: int, b: int) -> str:
    """Return ``x,y`` as a comma‑separated string."""
    return ",".join(map(str, (a, b)))


def grp(c: str) -> str:
    """Wrap a string in a ``<g>`` element."""
    return f"<g>{c}</g>"


def pill(txt: str, *, pct: bool = False) -> str:
    """Return a styled pill shape for a number.

    The helper is used by :func:`counter` to display numeric counters.
    """
    if pct:
        txt = signed(txt)
    return f"<tspan class='pill'>{txt}</tspan>"


def chip(c: str) -> str:
    """Return a simple SVG ``<tspan>`` element.

    It is used by the counters for the individual chip values.
    """
    return f"<tspan>{c}</tspan>"


def counter(v: str, *, pct: bool = False, w: int | None = None) -> str:
    """Return a counter string with a leading ``+`` if positive.

    The function relies on :func:`pill` to format the value.
    """
    return pill(v, pct=pct) if pct else pill(v)


def bar(v: int, max: int = 5000, *, pct: bool = False, w: int | None = None) -> str:
    """Return a visual bar string for a value ``v``.

    The bar is rendered using SVG ``<tspan>`` elements.
    """
    return f"<tspan class='bar'>{v}</tspan>"


# Public API
__all__ = [
    "fmt_in",
    "plain",
    "signed",
    "pretty",
    "fit_size",
    "est_width",
    "wrap_lines",
    "t",
    "runs",
    "r",
    "grp",
    "pill",
    "chip",
    "counter",
    "bar",
]
