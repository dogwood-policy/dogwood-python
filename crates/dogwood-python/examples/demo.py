# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Python port of the @aws/dogwood-wasm demo: governing an email-sending AI agent."""
import re
from datetime import datetime, timezone

import dogwood as dw

# 1. Action schema derived from an MCP tools/list manifest.
manifest = [
    {"name": "ReadThread", "description": "Read an email thread",
     "inputSchema": {"type": "object", "properties": {"thread": {"type": "string"}}, "required": ["thread"]},
     "outputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
    {"name": "SendEmail", "description": "Send an email reply",
     "inputSchema": {"type": "object",
                     "properties": {"thread": {"type": "string"}, "to": {"type": "string"}, "body": {"type": "string"}},
                     "required": ["thread", "to", "body"]},
     "outputSchema": {"type": "object", "properties": {"sent": {"type": "boolean"}}, "required": ["sent"]}},
]
schema = dw.mcp_to_cedar_schema(manifest)
print("=== Generated Cedar action schema ===\n" + schema)

# 2. Policy.
policy = """
@id("allow_reads")
permit (principal, action == Mail::Action::"ReadThread", resource);

// Reply only to a thread the agent actually read in the last hour.
@id("reply_after_read")
permit (principal, action == Mail::Action::"SendEmail", resource)
when { context.input.to like "*@example.com" }
when temporal {
    formerly within 1h Mail::Action::"ReadThread"::response{ input.thread: context.input.thread }
};

// Rate limit: at most 2 sent emails in any 10-minute window.
@id("send_rate_limit")
forbid (principal, action == Mail::Action::"SendEmail", resource)
when temporal {
    exists (n: Long). (
        (count for (t: Timepoint). where (
            formerly within 10m (Mail::Action::"SendEmail"::response{} && tp(t))
        )) == n && n >= 2
    )
};
"""
# The MCP converter picks its own namespace; rewrite our policy to match.
ns = re.search(r"namespace\s+(\w+)", schema).group(1)
src = policy.replace("Mail::", f"{ns}::")

# 3. Static checks.
print("=== check_action_schema ===", dw.check_action_schema(schema))
v = dw.validate(src, schema)
print("=== validate === passed:", v["passed"], [e["message"] for e in v["errors"]],
      [w["message"] for w in v["warnings"]])
print("=== explain ===\n" + dw.explain(src, schema)["text"])

# 4. Live authorizer: feed an agent session event by event.
auth = dw.DogwoodAuthorizer(src, schema)
print("actions:", auth.actions, "event_kinds:", auth.event_kinds, "decision_kinds:", auth.decision_kinds)

P = {"type": f"{ns}::User", "id": "mail-agent"}
R = {"type": f"{ns}::Gateway", "id": "gw1"}


def now(t):
    """`context.system.now`, which the MCP schema template requires: a Cedar
    datetime, given as Cedar's JSON escape for an extension value."""
    iso = datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"__extn": {"fn": "datetime", "arg": iso}}


def ev(t, tool, kind, inp, label="", out=None):
    logged = {"input": inp, **({"output": out} if out is not None else {})}
    e = {"action": f"{ns}::Action::{tool}", "kind": kind, "timestamp": t, "principal": P, "resource": R,
         "logged": logged, "context": {"input": inp, "system": {"now": now(t)}}}
    try:
        d = auth.is_authorized(e)
    except dw.DogwoodError as err:
        print(f"@{t} {tool} RAISED: {err.diagnostic['message']}")
        return None
    if d:
        errs = f"errors={d['errors']}" if d["errors"] else ""
        print(f"@{t:>5} {tool:<10} {d['verdict'].upper():<5} rules={d['determining_rules']} {errs}  <- {label}")
    return d


OUTPUTS = {"ReadThread": {"text": "..."}, "SendEmail": {"sent": True}}


def call(t, tool, inp, label):
    """Request, and on allow the tool "runs" and we log its response (with the
    tool's output, which a response's signature requires)."""
    d = ev(t, tool, "request", inp, label)
    if d and d["allowed"]:
        ev(t + 1, tool, "response", inp, out=OUTPUTS[tool])


print("\n=== Live session ===")
call(0,    "SendEmail",  {"thread": "t1", "to": "bob@example.com", "body": "hi"},   "reply before reading -> deny")
call(10,   "ReadThread", {"thread": "t1"},                                          "read t1")
call(20,   "SendEmail",  {"thread": "t1", "to": "bob@example.com", "body": "hi"},   "reply to read thread -> allow")
call(30,   "SendEmail",  {"thread": "t2", "to": "bob@example.com", "body": "hi"},   "thread t2 never read -> deny")
call(40,   "SendEmail",  {"thread": "t1", "to": "eve@evil.com", "body": "hi"},      "external recipient -> deny")
call(50,   "SendEmail",  {"thread": "t1", "to": "carol@example.com", "body": "hi"}, "2nd send -> allow")
call(60,   "SendEmail",  {"thread": "t1", "to": "dave@example.com", "body": "hi"},  "3rd send in 10m -> rate-limited")
call(700,  "SendEmail",  {"thread": "t1", "to": "dave@example.com", "body": "hi"},  "window passed -> allow")
call(4000, "SendEmail",  {"thread": "t1", "to": "dave@example.com", "body": "hi"},  "read >1h ago -> deny")

# 5. Input validation: bare action id is rejected, not silently denied.
for bad in ({"action": "SendEmail", "timestamp": 4200}, {"action": f"{ns}::Action::SendEmail", "timestamp": 1}):
    try:
        auth.is_authorized(bad)
    except dw.DogwoodError as e:
        print("rejected:", e.diagnostic["message"])

print("decision_count:", auth.decision_count)
