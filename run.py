"""Entry point. JSON in, JSON out.

  uv run python run.py --question "How long can FD003-A100 keep flying?"
  uv run python run.py --input input_examples/critical_hpc.json [--output result.json]
  uv run python run.py --serve            # POST /run {"question": "..."} on RUN_HOST:RUN_PORT

The input may carry "inspections": findings an inspector entered (borescope, visual, oil analysis, chip detector,
NDT; see input_examples/inspection_*.json). They apply to this run; the server assigns each one's ID and entry time.
With "record": true they are also appended to the engine's findings file (.state/inspections/<asset>.jsonl), which
every later run for that engine reads: off unless the server sets API_ALLOW_RECORD=true (403 otherwise). A finding
after the engine's last cycle or dated after the reference date is held back and never recorded.

The HTTP server answers JSON only: bodies up to 64 KB with a valid Content-Length, a socket timeout, at most
RUN_CONCURRENCY runs at a time (503 when busy), 405 for other methods, and no exception internals in a 500.

SAMPLE_MODE=true runs on the committed snapshot with no Zetaris, Nightshift or network access.
"""

import os

# One OpenMP team per request thread on a many-core machine multiplied threads (and memory) per run; cap it before
# LightGBM loads.
os.environ.setdefault("OMP_NUM_THREADS", "4")

import argparse  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402
import sys  # noqa: E402
import threading  # noqa: E402
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer  # noqa: E402
from pathlib import Path  # noqa: E402
from urllib.parse import urlsplit  # noqa: E402

from pydantic import BaseModel, Field, ValidationError  # noqa: E402

from casefile.schemas import InspectionFinding, InspectionFindingIn  # noqa: E402
from graph.runner import execute_run, new_run_id  # noqa: E402
from settings import ROOT, get_settings  # noqa: E402
from tools.inspections import record_finding  # noqa: E402

MAX_BODY = 64 * 1024
log = logging.getLogger("run")


class RunRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    # IDs, entry times and the example flag are the server's; a photo never comes through the API.
    inspections: list[InspectionFindingIn] = Field(default_factory=list, max_length=50)
    record: bool = False            # also append the findings (needs API_ALLOW_RECORD=true on the server)


def error_json(code: str, message: str, run_id: str | None = None) -> dict:
    return {"run_id": run_id, "status": "error", "error": {"code": code, "message": message}}


def health_payload(settings=None) -> dict:
    """The snapshot check, plus the component states the brief page's status strip shows (tools/status.py): mode,
    Zetaris and its AWS sources, the Meterless context service, Nightshift and the CMMS write path. Sample mode checks
    nothing over the network; live mode reuses checks younger than STATUS_TTL_S and waits at most a few seconds for
    a fresh one. No credentials, tokens, hosts or connection strings."""
    from tools import status

    settings = settings or get_settings()
    snapshot = (ROOT / "data" / "snapshot" / "cycles.parquet").exists()
    return {"status": "ok" if snapshot else "degraded", "sample_mode": settings.sample_mode,
            "data": "snapshot" if settings.sample_mode else settings.zetaris_mode, "snapshot": snapshot,
            **status.current(settings, wait_s=settings.status_timeout_s + 1.0)}


def _invalid(error: ValidationError) -> str:
    first = error.errors(include_url=False)[0]
    where = ".".join(str(p) for p in first.get("loc") or ())
    return f"{where}: {first['msg']}" if where else first["msg"]


def handle(payload: object, settings=None, local: bool = False) -> tuple[int, dict]:
    """One request. `local` is the command line (the machine's own user), which may record findings; over HTTP
    recording needs API_ALLOW_RECORD=true."""
    settings = settings or get_settings()
    try:
        request = RunRequest.model_validate(payload)
    except ValidationError as error:
        return 400, error_json("invalid_input", _invalid(error))
    if request.record and request.inspections and not (local or settings.api_allow_record):
        return 403, error_json("record_disabled", "recording inspection findings through the API is turned off on "
                                                  "this server; send them without \"record\" to apply them to one run")
    from data.db import load_pack

    modules = (load_pack().get("inspections") or {}).get("modules") or {}
    unknown = sorted({f.module for f in request.inspections if f.module not in modules})
    if unknown:
        return 400, error_json("invalid_input", f"inspections.module: unknown module {', '.join(unknown)}; one of "
                                                f"{', '.join(modules)}")
    findings = [f.to_finding() for f in request.inspections]
    run_id = new_run_id()
    try:
        outcome = execute_run(request.question, settings, run_id=run_id, inspections=findings)
    except Exception:  # the API contract is structured JSON errors, never a stack trace or exception text
        log.exception("run %s failed", run_id)
        return 500, error_json("run_failed", "the run failed; the server log has the details", run_id)
    result = outcome.result
    if request.record and request.inspections:
        held = {e.content["finding"]["finding_id"] for e in outcome.case.evidence.values()
                if e.kind == "inspection" and e.content.get("quarantined")}
        result["inspections_recorded"] = record_inspections(findings, result, held, settings)
    return (500 if result["status"] == "error" else 200), result


