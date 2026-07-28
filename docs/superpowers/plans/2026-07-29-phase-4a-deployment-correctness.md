# Phase 4a — Deployment Correctness: Task-Level Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `Employee` rows a trustworthy record of what is actually
deployed — created immediately, impossible to duplicate, impossible to fake
with an engine-less role — *before* any screen renders from them.

**Architecture:** Collapse three deployment paths into **one** (`deployment.py`),
guarded by a **database** uniqueness constraint. Migration bug fix only: **no
new customer or founder UI.** Every change is to existing behavior that Phases
4b and 5 are about to depend on.

**Tech Stack:** Python 3, SQLModel/SQLAlchemy, pytest. No new dependency.

**Parent plan:** `docs/superpowers/plans/2026-07-28-departments-migration-execution-plan.md` (Phase 4a)
**Grounding audit:** `docs/superpowers/specs/2026-07-29-phase-4-deployment-path-audit.md` (findings F1–F8, invariants I1–I14)

## Global Constraints

- Run tests from `agent/`: `cd agent && .venv/bin/python -m pytest tests/ -q`.
- Baseline to preserve: **378 passing** (end of Phase 3).
- **No new UI.** No template, no new route, no change to any rendered page. A diff touching `agent/templates/` means the phase has drifted.
- **One deployment writer.** At the end of this phase, `deployment.py` is the only module that constructs an `Employee` row outside `db.py`'s backfill.
- Must work identically on **SQLite and Postgres**.
- **`test_runner.py` is the one file where pre-existing tests are deliberately modified** — its assertions encode the `requested_roster` behavior being migrated away from (I6). Every other pre-existing test stays untouched.
- Commit after every task; each task leaves the full suite green on its own.

---

## Invariant → Task Map

The audit's 14 invariants, and where each is satisfied or deferred.

| Invariant | Task | Test |
|---|---|---|
| **I1** Rows created immediately, not at boot | 3 | `test_activate_frontdesk_creates_the_employee_row_immediately` |
| **I2** Idempotent deployment | 2 | `test_deploying_the_same_role_twice_creates_one_row` |
| **I3** Duplicates impossible under concurrency | 1 | `test_the_database_rejects_a_duplicate_employee` |
| **I4** Every deployed key resolves to a department | 2 | `test_every_deployable_role_resolves_to_a_department` |
| **I5** `planned` employees cannot deploy | 2 | `test_deploying_a_planned_employee_is_rejected` |
| **I6** `is_active` parity | 4 | `test_is_active_parity_*` (4 scenarios) |
| **I7** Delete cascade complete | 5 | `test_delete_client_removes_interests_and_notifications` |
| **I8** Backfill stays idempotent, no resurrection | 1, 3 | existing `test_backfill_employees_idempotent` + `test_backfill_is_a_no_op_once_deployment_creates_rows` |
| **I9** Notification only after rows commit | — | **DEFERRED** — see below |
| **I10** Re-running completes a partial deploy | 2 | `test_re_running_a_partial_deployment_completes_it` |
| **I11** Interest deploys nothing | — | Phase 3's two guards; must stay green |
| **I12** Business isolation | 2 | `test_deploying_for_one_business_never_touches_another` |
| **I13** Retention key stays `"retention"` | 1 | existing `test_retention_manager_maps_to_canonical_key` stays green |
| **I14** `frontdesk_live` and the row agree | 3 | `test_frontdesk_live_and_the_employee_row_agree` |

**I5 note:** `reviews` is `internal`, not `live` — both count as deployable.
Only `planned` is rejected.

### I9 is explicitly deferred (founder, 2026-07-29)

Phase 4a **does not send an owner notification when a department is
deployed**, and therefore **does not create an `OwnerNotification` row for
it.** The reason is that `delivered` has one meaning and must keep it:

- `True` — an outbound notification was successfully delivered.
- `False` — an outbound notification was attempted and failed.

A deployment row with no send behind it fits neither, and writing one purely
as an activity record would overload the column into "something happened."
So Phase 4a touches `notifications.py` **not at all**.

**When the deferral lifts:** if "your department is live" becomes a real
product notification (a candidate for Phase 4b, where the ops console decides
whether go-live warrants a text), it ships *with* a send, and I9's ordering
test is written then — asserting the `Employee` rows are already queryable at
the moment the notification is recorded. The ordering constraint itself is
already documented in the audit (§3) so it is not lost.

**I14 is satisfied narrowly, deliberately:** `activate_frontdesk` creates the
row, and `runner.is_active` moves onto `Employee` rows (Task 4), leaving
`frontdesk_live` as the **activation-lifecycle flag** the portal redirects on.
Removing it entirely belongs to Phase 7's cleanup.

---

## Task 1: The unique-index migration (detect → normalize → index → prove)

Four steps in the founder's requested order, in one task because they are one
reversible unit: the index cannot be created before duplicates are gone, and
the proof is meaningless without the index.

