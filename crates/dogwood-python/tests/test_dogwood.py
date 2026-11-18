# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Prototype test suite for the Python bindings.

Ports the core of the WASM package's `test.cjs` smoke tests and the safety-
critical cases of `test-coverage.cjs` / `test-bounds.cjs`, plus the
Python-specific conversion rules (bool vs int, int range, Decimal, tuples,
cycles) and threading.
"""
import json
import threading
from decimal import Decimal

import pytest

import dogwood as dw

# ─── fixtures (as in the WASM tests) ─────────────────────────────────

SCHEMA = """namespace Drupe {
  entity Gateway;
  entity OAuthUser = { id: String } tags String;
  type ReadInput = { document: String };
  type ReadOutput = { content: String };
  action "Read" appliesTo {
    principal: [OAuthUser], resource: [Gateway],
    context: { input: ReadInput, output?: ReadOutput }
  };
}"""

ALLOW_ALL = 'permit(principal, action == Drupe::Action::"Read", resource);'
POLICY = """permit(principal, action == Drupe::Action::"Read", resource)
when { context.input.document like "*report*" };"""
TEMPORAL_POLICY = """permit(principal, action == Drupe::Action::"Read", resource)
when temporal { formerly within 1h Drupe::Action::"Read"::request{} };"""
# `formerly` includes the current timepoint, so correlate on a *response* to
# get a policy whose first request is denied.
AFTER_RESPONSE_POLICY = """permit(principal, action == Drupe::Action::"Read", resource)
when temporal { formerly within 1h Drupe::Action::"Read"::response{} };"""

SIZED_SCHEMA = """namespace Drupe {
  entity Gateway;
  entity OAuthUser = { id: String } tags String;
  type ReadInput = { document: String, size: Long };
  action "Read" appliesTo {
    principal: [OAuthUser], resource: [Gateway], context: { input: ReadInput }
  };
}"""
PERMIT_UNLESS_LARGE = """permit(principal, action == Drupe::Action::"Read", resource);
forbid(principal, action == Drupe::Action::"Read", resource)
when { context.input.size > 100 };"""

RHAI_SCRIPT = """fn evaluate(text, pattern) {
  if type_of(text) == "()" || type_of(pattern) == "()" { return #{ matched: false }; }
  #{ matched: regex_is_match(pattern, text) }
}"""


def providers_json(implementation):
    return json.dumps({"availableProviders": {"Strings::Matches": {
        "argumentTypes": [{"paramType": "string"}, {"paramType": "string"}],
        "outputType": {"paramType": "record", "fields": {"matched": {"paramType": "bool"}},
                       "required": ["matched"]},
        "implementation": implementation}}})


GUARDRAIL_POLICY = """permit(principal, action == Drupe::Action::"Read", resource)
when guardrails { Strings::Matches(context.input.document, "^[A-Z]+$").matched == true };"""

PRINCIPAL = {"type": "Drupe::OAuthUser", "id": "alice"}
RESOURCE = {"type": "Drupe::Gateway", "id": "gw1"}


def event(ts, **extra):
    inp = {"document": "d"}
    e = {"action": "Drupe::Action::Read", "kind": "request", "timestamp": ts,
         "principal": PRINCIPAL, "resource": RESOURCE,
         "logged": {"input": inp}, "context": {"input": inp}}
    e.update(extra)
    return e


def response(ts):
    """A Read response, with its output logged."""
    return event(ts, kind="response",
                 logged={"input": {"document": "d"}, "output": {"content": "c"}})


def sized(ts, inp):
    return event(ts, logged={"input": inp}, context={"input": inp})


def trace_line(ts, doc):
    d = json.dumps(doc)
    return (f'@{ts} scope(principal: Drupe::OAuthUser::"alice", resource: Drupe::Gateway::"gw1") '
            f'request_context(input: {{ document: {d} }}) '
            f'Drupe::Action::"Read"::request(callerPrincipal: Drupe::OAuthUser::"alice", '
            f'callerResource: Drupe::Gateway::"gw1", input: {{ document: {d} }})')


# ─── one-shot operations ─────────────────────────────────────────────

def test_check_action_schema():
    assert dw.check_action_schema(SCHEMA) == {"kind": "action", "ok": True}


def test_check_parse_summaries():
    r = dw.check_parse(POLICY)
    assert r["policy_count"] == 1 and r["policies"][0]["uses_temporal"] is False
    r = dw.check_parse(TEMPORAL_POLICY)
    assert r["policies"][0]["uses_temporal"] is True and r["policies"][0]["temporal_count"] == 1


def test_validate_and_lower():
    assert dw.validate(POLICY, SCHEMA)["passed"] is True
    r = dw.lower(POLICY, SCHEMA)
    assert r["cedar_policies"] and r["self_contained"] is True
    r = dw.lower(TEMPORAL_POLICY, SCHEMA)
    assert r["self_contained"] is False and len(r["temporal_fields"]) == 1


def test_explain():
    r = dw.explain(TEMPORAL_POLICY, SCHEMA)
    assert r["informalization"]["rules"][0]["span"][0] == 0
    assert "Rule 1" in r["text"] and "within the last 1 hour" in r["text"]
    assert "Drupe::" not in r["text"]
    assert 'Drupe::Action::"Read"' in dw.explain(POLICY)["text"]


def test_mcp_to_cedar_schema_accepts_text_or_list():
    tools = [{"name": "SellShares", "description": "Sell shares of a stock.",
              "inputSchema": {"type": "object",
                              "properties": {"stock": {"type": "string"}, "shares": {"type": "integer"}},
                              "required": ["stock", "shares"]}}]
    assert dw.mcp_to_cedar_schema(json.dumps(tools)) == dw.mcp_to_cedar_schema(tools)
    assert "SellShares" in dw.mcp_to_cedar_schema(tools)


def test_mcp_manifest_depth_is_bounded():
    with pytest.raises(dw.DogwoodError, match="nested deeper"):
        dw.mcp_to_cedar_schema("[" * 10_000 + "]" * 10_000)


def test_replay():
    r = dw.replay(ALLOW_ALL, SCHEMA, trace_line(1000, "a"))
    assert [v["verdict"] for v in r["verdicts"]] == ["allow"]


def test_error_carries_diagnostic():
    with pytest.raises(dw.DogwoodError) as info:
        dw.check_parse("permit(principal action resource);")
    d = info.value.diagnostic
    assert d["severity"] == "error" and isinstance(d["message"], str)
    assert d["spanned"] is True and d["labels"][0]["start"] >= 0
    assert str(info.value) == d["message"]
    assert isinstance(info.value, Exception)


def test_provider_script_file_is_rejected_not_false_green():
    with pytest.raises(dw.DogwoodError, match="scriptFile"):
        dw.check_providers(providers_json({"kind": "rhai", "scriptFile": "m.rhai"}))
    with pytest.raises(dw.DogwoodError, match="scriptFile"):
        dw.DogwoodAuthorizer(GUARDRAIL_POLICY, SCHEMA,
                             providers=providers_json({"kind": "rhai", "scriptFile": "m.rhai"}))


def test_inline_provider_runs():
    auth = dw.DogwoodAuthorizer(GUARDRAIL_POLICY, SCHEMA,
                                providers=providers_json({"kind": "rhai", "script": RHAI_SCRIPT}))
    ok = {"document": "ABC"}
    bad = {"document": "abc"}
    assert auth.is_authorized(event(1, logged={"input": ok}, context={"input": ok}))["allowed"]
    assert not auth.is_authorized(event(2, logged={"input": bad}, context={"input": bad}))["allowed"]


# ─── live authorizer ─────────────────────────────────────────────────

def test_surface_and_lifecycle():
    auth = dw.DogwoodAuthorizer(AFTER_RESPONSE_POLICY, SCHEMA)
    assert auth.actions == ["Drupe::Action::Read"]
    assert auth.decision_kinds == ["request"]
    assert auth.event_kinds == ["error", "request", "response"]
    assert auth.last_timestamp is None
    assert auth.is_authorized(event(0))["verdict"] == "deny"   # no response yet
    assert auth.is_authorized(response(1)) is None  # history-only
    assert auth.decision_count == 1
    assert auth.is_authorized(event(10))["verdict"] == "allow"  # the response at 1
    assert auth.decision_count == 2 and auth.last_timestamp == 10
    auth.reset()
    assert auth.decision_count == 0 and auth.last_timestamp is None
    assert auth.is_authorized(event(0))["verdict"] == "deny"


def test_rejected_event_leaves_history_untouched():
    auth = dw.DogwoodAuthorizer(TEMPORAL_POLICY, SCHEMA)
    for bad, pat in [(event(5, action="Read"), "did you mean `Drupe::Action::Read`"),
                     (event(5, kind="requst"), "unknown event kind"),
                     (event(5, logged={"e": {"x": {"__entity": {"type": "T"}}}}), "__entity")]:
        with pytest.raises(dw.DogwoodError, match=pat):
            auth.is_authorized(bad)
    assert auth.decision_count == 0 and auth.last_timestamp is None
    auth.is_authorized(event(5))
    with pytest.raises(dw.DogwoodError, match="before the previous"):
        auth.is_authorized(event(4))
    assert auth.decision_count == 1


@pytest.mark.parametrize("inp", [{"document": "d", "size": "huge"}, {"document": "d"}])
def test_errored_forbid_fails_closed(inp):
    auth = dw.DogwoodAuthorizer(PERMIT_UNLESS_LARGE, SIZED_SCHEMA)
    d = auth.is_authorized(sized(1, inp))
    assert d["errors"] and d["allowed"] is False and d["verdict"] == "deny"
    assert d["determining_rules"] == []


# ─── Python value conversion ─────────────────────────────────────────

def test_every_value_kind_converts():
    auth = dw.DogwoodAuthorizer(ALLOW_ALL, SCHEMA)
    edge = {"nul": None, "yes": True, "no": False, "zero": 0, "neg": -42,
            "big": 2**63 - 1, "frac": 2.5, "str": "héllo → 世界 🎉",
            "quoted": 'has "quotes" and \\ backslash', "arr": [1, "two", False, None],
            "tup": (1, 2), "obj": {"a": 1, "b": {"c": "deep"}}, "empty": {},
            "dec": Decimal("1.50"), "tagged_dec": {"__decimal": "0.01"},
            "ent": {"__entity": {"type": "Drupe::OAuthUser", "id": "bob"}}}
    d = auth.is_authorized(event(1, logged={"input": {"document": "d"}, "edge": edge}))
    assert d["verdict"] == "allow", d


def test_bool_is_not_an_int():
    # `True` is an int subclass in Python; it must arrive as a Cedar Bool, so
    # comparing it to a Long errors (fail closed) rather than matching 1.
    auth = dw.DogwoodAuthorizer(PERMIT_UNLESS_LARGE, SIZED_SCHEMA)
    assert auth.is_authorized(sized(1, {"document": "d", "size": True}))["errors"]
    assert auth.is_authorized(sized(2, {"document": "d", "size": 5}))["allowed"]
    assert not auth.is_authorized(sized(3, {"document": "d", "size": 500}))["allowed"]


@pytest.mark.parametrize("value,pat", [
    (2**63, "outside the signed 64-bit range"),
    (float("nan"), "not finite"),
    (Decimal("Infinity"), "not a finite fixed-point"),
    (object(), "unsupported value of type `object`"),
    ({1: "x"}, "dict keys must be str"),
])
def test_unconvertible_values_raise(value, pat):
    auth = dw.DogwoodAuthorizer(ALLOW_ALL, SCHEMA)
    with pytest.raises(dw.DogwoodError, match=pat):
        auth.is_authorized(event(1, logged={"input": {"document": "d"}, "e": {"x": value}}))


def test_non_dict_event_raises():
    auth = dw.DogwoodAuthorizer(ALLOW_ALL, SCHEMA)
    with pytest.raises(dw.DogwoodError, match="expected a dict"):
        auth.is_authorized([1, 2])
    with pytest.raises(dw.DogwoodError, match="invalid event"):
        auth.is_authorized({"timestamp": 1})  # no action


# ─── bounds ──────────────────────────────────────────────────────────

def nested(n):
    v = "leaf"
    for _ in range(n):
        v = [v]
    return v


def test_depth_limit_matches_replay():
    auth = dw.DogwoodAuthorizer(ALLOW_ALL, SCHEMA)
    ok = auth.is_authorized(event(1, logged={"input": {"document": "d"}, "e": {"x": nested(127)}}))
    assert ok["verdict"] == "allow"
    with pytest.raises(dw.DogwoodError, match="nested deeper than the limit of 128"):
        auth.is_authorized(event(2, logged={"input": {"document": "d"}, "e": {"x": nested(128)}}))
    # Far past any stack: rejected by the iterative walk, not a crash.
    with pytest.raises(dw.DogwoodError, match="nested deeper"):
        auth.is_authorized(event(3, logged={"e": {"x": nested(100_000)}}))
    assert auth.is_authorized(event(4))["verdict"] == "allow"


def test_cycles_are_rejected():
    auth = dw.DogwoodAuthorizer(ALLOW_ALL, SCHEMA)
    a = []
    a.append(a)
    with pytest.raises(dw.DogwoodError, match="cyclic"):
        auth.is_authorized(event(1, logged={"e": {"x": a}}))


def test_width_and_string_limits():
    auth = dw.DogwoodAuthorizer(ALLOW_ALL, SCHEMA)
    with pytest.raises(dw.DogwoodError, match="limit of 100000 values"):
        auth.is_authorized(event(1, logged={"e": {"x": [0] * 200_000}}))
    with pytest.raises(dw.DogwoodError, match="characters"):
        auth.is_authorized(event(1, logged={"e": {"x": "a" * (16 * 1024 * 1024 + 1)}}))


# ─── threading ───────────────────────────────────────────────────────

def test_concurrent_instances_and_shared_instance():
    def session(results, i):
        auth = dw.DogwoodAuthorizer(AFTER_RESPONSE_POLICY, SCHEMA)
        out = []
        for t in range(0, 100, 2):
            out.append(auth.is_authorized(event(t))["verdict"])
            if t == 0:
                auth.is_authorized(response(t + 1))
        results[i] = out

    results = [None] * 8
    threads = [threading.Thread(target=session, args=(results, i)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(r == ["deny"] + ["allow"] * 49 for r in results)

    # One instance shared across threads: calls serialize, nothing is lost.
    shared = dw.DogwoodAuthorizer(ALLOW_ALL, SCHEMA)
    def hammer():
        for _ in range(100):
            shared.is_authorized(event(0))
    threads = [threading.Thread(target=hammer) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert shared.decision_count == 400


def test_nonconforming_history_event_is_rejected_not_dropped():
    # A response whose logged record does not conform to its signature (here a
    # wrong-typed field) is not ingested by the authorizer. It must raise, not return the same `None` as a recorded
    # response, and must leave the history (and timestamp) untouched.
    auth = dw.DogwoodAuthorizer(AFTER_RESPONSE_POLICY, SCHEMA)
    with pytest.raises(dw.DogwoodError, match="event rejected, not recorded"):
        auth.is_authorized(event(1, kind="response", logged={"input": {"document": 7}}))
    assert auth.last_timestamp is None
    assert auth.is_authorized(event(2))["verdict"] == "deny"  # nothing recorded
    assert auth.is_authorized(response(3)) is None
    assert auth.is_authorized(event(4))["verdict"] == "allow"


def test_replay_reports_rejected_history_events():
    bad = ('@2 scope(principal: Drupe::OAuthUser::"alice", resource: Drupe::Gateway::"gw1") '
           'Drupe::Action::"Read"::response(callerPrincipal: Drupe::OAuthUser::"alice", '
           'callerResource: Drupe::Gateway::"gw1", input: { document: 7 })')
    r = dw.replay(ALLOW_ALL, SCHEMA, trace_line(1, "a") + "\n" + bad)
    assert [v["verdict"] for v in r["verdicts"]] == ["allow"]
    assert [(x["position"], x["timestamp"]) for x in r["rejected"]] == [(1, 2)]
    assert "document" in r["rejected"][0]["reason"]


def test_evaluates_on_the_main_thread():
    # Cedar fails closed ("recursion limit reached") when it believes the stack
    # is nearly exhausted. On musl the main thread's stack used to be
    # under-reported, so every decision there was a deny. A decision must be
    # the same on the main thread as on a fresh default-size thread.
    assert threading.current_thread() is threading.main_thread()

    def decide():
        auth = dw.DogwoodAuthorizer(ALLOW_ALL, SCHEMA)
        d = auth.is_authorized(event(1))
        r = dw.replay(ALLOW_ALL, SCHEMA, trace_line(1, "a"))
        return d["verdict"], d["errors"], [v["verdict"] for v in r["verdicts"]]

    expected = ("allow", [], ["allow"])
    assert decide() == expected
    results = []
    t = threading.Thread(target=lambda: results.append(decide()))
    t.start()
    t.join()
    assert results == [expected]
