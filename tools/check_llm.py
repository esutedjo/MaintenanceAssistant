"""Check the model provider: Nightshift state, then short requests through each model role's own Nightshift app
(critic, intake, Ask Cleris), showing which model and provider answered, how fast, what it cost and the receipt.
Run: uv run task check-llm            (one request per role)
     uv run task check-llm --repeat 5 (five per role: a quick read on whether each provider keeps answering)"""

import argparse
import logging
import statistics
import sys

from settings import get_settings
from tools.llm import LlmClient, LlmRefusal, LlmUnavailable

ROLES = {"critic": "the critic (should be Nemotron)", "intake": "free-text intake", "explain": "Ask Cleris"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repeat", type=int, default=1, help="requests per role")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="[%(levelname)s] %(name)s %(message)s")
    settings = get_settings()
    client = LlmClient(settings)
    failed = 0
    try:
        state, modes = client.status()
        print(f"Nightshift at {client.base_url}: {state}" + (f" ({len(modes)} routing modes)" if modes else ""))
        if not client.configured:
            print("METERLESS_RUNTIME_TOKEN is not set; copy it from Nightshift's Console into .env.local.")
            return 1
        for role, what in ROLES.items():
            app, model = settings.lane(role)
            replies, errors = [], []
            for _ in range(max(1, args.repeat)):
                try:
                    replies.append(client.chat([{"role": "user", "content": "Reply with the single word OK."}],
                                               model=model, max_tokens=16, app=app,
                                               timeout_s=settings.critic_timeout_s))
                except (LlmRefusal, LlmUnavailable) as error:
                    errors.append(f"{type(error).__name__}: {str(error)[:120]}")
            served = sorted({f"{r.model} ({r.route_provider or '?'})" for r in replies})
            line = f"{role:8} {what}: app {app}, {len(replies)} of {len(replies) + len(errors)} answered"
            if replies:
                cost = sum(r.cost_usd or 0 for r in replies)
                line += (f"; served by {', '.join(served)}; median {statistics.median(r.duration_s for r in replies):.1f}"
                         f" s; cost ${cost:.4f}; last receipt {replies[-1].receipt_id or 'none'}")
            print(line + "".join(f"\n         {e}" for e in errors[:3]))
            failed += bool(errors)
            if role == "critic" and replies and not all("nemotron" in r.model.lower() for r in replies):
                print("         the critic lane is not Nemotron: pin turbofan-maintenance-critic to the nemotron lane")
                failed += 1
        return 1 if failed else 0
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
