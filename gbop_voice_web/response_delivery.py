"""Conservative evidence that generated market speech finished local playback.

Tool results and generated transcripts alone are not proof of delivery. A voice
item is eligible only after transcript, completed response, and successful audio
drain agree in the same still-current conversation generation. This does not
claim to know whether a person heard or understood their audio device.
"""
from collections import OrderedDict


class MarketResponseDelivery:
    def __init__(self, context, on_delivered=None, limit=64):
        self.context = context
        self.on_delivered = on_delivered
        self.limit = limit
        self.items = OrderedDict()
        self.responses = OrderedDict()

    def _item(self, item_id, response_id=None):
        if not item_id:
            return None
        row = self.items.get(item_id)
        if row is None:
            row = {'generation': self.context.generation, 'response_id': response_id,
                   'text': '', 'played': False, 'rejected': False, 'recorded': False}
            self.items[item_id] = row
            while len(self.items) > self.limit:
                self.items.popitem(last=False)
        elif response_id and row['response_id'] not in (None, response_id):
            row['rejected'] = True
        elif response_id:
            row['response_id'] = response_id
        return row

    def started(self, item_id, response_id=None):
        self._item(item_id, response_id)

    def transcript(self, item_id, text, response_id=None):
        row = self._item(item_id, response_id)
        if row is not None:
            row['text'] = str(text or '').strip()
            self._finish(item_id, row)

    def playback_done(self, item_id, *, completed):
        row = self.items.get(item_id)
        if row is None:
            return
        row['played'] = bool(completed)
        row['rejected'] |= not completed
        self._finish(item_id, row)

    def response_done(self, response):
        response_id = response.get('id')
        if not response_id:
            return
        self.responses[response_id] = response.get('status')
        while len(self.responses) > self.limit:
            self.responses.popitem(last=False)
        for item_id, row in list(self.items.items()):
            if row['response_id'] == response_id:
                if response.get('status') != 'completed':
                    row['rejected'] = True
                self._finish(item_id, row)

    def _finish(self, item_id, row):
        if (row['recorded'] or row['rejected'] or not row['played'] or not row['text']
                or self.responses.get(row['response_id']) != 'completed'
                or not self.context.current(row['generation'])):
            return
        row['recorded'] = True
        marked = self.context.complete_response(row['text'], generation=row['generation'],
            response_id=item_id, completed=True)
        if marked and self.on_delivered is not None:
            self.on_delivered(row['generation'])

    def cancel(self):
        for row in self.items.values():
            if not row['recorded']:
                row['rejected'] = True
