"""Synthetic-only, deterministic narration inference and chronology regression tests."""
from copy import deepcopy
from datetime import datetime, timezone
import unittest

from gbop_voice_web.journal_inference import infer_story_context


class JournalInferenceTests(unittest.TestCase):
    NOW = datetime(2026, 10, 7, 1, 15, tzinfo=timezone.utc)

    def infer(self, text, **kwargs):
        return infer_story_context(text, **kwargs)

    def test_canonical_asset_exact_play_title_and_raw_source(self):
        source = 'Journal my NAS trade today. I entered short with the nine ate eight play.'
        report = self.infer(source, now=self.NOW, timezone_name='America/New_York')
        value = report['values']
        self.assertEqual((value['asset'], value['play'], value['direction']), ('NAS100', '9ate8', 'Bearish'))
        self.assertEqual(value['trade_date'], '2026-10-06')
        self.assertIn('9ate8', value['title'])
        self.assertEqual(report['raw_source'], source)
        self.assertEqual(report['provenance']['asset']['kind'], 'inferred')
        self.assertEqual(report['provenance']['direction']['kind'], 'explicit')
        self.assertEqual(report['provenance']['trade_date']['source'], 'relative_date')

    def test_unknown_zone_and_naive_clock_never_guess_local_today(self):
        for kwargs in ({}, {'now': datetime(2026, 10, 7, 1, 15)},
                       {'now': self.NOW, 'context': {'now_is_server_clock': True}}):
            with self.subTest(kwargs=kwargs):
                report = self.infer('Journal my NAS trade today.', **kwargs)
                self.assertNotIn('trade_date', report['values'])
                self.assertIn('timezone is unknown', ' '.join(report['uncertainties']))

    def test_explicit_aware_clock_can_supply_local_date(self):
        report = self.infer('I traded NAS today.', now=self.NOW)
        self.assertEqual(report['values']['trade_date'], '2026-10-07')

    def test_explicit_member_zone_from_narration_but_not_market_zone(self):
        report = self.infer('My timezone is America/Los_Angeles. I traded NAS today.',
                           now=self.NOW, context={'now_is_server_clock': True})
        self.assertEqual(report['values']['time_zone'], 'America/Los_Angeles')
        self.assertEqual(report['values']['trade_date'], '2026-10-06')
        report = self.infer('New York market review. I traded NAS today.', now=self.NOW,
                           context={'now_is_server_clock': True})
        self.assertNotIn('trade_date', report['values'])
        self.assertNotIn('time_zone', report['values'])

    def test_target_bias_hedge_and_countertrend_are_not_member_direction(self):
        for source in ('NAS downside target is the Young Lefty low.',
                       'NAS was bearish, but I was considering a countertrend hedge.',
                       'I entered NAS targeting the low as part of my hedge.',
                       'I bought NAS to cover my short, then sold my long hedge.',
                       'If I entered short on NAS, I would target the low.',
                       'I never entered short on NAS.', 'I wanted to go short on NAS.'):
            with self.subTest(source=source):
                self.assertNotIn('direction', self.infer(source)['values'])
        report = self.infer('I went long NAS as a hedge against the downside target.')
        self.assertEqual(report['values']['direction'], 'Bullish')

    def test_executed_position_with_relative_target_can_support_direction(self):
        cases = (
            ('I entered NAS to trade down to the Young Lefty low.', 'Bearish'),
            ('I entered to trade up to the high.', 'Bullish'),
            ('I entered NAS targeting a level below my entry.', 'Bearish'),
            ('I opened a position with a target above the entry price.', 'Bullish'),
        )
        for source, expected in cases:
            with self.subTest(source=source):
                report = self.infer(source)
                self.assertEqual(report['values']['direction'], expected)
                self.assertEqual(report['provenance']['direction']['kind'], 'inferred')
                self.assertEqual(report['provenance']['direction']['source'], 'actual_position_target')
                self.assertIn('entry' if 'entry' in source else 'entered', report['provenance']['direction']['evidence'])

    def test_target_inference_rejects_negation_hedges_and_ambiguous_context(self):
        cases = (
            'I entered NAS with the Young Lefty low as target.',
            'NAS target was below the entry price.',
            'If I entered NAS to trade down to the low, I would watch it.',
            'I never entered NAS to trade down to the low.',
            'I entered NAS to trade down to the low as a hedge.',
            'I entered NAS targeting below my entry in a countertrend hedge.',
            'I bought NAS. I entered targeting below my entry.',
            'I entered NAS. My friend aimed below his entry.',
            'I entered NAS and my friend was targeting below the entry.',
            'I entered NAS targeting below my entry or above my entry.',
            'I entered to trade down to the low. I entered to trade up to the high.',
        )
        for source in cases:
            with self.subTest(source=source):
                self.assertNotIn('direction', self.infer(source)['values'])

    def test_actual_multiple_positions_remain_ambiguous(self):
        report = self.infer('I went short NAS. I went long as a hedge.')
        self.assertNotIn('direction', report['values'])
        self.assertIn('multiple actual positions', ' '.join(report['uncertainties']))

    def test_market_context_never_becomes_actual_position(self):
        report = self.infer('Journal the trade.', context={'asset':'NAS100', 'direction':'Bearish'})
        self.assertNotIn('direction', report['values'])
        report = self.infer('Journal the trade.', context={'actual_position':True,'asset':'NAS100','direction':'short'})
        self.assertEqual(report['values']['direction'], 'Bearish')
        self.assertEqual(report['provenance']['direction']['kind'], 'inferred')

    def test_unknown_risk_result_and_historical_dates_never_inferred(self):
        report = self.infer('Journal my NAS trade last Thursday. Took profit at the low, then got stopped out.',
                           now=self.NOW, timezone_name='UTC')
        for field in ('risk_r', 'result_r', 'pnl', 'reported_outcome', 'trade_date'):
            self.assertNotIn(field, report['values'])
        report = self.infer('Journal my NAS trade from 2024-03-08.', now=self.NOW)
        self.assertEqual(report['values']['trade_date'], '2024-03-08')
        self.assertNotIn('trade_date', self.infer('Market review date 2024-03-08.')['values'])

    def test_preserve_approximate_cross_midnight_entry_and_exit_without_exact_time(self):
        source = 'Journal my NAS trade. I entered short about 11:50 PM and exited around 12:20 AM the next day.'
        report = self.infer(source)
        self.assertEqual(report['values']['reported_entry_time_text'], 'about 11:50 PM')
        self.assertEqual(report['values']['reported_exit_time_text'], 'around 12:20 AM the next day')
        self.assertNotIn('reported_entry_at', report['values'])
        self.assertNotIn('reported_exit_at', report['values'])
        self.assertNotIn('trade_date', report['values'])
        self.assertEqual(report['raw_source'], source)

    def test_cross_midnight_relative_entry_date_does_not_use_exit_day(self):
        source = 'I entered around 11:50 PM yesterday and exited about 12:20 AM today.'
        report = self.infer(source, now=self.NOW, timezone_name='UTC')
        self.assertEqual(report['values']['trade_date'], '2026-10-06')
        self.assertEqual(report['values']['reported_entry_time_text'], 'around 11:50 PM yesterday')
        self.assertEqual(report['values']['reported_exit_time_text'], 'about 12:20 AM today')

    def test_candle_label_and_target_touch_never_become_fill_times(self):
        report = self.infer('The NAS 9 AM candle purged. The target hit at 10 AM.')
        self.assertNotIn('reported_entry_time_text', report['values'])
        self.assertNotIn('reported_exit_time_text', report['values'])
        self.assertNotIn('trade_date', report['values'])

    def test_caller_values_not_overwritten_and_provenance_not_mutated(self):
        values = {'asset':'NAS100','direction':'Bullish','play':'9ate8','trade_date':'2024-01-01',
                  'entries':[{'entry_index':1,'risk_r':None}]}
        previous = {'direction': {'kind':'inferred','source':'position_context'}}
        before, old = deepcopy(values), deepcopy(previous)
        report = self.infer('I entered short SPX today.', values=values, context={'provenance':previous})
        self.assertEqual(values, before)
        self.assertEqual(previous, old)
        self.assertEqual(report['values']['direction'], 'Bullish')
        self.assertEqual(report['provenance']['direction'], old['direction'])
        self.assertEqual(report['values']['trade_date'], '2024-01-01')

    def test_multiple_assets_do_not_select_one_and_exact_terminology_preserved(self):
        self.assertNotIn('asset', self.infer('I traded NAS and SPX.')['values'])
        self.assertEqual(self.infer('My 9ate8 trade was NAS100.')['values']['play'], '9ate8')
        self.assertNotIn('play', self.infer('9 at 8; maybe 98.')['values'])
        self.assertEqual(self.infer('NAS, not SPX.')['values']['asset'], 'NAS100')

    def test_entry_model_does_not_compete_with_named_play(self):
        report = self.infer('I traded NAS with the 9ate8 play and a Blessed Thief entry, then Super Soup.')
        self.assertEqual(report['values']['play'], '9ate8')
        self.assertNotIn('play', self.infer('My NAS entry was Super Soup.')['values'])

    def test_journal_logging_and_market_review_dates_are_not_trade_dates(self):
        for text in ('Journal saved on 2026-10-07.',
                     'I want to journal my trade using the chart from 2026-10-07.',
                     'Journal my trade. It was uploaded on 2026-10-07.'):
            with self.subTest(text=text):
                self.assertNotIn('trade_date', self.infer(text)['values'])

    def test_contextual_target_default_is_inferred_and_not_direction(self):
        report = self.infer('Journal my NAS 9ate8 trade.')
        self.assertEqual(report['values']['objective'], 'Opposing liquidity of the 8 range')
        self.assertEqual(report['provenance']['objective']['kind'], 'inferred')
        self.assertEqual(report['provenance']['objective']['source'], 'contextual_target_default')
        self.assertNotIn('direction', report['values'])
        report = self.infer("I'm trading the 9 range on NAS.")
        self.assertEqual(report['values']['objective'], 'Opposing liquidity of the 9 range')
        self.assertNotIn('direction', report['values'])
        self.assertNotIn('objective', self.infer('Review the NAS 9 range.')['values'])

    def test_explicit_target_overrides_contextual_default(self):
        first = self.infer('Journal my NAS 9ate8 trade.')
        changed = self.infer('My target was the midpoint.', values=first['values'],
                             context={'provenance':first['provenance']})
        self.assertEqual(changed['values']['objective'], 'the midpoint')
        self.assertEqual(changed['provenance']['objective']['kind'], 'explicit')
        explicit = self.infer('NAS 9ate8 trade.', values={'objective':'50% of the range'})
        self.assertEqual(explicit['values']['objective'], '50% of the range')

    def test_explicit_entry_targets_are_not_overwritten_or_given_conflicting_default(self):
        entries = [{'entry_index':1, 'objective':'midpoint'}, {'entry_index':2, 'objective':'the 9 AM high'}]
        report = self.infer('Journal my NAS 9ate8 trade.', values={'entries':entries})
        self.assertNotIn('objective', report['values'])
        self.assertEqual(report['values']['entries'], entries)
        prior = self.infer('NAS 9ate8 trade.')
        prior['values']['entries'] = entries
        report = self.infer('Added my reported entry targets.', values=prior['values'],
                            context={'provenance':prior['provenance']})
        self.assertNotIn('objective', report['values'])
        self.assertEqual(report['values']['entries'], entries)

    def test_changed_play_retracts_only_its_own_contextual_default(self):
        previous = self.infer('NAS 9ate8 trade.')
        previous['values']['play'] = 'Young Lefty'
        changed = self.infer(None, values=previous['values'], context={'provenance':previous['provenance']})
        self.assertNotIn('objective', changed['values'])
        previous = self.infer('NAS 9ate8 trade.')
        changed = self.infer('I am trading the 9 range.', values=previous['values'],
                            context={'provenance':previous['provenance']})
        self.assertEqual(changed['values']['objective'], 'Opposing liquidity of the 9 range')
        stable = self.infer(None, values=changed['values'], context={'provenance':changed['provenance']})
        self.assertEqual(stable['values']['objective'], changed['values']['objective'])
        explicit = self.infer('My NAS 9ate8 trade. I entered targeting the midpoint.')
        self.assertEqual(explicit['values']['objective'], 'the midpoint')

    def test_only_generated_title_refreshes_and_supplied_aliases_normalize(self):
        original = self.infer('Journal the trade.')
        report = self.infer('NAS 9ate8.', values=original['values'], context={'provenance':original['provenance']})
        self.assertIn('NAS100', report['values']['title'])
        report = self.infer('NAS.', values={'asset':'NAS','play':'Nine ate eight','title':'My exact title'})
        self.assertEqual(report['values']['asset'], 'NAS100')
        self.assertEqual(report['values']['play'], '9ate8')
        self.assertEqual(report['values']['title'], 'My exact title')


if __name__ == '__main__':
    unittest.main()
