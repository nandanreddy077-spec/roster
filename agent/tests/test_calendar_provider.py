from calendar_provider import ManualCalendarProvider, get_calendar_provider


def test_manual_provider_returns_requested_count():
    provider = ManualCalendarProvider()
    slots = provider.get_available_slots("Mon-Sat 7am-7pm", count=3)
    assert len(slots) == 3


def test_manual_provider_skips_sundays():
    provider = ManualCalendarProvider()
    slots = provider.get_available_slots("Mon-Sat 7am-7pm", count=5)
    for s in slots:
        weekday_name = s.split()[0]
        assert weekday_name != "Sunday"


def test_get_calendar_provider_returns_manual_provider():
    provider = get_calendar_provider(client=None)
    assert isinstance(provider, ManualCalendarProvider)
