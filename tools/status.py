"""Live status of the services behind a brief, for the brief page's status strip and GET /health (run.py).

Components: mode; Zetaris and, behind it, the AWS sources it answers for (RDS Postgres telemetry_pg, cmms_pg,
knowledge_pg; S3 s3_files); the Meterless context service (World Model and H-MEM); Meterless Nightshift and the model
the critic lane serves; the CMMS write path. States: ok (green), warn (amber), off (grey).

Checks are read-only and short: one request each (no retries) with STATUS_TIMEOUT_S (2.5 s), run in parallel on a
background thread, and reused for STATUS_TTL_S (45 s), so the brief never waits on them. Sample mode makes no network
call at all. Nothing shown carries a credential, token, host or connection string: errors are reduced to their type
or HTTP status, and the context service shows only its port. Reuses the checks of tools/preflight.py."""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx

from settings import Settings

STATES = {"ok": "green", "warn": "amber", "off": "grey"}
# One cheap read per registered Zetaris source: (source, AWS service, statement).
SOURCE_PROBES = [
    ("telemetry_pg", "RDS Postgres", "SELECT asset_id FROM telemetry_pg.cycles"),
    ("cmms_pg", "RDS Postgres", "SELECT asset_id FROM cmms_pg.assets"),
    ("knowledge_pg", "RDS Postgres", "SELECT chunk_id FROM knowledge_pg.doc_chunks"),
    ("s3_files", "S3", "SELECT fmea_id FROM s3_files.fmea"),
]
# The model each Nightshift app last answered with in this process (live calls only): app -> (model, UTC time).
MODEL_SEEN: dict[str, tuple[str, datetime]] = {}


@dataclass
class Component:
    key: str
    label: str
    state: str                  # ok | warn | off
    summary: str                # one line: state first, then the facts behind it, " · "-separated
    details: list[str] = field(default_factory=list)
    parts: list[dict] = field(default_factory=list)     # Zetaris: one entry per AWS source
    chip: str = ""              # the strip's few words; the summary's first segments when empty

    def __post_init__(self) -> None:
        self.chip = self.chip or " · ".join(self.summary.split(" · ")[:2])

    def as_dict(self) -> dict:
        return asdict(self)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def clock(at: datetime | None) -> str:
    return at.strftime("%H:%M:%S UTC") if at else "never"


def note_model_reply(app: str, model: str) -> None:
    """Called after a live Nightshift reply (critic, intake): the strip names the model the critic lane serves."""
    if model:
        MODEL_SEEN[app] = (model, utcnow())


def pretty_model(model: str) -> str:
    """nvidia/nemotron-3-ultra-550b-a55b -> Nemotron 3 Ultra 550B A55B."""
    words = []
    for part in model.split("/")[-1].split("-"):
        if re.fullmatch(r"a?\d+(\.\d+)?[bm]", part):
            words.append(part.upper())
        else:
            words.append(part.capitalize() if part.isalpha() else part)
    return " ".join(words)


def _error(error: BaseException) -> str:
    """An error without its message: messages can carry hosts or URLs."""
    if isinstance(error, httpx.TimeoutException):
        return "no answer in time"
    if isinstance(error, httpx.HTTPStatusError):
        return f"HTTP {error.response.status_code}"
    match = re.search(r"HTTP (\d{3})", str(error))
    return f"{type(error).__name__}" + (f" (HTTP {match.group(1)})" if match else "")


# --- sample mode ------------------------------------------------------------------------------------------------

def _sample_parts(settings: Settings, memory: str | None) -> tuple[str, str]:
    """(case memory, critic) as sample mode uses them. `memory` is the last run's backend when there is one."""
    from tools import llm_recordings

    if memory is None:
        explicit = settings.explicit("context_store") and settings.context_store == "meterless"
        memory = "Meterless context service if it answers (CONTEXT_STORE=meterless), else SQLite" if explicit \
            else "sqlite"
    store = {"sqlite": "SQLite", "meterless": "Meterless context service"}.get(memory, memory)
    critic = "recorded Nemotron" if llm_recordings.RECORDINGS.exists() else "deterministic critic"
    return store, critic


