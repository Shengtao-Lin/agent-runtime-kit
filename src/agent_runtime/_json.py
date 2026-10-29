"""Private checks for lossless JSON payloads at canonical tool boundaries."""

from __future__ import annotations

import math
from typing import cast


def validate_json_value(value: object) -> object:
    """Reject Python-only values, non-finite numbers and cycles without coercion."""
    ancestors: set[int] = set()

    def visit(item: object) -> None:
        if item is None or type(item) in (str, bool, int):
            return
        if type(item) is float:
            if math.isfinite(item):
                return
        elif type(item) in (list, dict):
            identity = id(item)
            if identity in ancestors:
                raise ValueError("Tool payload must be an acyclic JSON value")
            ancestors.add(identity)
            try:
                if type(item) is dict:
                    mapping = cast(dict[object, object], item)
                    for key, child in mapping.items():
                        if type(key) is not str:
                            raise ValueError("Tool payload object keys must be strings")
                        visit(child)
                else:
                    for child in cast(list[object], item):
                        visit(child)
            finally:
                ancestors.remove(identity)
            return
        raise ValueError("Tool payload must contain only finite JSON values")

    try:
        visit(value)
    except RecursionError as exc:
        raise ValueError("Tool payload exceeds supported nesting") from exc
    return value