def record_inspections(findings: list[InspectionFinding], result: dict, held: set[str] | None = None,
                       settings=None) -> list[dict]:
    """Append each finding for the engine the run resolved, after the run read it; never rewrite a stored one, and
    never store one the run held back as implausible (after the last cycle, or a future date)."""
    asset = (result.get("asset") or {}).get("asset_id")
    out = []
    for finding in findings:
        reason = ("the run failed" if result["status"] == "error" else
                  f"not about {asset}" if finding.asset_id != asset else
                  "held back as implausible (after the engine's last cycle or the reference date)"
                  if finding.finding_id in (held or set()) else None)
        if reason:
            out.append({"finding_id": finding.finding_id, "recorded": False, "reason": reason})
            continue
        try:
            path = record_finding(settings or get_settings(), finding)
            out.append({"finding_id": finding.finding_id, "recorded": True, "path": str(path.relative_to(ROOT))
                        if path.is_relative_to(ROOT) else str(path)})
        except ValueError as error:
            out.append({"finding_id": finding.finding_id, "recorded": False, "reason": str(error)})
    return out


_slots: threading.BoundedSemaphore | None = None
_slots_lock = threading.Lock()


def run_slots() -> threading.BoundedSemaphore:
    global _slots
    with _slots_lock:
        if _slots is None:
            _slots = threading.BoundedSemaphore(max(1, get_settings().run_concurrency))
        return _slots


class Handler(BaseHTTPRequestHandler):
    timeout = 30                                # socket timeout: slow or short bodies cannot hold a thread forever
    error_content_type = "application/json"

    def version_string(self) -> str:
        return "Cleris"                         # no Python or library version in the Server header

    def _send(self, status: int, body: dict, head: bool = False, headers: dict | None = None) -> None:
        data = json.dumps(body, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if not head:
            self.wfile.write(data)

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        """Protocol errors (bad request line, headers too long, ...) answer JSON too."""
        try:
            self.close_connection = True
            self._send(code, error_json("http_error", message or self.responses.get(code, ("error",))[0]))
        except OSError:
            pass

    def log_message(self, format: str, *args) -> None:          # noqa: A002 - the stdlib's signature
        log.info("%s %s", self.address_string(), format % args)

    def _route(self) -> str:
        return urlsplit(self.path).path

    def do_GET(self) -> None:
        if self._route() == "/health":
            self._send(200, health_payload())
        else:
            self._send(404, error_json("not_found", "no such route"))

    def do_HEAD(self) -> None:
        if self._route() == "/health":
            self._send(200, health_payload(), head=True)
        else:
            self._send(404, error_json("not_found", "no such route"), head=True)

    def _drain(self) -> None:
        """Read a small declared body before an early answer; closing with it unread can reset the client's socket
        before it reads the response (seen on Windows under load). A missing, bad or oversized length just closes."""
        raw = (self.headers.get("Content-Length") or "0").strip()
        if raw.isdigit() and int(raw) <= MAX_BODY:
            try:
                self.rfile.read(int(raw))
                return
            except OSError:
                pass
        self.close_connection = True

    def _not_allowed(self) -> None:
        self._drain()
        self._send(405, error_json("method_not_allowed", "use GET /health or POST /run"),
                   headers={"Allow": "GET, HEAD, POST"})

    do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _not_allowed

    def _body(self) -> tuple[bytes | None, tuple[int, dict] | None]:
        raw = self.headers.get("Content-Length")
        if raw is None:
            return None, (411, error_json("length_required", "Content-Length is required"))
        if not raw.strip().isdigit():
            return None, (400, error_json("invalid_length", "Content-Length must be a non-negative integer"))
        size = int(raw)
        if size > MAX_BODY:
            return None, (413, error_json("too_large", f"the body is over {MAX_BODY} bytes"))
        return self.rfile.read(size), None

    def do_POST(self) -> None:
        if self._route() != "/run":
            self._drain()
            self._send(404, error_json("not_found", "no such route"))
            return
        try:
            body, problem = self._body()
        except (TimeoutError, OSError):
            self.close_connection = True
            return
        if problem:
            self.close_connection = True
            self._send(*problem)
            return
        try:
            payload = json.loads(body.decode("utf-8") or "null")
        except (UnicodeDecodeError, ValueError, RecursionError) as error:   # JSONDecodeError is a ValueError
            self._send(400, error_json("invalid_json", f"the body is not valid UTF-8 JSON ({type(error).__name__})"))
            return
        slots = run_slots()
        if not slots.acquire(timeout=0.5):
            self._send(503, error_json("busy", "too many runs in progress; retry shortly"), headers={"Retry-After": "5"})
            return
        try:
            self._send(*handle(payload))
        finally:
            slots.release()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(name)s %(message)s", stream=sys.stderr)
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--question")
    source.add_argument("--input", help="JSON file with {\"question\": ...} and optional \"inspections\"")
    source.add_argument("--serve", action="store_true")
    parser.add_argument("--output", help="write the result JSON here as well as stdout")
    args = parser.parse_args()

    if args.serve:
        settings = get_settings()
        host = settings.run_host
        if Path("/.dockerenv").exists() and host in {"127.0.0.1", "localhost"}:
            host = "0.0.0.0"  # a container bound only to loopback is unreachable via the published port
        server = ThreadingHTTPServer((host, settings.run_port), Handler)
        server.daemon_threads = True
        logging.info("serving POST /run on %s:%s (sample_mode=%s)", host, settings.run_port, settings.sample_mode)
        server.serve_forever()
        return 0

    payload = {"question": args.question} if args.question else json.loads(open(args.input, encoding="utf-8").read())
    status, body = handle(payload, local=True)
    text = json.dumps(body, indent=1, default=str)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle_out:
            handle_out.write(text + "\n")
    print(text)
    return 0 if status == 200 else 1


if __name__ == "__main__":
    sys.exit(main())
