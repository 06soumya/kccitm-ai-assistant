"""
Query normalizer for KCCITM AI Assistant.

Preprocesses user queries BEFORE they hit the router/pipeline:
  1. Hinglish/Hindi keyword expansion → English equivalents
  2. Typo correction (exact map + conservative fuzzy)
  3. Abbreviation expansion (safe terms only)
  4. Whitespace/punctuation cleanup

Safety rules:
  - NEVER mangle student names (unknown words are left alone)
  - NEVER touch words that are valid English ("pass", "fail", "me", "it", etc.)
  - Fuzzy correction only at distance 1 and only for words >= 5 chars
  - Abbreviation expansion only for unambiguous technical terms

All transforms are local (no LLM call, no network), <1ms per query.

Usage:
    from core.query_normalizer import normalize_query
    clean = normalize_query("kitne bacche paas hue batch 2021 mein")
    # → "how many students passed batch 2021 in"
"""

import logging
import re

logger = logging.getLogger(__name__)


# ── 1. Hinglish / Hindi keyword map ─────────────────────────────────────────
# ONLY Hindi words that have NO English meaning. Common English words like
# "pass", "fail", "grade", "number", "college", "branch" are excluded —
# they already work fine in the English pipeline.

_HINGLISH_MAP: dict[str, str] = {
    # Question words (purely Hindi, no English collision)
    "kitne": "how many",
    "kitna": "how much",
    "kaun": "who",
    "kaunsa": "which",
    "kya": "what",
    "kaise": "how",
    "kyun": "why",
    "kab": "when",
    "kahan": "where",
    "konsa": "which",
    "kon": "who",
    # Student/academic (purely Hindi)
    "bacche": "students",
    "baccha": "student",
    "bacha": "student",
    "bachhe": "students",
    "ladka": "male student",
    "ladki": "female student",
    "ladke": "male students",
    "ladkiyan": "female students",
    "ladkiyo": "female students",
    "chhatra": "student",
    "vidyarthi": "student",
    "topar": "topper",
    # Results (ONLY Hindi variants — "pass"/"fail" are valid English, leave them)
    "paas": "passed",
    "pas": "passed",
    "phail": "failed",
    "fel": "failed",
    "girne": "failed",
    "gir gaye": "failed",
    "nikle": "passed",
    "nikal gaye": "passed",
    "hue": "",
    "hua": "",
    "hui": "",
    # Subjects (purely Hindi)
    "ganit": "mathematics",
    "bhautiki": "physics",
    "rasayan": "chemistry",
    "angrezi": "english",
    # Metrics (ONLY Hindi — "grade", "number" are valid English)
    "nambr": "marks",
    "nambar": "marks",
    "ank": "marks",
    "pratishat": "percentage",
    "ausat": "average",
    # Comparison/superlative (purely Hindi)
    "sabse accha": "best",
    "sabse acha": "best",
    "sabse bura": "worst",
    "sabse kam": "lowest",
    "sabse zyada": "highest",
    "sabse jyada": "highest",
    "sabse": "most",
    "zyada": "more",
    "jyada": "more",
    "kam": "less",
    "accha": "good",
    "acha": "good",
    "bura": "bad",
    "behtareen": "best",
    "behtarin": "best",
    # Connectors (ONLY unambiguous Hindi — "me", "the", "do" are English, excluded)
    "ka": "of",
    "ke": "of",
    "ki": "of",
    "mein": "in",
    "mai": "in",
    "aur": "and",
    "ya": "or",
    "wale": "",
    "wali": "",
    "wala": "",
    "hai": "",
    "hain": "",
    # Batch/semester
    "saal": "year",
    "sal": "year",
    # Actions (purely Hindi)
    "dikhao": "show",
    "dikha": "show",
    "batao": "tell",
    "bata": "tell",
    "dedo": "give",
    "chahiye": "need",
    # Domain
    "vibhag": "department",
}

# ── 2. Common typo map (exact replacements, no fuzzy needed) ─────────────────
# Only genuine misspellings → correct form. NO valid English words.

