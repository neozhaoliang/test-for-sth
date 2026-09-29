# -*- coding: utf-8 -*-
"""
Inspect and verify frozen investment-research snapshots.

Examples:
    python tools/snapshot_cli.py verify data/investment_snapshots/600036/2026-09-29/<snapshot_id>
    python tools/snapshot_cli.py show data/investment_snapshots/600036/2026-09-29/<snapshot_id>
    python tools/snapshot_cli.py inputs data/investment_snapshots/600036/2026-09-29/<snapshot_id>
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from analysis.snapshot_replay import replay_snapshot
from analysis.snapshot_store import (
    load_snapshot_manifest,
    load_snapshot_research_inputs,
    verify_snapshot_integrity,
)


def _dump(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["verify", "show", "inputs", "replay"])
    parser.add_argument("path")
    args = parser.parse_args()

    path = Path(args.path)
    if args.command == "verify":
        result = verify_snapshot_integrity(path)
        _dump(result)
        raise SystemExit(0 if result.get("ok") else 1)

    if args.command == "show":
        manifest = load_snapshot_manifest(path)
        _dump(manifest.model_dump(mode="json"))
        return

    if args.command == "inputs":
        _dump(load_snapshot_research_inputs(path))
        return

    if args.command == "replay":
        report = asyncio.run(replay_snapshot(path))
        _dump(report.model_dump(mode="json"))
        return


if __name__ == "__main__":
    main()
