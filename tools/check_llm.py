"""Check the model provider: Nightshift state, then one short request showing which model answered.
Run: uv run task check-llm"""

import logging
import sys

from settings import get_settings
from tools.llm import LlmClient, LlmRefusal, LlmUnavailable


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(name)s %(message)s")
    settings = get_settings()
    client = LlmClient(settings)
    try:
        state, modes = client.status()
        print(f"Nightshift at {client.base_url}: {state}" + (f" ({', '.join(modes)})" if modes else ""))
        if not client.configured:
            print("METERLESS_RUNTIME_TOKEN is not set; copy it from Nightshift's Console into .env.local.")
        try:
            result = client.chat([{"role": "user", "content": "Reply with the single word OK."}], max_tokens=16)
        except (LlmRefusal, LlmUnavailable) as error:
            print(f"FAIL {type(error).__name__}: {error}")
            return 1
        print(f"provider={result.provider} requested={result.requested_model} model={result.model} "
              f"duration={result.duration_s}s reply={result.text.strip()[:60]!r}")
        if result.fallback_reason:
            print(f"fallback: {result.fallback_reason}")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