def sample_summary(settings: Settings, memory: str | None = None) -> str:
    store, critic = _sample_parts(settings, memory)
    return f"Sample mode: snapshot · {store} · {critic}"


def sample_components(settings: Settings, memory: str | None = None) -> list[Component]:
    """What sample mode uses, with no network check."""
    summary = sample_summary(settings, memory)
    store, critic = _sample_parts(settings, memory)
    return [
        Component("mode", "Mode", "off", "sample", [summary]),
        Component("zetaris", "Zetaris", "off", "not called: committed snapshot",
                  ["Sample mode reads data/snapshot/*.parquet, extracts of the Zetaris sources"],
                  [{"name": n, "kind": k, "state": "off", "summary": "snapshot"} for n, k, _ in SOURCE_PROBES]),
        Component("context_service", "Meterless context service", "off", store,
                  ["World Model and H-MEM: " + store]),
        Component("nightshift", "Meterless Nightshift", "off", critic,
                  ["Sample mode replays recorded Nemotron replies on an exact prompt match; no live call"]),
        Component("cmms", "CMMS write path", "off", "JSONL draft work orders",
                  ["Approve appends to .state/draft_work_orders.jsonl"]),
    ]


# --- live checks ------------------------------------------------------------------------------------------------

def mode_component(settings: Settings) -> Component:
    if settings.zetaris_mode == "live":
        return Component("mode", "Mode", "ok", "live", ["SAMPLE_MODE off, ZETARIS_MODE=live"])
    return Component("mode", "Mode", "warn", "live · data from snapshot",
                     ["ZETARIS_MODE=snapshot: briefs read the committed snapshot"])


def check_zetaris(settings: Settings, transport: httpx.BaseTransport | None = None) -> Component:
    from tools import zetaris

    if settings.zetaris_mode != "live":
        return Component("zetaris", "Zetaris", "off", "not used (ZETARIS_MODE=snapshot)")
    try:
        client = zetaris.ZetarisClient(settings, timeout_s=settings.status_timeout_s, transport=transport)
    except zetaris.ZetarisError:
        return Component("zetaris", "Zetaris", "warn", "not configured",
                         ["ZETARIS_BASE_URL, ZETARIS_ORG_ID or ZETARIS_API_KEY is missing; briefs use the snapshot"])

    def probe(item: tuple[str, str, str]) -> dict:
        name, kind, sql = item
        start = time.monotonic()
        try:
            client.probe(sql)
            return {"name": name, "kind": kind, "state": "ok", "seconds": round(time.monotonic() - start, 2),
                    "summary": f"answered in {time.monotonic() - start:.1f} s"}
        except Exception as error:      # a failed probe is a state, never an exception in the UI
            return {"name": name, "kind": kind, "state": "warn", "seconds": None, "summary": _error(error)}

    try:
        with ThreadPoolExecutor(max_workers=len(SOURCE_PROBES)) as pool:
            parts = list(pool.map(probe, SOURCE_PROBES))
    finally:
        client.close()
    answered = [p for p in parts if p["state"] == "ok"]
    last = zetaris.LAST_OK.get("any")
    for p in parts:
        p["last_ok"] = clock(zetaris.LAST_OK.get(p["name"]))
    if not answered:
        state = "warn"
        summary = f"no answer · {parts[0]['summary']} · last query {clock(last)} · briefs fall back to the snapshot"
        chip = f"no answer · last query {clock(last)}"
    else:
        slowest = max(p["seconds"] for p in answered)
        state = "ok" if len(answered) == len(parts) else "warn"
        word = "reachable" if state == "ok" else f"{len(answered)} of {len(parts)} sources answering"
        summary = f"{word} · {slowest:.1f} s · last query {clock(last)}"
        chip = summary
    details = [f"{p['name']} ({p['kind']}): {p['summary']}; last answer {p['last_ok']}" for p in parts]
    return Component("zetaris", "Zetaris", state, summary, details, parts, chip)


