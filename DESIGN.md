# Design System — Roster

## Product Context
- **What this is:** A managed AI workforce for home-service businesses — currently two
  agents (Frontdesk: missed-call text-back, Revenue Recovery: quote follow-up +
  dormant-customer reactivation). Concierge B2B model, sold via cold call/text.
- **Who it's for:** Two very different audiences on two very different surfaces:
  - Landing page → skeptical HVAC/plumbing business owners, usually on a phone,
    clicking a link from a cold call or text they just got.
  - Dashboard → the founder/ops team managing clients and campaigns internally.
- **Space/industry:** Home-service trades software. Category peers (ServiceTitan,
  Housecall Pro, Jobber, Rosie/heyrosie.com) converge on generic blue/teal SaaS
  palettes, rounded illustration, gradient heroes — visually interchangeable.
- **Project type:** Marketing site (landing page) + internal tool (dashboard).

## The memorable thing
All three, layered in order: **loss aversion first** (a missed call is money gone
right now), **trust second** (this actually works, stated plainly), **identity
third**, carried by voice rather than a section — the site should read like it was
written by someone who's been in a truck, not a Silicon Valley product team.

## Aesthetic Direction
- **Direction:** Organic/editorial. Warm, papery, ledger-like — not tech-blue.
- **Decoration level:** Intentional. Typography and whitespace do most of the work;
  a light textural motif (rule lines, ticket/receipt-style dividers) reinforces
  "trade paperwork" without becoming twee or decorative-for-its-own-sake.
- **Mood:** Should feel like a well-run local business's letterhead, not a startup
  pitch deck. Confident, direct, a little blunt — never corporate-friendly.
- **Reference sites:** heyrosie.com, servicetitan.com, housecallpro.com, getjobber.com
  — all converge on blue/teal SaaS palettes + rounded stock-illustration hero. The
  deliberate departure here (warm neutral + single strong accent, serif display) is
  the risk that makes Roster look like it belongs to this industry instead of to
  Silicon Valley.

## Typography
- **Display/Hero:** Fraunces (serif) — more character and warmth than a system
  Georgia stack while still reading "established, printed, trustworthy." Loaded via
  Google Fonts, one weight family (variable), used sparingly (H1/H2 only).
- **Body:** System sans stack (`-apple-system, "Segoe UI", Roboto, sans-serif`) on
  both surfaces — fast, neutral, no webfont cost for body copy. Landing page and
  dashboard body text stay visually related but the landing page runs slightly
  larger (17px vs 15px) since it's read, not scanned.
- **UI/Labels (dashboard):** same system sans stack, smaller sizes, no serif.
- **Scale (landing):** H1 44px/1.15, H2 30px/1.2, H3 20px/1.3, body 17px/1.6, small 14px.
- **Scale (dashboard):** H1 28px, H2 18px, H3 16px, body 14px, small 12–13px (existing).
- **Loading:** `<link>` to Google Fonts (Fraunces) on the landing page only — the
  dashboard keeps its existing serif fallback stack (internal tool, no webfont cost
  justified).

## Color
- **Approach:** Restrained — one warm neutral system, one strong accent, plus two
  new semantic colors added specifically for the landing page's psychology (urgency
  and proof), kept distinct from the decorative accent so they read as signals, not
  branding.
- **Base palette (shared, extends the existing dashboard tokens):**
  - `--bg: #F7F4EC` (warm cream, existing)
  - `--bg-alt: #F0EBDF` (existing)
  - `--card: #FFFFFF` (existing)
  - `--border: #E4DDCB` (existing)
  - `--text: #1F1B16` (existing, near-black warm)
  - `--text-dim: #6B6354` (existing)
  - `--accent: #C2693D` (rust, existing — primary CTA / brand color)
  - `--accent-dim: #F1DDD0` (existing)
- **New, landing-page-only semantic additions:**
  - `--urgency: #A8331F` (deeper, more serious red-rust than `--accent` — used
    once, for the cost-of-missed-calls number. Never used decoratively.)
  - `--proof: #3F6B4A` (muted trust-green — used only for the results/testimonial
    slot, so real numbers read as credible, not salesy.)
- **Dashboard-only (existing, unchanged):** `--good`, `--soon`, `--bad` status colors.
- **Dark mode:** not implemented on either surface — this audience does not expect
  it and the dashboard is an internal tool used in daylight; revisit if requested.

## Spacing
- **Base unit:** 8px (both surfaces, matches existing dashboard rhythm).
- **Density:** Landing page = spacious (generous section padding, single column,
  thumb-friendly on mobile). Dashboard = comfortable (existing density, unchanged).
- **Scale:** xs(4) sm(8) md(16) lg(24) xl(32) 2xl(48) 3xl(64) 4xl(96 — landing
  section padding only).

## Layout
- **Approach:** Hybrid. Landing page = creative-editorial (asymmetric hero, pull
  quotes, one intentional grid break at the two-agent section). Dashboard =
  grid-disciplined (existing card-grid convention, unchanged).