**Files:**
- Modify: `agent/db_models.py` — declare the constraint (fresh databases)
- Modify: `agent/db.py` — `_dedupe_employees()` + `_migrate_add_indexes()`, called from `_init_db_locked`
- Test: `agent/tests/test_employee_dedupe.py` (new)

**Interfaces:**
- Produces: `db._dedupe_employees(engine=None) -> list[int]` (ids removed, for logging/tests); `db._migrate_add_indexes()`.
- `db_models.Employee` gains `__table_args__ = (Index("uq_employee_business_role", "business_id", "role_key", unique=True),)`.
- Task 1 also proves the WHOLE migration is idempotent by running `_init_db_locked()` twice against a database seeded with duplicates (founder request, 2026-07-29): no rows removed the second time, none recreated, index still present, no errors.

- [ ] **Step 1: Write the failing tests**

Create `agent/tests/test_employee_dedupe.py`:

```python
"""The employee uniqueness migration.

audit F2 (verified empirically): create_all() does NOT add an index to a table
that already exists, so the constraint needs real DDL for existing databases —
and that DDL can only run once duplicates are gone.

audit F3: nothing prevents duplicates today. _hire_employee is a
SELECT-then-INSERT with no constraint behind it, and FastAPI runs sync handlers
in a threadpool even at --workers 1."""
import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from db import _dedupe_employees, _migrate_add_indexes
from db_models import Business, Employee


def _business(session, email):
    b = Business(business_name="B", trade="hvac", email=email)
    session.add(b)
    session.commit()
    session.refresh(b)
    return b


def test_dedupe_keeps_the_oldest_row_and_reports_what_it_removed(test_engine):
    """Deterministic rule: keep the LOWEST id per (business_id, role_key).
    Nothing has ever written Employee.status (audit: no pause/resume/fire
    exists), so no duplicate can carry state worth preserving over another —
    oldest-wins is safe as well as deterministic."""
    with Session(test_engine) as s:
        b = _business(s, "dedupe1@test.io")
        keep = Employee(business_id=b.id, role_key="frontdesk", display_name="first")
        s.add(keep)
        s.commit()
        s.refresh(keep)
        s.add(Employee(business_id=b.id, role_key="frontdesk", display_name="second"))
        s.add(Employee(business_id=b.id, role_key="frontdesk", display_name="third"))
        s.commit()
        business_id, keep_id = b.id, keep.id

    removed = _dedupe_employees(test_engine)

    assert len(removed) == 2
    with Session(test_engine) as s:
        rows = s.exec(select(Employee).where(Employee.business_id == business_id)).all()
        assert [r.id for r in rows] == [keep_id]
        assert rows[0].display_name == "first"


def test_dedupe_leaves_distinct_roles_alone(test_engine):
    with Session(test_engine) as s:
        b = _business(s, "dedupe2@test.io")
        s.add(Employee(business_id=b.id, role_key="frontdesk"))
        s.add(Employee(business_id=b.id, role_key="quote_chaser"))
        s.commit()
        business_id = b.id

    assert _dedupe_employees(test_engine) == []

    with Session(test_engine) as s:
        assert len(s.exec(select(Employee).where(Employee.business_id == business_id)).all()) == 2


def test_dedupe_never_merges_across_businesses(test_engine):
    """I12: the same role_key at two businesses is not a duplicate."""
    with Session(test_engine) as s:
        a = _business(s, "dedupe3a@test.io")
        b = _business(s, "dedupe3b@test.io")
        s.add(Employee(business_id=a.id, role_key="frontdesk"))
        s.add(Employee(business_id=b.id, role_key="frontdesk"))
        s.commit()

    assert _dedupe_employees(test_engine) == []

    with Session(test_engine) as s:
        assert len(s.exec(select(Employee)).all()) == 2


def test_dedupe_is_idempotent(test_engine):
    with Session(test_engine) as s:
        b = _business(s, "dedupe4@test.io")
        s.add(Employee(business_id=b.id, role_key="frontdesk"))
        s.add(Employee(business_id=b.id, role_key="frontdesk"))
        s.commit()

    first = _dedupe_employees(test_engine)
    second = _dedupe_employees(test_engine)

    assert len(first) == 1
    assert second == []


def test_the_index_migration_creates_the_unique_index(test_engine):
    """audit F2: this is the step create_all() cannot do for an existing
    table. Asserting the index EXISTS by name is what proves the DDL ran,
    rather than inferring it from behavior that a fresh table would also
    show."""
    _migrate_add_indexes(test_engine)

    names = {i["name"] for i in inspect(test_engine).get_indexes("employee")}
    assert "uq_employee_business_role" in names


def test_the_index_migration_is_idempotent(test_engine):
    _migrate_add_indexes(test_engine)
    _migrate_add_indexes(test_engine)  # must not raise

    names = {i["name"] for i in inspect(test_engine).get_indexes("employee")}
    assert "uq_employee_business_role" in names


def test_the_database_rejects_a_duplicate_employee(test_engine):
    """I3. The whole point of the migration: after it, a duplicate is
    impossible rather than merely unlikely."""
    _migrate_add_indexes(test_engine)

    with Session(test_engine) as s:
        b = _business(s, "dupe@test.io")
        s.add(Employee(business_id=b.id, role_key="frontdesk"))
        s.commit()

        s.add(Employee(business_id=b.id, role_key="frontdesk"))
        with pytest.raises(IntegrityError):
            s.commit()
```