def context_health_summary(health: dict) -> tuple[str, int, int, int]:
    """(summary, relations, document nodes, memories) from the context service's GET /health (shared with
    tools/preflight.py)."""
    world, hmem = health.get("world") or {}, health.get("hmem") or {}
    relations = int(world.get("relations") or 0)
    documents = int((world.get("entities") or {}).get("document") or 0)
    memories = int(hmem.get("memories") or 0)
    return (f"World Model {relations} relations, {documents} document nodes · H-MEM {memories} memories",
            relations, documents, memories)


def check_context_service(settings: Settings, transport: httpx.BaseTransport | None = None) -> Component:
    if settings.context_store == "sqlite":
        return Component("context_service", "Meterless context service", "off", "not used (CONTEXT_STORE=sqlite)",
                         ["World Model and H-MEM run on the SQLite fallback"])
    port = urlparse(settings.context_service_url).port
    where = f"port {port}" if port else "context service"
    start = time.monotonic()
    try:
        with httpx.Client(timeout=settings.status_timeout_s, transport=transport) as http:
            response = http.get(settings.context_service_url.rstrip("/") + "/health")
            response.raise_for_status()
            health = response.json()
    except Exception as error:
        return Component("context_service", "Meterless context service", "warn",
                         f"unreachable · {where} · {_error(error)} · briefs use SQLite",
                         ["World Model and H-MEM fall back to SQLite (flagged in the brief)"])
    took = time.monotonic() - start
    summary, relations, documents, memories = context_health_summary(health)
    loaded = relations and memories
    return Component("context_service", "Meterless context service", "ok" if loaded else "warn",
                     (f"up · {where} · {took * 1000:.0f} ms · World Model {relations} edges · H-MEM {memories} "
                      "memories" if loaded else f"empty · {where} · run uv run task load-context"),
                     [summary])


def check_nightshift(settings: Settings, transport: httpx.BaseTransport | None = None) -> Component:
    from tools.llm import LlmClient

    client = LlmClient(settings, meterless_transport=transport)
    try:
        if not client.configured:
            return Component("nightshift", "Meterless Nightshift", "warn", "no runtime token · deterministic critic",
                             ["METERLESS_RUNTIME_TOKEN is not set; the critic runs its code checks only"])
        start = time.monotonic()
        state, modes = client.status(timeout_s=settings.status_timeout_s)
        took = time.monotonic() - start
    finally:
        client.close()
    app = settings.meterless_critic_app
    seen = MODEL_SEEN.get(app)
    lane = pretty_model(seen[0]) if seen else "critic model shown after the first live critic call"
    details = [f"Nightshift app {app}, routing {settings.meterless_critic_model}"]
    if seen:
        details.append(f"Critic lane last served by {seen[0]} at {clock(seen[1])}")
    if state != "online":
        words = {"unauthorized": "token refused", "offline": "not reachable"}[state]
        return Component("nightshift", "Meterless Nightshift", "warn", f"{words} · deterministic critic", details)
    nemotron = seen is None or "nemotron" in seen[0].lower()
    if not nemotron:
        details.append("Critic lane not pinned to Nemotron: pin the Judge lane in Nightshift")
    return Component("nightshift", "Meterless Nightshift", "ok" if nemotron else "warn",
                     f"online · {lane} · {took * 1000:.0f} ms", details + [f"{len(modes)} routing modes"])


def cmms_component(settings: Settings) -> Component:
    """Configured or not; never a connection to the live CMMS (approve is the only writer)."""
    if settings.pg_app_user and settings.pg_app_password.get_secret_value():
        return Component("cmms", "CMMS write path", "ok", "configured · approve inserts a draft into cmms.work_orders",
                         ["The JSONL record (.state/draft_work_orders.jsonl) is written as well"], chip="configured")
    return Component("cmms", "CMMS write path", "warn", "JSONL fallback · .state/draft_work_orders.jsonl",
                     ["PG_APP_USER is not set: approve appends to .state/draft_work_orders.jsonl"],
                     chip="JSONL fallback")


