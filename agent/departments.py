"""Department registry — the customer-facing unit of Roster's product.

A department is a business capability delivered by a coordinated team of
specialised AI employees that share the same business context, memory, and
objectives. Customers hire departments because they buy outcomes, not
individual AI employees. (Product blueprint §1:
docs/superpowers/specs/2026-07-28-departments-product-blueprint-design.md)

This is a CODE registry, not a table — same pattern as employees.py's
EmployeeDefinition and runner.py's RoleDefinition. Department membership is
NOT duplicated here: it is derived from each EmployeeDefinition's own
`department` tag, so the two registries cannot drift out of sync.

The copy fields are customer-facing. `mission` heads an active department's
page; `problem`/`outcome`/`why_adopt` are rendered on an INACTIVE
department's card, which the blueprint (§7) requires to educate rather than
just report absence. Voice follows DESIGN.md: warm, blunt, plain, no jargon.
"""
from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class Department:
    key: str
    display_name: str
    mission: str      # one line, heads the department page
    problem: str      # inactive card: what's broken today
    outcome: str      # inactive card: what changes when it's staffed
    why_adopt: str    # inactive card: why an owner eventually wants it
    hireable: bool = True


# Display order is customer-facing and matches the live /roster page.
# Mission lines are reused verbatim from agent/landing/roster.html — already
# founder-approved copy in the right voice; do not rewrite them.
REGISTRY: List[Department] = [
    Department(
        key="customer_service",
        display_name="Customer Service",
        mission="Every call answered, every happy customer thanked.",
        problem=(
            "The phone rings while you're under a sink or on a roof. Nobody "
            "picks up, and the job goes to whoever answers next."
        ),
        outcome="Every call gets answered and the job gets on the books, day or night.",
        why_adopt=(
            "Usually the first department a shop staffs — a missed call is "
            "money gone today, not money gone next quarter."
        ),
    ),
    Department(
        key="sales",
        display_name="Sales",
        mission="Nobody works a quote, so it goes cold. This department doesn't let it.",
        problem=(
            "Estimates go out and nobody works them. Most quotes need several "
            "follow-ups before they close, and yours get one."
        ),
        outcome=(
            "Every open estimate gets chased until it's a yes or a no, so fewer "
            "of them just go quiet."
        ),
        why_adopt=(
            "Usually added once Customer Service is booking steadily and the "
            "pile of unanswered estimates becomes the obvious next leak."
        ),
    ),
    Department(
        key="operations",
        display_name="Operations",
        mission="The crew runs on schedule, even when nobody's watching it.",
        problem=(
            "Jobs get booked, then someone has to work out who's going where — "
            "and that someone is you, between jobs."
        ),
        outcome=(
            "The right tech gets to the right job, and urgent work finds the "
            "nearest free truck without a scramble."
        ),
        why_adopt=(
            "Usually added once there are enough trucks that dispatching stops "
            "being something you can hold in your head."
        ),
    ),
    Department(
        key="finance",
        display_name="Finance",
        mission="The money owed gets collected, not just invoiced.",
        problem=(
            "Invoices go out, and collecting on them means being the bad guy — "
            "or not collecting at all."
        ),
        outcome=(
            "Money you've already earned actually lands in the account, without "
            "an awkward phone call from you."
        ),
        why_adopt=(
            "Usually after noticing how much sits unpaid past 30 days once "
            "Customer Service and Operations are already busy booking and "
            "running jobs."
        ),
    ),
    Department(
        key="customer_success",
        display_name="Customer Success",
        mission="Old customers become repeat customers.",
        problem=(
            "Old customers who'd happily book again are sitting in a list nobody "
            "has time to call, and maintenance plans lapse quietly."
        ),
        outcome=(
            "Past customers come back on their own schedule, and plans get "
            "renewed before they expire."
        ),
        why_adopt=(
            "Usually added once there's enough customer history on the books to "
            "be worth working — it's the cheapest revenue in the business."
        ),
    ),
    Department(
        key="marketing",
        display_name="Marketing",
        mission="The phone rings without you spending on ads to make it ring.",
        problem=(
            "Happy customers would refer you and buy more, but nobody's "
            "consistently asking them to."
        ),
        outcome="The phone rings more without spending on ads to make it ring.",
        why_adopt=(
            "Usually once Customer Success is already rebooking old customers "
            "and the natural next question is \"how do I get new ones the same "
            "way.\""
        ),
    ),
    Department(
        key="leadership",
        display_name="Leadership",
        mission="Someone's watching the business, even at 11pm.",
        problem=(
            "You find out how the week really went by feel, usually after it's "
            "too late to do anything about it."
        ),
        outcome=(
            "One place that tells you what happened across the whole operation "
            "and what it means."
        ),
        why_adopt=(
            "Included with every workforce from the first department onward — it "
            "gets sharper as more departments come online and there's more to "
            "connect."
        ),
        hireable=False,
    ),
]
