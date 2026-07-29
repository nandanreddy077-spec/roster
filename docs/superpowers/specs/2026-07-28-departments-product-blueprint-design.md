# Roster Product Blueprint — Departments as the Primary Abstraction

**Date:** 2026-07-28 (revised same day after founder review)
**Status:** **Approved — this is the product source of truth**, revision 3.
Every implementation decision follows this document; the migration plan
adapts the existing product to match it, not the other way around. Product
design only — no code, schema, or template changes made or implied yet.
**One item stays open:** the customer-facing label chosen in §4a ("The
Briefing") is a **provisional working name**, not a final decision — it
needs to be seen in the real UI and tested with real customers before it's
locked. The underlying concept and hierarchy it names (the aggregate,
cross-department executive view, always included, gets smarter with more
departments) is fully locked along with everything else in this document.
**Relationship to prior work:** this is the target-state design. It
supersedes nothing technical — `docs/superpowers/specs/2026-07-28-departments-architecture-migration-review.md`
already maps old→new at the code level; once this blueprint is approved, that
review's migration plan gets re-sequenced against the IA defined here.
**Built as instructed:** from scratch, ignoring the existing UI/flows —
existing code is referenced only where it validates that something here is
already buildable without new infrastructure.

---

## Source of truth (restated, so this doc stands alone)

Roster is an AI workforce company for home-service businesses. Customers do
not self-serve. They contact Roster; Roster's team provisions and deploys
their AI workforce; once setup is complete, the customer receives dashboard
access. The primary customer-facing abstraction is **departments**, not
individual employees. Customers hire, manage, and monitor departments —
Customer Service, Sales, Operations, Finance, Customer Success, Marketing,
and Leadership. Employees exist inside departments as an implementation
detail, visible for transparency, never competing with departments as the
navigation or purchasing model.

---

## 1. The Core Abstraction — What a Department Is

> **A department is a business capability delivered by a coordinated team of
> specialised AI employees that share the same business context, memory, and
> objectives. Customers hire departments because they buy outcomes, not
> individual AI employees.**

Everything below follows from this one sentence, so it's worth unpacking
what it rules in and out:

- **A department is not a folder.** It isn't a UI grouping applied on top of
  a flat employee list after the fact — it's the thing being sold, staffed,
  and measured. If a capability can't be described as a business outcome
  ("estimates get followed up," "calls get answered"), it isn't a
  department-level concept.
- **A department is not one employee with a group label.** Even where only
  one employee currently does the work inside a department, the department
  is still the unit the customer thinks in — the employee is free to become
  two, or five, without the customer's mental model ever needing to change.
- **Shared context, memory, and objectives** is what makes a department
  *coordinated* rather than a bundle of coincidentally-related features —
  every employee inside a department (and every department inside a
  business) reads and writes the same business record. This is a product
  requirement, not an implementation detail: it's *why* Leadership can
  narrate one customer's journey across three departments as a single
  story (§9), and *why* hiring a second department is worth more than the
  first (it inherits everything the first one learned).
- **Departments are the sellable unit; employees are how the work gets
  done.** This is the line billing, navigation, and every page in this
  blueprint are not allowed to cross.

---

## 2. Design Principle (permanent)

> **Customers interact with departments. Roster orchestrates employees.**
> Departments are the hero. Business outcomes are second. Employee activity
> is third. Customers should never feel like they are managing AI agents.

### Permanent architectural invariant (founder, 2026-07-29)

> **Every customer-visible deployment state must derive from `Employee` rows
> through the shared deployment helpers — never from duplicated template
> logic.**

The customer dashboard and the founder ops console must answer "what is
deployed for this business?" by calling the *same* code over the *same* rows.
Any surface that recomputes deployment state from its own fields — a
`requested_roster` string, a `tested_at` timestamp, a hardcoded template
badge — will drift from the truth, and the two surfaces will tell the
customer and the Roster team different things about the same business.

This is not hypothetical: the founder console's roster was hardcoded for its
entire life before Phase 4b, and the customer dashboard's "Your office" card
derives its state from `requested_roster` and `tested_at` rather than from
deployment. Phase 5 exists in part to end that.

---

This is added at the same level of permanence as the core abstraction above
— every section from here on is a specific application of it, and any future
feature or screen gets checked against it before it ships. Two direct
consequences, stated once here so they don't need re-arguing in every
section below:

- If a screen's headline number is anything about an AI (tokens, model,
  "3 agents active"), it's wrong. The headline is always a business outcome.
