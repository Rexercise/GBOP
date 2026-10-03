"""Shared community risk classification; independent of Discord/web runtime."""
from pathlib import Path
import re

TIER_LIMITS = {1: 0.60, 2: 0.30, 3: 0.10}
KNOWLEDGE_FILES = ("gtop_knowledge.txt", "gtop_boneless.txt", "gtop_lifecycle.txt", "gtop_super_soup.txt")
CANONICAL_KNOWLEDGE = "\n\n".join(
    Path(__file__).with_name(name).read_text(encoding="utf-8").strip()
    for name in KNOWLEDGE_FILES
)


def tier_max_r(tier):
    return TIER_LIMITS.get(tier)


def infer_tier(entry_model, supplied=None):
    name = re.sub(r"[_-]+", " ", (entry_model or "").strip().lower())
    # Specific late-stage KOD wins over embedded body/super-soup wording.
    if re.search(r"\bkod\b", name):
        return 2
    if "super soup" in name:
        if "wick" in name:
            return supplied if supplied in TIER_LIMITS else None
        return 1
    if any(x in name for x in ("blessed thief", "breaker", "88.7")) or re.search(r"\bote\b", name):
        return 3
    if any(x in name for x in ("model 1", "turtle body", "body soup", "turtle wick", "wick soup")) or re.search(r"\bc(?:i)?sd\b", name):
        return 2
    return supplied if supplied in TIER_LIMITS else None


def classification_warning(entry_model, supplied):
    expected = infer_tier(entry_model)
    if expected is not None and expected != supplied:
        return f"{entry_model} is classified as Tier {expected}; Tier {supplied} was recorded."
    return None


def tier_used_r(db, thesis_id, tier):
    with db() as conn:
        rows = conn.execute(
            "SELECT entry_model, tier, risk_r FROM thesis_executions WHERE thesis_id = ?",
            (thesis_id,),
        ).fetchall()
    # Canonical model classification prevents a manually mislabelled entry
    # from freeing budget; historical journal rows remain untouched.
    return sum(float(row[2] or 0) for row in rows if infer_tier(row[0], row[1]) == tier)
