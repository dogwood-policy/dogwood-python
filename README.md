# dogwood-python (prototype)

Python bindings for the [Dogwood](https://github.com/dogwood-policy/dogwood)
policy frontend: a PyO3 shell over `dogwood-ops`, the host-neutral operations
the `dogwood` CLI and `@aws/dogwood-wasm` share (reports, errors, the live
authorizer, input limits, fail-closed rules). This crate only converts Python
values and maps `OpError` to `dogwood.DogwoodError`.

`dogwood-ops` is not published yet, so `crates/dogwood-python/Cargo.toml`
takes it by path from a `Dogwood_Language` checkout at
`~/Dogwood/src/Dogwood_Language` (relative path
`../../../Dogwood/src/Dogwood_Language/dogwood-ops`). Switch to a version
requirement once it is released.

## Python

```sh
python3 -m venv .venv && .venv/bin/pip install maturin pytest
cd crates/dogwood-python
VIRTUAL_ENV=$PWD/../../.venv ../../.venv/bin/maturin develop
../../.venv/bin/python -m pytest tests
../../.venv/bin/python examples/demo.py
```

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

## Dogwood 1.2 contract (mainline)

Built against mainline `dogwood-language`, which checks events against the
schemas at run time:

* a request's context must match the action's context type (the MCP template
  now requires `context.system.now`, a Cedar `datetime`, given as
  `{"__extn": {"fn": "datetime", "arg": "2026-01-01T00:00:00Z"}}`);
* a history event (e.g. a `response`) whose logged record does not conform to
  its signature is not recorded: `is_authorized` raises `DogwoodError`
  ("event rejected, not recorded in the history") and leaves the history
  untouched, and `replay` lists it in `rejected`;
* a `.log` trace must log `callerResource` as well as `callerPrincipal`.
