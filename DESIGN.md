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

**Explored 2026-08-27, then parked.** A mechanism-proof direction was worked up
in full — editorial layout, hairline rules instead of cards, and "the refusal"
(Roster declining to promise a time nobody confirmed) promoted to the page's
signature. The founder then chose to replicate a supplied reference design 1:1
instead (see the Decisions Log), so the shipped landing is that reference:
card-based, feature-grid structure, a live-voice-demo section, a slider loss
calculator, and a market-positioning comparison table. The mechanism-proof
direction is not deleted — if the reference approach underperforms, it is the
documented fallback. The honesty corrections from that pass DID ship: no
ServiceTitan-sync claim, no invented per-customer stats, employee names matched
to `employees.REGISTRY`, and unstaffed roles marked as such.

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
- **Data (landing, added 2026-08-27):** `--mono: "JetBrains Mono", ui-monospace, Menlo, monospace`
  with `tabular-nums`. Pinned (not left to the OS) because `$117,000` at ~56px is
  the loudest type on the page. **Usage rule — mono only where values genuinely align in a
  column:** feed timestamps, job IDs, call durations, ledger dollar figures. Never on
  eyebrows, section labels, or decorative microtext — mono-as-"technical-vibes" is
  trend slop, not an operational signal, and it is the one thing that separates
  reading operational from cosplaying it.
- **Scale (landing):** five roles, one job each. **Serif does headlines only, sans
  does everything else, mono does data** — no role overlaps.
  Display `clamp(40px,5.4vw,60px)`/1.02/-0.033em · Section `clamp(28px,3.2vw,38px)`/1.08/-0.028em
  · Subhead 19px/1.3/-0.014em · Body 17px/1.6 · Micro 13px. Plus one eyebrow
  (11px/700/0.15em caps, sans).
- **Scale (dashboard):** H1 28px, H2 18px, H3 16px, body 14px, small 12–13px (existing).
- **Loading:** `<link>` to Google Fonts (Fraunces) on the landing page only — the
  dashboard keeps its existing serif fallback stack (internal tool, no webfont cost
  justified).

## Color

**The landing page (`index-v2.html` / `styles-v2.css`) runs "Slate & Ember"
as of 2026-08-28.** The dashboard and pre-v2 pages still use the warm-ivory/rust
system below — the two surfaces have forked on purpose (see the Decisions Log
entry for why the warm system was retired on the landing).

### Landing — Slate & Ember
- **The formula** (studied off Probook, Linear, Decagon, Sierra, Ramp — their
  live computed styles): ~90% of the page is two colours (a near-neutral ground
  + a warm near-black ink), body text is the ink lifted, and ONE accent lands on
  ~2% of pixels. Every value lives in one `:root` block in `styles-v2.css`.
- **Tokens** (all verified >=4.5:1 for the roles they carry):
  - `--bg: #F5F5F3` (neutral-warm near-white) · `--bg-deep: #ECEBE7` (footer/recessed)
  - `--card: #FFFFFF` · `--border: #E3E2DD` · `--border-soft: #EDECE8`
  - `--text: #1A1A17` (warm near-black) · `--text-dim: #63625C` (the ink lifted)
  - `--label: #636259` (micro-labels)
  - `--accent: #C2410C` (burnt ember) · `--accent-dark: #9A3412` (clears AA behind white)
  - `--accent-soft: #F6E5DA` (the one tinted comparison row)
  - `--success: #1E6E43` · `--success-soft: #E1EFE7` (green — "booked", checkmarks)
- **Ember is on ~5 things and no more:** the primary CTA, the emphasised word
  "software" in the H1, the `$117,000` loss figure, the current nav item, and
  in-prose links. Section eyebrows are grey `--label`; stat numbers are
  near-black. If the render shows ember on a sixth kind of element, that is drift.
- **Type — Geist, not Fraunces.** Every premium reference in this class uses a
  tight neo-grotesque, not a literary serif; the serif was the single biggest
  "AI editorial" tell. Geist 400/500/600/700 via Google Fonts; display tracking
  −0.03em; `--serif` kept as the var name but points at Geist. Inline numbers use
  Geist Sans + `tabular-nums` (Geist Mono ballooned commas/colons at these sizes).
