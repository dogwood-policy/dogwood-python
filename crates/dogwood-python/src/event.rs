// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

//! Python event `dict` → core [`EventInput`].
//!
//! Two passes, as in the WebAssembly binding:
//!
//! 1. [`check_event`] — an **iterative** walk (explicit work stack) that
//!    enforces the core's depth, value-count and string-length limits before
//!    any recursive code sees the input. A cyclic structure is rejected as too
//!    deep rather than looping.
//! 2. [`to_json`] — the (now depth-bounded) recursive conversion into a
//!    `serde_json::Value`, then `serde_json::from_value` into [`EventInput`],
//!    which reuses the core's serde contract (defaults, field names) as is.
//!    It checks the depth again as it goes, so even an input that changed
//!    after the walk raises instead of overflowing the native stack.
//!
//! Python values map to Dogwood values as JSON does, plus:
//!
//! * `decimal.Decimal` → a Cedar decimal with its exact text (the JS binding's
//!   `{"__decimal": "1.50"}` tag is also accepted). Exactly `Decimal`, not a
//!   subclass: converting must not run caller-defined Python code (a
//!   subclass's `__str__`), which could change the event after it was bounded;
//! * `tuple` is accepted wherever `list` is;
//! * an `int` outside the signed 64-bit range is rejected rather than rounded;
//! * `bool` is never mistaken for `int` (it is a subclass in Python).

use dogwood_ops::EventInput;
use dogwood_ops::bounds::{
    EVENT_FRAMING_DEPTH, MAX_VALUE_DEPTH, add_string, admit,
};
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyDict, PyFloat, PyInt, PyList, PyString, PyTuple};
use serde_json::{Map, Number, Value};

/// Bound, convert and deserialize one event.
pub fn event_input(event: &Bound<'_, PyAny>) -> Result<EventInput, String> {
    if !event.is_instance_of::<PyDict>() {
        return Err(format!(
            "invalid event: expected a dict, got `{}`",
            type_name(event)
        ));
    }
    check_event(event).map_err(|e| format!("invalid event: {e}"))?;
    let json = to_json(event, 0).map_err(|e| format!("invalid event: {e}"))?;
    serde_json::from_value(json).map_err(|e| format!("invalid event: {e}"))
}

fn type_name(value: &Bound<'_, PyAny>) -> String {
    value
        .get_type()
        .name()
        .map(|n| n.to_string())
        .unwrap_or_else(|_| "?".to_string())
}

/// The deepest value nesting an event may have (the walk's limit).
const MAX_DEPTH: usize = MAX_VALUE_DEPTH + EVENT_FRAMING_DEPTH;

/// Exactly `decimal.Decimal`. A subclass is rejected as unsupported: its
/// `__str__` would be caller code run in the middle of conversion.
fn is_decimal(value: &Bound<'_, PyAny>) -> bool {
    let py = value.py();
    py.import("decimal")
        .and_then(|m| m.getattr("Decimal"))
        .map(|ty| value.get_type().is(&ty))
        .unwrap_or(false)
}

/// A `Decimal`'s text. Only called on an exact `Decimal` (see
/// [`is_decimal`]), so this runs the C implementation, never caller code.
fn decimal_text(value: &Bound<'_, PyAny>) -> Result<String, String> {
    Ok(value.str().map_err(|e| e.to_string())?.to_string())
}

