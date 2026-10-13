"""Deterministic presentation cleanup; never changes the stored market evidence."""
import re


def clean_ss_report(answer: str) -> str:
    """Remove the retired prior-close comparison only from an SS report."""
    if not re.search(r'(?im)^\s*(?:#{1,4}\s*)?(?:\d+[.)]\s*)?\*{0,2}SS Report\b', answer):
        return answer
    answer = re.sub(
        r'(?im)^[ \t]*(?:[-*][ \t]+)?\*{0,2}Compared with the prior observed close\*{0,2}[ \t]*:\*{0,2}[^\n]*(?:\n|$)',
        '', answer,
    )
    return re.sub(r'\n{3,}', '\n\n', answer)
