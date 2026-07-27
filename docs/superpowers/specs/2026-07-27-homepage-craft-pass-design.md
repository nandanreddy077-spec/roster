# Homepage Craft Pass — Design

## Context
Founder asked for a "complete rethink" of rosterhires.com in the style of
Linear/Stripe/Vercel/Avoca — bold typography, interactive hero, ROI
calculator, employee "cards," integrations, security, customer stories, built
as Next.js/React/Tailwind.

Investigation before designing surfaced three conflicts with that brief, each
resolved with the founder before this doc was written:

1. **Stack** — the live site is a FastAPI app serving static HTML/CSS
   (`agent/landing/index.html`, `styles.css`), no Node/Next.js pipeline
   exists anywhere in the repo. → **Resolved: stay static HTML, no new infra.**
2. **Timing** — [[customer-sprint-30-days]] bans feature work through
   2026-08-15, and the 2026-07-21 acquisition-system decision deliberately
   keeps this exact page a waitlist while outbound converts. → **Resolved:
   build behind an unlinked route, hold the launch swap.**
3. **Aesthetic** — DESIGN.md (rebuilt 2026-07-22 after three founder review
   rounds) locks a warm/blunt/editorial "trade paperwork, not Silicon
   Valley" identity, and its decision log records a similar polished
   "office-first" rebuild being rejected for making visitors understand the
   company before feeling their own pain. → **Resolved: keep the identity
   and the validated narrative arc, raise execution craft (type, grid,
   motion) to match the requested bar — this is not a rebuild.**

Current homepage (`agent/landing/index.html`, 410 lines) already carries the
full validated narrative arc: hero → agitation → pain ledger → 5 flagship
employee cards → journey timeline → how-it-works → morning report → typical-
vs-Roster comparison → objections FAQ → pricing anchor → founding scarcity →
trades strip → CTA. This doc scopes a craft/motion pass on top of that, not
a new information architecture.

## Goals
- Raise typographic and spacing confidence to a Linear/Stripe/Vercel bar
  without changing the palette, voice, or section order.
- Add two *honest* interactive elements the original brief asked for: a
  hero mechanism animation and a personalized loss calculator.
- Make the 7-department org structure legible from the homepage without
  overwhelming it.
- Ship it somewhere reviewable before touching the live page.

## Non-goals (and why)
- **No Next.js/React/Tailwind.** No build pipeline exists; introducing one
  is an infrastructure change no one asked for independent of this request.
- **No security/SOC2/audit-log section.** Pre-revenue solo-founder company;
  claiming a security posture that doesn't exist is a false statement, not
  a design choice.
- **No integrations list** (ServiceTitan/Housecall Pro/Twilio/Stripe).
  None of these are real integrations today — same honesty problem.
- **No customer quotes, logos, or "trusted by" claims.** No customers yet;
  DESIGN.md already established founder-run authenticity over fabricated
  proof for this exact reason (the "illustrative" label on the sample
  morning report is the existing precedent).
- **No stock photography of people.** Both the founder's original brief
  ("no stock photos, no generated people") and DESIGN.md's authenticity
  stance rule this out, even though the Avoca reference uses it. Trades
  get simple icon/line-art treatment instead, not photos.

## Rollout
New files, existing route pattern (`agent/app.py` already does per-file
`FileResponse` routes for the landing, e.g. `/styles.css` → `landing/styles.css`):
- `agent/landing/index-v2.html`, `agent/landing/styles-v2.css` — start as
  copies of the current files, edited in place.
- New `GET /preview` route → `index-v2.html`; new `GET /styles-v2.css`
  route → `styles-v2.css`. Unlinked from nav, `robots.txt`-excluded is not
  needed (no such file exists today; not adding one for a two-person-visits
  preview page).
- Live `/` keeps serving today's `index.html` untouched. Shipping later is
  swapping which file the `/` route points to — a one-line change, not part
  of this pass.

## Typography & grid
- Hero H1: 46px → ~64px desktop (clamp for mobile), tighter letter-spacing.
- Section H2s: 32px → 36–40px.
- Collapse today's eleven ad-hoc `max-width` values (480–780px, one-off per
  section) to three: narrow-text (680px, prose/FAQ), standard (860px,
  cards/comparisons), wide-grid (1040px, existing nav/section outer width —
  unchanged).
- Section padding moves onto DESIGN.md's already-documented 8px scale
  exactly (96 / 64 / 48) instead of today's close-but-inconsistent 88/72/64/56.
- No new colors, no new font. Fraunces + system sans stack, existing tokens.

## Motion — scoped amendment to DESIGN.md's "no looping" line
DESIGN.md currently states motion should never be "scroll-driven or
looping." This pass adds three motion elements, each **plays once, ends on
a resting state, never loops**, and respects `prefers-reduced-motion`
(already wired via the existing media query) — a narrow exception, not a
reversal:

1. **Hero mechanism.** Missed call → text sent → job booked → CRM updated,
   built from a few positioned message-bubble elements + CSS/JS timing.
   Plays once when the hero scrolls into view (or on load, since it's above
   the fold). ~6–8s, then rests on "Job booked."
2. **Loss calculator.** The existing static industry-average ledger's top
   two rows (`Calls to small shops that go unanswered` / ticket-adjacent
   row) become number inputs — missed calls/week, average ticket — with the
   bottom total recalculating live via vanilla JS. More honest than an
   industry average since it's the visitor's own numbers; no library.
3. **Scroll-reveal.** Every other section gets the same one-time fade/rise
   the hero already has (`@keyframes rise`), triggered via
   `IntersectionObserver` the first time each section enters the viewport.
   Not repeated on scroll-back.

## New section: department strip
Placed immediately after the existing 5-card "Meet the office" section,
before the journey timeline. Reuses the existing `.trade-strip` component
(built for the trades list) rather than inventing new UI — same plain-text
row, same dot convention (small filled dot = departments with at least one
"Working" employee today; muted = still hiring).

- Eyebrow: "Five employees. Seven departments."
- Row: Customer Service · Sales · Operations · Finance · Customer Success ·
  Marketing · Leadership (names only, no per-employee detail — that's what
  `/roster` is for).
- Existing "See the Full Roster →" button, already present one section
  down, is what carries the click-through — no new CTA needed.

Rationale: showing all 7 departments with their full employee lists (the
current `/roster` accordion) on the homepage would be the same choice-
overload failure mode the hire-flow redesign already fixed once (flat menu,
no default, DESIGN.md 2026-07-08 entry). Department *names* only, in a
single scannable line, apply chunking (Miller's law) and progressive
disclosure — curiosity pulls the interested visitor into `/roster` instead
of forcing everyone through it.

## Trades section
Keep the existing 10-trade strip and "Live in HVAC/plumbing, rest incoming"
copy unchanged. Add a small icon beside each trade name — hand-rolled inline
SVG line-art (single-color, matches `--text`/`--soon` per trade's live/soon
state), not a photo. No icon-library dependency added for ten glyphs.

## Files touched
- New: `agent/landing/index-v2.html`, `agent/landing/styles-v2.css`
- Edited: `agent/app.py` (two new routes, `/preview` and `/styles-v2.css`)
- Unchanged: `agent/landing/index.html`, `agent/landing/styles.css`,
  `agent/landing/roster.html`, `DESIGN.md` (the motion-exception above is
  recorded in DESIGN.md's Decisions Log as part of implementation, not
  this spec)