- **Landing max content width:** 640px for text-heavy sections (readability), 1040px
  for the two-agent comparison grid (matches dashboard's existing max-width so the
  brand doesn't visually fork).
- **Border radius:** sm 6px (badges/tags), md 10px (buttons/inputs), lg 12px
  (cards) — matches existing dashboard scale exactly, no divergence.

## Motion
- **Approach:** Minimal-functional on both surfaces. This audience does not reward
  flashy animation — a busy trades owner skimming on a phone wants speed, not
  choreography. Hover/focus states only; one soft fade-in on the landing hero,
  nothing scroll-driven or looping.
- **Duration:** short (150–200ms) for hover/focus transitions only.

## Safe choices (category baseline)
- Single, frictionless CTA (call/text) repeated at every scroll point on the
  landing page — every high-converting local-service page does this, don't fight it.
- Mobile-first, single-column layout, large tap targets — this audience is on a
  phone, not a desktop.
- Dashboard keeps its existing card-grid IA — the ops team has already learned it.

## Risks taken (where Roster gets its own face)
1. **Warm paper palette instead of SaaS blue/teal.** Every direct competitor looks
   the same shade of tech-blue. This is a one-line differentiator: the page looks
   like it belongs to the trade, not to Silicon Valley. Cost: reads less
   "VC-fundable" to an outside investor audience — irrelevant here, the actual
   reader is a 50-year-old HVAC owner.
2. **Loss aversion leads, ahead of any product explanation.** Most competitor sites
   open with a value-prop headline about the product. This page opens on the
   customer's own money already walking out the door. Cost: slightly less
   "polished software company," more "direct and a little blunt" — intentional,
   matches the identity signal from the memorable-thing brief.
3. **Real dollar math shown, not just implied.** A visible small calculation
   (missed calls/week × avg ticket) makes the loss concrete instead of a vague claim.
   Cost: requires the copy to commit to specific-feeling numbers even before a real
   client case study exists — mitigated by framing it as an industry-average
   estimate, not a claim about this specific reader's business.

## Decisions Log
| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-07-02 | Initial design system created | `/design-consultation`, based on existing dashboard tokens + competitor research (Rosie, ServiceTitan, Housecall Pro, Jobber) + user's memorable-thing brief ("all three": loss aversion, trust, identity) |
| 2026-07-05 | Landing repositioned to "Employment Offer" (hiring frame) | User-approved brainstorm (spec: `docs/superpowers/specs/2026-07-05-landing-repositioning-design.md`): hero sells hiring AI employees; new motifs within existing tokens — ledger sheet with dotted leaders for industry math, employee-number badge cards for the roster, dark "open req" statement section, commission-deal pricing comparison, founding-roster sign-up slots, perforated tear dividers. Palette/type unchanged. |
| 2026-07-05 | Explicit trade targeting restored | Research (Avoca per-vertical pages; niche-language conversion data): hero eyebrow "AI employees for the trades," sub names HVAC/plumbing, "Built for the trades" grid restored (HVAC/Plumbing "Staffing now" with trade-specific pain copy, others "Ready when you are" — never "coming soon"), founding offer names the trades, title/OG tags name HVAC & plumbing for text-message link previews. |
| 2026-07-08 | Self-serve hire flow added (`/hire` in FastAPI app; `agent/templates/hire.html`, `agent/static/hire.css`) | Onboarding-psychology research (endowed-progress/Zeigarnik, IKEA-effect vs activation-friction tension, trades-owner control fear, loss aversion). 7-step wizard framed as briefing a new hire, not configuring software: micro-commitment first, then business facts, then 5 judgment-call screens (answer mode → pricing → turn-away → the control/handoff switch → reach-you). Progress bar (endowed progress), choice cards for judgments (ownership without data-entry friction), "training week" = the 7-day free trial (NOT the stale commission line still on the landing — memory 2026-07-07), loss-framed confirmation. Reuses landing tokens (Fraunces, warm paper, badge/tear motifs) so wizard reads as one surface with the landing. Creates a real Client (same model as concierge `/clients/new`); 5 judgments compose into `pricing_faq`. Landing hero CTA + live trade cards now link into `/hire`. Deferred (flagged next piece): auto-research pre-fill (scrape site/GBP) — the "it already knows my business" aha; built honestly without faking it. |
| 2026-07-08 | Roster picker added as hire-flow step 1 (now 8 steps) | Choice-architecture research (jam-study choice overload vs the default effect: a clear pre-recommended option lifts conversion ~18–20% and removes menu paralysis). Founder wanted a "pick your AI employee from a list" entry (staffing-agency / LinkedIn-for-AI-employees vision). Resolution that satisfies both: the whole roster is *visible* but only ONE decision is asked — Frontdesk is the dominant "Start here · most shops hire this first" default card (rust border, elevated), the other four agents are optional "Add to your roster" checkboxes, and a dashed "a role you don't see here" custom escape hatch reveals a text field. Add-on/custom picks don't configure here (Frontdesk is the hire the flow sets up) — they persist to new `Client.requested_roster` (JSON) and surface on the confirmation as "Also on your roster — the founder will set these up," matching the concierge reality (Chaser/Rebooker need customer history). Avoided the failure mode: a flat menu of equal agents with no default. Landing hero CTA changed to "Hire your AI employee". |
