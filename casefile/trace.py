"""Writes trace events to logs/trace-<run_id>.jsonl and a readable line to stdout."""

import logging
import threading
from pathlib import Path

from casefile.schemas import TraceEvent
from settings import ROOT

LOG_DIR = ROOT / "logs"
log = logging.getLogger("trace")


class TraceWriter:
    def __init__(self, run_id: str, log_dir: Path = LOG_DIR):
        log_dir.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id
        self.path = log_dir / f"trace-{run_id}.jsonl"
        # Parallel agents share this writer. On Windows, appends from separate file handles are not atomic, so two
        # lines written at once could overwrite each other; one write at a time keeps every line whole.
        self._lock = threading.Lock()

    def write(self, event: TraceEvent) -> TraceEvent:
        # `recorded` appears only on lines that read a model reply (True: a sample-mode replay, False: live).
        line = event.model_dump_json(exclude={"recorded"} if event.recorded is None else None) + "\n"
        with self._lock, self.path.open("a", encoding="utf-8") as handle:
            handle.write(line)
        target = f" -> {event.target_agent}" if event.target_agent else ""
        model = f" (model: {event.model}{', recorded reply' if event.recorded else ''})" if event.model else ""
        retry = f" retry={event.retry_count}" if event.retry_count else ""
        log.info("run_id=%s %s%s %s [%s]%s%s: %s", self.run_id, event.agent_name, target, event.action,
                 event.status, retry, model, event.output_summary)
        return event
