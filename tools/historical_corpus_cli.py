# -*- coding: utf-8 -*-
"""Inspect and verify immutable historical public-data source bundles."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from analysis.historical_corpus import (
    load_historical_source_manifest,
    verify_historical_source_bundle,
)


def _bundle_base(path: Path) -> Path:
    return path if path.is_dir() else path.parent


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ("verify", "show", "sources", "diagnostics"):
        p = sub.add_parser(name)
        p.add_argument("path")

    args = parser.parse_args()
    path = Path(args.path)

    if args.command == "verify":
        result = verify_historical_source_bundle(path)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        raise SystemExit(0 if result.get("ok") else 1)

    manifest = load_historical_source_manifest(path)
    if args.command == "show":
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return

    base = _bundle_base(path)
    logical = "sources" if args.command == "sources" else "diagnostics"
    filename = (manifest.get("files") or {}).get(logical, f"{logical}.json")
    payload = json.loads((base / filename).read_text(encoding="utf-8"))
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
