from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.config import get_config
from backend.data.financial_snapshot import capture_vendor_research_symbol
from backend.data.vci_client import VCIClient


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Capture one Vietcap financial history as quarantined research "
            "data without changing the active financial dataset."
        )
    )
    parser.add_argument("symbol")
    args = parser.parse_args()
    config = get_config()
    with VCIClient(config.vci.rate_limit_rpm) as client:
        result = capture_vendor_research_symbol(
            config.db_path, args.symbol, client
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