_TYPO_MAP: dict[str, str] = {
    # SGPA/CGPA typos
    "sgap": "sgpa",
    "spga": "sgpa",
    "cgap": "cgpa",
    "cpga": "cgpa",
    # Student
    "studnts": "students",
    "studnet": "student",
    "studens": "students",
    "stduent": "student",
    "stdent": "student",
    "stuent": "student",
    "studetn": "student",
    "studnets": "students",
    # Topper
    "toper": "topper",
    "topers": "toppers",
    "tpper": "topper",
    "toppr": "topper",
    "topprs": "toppers",
    "toppar": "topper",
    # Semester
    "semster": "semester",
    "semestr": "semester",
    "semeter": "semester",
    "semeser": "semester",
    "semstr": "semester",
    "semestar": "semester",
    # Branch
    "brach": "branch",
    "brnch": "branch",
    "branc": "branch",
    # Marks/results
    "makrs": "marks",
    "mraks": "marks",
    "marsk": "marks",
    "rsult": "result",
    "reslt": "result",
    "reuslt": "result",
    "reslut": "result",
    "resuts": "results",
    "resulst": "results",
    # Average
    "averge": "average",
    "avrage": "average",
    "avrge": "average",
    "avarage": "average",
    "averag": "average",
    # Percentage
    "percntage": "percentage",
    "percentge": "percentage",
    "percetage": "percentage",
    "precentage": "percentage",
    # Pass/fail
    "passd": "passed",
    "faild": "failed",
    "faied": "failed",
    "faileed": "failed",
    # Subject
    "subjct": "subject",
    "suject": "subject",
    "subect": "subject",
    # Compare
    "compre": "compare",
    "comapre": "compare",
    "compair": "compare",
    # Highest/lowest
    "hghest": "highest",
    "highst": "highest",
    "higest": "highest",
    "lowst": "lowest",
    "loweset": "lowest",
    # Back paper
    "bakpaper": "back paper",
    "backpaper": "back paper",
    # Ranking
    "rnking": "ranking",
    "rankng": "ranking",
    "ranklist": "rank list",
    # Gender
    "femal": "female",
    "femle": "female",
    # Batch
    "btach": "batch",
    "bacth": "batch",
    "btech": "b.tech",
    "batchwise": "batch wise",
    "semesterwise": "semester wise",
    "subjectwise": "subject wise",
    "branchwise": "branch wise",
    "genderwise": "gender wise",
}

# ── 3. Abbreviation/slang expansion ─────────────────────────────────────────
# ONLY unambiguous technical terms. NO common English words.
# Excluded: "me" (→ mech eng), "se" (→ software eng), "it" (→ info tech),
#           "do" (→ give), "ai" (→ art. intel), "cd" (→ compiler),
#           "cs" (→ could be "cs" in sentence), "ce" (→ civil eng),
#           "ee" (→ electrical eng), "eg" (→ "e.g.")
# These 2-letter abbreviations collide with common English words.

_ABBREV_MAP: dict[str, str] = {
    # Branch abbreviations (3+ chars or unambiguous)
    "cse": "computer science and engineering",
    "ece": "electronics and communication engineering",
    "mech": "mechanical engineering",
    # Subject abbreviations (3+ chars, unambiguous)
    "dbms": "database management system",
    "dsa": "data structures",
    "coa": "computer architecture",
    "oop": "object oriented programming",
    "oops": "object oriented programming",
    "pps": "programming for problem solving",
    "daa": "design and analysis of algorithms",
    "toc": "theory of computation",
    "evs": "environment",
    "bee": "basic electrical engineering",
    # Slang (3+ chars, unambiguous)
    "sem": "semester",
    "avg": "average",
    "pct": "percentage",
    "dept": "department",
    "subj": "subject",
}


# ── 4. Domain vocabulary for fuzzy correction ────────────────────────────────
# Words the user is likely trying to type. Used ONLY for fuzzy matching.

_DOMAIN_VOCAB: set[str] = {
    "students", "student", "topper", "toppers", "batch", "semester",
    "branch", "marks", "grade", "average", "percentage", "count",
    "total", "highest", "lowest", "passed", "failed", "result",
    "results", "ranking", "compare", "subject", "internal", "external",
    "backlog", "computer", "science", "electronics", "communication",
    "mechanical", "mathematics", "physics", "chemistry", "programming",
    "database", "operating", "network", "algorithm", "compiler",
    "automata", "software", "engineering", "graphics", "electrical",
    "artificial", "intelligence", "machine", "learning", "gender",
}

# Words that must NEVER be fuzzy-corrected (common English words that
# happen to be close to domain vocab by edit distance).
_FUZZY_PROTECTED: set[str] = {
    "full", "fill", "fall", "fell", "tell", "tall", "call", "ball",
    "best", "test", "rest", "west", "nest", "past", "fast", "last",
    "come", "some", "home", "name", "same", "game", "made", "take",
    "like", "time", "give", "live", "have", "make", "rate", "late",
    "date", "gate", "each", "much", "such", "when", "then", "than",
    "them", "they", "this", "that", "what", "with", "from", "will",
    "well", "were", "here", "more", "also", "only", "very", "just",
    "most", "many", "some", "your", "show", "list", "find",
    "about", "their", "which", "would", "could", "should",
    "after", "before", "where", "while", "other", "under",
    "never", "every", "still", "first", "above", "below",
}


# ── 5. Levenshtein distance for fuzzy matching ───────────────────────────────