- [ ] **Step 2: Run and confirm failure**

Run: `cd agent && .venv/bin/python -m pytest tests/test_employee_dedupe.py -q 2>&1 | tail -6`
Expected: `ImportError: cannot import name '_dedupe_employees' from 'db'`.

- [ ] **Step 3: Declare the constraint on the model (fresh databases)**

In `agent/db_models.py`, add to `Employee`:

```python
class Employee(SQLModel, table=True):
    # Fresh databases get this via create_all. EXISTING databases do not —
    # create_all skips a table that already exists, indexes included (verified:
    # docs/superpowers/specs/2026-07-29-phase-4-deployment-path-audit.md F2) —
    # so db._migrate_add_indexes issues the DDL for those.
    __table_args__ = (
        # Index, NOT UniqueConstraint: a table-level UNIQUE becomes part of
        # CREATE TABLE under an auto-generated name, so `CREATE UNIQUE INDEX IF
        # NOT EXISTS uq_employee_business_role` would then build a SECOND,
        # separately-named mechanism enforcing the same rule. A named unique
        # Index means fresh databases (create_all) and existing ones
        # (_migrate_add_indexes) converge on exactly one object with one name.
        Index("uq_employee_business_role", "business_id", "role_key", unique=True),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    ...
```

(The remaining fields are unchanged.)

- [ ] **Step 4: Add de-duplication and the index DDL**

In `agent/db.py`, add both functions and wire them into `_init_db_locked`:

```python
def _dedupe_employees(engine=None) -> list:
    """Remove duplicate (business_id, role_key) Employee rows, keeping the
    LOWEST id — the oldest, and the one any historical reference would point
    at. Returns the ids removed so the migration is auditable rather than
    silent.

    Deterministic and safe because nothing has ever written Employee.status
    (there is no pause/resume/fire path anywhere), so no duplicate can carry
    state that another lacks. Must run BEFORE _migrate_add_indexes: the unique
    index cannot be created while violations exist.
    """
    import sys
    from sqlmodel import Session, select
    from db_models import Employee

    eng = engine if engine is not None else globals()["engine"]
    removed = []
    with Session(eng) as s:
        seen = {}
        for e in s.exec(select(Employee).order_by(Employee.id)).all():
            key = (e.business_id, e.role_key)
            if key in seen:
                removed.append(e.id)
                s.delete(e)
            else:
                seen[key] = e.id
        if removed:
            s.commit()
            print(f"[migration] removed {len(removed)} duplicate employee rows: {removed}",
                  file=sys.stderr)
    return removed


def _migrate_add_indexes(engine=None):
    """Indexes added to tables that ALREADY exist. SQLModel.create_all() only
    builds indexes as part of creating a table, so a constraint added to an
    existing model never reaches an existing database without this (audit F2).

    Idempotent via IF NOT EXISTS. Run only after _dedupe_employees.
    """
    from sqlalchemy import text

    eng = engine if engine is not None else globals()["engine"]
    statements = (
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_employee_business_role "
        "ON employee (business_id, role_key)",
    )
    with eng.connect() as conn:
        for ddl in statements:
            try:
                conn.execute(text(ddl))
                conn.commit()
            except Exception as e:
                # A pre-existing violation is the one real failure mode here,
                # and it must be loud: the constraint silently not existing is
                # exactly the state this migration exists to end.
                import sys
                print(f"[migration] FAILED to create index: {ddl} — {e}", file=sys.stderr)
                conn.rollback()
```

…and in `_init_db_locked`, after the existing backfills:

```python
def _init_db_locked():
    _migrate_rename_client_to_business()
    SQLModel.metadata.create_all(engine)
    _migrate_add_columns()
    _backfill_customers()
    _backfill_employees()
    # Order matters: duplicates must be gone before the unique index is built.
    _dedupe_employees()
    _migrate_add_indexes()
```

- [ ] **Step 5: Run new tests, then the full suite**

Run: `cd agent && .venv/bin/python -m pytest tests/test_employee_dedupe.py -v`
Expected: 8 passed.

Run: `cd agent && .venv/bin/python -m pytest tests/ -q`
Expected: `386 passed`. In particular `test_employee_model.py` must stay green
unmodified — including `test_retention_manager_maps_to_canonical_key` (I13)
and `test_backfill_employees_idempotent` (I8).

- [ ] **Step 6: Commit**

```bash
git add agent/db_models.py agent/db.py agent/tests/test_employee_dedupe.py
git commit -m "fix(migration): make duplicate Employee rows impossible

Four steps, one reversible unit: detect duplicates, normalize them
deterministically (keep the lowest id — safe because nothing has ever
written Employee.status), create the unique index, prove duplicates now
raise.