- **Why not the two earlier attempts:** warm ivory + terracotta + Fraunces read
  as "Claude-generated" (it is Anthropic's brand language). Kraft + blueprint blue
  was rejected 0/10 — too much chroma in the neutral, and the literal
  "materials" concept read as costume. Slate & Ember is restraint executed the
  way the reference companies do it: burnt ember is the accent nobody in this
  exact field owns (home services is blue/green, AI agents are indigo).

### Dashboard + pre-v2 pages — warm ivory/rust (unchanged)
- `--bg: #F7F4EC` · `--bg-alt: #F0EBDF` · `--card: #FFFFFF` · `--border: #E4DDCB`
- `--text: #221B12` · `--text-dim: #6B6354`
- `--accent: #B24E2A` · `--accent-dim: #F0D7C7` · `--accent-dark: #8A3D1E` (white on it 7.6:1)
- Landing-only semantic additions that shipped with the old system: `--urgency: #A8331F`,
  `--proof: #3F6B4A`. Superseded on the landing by Slate & Ember's `--accent`/`--success`.
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
- **Card discipline (landing, added 2026-08-27) — NOT in force on the shipped
  page.** This rule (a card only for a discrete product object; concept-lists get
  hairline rules) belongs to the parked mechanism-proof direction above. The
  shipped reference layout is deliberately card-based throughout. Kept here as the
  rule to restore if that direction is ever revived.
- **Tonal restraint (landing, added 2026-08-27):** the shipped page runs on one
  paper ground with a single tinted band (the comparison section) and a recessed
  footer. Cards sit on `--card`. Resist adding more grounds — the review pass
  found the page had drifted to four tinted bands alternating like zebra stripes,
  which is the generic-SaaS rhythm the aesthetic is meant to avoid.

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
1. **[RETIRED 2026-08-28 on the landing — see Color / Decisions Log]** **Warm paper palette instead of SaaS blue/teal.** Every direct competitor looks
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
| 2026-08-19 | Palette deepened: rust accent + near-black text, both landing surfaces | `/design-consultation`, user picked "Deepened Rust" from 3 proposed directions (Deepened Rust / Olive Trade / Ink & Brass) — lowest-risk evolution of the existing warm-paper system rather than a replacement, since the palette is the stated differentiator against blue/teal competitors (2026-07-31 entry). `--text` #1F1B16→#221B12, `--accent` #C2693D→#B24E2A, `--accent-dim` #F1DDD0→#F0D7C7, `--accent-dark` #A2532F→#8A3D1E. Applied to both `styles.css` and `styles-v2.css` (shared token names/values across `index-v2.html` and `roster.html`) so the two live landing surfaces don't visually fork. `--proof`, `--urgency`, `--label`, `--soon`, `--ink*` all untouched — the trust/urgency signal colors stay distinct from the brand accent, same reasoning as the original palette brief. Verified white-on-`--accent-dark` still clears AA (7.6:1, up from the 2026-07-31 fix's 3.9:1 floor). Olive Trade was rejected because it would collide with the existing `--proof` trust-green signal; Ink & Brass was rejected as too large a departure from the spacious/airy feel DESIGN.md calls out as intentional for mobile trades users. |
| 2026-08-19 | Employee grid → persona cards; "See it work" → a timeline; hero horizontal-overflow bug fixed | User asked for craft-level inspiration from Broccoli (named AI-agent personas, e.g. "Dane"/"Amy") and Avoca (timestamped sequential call-flow visualization instead of static screenshots) — composition patterns only, no copy/stats/integrations borrowed (both are competitors with real customer data Roster doesn't have). Applied via the `ckw-design` plugin skill (design-thinking + design-system + design-spatial), enabled in `~/.claude/settings.json` this session. **Employee grid** (`#team`): each of the 5 live-role cards gets a small circular initial-avatar + a `Working` status pill (`.emp-head`/`.emp-avatar`/`.emp-status`), same visual language as `/roster`'s existing `.emp-avatar`/`.status-pill` so the two pages read as one team roster — all 5 are genuinely `"live"` in `employees.py`, so no `Hiring` pill appears here (unlike `/roster`, which also lists planned roles). **"See it work"** (`#work`): the numbered list gained a continuous hairline rail (`.flow::before`) down the number column and a small relative time-tag per step (`9:47 PM` / `Seconds later` / `Same call` / `Still 9:47 PM` / `Next morning`) — all drawn from the example already in the copy, no invented speed metric (Avoca's own timestamps are real customer data Roster doesn't have). **Bug found by the design-spatial mandatory overflow gate** (`scrollWidth − clientWidth` at 390/1024/1280px, run for real this time via a local HTTP server + fresh browser tabs — an artifact-preview tab had been silently misreporting `innerWidth` in earlier sessions): `.hero-grid`'s single-column mobile track had no explicit `grid-template-columns`, so CSS Grid's implicit-track default sized it to the `.convo-wrap` card's max-content width (a `min-width:auto` grid-item default), overflowing the viewport by 56px below the 1000px breakpoint — invisible at desktop width, which is exactly why it shipped in the 2026-07-31 pass despite that entry's own "no horizontal overflow at 375/768/1280" claim. Fixed with `grid-template-columns: minmax(0, 1fr)` on the base rule. Re-verified 0 overflow at 390/1024/1280 on both `index-v2.html` and `roster.html`; full pytest suite still 1181 passed (unaffected — HTML/CSS only). |
| 2026-08-28 | Landing palette retired: warm ivory/rust -> (Blueprint & Kraft, rejected) -> **Slate & Ember**; Fraunces -> Geist | Founder: "because of colour pallets people are thinking it's claude generated". Correct read -- warm off-white + one terracotta accent + a literary serif is the 2025-26 "tasteful AI output" signature (it is Anthropic's brand). **Attempt 1 (Blueprint & Kraft** -- kraft ground, blueprint-blue accent, a faint drafting grid): founder rejected 0/10, "worst" -- too much chroma in the neutral, and the literal materials concept read as costume. **Attempt 2 (Slate & Ember, shipped):** researched the live computed palettes of Probook, Linear, Decagon, Sierra and Ramp -- the shared formula is a near-neutral ground + a warm near-black ink + ONE accent on ~2% of the page + a tight neo-grotesque, never a serif. Applied: `--bg #F5F5F3`, `--text #1A1A17`, one accent burnt-ember `#C2410C` / `#9A3412` on the CTA + the "$117,000" loss figure + current nav + in-prose links only (eyebrows -> grey, stat numbers -> near-black), green `#1E6E43` keeps "booked/live". **Fraunces -> Geist** (400-700, Google Fonts, -0.03em display tracking) -- the serif was the single biggest AI tell; every reference company uses a grotesque. Inline numbers moved to Geist Sans + tabular-nums (Geist Mono ballooned commas/colons). `theme-color` + font links updated on all three landing pages. Fresh-eyes judge: 7/10, "clears the bar the first two attempts failed". Contrast 0 failures at 390/768/1280/1440; 1366 tests pass. Dashboard + pre-v2 pages keep the warm system -- the surfaces have forked on purpose. Layout (5x repeated section template) flagged by the judge as the next iteration, separate from the palette. |
| 2026-08-27 | Landing rebuilt to replicate a founder-supplied reference 1:1, then an interaction layer + real xAI voice added | **Sequence:** (a) `/design-consultation` proposed a mechanism-proof editorial direction (hairline rules, "the refusal" as signature) — built, reviewed, then set aside; (b) founder directed "make it exactly like this reference, just change colour palettes", so the page was rebuilt section-for-section from the reference: nav + dark-pill CTA, hero with a cycling office panel, "Operational Simplicity" 3-card how-it-works, 6-card AI-roster grid, live-voice-demo (tabs + waveform + transcript), "Industry Problem" (struck headline, quote box, 4 stat tiles, slider loss calculator), 6-column comparison table, 2-card pricing, demo form. (c) Three palette options rendered on that layout — Warm Ivory & Rust (the reference’s own, shipped as default), Sand & Oxblood, Ember on Charcoal — founder still choosing; palette lives in ONE `:root` block in `styles-v2.css`, no hex anywhere else, so a swap is one block. (d) Fresh-eyes design review (dev-team:web-design-reviewer) found 12 real defects, all fixed and re-measured: the scenario tabs rendered all 12 transcript lines at once (`display:grid` beat the `hidden` attribute), see-through sticky nav, 98px post-load layout shift, 156px dead void under the hero rail, no pricing CTA, no mobile nav on an 11k-px page, 15 sans font-sizes with 0.5px steps, one card component with 5 paddings. (e) Interaction layer: hover-lift on cards, tap ripple from the touch point, nav scroll-spy, clickable hero rail, slider fill + value bump — all `@media (hover:hover)` gated and dropped under `prefers-reduced-motion`. (f) **Voice:** the product runs xAI Grok Voice Agent (`xai_voice_adapter.py`, voice `eve`); its realtime API needs a SIP `call_id` and can’t be called from a browser, so `scripts/build_demo_voice.py` pre-renders the call replay with xAI’s standalone TTS (`POST /v1/tts`) ONCE into `agent/static/voice/*.mp3` + `manifest.json` (~$0.03/run, committed, zero API cost per visitor). Player is dual-mode: real MP3s when the manifest exists, browser speech-synthesis fallback (novelty-voice blocklist + quality ranking) otherwise. Dialogue rewritten for real phone cadence with `[pause]`/`[breath]`/`<soft>` speech tags on the audio and clean text in the transcript; employee at speed 0.96, stressed caller at 1.06; picker offers 6 curated xAI voices, caller auto-picks the opposite gender. **Honesty corrections that shipped:** no ServiceTitan-sync claim (`calendar_provider.py` only wires `ManualCalendarProvider`); transcript shows what the product actually says, not a confirmed-time claim (`booking_language.assert_no_confirmation_claim` raises on those); "62% calls unanswered" replaced with the sourced 27% (Invoca via ServiceTitan); employee names matched to `employees.REGISTRY` (Reviews→Happy Call, Rebooker→Retention Manager, Lead Qualifier added); `$149–$199` pricing and the `280ms` latency badge KEPT per founder ("reference exactly, claims included") — flagged as unbacked. `research/` dir is exploration notes, not part of the page. |
