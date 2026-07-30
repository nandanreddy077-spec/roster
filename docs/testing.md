# Testing

## Running tests

```bash
cd agent
source .venv/bin/activate
pytest                     # whole suite
pytest tests/test_engine.py -v      # one file
pytest -k "recovery"       # by keyword
```

`conftest.py` provides the shared fixtures (test DB engine/session, admin
password, a stub agent) — no separate test database setup needed.

## Layout

All tests live flat in `agent/tests/`, one `test_*.py` per module under test
(78 files). There's no `unit/` vs `integration/` split; tests are organized
by the feature they cover instead:

| Area | Example files |
|---|---|
| Portal / customer dashboard | `test_portal_dashboard.py`, `test_portal_dashboard_v2.py`, `test_portal_department_workspace.py`, `test_portal_employee_workspace.py`, `test_portal_onboarding.py`, `test_portal_nav.py` |
| Revenue Recovery | `test_recovery_engine.py`, `test_recovery_service.py`, `test_recovery_tick.py`, `test_recovery_trial_cap.py` |
| Referrals / Reviews | `test_referral_engine.py`, `test_referral_service.py`, `test_review_engine.py`, `test_review_service.py` |
| Data / migrations | `test_db.py`, `test_db_models.py`, `test_customer_id_columns.py`, `test_backfill_customers.py` |
| Employees / departments | `test_employees.py`, `test_departments.py`, `test_employee_deploy.py`, `test_employee_workspace.py` |
| Voice | `test_xai_voice_adapter.py`, `test_voice_loop_integration.py`, `test_twilio_signature.py` |
| Cross-cutting | `test_eventbus.py`, `test_metrics.py`, `test_trial_cap.py`, `test_session_secret.py` |

The frozen dashboard invariants in [`../ARCHITECTURE.md`](../ARCHITECTURE.md)
are each backed by a specific test named in that file (e.g.
`test_every_metric_declares_what_records_it_drills_into`,
`test_the_nav_names_no_implementation_structure`) — those are the tests to
run first if you're touching `/v2/dashboard*`.

## Writing a new test

Follow the existing convention: one `test_<module>.py` per file under test,
using the fixtures in `conftest.py`. There's no fixtures/helpers subfolder —
shared setup belongs in `conftest.py`, not scattered per-file.
