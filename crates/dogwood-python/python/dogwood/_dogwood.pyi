# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Type stubs for the native module. Shapes mirror @aws/dogwood-wasm's .d.ts."""
from decimal import Decimal
from typing import Any, Dict, List, Literal, Mapping, Optional, Sequence, Tuple, TypedDict, Union

Severity = Literal["error", "warning", "advice"]
Verdict = Literal["allow", "deny"]

class _LabelBase(TypedDict):
    start: int
    len: int

class Label(_LabelBase, total=False):
    message: str

class _DiagnosticBase(TypedDict):
    severity: Severity
    message: str
    spanned: bool

class Diagnostic(_DiagnosticBase, total=False):
    code: str
    labels: List[Label]
    help: str

class DogwoodDiagnostic(Diagnostic, total=False):
    related: List[Diagnostic]

class DogwoodError(Exception):
    diagnostic: DogwoodDiagnostic

class PolicySummary(TypedDict):
    temporal_count: int
    uses_temporal: bool
    provider_invocations: List[str]
    undeclared_providers: List[str]

class CheckParseReport(TypedDict):
    policy_count: int
    policies: List[PolicySummary]

class ValidateReport(TypedDict):
    passed: bool
    passed_without_warnings: bool
    errors: List[Diagnostic]
    warnings: List[Diagnostic]

class LowerArtifacts(TypedDict):
    cedar_policies: str
    cedar_schema: str
    cedar_schema_json: str
    self_contained: bool
    temporal_fields: List[str]
    provider_fields: List[str]
    decision_kinds: List[str]

class TimepointVerdict(TypedDict):
    index: int
    timestamp: int
    verdict: Verdict
    determining_rules: List[int]
    errors: List[str]

class RejectedEvent(TypedDict):
    position: int
    timestamp: int
    reason: str

class _ReplayReportBase(TypedDict):
    verdicts: List[TimepointVerdict]

class ReplayReport(_ReplayReportBase, total=False):
    # History events rejected at ingestion; absent when every event conforms.
    rejected: List[RejectedEvent]

class _PhraseBase(TypedDict):
    template: str
    children: List["Phrase"]
    layout: Literal["Inline", "Block", "Hanging"]

class Phrase(_PhraseBase, total=False):
    span: Tuple[int, int]

class _InformalizationBase(TypedDict):
    rules: List[Phrase]

class Informalization(_InformalizationBase, total=False):
    preamble: Phrase

class ExplainReport(TypedDict):
    informalization: Informalization
    text: str

class _SchemaCheckBase(TypedDict):
    kind: str
    ok: bool

class SchemaCheckReport(_SchemaCheckBase, total=False):
    warnings: List[str]

# ─── live authorizer ────────────────────────────────────────────────

class EntityRef(TypedDict):
    type: str
    id: str

# A Dogwood value. `{"__entity": EntityRef}` and `{"__decimal": "1.50"}` are
# the tagged escapes; `decimal.Decimal` is accepted for the latter.
DogwoodValue = Union[
    None, bool, int, float, str, Decimal,
    Sequence["DogwoodValue"], Mapping[str, "DogwoodValue"],
]

class _EntityInputBase(TypedDict):
    type: str
    id: str

class EntityInput(_EntityInputBase, total=False):
    attrs: Mapping[str, DogwoodValue]
    parents: List[EntityRef]

class _EventInputBase(TypedDict):
    action: str

class EventInput(_EventInputBase, total=False):
    kind: str
    timestamp: int
    principal: EntityRef
    resource: EntityRef
    logged: Mapping[str, Mapping[str, DogwoodValue]]
    context: Mapping[str, Mapping[str, DogwoodValue]]
    entities: List[EntityInput]

class AuthorizerDecision(TypedDict):
    index: int
    timestamp: int
    verdict: Verdict
    allowed: bool
    determining_rules: List[int]
    errors: List[str]

class DogwoodAuthorizer:
    def __init__(
        self,
        source: str,
        action_schema: str,
        event_schema: Optional[str] = None,
        providers: Optional[str] = None,
        macros: Optional[str] = None,
    ) -> None: ...
    def is_authorized(self, event: EventInput) -> Optional[AuthorizerDecision]: ...
    def reset(self) -> None: ...
    @property
    def decision_count(self) -> int: ...
    @property
    def decision_kinds(self) -> List[str]: ...
    @property
    def event_kinds(self) -> List[str]: ...
    @property
    def actions(self) -> List[str]: ...
    @property
    def last_timestamp(self) -> Optional[int]: ...

# ─── one-shot operations ────────────────────────────────────────────

def check_parse(source: str, event_schema: Optional[str] = None, providers: Optional[str] = None,
                macros: Optional[str] = None) -> CheckParseReport: ...
def validate(source: str, action_schema: str, event_schema: Optional[str] = None,
             providers: Optional[str] = None, macros: Optional[str] = None) -> ValidateReport: ...
def lower(source: str, action_schema: str, event_schema: Optional[str] = None,
          providers: Optional[str] = None, macros: Optional[str] = None) -> LowerArtifacts: ...
def replay(source: str, action_schema: str, log: str, event_schema: Optional[str] = None,
           providers: Optional[str] = None, macros: Optional[str] = None) -> ReplayReport: ...
def explain(source: str, action_schema: Optional[str] = None, event_schema: Optional[str] = None,
            providers: Optional[str] = None, macros: Optional[str] = None) -> ExplainReport: ...
def check_action_schema(source: str) -> SchemaCheckReport: ...
def check_event_schema(source: str) -> SchemaCheckReport: ...
def check_providers(json: str) -> SchemaCheckReport: ...
def mcp_to_cedar_schema(manifest: Union[str, List[Dict[str, Any]]]) -> str: ...
