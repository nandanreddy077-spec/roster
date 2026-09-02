# Roster — Customer Dashboard Architecture Invariants

_Frozen 2026-07-29, at the close of the departments migration (Phase 5). This
is the review checklist for every future PR that touches `/v2/dashboard*`.
An invariant here is not a style preference — each one exists because
violating it caused a real bug during the migration, listed inline. Don't
relax one without the same conversation that established it._

Grounding: the product shape is
[`docs/superpowers/specs/2026-07-28-departments-product-blueprint-design.md`](docs/superpowers/specs/2026-07-28-departments-product-blueprint-design.md);
the build history is
[`docs/superpowers/plans/2026-07-28-departments-migration-execution-plan.md`](docs/superpowers/plans/2026-07-28-departments-migration-execution-plan.md)
and
[`docs/superpowers/plans/2026-07-29-phase-5-customer-dashboard.md`](docs/superpowers/plans/2026-07-29-phase-5-customer-dashboard.md).

## The layering

```
Registry layer            departments.REGISTRY, employees.REGISTRY,
                           metrics.METRIC_RECORDS / EMPLOYEE_RECORDS
        ↓
Shared domain helpers      departments.department_status_for,
                           workspace._status_for / _employee_views
        ↓
View models                DepartmentWorkspace, EmployeeWorkspace,
  (workspace.py)           ExpansionWorkspace, BriefingWorkspace
        ↓
Templates                  dashboard_v2/*.html — layout only
        ↓
Navigation                 portal.NAV_ITEMS — stable customer concepts
```

Every customer-facing page in the migration follows this exact shape. A new
page that doesn't fit this pipeline is a signal to stop and reconsider, not
to special-case it.

## The invariants

1. **Templates never assemble business objects.** A template receives one
   already-built view model and lays it out — it never imports
   `departments.py`, `metrics.py`, or queries the database itself.
   *Enforced by construction*: every `dashboard_v2/*.html` route in
   `portal.py` passes exactly one `workspace=` object; templates never
   receive raw `DepartmentStatus`/`Employee`/`Job` rows.

2. **Registries are the only source of configuration.** Department
   copy (`departments.REGISTRY`), employee definitions
   (`employees.REGISTRY`), and metric/record declarations
   (`metrics.METRIC_RECORDS`, `metrics.EMPLOYEE_RECORDS`) are code, not
   duplicated in a template, a route, or a second table. Renaming a
   department's mission or adding an employee is a one-line registry change.

3. **View models are the only composition layer.** `workspace.py` is the one
   place that joins a registry with a domain helper. Each view model has
   exactly one builder (`build_department_workspace`,
   `build_employee_workspace`, `build_expansion_workspace`,
   `build_briefing_workspace`) and each is structurally guarded — a test
   asserts `set(SomeWorkspace.__dataclass_fields__) == {...exact fields...}`
   so scope creep fails a test before it fails a code review.

4. **Business logic never lives in templates.** A template's only
   conditionals are presence/absence checks (`{% if workspace.highlights
   %}`) — never a computation, a status decision, or a label lookup a
   template invents itself. Labels are centralized dicts
   (`workspace.METRIC_LABELS`, `workspace.CUSTOMER_STATE_LABELS`,
   `workspace.NOTIFICATION_KIND_LABELS`) registered as Jinja globals.

