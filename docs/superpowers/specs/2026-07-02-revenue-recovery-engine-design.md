# Revenue Recovery Engine — Design Spec

**Date:** 2026-07-02  
**Status:** Design approved, ready for implementation plan

---

## Executive Summary

**Revenue Recovery** is a standalone Roster agent that pursues two customer types via automated SMS sequences: unsold estimates ("quote follow-up") and lapsed maintenance customers ("reactivation/rebooking"). It uses the same underlying engine and sequence logic with different message templates and entry points.

**Key outcome:** Customer replies "yes" → Recovery closes the booking itself (confirms time, checks their calendar, creates a Job).

**Independence:** Fully standalone. Works without Frontdesk or any other agent. Can be purchased/enabled per-client independently.

---

## Problem & Opportunity

### The Gap
Home-service contractors leave massive revenue on the table through incomplete follow-up:
- **Quote follow-up:** 25–35% of unsold estimates convert with zero follow-up; structured 3-week sequences lift this to 45–55% (~doubling the rate)
- **Reactivation:** Dormant customers convert at 15–30% (vs 3–5% for cold leads) and deliver 10–20x ROI on campaign spend
- **Current state:** Most contractors follow up once or twice, then abandon the deal. Large projects have 30–60 day sales cycles; the money disappears at day 2

### Why This Agent
1. **Unworked money:** Incumbents (Housecall Pro, Jobber, ServiceTitan) automate *reminders* and *reviews*, but none runs persistent conversational pursuit
2. **Outcome pricing fit:** "We revived that $8K quote you'd written off" is cleanly chargeable per-booked-job
3. **Wedge strength:** 10–20x ROI at scale makes this a natural follow-on to Frontdesk (same business, different problem solved)
4. **Two-for-one:** Quote follow-up and reactivation share ~90% of machinery; build once, sell twice

---

## Design

### Core Flow

#### 1. Owner Creates a Campaign
Dashboard form captures:
- **Campaign type:** "Quote Follow-Up" OR "Reactivation/Rebooking"
- **Customer list:** Phone, name, service type, estimate amount (quotes) OR date of last service (reactivation)
- **Optional overrides:** Custom message templates per message slot

#### 2. Automated Sequence (5–6 texts over 3–4 weeks)
Schedule: Day 1, 3, 7, 14, 21, 28

Each message:
- Uses Roster-provided default template (customizable)
- Contains variables: `{{customer_name}}`, `{{service_type}}`, `{{estimate_amount}}`, `{{days_since}}`
- Single-purpose: re-engage, remind, nudge, urgency, final touch

**Quote Follow-Up Template Example:**
- Day 1: "Hi {{customer_name}}, just checking in on that {{service_type}} quote we sent. Still interested? 👍"
- Day 3: "{{customer_name}}, spots opening up for {{service_type}} — let me know if you want to lock one in"
- Day 7: "Quick reminder: the {{service_type}} quote expires at the end of the month. Book now?"
- Day 14: "{{customer_name}}, before we move on — any blockers with the {{service_type}} quote? Happy to adjust"
- Day 21: "Last chance: {{service_type}} quote is expiring soon. Confirm or let us know?"
- Day 28: (Optional) "Going once more: ready to book that {{service_type}}?"

**Reactivation Template Example:**
- Day 1: "Hi {{customer_name}}, time for your annual {{service_type}} tune-up! Book now while spots are open 📅"
- Day 3: "{{customer_name}}, HVAC tune-ups keep systems running smooth — let's get you scheduled"
- Day 7: "Heads up: peak season for {{service_type}} is here. Availability filling fast — reserve your slot?"
- Day 14: "{{customer_name}}, been a while! Ready for your {{service_type}} maintenance?"
- Day 21: "Last call for {{service_type}} before busy season. Lock in your appointment now?"
- Day 28: (Optional) "{{customer_name}}, your system needs attention. Book your service today?"

