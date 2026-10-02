import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from gbop_voice_web.forex_news import (
    ForexNewsEvent,
    due_high_impact_events,
    event_delivery_key,
    format_pre_shift_news,
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

    def test_pre_shift_message_mentions_news_day_and_shift_event(self):
        now = datetime(2026, 10, 2, 8, 55, tzinfo=ET)
        events = [
            ForexNewsEvent(
                "Non-Farm Employment Change",
                "USD",
                datetime(2026, 10, 2, 10, 0, tzinfo=ET),
            ),
        ]

        message = format_pre_shift_news(events, now, shift="day")

        self.assertIn("red-folder news today", message)
        self.assertIn("10:00 AM ET", message)
        self.assertIn("Non-Farm Employment Change", message)
        self.assertIn("during Day Shift", message)

    def test_pre_shift_message_mentions_earlier_red_news(self):
        now = datetime(2026, 10, 2, 8, 55, tzinfo=ET)
        events = [
            ForexNewsEvent(
                "Non-Farm Employment Change",
                "USD",
                datetime(2026, 10, 2, 8, 30, tzinfo=ET),
            ),
        ]

        message = format_pre_shift_news(events, now, shift="day")

        self.assertIn("No additional red-folder releases remain today", message)
        self.assertIn("8:30 AM ET", message)

    def test_five_minute_alert_includes_member_plan_and_risk_plan(self):
        now = datetime(2026, 10, 2, 8, 25, tzinfo=ET)
        event = ForexNewsEvent(
            "Non-Farm Employment Change",
            "USD",
            datetime(2026, 10, 2, 8, 30, tzinfo=ET),
            forecast="89K",
            previous="162K",
        )

        message = format_red_folder_alert(
            [event],
            now,
            lead_minutes=5,
            trading_plan="Tier 1 only unless my saved fallback appears.",
            risk_plan="1R = 2% of account; Tier 1/2/3 = 60/30/10% of 1R.",
            personal_rule="Never bring regular size into a binary event.",
        )

        self.assertIn("5 Minute Reminder", message)
        self.assertIn("Your trading plan", message)
        self.assertIn("Tier 1 only", message)
        self.assertIn("Your risk plan", message)
        self.assertIn("1R = 2%", message)
        self.assertIn("Your personal Never Again rule", message)
        self.assertIn("Never bring regular size into a binary event", message)
        self.assertIn("News-risk reminder", message)

    def test_never_again_rule_is_not_global(self):
        now = datetime(2026, 10, 2, 8, 25, tzinfo=ET)
        event = ForexNewsEvent(
            "Non-Farm Employment Change",
            "USD",
            datetime(2026, 10, 2, 8, 30, tzinfo=ET),
        )

        message = format_red_folder_alert(
            [event],
            now,
            lead_minutes=5,
            trading_plan="Follow my saved A+ criteria.",
            risk_plan="Follow my saved risk profile.",
        )

        self.assertNotIn("Never bring regular size into a binary event", message)
        self.assertNotIn("Never Again rule", message)
        self.assertIn("stay inside your own predefined setup criteria", message)

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
        self.assertIn("News-risk reminder", message)
        self.assertNotIn("Never bring regular size into a binary event", message)


if __name__ == "__main__":
    unittest.main()
