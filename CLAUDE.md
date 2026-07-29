## Design System
Always read DESIGN.md before making any visual or UI decisions.
All font choices, colors, spacing, and aesthetic direction are defined there.
Do not deviate without explicit user approval.

## Customer Dashboard Architecture
Always read ARCHITECTURE.md before making any change to `/v2/dashboard*` —
routes, view models, or templates. It is the frozen, PR-review checklist for
the customer dashboard's layering (registries → shared helpers → view models
→ templates → navigation). Do not violate an invariant listed there without
explicit user approval.
