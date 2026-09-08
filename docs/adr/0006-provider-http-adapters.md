# ADR 0006: Direct HTTP Provider Adapters

## Status

Accepted.

## Decision

Provide small async HTTP adapters for OpenAI-compatible Chat Completions, Anthropic Messages, and
Google Gemini generateContent. Keep each adapter in an optional dependency extra and translate
messages, tools, usage, streaming chunks, and failures at that boundary.

## Consequences

The core remains provider-independent and does not require vendor SDKs. The adapters intentionally
cover the runtime contract rather than every provider feature. Provider API changes are isolated to
focused modules and mock-transport contract tests.
