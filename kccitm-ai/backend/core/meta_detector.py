"""
Detects dataset-meta questions — questions ABOUT THE DATASET ITSELF
("what kind of data do you have", "describe the database", "kya kya data hai")
as opposed to queries against the data ("top 5 students", "tell me about
roll number X", "average SGPA in CSE").

When a dataset-meta question is detected, the orchestrator short-circuits
to a templated answer built from the cached dataset_context — sub-50ms
instead of going through router → RAG (which can't answer dataset-level
questions because the index contains per-student chunks, not summaries).

Conservative matching is deliberate: false negatives are cheap (query
continues through the normal pipeline), false positives are expensive
(user gets a generic overview when they wanted a specific answer).
"""
from __future__ import annotations

import re

# Patterns that explicitly ask about the dataset / database / system itself.
# At least one must match for the query to be classified as dataset-meta.
_META_PATTERNS = [
    # English — "what kind of data..."
    r"\bwhat\s+(kind|kinds|sort|sorts|type|types)\s+of\s+(data|information|records|info)\b",
    r"\bwhat\s+data\s+(do\s+you\s+(have|store|contain|hold|keep)|is\s+(stored|available|kept)|exists?)\b",
    r"\bwhat['’]?s\s+(in|inside|stored\s+in)\s+(this|the|your)\s+(database|dataset|data|system|db)\b",

    # English — "describe / summary / overview"
    r"\b(describe|summari[sz]e)\s+(the|your|this)\s+(database|dataset|data|records?)\b",
    r"\b(give|provide|show)\s+(me\s+)?(an?\s+)?(overview|summary)\s+of\s+(the|your|this)\s+(data|database|dataset)\b",
    r"\btell\s+me\s+about\s+(the|your|this)\s+(database|dataset|data|records?)\b",

    # English — "what fields / columns / structure"
    r"\bwhat\s+(fields?|columns?|attributes?|tables?)\s+(do\s+you|are\s+(there|stored|available)|exist)\b",
    r"\bwhat\s+(do\s+you|can\s+you)\s+know\s+about\s+(the|your|this)\s+(data|database|dataset|records?)\b",

    # English — capabilities-as-data
    r"\bwhat\s+(can|kind\s+of|sort\s+of|type\s+of)\s+(you\s+)?(questions|queries|things)\s+can\s+(i|we)\s+(ask|query)\b",

    # Hinglish
    r"\bkya\s+kya\s+data\b",
    r"\bkaisa\s+data\b",
    r"\bkya\s+data\s+(hai|h)\b",
    r"\bdata\s+me(\s+)?n?\s+kya\s+(kya|hai|h)\b",
    r"\b(database|system)\s+me(\s+)?n?\s+kya\s+(hai|h|kya)\b",
    r"\bkis\s+(tarah|prakar)\s+ka\s+data\b",
]

# If ANY of these appear, the query is NOT dataset-meta even if a META
# pattern also matched — it's a specific query.
_NOT_META_PATTERNS = [
    r"\b\d{13}\b",                              # roll number
    r"\b[A-Z]{2,4}\d{3,4}[A-Z]?\b",             # subject code (e.g. KCS503)

    # concept questions — handled by existing _is_meta_question / OpenAI path
    r"\bwhat\s+is\s+(an?\s+)?(sgpa|cgpa|grade|carry\s+paper|cp|backlog|grading)\b",

    # SQL aggregations / rankings
    r"\btop\s+\d+\b",
    r"\bbottom\s+\d+\b",
    r"\bhow\s+many\b",
    r"\bcount\s+(of\s+|the\s+)?(students?|records?)\b",
    r"\baverage\s+(sgpa|marks|grades?|score)\b",
    r"\bpass\s+rate\b",
    r"\bfail\s+rate\b",

    # explicit student / branch / semester filters
    r"\b(semester|sem)\s+\d+\b",
    r"\bbatch\s+\d{4}\b",
]

# Hard cap on query length we're willing to scan — meta-questions are short
_MAX_LEN = 300


def is_dataset_meta_question(query: str) -> bool:
    """
    Return True iff `query` is asking about the dataset itself.

    Two-stage filter:
      1. At least one META pattern must match.
      2. No NOT-META pattern may match.

    Designed to be conservative — biased toward false negatives.
    """
    if not query:
        return False
    q = query.lower().strip()
    if not q or len(q) > _MAX_LEN:
        return False
    if not any(re.search(p, q) for p in _META_PATTERNS):
        return False
    if any(re.search(p, q) for p in _NOT_META_PATTERNS):
        return False
    return True
