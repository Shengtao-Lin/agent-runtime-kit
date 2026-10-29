# Documentation Map

Agent Runtime Kit is an importable library. Start with the path matching your job; running the
sample service is not required for application integration.

| Need | Start here |
| --- | --- |
| Embed the library in an independent application | [Library integration](library-integration.md) |
| Understand ownership, guarantees and limits | [Architecture](architecture.md) |
| Decide what to build next and how to accept it | [Development plan](plan.md) |
| Understand why a design was chosen | [ADR index](adr/README.md) |
| Operate or troubleshoot one component | [Component runbooks](runbooks/README.md) |
| Run the fictional service / local walkthrough | [User runbook](user-runbook.md) |
| Review the current design assessment | [2026-10-28 review](validation/architecture-review-2026-10-28.md) |
| Upgrade to the current release | [v0.4.2 upgrade guide](upgrading-to-v0.4.2.md) |
| Inspect actual publication and adoption evidence | [v0.4.2 evidence](validation/v0.4.2-publication.md) |

## Which document is authoritative for what?

- **Architecture:** implemented component ownership/invariants and explicit known limitations.
- **ADRs:** accepted decisions, rationale, alternatives and later amendments.
- **Plan:** proposed implementation priorities and exit criteria; not a statement that work shipped.
- **Runbooks:** component operation and host responsibilities, using public interfaces.
- **Validation reports:** dated evidence for a specific source/artifact, platform and test scope.
- **Release notes / upgrade guides:** version-specific changes and rollout/rollback implications.

Do not promote a proposal to an implemented guarantee until code and relevant acceptance agree.
Keep the README concise; link evidence instead of repeating changing test counts in every document.

## Historical context

The [initial-spec closure](validation/initial-spec-closure.md) maps the original toolkit goals to
implementation. The [iteration archive](validation/iteration-history-through-v0.4.2.md) preserves
earlier plans and session-specific limits. Those are not the active roadmap or current authorization.
Historical release evidence remains under `validation/`, with matching notes under `releases/`.
