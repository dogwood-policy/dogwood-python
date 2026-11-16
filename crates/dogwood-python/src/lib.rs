// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

//! Python bindings for the Dogwood policy frontend.
//!
//! A thin PyO3 shell over [`dogwood_ops`], the operations the `dogwood` CLI
//! and the WebAssembly binding share. Each operation takes `str`s, runs with the GIL
//! released, and returns its report as plain `dict`s/`list`s (via
//! `pythonize`). A fatal error is raised as `dogwood.DogwoodError`, whose
//! `.diagnostic` attribute carries the structured diagnostic — the same JSON
//! shape as the CLI's `--format json` and the JS error's `.diagnostic`.
//!
//! | Python                  | JS (`@aws/dogwood-wasm`) |
//! |-------------------------|--------------------------|
//! | `check_parse`           | `checkParse`             |
//! | `validate`              | `validate`               |
//! | `lower`                 | `lower`                  |
//! | `replay`                | `replay`                 |
//! | `explain`               | `explain`                |
//! | `check_action_schema`   | `checkActionSchema`      |
//! | `check_event_schema`    | `checkEventSchema`       |
//! | `check_providers`       | `checkProviders`         |
//! | `mcp_to_cedar_schema`   | `mcpToCedarSchema`       |
//! | `DogwoodAuthorizer`     | `DogwoodAuthorizer`      |

mod event;

use std::sync::Mutex;

use dogwood_ops::{LiveAuthorizer, OpError, ops};
use pyo3::create_exception;
use pyo3::exceptions::PyException;
use pyo3::prelude::*;
use serde::Serialize;

create_exception!(
    dogwood,
    DogwoodError,
    PyException,
    "A fatal Dogwood error. `str(e)` is the human message; `e.diagnostic` is \
     the structured diagnostic dict (severity, code, message, labels, help, \
     spanned, related)."
);

const POISONED: &str = "this authorizer is unusable: an earlier call panicked mid-decision";

// ─── conversions ─────────────────────────────────────────────────────

/// Turn an [`OpError`] into a raised `DogwoodError` with `.diagnostic` set.
fn raise(py: Python<'_>, e: OpError) -> PyErr {
    let err = DogwoodError::new_err(e.to_string());
    if let Ok(diagnostic) = pythonize::pythonize(py, &e) {
        let _ = err.value(py).setattr("diagnostic", diagnostic);
    }
    err
}

/// A plain-message `DogwoodError` (still with a `.diagnostic`).
fn raise_msg(py: Python<'_>, msg: impl Into<String>) -> PyErr {
    raise(py, OpError::message(msg))
}

/// Convert a serializable report into Python `dict`s/`list`s.
fn to_py<'py, T: Serialize>(py: Python<'py>, value: &T) -> PyResult<Bound<'py, PyAny>> {
    pythonize::pythonize(py, value).map_err(|e| raise_msg(py, format!("internal: {e}")))
}

/// Run a core operation with the GIL released, then convert its result.
fn run<'py, T, F>(py: Python<'py>, op: F) -> PyResult<Bound<'py, PyAny>>
where
    T: Serialize + Send,
    F: FnOnce() -> Result<T, OpError> + Send,
{
    match py.detach(op) {
        Ok(report) => to_py(py, &report),
        Err(e) => Err(raise(py, e)),
    }
}

// ─── one-shot operations ─────────────────────────────────────────────

/// Parse and macro-expand a `.dw` policy set; needs no action schema.
#[pyfunction]
#[pyo3(signature = (source, event_schema=None, providers=None, macros=None))]
fn check_parse<'py>(
    py: Python<'py>,
    source: String,
    event_schema: Option<String>,
    providers: Option<String>,
    macros: Option<String>,
) -> PyResult<Bound<'py, PyAny>> {
    run(py, || {
        let schema = dogwood_ops::inputs(event_schema.as_deref(), providers.as_deref(), macros.as_deref())?;
        ops::check_parse(&source, &schema)
    })
}

/// Lower against the Cedar action schema and type-check.
#[pyfunction]
#[pyo3(signature = (source, action_schema, event_schema=None, providers=None, macros=None))]
fn validate<'py>(
    py: Python<'py>,
    source: String,
    action_schema: String,
    event_schema: Option<String>,
    providers: Option<String>,
    macros: Option<String>,
) -> PyResult<Bound<'py, PyAny>> {
    run(py, || {
        let schema = dogwood_ops::inputs(event_schema.as_deref(), providers.as_deref(), macros.as_deref())?;
        ops::validate_policies(&source, &schema, &action_schema)
    })
}

