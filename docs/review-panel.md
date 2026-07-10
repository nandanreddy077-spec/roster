# The Roster Review Panel

Structured disagreement before code. Borrowed from the idea (not the framework) behind
OneManCompany: one model converging on an elegant, unchallenged solution is a risk. Every
major spec/PRD passes through seven roles that each critique it from one fixed perspective
**before a line of code is written.**

## The gate rule

- No **major** change (new feature, new flow, pricing/positioning shift, anything a customer
  touches) goes to implementation without a panel pass. Small fixes and mechanical work skip it.
- Each role returns **SIGN-OFF** or a **BLOCKING OBJECTION**. A blocking objection must name the
  *specific change* that would turn it into a sign-off — no vague "I don't like it."
- **Convergence is the signal.** If the roles independently land on the same concern, that's the
  real problem. If they scatter, the spec is probably unfocused.
- The **founder (you) is the CEO of record** and can override any role's objection — but the
  override is logged here with the reason, so we can learn from it later. (Example already on
  the books: 2026-07-10, founder chose live-voice over SMS for the core loop despite the
  Architect/CEO flagging it as the higher-risk, unverified path.)

## The one filter above all roles

Every proposal is judged against a single question first: **does this reduce time-to-value or
increase the odds of the first 10 paying customers?** If not, it's complexity — cut it or defer
it, no matter how elegant.

## The seven roles

| Role | Agent | The one question it must answer |
|---|---|---|
| **CEO** | `panel-ceo` | Is this the single highest-leverage thing to build *this week* for first-10-customers — and what are we choosing *not* to do by doing it? |
| **Head of Product** | `panel-product` | Does this solve a real, present customer problem and reduce time-to-value — or is it complexity dressed up as progress? |
| **Customer Advocate** | `panel-customer` | Would a non-technical 5-truck plumbing owner understand it, trust it (not "scam"), and pay? Where exactly do they get confused or bail? |
| **UX Reviewer** | `panel-ux` | Can this flow be simpler / fewer steps? What is the aha moment, and is it reachable in the first 60 seconds? |
| **Technical Architect** | `panel-architect` | Is it actually buildable and reliable with what we have today? What's *unverified*? What breaks at 10 and 100 clients? |
| **Growth Lead** | `panel-growth` | Does this help acquire or retain customers, and can I *demo it to shop #1 next week*? |
| **QA** | `panel-qa` | What breaks? What are the edge cases and the worst possible first-run experience? |

## How a panel run works

1. Write the spec (`docs/superpowers/specs/…`).
2. Dispatch the seven role-agents against it (parallel where independent). Each reads the spec and
   returns its verdict + objections.
3. Collect verdicts. Resolve blocking objections by revising the spec, or log a founder override.
4. Only then → implementation plan → build.

The role-agents live in `.claude/agents/panel-*.md`. Existing plan-review skills
(`plan-ceo-review`, `plan-eng-review`, `plan-design-review`, `plan-devex-review`) can stand in
for CEO / Architect / UX interactively when you want a live back-and-forth instead of a written pass.

## Override log

- **2026-07-10 — Core loop: live voice over SMS.** Panel (Architect, CEO) flagged live-voice
  (xAI Voice Agent API) as higher-risk and unverified end-to-end vs. the shippable SMS text-back
  path. Founder chose live voice as the better product/demo. **Consequence the panel attached to
  the override:** the first work is a *de-risking spike* — prove xAI voice answers a call and holds
  a real conversation against our engine — before any signup/provisioning UI is built around it.