- If a customer's next action is "configure," "set up," or "manage an
  agent," the flow is wrong. Their actions are limited to reading what's
  happening and talking to a human about what happens next.

---

## 3. Customer Journey — Contact Us → Internal Onboarding → Dashboard

```
CONTACT US                    INTERNAL ONBOARDING                  DASHBOARD ACCESS
(public, no login)            (Roster team, not the customer)      (customer, session login)
─────────────────             ────────────────────────────         ──────────────────────────
"Tell us about                Discovery call:                      Welcome + first login:
your business"                  - diagnose the real bottleneck        "Your workforce is live"
  name, business,                 (not a product picker)              staffing announcement —
  phone, trade,                - recommend department(s),             names which department(s)
  one line on the                 not a feature list                  are staffed and why
  pain that made                                                      (ties back to the
  them reach out                Provisioning:                        discovery call's diagnosis)
        │                        - business knowledge setup
        ▼                        - department(s) deployed                    │
  "We'll be in touch"            - numbers/channels connected                 ▼
  (sets expectation:                                                  Dashboard Overview:
  a human calls you,             Testing & QA:                        outcomes-first, empty
  not a signup email)             - Roster tests the workforce         states honestly labeled
                                    end-to-end before go-live           ("no jobs yet — give it
                                                                        a day") — never a fake
                                Go-live:                                empty dashboard
                                  - customer notified,
                                    dashboard account created
```

**Why this shape:** the customer never sees a form more complex than "tell us
what's going on" — every configuration decision (which department, what tone,
what escalation number) is *made about them*, by a human, during the
discovery call, not entered by them into a wizard. This is a stricter reading
of a principle Roster already half-committed to in `SALES.md`'s Customer
Onboarding Principle ("diagnosis before deployment") — this blueprint just
makes it the *only* path, not an aspiration alongside a self-serve wizard.

**First-login moment matters as much as signup did in the old design.** The
old product's "activation_live" reveal ("Your employee is live") is replaced
by a **staffing announcement** at the department level: *"Sales is staffed —
Quote Chaser is following up on every open estimate."* Same psychological
job (a hiring moment, not a config confirmation), scaled to departments —
and it's the first moment the Design Principle in §2 gets tested: it names
the department first, the employee only as supporting detail.

---

## 4. Dashboard Information Architecture

Top-level structure (customer-facing labels on the left; internal/registry
name in parens where they differ — see the naming note below):

| Nav item | What it shows | Empty state |
|---|---|---|
| **Overview** | Cross-department outcomes strip (jobs booked, $ recovered, calls answered, renewals scheduled — only for departments actually active) + one highlight from the executive-view page (§4a naming) + a "what happened since you last looked" digest + **contextual expansion prompts** when the data supports one (see §7) | "Your workforce is being set up — you'll see activity here once it's live." |
| **Departments** | Grid of department cards: active departments (with live outcome numbers) + inactive departments, each one educational by design (§7) — never a bare "coming soon" | N/A — always shows the full 7, honestly split active/inactive |
| *(Department detail pages)* | One page per active department — see §6 | Reached only from Overview/Departments, never bookmarked cold |
| **The Briefing** *(working name, not final — see §4a)* *(internal: "Leadership")* | The aggregate, cross-department executive view — trends, what's working, what's not, and (once data supports it) a recommendation to expand | "Your Briefing builds up as your departments get to work — check back in a few days." |
| **Notifications** | Owner-facing alerts: urgent escalations, a department going live, a job needing owner attention — mirrors today's owner-SMS but as a durable, reviewable log, not just a text that scrolls away | "Nothing needs your attention right now." |
| **Settings** | Business profile (read-mostly — the facts Roster collected at onboarding), notification preferences, billing/invoice history, account/login | N/A |

