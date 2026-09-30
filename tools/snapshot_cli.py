# -*- coding: utf-8 -*-
"""
Inspect, verify, replay and compare frozen investment-research snapshots.

Examples:
    python tools/snapshot_cli.py verify data/investment_snapshots/600036/2026-09-29/<snapshot_id>
    python tools/snapshot_cli.py show data/investment_snapshots/600036/2026-09-29/<snapshot_id>
    python tools/snapshot_cli.py inputs data/investment_snapshots/600036/2026-09-29/<snapshot_id>
    python tools/snapshot_cli.py replay <snapshot_id> --out artifacts/replay-a.json
    python tools/snapshot_cli.py replay-external <snapshot_id> --synthesis external.json --out artifacts/replay-external.json
    python tools/snapshot_cli.py compare artifacts/replay-a.json artifacts/replay-b.json --out artifacts/replay-diff.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from analysis.external_synthesis import load_external_synthesis, make_external_synthesis_fn
from analysis.snapshot_compare import compare_replayed_reports, load_report_json
from analysis.snapshot_replay import replay_snapshot
from analysis.snapshot_store import (
    load_snapshot_manifest,
    load_snapshot_research_inputs,
    verify_snapshot_integrity,
)


def _dump(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _write_or_dump(value, output: str) -> None:
    if not output:
        _dump(value)
        return
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(str(path))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=["verify", "show", "inputs", "replay", "replay-external", "compare"],
    )
    parser.add_argument("path")
    parser.add_argument("path_b", nargs="?")
    parser.add_argument("--out", default="")
    parser.add_argument("--synthesis", default="")
    args = parser.parse_args()

    path = Path(args.path)
    if args.command == "verify":
        result = verify_snapshot_integrity(path)
        _write_or_dump(result, args.out)
        raise SystemExit(0 if result.get("ok") else 1)

    if args.command == "show":
        manifest = load_snapshot_manifest(path)
        _write_or_dump(manifest.model_dump(mode="json"), args.out)
        return

    if args.command == "inputs":
        _write_or_dump(load_snapshot_research_inputs(path), args.out)
        return

    if args.command == "replay":
        report = asyncio.run(replay_snapshot(path))
        _write_or_dump(report.model_dump(mode="json"), args.out)
        return

    if args.command == "replay-external":
        if not args.synthesis:
            parser.error("replay-external requires --synthesis <json>")
        envelope = load_external_synthesis(args.synthesis)
        report = asyncio.run(
            replay_snapshot(
                path,
                synthesis_fn=make_external_synthesis_fn(envelope),
                replay_metadata=envelope.replay_metadata(),
            )
        )
        _write_or_dump(report.model_dump(mode="json"), args.out)
        return

    if args.command == "compare":
        if not args.path_b:
            parser.error("compare requires two replay report JSON paths")
        result = compare_replayed_reports(
            load_report_json(path),
            load_report_json(args.path_b),
        )
        _write_or_dump(result, args.out)
        return


if __name__ == "__main__":
    main()
