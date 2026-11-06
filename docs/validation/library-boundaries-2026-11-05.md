# Library Boundary Acceptance

Date: 2026-11-05. Scope: development source after policy PR #38; published v0.4.2 is unchanged.

## Reproducer and correction

The first external core-only wheel probe rejected `agent_runtime.adapters.langchain` during
`import agent_runtime`. The root imports the registry, which imports `adapters.base`; Python first
executes `adapters/__init__.py`, which eagerly imported all three implementations. Optional vendor
packages were not required, but concrete implementation modules still crossed the import boundary.

Adapter exports now use explicit lazy imports. `AgentInvoker` and `StreamingAgentInvoker` remain
eager, and existing public implementation import paths remain available. No model/schema change,
dependency addition or resource-lifetime change is introduced. Reverting this fix restores eager
loading and fails the new probe.

## Automated guards

- `scripts/check_library.py` parses all package Python files. Selected core contracts/runtime and
  hooks/tools reject static dependencies on concrete service, database, framework and provider
  implementations or their optional libraries. Nested and type-checking imports are included.
  Package code cannot statically import `examples` or `applications`.
- The wheel check requires the package root, typing marker, migration environment/template and
  both existing revisions. Only package and distribution metadata roots are allowed; application,
  example, cache and `.env` artifacts are rejected.
- `scripts/validate_core.py` installs the wheel without extras in a temporary external venv,
  checks dependency consistency and launches a copied probe using isolated Python (`-I`).
  The probe verifies non-editable provenance, selected public core imports, twenty forbidden
  module prefixes, and import-time network/process/thread/configuration side effects. It also
  deliberately attempts every forbidden import to verify the guard is active.
- CI runs the source guard in quality and the installed-wheel probe in packaging, retaining
  `core-imports.json` as an artifact. Existing optional-component and downstream consumer checks
  remain in place.

These checks are focused architectural evidence, not a complete static analyzer or a sandbox.
The source rule does not resolve arbitrary dynamic imports; the process probe covers its listed
entry points. Explicit host configuration and importing concrete integrations remain supported.

## Local evidence

Tests use the repository venv. The candidate still has development metadata version `0.4.2`;
it is not the published v0.4.2 wheel.

- `python -m pytest -m 'not integration' -q -p no:cacheprovider`: 566 passed, 72 deselected.
- Ruff lint/format, strict Pyright and `python scripts/check_library.py`: passed.
- `uv build --offline --out-dir build/library-boundary-candidate`, followed by
  `python scripts/validate_core.py --wheel-dir build/library-boundary-candidate
  --report-dir build/library-boundary-core`: passed from an external Windows venv with all twenty
  forbidden import prefixes checked. The report records the actual installed module path.
- Negative pytest fixtures reject forbidden imports and missing/unexpected wheel resources.

Final-commit CI and downstream wheel acceptance are required before merge. Documentation structure,
public API inventory and compatibility-trigger expansion are separate remaining Stage C work.
