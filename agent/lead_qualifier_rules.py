"""Lead Qualifier's vocabulary: keyword tables and structured reason codes.
Pure constants — no logic, no DB, no LLM. Kept separate from
lead_qualifier_engine.py so the engine module contains only classification
logic, per the founder's explicit split (2026-07-30).

Reason codes are a closed, structured vocabulary (rule IDs, not English
sentences) — stored on JobQualification.reasoning for analytics and
auditability, so a later query can group/count by exactly which rule fired
without parsing prose.
"""

REPLACEMENT_KEYWORDS = (
    "replace", "replacement", "new unit", "new system", "install a new", "installation",
)
MAINTENANCE_KEYWORDS = (
    "tune-up", "tune up", "maintenance", "inspection", "seasonal", "flush",
    "annual service", "check-up", "checkup",
)
FINANCING_KEYWORDS = (
    "financ", "afford", "payment plan", "monthly payment", "pay over time", "budget",
)
SPAM_KEYWORDS = (
    "seo", "web design", "marketing services", "google ranking",
    "increase your rankings", "link building",
)

REASON_JOB_TYPE_REPLACEMENT_KEYWORD = "JOB_TYPE_REPLACEMENT_KEYWORD"
REASON_JOB_TYPE_ESTIMATE_DEFAULT = "JOB_TYPE_ESTIMATE_DEFAULT"
REASON_JOB_TYPE_MAINTENANCE_KEYWORD = "JOB_TYPE_MAINTENANCE_KEYWORD"
REASON_JOB_TYPE_REPAIR_DEFAULT = "JOB_TYPE_REPAIR_DEFAULT"

REASON_FINANCING_REPLACEMENT_JOB_TYPE = "FINANCING_REPLACEMENT_JOB_TYPE"
REASON_FINANCING_KEYWORD_MATCH = "FINANCING_KEYWORD_MATCH"
REASON_FINANCING_NO_SIGNAL = "FINANCING_NO_SIGNAL"

REASON_MEMBERSHIP_HAS_PLAN = "MEMBERSHIP_HAS_PLAN"
REASON_MEMBERSHIP_JOB_TYPE_EXCLUDED = "MEMBERSHIP_JOB_TYPE_EXCLUDED"
REASON_MEMBERSHIP_ELIGIBLE_NO_PLAN = "MEMBERSHIP_ELIGIBLE_NO_PLAN"

REASON_PRIORITY_URGENCY = "PRIORITY_URGENCY_EMERGENCY_OR_SAME_DAY"
REASON_PRIORITY_JOB_TYPE_REPLACEMENT = "PRIORITY_JOB_TYPE_REPLACEMENT"
REASON_PRIORITY_FINANCING_CANDIDATE = "PRIORITY_FINANCING_CANDIDATE"
REASON_PRIORITY_NORMAL_NO_SIGNAL = "PRIORITY_NORMAL_NO_SIGNAL"

REASON_SPAM_INVALID_CALLBACK = "SPAM_INVALID_CALLBACK"
REASON_SPAM_BLANK_SERVICE_TYPE = "SPAM_BLANK_SERVICE_TYPE"
REASON_SPAM_KEYWORD_MATCH = "SPAM_KEYWORD_MATCH"
REASON_SPAM_CLEAN_NO_SIGNAL = "SPAM_CLEAN_NO_SIGNAL"