def _guarded(key: str, label: str, check: Callable[[Settings], Component], settings: Settings) -> Component:
    try:
        return check(settings)
    except Exception as error:      # one broken check is one amber chip, never a broken strip
        return Component(key, label, "warn", f"check failed · {_error(error)}")


def live_components(settings: Settings) -> list[Component]:
    checks = [("zetaris", "Zetaris", check_zetaris),
              ("context_service", "Meterless context service", check_context_service),
              ("nightshift", "Meterless Nightshift", check_nightshift)]
    with ThreadPoolExecutor(max_workers=len(checks)) as pool:
        found = list(pool.map(lambda item: _guarded(*item, settings), checks))
    return [mode_component(settings), *found, cmms_component(settings)]


# --- cache --------------------------------------------------------------------------------------------------------

def _pending(settings: Settings) -> list[Component]:
    return [mode_component(settings), *(Component(k, label, "off", "checking…") for k, label in (
        ("zetaris", "Zetaris"), ("context_service", "Meterless context service"),
        ("nightshift", "Meterless Nightshift"))), cmms_component(settings)]


def snapshot(components: list[Component], at: datetime | None, sample: bool, summary: str | None = None) -> dict:
    states = [c.state for c in components]
    overall = "sample" if sample else ("ok" if all(s != "warn" for s in states) else "degraded")
    return {"overall": overall, "checked_at": at.isoformat(timespec="seconds") if at else None,
            "summary": summary or " · ".join(f"{c.label}: {c.summary}" for c in components),
            "components": [c.as_dict() for c in components]}


class StatusBoard:
    """Caches the live checks for `ttl_s` and refreshes them on a background thread, one refresh at a time."""

    def __init__(self, checks: Callable[[Settings], list[Component]] = live_components):
        self._checks = checks
        self._lock = threading.Lock()
        self._result: dict | None = None
        self._at = 0.0
        self._key: tuple | None = None
        self._thread: threading.Thread | None = None

    def _refresh(self, settings: Settings, key: tuple) -> None:
        try:
            components = self._checks(settings)
        except Exception as error:      # a broken check must not take the strip or /health down
            components = [Component("status", "Status checks", "warn", _error(error))]
        result = snapshot(components, utcnow(), sample=False)
        with self._lock:
            self._result, self._at, self._key = result, time.monotonic(), key

    def get(self, settings: Settings, wait_s: float = 0.0, memory: str | None = None) -> dict:
        """The latest status. Sample mode answers at once without network. Live mode returns the cached result and
        starts a refresh when it is older than STATUS_TTL_S; `wait_s` waits that long for a running refresh."""
        if settings.sample_mode:
            return snapshot(sample_components(settings, memory), utcnow(), sample=True,
                            summary=sample_summary(settings, memory))
        key = (settings.zetaris_mode, settings.context_store, settings.context_service_url,
               settings.meterless_base_url, settings.meterless_critic_app)
        with self._lock:
            stale = self._result is None or self._key != key or time.monotonic() - self._at >= settings.status_ttl_s
            if stale and (self._thread is None or not self._thread.is_alive()):
                self._thread = threading.Thread(target=self._refresh, args=(settings, key), daemon=True,
                                                name="status-checks")
                self._thread.start()
            thread, result = self._thread, (self._result if self._key == key else None)
        if stale and wait_s > 0 and thread is not None:
            thread.join(wait_s)
            with self._lock:
                result = self._result if self._key == key else result
        return result or snapshot(_pending(settings), None, sample=False)


BOARD = StatusBoard()


def current(settings: Settings, wait_s: float = 0.0, memory: str | None = None) -> dict:
    return BOARD.get(settings, wait_s, memory)
