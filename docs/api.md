# Routes

There is no separate "API" — `app.py` and `portal.py` serve HTML pages, form
posts, and webhooks from the same FastAPI app. This is a reference, not a
contract for external API consumers.

## `app.py` — public site, founder console, webhooks

| Route | Method | What it's for |
|---|---|---|
| `/` | GET | Public landing page (`index-v2.html`) |
| `/styles.css`, `/styles-v2.css` | GET | Landing page stylesheets |
| `/roster` | GET | Secondary marketing page |
| `/preview` | GET | Alias for the current landing page (kept after the homepage rewrite shipped) |
| `/thanks` | GET | Post-signup thank-you page |
| `/request-access` | POST | Early-access form submission |
| `/clients` | GET | Founder console — list of every business, behind HTTP Basic auth (`ADMIN_PASSWORD`) |
| `/clients/new` | GET, POST | Create a new client business |
| `/clients/{client_id}` | GET | Founder's view of one business (conversations, jobs) |
| `/clients/{client_id}/chat` | POST | Dashboard test-chat — simulate being the customer |
| `/clients/{client_id}/provision-number` | POST | Buy/attach a Twilio number |
| `/clients/{client_id}/retry-xai-registration`, `/attach-xai-number` | POST | xAI voice number registration |
| `/clients/{client_id}/employees/deploy` | POST | Deploy an AI employee for a business |
| `/clients/{client_id}/review-link` | POST | Set the Google/Yelp review link |
| `/clients/{client_id}/jobs/{job_id}/complete` | POST | Mark a job done (triggers review/referral asks) |
| `/clients/{client_id}/interests/{interest_id}/actioned` | POST | Founder actions an expansion-interest signal |
| `/clients/{client_id}/pipeline-stage` | POST | Move a client's founder-side pipeline stage |
| `/clients/{client_id}/referral-incentive` | POST | Set the referral incentive |
| `/clients/{client_id}/recovery/new`, `/recovery/{campaign_id}` | GET, POST | Create/view a Revenue Recovery campaign |
| `/clients/{client_id}/delete` | POST | Delete a client business |
| `/webhook/sms` | POST | Twilio inbound-SMS webhook — the main conversation entry point |
| `/webhook/voice-status` | POST | Twilio call-status callback (missed-call detection) |
| `/webhook/xai-incoming-call` | POST | xAI's realtime-call webhook — opens the voice WebSocket |

## `portal.py` — customer self-serve portal (`APIRouter`)

| Route | Method | What it's for |
|---|---|---|
| `/signup`, `/login`, `/logout` | GET/POST, GET/POST, POST | Customer account auth |
| `/auth/google/login`, `/auth/google/callback` | GET | Optional Google OAuth login |
| `/onboarding/business`, `/onboarding/receptionist` | GET, POST | Self-serve onboarding wizard |
| `/activation/live` | GET | Live-activation status page |
| `/dashboard` | GET | Legacy (pre-departments) dashboard |
| `/dashboard/test` | POST | Customer-facing test-chat |
| `/dashboard/source`, `/review-link`, `/referral-incentive` | POST | Dashboard settings actions |
| `/v2/dashboard` | GET | Overview — the department-first dashboard entry point |
| `/v2/dashboard/departments` | GET | All departments |
| `/v2/dashboard/departments/{department_key}` | GET | One Department Workspace |
| `/v2/dashboard/departments/{department_key}/employees/{role_key}` | GET | One Employee Workspace |
| `/v2/dashboard/departments/{department_key}/expand` | GET, POST | Expansion Workspace — request a new department |
| `/v2/dashboard/briefing` | GET | Briefing — synthesis read model across departments |
| `/v2/dashboard/notifications` | GET | Notification feed |
| `/roster/hire`, `/roster/hire/retention-manager` | GET, POST | Founding-roster hire flow |

`/v2/dashboard*` is the frozen architecture covered by
[`../ARCHITECTURE.md`](../ARCHITECTURE.md) — any change there should satisfy
its invariants, not just this table.
