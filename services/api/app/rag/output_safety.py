"""Small, deterministic output tripwire; not a complete Secret or injection detector."""
import re

from app.core.exceptions import RAGResultError


# Match value-bearing credential assignments, including JSON/code examples.
# Variable references without an assignment remain useful for code explanations.
_CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?i)(?<![\w])(?:[\"']?)(?:[a-z0-9]+[_-])*(?:"
    r"api[_ -]?key|access[_ -]?token|refresh[_ -]?token|auth[_ -]?token|"
    r"client[_ -]?secret|private[_ -]?key|password|passwd|secret[_ -]?key"
    r")(?:[\"']?)\s*[:=]\s*\S"
)
_PRIVATE_KEY = re.compile(r"-----BEGIN (?:[A-Z0-9]+ )?PRIVATE KEY-----")


def validate_rag_answer_safety(answer: str) -> None:
    """Reject the entire response; never echo potentially sensitive model text.

    Limited lexical checks can have false positives and miss other representations.
    Do not treat passing this check as proof of confidentiality or grounding.
    """
    if _CREDENTIAL_ASSIGNMENT.search(answer) or _PRIVATE_KEY.search(answer):
        raise RAGResultError("Code chat generation returned potentially sensitive output.")
