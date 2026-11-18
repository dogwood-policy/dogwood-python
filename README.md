# aws-dogwood: Python bindings for Dogwood

Python bindings for the [Dogwood](https://github.com/dogwood-policy/dogwood)
policy frontend: a PyO3 shell over
[`dogwood-ops`](https://crates.io/crates/dogwood-ops), the host-neutral
operations the `dogwood` CLI and `@aws/dogwood-wasm` share (reports, errors,
the live authorizer, input limits, fail-closed rules). This package only
converts Python values and maps `OpError` to `dogwood.DogwoodError`.

```sh
pip install aws-dogwood
```

Wheels are published for Linux (glibc and musl; x86_64 and aarch64), macOS
(x86_64 and arm64) and Windows (x64 and arm64). One abi3 wheel per platform
covers CPython >= 3.9. Elsewhere pip builds from the sdist, which needs a Rust
toolchain.

```python
import dogwood as dw

schema = dw.mcp_to_cedar_schema(tools)          # str or list of tool dicts
report = dw.validate(policy, schema)             # dict, see the .pyi stubs
auth = dw.DogwoodAuthorizer(policy, schema)
d = auth.is_authorized({"action": "Drupe::Action::SendEmail", "timestamp": 20,
                        "principal": {"type": "Drupe::User", "id": "agent"},
                        "context": {"input": {...}}, "logged": {"input": {...}}})
if not d["allowed"]: ...
```

Same surface and semantics as `@aws/dogwood-wasm`, in snake_case. Differences:

* Errors raise `dogwood.DogwoodError`; `.diagnostic` is the same dict as the
  JS error's `.diagnostic`.
* `decimal.Decimal` maps to a Cedar decimal (exact text); `tuple` works as a
  list; `bool` never becomes a `Long`; an `int` outside i64 raises.
* `last_timestamp` is an `int` (no `f64` widening needed in Python).
* Every call releases the GIL; `DogwoodAuthorizer` is thread-safe (an
  internal mutex — the engine is `Send` but not `Sync`) and the module
  declares free-threaded support (`gil_used = false`).
* One abi3 wheel covers CPython >= 3.9.

## Dogwood 1.2 contract

Built against `dogwood-language` 1.2, which checks events against the
schemas at run time:

* a request's context must match the action's context type (the MCP template
  now requires `context.system.now`, a Cedar `datetime`, given as
  `{"__extn": {"fn": "datetime", "arg": "2026-01-01T00:00:00Z"}}`);
* a history event (e.g. a `response`) whose logged record does not conform to
  its signature is not recorded: `is_authorized` raises `DogwoodError`
  ("event rejected, not recorded in the history") and leaves the history
  untouched, and `replay` lists it in `rejected`;
* a `.log` trace must log `callerResource` as well as `callerPrincipal`.

## Development

```sh
python3 -m venv .venv && .venv/bin/pip install maturin pytest
VIRTUAL_ENV=$PWD/.venv .venv/bin/maturin develop
.venv/bin/python -m pytest crates/dogwood-python/tests
.venv/bin/python crates/dogwood-python/examples/demo.py
```

`pyproject.toml` is at the repository root so the package can carry
`LICENSE`, `NOTICE` and `THIRD-PARTY-NOTICES`. Generate the last with
`scripts/third-party-notices.sh` (needs `cargo-about`) before building a
distribution; a release fails without it.

## CI

`.github/workflows/ci.yml` runs on every PR and push to `main`, and daily:

* `build-test`: `cargo build --locked`, generates `THIRD-PARTY-NOTICES`,
  builds the abi3 wheel with maturin, runs `pytest` against the installed
  wheel on CPython 3.13 and runs `examples/demo.py` on CPython 3.9 (the oldest
  supported version) in a digest-pinned `python:3.9-slim` container;
* `cargo-deny`: RustSec advisories, licenses, bans and sources (`deny.toml`);
* `pip-audit`: audits the hash-pinned tools in `ci/requirements.txt` and
  `ci/test-requirements.txt`.

To change those tools, edit the `.in` file next to them and recompile with
the command at its top.

## Releasing

Releases are published to PyPI by `.github/workflows/release.yml` when a
`v*` tag is pushed; see [RELEASING.md](RELEASING.md).

## Contributing and security

See [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md).
