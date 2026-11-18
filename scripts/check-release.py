#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Check the release artifacts in DIST against the release tag.

Run by .github/workflows/release.yml after every wheel and the sdist are built
and tested, before anything is published; safe to run locally on a dist/.

Fails (non-zero) unless:
  - the tag is vMAJOR.MINOR.PATCH[-PRERELEASE] and the crate version in
    crates/dogwood-python/Cargo.toml equals it (without the leading `v`; a
    prerelease is written the Rust way, 1.3.0-rc.1, and PyPI shows 1.3.0rc1);
  - DIST holds exactly one sdist and one abi3 wheel per release platform,
    all named aws-dogwood at that version;
  - every wheel and the sdist carry LICENSE, NOTICE and THIRD-PARTY-NOTICES
    (Apache-2.0 section 4) and their metadata names this repository.

Usage: scripts/check-release.py vX.Y.Z DIST
"""

import email
import re
import sys
import tarfile
import tomllib
import zipfile
from pathlib import Path

NAME = "aws-dogwood"
DIST_NAME = NAME.replace("-", "_")
REPO = "https://github.com/dogwood-policy/dogwood-python"
LICENSE_FILES = ["LICENSE", "NOTICE", "THIRD-PARTY-NOTICES"]
# One abi3 wheel per platform built by release.yml (the `wheels` matrix).
PLATFORMS = {
    "manylinux x86_64": r"manylinux_2_17_x86_64",
    "manylinux aarch64": r"manylinux_2_17_aarch64",
    "musllinux x86_64": r"musllinux_1_2_x86_64",
    "musllinux aarch64": r"musllinux_1_2_aarch64",
    "macOS x86_64": r"macosx_\d+_\d+_x86_64",
    "macOS arm64": r"macosx_\d+_\d+_arm64",
    "Windows x64": r"win_amd64",
    "Windows arm64": r"win_arm64",
}

root = Path(__file__).resolve().parent.parent
errors = []


def err(msg):
    errors.append(msg)


def pep440(version):
    """1.3.0-rc.1 -> 1.3.0rc1 (how maturin and PyPI spell a Cargo prerelease)."""
    m = re.fullmatch(r"(\d+\.\d+\.\d+)(?:-(alpha|beta|rc)\.?(\d+))?", version)
    if not m:
        return None
    base, pre, n = m.groups()
    return base + ({"alpha": "a", "beta": "b", "rc": "rc"}[pre] + n if pre else "")


def check_metadata(label, text):
    md = email.message_from_string(text)
    if md.get("Name") != NAME:
        err(f"{label}: Name is {md.get('Name')!r}, expected {NAME!r}")
    if md.get("Version") != want:
        err(f"{label}: Version is {md.get('Version')!r}, expected {want!r}")
    urls = md.get_all("Project-URL") or []
    if not any(u.split(",", 1)[-1].strip() == REPO for u in urls):
        err(f"{label}: no Project-URL for {REPO}")
    for f in LICENSE_FILES:
        if f not in (md.get_all("License-File") or []):
            err(f"{label}: License-File {f} not declared")


def main():
    global want
    if len(sys.argv) != 3:
        sys.exit(__doc__.strip().splitlines()[-1])
    tag, dist = sys.argv[1], Path(sys.argv[2])

    m = re.fullmatch(r"v(\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)", tag)
    if not m:
        sys.exit(f"error: tag {tag} is not vMAJOR.MINOR.PATCH[-PRERELEASE]")
    cargo = tomllib.loads((root / "crates/dogwood-python/Cargo.toml").read_text())
    cargo_version = cargo["package"]["version"]
    if cargo_version != m.group(1):
        err(f"Cargo.toml version {cargo_version} does not match tag {tag}")
    want = pep440(m.group(1))
    if want is None:
        sys.exit(f"error: {m.group(1)} is not X.Y.Z or X.Y.Z-(alpha|beta|rc).N")

    for f in LICENSE_FILES:
        if not (root / f).is_file() or (root / f).stat().st_size == 0:
            err(f"{f} is missing from the repository root")

    files = sorted(p for p in dist.iterdir() if p.is_file())
    wheels = [p for p in files if p.suffix == ".whl"]
    sdists = [p for p in files if p.name.endswith(".tar.gz")]
    for p in files:
        if p not in wheels and p not in sdists:
            err(f"unexpected file in {dist}: {p.name}")

    # sdist
    if len(sdists) != 1:
        err(f"expected 1 sdist, found {len(sdists)}")
    for p in sdists:
        if p.name != f"{DIST_NAME}-{want}.tar.gz":
            err(f"sdist is named {p.name}, expected {DIST_NAME}-{want}.tar.gz")
        with tarfile.open(p) as t:
            names = set(t.getnames())
            top = f"{DIST_NAME}-{want}"
            for f in LICENSE_FILES:
                if f"{top}/{f}" not in names:
                    err(f"{p.name}: {f} missing")
            if f"{top}/PKG-INFO" in names:
                check_metadata(p.name, t.extractfile(f"{top}/PKG-INFO").read().decode())
            else:
                err(f"{p.name}: PKG-INFO missing")

    # wheels
    seen = {k: [] for k in PLATFORMS}
    for p in wheels:
        prefix = f"{DIST_NAME}-{want}-cp39-abi3-"
        if not p.name.startswith(prefix):
            err(f"wheel {p.name} is not {prefix}<platform>.whl")
            continue
        plat = p.name[len(prefix) : -len(".whl")]
        matches = [k for k, rx in PLATFORMS.items() if re.search(rf"(^|\.){rx}($|\.)", plat)]
        if len(matches) != 1:
            err(f"wheel {p.name}: platform tag {plat} is not a release platform")
            continue
        seen[matches[0]].append(p.name)
        with zipfile.ZipFile(p) as z:
            names = set(z.namelist())
            info = f"{DIST_NAME}-{want}.dist-info"
            for f in LICENSE_FILES:
                if f"{info}/licenses/{f}" not in names:
                    err(f"{p.name}: {f} missing")
            check_metadata(p.name, z.read(f"{info}/METADATA").decode())
    for k, found in seen.items():
        if len(found) != 1:
            err(f"expected 1 {k} wheel, found {len(found)}: {found}")

    if errors:
        for e in errors:
            print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"ok: {NAME} {want} from {REPO}: {len(wheels)} wheels + sdist")
    for p in files:
        print(f"  {p.name}")


if __name__ == "__main__":
    main()
