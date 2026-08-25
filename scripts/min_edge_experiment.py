#!/usr/bin/env python3
"""P2.3 MIN_EDGE experiment — read-only analysis when near-miss > 0."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.strategies.min_edge_experiment import EXPERIMENT_PATH, run_and_persist


def main() -> int:
    result = run_and_persist()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"\nWritten: {EXPERIMENT_PATH}")
    if result.get("status") == "skipped":
        print(result.get("reason", ""))
        return 0
    passes = result.get("passes_at_threshold") or {}
    print("Pass counts:", passes)
    print("Recommendation:", result.get("recommendation"))
    print("Note:", result.get("note"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
