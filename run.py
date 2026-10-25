"""Entry point. JSON in, JSON out.

  uv run python run.py --question "How long can FD003-A100 keep flying?"
  uv run python run.py --input input_examples/critical_hpc.json [--output result.json]
  uv run python run.py --serve            # POST /run {"question": "..."} on RUN_HOST:RUN_PORT

SAMPLE_MODE=true runs on the committed snapshot with no Zetaris, Nightshift or network access.
"""

import argparse
import json
import logging
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from graph.runner import new_run_id, run_case
from settings import ROOT, get_settings


class RunRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)


def error_json(code: str, message: str, run_id: str | None = None) -> dict:
    return {"run_id": run_id, "status": "error", "error": {"code": code, "message": message}}


def health_payload() -> dict:
    settings = get_settings()
    snapshot = (ROOT / "data" / "snapshot" / "cycles.parquet").exists()
    return {"status": "ok" if snapshot else "degraded", "sample_mode": settings.sample_mode,
            "data": "snapshot" if settings.sample_mode else settings.zetaris_mode, "snapshot": snapshot}


def handle(payload: object) -> tuple[int, dict]:
    try:
        request = RunRequest.model_validate(payload)
    except ValidationError as error:
        return 400, error_json("invalid_input", error.errors(include_url=False)[0]["msg"])
    run_id = new_run_id()
    try:
        result = run_case(request.question, run_id=run_id)
    except Exception as error:  # the API contract is structured JSON errors, never a stack trace
        logging.getLogger("run").exception("run %s failed", run_id)
        return 500, error_json("run_failed", f"{type(error).__name__}: {error}"[:500], run_id)
    return (500 if result["status"] == "error" else 200), result


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: dict) -> None:
        data = json.dumps(body, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send(200, health_payload())
        else:
            self._send(404, error_json("not_found", f"no route GET {self.path}"))

    def do_POST(self) -> None:
        if self.path != "/run":
            self._send(404, error_json("not_found", f"no route POST {self.path}"))
            return
        try:
            payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"null")
        except json.JSONDecodeError as error:
            self._send(400, error_json("invalid_json", str(error)))
            return
        self._send(*handle(payload))


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(name)s %(message)s", stream=sys.stderr)
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--question")
    source.add_argument("--input", help="JSON file with {\"question\": ...}")
    source.add_argument("--serve", action="store_true")
    parser.add_argument("--output", help="write the result JSON here as well as stdout")
    args = parser.parse_args()

    if args.serve:
        settings = get_settings()
        host = settings.run_host
        if Path("/.dockerenv").exists() and host in {"127.0.0.1", "localhost"}:
            host = "0.0.0.0"  # a container bound only to loopback is unreachable via the published port
        server = ThreadingHTTPServer((host, settings.run_port), Handler)
        logging.info("serving POST /run on %s:%s (sample_mode=%s)", host, settings.run_port, settings.sample_mode)
        server.serve_forever()
        return 0

    payload = {"question": args.question} if args.question else json.loads(open(args.input, encoding="utf-8").read())
    status, body = handle(payload)
    text = json.dumps(body, indent=1, default=str)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle_out:
            handle_out.write(text + "\n")
    print(text)
    return 0 if status == 200 else 1


if __name__ == "__main__":
    sys.exit(main())
