# Contributing

This is a private, proprietary product (see the note in
[`docs/future-refactors.md`](docs/future-refactors.md) about why there's no
`LICENSE` file) — this doc is for whoever is working in this repo, not
external open-source contributors.

## Before you build anything non-trivial

Run it through the review process in
[`docs/review-panel.md`](docs/review-panel.md) — a structured-disagreement
pass covering leverage, feasibility, product fit, UX, growth, QA, and
customer trust — before writing code. Save the resulting spec + plan under
`docs/superpowers/specs/` and `docs/superpowers/plans/`
(`YYYY-MM-DD-<topic>[-design].md`), matching the existing convention.

## Required reading before specific changes

- Touching `/v2/dashboard*` (routes, view models, templates, navigation)?
  Read [`ARCHITECTURE.md`](ARCHITECTURE.md) in full — it's a frozen,
  PR-review checklist, not background reading.
- Any visual/UI change? Read [`DESIGN.md`](DESIGN.md) — fonts, colors,
  spacing, and aesthetic direction are all defined there.
- Anything else architectural? Check
  [`docs/architecture.md`](docs/architecture.md) and
  [`ROADMAP.md`](ROADMAP.md)'s Development Policy first — refactors are
  welcome when they preserve behavior, keep tests passing, and land in
  small reviewable commits.

## Workflow

1. Write the test first where the change has real logic (see
   [`docs/testing.md`](docs/testing.md)) — this codebase follows TDD for
   business logic.
2. Run `cd agent && pytest` before every commit. Every invariant in
   `ARCHITECTURE.md` is backed by a named test — run those specifically if
   you touched the dashboard.
3. Commit messages follow `type(scope): summary`, e.g.
   `feat(portal): the Department Workspace`, `fix(voice): per-call token/cost budget`,
   `docs: Phase 5 audit + task plan` — see `git log` for more examples.
4. Keep changes scoped to one responsibility per commit; frequent small
   commits over one large one. Current practice is committing directly to
   the working branch rather than a PR-per-change — a
   handful of larger integrations have used a merge commit instead (`git log
   --merges`); follow whichever the change's size actually warrants.

## Code conventions

See [`docs/coding-guidelines.md`](docs/coding-guidelines.md) — idempotent
write paths, registries-as-code, fail-closed secrets, naming conventions.