**Expansion is deliberately not a nav item.** Navigation represents how an
owner runs their business every day — Roster is an operating system, not a
storefront, and a permanent "buy more" tab in the primary nav would read as
the latter. Instead, the desire to expand is created and captured exactly
where it naturally arises: an Overview recommendation, an inactive
department card (§7), or a Briefing recommendation (§9's growth-nudge
mechanic). All three routes lead to the same place — a request into the
internal ops pipeline (§10) — there's no separate "store" to build.

**Naming note (reuses an existing, working pattern):** `roles.py` already
maps an internal name ("Frontdesk") to a trade-aware customer-facing label
("Receptionist," "CSR," "Office Manager"). The customer-facing name chosen
for the internally-tracked "Leadership" department (§4a) is the same pattern
one level up — internal ops, reporting, and the employee/department
registry can keep calling it Leadership; the customer never needs to see
that word.

### 4a. Naming "Leadership" for the customer — exploration

The founder's brief for this page is more specific than "analytics": it
aggregates every active department's outcomes into one narrative and
proactively tells the owner what it means — a chief of staff, not a
dashboard widget. The name has to carry that job, fit the existing brand
voice (`DESIGN.md`: warm, blunt, plain, zero jargon, "a well-run local
business's letterhead," explicitly *not* tech-blue/Silicon-Valley), and not
sound like it's itself another hireable employee (ruling out anything with
"Assistant," "Manager," or "Advisor" in it — that would undercut the very
point that this is a view, not a worker).

