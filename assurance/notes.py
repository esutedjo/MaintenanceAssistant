"""Screen the LLM critic's free-text notes before the engineer sees them. Notes never block a cited brief, but a note
that code can show is false still costs the reader's trust, so it is dropped (kept in the trace for audit):

- it says an evidence ID is missing or not listed when that record is in the case file;
- it names an evidence ID the case file does not have (every claim's citations resolve, or the rule checks would
  have sent the brief back, so such an ID is the model's own);
- it repeats a checkable challenge the model also raised and code refuted.

Near-duplicates are folded: notes or refuted challenges that differ only in their numbers become one line."""

from __future__ import annotations

import re
from typing import Any

from casefile.schemas import CaseFile

EVIDENCE_ID = re.compile(r"\bEV-[A-Z]{3}-\d{2,}\b")
_ID = r"(EV-[A-Z]{3}-\d{2,})"
# "EV-PRG-01 is not listed", "EV-PRG-01 record is missing", "EV-X-01 does not exist", "missing EV-PRG-01", "no EV-…"
MISSING_AFTER = re.compile(
    _ID + r"(?:\s+(?:record|evidence|itself))?\s+(?:(?:is|was|are)\s+(?:not\s+(?:listed|present|provided|available|"
    r"included|found|shown|given|in\s+the\s+(?:evidence|case\s+file|list))|missing|absent|unavailable)|"
    r"(?:does\s+not|doesn't)\s+(?:exist|appear))", re.I)
MISSING_BEFORE = re.compile(r"\b(?:missing|absent|no)\s+(?:evidence\s+)?(?:record\s+)?" + _ID, re.I)
# A free number, outside identifiers such as EV-PRG-01 or TF-HPC-1101 (a range like 5-11 still yields 5 and 11).
LOOSE_NUMBER = re.compile(r"(?<![\w.])\d+(?:\.\d+)?")
SKELETON_NUMBER = re.compile(r"(?<![\w.-])-?\d+(?:\.\d+)?(?![\w-])")
WORD = re.compile(r"[a-z0-9]+")
NEAR_DUPLICATE = 0.8


def _numbers(text: str) -> list[float]:
    return [float(n) for n in LOOSE_NUMBER.findall(EVIDENCE_ID.sub(" ", text))]


def _mentions_claim(note: str, index: Any) -> bool:
    try:
        i = int(index)
    except (TypeError, ValueError):
        return False
    return re.search(rf"\bclaims?\s*\[?\s*{i}\s*\]?(?![\d.])", note, re.I) is not None


def _repeats(note: str, challenge: dict[str, Any], case: CaseFile) -> bool:
    """Whether a free-text note makes the same objection as a challenge code refuted."""
    kind, text = challenge.get("kind"), note.lower()
    rec = case.recommendation
    if kind == "unsupported_number":
        if not _mentions_claim(note, challenge.get("claim")):
            return False
        try:
            value = float(challenge.get("value"))
        except (TypeError, ValueError):
            return True
        return any(abs(n - value) <= 0.051 for n in _numbers(note))
    if kind == "deadline_after_bound":
        return "deadline" in text
    if kind == "missing_mandate":
        tasks = {d.required_task for d in case.findings.documents if d.mandatory and d.required_task}
        return "mandat" in text or any(t and t.lower() in text for t in tasks)
    if kind == "part_not_in_evidence":
        return any(p.part_number.lower() in text for p in (rec.parts if rec else [])) and \
            any(w in text for w in ("not in", "missing", "not listed", "absent"))
    if kind == "wrong_engine":
        return ("engine" in text or "asset" in text) and re.search(r"\bFD\d{3}-A\d{3}\b", note) is not None
    return False


def false_reason(note: str, case: CaseFile) -> str | None:
    """Why code can show the note is false, or None."""
    for pattern in (MISSING_AFTER, MISSING_BEFORE):
        for match in pattern.finditer(note):
            ident = match.group(1).upper()
            if ident in case.evidence:
                return f"says {ident} is missing, but {ident} is in the case file"
    unknown = [i for i in dict.fromkeys(EVIDENCE_ID.findall(note)) if i not in case.evidence]
    if unknown:
        return f"cites {', '.join(unknown)}, which the case file does not have"
    return None


def screen(case: CaseFile, notes: list[str], refuted: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    """(notes to show, dropped notes as "<why>: <note>"). `refuted` are the raw challenges code refuted."""
    kept, dropped = [], []
    for note in notes:
        reason = false_reason(note, case)
        if reason is None:
            twin = next((c for c in refuted if _repeats(note, c, case)), None)
            if twin is not None:
                reason = f"repeats the {str(twin.get('kind')).replace('_', ' ')} challenge code refuted"
        if reason:
            dropped.append(f"{reason}: {note}")
        else:
            kept.append(note)
    return kept, dropped


def _skeleton(text: str) -> str:
    return SKELETON_NUMBER.sub("#", " ".join(text.split()))


def _similar(a: str, b: str) -> bool:
    x, y = set(WORD.findall(a.lower())), set(WORD.findall(b.lower()))
    return bool(x and y) and len(x & y) / len(x | y) >= NEAR_DUPLICATE


def _merge(texts: list[str]) -> str:
    """One line for lines that differ only in their numbers: each differing number becomes "a or b"."""
    numbers = [SKELETON_NUMBER.findall(t) for t in texts]
    pieces = SKELETON_NUMBER.split(texts[0])
    out = pieces[0]
    for i, rest in enumerate(pieces[1:]):
        values = list(dict.fromkeys(n[i] for n in numbers if i < len(n)))
        out += " or ".join(values) + rest
    return out


def fold(lines: list[str], marker: str | None = None) -> tuple[list[str], int]:
    """Fold near-duplicate lines, keeping the first one's place. Lines that differ only in their numbers merge into
    one line naming every value; other near-duplicates (word overlap of 80% or more) keep the first and count the
    rest. With `marker` (the refuted-challenge wording) a merged line says how many challenges it stands for.
    Returns (lines, how many lines were folded away)."""
    groups: list[list[str]] = []
    for line in lines:
        home = next((g for g in groups if _skeleton(g[0]) == _skeleton(line) or _similar(g[0], line)), None)
        if home is None:
            groups.append([line])
        elif line not in home:
            home.append(line)
    out = []
    for group in groups:
        if len(group) == 1:
            out.append(group[0])
            continue
        same = all(_skeleton(g) == _skeleton(group[0]) for g in group)
        text = _merge(group) if same else group[0]
        if marker and marker in text:
            text = text.replace(marker, f"{marker} ({len(group)} challenges)", 1)
        elif not same:
            text += f" (and {len(group) - 1} similar note{'s' if len(group) > 2 else ''})"
        out.append(text)
    return out, len(lines) - len(out)
