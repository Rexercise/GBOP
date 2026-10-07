# Soupier Soup terminology record

## Canonical replacement text

The active wording is in `gtop_soupier_soup.txt`, loaded by the shared
`gtop_protocol.CANONICAL_KNOWLEDGE` include list. Its scope is terminology only:
the exact name **Soupier Soup** (S O U P I E R Soup), conceptually a
**soup of a Super Soup**.

## Change record

- Rule ID: `GTOP-TERM-SOUPIER-SOUP`
- Revision: 1
- Status: active
- Type: clarification
- Fields and scope: exact name and conceptual definition only
- Previous wording: no dedicated Soupier Soup term in the accessible baseline
- Accepted wording: “Soupier Soup”; conceptually “soup of a Super Soup”
- Authority: latest explicit user clarification, relayed on 2026-10-07
- Source identifier: original user message ID not provided with the relay
- Chronology: 2026-10-07 is the relay date, not an inferred effective date
- Origin: user-explicit terminology; no inferred trading rules
- Supersedes: no existing named-model rule or classification

## Consistency effects and unresolved items

`recognize_soupier_soup` recognizes the whole exact term, case/whitespace
differences, and the user's explicitly supplied letter-by-letter spelling cue
“S O U P I E R Soup”, returning the canonical spelling. No fuzzy alternate
spellings or unconfirmed aliases are accepted.

`infer_tier` does not infer a tier when that term is present. In particular,
“Soupier Soup (soup of a Super Soup)” must not inherit Tier 1 from the nested
Super Soup wording. A valid explicitly supplied tier remains a supplied value,
not a canonical classification. Recognition itself does not classify a trade.

Qualification, timeframe, confirmation, entry sequence, entry timing, tier,
invalidation and targets are unspecified. No market-evidence detector or new
execution behavior is introduced. Existing Super Soup and other model rules
are preserved. No live data, deployment or external synchronization is implied
by this repository change.
