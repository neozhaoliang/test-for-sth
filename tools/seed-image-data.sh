#!/usr/bin/env bash
# Docker bootstrap: copy the immutable data snapshot baked into the image into
# a new/empty persistent data volume. Never overwrite existing research data.
set -Eeuo pipefail

seed_dir="${IMAGE_DATA_SEED_DIR:-/opt/image-data}"
data_dir="${RUNTIME_DATA_DIR:-/app/data}"

if [[ ! -d "$seed_dir" ]]; then
  echo "[Data] Image data seed is missing: $seed_dir" >&2
  exit 1
fi
if [[ -z "$(find "$seed_dir" -type f -name '*.jsonl' -print -quit)" ]]; then
  echo "[Data] Image data seed contains no JSONL files: $seed_dir" >&2
  exit 1
fi

mkdir -p "$data_dir"
if [[ -z "$(find "$data_dir" -type f -print -quit)" ]]; then
  echo "[Data] Data volume empty; importing versioned image snapshot..."
  # Do not preserve build-time root ownership: the app runs as UID 10001.
  cp -R --no-preserve=ownership "$seed_dir"/. "$data_dir"/
  echo "[Data] Imported $(find "$data_dir" -type f -name '*.jsonl' | wc -l) JSONL files."
else
  echo "[Data] Data volume already contains files; keeping existing data unchanged."
  echo "[Data] Available $(find "$data_dir" -type f -name '*.jsonl' | wc -l) JSONL files."
fi
