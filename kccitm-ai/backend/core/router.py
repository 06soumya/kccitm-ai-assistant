"""
Query classification and routing for KCCITM AI Assistant.

The QueryRouter takes a natural language query and classifies it into one of
three pipelines — SQL, RAG, or HYBRID — while also extracting structured
metadata (filters, entities, intent).

Usage:
    from core.llm_client import OllamaClient
    from core.router import QueryRouter

    llm = OllamaClient()
    router = QueryRouter(llm)
    result = await router.route("top 5 students by SGPA in semester 4")
    # RouteResult(route='SQL', filters={'semester': 4}, ...)
"""

import json
import logging
import re
from dataclasses import dataclass, field

from core.llm_client import OllamaClient

logger = logging.getLogger(__name__)

# ── Branch abbreviation map ───────────────────────────────────────────────────

_BRANCH_MAP: dict[str, str] = {
    "CSE":  "COMPUTER SCIENCE AND ENGINEERING",
    "ECE":  "ELECTRONICS AND COMMUNICATION ENGINEERING",
    "ME":   "MECHANICAL ENGINEERING",
    "CE":   "CIVIL ENGINEERING",
    "EE":   "ELECTRICAL ENGINEERING",
    "IT":   "INFORMATION TECHNOLOGY",
    "CS":   "COMPUTER SCIENCE AND ENGINEERING",
    "MECH": "MECHANICAL ENGINEERING",
    "CIVIL": "CIVIL ENGINEERING",
    "ELECTRICAL": "ELECTRICAL ENGINEERING",
}

# ── Router system prompt (v1 — stored in prompts.db for Phase 9 evolution) ───

ROUTER_SYSTEM_PROMPT = """Classify this query about student academic data.

ROUTES:
- SQL: Answer is a number, count, average, ranking, or data list
- RAG: Answer is a description, explanation, profile, or analysis
- HYBRID: Answer needs both numbers and explanation

FOLLOW-UP HANDLING:
If the query is short and references previous context (like "what about semester 4?", "and the bottom 5?", "show me their results"):
- Look at the chat history to understand what was asked before
- Apply the SAME intent to the new parameters
- Example: Previous was "top 5 students in semester 1" then "what about semester 4" means "top 5 students in semester 4"
- KEEP the same route as the previous query

DECISION RULES:
"how many", "count", "total" -> SQL
"top N", "bottom N", "rank", "list" -> SQL
"average", "mean", "sum" -> SQL
"compare X and Y" (numbers) -> SQL
"tell me about [student name]" -> SQL (gets all semester results for that student)
"describe", "explain" -> RAG
"who is struggling", "students at risk" -> RAG
"analyze", "why did", "recommend" -> HYBRID
"KCS503 results" -> SQL (gets subject_marks data)
Subject code alone -> SQL (gets marks, grades, back_paper)
Student name alone -> SQL (gets all semester results for that student)
"tell me about [name]" -> SQL (NOT RAG — we need structured data from the database)
"highest scoring subject" -> SQL (aggregation query)
"best performing subject" -> SQL (aggregation query)
"average marks in KCS503" -> SQL (aggregation query)
"subject wise ranking" -> SQL (aggregation query)
"worst performing subject" -> SQL (aggregation query)

FORCED SQL RULE: Any query with these words is ALWAYS SQL: count, how many, total, average, mean, sum, top, bottom, highest, lowest, best, worst, ranking, rank, list, compare, percentage, pass rate, fail rate, grade distribution, subject wise, semester wise, batch wise, gender wise, branch wise. No exceptions.

ABBREVIATION MAP:
CSE = "COMPUTER SCIENCE AND ENGINEERING"
ECE = "ELECTRONICS AND COMMUNICATION ENGINEERING"
ME = "MECHANICAL ENGINEERING"
sem = semester
avg = average

Respond ONLY with JSON:
{"route":"SQL","needs_filter":true,"filters":{"semester":4,"branch":"COMPUTER SCIENCE AND ENGINEERING"},"entities":["semester 4"],"intent":"top 5 ranking","confidence":0.9}"""


# ── SQL / RAG keyword heuristics for fallback ────────────────────────────────

_SQL_KEYWORDS = {
    "top", "rank", "ranklist", "ranking", "average", "avg", "count", "how many",
    "total", "sum", "percent", "percentage", "highest", "lowest", "best", "worst",
    "compare", "comparison", "vs", "versus", "pass rate", "fail", "failed",
    "above", "below", "threshold", "cgpa", "sgpa", "grade", "score", "marks",
    "list all", "show all", "topper", "toppers",
}
_RAG_KEYWORDS = {
    "tell me about", "describe", "explain", "who is", "struggling",
    "performing well", "improved", "trend", "why", "how did", "overall",
    "qualitative", "context", "background", "profile",
}


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class RouteResult:
    """Result of query classification by QueryRouter."""

    route: str                                           # "SQL" | "RAG" | "HYBRID"
    needs_filter: bool                                   # Whether filters should be applied
    entities: list[str] = field(default_factory=list)   # Names, roll numbers, subject codes
    filters: dict = field(default_factory=dict)         # {"semester": 4, "branch": "..."}
    intent: str = ""                                     # "find top students by SGPA"
    complexity: str = "simple"                          # "simple" | "moderate" | "complex"
    confidence: float = 0.0                             # 0.0–1.0


