# SALES.md — how Roster gets sold

**The test for every feature: does this help close a deal?** If a proposed
build doesn't make it easier to acquire, activate, trust, or expand a customer,
it's not a sales priority (see `ROADMAP.md`).

> **Updated 2026-07-28 for the department-first pivot.** Customers hire
> **departments**, not individual AI employees, and there is exactly one
> onboarding path: Contact Us → Roster-led discovery and provisioning →
> dashboard access. Product source of truth:
> [`docs/superpowers/specs/2026-07-28-departments-product-blueprint-design.md`](docs/superpowers/specs/2026-07-28-departments-product-blueprint-design.md).
> Employee names (Frontdesk, Quote Chaser, Retention Manager) are still
> real and still used internally — on a sales call they're the *proof* of
> how a department does its work, never the thing being sold.

---

## The pitch (no "AI" in it, for the owner)
> "You don't log into anything. When you can't pick up, my system answers the
> call, talks to the customer, and books the job. You just get a text: *job
> booked.* You pay a flat monthly rate after a free week."

**Category:** Roster is the **AI workforce company for home-service
businesses** — you *hire departments* (Customer Service, Sales, Operations,
Finance, Customer Success, Marketing) that run your office. Not "an AI
receptionist," not "call-answering software," and not a menu of individual AI
agents to assemble yourself. You hire a department the way you'd hire a team;
the AI employees inside it are our problem to staff and run, not yours.

## Who to sell to first (ICP)
1–10 truck **plumbing, HVAC, electrical, roofing, garage-door** shops — where a
missed call is a lost emergency job worth hundreds of dollars, *today*. Owner is
in the field, no office staff, skeptical, on a phone.

**Sell to these last (softer wedge):** cleaning, landscaping, pest — recurring/
scheduled work, so "never miss the emergency call" lands weaker. Serviceable,
not the beachhead.

## The ROI framing (make it a money conversation, not a software one)
Never lead with "$X/month for AI." Lead with their leak:
> "One plumbing emergency ≈ $400. Miss three calls a month = ~$1,200 gone.
> Roster's a fraction of that, and it catches them."
Missed-call → booked-job math turns price into ROI.

## The wedge sequence
**Customer Service is the door; the revenue departments are the money.** Sell
the department that solves the bottleneck the discovery call actually found —
usually Customer Service, because a missed call is the sharpest and most
provable pain. Once they trust it and it's holding their front line, **Sales**
(quote follow-up) and **Customer Success** (renewals, rebooking, reviews) are
the expansion — each one more money from customers the first department
already captured, and each one inheriting everything the business already
knows about them.

## Objection battlecard
| They say | You say |
|---|---|
| "Does it actually work?" | "Try it before you trust it — text your own number right now and watch it answer. You go live only after *you've* seen it work." |
| "Will it embarrass me / make something up?" | "It never invents a price or a time it doesn't know, and it hands hard calls or emergencies straight to you." |
| "Can I turn it off?" | "It's staff — you can stand a department down anytime, and bringing it back keeps everything it already learned about your customers." |
| "Why wouldn't I just hire someone / use an answering service?" | "A front-desk hire is ~$38K/yr and calls in sick; an answering service just takes messages. Roster books the job, 24/7, for a flat rate — and never forgets a customer." |
| "Why not Avoca?" | "Avoca sells enterprise software to shops with call centers — sales calls, demos, procurement, integrations. You'd hire an AI employee in 15 minutes with no sales rep. Different product for a different shop." |
| "What about my Jobber / Housecall Pro?" | "You keep it. Right now the booked job texts straight to you; we connect to your tool when enough of you ask for the same one." |
| "What's it cost?" | "Free for 7 days, then one flat monthly rate — no setup fee, no per-call surprise." |

## What we can honestly claim TODAY (the honesty anchor)
- ✅ **Contact Us + Roster-led onboarding** — real and live at rosterhires.com. As of the 2026-07-28 pivot this is the **only** onboarding path: self-serve signup is being retired outright, with no founder-only exception (departments execution plan, Phases 6–7).
- ⚠️ **SMS text-back + booking** — built; claim it as working **only after** a real end-to-end text has been verified (`ANTHROPIC_API_KEY` + a live number).
- ⛔ **Live voice** — the primary wedge, but **unverified end-to-end** (see
  `CUSTOMER.md` Open Risk #1). Do **not** promise "answers your calls live"
  until a real call has been proven. Until then, sell the concierge setup, not a
  finished product.

## Customer Onboarding Principle (diagnosis before deployment)
Roster does not assume every customer starts with the same department. Every
customer begins with a business discovery session. We identify the customer's
biggest operational bottleneck, estimate where the highest ROI exists, and
recommend the **department** most likely to solve that problem. We deploy that
department, measure results, and use those results to guide future hires.
Customer Service is usually the answer today — it's the department whose
employees are furthest along (`agent/employees.py`) and whose pain is
sharpest — but the onboarding experience is built around diagnosis and ROI
first, deployment second, never product selection first. **The customer never
picks from a catalog; we recommend, and we deploy.**

## The motion (Roster-led, permanently — not a stopgap)
A business contacts us, then **you personally join a 15-minute discovery
call**: understand their biggest bottleneck (see the Customer Onboarding
Principle above), configure forwarding, verify the workforce, make the first
real call succeed. Those calls are also **customer research** — every friction
goes in `CUSTOMER.md`.

Roster-led onboarding is **the product**, not a phase we're waiting to
graduate out of (2026-07-28 pivot — this replaces the earlier "default until
customer-success metrics prove a repeatable self-serve motion" framing). What
gets more efficient over time is Roster's internal ops tooling, never the
customer doing more of the work themselves. There is one onboarding flow and
no internal exception to it.