The index needs real DDL rather than a model change: create_all() skips a
table that already exists, indexes included (verified empirically, audit
F2). The model declaration covers fresh databases; _migrate_add_indexes
covers existing ones. Both are idempotent, and de-duplication logs every
row it removes rather than deleting silently.

Satisfies I3; keeps I8 and I13 green."
```

---

## Task 2: `deployment.py` — the single deployment path

**Files:**
- Modify: `agent/departments.py` — expose `canonical_role_key()` and `deployable_employees_for()`
- Create: `agent/deployment.py`
- Test: `agent/tests/test_deployment.py` (new)

**Interfaces:**
- Produces:
  - `departments.canonical_role_key(role_key) -> str` — resolves legacy spellings (`"retention"` → `"retention_manager"`); makes the previously-private `_LEGACY_ROLE_KEYS` usable by `runner.py` in Task 5.
  - `departments.deployable_employees_for(department_key) -> list[EmployeeDefinition]` — registry entries with status `live` or `internal`. **Never `planned`** (audit F4).
  - `deployment.deploy_role(session, business_id, role_key) -> Employee | None` — returns the row (new or existing), or `None` if nothing was created because it already existed. Raises `ValueError` for unknown or `planned` roles.
  - `deployment.deploy_department(session, business_id, department_key) -> list[Employee]` — every deployable role in the department; returns only the rows **newly** created.

- [ ] **Step 1: Write the failing tests**

Create `agent/tests/test_deployment.py`:

```python
"""The single deployment path. Before this module, three separate places
created Employee rows (db.py's backfill x2, portal.py's _hire_employee) and
two more marked a business deployed without creating one at all
(activation.activate_frontdesk, app.deploy_employee) — audit F1."""
import pytest
from sqlmodel import Session, select

from db_models import Business, Employee
from deployment import deploy_department, deploy_role


def _business(session, email):
    b = Business(business_name="B", trade="hvac", email=email)
    session.add(b)
    session.commit()
    session.refresh(b)
    return b


def test_deploy_role_creates_the_employee_row(session):
    b = _business(session, "dep1@test.io")

    row = deploy_role(session, b.id, "frontdesk")

    assert row is not None
    assert row.id is not None
    assert row.role_key == "frontdesk"
    assert row.status == "active"


def test_deploying_the_same_role_twice_creates_one_row(session):
    """I2. Idempotent: the second call is a no-op that reports it created
    nothing, so a caller can tell a fresh deploy from a repeat."""
    b = _business(session, "dep2@test.io")

    first = deploy_role(session, b.id, "frontdesk")
    second = deploy_role(session, b.id, "frontdesk")

    assert first is not None
    assert second is None
    assert len(session.exec(
        select(Employee).where(Employee.business_id == b.id)
    ).all()) == 1


def test_deploying_a_planned_employee_is_rejected(session):
    """I5 / audit F4. `dispatcher` has no engine — status `planned` in the
    registry. Creating a row for it would make active_departments_for()
    report Operations as STAFFED, and the customer's dashboard would show a
    department that cannot do anything."""
    b = _business(session, "dep3@test.io")

    with pytest.raises(ValueError):
        deploy_role(session, b.id, "dispatcher")

    assert session.exec(select(Employee).where(Employee.business_id == b.id)).all() == []


def test_deploying_an_unknown_role_is_rejected(session):
    b = _business(session, "dep4@test.io")

    with pytest.raises(ValueError):
        deploy_role(session, b.id, "not_a_role")


def test_deploying_for_one_business_never_touches_another(session):
    """I12. Business isolation is the security boundary (platform PRD §12)."""
    a = _business(session, "dep5a@test.io")
    b = _business(session, "dep5b@test.io")

    deploy_role(session, a.id, "frontdesk")

    assert session.exec(select(Employee).where(Employee.business_id == b.id)).all() == []


def test_deploy_department_creates_every_deployable_role(session):
    """Customer Service = frontdesk (live) + reviews (internal). `support` is
    planned and must be skipped, not deployed."""
    b = _business(session, "dep6@test.io")

    created = deploy_department(session, b.id, "customer_service")

    keys = {e.role_key for e in created}
    assert keys == {"frontdesk", "reviews"}
    assert "support" not in keys


def test_deploy_department_is_idempotent(session):
    b = _business(session, "dep7@test.io")

    deploy_department(session, b.id, "customer_service")
    second = deploy_department(session, b.id, "customer_service")

    assert second == []
    assert len(session.exec(
        select(Employee).where(Employee.business_id == b.id)
    ).all()) == 2


def test_deploy_department_rejects_a_department_with_nothing_deployable(session):
    """audit F4: Operations, Finance and Marketing are hireable in the
    registry but have zero live/internal employees today. Refusing loudly is
    what stops the ops console from 'deploying' vaporware."""
    b = _business(session, "dep8@test.io")

    with pytest.raises(ValueError):
        deploy_department(session, b.id, "operations")


def test_deploy_department_rejects_leadership(session):
    b = _business(session, "dep9@test.io")

    with pytest.raises(ValueError):
        deploy_department(session, b.id, "leadership")


