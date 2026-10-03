import copy
import unittest

from gbop_voice_web.photo_recall import recall_cards, result_text, text


def photo(identifier, number=2, **changes):
    value = {
        'id': identifier, 'trade_number': number, 'mime': 'image/png',
        'created_at': '2026-09-30', 'image_base64': 'aW1hZ2U=',
        'asset': 'USTECm', 'play': 'Custom bearish V7', 'entry_model': '',
        'tier': None, 'analysis': 'A chart showing the 9 AM range.',
        'trade_details': {'asset': 'NAS', 'direction': 'Bearish', 'status': 'CLOSED',
                          'play': "Monday's Range", 'objective': '9 AM low'},
        'journal': {'description': 'Exited during 3 PM distribution.',
                    'study_note': 'Three entries managed together.',
                    'rule_adherence': 'unknown', 'result_r': None},
    }
    value.update(changes)
    return value


class RecallCardTests(unittest.TestCase):
    def test_screenshot_regression_one_summary_and_short_photo_cards(self):
        originals = [photo('b'), photo('other', 3), photo('a')]
        before = copy.deepcopy(originals)
        cards = recall_cards(originals)
        self.assertEqual([p['id'] for p, _, _ in cards], ['a', 'b', 'other'])
        first, second = [payload['embeds'][0] for _, _, payload in cards[:2]]
        self.assertEqual(first['title'], 'Trade #2 · NAS · Bearish · CLOSED')
        self.assertEqual(second['title'], 'Trade #2 · Photo 2')
        self.assertIn("**Play:** Monday's Range", first['description'])
        self.assertIn('**Photo tags:** Instrument: USTECm · Play: Custom bearish V7',
                      first['description'])
        self.assertIn('**Final R:** Not recorded', first['description'])
        self.assertNotIn('Journal note', second['description'])
        self.assertNotIn("Monday's Range", second['description'])
        for card in (first, second):
            for unwanted in ('result_r', 'rule_adherence', 'null', 'Tier unknown', ' |  | '):
                self.assertNotIn(unwanted, card.get('description', ''))
        self.assertEqual(originals, before)

    def test_files_stay_with_their_photo_and_pagination_is_explicit(self):
        for p, filename, payload in recall_cards([photo('b'), photo('a')], has_more=True):
            self.assertEqual(filename, 'trade-photo-' + p['id'] + '.png')
            self.assertEqual(payload['embeds'][0]['image']['url'], 'attachment://' + filename)
            self.assertEqual(payload['attachments'], [{'id': 0, 'filename': filename}])
            self.assertEqual(payload['allowed_mentions'], {'parse': []})
        last = recall_cards([photo('a')], has_more=True)[-1][2]['embeds'][0]
        self.assertIn('in this batch', last['footer']['text'])
        self.assertIn('More photos available', last['footer']['text'])

    def test_unlinked_pages_do_not_become_one_trade_or_share_results(self):
        pages = [
            photo('one', None, trade_details={}, journal=None, handwritten_journals=[
                {'description': 'First written trade', 'result_r': 0,
                 'metadata': '{"transcription":"private raw data"}'},
                {'description': 'Second written trade', 'result_r': -1}]),
            photo('two', None, trade_details={}, journal=None),
        ]
        cards = recall_cards(pages)
        first, second = [payload['embeds'][0] for _, _, payload in cards]
        self.assertEqual(first['title'], 'Unlinked journal photo')
        self.assertEqual(second['title'], 'Unlinked journal photo')
        self.assertIn('+0R', first['description'])
        self.assertIn('-1R', first['description'])
        self.assertNotIn('private raw data', first['description'])
        self.assertNotIn('First written trade', second['description'])

    def test_results_are_not_guessed(self):
        for value in (None, '', 'unknown', float('nan'), float('inf'), True):
            self.assertEqual(result_text(value), 'Not recorded')
        self.assertEqual(result_text(0), '+0R')
        self.assertEqual(result_text(-1), '-1R')
        self.assertEqual(result_text(2.5), '+2.5R')
        self.assertIn('unclear', text('[unclear] entry; exit 2R'))
        self.assertEqual(text('{"result_r": null}'), '')

    def test_long_notes_fit_discord_limits_and_mentions_are_disabled(self):
        value = photo('long', asset='@everyone ' * 1000, play='*custom* ' * 1000,
                      analysis='Observed ' * 1000, entry_model='model ' * 1000)
        value['trade_details'] = {k: 'text ' * 1000 for k in value['trade_details']}
        value['journal'] = {k: 'text ' * 1000 for k in value['journal']}
        value['handwritten_journals'] = [
            {'description': 'notes ' * 1000, 'result_r': 1} for _ in range(100)]
        payload = recall_cards([value])[0][2]
        embed = payload['embeds'][0]
        self.assertLessEqual(len(embed['title']), 256)
        self.assertLessEqual(len(embed['description']), 4096)
        self.assertLessEqual(sum(len(embed[k]) for k in ('title', 'description')) +
                             len(embed['footer']['text']), 6000)
        self.assertNotIn('@everyone', embed['description'])
        self.assertIn('97 more entries', embed['description'])
        self.assertEqual(payload['allowed_mentions'], {'parse': []})
