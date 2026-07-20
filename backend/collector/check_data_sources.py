"""CLI for lightweight sector data-source health checks."""

from __future__ import annotations

import argparse
import json

from backend.expectation_gap.database import connect, migrate
from backend.sector_radar.health_checks import CHECK_ORDER, run_health_checks


def main() -> int:
    parser = argparse.ArgumentParser(description="检查行业数据源健康状态")
    parser.add_argument("--source", choices=(*CHECK_ORDER, "all"), default="all")
    args = parser.parse_args()
    connection = connect()
    try:
        migrate(connection)
        results = run_health_checks(connection, args.source)
        print(json.dumps(results, ensure_ascii=False, indent=2))
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
