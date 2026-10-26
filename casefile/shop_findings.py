"""What the shop found on similar past removals, read from the case-memory record's CMMS text ("Shop finding: ENGINE
REMOVED UNSCHEDULED AT 209 CYC FOR PERFORMANCE DETERIORATION, EGT MARGIN EXHAUSTED. SHOP FOUND HPC STG 1-3 BLADES
ERODED, ..."), in sentence case for the brief. Which words stay in capitals and which shop abbreviations are spelled
out is asset-pack configuration (`shop_findings`). Every number comes from the record itself, so the claim built here
passes the numbers check against the case evidence it cites. No model reads or writes any of it."""

from __future__ import annotations

import re
from typing import Any

CASE = re.compile(r"Past case (?P<case>\S+) .*?removed unscheduled at cycle (?P<cycle>\d+)", re.IGNORECASE)
WARNING = re.compile(r"warning_to_removal_cycles=(\d+)")
NOTE = re.compile(r"Shop finding:\s*(?P<note>.+)$", re.IGNORECASE | re.DOTALL)
REASON = re.compile(r"\bFOR (?P<reason>[^.]+)\.", re.IGNORECASE)
FOUND = re.compile(r"\bSHOP FOUND (?P<found>[^.]+)\.?", re.IGNORECASE)
REPLACED = re.compile(r"\bREPLACED (?P<parts>[^.]+)", re.IGNORECASE)
PART = re.compile(r"\b[A-Z]{2}-[A-Z]{2,4}-\d{3,}\b")
TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*")


def readable(text: str, pack: dict) -> str:
    """CMMS capitals in sentence case: pack acronyms and part numbers keep their case, shop abbreviations are spelled
    out (STG -> stage)."""
    spec = pack.get("shop_findings") or {}
    keep = {w.upper() for w in spec.get("keep_upper") or []}
    spell = {k.upper(): v for k, v in (spec.get("spell_out") or {}).items()}

    def word(match: re.Match) -> str:
        token = match.group(0)
        upper = token.upper()
        if upper in spell:
            return spell[upper]
        if upper in keep or PART.fullmatch(upper) or any(ch.isdigit() for ch in token) and "-" in token \
                and any(ch.isalpha() for ch in token):
            return upper
        return token.lower()

    return TOKEN.sub(word, " ".join((text or "").split())).strip(" .")


def shop_finding(content: str, pack: dict) -> dict[str, Any] | None:
    """The past case's removal and shop finding as fields, or None when the record has no shop finding."""
    note = NOTE.search(content or "")
    if not note:
        return None
    raw = note.group("note").strip()
    head = CASE.search(content or "")
    warning = WARNING.search(content or "")
    reason, found, replaced = REASON.search(raw), FOUND.search(raw), REPLACED.search(raw)
    return {
        "case": head.group("case") if head else None,
        "removed_at_cycle": int(head.group("cycle")) if head else None,
        "warning_cycles": int(warning.group(1)) if warning else None,
        "reason": readable(reason.group("reason"), pack) if reason else None,
        "found": readable(found.group("found"), pack) if found else readable(raw, pack),
        "replaced": PART.findall(replaced.group("parts").upper()) if replaced else [],
    }


def finding_line(item: dict[str, Any]) -> str:
    """One past case in a line for the UI: case, removal, what the shop found, what it replaced."""
    bits = [f"{item.get('case') or 'Past case'}: removed at cycle {item.get('removed_at_cycle')}"
            + (f" for {item['reason']}" if item.get("reason") else "")]
    bits.append(f"the shop found {item.get('found')}")
    if item.get("replaced"):
        bits.append(f"replaced {', '.join(item['replaced'])}")
    return "; ".join(bits) + "."


def findings_claim(items: list[dict[str, Any]]) -> str:
    """The brief's sentence on the similar past removals: which engines, when, and what the shop found on them."""
    n = len(items)
    removed = ", ".join(f"{i.get('case')} at cycle {i.get('removed_at_cycle')}" for i in items)
    head = f"{n} similar past removal{'s' if n != 1 else ''} in case memory ({removed})"
    found = list(dict.fromkeys(i.get("found") for i in items))
    reasons = list(dict.fromkeys(i.get("reason") for i in items if i.get("reason")))
    if len(found) == 1:
        tail = f"the shop found {found[0]}" + (" on both" if n == 2 else f" on all {n}" if n > 2 else "")
    else:
        tail = "shop findings: " + "; ".join(f"{i.get('case')}: {i.get('found')}" for i in items)
    why = f", after removal for {reasons[0]}" if len(reasons) == 1 else ""
    return f"{head}; {tail}{why}."