def test_every_deployable_role_resolves_to_a_department(session):
    """I4. A deployed role whose key doesn't resolve would vanish from every
    department view — the exact failure Phase 1's alias exists to prevent,
    asserted here against what deployment can actually write."""
    from departments import REGISTRY, department_for_role, deployable_employees_for

    for department in REGISTRY:
        for employee in deployable_employees_for(department.key):
            assert department_for_role(employee.key) is not None, employee.key
```

- [ ] **Step 2: Run and confirm failure**

Run: `cd agent && .venv/bin/python -m pytest tests/test_deployment.py -q 2>&1 | tail -5`
Expected: `ModuleNotFoundError: No module named 'deployment'`.

- [ ] **Step 3: Extend `departments.py`**

Append to `agent/departments.py`:

```python
def canonical_role_key(role_key: str) -> str:
    """The registry spelling of a role key, resolving legacy spellings.
    roles.ROLE_KEYS emits "retention" where the registry says
    "retention_manager", and that spelling is on real Employee rows
    (test_employee_model.py pins it), so callers comparing a stored key to
    the registry must normalize through here."""
    return _LEGACY_ROLE_KEYS.get(role_key, role_key)


def deployable_employees_for(department_key: str) -> List:
    """The employees in this department that can actually be provisioned —
    registry status `live` or `internal`. NEVER `planned`: those have no
    engine, and deploying one would make active_departments_for() report the
    department as staffed while it cannot do any work (audit F4)."""
    return [
        e for e in _EMPLOYEE_REGISTRY
        if e.department == department_key and e.status in ("live", "internal")
    ]
```

- [ ] **Step 4: Create `deployment.py`**

Create `agent/deployment.py`:

```python
"""The single path that puts an AI employee to work for a business.

Before this module there were three Employee writers and two paths that marked
a business "deployed" without creating a row at all (audit F1) — so a business
showed zero departments until the app restarted. Everything now routes here.

Deployment is idempotent by design rather than by luck: a caller can re-run it
to finish a half-completed deploy, and the database's unique index makes a
duplicate impossible even under a concurrent double-submit.
"""
from typing import List, Optional

from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from db_models import Employee
from departments import canonical_role_key, deployable_employees_for, get_department
from employees import REGISTRY as _EMPLOYEE_REGISTRY

_BY_KEY = {e.key: e for e in _EMPLOYEE_REGISTRY}


def _existing(session, business_id: int, role_key: str) -> Optional[Employee]:
    return session.exec(
        select(Employee).where(
            Employee.business_id == business_id, Employee.role_key == role_key
        )
    ).first()


def deploy_role(session, business_id: int, role_key: str) -> Optional[Employee]:
    """Put one employee to work. Returns the newly-created row, or None if it
    was already deployed — so a caller can distinguish a fresh deployment from
    a repeat and avoid re-notifying (I9/I10).

    Raises ValueError for an unknown role, or one whose registry status is
    `planned`: those have no engine, and a row for one would make the
    department look staffed while it cannot do any work (audit F4).
    """
    definition = _BY_KEY.get(canonical_role_key(role_key))
    if definition is None:
        raise ValueError(f"unknown role: {role_key!r}")
    if definition.status == "planned":
        raise ValueError(
            f"{role_key!r} is planned, not deployable — it has no engine yet"
        )

    if _existing(session, business_id, role_key) is not None:
        return None

    row = Employee(
        business_id=business_id, role_key=role_key,
        display_name=definition.display_name,
    )
    session.add(row)
    try:
        session.commit()
    except IntegrityError:
        # Lost a concurrent double-submit; the unique index rejected us and
        # the winner's row is the one that counts.
        session.rollback()
        return None
    session.refresh(row)
    return row


def deploy_department(session, business_id: int, department_key: str) -> List[Employee]:
    """Staff a whole department. Returns only the rows NEWLY created, so
    re-running to complete a partial deployment reports just the gap it
    filled.

    Raises ValueError for an unknown department, a non-hireable one
    (Leadership), or one with no deployable employees — Operations, Finance
    and Marketing have none today, and silently doing nothing would let the
    ops console claim it deployed vaporware.
    """
    department = get_department(department_key)
    if department is None:
        raise ValueError(f"unknown department: {department_key!r}")
    if not department.hireable:
        raise ValueError(f"{department_key!r} is not a hireable department")

    deployable = deployable_employees_for(department_key)
    if not deployable:
        raise ValueError(
            f"{department_key!r} has no deployable employees yet — "
            "every role in it is still `planned`"
        )

    created = []
    for definition in deployable:
        row = deploy_role(session, business_id, definition.key)
        if row is not None:
            created.append(row)
    return created
```

- [ ] **Step 5: Run new tests, then the full suite**

Run: `cd agent && .venv/bin/python -m pytest tests/test_deployment.py -v`
Expected: 11 passed.

Run: `cd agent && .venv/bin/python -m pytest tests/ -q`
Expected: `397 passed`. No failures.

- [ ] **Step 6: Commit**

```bash
git add agent/departments.py agent/deployment.py agent/tests/test_deployment.py
git commit -m "feat(deployment): one path that puts an employee to work