/// The iterative bounds walk. Limits are checked when a container's children
/// are *admitted*, before any is pushed, so the work stack never outgrows
/// the value-count limit however wide the input is.
fn check_event(event: &Bound<'_, PyAny>) -> Result<(), String> {
    let max_depth = MAX_DEPTH;
    let mut stack: Vec<(Bound<'_, PyAny>, usize)> = vec![(event.clone(), 0)];
    let mut values = 1usize;
    let mut string_length = 0usize;

    while let Some((value, depth)) = stack.pop() {
        let child_depth = depth + 1;
        if let Ok(s) = value.cast::<PyString>() {
            add_string(s.len().map_err(|e| e.to_string())?, &mut string_length)?;
        } else if let Ok(dict) = value.cast::<PyDict>() {
            admit(dict.len(), child_depth, max_depth, &mut values)?;
            for (k, v) in dict.iter() {
                let key = k.cast::<PyString>().map_err(|_| {
                    format!("dict keys must be str, got `{}`", type_name(&k))
                })?;
                add_string(key.len().map_err(|e| e.to_string())?, &mut string_length)?;
                stack.push((v, child_depth));
            }
        } else if let Ok(list) = value.cast::<PyList>() {
            admit(list.len(), child_depth, max_depth, &mut values)?;
            stack.extend(list.iter().map(|item| (item, child_depth)));
        } else if let Ok(tuple) = value.cast::<PyTuple>() {
            admit(tuple.len(), child_depth, max_depth, &mut values)?;
            stack.extend(tuple.iter().map(|item| (item, child_depth)));
        } else if is_decimal(&value) {
            // Its text is a string in the event like any other.
            add_string(decimal_text(&value)?.len(), &mut string_length)?;
        }
        // Anything else is a scalar here; unsupported types are reported by
        // `to_json`, which knows where they sit.
    }
    Ok(())
}

/// Convert a bounded Python value into JSON. Recursive; called after
/// [`check_event`] has capped the depth, and checks it again (`depth` is the
/// value's own nesting) so it can never recurse past the limit.
fn to_json(value: &Bound<'_, PyAny>, depth: usize) -> Result<Value, String> {
    if depth > MAX_DEPTH {
        return Err(format!("value nesting exceeds the maximum depth of {MAX_DEPTH}"));
    }
    if value.is_none() {
        return Ok(Value::Null);
    }
    // `bool` before `int`: `True` is an `int` in Python.
    if let Ok(b) = value.cast::<PyBool>() {
        return Ok(Value::Bool(b.is_true()));
    }
    if value.is_instance_of::<PyInt>() {
        let i: i64 = value.extract().map_err(|_| {
            format!(
                "integer {} is outside the signed 64-bit range of a Dogwood Long",
                value.str().map(|s| s.to_string()).unwrap_or_default()
            )
        })?;
        return Ok(Value::Number(i.into()));
    }
    if let Ok(f) = value.cast::<PyFloat>() {
        let f = f.value();
        return Number::from_f64(f)
            .map(Value::Number)
            .ok_or_else(|| format!("float {f} is not finite and has no Dogwood value"));
    }
    if value.is_instance_of::<PyString>() {
        return value.extract::<String>().map(Value::String).map_err(|e| e.to_string());
    }
    if let Ok(dict) = value.cast::<PyDict>() {
        let mut map = Map::with_capacity(dict.len());
        for (k, v) in dict.iter() {
            let key: String = k.extract().map_err(|_| "dict keys must be str".to_string())?;
            map.insert(key, to_json(&v, depth + 1)?);
        }
        return Ok(Value::Object(map));
    }
    if let Ok(list) = value.cast::<PyList>() {
        return list.iter().map(|v| to_json(&v, depth + 1)).collect::<Result<_, _>>().map(Value::Array);
    }
    if let Ok(tuple) = value.cast::<PyTuple>() {
        return tuple.iter().map(|v| to_json(&v, depth + 1)).collect::<Result<_, _>>().map(Value::Array);
    }
    if is_decimal(value) {
        // Exact text, routed through the core's `__decimal` tag.
        let text = decimal_text(value)?;
        if !text.chars().all(|c| c.is_ascii_digit() || c == '.' || c == '-') {
            return Err(format!("decimal `{text}` is not a finite fixed-point number"));
        }
        let mut map = Map::with_capacity(1);
        map.insert("__decimal".to_string(), Value::String(text));
        return Ok(Value::Object(map));
    }
    Err(format!(
        "unsupported value of type `{}` (expected None, bool, int, float, str, \
         decimal.Decimal, list, tuple or dict)",
        type_name(value)
    ))
}
