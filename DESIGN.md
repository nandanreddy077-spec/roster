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
| 2026-07-10 | Landing pricing copy de-commissioned; canonical domain set | Resolves the long-standing stale "commission / $0 salary, pay per booked job / you owe nothing" pricing copy (flagged since the 2026-07-07 pricing switch to free-trial → flat monthly). Kept the two-card human-hire comparison motif ($38K/yr front-desk hire vs Roster) but reframed the Roster side: hero sub, pricing `<h2>` ("The only office hire you try before you pay."), the offer-card price ("Free for 7 days — then one flat monthly rate"), bullets, and the pricing-note now lead with the free-week risk-reversal instead of commission. No specific public monthly price stated (not locked). Meta description/OG updated to match; added `canonical` + `og:url` = `https://rosterhires.com/`. Palette/type/layout unchanged — copy only. |
| 2026-07-11 | Onboarding agent-context fields + honest "earned Live" dashboard | Two linked fixes. (1) Self-serve onboarding now captures what each agent actually needs: a required pricing/FAQ field on `/onboarding/business` (previously `pricing_faq` was never asked and then *overwritten* with a stale escalation note, so self-serve receptionists had zero pricing knowledge); `tone` (existing default "professional and friendly") wired into both SMS + voice prompts; Retention Manager hire became an optional mini-step capturing review link + referral incentive, also editable anytime from a new dashboard "Reviews & Referrals" card. (2) Dashboard integrity redesign: the receptionist no longer shows "Live" off a form submit. New honest status ladder — **"Ready — try it before you trust it"** (rust dot) until the owner sends a test message and gets a real reply, which sets `Client.tested_at` and flips it to **"Working — you've seen it answer"** (proof-green `--proof`). Modeled on voice-agent-company dashboards: the centerpiece is an in-dashboard test chat (reuses the real engine via `handle_customer_message` on a `portal-test` thread — works with zero Twilio), plus honest "Your number" (never a fake number; "Connecting your line" when none) and an Activity log (real jobs, test-booked jobs tagged). Activation page stops shouting "is live 🎉" → "You've hired your {role} — test it before you trust it." Rationale: matches the DESIGN.md "direct, a little blunt, never corporate-friendly" identity — "Live" must mean the owner *watched it work*, and bad onboarding input becomes visible in the test instead of hidden behind a green check. Reuses existing tokens/`portal.css`; no new palette. |
| 2026-07-16 | Trades section: heavy 9-card grid → clean Avoca-style name strip | Founder review ("don't show this way, take inspiration from Avoca"). Looked at avoca.ai directly: they present trades as an understated horizontal row of industry NAMES (no cards/badges/per-trade copy). Adopted that restraint in Roster's warm-paper system: `.trade-strip` = a centered flex-wrap `<ul>` of the 9 trade names, sans 21px/500, two quiet tiers — live trades (HVAC/Plumbing) in `--text` with a small `--proof` green dot, the rest in muted `--soon`; one honest note line below ("Live today in HVAC and plumbing… request early access for your trade"). Removed all `.trade-card/.trade-badge/.trade-soon/.trade-live` CSS + the orphaned `.trades-grid` mobile rules. STATIC, not Avoca's auto-scroll marquee — DESIGN.md motion rule forbids looping/scroll-driven animation; can revisit if founder wants to override. Typography + whitespace do the work; far lighter than the card grid. |
| 2026-07-16 | Landing converted to a "Request early access" lead-capture funnel; phone number removed | Twilio KYC pending → can't provision per-customer numbers → self-serve can't complete yet. So the landing's ONLY conversion path is now a request form (Name · Business · Phone · Trade → new `AccessRequest` table → founder follows up from `/clients`, which now shows an "Access requests" section). Every CTA (nav, hero, cost-ledger, founding, all 9 trade cards, final) repointed to `#request`; ALL `tel:`/`sms:`/visible phone numbers removed; trade-card badges "Join the waitlist" → "Coming soon"; self-serve `/signup` unlinked from the landing (route kept working for post-KYC). Framing = selective/premium ("we onboard a handful at a time, we'll reach out within a day") to match founding-roster scarcity — founder-chosen (option A). New `/thanks` page + `.request-form` input styles reuse existing tokens. Lets the founder market NOW and hand-onboard the first few shops. REVERT to self-serve "Hire your AI employee" CTAs once Twilio provisions numbers. |
| 2026-07-16 | Trades section reframed home-services-wide (reverses the 2026-07-05 "never coming soon" call) | Founder-approved: the "Proving it in HVAC and plumbing first" framing led with narrowness, and the four "Ready when you are" cards were dead (no copy, no action) — a roofer/electrician landing here felt excluded. Reframed to feel built for the *whole* home-services industry while staying honest: eyebrow "Built for home services", H2 "Your trade is on the roster.", lede says HVAC + plumbing are hired and working today and the rest is next. Grid expanded 6→9 trades (added Garage door, Cleaning, Pool service). HVAC/Plumbing keep "Staffing now" (green, → `/signup`); the other 7 became clickable `.trade-soon` "Join the waitlist" cards (warm `--bg-alt` bg + rust `--accent`/`--accent-dim` badge) linking to `sms:+19014038929` with a per-trade prefilled "add my {trade} shop to the waitlist" — a bounce becomes a lead, zero backend, matches the concierge/founder-runs-everything reality. Still firmly home-services only (NOT "every business" — see [[product-validation-mission]] / positioning lock). Reuses existing card components + tokens; no rebuild. Title/OG tags still name HVAC & plumbing (deliberate — cold-call text-preview targeting, unchanged). |
| 2026-07-11 | Google login added to signup/login (optional, additive) | "Continue with Google" button + "or" divider (`.btn-google`, `.or-divider` in `portal.css`, official multi-color Google "G" `static/google.svg`) above the existing email/password form on both `signup.html` and `login.html` — kept both methods rather than replacing password auth (working code, some owners will still prefer email). One account per email regardless of method: a Google sign-in on an email that already has a password account logs into the *same* `Client`, never a duplicate. New accounts created via Google are passwordless (`password_hash` stays `None` — already `Optional` on `Client`, no migration needed) and land in `/onboarding/business`, same as email signup — Google just replaces the credential step, not the flow. Button only renders when `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` are set (`google_auth.google_enabled()`), so the pages are byte-identical to before until those env vars exist — nothing to break pre-credentials. OAuth via Authlib, reusing the existing `SessionMiddleware` for state/nonce. |
| 2026-07-21 | Request-access onboarding made the standing default; dashboard "Hire" reframed as a reviewed conversation | See `docs/superpowers/specs/2026-07-21-ai-staffing-repositioning-design.md`. Supersedes the 2026-07-16 entry's "REVERT to self-serve... once Twilio provisions numbers" plan — request-led onboarding + a founder discovery call stays the default until customer-success metrics (onboarding time, activation rate, first-week retention) show a repeatable self-serve motion works, not because KYC clears or a fixed customer count is hit. `login.html`'s residual self-serve "Get started" link removed (landing itself had no self-serve CTA since the same 2026-07-16 pivot). Dashboard's existing data-driven Quote Chaser recommendation reframed from a self-serve "Hire" button to "Discuss Your Next AI Hire" — same backend (`requested_roster` queue for founder follow-up), copy only, so the recommendation reads as a reviewed business insight, not a feature purchase. |
| 2026-07-27 | Trades section: dropped the "HVAC/plumbing live, rest coming soon" split | Reverses part of the 2026-07-16 "Trades section reframed home-services-wide" entry. Founder confirmed on redesign review: the product has no per-trade limits — same agent mechanics (answer calls, book jobs, chase quotes) regardless of trade; HVAC/plumbing were just the only paying customers as of Jul 16, a customer-count fact, not a capability limit. All 10 trades now render identically (no dimmed/grey treatment); footnote changed from "Working today in HVAC and plumbing" to "Built for any home service business with a phone line and a crew." Applies to `index-v2.html`/`styles-v2.css` (the live `/`); `index.html`/`roster.html` (the pre-v2 files, no longer routed) still carry the old framing and were not touched. |
| 2026-07-27 | Homepage v2 built behind `/preview` (NOT live) — comprehension-first craft pass + narrow motion exception | Founder brief: after 5 seconds an owner must know what Roster is / does / why care / why different; the complaint was comprehension, not aesthetics ("visitors leave understanding pieces instead of the whole product"). Spec: `docs/superpowers/specs/2026-07-27-homepage-craft-pass-design.md`. Built as `agent/landing/index-v2.html` + `styles-v2.css` + `/preview` route; live `/` untouched (shipping = repointing `root()`), so the 2026-07-21 waitlist decision and the SPRINT feature freeze both still hold. **Kept:** palette, Fraunces, warm/blunt voice, and the validated pain-first arc. **Changed:** every section now answers exactly one customer question; hero states the category in one line ("Hire the office. Not the software." + "AI employees for home service businesses"); employee cards lead with the OUTCOME as the heading and the role name demoted to a label; new "You don't buy Roster. You staff it." section carries the AI-workforce concept explicitly. **Craft:** H1 46→clamp(38,6.4vw,68), eleven ad-hoc max-widths collapsed to three tokens (680/880/1120), spacing onto the documented 8px scale, real 12-col hero grid. **Motion exception to the "no scroll-driven/looping" rule:** three additions, each plays ONCE and rests, all `prefers-reduced-motion` aware — (1) hero live-office feed teaching the whole loop (call→book→dispatch→chase→approve→review→rebook), counters derived from per-row `data-job`/`data-value` so totals can't drift from the events shown; (2) interactive loss calculator on the visitor's own two numbers, clamped input, conservative 1-in-3 framing as the headline number; (3) one-time IntersectionObserver reveals with a 3s force-visible safety net (content is `opacity:0` by default, so a non-firing observer would otherwise hide the page). **Honesty held:** no SOC2/security section, no integrations list, no logos/testimonials/customer quotes, no stock photos or generated people (10 hand-rolled inline-SVG trade icons instead), hero feed labeled "Illustration", department dots reflect real `/roster` build status (4 staffed / 3 hiring). |
| 2026-07-31 | Homepage redesigned product-first; CTA renamed "Request a demo"; two accessibility corrections to the palette | Founder brief: compete with the best B2B SaaS homepages, make the product visible above the fold, increase demo conversions, keep the existing positioning and voice. Rewrote `index-v2.html` + `styles-v2.css` in place (live `/` and `/preview`, no new routes, no new files). **Kept** the warm-paper palette, Fraunces, the blunt voice, and nearly all founder-approved copy — the palette *is* the differentiator against every blue/teal competitor, so the cool-grey/navy wireframes were used for hierarchy only, not for colour or type. **Hero** is now a real split: copy left, a working sample account right (department rail with per-employee live status → today's feed → one open item where Frontdesk answers tonight's call *because* Dispatcher wrote the warranty note in March). Shared memory is shown inside the interface and never explained in marketing copy. Only employees that actually exist in `employees.py` appear (Frontdesk, Reviews, Quote Chaser, Lead Qualifier, Dispatcher); the honesty tag reads "Sample account". **Structure** collapsed to 9 sections, each answering one question, with hairline rules instead of alternating grey blocks and two ink bands (the cost, the close) as the only contrast moments. The old separate monologue + calculator sections merged into one ink "cost" band. Trades moved out of the FAQ into their own 5×2 grid strip. **Copy** changes limited to: hero CTA "Request early access" → "Request a demo" (same `/request-access` form and backend — the 2026-07-21 request-led default is unchanged, only the label now names the 15-minute call the founder actually runs), "Five things that stop slipping" → "The work that stops slipping" (the grid holds six tiles), a new how-it-works H2, Dispatcher added as a fifth employee card, and the shared-memory tile labelled "How they work together" so it doesn't read as a role. **Two token corrections, both accessibility not taste:** new `--label: #6F6857` for 10–11px micro-labels (`--soon` at 2.7:1 failed AA), and `.btn-primary` filled with `--accent-dark` (white on `--accent` is 3.9:1). Verified: zero contrast failures across the page, no heading-level skips, every label bound, no horizontal overflow at 375/768/1280, calculator clamps junk input. **Motion:** the pulsing live-dot was cut — DESIGN.md forbids looping animation (it also blocked headless capture). Reveals still play once with the 3s safety net. |
| 2026-07-22 | Landing fully rebuilt: pain/proof-first flow + separate `/roster` org-chart page + free trial removed | Founder feedback over 3 rounds. (1) A first "office/department-first" homepage rebuild was rejected — it made the visitor *understand the company* before *feeling their own pain*. Corrected to the proven flow (hook → agitate → prove the loss → meet the office → proof → objections → price → convert), grounded in landing-page conversion research (loss-aversion made specific & attributable to inaction; specificity > abstraction; anchor before price; VoC copy, zero jargon — Avoca says "answer every call," never "LLM"). (2) The 14-role department org-chart moved OFF the homepage to a dedicated **`/roster` page** (`agent/landing/roster.html`, route in `app.py`) — homepage builds *desire* with 5 pain-named cards (Frontdesk/Quote Chaser/Dispatcher/Collections/Reviews) + a "See the Full Roster" button; the deep org-chart builds *belief* for the curious/investors. Apple "Meet iPhone → Learn more" pattern. New homepage sections: a **dark `--ink` agitation band** (`.statement`/`.monologue`) rendering the owner's real internal monologue ("I've got 18 missed calls and it's not even lunch," etc.) then the reframe "That's not you failing at business. That's a business running without an office"; restored ledger (loss aversion), restored 3-question objections FAQ. (3) **7-day free trial REMOVED everywhere** (founder directive) — replaced by a stronger cost-anchor: two-card `.anchor` compare ($45K/yr human office hire vs "One flat rate/month, a fraction of a single salary"), risk-reversal now "No setup fee. No contract. Cancel any day it stops earning its keep." + founding-group scarcity. No public monthly $ stated (still not locked). Honesty rules held: pre-revenue (no fake logos/testimonials — founder-run authenticity instead), sample morning-report labeled "illustrative," unbuilt roles badged Hiring/Coming Soon not faked live. Title/OG changed to trade-neutral "Run your business. We'll run the office." (departs from the 2026-07-16 HVAC-named-tags call — acceptable now that outreach is email/Loom, not cold-call text-preview). Palette/type/tokens unchanged. |
