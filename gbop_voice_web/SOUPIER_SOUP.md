# Soupieror Soup concept and journaling record

## Canonical replacement text

The active wording is in `gtop_soupier_soup.txt`, loaded by the shared
`gtop_protocol.CANONICAL_KNOWLEDGE` include list. The exact canonical name is
**Soupieror Soup** (S O U P I E R O R Soup), formerly **Soupier Soup**. It is a soup of the Super Soup, treating
the Super Soup candle as its own CRT for the next refined entry. The resulting
Soupieror Soup candle may itself become the CRT for another nested setup. Each
layer stays linked to its immediate parent candle/CRT and original parent
play/range.

Member-reported entries use the existing narrative journal fields: parent
`play`, `entry_model`, `candle_label`, `context_notes` and entry `notes`.
Stable `entry_index` values track reported executions, rather than counting
every described nested setup as another fill. Literal narration is retained,
including unconfirmed spoken terms, while unknown risk, tier and result R remain
unknown. Capture and save proceed before optional naming clarification.

## Change record

- Rule ID: `GTOP-TERM-SOUPIER-SOUP`
- Revision: 1
- Status: superseded for the concept-only limitation by revision 2 below
- Type: clarification
- Fields and scope: exact name and conceptual definition only
- Previous wording: no dedicated Soupier Soup term in the accessible baseline
- Accepted wording: “Soupier Soup”; conceptually “soup of a Super Soup”
- Authority: latest explicit user clarification, relayed on 2026-10-07
- Source identifier: original user message ID not provided with the relay
- Chronology: 2026-10-07 is the relay date, not an inferred effective date
- Origin: user-explicit terminology; no inferred trading rules
- Supersedes: no existing named-model rule or classification

### Revision 2

- Rule ID: `GTOP-TERM-SOUPIER-SOUP`
- Revision: 2
- Status: active for recursive concept and journaling; spelling superseded by revision 3
- Type: clarification of the recursive concept and its journal representation
- Fields and scope: candle-as-CRT recursion, retained parent lineage and
  member-reported narrative entries; exact canonical spelling is retained
- Previous wording: conceptually “soup of a Super Soup”; entry sequence and all
  further details unspecified; no entry-model classification inferred
- Accepted wording: “a soup of the Super Soup, treating the Super Soup candle
  as its own CRT for the next refined entry”; the resulting Soupier candle may
  itself become a CRT for another nested setup, each layer tied to its parent
- Authority and chronology: explicit owner clarification accepted on 2026-10-09,
  including further nested recursion and support for smooth member journaling
- Source: direct owner voice clarification on 2026-10-09, approximately
  00:35–00:40 UTC. The accepted definition, further recursion and original-parent
  linkage were explicitly confirmed in that conversation. Private call/thread
  identifiers and raw transcript excerpts are omitted from this repository.
  Individual transcript message IDs were not supplied.
- Naming evidence: this call's “Superior Soup” is an audio transcript rendering,
  not an explicit rename; revision 1's spelling was retained at this stage.
  The later explicit spelling correction is recorded in revision 3.
- Chronology: 2026-10-09 is the clarification date, not an inferred effective date
- Origin: user-explicit recursive concept and smooth-journaling requirement;
  mapping lineage into existing journal fields is an implementation choice
- Supersedes: revision 1's concept-only limitation within this term; preserves
  its spelling, unspecified risk/tier and market-qualification boundaries

### Revision 3

- Rule ID: `GTOP-TERM-SOUPIER-SOUP`
- Revision: 3
- Status: active
- Type: correction of the exact canonical spelling
- Previous wording: “Soupier Soup” → accepted wording: **Soupieror Soup**
- Authority and chronology: explicit owner spelling correction on 2026-10-09
  at approximately 02:21 UTC, later than revision 2's clarification.
- Source: direct owner correction. Private conversation identifiers and raw
  transcript excerpts are omitted from this public repository.
- Supersedes: earlier canonical spelling only. The recursive CRT definition,
  parent lineage, unspecified tier/risk and journal behavior remain unchanged.
- Former-name lookup: Soupier Soup maps to Soupieror Soup. Stored member
  narration and historical entry names are never rewritten by this lookup.
- Spoken wording: Superior Soup may refer to Soupieror Soup only when the
  conversation clearly identifies this recursive concept. The stateless
  recognizer does not accept Superior Soup as an unconditional alias.
- Compatibility: existing filenames, public helper/constant names and stable
  rule ID remain unchanged; only the helper's recognized value is corrected.

## Consistency effects and unresolved items

`recognize_soupier_soup` recognizes Soupieror Soup and the former Soupier Soup
name, case/whitespace differences and their letter-by-letter spellings, returning
Soupieror Soup. It is a lookup, not a rewrite of existing journal text. Fuzzy
alternate spellings and unconditional Superior Soup recognition are excluded.

`infer_tier` does not infer a tier when either the canonical or former name is
present. In particular, “Soupieror Soup (soup of a Super Soup)” and its former-name
equivalent must not inherit Tier 1 from the embedded Super Soup wording. A valid
explicitly supplied tier remains a supplied value, not a canonical classification.
Recognition itself does not classify a trade. Ambiguous spoken wording stays
literal unless clear conversation context resolves it; historical wording is
retained even after such clarification.

The recursive relationship is defined; numerical thresholds, timeframe mapping,
confirmation, entry timing, tier, invalidation and targets remain unspecified.
Market qualification and member-reported identity stay separate. A purge alone
does not establish a completed soup. No market-evidence detector is introduced.

The shared include reaches Discord text, the shared realtime-session class used
by both Discord voice identities, the browser backend and browser live voice.
Existing narrative journal storage already supports these reported entry names
and lineage notes without a schema change. Compact active wording retains the
existing voice-prompt length budget; shared journaling instructions still govern
staging and explicit finalization. Regression tests use synthetic local
storage and prompt assembly; they do not establish live model response quality.

The `/execution log`, `/trade` and `/entry` dropdowns still require a Tier 1/2/3
selection and immediately apply that tier's risk budget. Adding only a Soupieror
Soup label would continue to require an otherwise unspecified tier. These
dropdowns and execution-risk behavior are deliberately unchanged in this bounded
candidate; an unknown-tier execution flow needs separate design and scope.

Existing Super Soup and other model rules are preserved. No live data,
deployment or external synchronization is implied by this repository change.