#### 3. Customer Replies

**If positive signal** ("yes", "👍", "interested", "book me", etc.):
- Recovery responds: "Great! When works best — Tuesday 2–4pm, Wednesday 10am–12pm, or Thursday 3–5pm?"
- Times pulled from owner's chosen calendar (Google, Jobber, Housecall Pro) or fallback to business hours
- Up to 3 proposed slots across the next 5–7 business days

**If negative signal** ("no", "not interested", "pass", etc.):
- Log response, stop messaging for this customer
- Mark as "declined" or "unsubscribe"

**If no reply after 6 touches:**
- Stop messaging
- Mark as "no response"
- Owner can manually reactivate if needed

#### 4. Booking Confirmation
Customer replies with a time slot preference ("Tuesday 2pm works"):
- Recovery confirms: "Perfect! You're booked for {{service_type}} on Tuesday 7/15 at 2pm. Bring your account number. Any questions? Reply here."
- Creates a **Job** record (client_id, customer_phone, service_type, address if available, time, notes)
- Owner sees new job on dashboard, can assign/dispatch

---

## Data Model

### New Tables

**RecoveryCampaign**
```
id (PK)
client_id (FK → Client)
face ("quote" or "reactivation")
name (user-friendly campaign name, e.g. "June Unsold Quotes")
customer_list_json (list of {phone, name, service_type, estimate_amount, date_sent})
template_overrides_json (map of day → custom message text, if owner customized)
started_at (datetime when campaign began)
is_active (bool)
created_at, updated_at
```

**RecoveryJob**
```
id (PK)
campaign_id (FK → RecoveryCampaign)
customer_phone
customer_name
service_type
estimate_amount (nullable, for quotes)
days_since_last_service (nullable, for reactivation)
current_status ("pending", "responded_yes", "responded_no", "no_response", "booked", "opted_out")
booked_job_id (FK → Job, populated when recovery successfully books)
created_at, updated_at
```

**RecoveryMessageLog**
```
id (PK)
recovery_job_id (FK → RecoveryJob)
message_day (1–6, indicating which touch in sequence)
message_text (the actual text sent)
sent_at (datetime)
delivered (bool)
customer_reply (text of reply received, if any)
replied_at (datetime of reply)
created_at
```

### Extend Existing

**Job** table: add optional field `recovery_job_id (FK → RecoveryJob)` to link booked jobs back to the campaign that created them (for attribution / analytics).

---

## Calendar Integration

**Owner choice during onboarding (per-client):**
- Google Calendar (OAuth, read free/busy)
- Jobber API (fetch schedule)
- Housecall Pro API (fetch schedule)
- Manual (owner provides available times via dashboard; Recovery offers only those)

**Fallback:** If no calendar connected, Recovery uses `Client.hours` (business hours from ClientConfig) to propose times.

**Proposed slots:** Recovery surfaces 3 time windows 2–7 days out, within business hours, respecting technician/crew availability if calendar data exists.

---

## Templates & Customization

### Default Templates
Roster provides 6 templates per face (quote + reactivation), one per message day. Vetted copy, trade-specific variants (HVAC vs Plumbing vs Electrical, etc.).

### Owner Customization
At campaign setup, owner can:
- Use defaults as-is (recommended, fastest)
- Override 1 or more messages (e.g., "I want Day 1 to mention my Yelp reviews")
- Keep the rest as defaults

UI: Simple text editor per message day, with variable hints (e.g., "Available variables: {{customer_name}}, {{service_type}}, {{estimate_amount}}, {{days_since}}").

---

## Independence / No Dependencies

**Recovery is fully standalone:**
- Does NOT require Frontdesk to be enabled
- Does NOT call Frontdesk's booking logic
- Owns its own intake, confirmation, and booking logic
- Can be purchased/enabled independently per client

**Coexistence:** If a client has both Frontdesk and Recovery:
- Frontdesk handles inbound missed-call texts
- Recovery handles outbound pursuit of old estimates/customers
- No cross-contamination; each owns its customer list

