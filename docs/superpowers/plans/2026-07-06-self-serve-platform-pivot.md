# Plan: Self-Serve "Any Business" AI Agent Platform (proposal under review)

Status: DRAFT — under /plan-ceo-review
Branch: main
Repo: nandanreddy077-spec/roster
Author: user proposal, submitted 2026-07-06

## Summary

Rebuild Roster as a horizontal self-serve platform:

1. Business picks its type from an industry picker (Home Services → Plumbing/HVAC/
   Roofing, Property Management → Residential/Multifamily, Dental, Law Firm,
   Insurance Agency, Staffing Agency, and more).
2. Customer sees an industry-specific Agent Library (done-for-you agents) and
   either picks one or describes a custom job in natural language.
3. A "Builder Agent" interviews the customer (via web form or live phone call),
   generates a structured Agent Definition, tests it against simulated scenarios,
   shows an Agent Readiness Score, and deploys on approval.
4. New infrastructure required: Business Brain (central per-business context
   store), Agent Runtime (generic executor for any Agent Definition), Agent
   Specification System (schema: industry, business type, role, JTBD, setup
   questions, knowledge, triggers, instructions, decision policies, actions,
   tools, permissions, guardrails, escalation, channels, success metrics,
   eval scenarios, deployment config), optional integrations (ServiceTitan,
   AppFolio, HubSpot), a recommendation engine ("Deploy Recommended Workforce"
   based on inferred business stats), and multi-agent handoff workflows.
5. Business model: platform subscription + per-agent subscription ($99–1000+/mo)
   + usage charges, moving away from commission/per-booked-job only.
6. Onboarding has two paths that both land in the same platform: self-service
   (industry picker → template → questions → deploy) and "Call Roster" (an AI
   phone interview that plays the same role as the self-service form, run by
   the Builder Agent, not a human).

## Why proposed

- The Agent Specification System is seen as the durable moat (proprietary
  operational knowledge per industry × role) versus commodity agent-builders
  (Lindy, Copilot Studio, Zapier Agents, Relevance AI, n8n).
- Removes the founder as a bottleneck in every future deployment — the Builder
  Agent, not a human, conducts onboarding, so growth isn't linear with founder
  time.
- Horizontal architecture lets Roster expand to any vertical (property
  management, dental, law, insurance, staffing) once demand appears, without
  rearchitecting.

## Conflicts with existing committed strategy

- **ROSTER.md "What we deliberately do NOT do now"** explicitly rules out:
  building all roles at once, expanding to new verticals before proving one,
  guessing integrations before customers name them, and building elaborate
  autonomous infrastructure before real conversations exist to engineer
  against. This proposal does all four in one document.
- **Existing approved design doc** (`~/.gstack/projects/nandanreddy077-spec-roster/
  nandanreddyavanaganti-main-design-20260706-005744.md`, APPROVED 2026-07-06):
  20-day YC sprint, "Sales + Receipt" approach — 70% sales, 30% build limited to
  (a) verifying the live LLM path and (b) a weekly owner receipt. Explicitly
  rejects new agents/scope during the sprint window. Current state per that
  doc: 1–5 real conversations, zero pilots live, zero revenue.
- **Pricing model conflict**: current landing page (`index.html`, "Employment
  Offer" positioning, committed 2026-07-05) sells commission-only, per-booked-
  job pricing as the explicit, market-researched differentiator versus
  incumbents. This proposal's subscription+usage model is the pricing pattern
  incumbents (Jobber, Housecall Pro) already use.
- **Trust story conflict**: current landing page FAQ promises "a human catches
  it... the founder reads every conversation." An AI-only "Call Roster" builder
  agent onboarding, with no human review before go-live, contradicts that
  promise at the exact moment (initial setup) trust matters most.

## Open question for review

Is any part of this proposal worth pulling into current scope now (e.g., the
Agent Specification System schema, written as documentation, at near-zero
cost), while deferring the platform/runtime/self-serve/multi-vertical parts
until there is real deployment history to encode? Or does the full proposal
represent the right 12-month direction and the 20-day sprint should be
revised to make room for the earliest layers of it now?
