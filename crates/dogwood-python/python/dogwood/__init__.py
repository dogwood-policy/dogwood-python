# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Python bindings for the Dogwood policy frontend.

The same operations as the ``@aws/dogwood-wasm`` npm package, in snake_case,
over the same Rust core. Reports are plain ``dict``/``list`` values whose
shapes are described by the ``TypedDict``s in the bundled type stubs.
"""

from ._dogwood import (
    DogwoodAuthorizer,
    DogwoodError,
    check_action_schema,
    check_event_schema,
    check_parse,
    check_providers,
    explain,
    lower,
    mcp_to_cedar_schema,
    replay,
    validate,
)

__all__ = [
    "DogwoodAuthorizer",
    "DogwoodError",
    "check_action_schema",
    "check_event_schema",
    "check_parse",
    "check_providers",
    "explain",
    "lower",
    "mcp_to_cedar_schema",
    "replay",
    "validate",
]