/// Lower a `.dw` policy set to Cedar and return the emitted artifacts.
#[pyfunction]
#[pyo3(signature = (source, action_schema, event_schema=None, providers=None, macros=None))]
fn lower<'py>(
    py: Python<'py>,
    source: String,
    action_schema: String,
    event_schema: Option<String>,
    providers: Option<String>,
    macros: Option<String>,
) -> PyResult<Bound<'py, PyAny>> {
    run(py, || {
        let schema = dogwood_ops::inputs(event_schema.as_deref(), providers.as_deref(), macros.as_deref())?;
        ops::lower_to_cedar(&source, &schema, &action_schema)
    })
}

/// Lower, then drive a `.log` trace through a fresh authorizer (fail-closed).
#[pyfunction]
#[pyo3(signature = (source, action_schema, log, event_schema=None, providers=None, macros=None))]
fn replay<'py>(
    py: Python<'py>,
    source: String,
    action_schema: String,
    log: String,
    event_schema: Option<String>,
    providers: Option<String>,
    macros: Option<String>,
) -> PyResult<Bound<'py, PyAny>> {
    run(py, || {
        let schema = dogwood_ops::inputs(event_schema.as_deref(), providers.as_deref(), macros.as_deref())?;
        dogwood_ops::replay(&source, &schema, &action_schema, &log)
    })
}

/// Informalize a `.dw` policy set into deterministic semi-English.
#[pyfunction]
#[pyo3(signature = (source, action_schema=None, event_schema=None, providers=None, macros=None))]
fn explain<'py>(
    py: Python<'py>,
    source: String,
    action_schema: Option<String>,
    event_schema: Option<String>,
    providers: Option<String>,
    macros: Option<String>,
) -> PyResult<Bound<'py, PyAny>> {
    run(py, || {
        let schema = dogwood_ops::inputs(event_schema.as_deref(), providers.as_deref(), macros.as_deref())?;
        ops::explain_policies(&source, &schema, action_schema.as_deref())
    })
}

/// Check a Cedar action schema (`.cedarschema`) in isolation.
#[pyfunction]
fn check_action_schema<'py>(py: Python<'py>, source: String) -> PyResult<Bound<'py, PyAny>> {
    run(py, || ops::check_action_schema(&source))
}

/// Check an event-schema DSL (`.dwschema`) in isolation.
#[pyfunction]
fn check_event_schema<'py>(py: Python<'py>, source: String) -> PyResult<Bound<'py, PyAny>> {
    run(py, || ops::check_event_schema(&source))
}

/// Check a `providers.json` declarations file in isolation.
#[pyfunction]
fn check_providers<'py>(py: Python<'py>, json: String) -> PyResult<Bound<'py, PyAny>> {
    run(py, || dogwood_ops::check_providers(&json))
}

/// Generate a Cedar action schema from an MCP `tools/list` manifest.
///
/// Takes the manifest as JSON text, as in JS; a `list` of tool dicts is also
/// accepted and serialized with `json.dumps` first.
#[pyfunction]
fn mcp_to_cedar_schema(py: Python<'_>, manifest: &Bound<'_, PyAny>) -> PyResult<String> {
    let text: String = match manifest.extract::<String>() {
        Ok(s) => s,
        Err(_) => py
            .import("json")?
            .call_method1("dumps", (manifest,))?
            .extract()?,
    };
    py.detach(|| dogwood_ops::mcp_to_cedar_schema(&text))
        .map_err(|e| raise(py, e))
}

// ─── the live authorizer ─────────────────────────────────────────────

/// A stateful, live Dogwood authorizer: lower a policy set once, then feed
/// events as they happen and get a decision per event, with temporal history
/// accumulating across calls. One instance is one ordered history.
///
/// Thread-safe: calls are serialized on an internal lock (the engine is
/// `Send` but not `Sync`), and the GIL is released while a decision is
/// evaluated. Events fed from several threads into one instance still form a
/// single history, ordered by lock acquisition — usually you want one
/// instance per session.
#[pyclass(module = "dogwood", name = "DogwoodAuthorizer")]
struct DogwoodAuthorizer {
    inner: Mutex<LiveAuthorizer>,
}

impl DogwoodAuthorizer {
    /// Run `f` on the engine under the lock (GIL released). A poisoned lock —
    /// a panic in an earlier call — fails closed: the instance raises from
    /// then on rather than deciding from possibly half-updated history.
    fn with<R: Send>(
        &self,
        py: Python<'_>,
        f: impl FnOnce(&mut LiveAuthorizer) -> R + Send,
    ) -> PyResult<R> {
        py.detach(|| self.inner.lock().map(|mut guard| f(&mut guard)).map_err(|_| ()))
            .map_err(|()| raise_msg(py, POISONED))
    }
}