def _levenshtein(s1: str, s2: str) -> int:
    """Compute Levenshtein edit distance between two strings."""
    if len(s1) < len(s2):
        return _levenshtein(s2, s1)
    if len(s2) == 0:
        return len(s1)

    prev_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        curr_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = prev_row[j + 1] + 1
            deletions = curr_row[j] + 1
            substitutions = prev_row[j] + (c1 != c2)
            curr_row.append(min(insertions, deletions, substitutions))
        prev_row = curr_row
    return prev_row[-1]


def _fuzzy_correct(word: str) -> str:
    """
    If `word` is close to a domain vocabulary term, return the correction.

    Conservative rules to avoid mangling names:
      - Only corrects words >= 5 chars (short words have too many collisions)
      - Max edit distance = 1 (not 2 — too aggressive)
      - Protected common English words are never corrected
      - Words already in vocab are left alone
      - Numbers, roll numbers, subject codes are left alone

    Returns the original word if no close match found.
    """
    if len(word) < 5:
        return word

    # Don't correct words that are already in vocab or protected
    if word in _DOMAIN_VOCAB or word in _FUZZY_PROTECTED:
        return word

    # Don't correct numbers, roll numbers, subject codes
    if word.isdigit() or re.match(r'^[a-z]{2,4}\d{3,4}[a-z]?$', word):
        return word

    best_match = word
    best_dist = 2  # only accept distance = 1

    for vocab_word in _DOMAIN_VOCAB:
        if abs(len(word) - len(vocab_word)) > 1:
            continue
        dist = _levenshtein(word, vocab_word)
        if dist < best_dist:
            best_dist = dist
            best_match = vocab_word

    if best_match != word:
        logger.debug("Fuzzy corrected: '%s' → '%s' (distance=%d)", word, best_match, best_dist)

    return best_match


# ── Main normalizer ──────────────────────────────────────────────────────────

def normalize_query(query: str) -> str:
    """
    Normalize a user query for better downstream understanding.

    Pipeline:
      1. Whitespace + punctuation cleanup
      2. Hinglish word replacement (multi-word phrases first, then single words)
      3. Exact typo correction
      4. Fuzzy typo correction (conservative: distance 1, length >= 5)
      5. Abbreviation expansion (only safe standalone terms)
      6. Final cleanup

    Safety: unknown words (like student names) pass through untouched.
    Returns the normalized query string.
    """
    original = query.strip()
    if not original:
        return original

    # Step 1: Basic cleanup — collapse whitespace, normalize quotes
    text = original.lower()
    text = re.sub(r'[""''`]', "'", text)
    text = re.sub(r'\s+', ' ', text).strip()

    # Step 2: Hinglish — replace multi-word phrases first, then single words
    # Sort by key length descending so "sabse accha" matches before "sabse"
    for hindi, english in sorted(_HINGLISH_MAP.items(), key=lambda x: -len(x[0])):
        pattern = r'\b' + re.escape(hindi) + r'\b'
        text = re.sub(pattern, english, text)

    # Step 3: Exact typo correction (word-level)
    words = text.split()
    corrected_words = []
    for word in words:
        clean_word = word.strip(".,!?;:'\"")
        if clean_word in _TYPO_MAP:
            corrected_words.append(_TYPO_MAP[clean_word])
            logger.debug("Typo fixed: '%s' → '%s'", clean_word, _TYPO_MAP[clean_word])
        else:
            corrected_words.append(word)
    text = ' '.join(corrected_words)

    # Step 4: Fuzzy correction (conservative — distance 1, length >= 5)
    words = text.split()
    fuzzy_words = []
    for word in words:
        clean_word = word.strip(".,!?;:'\"")
        if len(clean_word) >= 5 and not clean_word.isdigit():
            corrected = _fuzzy_correct(clean_word)
            fuzzy_words.append(corrected if corrected != clean_word else word)
        else:
            fuzzy_words.append(word)
    text = ' '.join(fuzzy_words)

    # Step 5: Abbreviation expansion (only safe standalone terms)
    words = text.split()
    expanded_words = []
    for word in words:
        clean_word = word.strip(".,!?;:'\"")
        if clean_word in _ABBREV_MAP:
            expanded_words.append(_ABBREV_MAP[clean_word])
            logger.debug("Abbrev expanded: '%s' → '%s'", clean_word, _ABBREV_MAP[clean_word])
        else:
            expanded_words.append(word)
    text = ' '.join(expanded_words)

    # Step 6: Final cleanup — collapse whitespace from empty Hinglish replacements
    text = re.sub(r'\s+', ' ', text).strip()

    if text != original.lower():
        logger.info("Query normalized: '%s' → '%s'", original, text)

    return text
