# Future refactors (not implemented)

Findings from the 2026-07-30 documentation/hygiene pass that would improve
the codebase but touch production code or historical records — out of scope
for that pass, which was documentation-only by agreement. Nothing below has
been implemented. Under `ROADMAP.md`'s Development Policy, refactors are
welcome when they preserve behavior, keep tests passing, land in small
reviewable commits, and aren't unnecessary rewrites — each item below should
still be its own reviewed change with tests run before/after, and anything
touching `/v2/dashboard*` must still satisfy `ARCHITECTURE.md`'s frozen
invariants (a permanent contract, not a calendar-gated one).

## 1. Remove dead `agent/landing/index.html`

**Finding:** `app.py`'s `root()` (line 205) and `/preview` (line 225) both
serve `index-v2.html`. The original pre-rewrite `index.html` is no longer
referenced by any route.
**Impact:** trivial — one unused static file.
**Plan:** confirm no external link/bookmark still points at it being served
some other way (it isn't, per the routes in `docs/api.md`), then delete it
and its route-adjacent references in one small PR, with a test asserting `/`
and `/preview` still 200.

## 2. `AGENTS.md` vs `CLAUDE.md` duplication

**Finding:** `AGENTS.md` (read by Codex-CLI-style tools) is a near-exact
subset of `CLAUDE.md`'s "Design System" section — same paragraph, nothing
else.
**Impact:** low — but a future edit to one and not the other silently
desyncs the instruction both tools follow.
**Plan:** decide which file is canonical and make the other point at it
(e.g. `AGENTS.md` becomes a one-line "see CLAUDE.md" pointer), or confirm via
whichever tool reads `AGENTS.md` that a pointer file is honored before
changing it.

## 3. Stale specs/plans without a "superseded" marker

**Finding:** these describe designs that were later replaced, but (unlike
the 2026-07-21 spec, which already says "SUPERSEDED 2026-07-28" at the top)
carry no marker of their own:

| File | Why it's stale |
|---|---|
| `docs/superpowers/specs/2026-07-01-ai-receptionist-design.md` + its plan | Describes the Vapi voice integration; replaced by the xAI adapter (`agent/xai_voice_adapter.py`) |
| `docs/superpowers/specs/2026-07-03-voice-endpoint-auth-design.md` + its plan | Auth design for the since-replaced voice provider |
| `docs/superpowers/plans/2026-07-06-self-serve-platform-pivot.md` | Draft "any business" platform proposal, left unresolved, no decision recorded either way |
| `docs/superpowers/specs/2026-07-10-self-serve-signup-dashboard-design.md` + its plan | Self-serve onboarding flow slated for full retirement in Phase 7 of the departments migration |
| `docs/superpowers/plans/2026-07-21-ai-staffing-repositioning.md` | Its own spec is marked superseded 2026-07-28; the plan file isn't |

**Impact:** low (reading confusion only — someone unfamiliar with the
project's history could act on a stale design), but real: this is exactly
the kind of cross-file inconsistency the original cleanup request called
out.
**Plan:** add a one-line status banner (`> **Status: Superseded — see
X.md**`) to the top of each. Deliberately not done in this pass — these are
historical decision records, and editing them wasn't part of the agreed
hygiene scope; a human should confirm each supersession claim first.

## 4. Four separate hand-written "superseded by the departments pivot" banners

**Finding:** `ROADMAP.md`, `SALES.md`, the platform-architecture PRD, and the
2026-07-21 spec each carry their own ad hoc "this is superseded" notice
rather than pointing at one canonical status source.
**Impact:** low-medium — the same fact is asserted four times; if the
departments migration status changes again, all four need a manual update.
**Plan:** consider a single canonical status line (e.g. at the top of
`ARCHITECTURE.md`, which is already the frozen source of truth for the
migration) that the other three link to instead of restating.

## 5. `agent/` is a flat 43-module package

**Finding:** every module lives directly in `agent/` with no subpackages.
`docs/architecture.md`'s table groups them logically (entry points, business
logic, background jobs, data, integrations, view models) but the filesystem
doesn't reflect that grouping.
**Impact:** medium — works fine today; would start to hurt navigability past
roughly double the current file count.
**Plan:** if/when this is worth doing, split into subpackages along the same
lines as that table (e.g. `agent/services/`, `agent/integrations/`), one
subpackage at a time, each behind passing tests — not a single big-bang move,
per `ROADMAP.md`'s Development Policy. Any part of this that touches
`/v2/dashboard*` must still satisfy `ARCHITECTURE.md`'s frozen invariants.

## 6. No CI configured

**Finding:** no `.github/workflows/` or equivalent exists — `pytest` only
runs when someone runs it locally.
**Impact:** medium — a broken test can reach `main` unnoticed.
**Plan:** a minimal workflow (checkout, `pip install -r agent/requirements.txt`,
`pytest agent/`) on push/PR would close this; deliberately not added here
since standing up CI is a CI/CD pipeline change, outside a docs-only pass.

## 7. `agent/README.md`'s "Pieces" table predates the departments migration

**Finding:** the table at the bottom of `agent/README.md` lists `engine.py`,
`service.py`, `app.py`, `db_models.py`, `channels.py`, `seed.py`, and the
recovery files, but not `departments.py`, `employees.py`, `workspace.py`,
`metrics.py`, or `portal.py` — all central to the current dashboard.
**Impact:** low — the rest of that README is accurate and detailed; only
this one table is behind.
**Plan:** extend the table (or point it at `docs/architecture.md`'s fuller
table) in a small follow-up edit.

## Not a refactor — a business decision

**No `LICENSE` file was added.** The original request included one, but this
is a pre-revenue, proprietary product being pitched to investors and paying
customers, not an open-source project. Adding a permissive license would
mean actually granting rights to the code, not decoration — that call
belongs to you, not this cleanup pass.