Idempotent by design: deploy_role returns None when the row already
existed, so a caller can tell a fresh deploy from a repeat and re-run to
finish a half-completed one. A concurrent double-submit loses to the
unique index rather than duplicating.

Rejects `planned` roles — they have no engine, and a row for one would
make active_departments_for() report the department as staffed while it
cannot do any work. Also rejects departments with nothing deployable
(Operations, Finance, Marketing today), loudly rather than silently.

Satisfies I2, I4, I5, I12. No call site wired yet."
```

---

## Task 3: Wire every deployment path (I1, I8, I14)

**Files:**
- Modify: `agent/activation.py` — `activate_frontdesk` creates the row
- Modify: `agent/app.py` — the deploy route creates rows
- Modify: `agent/portal.py` — `_hire_employee` routes through `deployment.py`
- Test: `agent/tests/test_deployment.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `agent/tests/test_deployment.py`:

```python
# --- every path now creates rows immediately (I1) -----------------------------


def test_activate_frontdesk_creates_the_employee_row_immediately(session):
    """I1 / audit F1. The second of the two paths that marked a business
    deployed without creating a row — the dashboard would have shown zero
    departments until the application restarted."""
    from activation import activate_frontdesk

    b = _business(session, "wire1@test.io")
    activate_frontdesk(session, b)

    rows = session.exec(select(Employee).where(Employee.business_id == b.id)).all()
    assert [r.role_key for r in rows] == ["frontdesk"]


def test_frontdesk_live_and_the_employee_row_agree(session):
    """I14. Two fields encode the same fact; after activation they must not
    disagree. runner.is_active moves onto the Employee row in Task 5, leaving
    frontdesk_live as the activation-lifecycle flag the portal redirects on."""
    from activation import activate_frontdesk
    from departments import active_departments_for

    b = _business(session, "wire2@test.io")
    activate_frontdesk(session, b)
    session.refresh(b)

    employees = session.exec(select(Employee).where(Employee.business_id == b.id)).all()
    assert b.frontdesk_live is True
    assert [d.key for d in active_departments_for(employees)] == ["customer_service"]


def test_backfill_is_a_no_op_once_deployment_creates_rows(session, test_engine):
    """I8. The boot backfill must not double-create what deployment already
    made, and must not resurrect anything."""
    from activation import activate_frontdesk
    from db import _backfill_employees

    b = _business(session, "wire3@test.io")
    activate_frontdesk(session, b)

    _backfill_employees(session.get_bind())

    rows = session.exec(select(Employee).where(Employee.business_id == b.id)).all()
    assert len(rows) == 1
```

- [ ] **Step 2: Run and confirm failure**

Run: `cd agent && .venv/bin/python -m pytest tests/test_deployment.py -q 2>&1 | tail -6`
Expected: the three new tests FAIL — `assert [] == ['frontdesk']` and similar.
The 10 from Task 2 still pass.

- [ ] **Step 3: Wire `activate_frontdesk`**

In `agent/activation.py`, before the final commit:

```python
    client.frontdesk_live = True
    client.activated_at = datetime.utcnow()
    session.add(client)
    session.commit()
    # The Employee row IS the deployment record (audit F1): without this the
    # business shows zero departments until the app restarts and the backfill
    # runs. Best-effort like the provisioning above — activation must always
    # complete — but loud, because a missing row is invisible otherwise.
    try:
        deploy_role(session, client.id, "frontdesk")
    except Exception as e:
        print(f"[activation] failed to create frontdesk employee for business "
              f"{client.id}: {e}", file=sys.stderr)
```

…with `from deployment import deploy_role` at the top.

- [ ] **Step 4: Wire the founder deploy route and the portal hire**

In `agent/app.py`'s `deploy_employee`, after the existing `requested_roster`
append (kept until Phase 7 so `runner.is_active`'s old path stays valid until
Task 5 migrates it):

```python
            session.commit()
        # The row is the deployment record, created NOW rather than at the
        # next boot's backfill (audit F1).
        deploy_role(session, client_id, role_key)
```

In `agent/portal.py`, replace `_hire_employee`'s body with a delegation:

```python
def _hire_employee(session: Session, business_id: int, role: str) -> None:
    """Routes through deployment.py so there is exactly ONE module that
    creates Employee rows. (This whole route is deleted in Phase 7; the
    delegation keeps it correct until then rather than leaving a second
    writer alive.)"""
    try:
        deploy_role(session, business_id, role_key_for(role))
    except ValueError:
        # A role the registry doesn't consider deployable. The customer-facing
        # hire path is retired in Phase 7; until then, don't 500 on it.
        pass
```

- [ ] **Step 5: Run new tests, then the full suite**

Run: `cd agent && .venv/bin/python -m pytest tests/test_deployment.py -v`
Expected: 14 passed.

