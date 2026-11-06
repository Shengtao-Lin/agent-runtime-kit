"""Review public exports and extension call shapes without freezing private helpers."""

from __future__ import annotations

import importlib
import inspect
import json
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import TypeAdapter, ValidationError

from agent_runtime.adapters.base import AgentStreamEvent
from agent_runtime.models_clients import ModelStreamEvent

EXPORT_MODULES = (
    "agent_runtime",
    "agent_runtime.adapters",
    "agent_runtime.memory",
    "agent_runtime.runs",
    "agent_runtime.feedback",
    "agent_runtime.hooks",
    "agent_runtime.models_clients",
    "agent_runtime.telemetry",
    "agent_runtime.tools",
)
EXTENSIONS = {
    "agent_runtime.adapters.base": ("AgentInvoker", "StreamingAgentInvoker"),
    "agent_runtime.models_clients.base": ("ModelClient", "StreamingModelClient"),
    "agent_runtime.memory.base": ("MemoryStore",),
    "agent_runtime.runs.base": ("RunStore",),
    "agent_runtime.feedback.base": ("FeedbackStore",),
    "agent_runtime.hooks.base": ("LifecycleHook", "HookManager"),
    "agent_runtime.hooks.guardrails": ("Guardrail", "GuardrailHook"),
    "agent_runtime.registry": ("AgentRegistry",),
    "agent_runtime.tools.registry": ("ToolRegistry",),
    "agent_runtime.adapters": (
        "NativeAgentInvoker",
        "LangChainRunnableAdapter",
        "LangGraphAdapter",
    ),
}


def call_shape(target: Any) -> dict[str, Any]:
    kind = "sync"
    if isinstance(target, property):
        kind = "property"
        target = target.fget
    elif inspect.isasyncgenfunction(target):
        kind = "async_iterator"
    elif inspect.iscoroutinefunction(target):
        kind = "coroutine"
    return {
        "kind": kind,
        "parameters": [
            {
                "name": parameter.name,
                "kind": parameter.kind.name,
                "default": (
                    "<required>"
                    if parameter.default is inspect.Parameter.empty
                    else "<callable>"
                    if callable(parameter.default)
                    else json.loads(json.dumps(parameter.default))
                ),
            }
            for parameter in inspect.signature(target).parameters.values()
        ],
    }


def public_snapshot() -> dict[str, Any]:
    exports: dict[str, list[str]] = {}
    for name in EXPORT_MODULES:
        module = importlib.import_module(name)
        names = sorted(module.__all__)
        assert len(names) == len(set(names)), name
        for symbol in names:
            getattr(module, symbol)  # Exercise lazy exports as well as the declared inventory.
        exports[name] = names
    members: dict[str, dict[str, Any]] = {}
    for module_name, classes in EXTENSIONS.items():
        module = importlib.import_module(module_name)
        for name in classes:
            target = getattr(module, name)
            namespace = cast(dict[str, Any], vars(target))
            members[f"{module_name}.{name}"] = {
                member_name: call_shape(member)
                for member_name, member in namespace.items()
                if (not member_name.startswith("_") or member_name == "__init__")
                and not (member_name == "__init__" and getattr(target, "_is_protocol", False))
                and (inspect.isfunction(member) or isinstance(member, property))
            }
    return {"exports": exports, "extension_members": members}


def test_public_exports_and_extension_shapes_match_reviewed_inventory() -> None:
    expected = Path(__file__).parents[1] / "snapshots" / "public_surface.json"
    assert public_snapshot() == json.loads(expected.read_text(encoding="utf-8"))


@pytest.mark.parametrize("boundary", ["model", "agent"])
def test_stream_union_uses_explicit_discriminator_and_preserves_text(boundary: str) -> None:
    adapter: TypeAdapter[Any] = TypeAdapter(
        ModelStreamEvent if boundary == "model" else AgentStreamEvent
    )
    payload = {"type": "text_delta", "delta": "visible text"}
    assert adapter.dump_python(adapter.validate_python(payload), mode="json") == payload
    expected = {"text_delta", "completed"}
    if boundary == "agent":
        expected.add("tool_result")
    discriminator = adapter.json_schema()["discriminator"]
    assert discriminator["propertyName"] == "type"
    assert set(discriminator["mapping"]) == expected
    for invalid in ({"delta": "missing type"}, {"type": "vendor_event"}, {**payload, "secret": 1}):
        with pytest.raises(ValidationError):
            adapter.validate_python(invalid)


def test_lazy_adapter_exports_retain_original_class_identity() -> None:
    package = importlib.import_module("agent_runtime.adapters")
    for symbol, module in (
        ("NativeAgentInvoker", "native"),
        ("LangChainRunnableAdapter", "langchain"),
        ("LangGraphAdapter", "langgraph"),
    ):
        assert getattr(package, symbol) is getattr(
            importlib.import_module(f"agent_runtime.adapters.{module}"), symbol
        )
    with pytest.raises(AttributeError):
        _ = package.UnknownAdapter
