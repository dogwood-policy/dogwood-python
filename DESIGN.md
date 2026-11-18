# Design: Dogwood Python bindings (`aws-dogwood`)

Status: implemented (1.2.0, not yet released). Owner: Dogwood maintainers.

## 1. Summary

`aws-dogwood` lets Python programs use the
[Dogwood](https://github.com/dogwood-policy/dogwood) policy frontend:
check, validate, lower, explain and replay `.dw` policy sets, generate a
Cedar action schema from an MCP `tools/list` manifest, and authorize events
live with temporal history. It is a thin [PyO3](https://pyo3.rs) extension
over [`dogwood-ops`](https://crates.io/crates/dogwood-ops), the host-neutral
operations crate the `dogwood` CLI and the WebAssembly binding
(`@aws/dogwood-wasm`) share. The import name is `dogwood`; it is distributed
on PyPI as one abi3 wheel per platform covering CPython 3.9 and later.

## 2. Goals and non-goals

Goals:

- **Same semantics as the CLI and the JS binding.** Every report, error,
  input limit and fail-closed rule comes from `dogwood-ops`; the binding adds
  no policy logic. A decision made in Python must match the one `dogwood
  replay` or `@aws/dogwood-wasm` makes for the same inputs.
- **Idiomatic Python.** snake_case names, plain `dict`/`list` results, a
  Python exception for fatal errors, `decimal.Decimal` support, and type
  stubs (`.pyi`, `py.typed`) so editors and type checkers see the report
  shapes.
- **Safe to embed in a server.** Fail closed on every malformed or
  unexpected input; never block other Python threads during evaluation; one
  authorizer instance usable from several threads.
- **Easy to install.** Prebuilt wheels for the platforms people deploy on;
  no Rust toolchain needed for them.

Non-goals:

- Reimplementing or extending Dogwood: new language features land in
  `dogwood-language` / `dogwood-ops` and reach Python through a dependency
  bump.
- Reading policy, schema or provider files: every input is text. Callers
  read files themselves (this is also why provider `scriptFile` references
  are rejected, see §7).
- An `async` API. Calls are CPU-bound and short and release the GIL, so
  `asyncio` users can wrap them with `asyncio.to_thread`.

## 3. Context

```
               dogwood-language   (parser, macro expansion, lowering to Cedar,
                      │            temporal engine, informalization)
                      ▼
                 dogwood-ops      (host-neutral operations: owned, serializable
                      │            reports; OpError; input bounds; fail-closed
          ┌───────────┼────────────┐  rule; provider guard; LiveAuthorizer)
          ▼           ▼            ▼
     dogwood CLI   dogwood-wasm   dogwood-python   ← this package
                  (@aws/dogwood-wasm)  (aws-dogwood)
```

`dogwood-ops` exists so the bindings do not each copy the CLI's operation
code; `dogwood-wasm` predates it and still carries copies (checked for drift
by its `pinned` CI job). `dogwood-python` has no copies: it depends on
`dogwood-ops` 1.2 from crates.io.

## 4. Architecture

The package has two layers:

| Layer | Files | Role |
|---|---|---|
| Native module `dogwood._dogwood` | `crates/dogwood-python/src/lib.rs`, `src/event.rs` | PyO3 functions and the `DogwoodAuthorizer` class; converts values, releases the GIL, maps errors |
| Python package `dogwood` | `python/dogwood/__init__.py`, `_dogwood.pyi`, `py.typed` | Re-exports the native API; type stubs |

Every native entry point has the same shape:

1. Take inputs as Rust `String`s (copied out of Python under the GIL), or,
   for an event, convert the `dict` under the GIL (§6.3).
2. Release the GIL and call `dogwood-ops` (`detach` in `lib.rs`, the one
   place that calls into the core).
3. Re-acquire the GIL and convert the report to Python with `pythonize`
   (serde → `dict`/`list`), or raise `DogwoodError`.

There is no other state than the `LiveAuthorizer` inside a
`DogwoodAuthorizer`.

## 5. API

| Python | JS (`@aws/dogwood-wasm`) | `dogwood-ops` | Returns |
|---|---|---|---|
| `check_parse(source, event_schema=None, providers=None, macros=None)` | `checkParse` | `ops::check_parse` | `CheckParseReport` |
| `validate(source, action_schema, …)` | `validate` | `ops::validate_policies` | `ValidateReport` |
| `lower(source, action_schema, …)` | `lower` | `ops::lower_to_cedar` | `LowerArtifacts` |
| `replay(source, action_schema, log, …)` | `replay` | `replay` | `ReplayReport` |
| `explain(source, action_schema=None, …)` | `explain` | `ops::explain_policies` | `ExplainReport` |
| `check_action_schema(source)` | `checkActionSchema` | `ops::check_action_schema` | `SchemaCheckReport` |
| `check_event_schema(source)` | `checkEventSchema` | `ops::check_event_schema` | `SchemaCheckReport` |
| `check_providers(json)` | `checkProviders` | `check_providers` | `SchemaCheckReport` |
| `mcp_to_cedar_schema(manifest)` | `mcpToCedarSchema` | `mcp_to_cedar_schema` | `str` |
| `DogwoodAuthorizer(source, action_schema, …)` | `DogwoodAuthorizer` | `LiveAuthorizer` | — |

`…` is the optional trio `event_schema`, `providers`, `macros`, passed by
keyword. `DogwoodAuthorizer` has `is_authorized(event)` (a decision `dict`,
or `None` for a history-only event), `reset()`, and the read-only properties
`decision_count`, `decision_kinds`, `event_kinds`, `actions` and
`last_timestamp`.

Report shapes are the `TypedDict`s in `_dogwood.pyi`, which mirror the JS
binding's `.d.ts`. Keys are the serde field names from `dogwood-ops`
(snake_case), so the Python and JS reports are the same JSON.

Differences from the JS binding, all deliberate:

- Fatal errors raise `dogwood.DogwoodError` (JS throws an `Error`); the
  structured diagnostic is on `.diagnostic` in both.
- `mcp_to_cedar_schema` also accepts a `list` of tool `dict`s (serialized
  with `json.dumps`), not only JSON text.
- `last_timestamp` is an `int` (JS needs a `number`).
- `decimal.Decimal`, `tuple` and Python's `bool`/`int` distinction are
  handled as in §6.3.

## 6. Data mapping

### 6.1 Inputs

Policy sets (`.dw`), action schemas (`.cedarschema`), event schemas
(`.dwschema`), provider declarations (`providers.json`), macros and `.log`
traces are all `str`. The binding never touches the filesystem.

### 6.2 Outputs

Reports are converted with `pythonize` from the serde types `dogwood-ops`
returns, so the binding cannot drift from the core's report shapes: a new
field in a report appears in Python without a binding change (the stubs need
updating to type it).

### 6.3 Events

`is_authorized` takes an event `dict` (`EventInput` in the stubs). It is
converted in two passes (`src/event.rs`), mirroring the WebAssembly binding:

1. **Bounds walk** (`check_event`): an iterative walk with an explicit work
   stack enforces the core's limits *before* any recursive code sees the
   input: value depth (`MAX_VALUE_DEPTH` 128, plus `EVENT_FRAMING_DEPTH` 4
   for the event's own nesting), total value count (`MAX_EVENT_VALUES`
   100,000) and total string length (`MAX_EVENT_STRING_LENGTH` 16 MiB).
   Limits are checked when a container's children are admitted, before they
   are pushed, so the walk's memory is bounded however wide the input is. A
   cyclic structure is rejected as too deep rather than looping forever.
2. **Conversion** (`to_json`): a recursive conversion, safe because the depth
   is now bounded, into a `serde_json::Value`, then `serde_json::from_value`
   into `EventInput`. Reusing the core's serde contract keeps field names,
   defaults (`kind = "request"`) and tagged values identical to the CLI and
   JS.

Value mapping:

| Python | Dogwood / Cedar | Notes |
|---|---|---|
| `None`, `str`, `list`, `dict` | as JSON | `dict` keys must be `str` |
| `tuple` | list | accepted wherever `list` is |
| `bool` | `Bool` | checked before `int`: `True` is an `int` in Python, but must never compare equal to `Long` 1 |
| `int` | `Long` | outside the signed 64-bit range raises instead of rounding |
| `float` | number | NaN and infinities raise |
| `decimal.Decimal` | Cedar `decimal` | exact text through the core's `{"__decimal": …}` tag; non-finite values (`NaN`, `Infinity`) and exponent forms raise |
| `{"__decimal": "1.50"}`, `{"__entity": {"type", "id"}}` | tagged values | same escapes as JSON and JS |
| anything else | — | raises `DogwoodError` naming the type |

## 7. Errors and fail-closed behaviour

Two kinds of failure are kept apart:

- **Caller errors raise.** A parse, macro, lowering or schema error, an
  invalid or over-limit event, an unknown action or event kind, a timestamp
  going backwards, or a history event that does not conform to its signature
  raises `DogwoodError`. `str(e)` is the message; `e.diagnostic` is the
  structured diagnostic (`severity`, `code`, `message`, `labels`, `help`,
  `spanned`, `related`), the same JSON as the CLI's `--format json`. A
  rejected event leaves the authorizer's history and `last_timestamp`
  untouched.
- **Policy problems deny.** An error while *evaluating* a policy is never
  raised: the decision is `deny` with the cause in `errors`. `dogwood-ops`
  applies one fail-closed rule to `replay` and `is_authorized` alike: Cedar
  skips a policy whose condition errors and decides from the rest, so a
  wrong-typed argument read by a `forbid` would otherwise drop that `forbid`
  and let a `permit` through. Any evaluation error forces `deny` and clears
  `determining_rules`.

Further guards inherited from `dogwood-ops`:

- **Provider `scriptFile` is rejected.** Provider scripts must be inline
  (`script`). A `scriptFile` reference cannot be resolved from text; left
  alone, `check_providers` would report `ok` and every policy calling the
  provider would then silently deny. `check_providers` and the
  `DogwoodAuthorizer` constructor raise instead.
- **Manifest bounds.** `mcp_to_cedar_schema` rejects manifests over 4 MiB or
  nested deeper than 128.

And one in the binding:

- **A poisoned authorizer stays dead.** If a call panics while holding the
  authorizer's lock, every later call on that instance raises (`this
  authorizer is unusable…`) rather than deciding from half-updated history.

## 8. Concurrency

- **GIL.** Every call releases the GIL while the core runs, so other Python
  threads keep running during lowering, replay or a decision. Only value
  conversion (reading Python objects) holds it.
- **`DogwoodAuthorizer` is thread-safe.** The engine is `Send` but not
  `Sync`, so each instance wraps it in a `Mutex`; calls on one instance are
  serialized. Events fed from several threads into one instance form a
  single history ordered by lock acquisition. Usually one instance per
  session or conversation is what you want; separate instances run fully in
  parallel.
- **Free-threaded CPython.** The module declares `gil_used = false`. abi3
  wheels cannot be loaded by free-threaded builds (3.14t), so those
  interpreters build from the sdist; this is not yet tested in CI.

## 9. Stack depth

Cedar's evaluator and validator check, before recursing, that at least
100 KiB of stack remains (`stacker::remaining_stack()`), and fail closed
("recursion limit reached") otherwise. Two consequences:

- **musl.** On musl, `pthread_getattr_np` reports the main thread's stack as
  only the part mapped so far rather than its rlimit, so `stacker` saw less
  than 100 KiB and *every* evaluation on the main thread was denied, on
  Alpine and other musl distributions. On musl only, `on_core_stack` in
  `lib.rs` runs each call through `stacker::maybe_grow(1 MiB, 8 MiB, …)`:
  when the reported remaining stack is under 1 MiB (always so on the main
  thread) the call runs on a fresh 8 MiB stack whose limit `stacker` knows
  exactly; on other threads it runs in place.
  `test_evaluates_on_the_main_thread` covers it, and the musl wheels are
  tested in Alpine at release. The underlying issue is upstream
  (Cedar/`stacker` on musl) and affects any musl build of Cedar; once fixed
  there or in `dogwood-ops`, the workaround can go.
- **Small thread stacks deny on every platform.** A thread created with a
  small stack (for example `threading.stack_size(256 * 1024)`) can run out of
  Cedar's margin and get a fail-closed deny. That is correct behaviour (the
  stack really is small) but surprising; use the default thread stack size.

## 10. Packaging and distribution

- **Build.** [maturin](https://www.maturin.rs/) with `pyproject.toml` at the
  repository root (`manifest-path` points at the crate), so the wheel and
  sdist can carry `LICENSE`, `NOTICE` and `THIRD-PARTY-NOTICES` as PEP 639
  `License-File`s. `locked = true`: builds use the committed `Cargo.lock`.
- **abi3.** PyO3's `abi3-py39` gives one wheel per platform for every
  CPython from 3.9, instead of one per Python version.
- **Platforms.** manylinux2014 and musllinux_1_2 (x86_64, aarch64), macOS
  (x86_64, arm64), Windows (x64, arm64), plus an sdist. No PyPy wheels.
- **Version.** Set once in `crates/dogwood-python/Cargo.toml` (pyproject
  reads it). Major.minor tracks the Dogwood release it is built on
  (`aws-dogwood` 1.2 ↔ `dogwood-ops` 1.2), as `@aws/dogwood-wasm` does;
  patch releases are binding fixes.
- **Attribution.** `scripts/third-party-notices.sh` (cargo-about, every
  release target) writes `THIRD-PARTY-NOTICES`: every third-party crate
  compiled in, its license text and any NOTICE file it ships (Apache-2.0
  §4). The accepted licenses in `about.toml` must match `deny.toml`.
- **Release.** `.github/workflows/release.yml` on a `v*` tag; see
  [RELEASING.md](RELEASING.md).

## 11. Testing

`crates/dogwood-python/tests/test_dogwood.py` (pytest) runs against the
installed wheel, never the source tree. It covers:

- every one-shot operation, and errors carrying `.diagnostic`;
- the live authorizer's lifecycle, temporal history, `reset`, and that a
  rejected event leaves history untouched;
- fail-closed cases: an erroring `forbid`, a non-conforming history event,
  provider `scriptFile`;
- value conversion: every supported kind, `bool` vs `int`, unconvertible
  values, the depth, width, string-length and cycle limits (and that the
  depth limit matches `replay`'s);
- concurrency: separate instances in parallel and one shared instance;
- deciding on the main thread (the musl case, §9).

Where it runs:

| When | What |
|---|---|
| Every PR, push to `main`, daily (`ci.yml`) | `build-test`: wheel on Linux x86_64, pytest on CPython 3.13, `examples/demo.py` on CPython 3.9 in a digest-pinned container, notices generation; `cargo-deny`; `pip-audit` |
| Every release and manual dry run (`release.yml`) | all of `ci.yml`, plus each of the eight wheels tested on its own platform (musl in Alpine), a wheel built from the sdist, and `scripts/check-release.py` on the exact files to publish |

## 12. Supply chain

- Third-party GitHub Actions and container images are pinned by commit SHA
  or digest; Dependabot keeps actions, cargo and the CI Python tools current
  (7-day cooldown).
- The Python tools CI installs (maturin, pytest) are hash-pinned
  (`ci/*.txt`, `--require-hashes`) and audited by `pip-audit`. The package
  itself has no Python runtime dependencies.
- `cargo-deny` gates RustSec advisories, licenses, duplicate/wildcard
  dependencies and sources (crates.io only). Accepted advisories are listed
  in `deny.toml` with a reason and owner.
- Publishing uses PyPI trusted publishing (OIDC, no stored token) from a
  protected environment that needs a second maintainer's approval; each file
  carries a provenance attestation.

## 13. Alternatives considered

- **Shell out to the `dogwood` CLI.** No native code to ship, but needs the
  CLI installed, pays a process start per decision, and cannot hold live
  temporal history across calls without a long-running protocol. Rejected.
- **Run `@aws/dogwood-wasm` under a WebAssembly runtime (e.g. wasmtime).**
  One build for every platform, but adds a large runtime dependency, slower
  evaluation, and JS-shaped glue that would need re-wrapping. Rejected.
- **A C ABI plus `ctypes`/`cffi`.** Avoids PyO3 but needs hand-written
  marshalling and memory management for every report, and loses typed
  exceptions. Rejected: PyO3 and `pythonize` reuse the serde types directly.
- **Copy the CLI's operation code, as `dogwood-wasm` did.** Rejected in
  favour of `dogwood-ops`, which removes the drift risk.
- **Return JSON strings instead of `dict`s.** Simpler binding, worse API;
  rejected. Accepting a `dict` for events also lets the binding bound and
  type-check values before conversion.
- **One wheel per CPython version.** Five or more times the wheels for no
  benefit; the API needs nothing outside the stable ABI. abi3 chosen.

## 14. Known limitations and open items

- The musl workaround (§9) should move upstream; report the bug to Cedar and
  `stacker`, and consider putting the guard in `dogwood-ops` so the CLI
  benefits.
- Free-threaded CPython builds from the sdist and is untested (§8).
- No PyPy wheels.
- The repository must be public before the first PyPI release
  ([RELEASING.md](RELEASING.md), one-time setup).
- The type stubs are maintained by hand alongside `dogwood-ops`'s report
  types; a field added there is untyped in Python until the stubs catch up.
