# Documentation Map

Agent Runtime Kit is an importable library. Start with the path matching your job; running the
sample service is not required for application integration.

| Need | Start here |
| --- | --- |
| Embed the library in an independent application | [Library integration](library-integration.md) |
| Understand ownership, guarantees and limits | [Architecture](architecture.md) |
| Review supported entry points and compatibility checks | [Public API inventory](public-api.md) |
| Decide what to build next and how to accept it | [Development plan](plan.md) |
| Understand why a design was chosen | [ADR index](adr/README.md) |
| Operate or troubleshoot one component | [Component runbooks](runbooks/README.md) |
| Run the fictional service / local walkthrough | [User runbook](user-runbook.md) |
| Review the current design assessment | [2026-10-28 review](validation/architecture-review-2026-10-28.md) |
| Upgrade to the current release | [v0.5.0 upgrade guide](upgrading-to-v0.5.0.md) |
| Inspect actual publication and adoption evidence | [v0.5.0 evidence](validation/v0.5.0-publication.md) |
| Review the pre-publication candidate checks | [v0.5.0 readiness](validation/v0.5.0-readiness.md) |

## Which document is authoritative for what?

- **Architecture:** implemented component ownership/invariants and explicit known limitations.
- **ADRs:** accepted decisions, rationale, alternatives and later amendments.
- **Plan:** proposed implementation priorities and exit criteria; not a statement that work shipped.
- **Runbooks:** component operation and host responsibilities, using public interfaces.
- **Validation reports:** dated evidence for a specific source/artifact, platform and test scope.
- **Release notes / upgrade guides:** version-specific changes and rollout/rollback implications.

Do not promote a proposal to an implemented guarantee until code and relevant acceptance agree.
Keep the README concise; link evidence instead of repeating changing test counts in every document.

## Checking navigation and architectural boundaries

From the repository venv, run `python scripts/check_docs.py` and
`python scripts/check_library.py`. CI runs both, and release verification also checks a core-only
installed wheel with `python scripts/validate_core.py --wheel-dir dist`. Tests use pytest.

The offline documentation guard covers root Markdown, `docs/` and application READMEs. Use
Markdown links for tracked acceptance reports so missing evidence files are detected. Local
destinations must exist inside the repository; Markdown fragments must match heading anchors.
Canonical ADR filenames, title IDs and index entries must agree and IDs must be unique. The
explicit historical lifetime alias remains valid only as a link to its canonical replacement.

The guard supports the repository's ATX headings, inline links and reference definitions, with
duplicate-heading suffixes and URL-decoded paths/fragments. Code fences and inline code are
excluded from link parsing. It does not fetch remote URLs, parse arbitrary HTML/Markdown dialects,
check prose claims or require ignored local build reports to exist. Dated evidence still needs
review of its actual artifact and verification scope.

## Historical context

The [initial-spec closure](validation/initial-spec-closure.md) maps the original toolkit goals to
implementation. The [iteration archive](validation/iteration-history-through-v0.4.2.md) preserves
earlier plans and session-specific limits. Those are not the active roadmap or current authorization.
Historical release evidence remains under `validation/`, with matching notes under `releases/`.