5. **Navigation always follows the workspace hierarchy**: Home →
   Departments → Department Workspace → Employee Workspace |
   Expansion Workspace. `portal.NAV_ITEMS` names stable customer concepts,
   never implementation structure —
   `test_the_nav_names_no_implementation_structure` fails the build if a nav
   label contains "employee", "agent", "job", "campaign", "recovery",
   "roster", or "role". Expansion is deliberately never a nav item — it's
   reached contextually, from within a Department Workspace or a Home growth
   nudge, never as a permanent "buy more" tab.

   **AMENDED 2026-09-01 (founder), five destinations → three.** The nav was
   Overview / Departments / The Briefing / Notifications / Settings. Overview
   and Departments rendered the same gateway cards from one shared partial,
   and the Briefing hand-rolled a third near-identical grid — so an owner had
   no way to know which of the three to open, and the product read as a maze.
   The Briefing was the only one of the three that answered a question ("does
   anything need me?"), so it moved to the front door as **Home**, absorbing
   Overview; `dashboard_v2/overview.html`, `dashboard_v2/briefing.html` and
   `dashboard_v2/_gateway_card.html` were deleted, and `/v2/dashboard/briefing`
   became a 303 to `/v2/dashboard` (the URL is in owners' text messages).
   Notifications became a drill-down reached from Home, the same shape as the
   Employee and Expansion workspaces — a feed that is empty most days does not
   earn a permanent tab. Guarded by `test_no_two_destinations_render_the_same_page`,
   which is the regression this amendment exists to prevent.

   The invariant this amendment does NOT relax: adding a destination is still
   the thing to resist. Three is the new ceiling, not a new floor.

6. **Every customer-visible metric must have a future drill-down path.**
   Metrics represent concrete underlying records, not opaque summary
   numbers — `metrics.METRIC_RECORDS` and `metrics.EMPLOYEE_RECORDS`
   declare, for every metric key, exactly which rows back it. See
   `test_every_metric_declares_what_records_it_drills_into`.

7. **Every drill-down must resolve to real rows.** A `RecordSource.fetch`
   always queries an actual table (`Job`, `Message`, `OwnerNotification`) —
   never a placeholder, a stub, or a hardcoded example row.

8. **Customer-visible state derives from shared helpers, never duplicated
   template logic.** Deployment state always flows through
   `departments.department_status_for` over real `Employee` rows — never
   `requested_roster`, a `tested_at` timestamp, or a hardcoded template
   badge. The founder console and the customer dashboard read the *same*
   `DepartmentStatus`; only the label maps differ
   (`CUSTOMER_STATE_LABELS` vs. the founder's own map), because
   `DepartmentStatus` is presentation-neutral by rule.

9. **No dead controls.** A link or button is only rendered when its target
   route would actually succeed. Every "Expand" link/nudge checks
   `build_expansion_workspace(...) is not None` first (the same gate the
   `/expand` route itself uses) before it's ever shown — on the Department
   Workspace, the Departments grid's `can_expand`, and the Briefing's growth
   nudges alike. When a POST and a GET share a target, they share the same
   precondition check, so a submit can never redirect to a page that 404s
   (Phase 5 Task 9's real bug: the POST route recorded interest before this
   check existed).

10. **No fabricated metrics.** If a number can't be honestly sourced today,
    it doesn't appear — not as a zero, not as a placeholder. "Reviews
    received" was dropped entirely rather than invented (no Google/Yelp
    integration exists); `ExpansionWorkspace.expected_outcomes` names
    *capability labels* for employees not yet deployed, never a fake
    zero-count for work that hasn't happened.

## One more, implicit until Phase 5 made it explicit

11. **Derive once.** A view model composes from the *already-composed*
    models beneath it, never by re-querying what a lower layer already
    computed. `BriefingWorkspace` is built from `DepartmentWorkspace` and
    `ExpansionWorkspace` — deliberately **not** from
    `build_employee_workspace` per employee, because that call re-derives
    `build_department_workspace` (hence a fresh `Employee` query) on top of
    the loop's own call. When a shared computation is cheap and bounded
    (the department registry is a fixed ~6 entries, never business-scaled),
    reusing the higher-level builder as directed is preferred over plumbing
    around it — see `docs/superpowers/plans/2026-07-29-phase-5-customer-dashboard.md`
    Task 10's "accepted bounded cost" note for the tradeoff reasoning.

## Product shape (companion to the invariants, not a rule to test)

The dashboard's complete hierarchy is:

```
Home → Departments → Department Workspace → Employee Workspace
                                           → Expansion Workspace
```

Resist adding new top-level pages. New work should deepen one of these
workspaces or improve the underlying AI employees — not introduce a sixth
destination. A genuinely new top-level page needs to represent an entirely
new *workspace*, not a new report or a new widget bolted onto navigation.
