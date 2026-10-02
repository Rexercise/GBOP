import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from gbop_voice_web.forex_news import (
    ForexNewsEvent,
    due_high_impact_events,
    event_delivery_key,
    format_red_folder_alert,
    parse_high_impact_events,
)


ET = ZoneInfo("America/New_York")


class ForexNewsTests(unittest.TestCase):
    def test_parser_keeps_only_high_impact(self):
        payload = [
            {
                "title": "Non-Farm Employment Change",
                "country": "USD",
                "date": "2026-10-02T08:30:00-04:00",
                "impact": "High",
                "forecast": "75K",
                "previous": "22K",
            },
            {
                "title": "Low Impact Example",
                "country": "USD",
                "date": "2026-10-02T09:00:00-04:00",
                "impact": "Low",
            },
        ]

        events = parse_high_impact_events(payload)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].currency, "USD")
        self.assertEqual(events[0].title, "Non-Farm Employment Change")
        self.assertEqual(events[0].scheduled_at.hour, 8)
        self.assertEqual(events[0].forecast, "75K")

    def test_due_window_excludes_past_and_far_future(self):
        now = datetime(2026, 10, 2, 8, 17, tzinfo=ET)
        events = [
            ForexNewsEvent(
                "Due",
                "USD",
                now + timedelta(minutes=13),
            ),
            ForexNewsEvent(
                "Too Far",
                "USD",
                now + timedelta(minutes=16),
            ),
            ForexNewsEvent(
                "Past",
                "USD",
                now - timedelta(minutes=1),
            ),
        ]

        due = due_high_impact_events(events, now, lead_minutes=15)

        self.assertEqual([event.title for event in due], ["Due"])

    def test_delivery_key_does_not_change_with_forecast_revision(self):
        when = datetime(2026, 10, 2, 8, 30, tzinfo=ET)
        first = ForexNewsEvent(
            "CPI m/m",
            "USD",
            when,
            forecast="0.2%",
            previous="0.3%",
        )
        revised = ForexNewsEvent(
            "CPI m/m",
            "USD",
            when,
            forecast="0.4%",
            previous="0.3%",
        )

        self.assertEqual(event_delivery_key(first), event_delivery_key(revised))

    def test_alert_contains_time_currency_and_risk_reminder(self):
        now = datetime(2026, 10, 2, 8, 20, tzinfo=ET)
        event = ForexNewsEvent(
            "CPI m/m",
            "USD",
            datetime(2026, 10, 2, 8, 30, tzinfo=ET),
            forecast="0.2%",
            previous="0.3%",
        )

        message = format_red_folder_alert([event], now, lead_minutes=15)

        self.assertIn("8:30 AM ET", message)
        self.assertIn("USD", message)
        self.assertIn("CPI m/m", message)
        self.assertIn("Never bring regular size into a binary event", message)


if __name__ == "__main__":
    unittest.main()
