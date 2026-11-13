"""Lossless fallback for explicit narrative assertions, never execution records.

The model still extracts complex stories. These narrow fallbacks prevent clear
member titles and confirmed entry narration from disappearing when it omits keys.
"""
from copy import deepcopy
import re


def explicit_title(raw):
    source = raw or ''
    patterns = (r'(?:^|[.;!?\n]\s*)Title:\s*([^.;!?\n]{1,160})(?=[.;!?\n]|$)',
                r'\b(?:title|name)\s+(?:this|that|it|the journal)\s+["“]?([^.;!?\n]{1,160})(?=[.;!?\n]|$)',
                r'\b(?:change|set|update)\s+(?:the\s+)?title\s+to\s+["“]?([^.;!?\n]{1,160})(?=[.;!?\n]|$)')
    found = []
    for pattern in patterns:
        for match in re.finditer(pattern, source, re.I):
            prefix = '' if match[0][:1] in '.;!?\n' else re.split(r'[.;!?\n]',source[:match.start()])[-1]
            if re.search(r"\b(?:don't|do not|not|never|maybe|could|should|might|would|if)\b", prefix, re.I):
                continue
            value = match[1].strip(' \t"“”')
            if not re.search(r'\b(?:maybe|unknown|unsure|or)\b', value, re.I):
                found.append((match.start(), value))
    return max(found)[1] if found else None



def retain_reported_facts(raw, values, draft, before, prior_provenance):
    """Run after extraction; current explicit wording wins over stale model keys."""
    source = raw or ''
    proof = prior_provenance.get('title') or {}
    title = explicit_title(source)
    if title:
        values['title'] = title
        draft['provenance']['title'] = {'kind':'explicit', 'source':'member_title', 'evidence':title}
        draft['suppressed_fields'] = [k for k in draft['suppressed_fields'] if k != 'title']
    elif before.get('title') and proof.get('source') == 'member_title' and 'title' not in draft['suppressed_fields']:
        values['title'] = before['title']
        draft['provenance']['title'] = deepcopy(proof)
    # One reported entry is not a priced/risked execution. Never add another entry
    # from a repeated assertion or resolve a hypothetical/rejected entry model.
    confirmed = re.search(r'(?:^|[.;!?\n]\s*)Entry confirmed:\s*there was an entry\s*[.!]?(?:\s|$)', source, re.I)
    if confirmed and not values.get('entries') and not before.get('entries'):
        values['entries'] = [{'entry_index':1, 'notes':source}]
        draft['provenance']['entries'] = {'kind':'explicit', 'source':'member_entry_confirmation', 'evidence':confirmed[0].strip()}
    # Preserve labeled lessons as reported context if extraction left it empty.
    # This is not independent verification of any setup, price action or result.
    lessons = re.search(r'(?:^|[.;!?\n]\s*)Lessons:\s*([^\n]+)', source, re.I)
    if lessons and not values.get('context_notes') and 'context_notes' not in draft['suppressed_fields']:
        values['context_notes'] = lessons[1].strip()
        draft['provenance']['context_notes'] = {'kind':'explicit', 'source':'member_reported_lessons', 'evidence':lessons[0].strip()}
