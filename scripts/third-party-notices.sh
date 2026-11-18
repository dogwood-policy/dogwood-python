#!/usr/bin/env bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Write THIRD-PARTY-NOTICES: attribution for every third-party crate compiled
# into the native extension, on any platform a wheel is built for.
#
#   1. cargo-about renders, from Cargo.lock, the crate list and the full text
#      of each license, for the targets in about.toml (with about.hbs). It runs
#      offline and fails if any crate's license cannot be read or is not in
#      about.toml's accepted list.
#   2. Every NOTICE file those crates ship (NOTICE, NOTICE.md, NOTICES.md, ...)
#      is appended verbatim, as Apache-2.0 section 4(d) requires.
#
# Requires cargo-about (the version pinned in .github/workflows) and python3.
# The crate sources must already be downloaded (`cargo fetch --locked`).
#
# Usage: scripts/third-party-notices.sh [OUTPUT]   (default: THIRD-PARTY-NOTICES)
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
out="${1:-$root/THIRD-PARTY-NOTICES}"
cd "$root"

command -v cargo-about >/dev/null || { echo "error: cargo-about is not installed" >&2; exit 1; }

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

cargo about generate --locked --offline --fail about.hbs -o "$tmp/licenses.txt"

# The crates cargo-about listed, as "name version" lines.
awk '/^Crates included$/ { getline; inlist = 1; next }
     inlist && /^=+$/ { exit }
     inlist && NF >= 2 { print $1, $2 }' "$tmp/licenses.txt" > "$tmp/crates.txt"
[[ -s "$tmp/crates.txt" ]] || { echo "error: no crates found in cargo-about output" >&2; exit 1; }

cargo metadata --locked --offline --format-version 1 > "$tmp/metadata.json"

# Append each listed crate's own NOTICE files, in crate order.
python3 - "$tmp/metadata.json" "$tmp/crates.txt" "$tmp/notices.txt" <<'PY'
import json, os, re, sys
metadata_file, crates_file, out_file = sys.argv[1:]
packages = json.load(open(metadata_file, encoding="utf-8"))["packages"]
by_id = {f"{p['name']} {p['version']}": p for p in packages}
text = ""
for line in filter(None, open(crates_file, encoding="utf-8").read().split("\n")):
    pkg = by_id.get(line)
    if pkg is None:
        sys.exit(f"crate {line} listed by cargo-about is not in cargo metadata")
    d = os.path.dirname(pkg["manifest_path"])
    for f in sorted(os.listdir(d)):
        if not re.fullmatch(r"notices?(\.[a-z]+)?", f, re.IGNORECASE):
            continue
        text += "\n------------------------------------------------------------------------\n"
        text += f"NOTICE file of {line} ({f})\n"
        text += "------------------------------------------------------------------------\n\n"
        text += open(os.path.join(d, f), encoding="utf-8").read().rstrip() + "\n"
open(out_file, "w", encoding="utf-8").write(text)
PY

{
  cat "$tmp/licenses.txt"
  if [[ -s "$tmp/notices.txt" ]]; then
    printf '\n========================================================================\n'
    printf 'NOTICE files from third-party crates\n'
    printf '========================================================================\n'
    cat "$tmp/notices.txt"
  fi
} > "$out"

echo "ok: $(wc -l < "$tmp/crates.txt" | tr -d ' ') crates, $(grep -c '^NOTICE file of ' "$out" || true) NOTICE file(s) -> $out"
