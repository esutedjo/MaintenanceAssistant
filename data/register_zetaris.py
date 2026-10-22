"""Register seeded tables in Zetaris and check each data source returns rows. Run: uv run task register"""

import sys

from settings import get_settings
from tools.zetaris import ZetarisClient

CHECKS = {
    "telemetry_pg": ["cycles", "signals", "telemetry_long"],
    "cmms_pg": ["assets", "work_orders", "findings", "parts_replaced", "planning_parts", "planning_stock",
                "planning_hangar_slots", "planning_flight_schedule"],
    "s3_files": ["fmea"],
}
# File sources are registered per file in the Zetaris UI; only JDBC sources can re-import their tables.
JDBC_SOURCES = {"telemetry_pg", "cmms_pg"}


def main() -> int:
    client = ZetarisClient(get_settings())
    ok = True
    try:
        for source, tables in CHECKS.items():
            if source in JDBC_SOURCES:
                client.register_tables(source)
                print(f"registered tables from {source}")
            for table in tables:
                try:
                    result = client.run_select(f"SELECT COUNT(*) AS n FROM {source}.{table}")
                    print(f"  {source}.{table}: {result['data']}")
                except Exception as error:
                    ok = False
                    print(f"  {source}.{table}: FAIL {type(error).__name__}: {error}")
    finally:
        client.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
