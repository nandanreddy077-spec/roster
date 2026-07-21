# SALES.md — how Roster gets sold

**The test for every feature: does this help close a deal?** If a proposed
build doesn't make it easier to acquire, activate, trust, or expand a customer,
it's not a sales priority (see `ROADMAP.md`).

---

## The pitch (no "AI" in it, for the owner)
> "You don't log into anything. When you can't pick up, my system answers the
> call, talks to the customer, and books the job. You just get a text: *job
> booked.* You pay a flat monthly rate after a free week."

**Category:** Roster is the **AI staffing company for home-service businesses** —
you *hire AI employees* that run your front office. Not "an AI receptionist,"
not "call-answering software." You hire staff; you don't run machinery.

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
**Frontdesk is the door; Recovery is the money.** Sell Frontdesk first (the
sharp, provable pain). Once they trust it and it's holding their front line,
the recovery employees (Quote Chaser, Rebooker, Renewals, Reviews) are the
expansion — each one more money from customers you already captured.

## Objection battlecard
| They say | You say |
|---|---|
| "Does it actually work?" | "Try it before you trust it — text your own number right now and watch it answer. You go live only after *you've* seen it work." |
| "Will it embarrass me / make something up?" | "It never invents a price or a time it doesn't know, and it hands hard calls or emergencies straight to you." |
| "Can I turn it off?" | "It's staff — pause or fire any employee anytime, and re-hiring keeps everything it already learned about your customers." |
| "Why wouldn't I just hire someone / use an answering service?" | "A front-desk hire is ~$38K/yr and calls in sick; an answering service just takes messages. Roster books the job, 24/7, for a flat rate — and never forgets a customer." |
| "Why not Avoca?" | "Avoca sells enterprise software to shops with call centers — sales calls, demos, procurement, integrations. You'd hire an AI employee in 15 minutes with no sales rep. Different product for a different shop." |
| "What about my Jobber / Housecall Pro?" | "You keep it. Right now the booked job texts straight to you; we connect to your tool when enough of you ask for the same one." |
| "What's it cost?" | "Free for 7 days, then one flat monthly rate — no setup fee, no per-call surprise." |

## What we can honestly claim TODAY (the honesty anchor)
- ✅ **Request-access + founder-led onboarding** — real and live at rosterhires.com. Self-serve `/signup` still works but is not advertised (see `DESIGN.md` 2026-07-21).
- ⚠️ **SMS text-back + booking** — built; claim it as working **only after** a real end-to-end text has been verified (`ANTHROPIC_API_KEY` + a live number).
- ⛔ **Live voice** — the primary wedge, but **unverified end-to-end** (see
  `CUSTOMER.md` Open Risk #1). Do **not** promise "answers your calls live"
  until a real call has been proven. Until then, sell the concierge setup, not a
  finished product.

## Customer Onboarding Principle (diagnosis before deployment)
While founder-led onboarding is the default, Roster does not assume every
customer starts with the same AI employee. Every customer begins with a
business discovery session. We identify the customer's biggest operational
bottleneck, estimate where the highest ROI exists, and recommend the AI
employee most likely to solve that problem. We deploy that employee, measure
results, and use those results to guide future AI hires. Today, Frontdesk is
the only `live` employee (`agent/employees.py`), so it is usually the answer
— but the onboarding experience is built around diagnosis and ROI first,
deployment second, not product selection first.

## The motion (concierge until proven otherwise)
Request access, then **you personally join a 15-minute discovery call** per
pilot: understand the business's biggest bottleneck (see the Customer
Onboarding Principle above), configure forwarding, verify the AI, make the
first real call succeed. Those calls are also **customer research** — every
friction goes in `CUSTOMER.md`. Founder-led onboarding stays the default
until a repeatable process is validated by customer-success metrics, not by
hitting a fixed customer count. Do not optimize for fully self-serve before
you understand why customers struggle.
