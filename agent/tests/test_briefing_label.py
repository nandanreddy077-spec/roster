"""The Briefing's visible name is provisional (blueprint §4a: "safe to build
against, not safe to consider final").

So routes, modules and template filenames stay `briefing` forever, and the
one thing that changes when the name changes is a single constant. These
tests make that structural rather than a convention someone has to remember.
"""

from pathlib import Path

import portal

TEMPLATES = Path(__file__).parent.parent / "templates"


def test_the_visible_label_is_a_single_constant():
    assert isinstance(portal.BRIEFING_LABEL, str) and portal.BRIEFING_LABEL.strip()


def test_every_portal_template_can_read_the_label_without_being_passed_it():
    """Registered as a Jinja global, so a route that forgets to pass it can't
    silently render an empty heading."""
    assert portal.templates.env.globals["briefing_label"] == portal.BRIEFING_LABEL


def test_no_template_hardcodes_the_visible_label():
    """The guard that makes renaming a one-line change. Currently vacuous —
    no Briefing template exists until Task 7 — but it fails the moment one
    hardcodes the words instead of using {{ briefing_label }}."""
    offenders = [
        path.name for path in TEMPLATES.rglob("*.html") if portal.BRIEFING_LABEL in path.read_text()
    ]
    assert offenders == [], (
        f"{offenders} hardcode the Briefing's visible name — use "
        "{{ briefing_label }} so renaming it stays a one-line change"
    )