---

## Success Metrics

- **Booking rate:** % of customers who reply "yes" and successfully book
- **Conversion per touch:** Response rate by message day (decay over sequence)
- **Revenue recovered:** $ value of jobs booked via Recovery
- **ROI:** Revenue recovered vs. SMS costs (Twilio)
- **Opt-out rate:** % who request unsubscribe (compliance check)

---

## Constraints & Scope

### In Scope
- SMS-only messaging (no email, push, in-app)
- Synchronous booking (customer confirms time via text, done immediately)
- Single business rule: one sequence per customer per campaign (no re-enters for same estimate)
- Manual campaign creation (owner pastes list + picks type)

### Out of Scope (Phase 1)
- CRM auto-sync (no pulling open estimates from Jobber automatically)
- Multi-channel (email, push, in-app)
- A/B testing templates
- Asynchronous booking (customer books online, outside SMS thread)
- Geofencing or location-based triggers
- Integration with payment processing (payment links in SMS)

---

## Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| Customer opt-outs (legal compliance) | Require unsubscribe link in SMS. Honor STOP immediately. Log all opts. |
| Over-aggressive messaging (6 texts = spam) | Data shows 60% response avg; 6 over 4 weeks is ~1.5/week, acceptable. Monitor opt-out rate. |
| Calendar integration fragility (Google auth expires, API breaks) | Graceful fallback: if calendar unavailable, use config hours. Log failures. Alert owner. |
| Booking logic breaks, customer loses interest | Confirmation text sent immediately. If Job creation fails, message still went — follow up manually. Retry Job creation in background. |
| Duplicate bookings (customer confirms same slot twice) | Check for existing booking at that time before creating new Job. Show warning. |

---

## Next Phase

Once Revenue Recovery is stable with real customers:
1. **CRM sync:** Auto-pull open estimates from Jobber/Housecall Pro
2. **Multi-channel:** Email follow-up for customers who don't text back (sequential fallback)
3. **Analytics dashboard:** Revenue recovered per campaign, response rates, booking funnel
4. **A/B testing:** Owner can test template variants on 10% of list, measure lift
5. **Payment links:** Send invoice/payment link in confirmation text for faster cash flow

---

## Appendix: Message Template Defaults

### Quote Follow-Up (Trade: HVAC)
1. Day 1: "Hi {{customer_name}}, just following up on that {{service_type}} estimate we sent on {{date_sent}}. Still interested? Let me know 👍"
2. Day 3: "{{customer_name}}, {{service_type}} season is picking up — spots filling fast. Want to lock in a time?"
3. Day 7: "Quick reminder: that {{service_type}} quote is good through the end of the month. Reply YES to book now"
4. Day 14: "{{customer_name}}, anything holding you back on the {{service_type}} quote? Happy to answer questions or adjust"
5. Day 21: "Your {{service_type}} quote expires soon. Ready to move forward? Yes or no?"
6. Day 28: "Last chance: ready to book your {{service_type}}? Reply YES or let me know"

### Reactivation (Trade: HVAC)
1. Day 1: "Hi {{customer_name}}, it's been {{days_since}} since your last service. Time for a tune-up! Book now while spots are open 📅"
2. Day 3: "{{customer_name}}, HVAC tune-ups prevent breakdowns and keep systems efficient. Let's get you scheduled"
3. Day 7: "Peak season for {{service_type}} is here. Availability filling fast. Ready to book?"
4. Day 14: "{{customer_name}}, been a while! Your system could use maintenance. Book your {{service_type}} now?"
5. Day 21: "{{customer_name}}, last call for {{service_type}} before the rush. Lock in your appointment today"
6. Day 28: "{{customer_name}}, your {{service_type}} system needs attention. Book your maintenance today? Yes?"

---

**Design approved by:** User  
**Ready for:** Implementation planning
