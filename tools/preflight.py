"""Preflight before recording a live demo: every service the live brief depends on, then one real live brief.
Read-only (nothing is approved or written). Prints PASS / WARN / FAIL per check. Run: uv run task preflight"""

from __future__ import annotations

import sys
import time

import httpx

from settings import get_settings

GOLDEN = ("FD001-A031", "FD003-A100", "FD001-A070", "FD001-A001")
results: list[tuple[str, str, str]] = []


def report(level: str, check: str, detail: str) -> None:
    results.append((level, check, detail))
    print(f"{level:4} {check}: {detail}", flush=True)


def main() -> int:
    settings = get_settings()
    report("PASS" if not settings.sample_mode else "FAIL", "mode",
           f"SAMPLE_MODE={settings.sample_mode}, ZETARIS_MODE={settings.zetaris_mode}")
    if settings.zetaris_mode != "live":
        report("WARN", "zetaris mode", "ZETARIS_MODE is not live; the brief will read the snapshot")

    from tools.zetaris import ZetarisClient
    try:
        client = ZetarisClient(settings)
        start = time.monotonic()
        rows = client.run_select("SELECT COUNT(*) AS n FROM telemetry_pg.cycles")["data"][0]
        drafts = client.run_select("SELECT asset_id, COUNT(*) AS n FROM cmms_pg.work_orders WHERE status = 'draft' "
                                   f"AND asset_id IN ({', '.join(repr(a) for a in GOLDEN)}) GROUP BY asset_id")["data"]
        client.close()
        report("PASS", "zetaris", f"{rows[0] if isinstance(rows, list) else rows} telemetry rows in "
                                   f"{time.monotonic() - start:.1f} s")
        if drafts:
            report("WARN", "draft work orders", f"golden engines already have drafts {drafts}; they are left out of "
                                                "history, but the CMMS shows them")
    except Exception as error:
        report("FAIL", "zetaris", f"{type(error).__name__}: {str(error)[:160]}")

    try:
        health = httpx.get(settings.context_service_url.rstrip("/") + "/health", timeout=3).json()
        documents = health.get("world", {}).get("entities", {}).get("document", 0)
        report("PASS" if documents else "WARN", "context service",
               f"{health.get('world', {}).get('relations')} relations, {documents} document nodes, "
               f"{health.get('hmem', {}).get('memories')} memories"
               + ("" if documents else "; run uv run task load-context"))
    except Exception as error:
        report("FAIL", "context service", f"{type(error).__name__}; start it with uv run task context-service")

    from tools.llm import LlmClient
    llm = LlmClient(settings)
    try:
        state, ids = llm.status()
        report("PASS" if state == "online" else "FAIL", "nightshift", f"{state}, {len(ids)} routing modes")
        start = time.monotonic()
        reply = llm.chat([{"role": "user", "content": 'Reply with the JSON {"ok": true} and nothing else.'}],
                         model=settings.meterless_critic_model, max_tokens=32, app=settings.meterless_critic_app,
                         response_format={"type": "json_object"}, timeout_s=settings.critic_timeout_s)
        nemotron = "nemotron" in reply.model.lower()
        report("PASS" if nemotron else "WARN", "critic lane",
               f"{settings.meterless_critic_app} answered by {reply.model} in {time.monotonic() - start:.1f} s"
               + ("" if nemotron else "; pin the lane to a Nemotron model in Nightshift"))
    except Exception as error:
        report("FAIL", "nightshift", f"{type(error).__name__}: {str(error)[:160]}")
    finally:
        llm.close()

    from graph.runner import execute_run
    start = time.monotonic()
    try:
        case = execute_run("What should I do about FD001-A031?", settings, run_id="preflight-FD001-A031").case
        critic = [v.model for v in case.verdicts if v.gate == "critic" and v.model]
        ok = case.status == "needs_review" and not case.degraded and critic
        report("PASS" if ok else "WARN", "live brief FD001-A031",
               f"{case.status} in {time.monotonic() - start:.1f} s; critic {critic or 'deterministic only'}; "
               f"degraded {case.degraded or 'none'}")
    except Exception as error:
        report("FAIL", "live brief FD001-A031", f"{type(error).__name__}: {str(error)[:160]}")

    failed = [r for r in results if r[0] == "FAIL"]
    warned = [r for r in results if r[0] == "WARN"]
    print("\nReady to record." if not failed and not warned else
          f"\n{len(failed)} failed, {len(warned)} warning(s). Fix them, or run again: Zetaris stalls now and then.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
