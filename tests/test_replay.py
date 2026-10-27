"""The no-install replay (tools/replay.py, docs/index.html): the committed runs are live and complete, the page embeds
every run and matches a fresh build, and no run file carries a URL or an AWS host."""

import json
import re

from tools import replay


def test_committed_replay_runs_are_live_and_complete():
    bundle = replay.load_runs()
    assert [run["key"] for run in bundle["runs"]] == [key for key, _, _ in replay.CASES]
    for run in bundle["runs"]:
        result, actions = run["result"], {line["action"] for line in run["trace"]}
        assert result["mode"] == {"sample_mode": False, "data": "live", "context_store": "meterless"}
        assert result["status"] == "needs_review" and not result["degraded"]
        assert any("nemotron" in call["model"] and not call["recorded"] for call in result["model_calls"])
        assert {"zetaris_query", "context_call"} <= actions
        assert all(line["output_summary"].endswith("live Zetaris SQL") for line in run["trace"]
                   if line["action"] == "zetaris_query")
    assert bundle["health"]["health"]["overall"] == "ok"


def test_page_embeds_every_run_and_matches_a_fresh_build(tmp_path):
    page = replay.build(page=tmp_path / "index.html")
    html = page.read_text(encoding="utf-8")
    data = json.loads(re.search(r'<script id="data" type="application/json">(.*?)</script>', html, re.DOTALL).group(1))
    assert len(data["runs"]) == len(replay.CASES) and (tmp_path / ".nojekyll").exists()
    text = re.sub(r"<[^>]+>", " ", re.sub(r"<script.*?</script>", "", html, flags=re.DOTALL))
    assert "Live runs in this replay" in text and "live Zetaris SQL reads" in text, "no summary without JavaScript"
    assert all(run["result"]["asset"]["asset_id"] in text for run in data["runs"])
    assert replay.PAGE.read_text(encoding="utf-8") == html, "docs/index.html is stale: run uv run task replay"


def test_run_files_hold_no_url_or_aws_host():
    for path in replay.DATA.iterdir():
        text = path.read_text(encoding="utf-8")
        assert "://" not in text and "amazonaws" not in text and ".rds." not in text, path.name