Run: `cd agent && .venv/bin/python -m pytest tests/ -q`
Expected: `400 passed`. `test_portal_dashboard.py`, `test_employee_deploy.py`,
`test_activation_live.py` and `test_employee_model.py` must all stay green
**unmodified**.

- [ ] **Step 6: Commit**

```bash
git add agent/activation.py agent/app.py agent/portal.py agent/tests/test_deployment.py
git commit -m "fix(deployment): create Employee rows immediately, not at next boot

Both paths that marked a business deployed without creating a row now
create it in the same request (audit F1): activate_frontdesk and the
founder deploy route. portal._hire_employee delegates to deployment.py so
exactly one module writes Employee rows.

Without this a provisioned business shows zero departments until the
application restarts, because Phase 5's dashboard reads these rows.

Satisfies I1 and I14; keeps I8 green."
```

---

## Task 4: Migrate `runner.is_active()` to `Employee` rows (I6)

**Files:**
- Modify: `agent/runner.py`
- Modify: `agent/tests/test_runner.py` — **the one file where pre-existing tests are deliberately rewritten**
- Test: `agent/tests/test_deployment.py` (append parity tests)

- [ ] **Step 1: Write the failing parity tests**

Append to `agent/tests/test_deployment.py`:

```python
# --- is_active parity (I6) ----------------------------------------------------


def test_is_active_parity_frontdesk(session):
    """I6: same answers before and after the migration. Frontdesk was read
    from Business.frontdesk_live; it now comes from the Employee row that
    activate_frontdesk creates alongside that flag."""
    from activation import activate_frontdesk
    from runner import is_active

    b = _business(session, "parity1@test.io")
    assert is_active(session, b, "frontdesk") is False

    activate_frontdesk(session, b)
    assert is_active(session, b, "frontdesk") is True


def test_is_active_parity_deployed_role(session):
    from runner import is_active

    b = _business(session, "parity2@test.io")
    assert is_active(session, b, "quote_chaser") is False

    deploy_role(session, b.id, "quote_chaser")
    assert is_active(session, b, "quote_chaser") is True


def test_is_active_parity_retention_legacy_key(session):
    """The stored key is "retention" (pinned by test_employee_model.py), but
    callers ask for "retention_manager". Both must answer the same, or the
    migration silently loses an employee (audit F8/I13)."""
    from runner import is_active

    b = _business(session, "parity3@test.io")
    deploy_role(session, b.id, "retention")

    assert is_active(session, b, "retention") is True
    assert is_active(session, b, "retention_manager") is True


def test_is_active_ignores_a_fired_employee(session):
    from runner import is_active

    b = _business(session, "parity4@test.io")
    row = deploy_role(session, b.id, "quote_chaser")
    row.status = "fired"
    session.add(row)
    session.commit()

    assert is_active(session, b, "quote_chaser") is False


def test_is_active_never_crosses_businesses(session):
    """I12 at the engine layer."""
    from runner import is_active

    a = _business(session, "parity5a@test.io")
    b = _business(session, "parity5b@test.io")
    deploy_role(session, a.id, "quote_chaser")

    assert is_active(session, b, "quote_chaser") is False
```

- [ ] **Step 2: Run and confirm failure**

Run: `cd agent && .venv/bin/python -m pytest tests/test_deployment.py -q 2>&1 | tail -6`
Expected: `TypeError: is_active() takes 2 positional arguments but 3 were given`.

- [ ] **Step 3: Rewrite `is_active`**

In `agent/runner.py`:

```python
def is_active(session, business: Business, role_key: str) -> bool:
    """Is `role_key` deployed and working for this business?

    Reads the Employee row, which is now the deployment record — it replaces
    the old requested_roster/frontdesk_live check, which conflated "the
    customer asked for this" with "this is running" (audit H1/F6). Legacy key
    spellings are normalized, since real rows carry "retention" while callers
    ask for "retention_manager".

    Takes a session because deployment state is a row now, not a column. The
    only caller, dispatch_job_completed, already has one.
    """
    from sqlmodel import select

    from db_models import Employee
    from departments import canonical_role_key

    wanted = canonical_role_key(role_key)
    for e in session.exec(
        select(Employee).where(Employee.business_id == business.id)
    ).all():
        if canonical_role_key(e.role_key) == wanted and e.status != "fired":
            return True
    return False
```

…and update `dispatch_job_completed` to pass the session:

```python
        if is_active(session, business, defn.role_key):
```

- [ ] **Step 4: Rewrite `test_runner.py`'s deployment-state tests**

`test_runner.py` currently constructs `Business(requested_roster=json.dumps([...]))`
with no session — those assertions encode exactly the behavior being migrated
away from. Rewrite them to deploy through `deployment.deploy_role` and pass a
session, keeping every scenario they covered (frontdesk on/off, a role
present/absent, dispatch firing only for active roles). **This is the only
pre-existing test file this phase modifies**; note it in the commit message.

- [ ] **Step 5: Run the full suite**

Run: `cd agent && .venv/bin/python -m pytest tests/test_deployment.py tests/test_runner.py -v`
Expected: 19 in `test_deployment.py`, all of `test_runner.py` green.

