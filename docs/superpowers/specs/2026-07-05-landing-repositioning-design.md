# Landing Page Repositioning — "The Employment Offer"

**Date:** 2026-07-05
**Surfaces:** `index.html` + `styles.css` (root — landing page only; dashboard untouched)

## Positioning

Roster is the **staffing company for AI employees**. Every competitor sells the
owner a tool he has to run — a $49 answering machine (Rosie), an app with an AI
checkbox (Jobber/Housecall Pro), or a $50K/yr enterprise suite (ServiceTitan,
Avoca). Roster sells *staff*: named employees with job descriptions, hired one
at a time, managed by us, paid on commission — they only earn when a job books.
The starting roster is five; the promise is any role the shop will ever need.

Tagline logic: *ServiceTitan sells the tool. Rosie takes messages. Roster shows
up to work.*

## Decisions (user-approved)

1. **Hero framing:** hiring frame — "Your next office hire doesn't need a desk."
2. **Custom-agent promise:** big mid-page section after the roster ("the open req"),
   plus a dashed "open position" teaser card at the end of the roster grid.
3. **Design latitude:** elevate execution, keep DESIGN.md identity (warm paper,
   Fraunces, rust accent). No palette/type changes.
4. **Proof strategy:** industry math (62% SMB missed calls, $1,200–$3,500 per
   missed HVAC call, 62% of callers go to a competitor next, 8–12 touches to
   close a quote) + founding-client offer (5 founding shops, pilot pricing,
   founder-run, cancel anytime). **No live-demo number** (live LLM path not yet
   verified end-to-end).

## Page architecture (one scroll, mobile-first)

1. **Nav** — logo + phone CTA, sticky.
2. **Hero** — hiring claim, sub, CTA pair, micro-trust line.
3. **The staffing gap** — industry math rendered as a ledger sheet ("what an
   empty desk costs"). Ends: "You don't have a marketing problem. You have
   three empty desks."
4. **Meet the roster** — 5 employee-badge cards (Frontdesk Nº001, Chaser Nº002,
   Rebooker Nº003, Renewals Nº004, Reviews Nº005), each with title, one-line
   job description, duties. Phone-mock visuals for Frontdesk and Chaser only.
   Sixth dashed card: "Open position — the role you need."
5. **How it works** — 3 deliberately boring concierge steps.
6. **The open req** — full-width custom-promise statement section.
7. **Pricing** — "The only office staff that works on commission." Pay per
   booked job; side-by-side vs. a $38K/yr receptionist working 40 of 168 hours.
8. **Founding roster** — 5 numbered slots, all OPEN; pilot pricing; founder runs
   the account; cancel any day.
9. **FAQ + final CTA** — three blunt objections (customers hate AI → humans get
   emergencies; do I change anything → keep number/software; what if it screws
   up → founder reads every conversation), then the phone number, big.

Every section's CTA is the same phone number.

## Visual direction (within DESIGN.md)

- Existing tokens unchanged; Fraunces display, system sans body; landing scale
  (H1 44/1.15, H2 30, body 17).
- Motifs: ledger sheet with dotted leaders + tabular numbers + red total row
  (`--urgency` used exactly once); employee cards styled as roster badges with
  monogram, employee-number stamp, small-caps title; perforated
  receipt/ticket-style section dividers; founding-roster slots as ruled sign-up
  lines.
- Motion: hero fade-in once; 150–200ms hover/focus transitions; nothing
  scroll-driven.
- Mobile-first single column; ledger and comparison collapse to stacked rows.

## Copy voice

Blunt, trade-paperwork, first-person-owner empathy ("you're on a roof, not at a
desk"). No SaaS vocabulary (platform, seamless, supercharge). Numbers hedged as
industry averages, never claims about the reader's shop.

## Out of scope

Dashboard styles, `concepts/` variants, any backend change.
