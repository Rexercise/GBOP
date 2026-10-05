"""Conservative classification of explicitly reported plan adherence.

Only complete normalized labels or statements count. Unknown, missing,
conflicting, hypothetical, and unsupported prose remain unknown; finding a
substring such as "no" in "unknown" is never evidence of a violation. Source
notes stay verbatim in their original records.
"""
import re


_LABELS = {
    "followed": {
        "yes", "y", "true", "full", "clean", "followed", "adhered",
        "full adherence", "fully followed", "fully adhered", "followed plan",
    },
    "partial": {
        "partial", "partially", "partial adherence", "partial deviation",
        "deviation", "deviated", "partially followed", "followed partially",
        "partially adhered",
    },
    "violated": {
        "no", "n", "false", "off plan", "violated", "violation", "violations",
        "broke", "broken", "not followed", "not adhered", "nonadherent",
        "nonadherence",
    },
}
_TARGET = r"(?:my |the )?(?:plan|rules?)"
_STATEMENTS = {
    "followed": (
        rf"(?:i )?(?:fully )?(?:followed|adhered to|stuck to|respected) {_TARGET}",
        rf"{_TARGET}(?: was| were)? (?:fully )?followed",
    ),
    "partial": (
        rf"(?:i )?(?:partially|mostly) (?:followed|adhered to|respected) {_TARGET}",
        rf"(?:i )?deviated from {_TARGET}",
        rf"{_TARGET}(?: was| were)? partially followed",
    ),
    "violated": (
        rf"(?:i )?(?:did not|didn't|didnt|never) (?:follow|adhere to|respect|stick to) {_TARGET}",
        rf"(?:i )?(?:broke|violated|ignored) {_TARGET}",
        rf"{_TARGET}(?: was| were)? (?:not followed|violated|broken)",
        rf"(?:not followed|did not follow) {_TARGET}",
    ),
}


def adherence_bucket(value):
    """Return followed/partial/violated, or unknown without inferred sentiment."""
    if not isinstance(value, str):
        return "unknown"
    text = re.sub(r"\s+", " ", value.casefold().replace("’", "'")
                  .replace("_", " ").replace("-", " ")).strip().rstrip(".!").strip()
    for bucket, labels in _LABELS.items():
        if text in labels or any(re.fullmatch(pattern, text) for pattern in _STATEMENTS[bucket]):
            return bucket
    return "unknown"