Run: `cd agent && .venv/bin/python -m pytest tests/ -q`
Expected: `405 passed`.

- [ ] **Step 6: Commit**

```bash
git add agent/runner.py agent/tests/test_runner.py agent/tests/test_deployment.py
git commit -m "refactor(runner): is_active reads Employee rows, not requested_roster

The old check conflated 'the customer asked for this' with 'this is
running' — requested_roster was both. Deployment state is now the Employee
row, and legacy key spellings are normalized so a stored 'retention' and a
requested 'retention_manager' answer the same.

Five parity tests cover the scenarios the old implementation handled:
frontdesk on/off, a deployed role, the retention alias, a fired employee,
and cross-business isolation.

test_runner.py is rewritten — the ONLY pre-existing test file this phase
modifies. Its assertions encoded the requested_roster behavior being
migrated away from, so preserving them verbatim would have meant
preserving the bug.

Satisfies I6."
```

---

## Task 5: Complete the delete cascade (I7)

**Files:**
- Modify: `agent/app.py` — `delete_client`
- Modify: `agent/tests/test_delete_client.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `agent/tests/test_delete_client.py`:

```python
def test_delete_client_removes_interests_and_notifications(test_engine, monkeypatch):
    """I7 / audit F7. The cascade predates both tables Phases 2 and 3 added,
    and SQLite enforces no foreign keys here (db.py sets no PRAGMA), so the
    orphans would be silent."""
    import app as app_module
    from db_models import DepartmentInterest, OwnerNotification

    monkeypatch.setattr(app_module, "engine", test_engine)
    with Session(test_engine) as s:
        b = Business(business_name="Cascade Co", trade="hvac")
        s.add(b)
        s.commit()
        s.refresh(b)
        bid = b.id
        s.add(DepartmentInterest(business_id=bid, department_key="finance"))
        s.add(OwnerNotification(business_id=bid, kind="job_booked",
                                source="sms_booking", message="m", delivered=True))
        s.commit()

    TestClient(app_module.app, headers=DASH_AUTH).post(
        f"/clients/{bid}/delete", data={"confirm_name": "Cascade Co"}
    )

    with Session(test_engine) as s:
        assert s.exec(select(DepartmentInterest).where(
            DepartmentInterest.business_id == bid)).all() == []
        assert s.exec(select(OwnerNotification).where(
            OwnerNotification.business_id == bid)).all() == []
```

- [ ] **Step 2: Run and confirm failure**

Run: `cd agent && .venv/bin/python -m pytest tests/test_delete_client.py -q 2>&1 | tail -5`
Expected: FAIL — the rows survive the delete.

- [ ] **Step 3: Extend the cascade**

In `agent/app.py`'s `delete_client`, alongside the existing deletes:

```python
        session.exec(delete(DepartmentInterest).where(DepartmentInterest.business_id == client_id))
        session.exec(delete(OwnerNotification).where(OwnerNotification.business_id == client_id))
```

…and add both to the `db_models` import.

- [ ] **Step 4: Run the full suite**

Run: `cd agent && .venv/bin/python -m pytest tests/ -q`
Expected: `406 passed`.

- [ ] **Step 5: Commit**

```bash
git add agent/app.py agent/tests/test_delete_client.py
git commit -m "fix(delete): cascade the two tables Phases 2 and 3 added

DepartmentInterest and OwnerNotification were missing from delete_client's
cascade. SQLite enforces no foreign keys here (db.py sets no PRAGMA), so
deleting a business orphaned both silently rather than erroring.

Satisfies I7."
```

---

## Phase 4a Acceptance Criteria

1. **Full suite green at 406 passed**, up from Phase 3's 378 — 28 new tests.
2. **Every invariant I1–I14 is satisfied or explicitly deferred** (see the map above). Exactly one is deferred — **I9**, because Phase 4a sends no deployment notification and therefore writes no `OwnerNotification` row for one.
3. **No UI changed** — `git diff main --stat -- agent/templates/` is empty.
4. **One deployment writer** — `grep -rn "Employee(" --include="*.py" agent/ | grep -v tests/` returns only `deployment.py` and `db.py`'s backfill.
5. **Duplicates are impossible** — a second `(business_id, role_key)` raises `IntegrityError`, and the index exists by name.
6. **De-duplication is deterministic and auditable** — keeps the lowest id, returns and logs what it removed.
7. **`test_runner.py` is the only pre-existing test file modified**, with the reason recorded in its commit.
8. **App boots** — `cd agent && .venv/bin/python -c "import app"` exits 0, and `init_db()` runs the new migration steps without error on an existing database.
9. **Five commits**, one per task, each green independently.

**Deliberately NOT in this phase:** no deployment notification (I9 deferred —
`notifications.py` is untouched), no ops-console UI, no deploy-by-department
route, no pipeline stage, no `requested_roster` removal (Phase 7 deletes it;
until then it is written but no longer read for deployment state), and no
resolution of whether `frontdesk_live` should exist at all (Phase 7).
