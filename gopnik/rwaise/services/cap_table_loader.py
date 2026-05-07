"""CSV cap-table loader + KYC / sanctions cross-check.

Wizard step 7 lets the issuer upload a CSV with columns:

    investor_email, classic_address, units, jurisdiction, accreditation

Each row is validated, normalised, and bucketed:

  - ``valid``         — clean row, ready to mint to.
  - ``review_queue``  — clean row but needs human attention (e.g. high
                        notional, sanctions-list near-match, missing
                        accreditation flag).
  - ``invalid``       — must be fixed before finalize (e.g. malformed
                        XRPL address, negative units).

This module returns a typed :class:`Report`. ``rwaise.routes`` jsonifies
it for the wizard's preview pane.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, TextIO

# RFC-light email shape — wizard validation; full check is on confirm-link
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# XRPL classic address shape (base58 r-prefix, 25-35 chars)
_XRPL_RE = re.compile(r"^r[1-9A-HJ-NP-Za-km-z]{24,34}$")

# A lightweight sanctions list. Production reads from a vendor feed
# (Refinitiv, Chainalysis Reactor, Treasury OFAC SDN). For demo:
_DEMO_SANCTIONS: set[str] = {
    "rUNSANCTIONED1234567890DEMO",  # placeholder
}


@dataclass
class Row:
    investor_email: str = ""
    classic_address: str = ""
    units: float = 0.0
    jurisdiction: str = ""
    accreditation: str = ""
    bucket: str = "invalid"
    notes: List[str] = field(default_factory=list)


@dataclass
class Report:
    rows: List[Row] = field(default_factory=list)
    total_units: float = 0.0
    valid_count: int = 0
    review_queue_count: int = 0
    invalid_count: int = 0
    is_clean: bool = False


def parse_and_validate(stream: TextIO,
                       *, high_notional_units: float = 100_000.0) -> Report:
    """Parse a CSV stream and return a :class:`Report`.

    Raises ``ValueError`` if the header is missing required columns.
    """
    reader = csv.DictReader(stream)
    required = {"investor_email", "classic_address", "units"}
    headers = set((reader.fieldnames or []))
    missing = required - headers
    if missing:
        raise ValueError(f"missing required column(s): {', '.join(sorted(missing))}")

    out = Report()
    for raw in reader:
        row = Row(
            investor_email=(raw.get("investor_email") or "").strip(),
            classic_address=(raw.get("classic_address") or "").strip(),
            units=_safe_float(raw.get("units")),
            jurisdiction=(raw.get("jurisdiction") or "").strip().upper(),
            accreditation=(raw.get("accreditation") or "").strip().lower(),
        )
        _validate_row(row, high_notional_units=high_notional_units)
        out.rows.append(row)
        if row.bucket == "valid":
            out.valid_count += 1
            out.total_units += row.units
        elif row.bucket == "review_queue":
            out.review_queue_count += 1
            out.total_units += row.units
        else:
            out.invalid_count += 1
    out.is_clean = out.invalid_count == 0
    return out


def _validate_row(row: Row, *, high_notional_units: float) -> None:
    notes: List[str] = []
    if not _EMAIL_RE.match(row.investor_email):
        notes.append("malformed email")
    if not _XRPL_RE.match(row.classic_address):
        notes.append("malformed XRPL classic address")
    if row.units <= 0:
        notes.append("units must be > 0")
    if row.classic_address in _DEMO_SANCTIONS:
        notes.append("sanctions-list match")

    if notes:
        row.bucket = "invalid"
        row.notes = notes
        return

    # Soft flags push to review_queue.
    soft: List[str] = []
    if row.units >= high_notional_units:
        soft.append(f"high notional (≥ {high_notional_units:g} units)")
    if not row.accreditation:
        soft.append("missing accreditation marker")
    if not row.jurisdiction:
        soft.append("missing jurisdiction")
    if soft:
        row.bucket = "review_queue"
        row.notes = soft
        return

    row.bucket = "valid"


def _safe_float(raw: Optional[str]) -> float:
    try:
        return float((raw or "").strip())
    except (TypeError, ValueError):
        return -1.0
