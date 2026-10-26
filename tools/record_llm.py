"""Capture Nemotron replies for sample mode. Run: uv run task record-llm

Builds the four golden briefs (input_examples/) on the committed snapshot in sample mode, plus one free-text intake
question, and sends each prompt those runs reach (the critic's, and the intake's for the free-text question) once
through the app's own Nightshift client with the critic app and model. Writes the reply text, the served model, the
prompt hash and the capture time to data/snapshot/llm_recordings.json. Never writes tokens, headers or settings.

Only prompts without a matching recording are sent, so a re-run after a failed call or a changed prompt costs just
those calls; recordings no run reaches any more are dropped. A prompt whose call fails twice, or whose reply is not
the JSON the code reads, is reported and not recorded, so sample mode keeps the deterministic path for it."""

from __future__ import annotations

import json
import logging
import sys
import tempfile
import time
from pathlib import Path

from agents.critic import _parse
from agents.intake import _json
from graph.runner import execute_run
from settings import ROOT, Settings, get_settings
from tools import llm_recordings as recordings
from tools.llm import LlmClient, LlmRefusal, LlmUnavailable

FREE_TEXT = "Should we pull the engine with serial 700131?"      # tests/test_intake_llm.py's free-text example
MAX_TOKENS = {"critic": 1024, "intake": 200}                      # as agents/critic.py and agents/intake.py send


def golden_questions() -> list[str]:
    names = ("healthy_engine.json", "critical_hpc.json", "fan_degradation.json", "methods_disagree.json")
    return [json.loads((ROOT / "input_examples" / n).read_text(encoding="utf-8"))["question"] for n in names]


def main() -> int:
    logging.basicConfig(level=logging.WARNING, format="[%(levelname)s] %(name)s %(message)s")
    live = get_settings()                       # the app's own settings; nothing from them is printed or stored
    client = LlmClient(live)
    captured: list[recordings.Recording] = []
    problems: list[str] = []
    question = ""

    def call(purpose: str, prompt: str) -> recordings.Recording | None:
        for attempt in (1, 2):                  # one retry: Nightshift's provider answers 502 now and then
            try:
                app, model = live.lane(purpose)
                reply = client.chat([{"role": "user", "content": prompt}], model=model,
                                    temperature=0, max_tokens=MAX_TOKENS[purpose], app=app,
                                    response_format={"type": "json_object"},
                                    timeout_s=max(live.critic_timeout_s, 60.0))
                (_parse if purpose == "critic" else _json)(reply.text)
                break
            except (LlmUnavailable, LlmRefusal, ValueError) as error:
                failure = f"{type(error).__name__}: {str(error)[:200]}"
            except Exception as error:           # a transport timeout and the like: report, record nothing
                failure = type(error).__name__
            if attempt == 2:
                problems.append(f"{purpose} for {question!r}: {failure}")
                return None
            time.sleep(5)
        found = recordings.Recording(purpose=purpose, case=question, prompt_sha256=recordings.prompt_hash(prompt),
                                     model=reply.model, captured_at=recordings.utc_now(), text=reply.text)
        captured.append(found)
        print(f"  {purpose}: {reply.model} in {reply.duration_s:.1f} s for {question!r}", flush=True)
        return found

    state, _ = client.status()
    print(f"Nightshift: {state}")
    if state != "online":
        print("Nightshift is not online with a valid token; sample mode stays deterministic.")
        return 1
    existing = recordings.load()
    recordings.capture, recordings.used = call, set()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            sample = Settings(_env_file=None, sample_mode=True, context_sqlite_path=Path(tmp) / "context.sqlite")
            for n, question in enumerate([*golden_questions(), FREE_TEXT], start=1):
                execute_run(question, sample, run_id=f"record-llm-{n}")
                (ROOT / "logs" / f"trace-record-llm-{n}.jsonl").unlink(missing_ok=True)
        kept = [existing[h] for h in existing if h in recordings.used]
    finally:
        recordings.capture, recordings.used = None, None
        client.close()
    if captured or len(kept) != len(existing):
        recordings.save([*kept, *captured])
    print(f"{len(kept)} recording(s) still match, {len(captured)} captured, {len(existing) - len(kept)} stale dropped; "
          f"{recordings.RECORDINGS.relative_to(ROOT)}")
    for problem in problems:
        print(f"not recorded: {problem}")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
