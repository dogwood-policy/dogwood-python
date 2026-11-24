# Releasing

`dogwood-aws` is published to [PyPI](https://pypi.org/project/dogwood-aws/)
only by [`.github/workflows/release.yml`](.github/workflows/release.yml), when
a version tag is pushed. Nobody publishes from a laptop, and no PyPI token
exists: the workflow authenticates with [PyPI trusted
publishing](https://docs.pypi.org/trusted-publishers/) (GitHub OIDC), and
each uploaded file carries a signed [provenance
attestation](https://docs.pypi.org/attestations/) linking it to the workflow
run and commit that built it.

A release is one sdist plus one abi3 wheel (CPython >= 3.9) for each of:

| Platform | Wheel tag | Built and tested on |
|---|---|---|
| Linux x86_64 (glibc) | `manylinux_2_17_x86_64` | `ubuntu-latest`, manylinux2014 container |
| Linux aarch64 (glibc) | `manylinux_2_17_aarch64` | `ubuntu-24.04-arm`, manylinux2014 container |
| Linux x86_64 (musl) | `musllinux_1_2_x86_64` | `ubuntu-latest`, tested in Alpine |
| Linux aarch64 (musl) | `musllinux_1_2_aarch64` | `ubuntu-24.04-arm`, tested in Alpine |
| macOS x86_64 | `macosx_*_x86_64` | `macos-15-intel` |
| macOS arm64 | `macosx_*_arm64` | `macos-latest` |
| Windows x64 | `win_amd64` | `windows-latest` |
| Windows arm64 | `win_arm64` | `windows-11-arm` |

## Versioning

The version is set once, in `crates/dogwood-python/Cargo.toml`;
`pyproject.toml` reads it from there. Its major.minor tracks the Dogwood
release the bindings are built on (the `dogwood-ops` dependency), as
`@aws/dogwood-wasm` does: `dogwood-aws` 1.2.x is built on `dogwood-ops` 1.2.
Patch releases are for fixes to the bindings themselves.

Prereleases are written the Cargo way, `X.Y.Z-rc.N` (or `-alpha.N`,
`-beta.N`), and tagged `vX.Y.Z-rc.N`. PyPI shows them as `X.Y.ZrcN`, and pip
installs them only when asked (`pip install --pre dogwood-aws`).

## One-time setup

These steps are done once, before the first release. Steps 1 and 2 need
people with the right access: an organization owner of `dogwood-policy`, and
a PyPI account that will own the `dogwood-aws` project.

1. **Make the repository public.** The PyPI page links to this repository,
   and the provenance attestations record its name and workflow in the
   public Sigstore transparency log, so the source should be visible before
   anything is published. An organization owner changes the repository's
   `visibility-allow-public` custom property to `true` (Settings → Custom
   properties; the organization's "block public visibility" ruleset is
   driven by it), after which an admin changes the visibility in Settings →
   General → Danger Zone.

2. **Register the trusted publisher on PyPI.** On PyPI, signed in with the
   account that will own the project: Your account → Publishing → *Add a new
   pending publisher* → GitHub, with:

   | Field | Value |
   |---|---|
   | PyPI Project Name | `dogwood-aws` |
   | Owner | `dogwood-policy` |
   | Repository name | `dogwood-python` |
   | Workflow name | `release.yml` |
   | Environment name | `pypi-publish` |

   A pending publisher does not reserve the name; the project is created by
   the first successful upload. After it, add the other maintainers as
   owners on PyPI (Your projects → `dogwood-aws` → Collaborators), and make
   sure every owner has two-factor authentication on.

3. **Check the GitHub configuration** (already in place; listed so it can be
   checked and recreated):
   - Environment `pypi-publish` (Settings → Environments): deployments only
     from tags matching `v*`; required reviewers: the `dogwood-dev` team;
     *Prevent self-review* on.
   - Tag rulesets (Settings → Rules → Rulesets): `v* tags: creation
     restricted to dogwood-dev` and `v* tags: immutable`.
   - Branch ruleset `main`: changes land through a pull request with one
     approval, and the `build-test`, `cargo-deny` and `pip-audit` checks must
     pass.

4. **Do a dry run** (below) and check that every job passes.

## Dry run

Actions → `release` → *Run workflow* (on `main`, or on a branch) runs every
job except `publish`: CI, `THIRD-PARTY-NOTICES`, the sdist (and a wheel built
from it), all eight wheels with their tests, and `scripts/check-release.py`
against the version in `Cargo.toml`. Nothing is uploaded. Use it before the
first release, and after any change to the release workflow, the build
configuration or the list of platforms.

## Cutting a release

1. On a branch, set the version in `crates/dogwood-python/Cargo.toml`
   (`version = "X.Y.Z"`) and update `Cargo.lock` with
   `cargo update -p dogwood-python`. For a new Dogwood minor release, update
   the `dogwood-ops` requirement in the same change. Open a PR and merge it
   once CI is green.
2. A member of `dogwood-dev` tags the merge commit on `main` and pushes the
   tag. Nobody else can create a `v*` tag, and once pushed it cannot be
   moved or deleted.
   ```sh
   git checkout main && git pull
   git tag -s vX.Y.Z -m "vX.Y.Z"
   git push origin vX.Y.Z
   ```
3. The `release` workflow runs CI, builds and tests the sdist and the eight
   wheels, and runs the `check` job, which:
   - fails unless the tagged commit is on `main`;
   - runs `scripts/check-release.py`, which fails unless the tag matches the
     version in `Cargo.toml`, there is exactly one sdist and one wheel per
     platform in the table above, all named `dogwood-aws` at that version,
     and every file carries `LICENSE`, `NOTICE` and `THIRD-PARTY-NOTICES`
     and names this repository in its metadata.

   `THIRD-PARTY-NOTICES` is generated by `scripts/third-party-notices.sh`
   from `Cargo.lock`: every third-party crate compiled into the extension on
   any release platform, the full text of its license, and any NOTICE file it
   ships. The accepted licenses in `about.toml` must match `deny.toml`.
4. The `publish` job then waits for approval on the `pypi-publish`
   environment. **Another** member of `dogwood-dev`, not the one who pushed
   the tag, approves it: Actions → the `release` run → *Review deployments* →
   `pypi-publish` → *Approve and deploy*. Before approving, the reviewer
   checks that the tag points at the intended commit on `main`, that every
   job passed, and that the `check` job's log lists the expected files.
5. `publish` uploads the files checked in step 3 with
   `pypa/gh-action-pypi-publish`, which runs `twine check` first and attaches
   an attestation to each file.
6. Check the result:
   - <https://pypi.org/project/dogwood-aws/> shows the new version, and each
     file's details page shows its provenance (the repository, the
     `release.yml` workflow and the commit);
   - the attestation can also be fetched directly:
     `https://pypi.org/integrity/dogwood-aws/X.Y.Z/<file name>/provenance`;
   - in a fresh virtual environment, `pip install dogwood-aws==X.Y.Z` and
     `python -c "import dogwood"` work.

If any check fails, nothing is published. Fix it on `main` and release a new
patch version: a `v*` tag cannot be reused, and neither can a version number
on PyPI, even after its files are deleted.

## Withdrawing a release

Do not delete a release from PyPI. If a published version is broken, *yank*
it (PyPI → `dogwood-aws` → Releases → Options → Yank, with a reason): pip
stops choosing it, but anyone pinned to it can still install it. Then
release a fixed patch version. If the problem is a security vulnerability,
also follow [SECURITY.md](SECURITY.md).