#[pymethods]
impl DogwoodAuthorizer {
    /// Lower `source` against `action_schema` and build a fresh authorizer
    /// with empty temporal history. Raises `DogwoodError` on a parse, macro,
    /// lowering, or schema failure.
    #[new]
    #[pyo3(signature = (source, action_schema, event_schema=None, providers=None, macros=None))]
    fn new(
        py: Python<'_>,
        source: String,
        action_schema: String,
        event_schema: Option<String>,
        providers: Option<String>,
        macros: Option<String>,
    ) -> PyResult<Self> {
        let inner = py
            .detach(|| LiveAuthorizer::new(&source, &action_schema, event_schema, providers, macros))
            .map_err(|e| raise(py, e))?;
        Ok(DogwoodAuthorizer {
            inner: Mutex::new(inner),
        })
    }

    /// Authorize one event (a `dict`; see `EventInput` in the type stubs).
    ///
    /// Returns the decision `dict` for a decision-kind event (by default
    /// `kind="request"`), or `None` for a history-only event — which still
    /// updates temporal history. Raises `DogwoodError` for a malformed, too
    /// large, or rejected event (unknown action or kind, timestamp going
    /// backwards); the instance stays usable and its history untouched.
    /// A *policy* problem is never raised: it is a fail-closed deny with the
    /// cause in `errors`.
    fn is_authorized<'py>(
        &self,
        py: Python<'py>,
        event: &Bound<'py, PyAny>,
    ) -> PyResult<Bound<'py, PyAny>> {
        // Bounded and converted under the GIL (it reads Python objects), then
        // decided with the GIL released.
        let event = event::event_input(event).map_err(|e| raise_msg(py, e))?;
        match self.with(py, |auth| auth.is_authorized(&event))? {
            Ok(Some(decision)) => to_py(py, &decision),
            Ok(None) => Ok(py.None().into_bound(py)),
            Err(e) => Err(raise(py, e)),
        }
    }

    /// Drop all temporal history and start over (re-lowers the sources).
    /// The decision index restarts at 0.
    fn reset(&self, py: Python<'_>) -> PyResult<()> {
        self.with(py, |auth| auth.reset())?
            .map_err(|e| raise(py, e))
    }

    /// How many decisions this instance has returned.
    #[getter]
    fn decision_count(&self, py: Python<'_>) -> PyResult<usize> {
        self.with(py, |a| a.decision_count())
    }

    /// The event kinds that are decision points (`["request"]` by default).
    #[getter]
    fn decision_kinds(&self, py: Python<'_>) -> PyResult<Vec<String>> {
        self.with(py, |a| a.decision_kinds())
    }

    /// Every event kind the event schema declares.
    #[getter]
    fn event_kinds(&self, py: Python<'_>) -> PyResult<Vec<String>> {
        self.with(py, |a| a.event_kinds())
    }

    /// Every fully qualified action id this instance can decide.
    #[getter]
    fn actions(&self, py: Python<'_>) -> PyResult<Vec<String>> {
        self.with(py, |a| a.actions())
    }

    /// The most recent event's timestamp, or `None` before the first.
    #[getter]
    fn last_timestamp(&self, py: Python<'_>) -> PyResult<Option<i64>> {
        self.with(py, |a| a.last_timestamp())
    }

    fn __repr__(&self, py: Python<'_>) -> PyResult<String> {
        self.with(py, |a| {
            format!(
                "<DogwoodAuthorizer actions={} decisions={}>",
                a.actions().len(),
                a.decision_count()
            )
        })
    }
}

// ─── module ──────────────────────────────────────────────────────────

#[pymodule(gil_used = false)]
fn _dogwood(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("DogwoodError", m.py().get_type::<DogwoodError>())?;
    m.add_class::<DogwoodAuthorizer>()?;
    m.add_function(wrap_pyfunction!(check_parse, m)?)?;
    m.add_function(wrap_pyfunction!(validate, m)?)?;
    m.add_function(wrap_pyfunction!(lower, m)?)?;
    m.add_function(wrap_pyfunction!(replay, m)?)?;
    m.add_function(wrap_pyfunction!(explain, m)?)?;
    m.add_function(wrap_pyfunction!(check_action_schema, m)?)?;
    m.add_function(wrap_pyfunction!(check_event_schema, m)?)?;
    m.add_function(wrap_pyfunction!(check_providers, m)?)?;
    m.add_function(wrap_pyfunction!(mcp_to_cedar_schema, m)?)?;
    Ok(())
}