# ── Router ────────────────────────────────────────────────────────────────────

class QueryRouter:
    """
    Classifies incoming queries and routes them to the optimal pipeline.

    Uses a lightweight LLM call (temperature=0.1, max_tokens=300) to classify
    the query type and extract structured metadata (entities, filters, intent).
    Falls back to keyword-based heuristics if LLM output is unparseable.
    """

    def __init__(self, llm: OllamaClient, prompt_store=None) -> None:
        """
        Args:
            llm:          OllamaClient instance
            prompt_store: Reserved for Phase 9 adaptive prompt evolution.
                          When set, _get_system_prompt() will load from DB.
        """
        self.llm = llm
        self.prompt_store = prompt_store

    async def route(
        self,
        query: str,
        chat_history: list[dict] | None = None,
    ) -> RouteResult:
        """
        Classify a query and determine the optimal processing pipeline.

        Args:
            query:        The user's natural language query
            chat_history: Optional recent chat history (last 2-4 exchanges)
                          for resolving follow-up queries like "what about sem 3?"

        Returns:
            RouteResult with route, filters, entities, intent, complexity, confidence
        """
        system_prompt = await self._get_system_prompt()
        user_prompt = self._build_user_prompt(query, chat_history)

        try:
            response = await self.llm.generate(
                prompt=user_prompt,
                system=system_prompt,
                temperature=0.10,
                max_tokens=300,
                format="json",
                options={"temperature": 0.10},
            )
            result = self._parse_response(response, query)
        except Exception as exc:
            logger.warning("Router LLM call failed (%s) — using keyword fallback", exc)
            result = self._fallback_classify(query)

        # Post-processing: regex fallback for subject codes the LLM might miss
        if not result.filters.get("subject_code"):
            subject_match = re.search(r'\b([A-Z]{2,4}\d{3,4}[A-Z]?)\b', query.upper())
            if subject_match:
                result.filters["subject_code"] = subject_match.group(1)
                result.needs_filter = True

        return result

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _get_system_prompt(self) -> str:
        """
        Return the router's system prompt.

        In Phase 9 this will load the active version from prompts.db.
        For now, returns the static constant.
        """
        if self.prompt_store is not None:
            try:
                from db.sqlite_client import fetch_one
                from config import settings
                row = await fetch_one(
                    settings.PROMPTS_DB,
                    "SELECT content FROM prompt_templates "
                    "WHERE prompt_name = ? AND section_name = ? AND is_active = 1",
                    ("router", "system"),
                )
                if row and row.get("content"):
                    return row["content"]
            except Exception as exc:
                logger.warning("Could not load router prompt from DB: %s", exc)
        return ROUTER_SYSTEM_PROMPT

    def _build_user_prompt(
        self,
        query: str,
        chat_history: list[dict] | None,
    ) -> str:
        """
        Build the classification prompt, optionally including recent context.

        Includes the last 2 exchanges from chat_history to help the LLM
        resolve follow-up queries (e.g., "what about semester 3?" after
        discussing a student in semester 5).
        """
        parts: list[str] = []

        if chat_history:
            recent = chat_history[-6:]  # last 3 exchanges (user + assistant each)
            if recent:
                ctx_lines = []
                for msg in recent:
                    role = msg.get("role", "user")
                    content = str(msg.get("content", ""))[:300]  # truncate long messages
                    ctx_lines.append(f"{role.upper()}: {content}")
                parts.append(
                    "RECENT CONVERSATION CONTEXT (use this to resolve follow-up queries like "
                    "\"what about semester 3\" or \"show the bottom 5\" or \"and for CSE?\"):\n"
                    + "\n".join(ctx_lines)
                )

        parts.append(f"USER QUERY: {query}")
        return "\n\n".join(parts)

    def _parse_response(self, response: str, original_query: str) -> RouteResult:
        """
        Parse the LLM's JSON response into a RouteResult.

        Handles:
        - Markdown code fences around JSON
        - Missing or null filter keys
        - Semester as string ("four" → 4)
        - Branch abbreviations (CSE → full name)
        - Completely malformed JSON (falls back to keyword classifier)
        """
        cleaned = self._clean_json(response)

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            logger.warning(
                "Router: could not parse LLM JSON response. "
                "Raw: %s — falling back to keyword classifier",
                response[:300],
            )
            return self._fallback_classify(original_query)

        route = str(data.get("route", "RAG")).upper()
        if route not in {"SQL", "RAG", "HYBRID"}:
            route = "RAG"

        raw_filters: dict = data.get("filters") or {}
        filters = self._validate_filters(raw_filters)

        entities: list[str] = [
            str(e) for e in (data.get("entities") or []) if e
        ]

        complexity = str(data.get("complexity", "simple")).lower()
        if complexity not in {"simple", "moderate", "complex"}:
            complexity = "simple"

        try:
            confidence = float(data.get("confidence", 0.0))
            confidence = max(0.0, min(1.0, confidence))
        except (TypeError, ValueError):
            confidence = 0.0

        needs_filter = bool(data.get("needs_filter", bool(filters)))

        return RouteResult(
            route=route,
            needs_filter=needs_filter,
            entities=entities,
            filters=filters,
            intent=str(data.get("intent", "")).strip()[:200],
            complexity=complexity,
            confidence=confidence,
        )

    def _fallback_classify(self, query: str) -> RouteResult:
        """
        Rule-based fallback when LLM output is unparseable.

        Heuristic:
        - Contains SQL-style keywords → SQL
        - Contains RAG-style keywords → RAG
        - Contains both → HYBRID
        - Default → RAG (safest)
        """
        q = query.lower()
        has_sql = any(kw in q for kw in _SQL_KEYWORDS)
        has_rag = any(kw in q for kw in _RAG_KEYWORDS)

        if has_sql and has_rag:
            route = "HYBRID"
        elif has_sql:
            route = "SQL"
        else:
            route = "RAG"

        # Try to extract a semester number with simple regex
        filters: dict = {}
        sem_match = re.search(r"\bsem(?:ester)?\s*(\d)\b", q)
        if sem_match:
            filters["semester"] = int(sem_match.group(1))

        return RouteResult(
            route=route,
            needs_filter=bool(filters),
            filters=filters,
            intent="(fallback classification)",
            confidence=0.3,
        )

    # ── Static helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _clean_json(text: str) -> str:
        """Strip markdown code fences from LLM JSON output."""
        text = text.strip()
        if text.startswith("```"):
            # Remove opening fence (handles ```json and plain ```)
            text = re.sub(r"^```(?:json)?\s*\n?", "", text)
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
        return text.strip()

    @staticmethod
    def _validate_filters(raw: dict) -> dict:
        """
        Clean and validate filter values from the LLM.

        - semester: coerced to int 1-8 (words like "four" → 4)
        - branch: uppercased and abbreviations expanded
        - null/None/empty-string values are dropped
        """
        _WORD_TO_NUM = {
            "one": 1, "two": 2, "three": 3, "four": 4,
            "five": 5, "six": 6, "seven": 7, "eight": 8,
            "first": 1, "second": 2, "third": 3, "fourth": 4,
            "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8,
        }

        cleaned: dict = {}

        # semester
        if raw.get("semester") is not None:
            val = raw["semester"]
            if isinstance(val, str):
                val = val.lower().strip()
                val = _WORD_TO_NUM.get(val, val)
            try:
                sem = int(val)
                if 1 <= sem <= 8:
                    cleaned["semester"] = sem
            except (ValueError, TypeError):
                pass

        # branch
        if raw.get("branch"):
            branch = str(raw["branch"]).strip().upper()
            # Expand known abbreviations
            cleaned["branch"] = _BRANCH_MAP.get(branch, branch)

        # roll_no
        if raw.get("roll_no"):
            cleaned["roll_no"] = str(raw["roll_no"]).strip()

        # name
        if raw.get("name"):
            cleaned["name"] = str(raw["name"]).strip().upper()

        # session
        if raw.get("session"):
            cleaned["session"] = str(raw["session"]).strip()

        # subject_code
        if raw.get("subject_code"):
            cleaned["subject_code"] = str(raw["subject_code"]).strip().upper()

        return cleaned


# ── Prompt initialisation helper (used by ingestion/init_prompts.py) ─────────

async def init_router_prompt() -> None:
    """
    Store the router's system prompt in prompts.db as version 1.
    Safe to call multiple times — skips if already present.
    """
    from db.sqlite_client import execute, fetch_one
    from config import settings
    import uuid

    existing = await fetch_one(
        settings.PROMPTS_DB,
        "SELECT id FROM prompt_templates "
        "WHERE prompt_name = ? AND section_name = ? AND is_active = 1",
        ("router", "system"),
    )
    if not existing:
        await execute(
            settings.PROMPTS_DB,
            "INSERT INTO prompt_templates (id, prompt_name, section_name, content, version, is_active) "
            "VALUES (?, ?, ?, ?, 1, 1)",
            (str(uuid.uuid4()), "router", "system", ROUTER_SYSTEM_PROMPT),
        )
        logger.info("Stored router/system prompt v1 in prompts.db")