| Option | Read | Verdict |
|---|---|---|
| **The Briefing** | What you'd call a chief of staff walking you through what happened and what it means — plain, no jargon, works read at any hour, scales naturally to future premium content (forecasts, benchmarks) without renaming | **Recommended.** Best match for both the brand voice and the "executive assistant" job description. |
| The Big Picture | Plain, blunt, on-voice — but reads slightly more passive/static than a page meant to proactively recommend action | Strong runner-up; use if "Briefing" tests as too formal with real owners. |
| Business Insights (status quo) | Safe, familiar SaaS convention — but exactly the generic-analytics framing the founder flagged as underselling the page | Reject — undersells the job the page does. |
| Command Center / HQ | Common in ops tooling, connotes control-room authority | Reject — reads as tech/SaaS jargon, directly against `DESIGN.md`'s explicit anti-Silicon-Valley voice rule. |
| Today | Extremely plain, evokes a living daily brief (cf. App Store's "Today" tab) | Reject for this slot — too thin a name to carry future forecasting/benchmarking scope; revisit only if the page's ambition shrinks. |

**Working name: "The Briefing"** — provisional, not locked. It's the
strongest fit found so far against the design principle in §2 (an
outcome-first narrative, pushed to its most concentrated form, without
sounding like one more AI agent the owner has to manage), but the founder
has held the label itself open pending two things this document can't
supply: seeing it live in the actual UI, and real customer reaction. Until
then, treat every "The Briefing" reference in this blueprint as *this
concept, current best label* — safe to build against, not safe to consider
final. "The Big Picture" remains the flagged fallback if "Briefing" tests
too formal.

---

## 5. Navigation

- **Structure:** persistent left sidebar — **Overview / Departments / The
  Briefing / Notifications / Settings.** Five items, all representing how an
  owner runs the business day to day. Nothing about buying more.
- **Entry into a department page:** two paths only — clicking a department
  card from **Departments**, or clicking an outcome strip item from
  **Overview** (e.g. tapping the "12 jobs booked" number jumps straight into
  the relevant department's page, outcome-first).
- **Entry into expansion (contextual, not navigational):** an Overview
  recommendation card, an inactive department card's "Ask us about this"
  (§7), or a Briefing recommendation (§9). All three are *content*, not
  *navigation* — they appear because the data or the moment calls for them,
  not because there's a permanent slot reserved for them.
- **Breadcrumb:** `Overview > Departments > Sales` — always shows the
  department is a drill-down from the workforce as a whole, never a
  standalone app.
- **Mobile:** the owner persona (PRD's P1: "on a phone, in a truck") means
  the dashboard needs a usable mobile nav, not just the landing page — a
  bottom tab bar (Overview / Departments / Briefing / More) is the
  recommended mobile pattern over a hamburger-hidden sidebar, since the
  top items are used constantly, not occasionally. Flagged here as a
  requirement for whoever does the visual design pass (`DESIGN.md` governs
  the actual craft, per `CLAUDE.md`).

---

## 6. Department Pages (Active)

Every hireable department (Customer Service, Sales, Operations, Finance,
Customer Success, Marketing) uses **one shared template** — learn it once on
whichever department you have, understand all of them:

1. **Header** — department name + one-line mission. (`roster.html`'s
   existing taglines are already the right voice — *"Nobody works a quote,
   so it goes cold. This department doesn't let it,"* — reuse them verbatim,
   don't rewrite.)
2. **Outcomes strip** — the department's business metrics, first and
   biggest on the page. Real numbers, in dollars/counts, never AI-internal
   metrics (matches the existing `DESIGN.md`/`ROADMAP.md` rule: "real dollar
   math, not vague claims"; "employee cards ... never AI metrics"; and §2's
   design principle — outcomes rank above employee activity, always).
3. **Activity feed** — chronological, real events ("Answered a call from
   (615) ***-1234 — booked AC repair, same-day"), each line tagged with
   which employee handled it. This *is* the progressive-disclosure moment:
   the outcome came first, the "who" is right here if you want it.
4. **Your team in this department** (secondary, below the fold) — the
   employees active here, one line each on what they do, status. Reuses the
   existing, validated trust mechanic (watch a named hire work) — it just no
   longer competes with the department for top billing.
5. **Controls — intentionally undesigned here.** With zero customers today,
   locking in exactly which controls (pause/resume an employee, escalation
   routing, anything else) are customer-clickable vs. always mediated by
   Roster is an **implementation decision, not a product requirement.**
   Customer controls in v1 stay intentionally minimal and evolve from real
   customer feedback once departments are actually live — this blueprint
   commits only to the one constraint that outranks it: whatever controls
   exist must never make the owner feel like they're managing AI agents
   (§2). A "talk to your account team" path always exists regardless of
   what else does.

---

## 7. Department Cards (Inactive) — Educational by Design

An inactive department is not a locked feature — it's a sales conversation
the customer has with themselves before they ever talk to Roster. Every
inactive card states, in the owner's own terms:

- **The problem** it solves (one line, concrete, no jargon)
- **The outcome** it creates (what changes, stated as a business result)
- **Why an owner eventually wants it** (the reason someone who didn't need
  it on day one ends up asking for it later)
- A single contextual CTA — **"Ask us about [Department]"** — which is the
  only "buy" affordance anywhere in the product (ties back to §5: this is
  content, not navigation)

Two worked examples, to make the template concrete rather than abstract:

> **Finance** *(inactive)*
> Problem: invoices go out, and collecting on them means being the bad guy —
> or not collecting at all.
> Outcome: money you've already earned actually lands in the account,
> without an awkward phone call from you.
> Why owners add it: usually after noticing how much sits unpaid past 30
> days once Customer Service and Operations are already busy booking and
> running jobs.
> → *Ask us about Finance*

> **Marketing** *(inactive)*
> Problem: happy customers would refer you and buy more, but nobody's
> consistently asking them to.
> Outcome: the phone rings more without spending on ads to make it ring.
> Why owners add it: usually once Customer Success is already rebooking
> old customers and the natural next question is "how do I get new ones the
> same way."
> → *Ask us about Marketing*

This is the same tagline voice `roster.html` already uses (§6) extended one
layer deeper — from "here's what this department is" to "here's why you,
specifically, with this much history already on the platform, would want
it." The more active departments a business has, the more specific and
data-backed this pitch can get (a natural on-ramp into §9's Briefing
recommendations).

---

## 8. Daily Owner Workflow

A typical day, mapped against the dashboard:

- **Passive default:** the owner does nothing — the workforce runs, a daily
  or weekly digest (SMS or dashboard banner, reusing the existing owner-SMS
  channel) says *"Yesterday: 6 calls answered, 2 booked, 1 estimate followed
  up, $340 collected."* This is the majority-case day.
- **Prompted attention:** an escalation (angry customer, true emergency,
  something a department couldn't confidently handle) surfaces as a
  **Notification**, same urgency and channel as today's `alert_owner` SMS,
  now also logged durably in-dashboard instead of only living in a text
  thread.
- **Owner-initiated check-in:** the owner opens the dashboard when curious
  ("how's Sales doing this week") — lands on Overview, drills into a
  department only if a number looks off or interesting.
- **Growth nudge (contextual, from The Briefing):** periodically, The
  Briefing surfaces a concrete, data-backed recommendation — *"12 estimates
  sent this month, none followed up — a Sales hire typically recovers
  20-30% of these."* This is the lightweight, always-on version of "premium
  Leadership forecasting" — the free tier notices the gap; a future paid
  tier would go further (seasonal forecasting, staffing predictions). It
  appears as a card inside The Briefing (or as an Overview highlight, §4) —
  never as a nav item, per §5.
- **Action loop:** owner marks a job done (fires Reviews), calls back an
  escalation, or taps "Ask us about [Department]" on a recommendation —
  these are the primary write-actions an owner has; everything else is
  read-only observation of work already happening.

---

## 9. How Departments Collaborate

Departments are not silos with separate memories — they are different views
onto **one shared business record**. A single job's life touches multiple
departments without the owner ever re-explaining anything:

```
Customer Service        Operations           Customer Success        Marketing
  answers call             dispatches           follows up post-job     asks for a review
  books the job     ──►    the crew      ──►    (renewal/rebooking) ──► or a referral
        │                      │                       │                    │
        └──────────────────────┴───────────────────────┴────────────────────┘
                                        │
                              ONE shared record of
                          this customer + this job,
                         read and added to by every
                            department in sequence
```

This is the same "call → book → dispatch → chase → approve → review →
rebook" loop the current live homepage (`index-v2.html`) already
illustrates as a hero animation — validating that this collaboration model
is already the right story to tell, just not yet the way the *dashboard*
itself is organized.

**What the owner sees of this:** The Briefing is where cross-department
handoffs become visible as one narrative — *"This AC-repair job: booked by
Customer Service Tuesday, completed by Operations Thursday, review
requested by Marketing Friday."* Individual department pages show their own
slice; The Briefing is the only place the *whole* thread is told as one
story. This is also the concrete reason it "gets smarter" as more
departments come online — it literally has more of the shared record to
narrate.

---

## 10. Internal Roster Operations for Provisioning Customers

Two explicitly separate products, sharing the same underlying business
records — the same split that already exists today between founder-admin
(`/clients*`) and the customer portal (`/dashboard`), generalized:

### 10a. Internal Roster Platform (the ops console)

A staged pipeline, one business per row, moved forward by the Roster team:

```
Lead          Discovery      Department       Provisioning    Testing &   Customer
Qualification → Call      → Recommendation → & Deployment  → QA        → Go-Live  → Ongoing Mgmt
```

| Stage | What happens | Who |
|---|---|---|
| **Lead qualification** | Contact-us submission reviewed — real ICP fit? | Ops/founder |
| **Discovery call** | Diagnose the actual bottleneck; notes captured against the business record (not a separate doc) | Founder/ops, on the call |
| **Department recommendation** | Which department(s) fit the diagnosed need — explicitly reasoned, logged (this is where `SALES.md`'s Customer Onboarding Principle becomes a real, trackable step instead of a paragraph of intent) | Founder/ops |
| **Provisioning & deployment** | Business knowledge captured (services, hours, pricing/FAQ, escalation contact — once, shared across every department that needs it, not re-asked per department); recommended department(s) deployed; channels/numbers connected | Ops |
| **Testing & QA** | Roster tests the deployed workforce end-to-end *before* the customer ever sees it — the "watch it work" trust moment happens on Roster's side first, then the customer's | Ops |
| **Customer go-live** | Dashboard account created, welcome/staffing-announcement sent | Ops |
| **Ongoing customer management** | Department expansion requests (arriving via the contextual prompts in §5/§7/§9, never a customer-side "store"), escalations needing human judgment, churn risk, renewal | Founder/ops, continuously |

**This is a generalization of code that already exists**, not a new
concept: `/clients/{id}/employees/deploy` already implements "founder
deploys, customer reflects" for individual employees. The ops console's job
is to make that same action operate at the department level and give it a
visible pipeline stage, rather than a bare deploy button on an
otherwise-flat business list.

### 10b. Customer Dashboard

Already fully specified in §4 above — Overview, Departments, department
detail pages, The Briefing, Notifications, Settings. Restated here only to
make the two-product split explicit: **the ops console is where departments
get decided and deployed; the dashboard is where the customer sees what's
already true.** The customer dashboard never presents a catalog of
undeployed departments to self-activate — every inactive department (§7)
educates and invites a conversation; none of them are clickable into
existence.

---

## 11. Billing and Department Expansion (architecture, not prices)

**This section is deliberately architectural only.** It defines what's
sellable, what's included, and how expansion flows — nothing here dictates
department page design, navigation, or IA; those are fixed by §§4–9 above
regardless of how billing evolves.

**What's sellable:** the department is the billable unit — not an
individual employee, not a per-minute/per-message metric the customer ever
sees. An active department is one line item.

**What's included by default, regardless of how many departments are
active:** The Briefing, the owner notification channel, and basic
support/account management. These are never separate line items — they're
the baseline of being a Roster customer at all, same as how Reviews today is
"a feature every agent gets, not a separate hire."

**Expansion flow (no self-serve checkout in v1):**
```
Owner acts on a contextual prompt
  (an Overview recommendation, an inactive department's "Ask us about X,"
  or a Briefing recommendation — never a permanent nav button, §5)
        │
Routes to the ops console as a new pipeline entry
  ("existing customer, expansion request")
        │
Founder/ops reviews — same discovery-before-deployment discipline as a
  new customer, since a wrong department recommendation wastes the
  customer's trust the same way a wrong first hire would
        │
Department deployed → appears in the customer's dashboard →
  billing adds the new line item (invoiced by Roster's ops process,
  not a self-serve credit-card flow — matches today's Stripe-payment-link
  reality, generalized)
```

**Tier architecture (recommended shape, no numbers attached):**

| Tier (working name) | What it bundles | Purpose |
|---|---|---|
| **Starter** | 1 department | The entry point — matches "Frontdesk is the wedge" from the existing sales model, generalized to "one department is the wedge." |
| **Growing Team** | A bundle of departments, priced as a bundle rather than strict à la carte | The expansion-era pricing lever — makes the 2nd/3rd department cheaper *together* than one at a time, which is the commercial argument for hiring the next department instead of stopping at one. Exact bundle size is a commercial decision, not fixed here. |
| **Full Workforce** | All 6 hireable departments | The compounding-value tier — matches the existing moat argument ("the full roster, added one role at a time, each raising switching cost") stated as a pricing tier instead of just an architecture argument. |

*Alternative considered and set aside for now:* strict à la carte
(department-by-department, no bundle discount) — simpler to explain but
gives Roster no structural incentive lever to push expansion, which is the
whole commercial point of the department model. Flagging this as a
deliberate choice, reversible once real pricing data exists.

**Where future tiers/enterprise plans fit:** an Enterprise/multi-location
tier (multiple businesses under one roll-up invoice, permissions, SLAs) sits
above Full Workforce — this is exactly `ROADMAP.md`'s existing **Phase
E — Scale**, unchanged by this blueprint. Nothing here needs building now;
naming it is enough to make sure the tier structure above doesn't
accidentally foreclose it.

---

## 12. What This Blueprint Deliberately Does Not Do

- Does not specify actual prices, or commit to exactly how many departments
  a "Growing Team" bundle contains — that's a commercial decision for after
  customer validation, per the founder's explicit scoping.
- Does not redesign the visual craft of any screen — that's `DESIGN.md`'s
  job, gated by `CLAUDE.md`, and happens after this IA is approved.
- Does not decide which customer controls (pause/resume, etc.) ship in v1 —
  deliberately left as an implementation decision to be made from real
  customer feedback, not fixed here (§6).
- Does not re-litigate anything the founder already decided in this or the
  prior review round — those are treated as fixed constraints throughout,
  not starting points for further debate.

---

## 13. Next Step

This is a draft for review, not yet locked. Once approved (as written, or
with adjustments), the next step is re-sequencing
`2026-07-28-departments-architecture-migration-review.md`'s migration plan
against this IA specifically — that document already identified which files
need to change; this one now tells it *what to change them into*.
