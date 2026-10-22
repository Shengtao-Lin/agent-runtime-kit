# Next Iteration: Direct-Library Provider Contracts

## Baseline and decision

As of 2026-10-21, v0.4.0 is published, the independent Release Desk application consumes its
SHA-256-pinned wheel, main CI is green, and no PR is open. The initial specification is implemented;
v0.4 additionally provides optional managed runtime lifetimes and installed-wheel sustained probes.
See [initial closure](validation/initial-spec-closure.md) and
[v0.4 publication](validation/v0.4.0-publication.md). These do not establish production certification.

The next bounded iteration is v0.4.1, not a new orchestration framework or sample-agent feature set.
Direct importable adapter calls must receive safe provider failures just as runtime callers do.
The first offline regression batch reproduced 23 failures out of 58 cases on the v0.4.0 source:
canonical text validation escaped in four paths, absent/partial usage invented zero counts,
numeric-string token counts were coerced, and unsupported multiple choices/candidates were accepted.

## Execution and acceptance

1. **Provider boundaries:** shared private canonical assembly, honest unknown usage, strict supplied
   token counts and single-candidate enforcement. Add mocked-HTTP generate/stream regressions,
   including response closure and no successful completion after validation failure. Preserve
   public signatures, canonical contract v1, schema 0002 and dependency ranges.
2. **Durable failure:** real PostgreSQL tests must retain failed/provider_failure records with no
   response or partial history; retrying a failed key must not call the provider again.
3. **Distribution gate:** run full venv pytest, formatting, lint and strict typing. Execute the
   new contract tests from a non-editable installed wheel outside the checkout. Add SHA-256-pinned
   actual v0.4.0 upgrade acceptance to CI/Release while retaining historical gates and short soak.
4. **Release and adoption:** merge only verified PR heads; tag only verified main. Publish v0.4.1
   through existing gates, download/check actual immutable assets, rerun provider/upgrade acceptance,
   then update the independent application's own lock to that actual wheel and validate it.

## Authority, stop conditions and non-goals

Communicate in Chinese; keep repository/PR/commit text English. Tests use venv and pytest. Scoped
implementation, pushes, PRs, merges and publication proceed under owner authorization. Recheck weekly
usage between stages; stop at 50% used or when credentials, account configuration, paid operations or
material product decisions require the owner. Do not consume usage reset credits automatically.

No live model calls, production load claims, automatic provider retries, new authentication/tenant
platform, RAG, scheduler, UI, database backend or PyPI configuration are included. Unknown usage
remains unknown, not a billing estimate. A stricter malformed-input boundary may expose incompatible
proxies; document it instead of silently coercing their responses.

After this iteration, the next product decision should be grounded in a real host workload or an
owner-approved credential/cost-scoped provider canary, not features added merely to fill a roadmap.
