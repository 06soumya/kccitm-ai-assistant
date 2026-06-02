"""
Query Planner for KCCITM AI Assistant.

Single LLM call that reads the FULL query + chat history + dataset context and
produces a structured PLAN that downstream code uses to execute the right
operation. This replaces brittle pattern-matching and regex short-circuits —
the LLM does the reasoning; the rest of the pipeline just executes the plan.

Output shape (all fields always present, defaults filled in by the parser):

    {
        # --- legacy intent fields (kept for backwards compat with existing
        #     short-circuits like the student-lookup fast path) ---
        "intent": "student_lookup" | "analytical" | "followup" | "concept",
        "expanded_query": "...",          # full standalone question
        "student_name": "..." | null,
        "roll_no": "..." | null,
        "is_followup": true/false,
        "active_student_followup": true/false,

        # --- new plan fields (drive routing + execution) ---
        "operation": "lookup" | "aggregate" | "list" | "comparison"
                   | "concept" | "meta" | "unknown",
        "route":    "SQL" | "RAG" | "HYBRID" | "AUTO",
        "entities": {
            "branch": str | null,
            "semester": int | null,
            "session": str | null,
            "course": str | null,
            "subject": str | null,
            "batch": str | null,
            "gender": str | null,
            "top_n": int | null,
            "aggregation": "avg" | "sum" | "count" | "min" | "max"
                         | "pass_rate" | null,
        },
        "confidence": float (0.0 .. 1.0),
        "ambiguities": [str],
        "needs_clarification": bool,
        "clarification_question": str | null,
        "clarification_options": [str],
        "needs_templates": bool,
        "reasoning": str,
    }

Usage:
    from core.query_understander import QueryUnderstander
    understander = QueryUnderstander(llm)
    plan = await understander.understand("average marks in DBMS for ECE", history)
"""

import json
import logging
import re

from core.llm_client import OllamaClient

logger = logging.getLogger(__name__)


PLAN_PROMPT = """You are the QUERY PLANNER for a KCCITM student-database assistant.

Your job: READ the user's query in full, REASON about what they actually want,
then output a structured JSON plan. Downstream code uses your plan to execute
the right operation — so be thorough and honest about uncertainty.

=== OUTPUT FIELDS (you MUST emit all of them) ===

intent : "student_lookup" | "analytical" | "followup" | "concept"
    High-level type. Used by legacy short-circuits.
    - student_lookup : query is about ONE specific student (name or roll)
    - analytical     : aggregates / lists / comparisons across students
    - followup       : continuation of prior turn ("what about ECE?")
    - concept        : definition / methodology ("what is SGPA")

operation : "lookup" | "aggregate" | "list" | "comparison"
          | "concept" | "meta" | "unknown"
    What kind of OPERATION the answer requires.
    - lookup     : pull info about ONE student
    - aggregate  : compute a number (avg, count, sum, pass-rate, min, max)
    - list       : enumerate N rows (top 5, all CSE students)
    - comparison : compare groups (CSE vs ECE, 2021 vs 2022)
    - concept    : explain a concept (no data needed)
    - meta       : about the dataset itself ("what data do you have")
    - unknown    : you cannot determine — set ambiguities and
                   needs_clarification=true

route : "SQL" | "RAG" | "HYBRID" | "AUTO"
    Which pipeline should handle this.
    - SQL    : structured query against MySQL tables
    - RAG    : unstructured retrieval from Milvus vector store
    - HYBRID : run both and merge
    - AUTO   : you're not sure — let the router decide

entities : object — include ONLY keys you actually extracted.
    Possible keys (omit if not mentioned by the user):
      branch       : canonical branch name (use the branch list above)
      semester     : integer
      session      : session string like "2021-22(REGULAR)"
      course       : course name
      subject      : subject keyword (e.g. "DBMS", "Operating Systems")
      batch        : 4-digit admission year (e.g. "2021")
      gender       : "Male" | "Female"
      top_n        : integer N when user says "top 5", "bottom 10"
      aggregation  : when operation=aggregate, one of:
                     "avg" | "sum" | "count" | "min" | "max" | "pass_rate"

student_name : extracted student name, or null
roll_no      : 13-digit roll number, or null

expanded_query : the full standalone question (resolve "what about it?"
                 against history). For student_lookup, a clean phrasing like
                 "show full results for bipasa sarkar".

is_followup : true if this query refers to a previous turn
active_student_followup : true if it's about the SAME student as just
                          discussed (history shows [active student: ...])

confidence : float 0.0 to 1.0 — your honest confidence in this plan.
    1.0  : everything crystal clear
    0.7+ : confident enough to proceed without clarification
    <0.7 : downstream will run a deeper reasoning pass
    DO NOT inflate. Honest low confidence is better than wrong-but-confident.

ambiguities : list of short strings describing what's unclear.
    Examples: ["which semester?", "average or top N?", "which year batch?"]
    Empty list when everything is clear.

needs_clarification : true ONLY when an ambiguity would change the answer
    materially. Don't ask for trivial details the user clearly didn't care
    about. Set to true when there are 2+ plausible interpretations that
    return different data.

clarification_question : the question to ask the user (or null).
    Make it short and specific. E.g. "Did you mean the average marks, the
    top 10 students, or the pass rate in DBMS?"

clarification_options : list of 2-4 concrete clickable options the user
    can pick. Each option should be a complete restatement of the query.
    Empty list when no clarification needed.
    Example: ["Average marks in DBMS",
              "Top 10 students in DBMS",
              "Pass rate in DBMS"]

needs_templates : true if you think looking at a library of pre-built SQL
    templates would help. Set true for UNUSUAL or COMPLEX SQL shapes —
    multi-table joins, "students who never failed", "subjects where >90%
    got grade X", pass-rate comparisons across cohorts. Set false for
    simple lookups, simple counts, simple top-N rankings.

reasoning : ONE short sentence explaining your plan. For logs + debugging.

=== HARD RULES ===

1. READ THE FULL QUERY. Don't pattern-match on the first words.
2. Hinglish is already normalized before reaching you ("kitne CSE me" →
   "how many in CSE"). Trust the English form.
3. A 13-digit number → student_lookup, intent=student_lookup, route=SQL.
4. A student NAME anywhere in the query → student_lookup, route=SQL.
5. "average / mean / avg" → operation=aggregate, aggregation=avg.
6. "how many / count / total number" → operation=aggregate, aggregation=count.
7. "top N / best N / highest" → operation=list, entities.top_n=N, route=SQL.
8. "compare / vs / versus" → operation=comparison.
9. "what kind of data / describe the database / what's in here" →
   operation=meta, route=AUTO.
10. "what is SGPA / how does grading work" → operation=concept, route=RAG.
11. Single digit 1-9 after a "Reply with the number" prompt → student_lookup
    (selection from a previous options list).
12. When the user gives ONE subject name without a verb (e.g. "DBMS marks"
    by itself), set needs_clarification=true with options for avg vs top vs
    pass-rate. If the verb is clear ("average DBMS marks") proceed without
    clarification.
13. NEVER auto-fill missing entities from the dataset list. Only fill what
    the user actually said (or what history makes clear via
    active_student_followup).

=== DATASET (for grounding entity extraction) ===
{dataset_ctx}

{history_block}

User query: "{query}"

Respond with ONLY a JSON object. No prose before or after.

Example for "average marks in DBMS for ECE semester 4":
{{
  "intent": "analytical",
  "operation": "aggregate",
  "route": "SQL",
  "entities": {{"subject": "DBMS", "branch": "ECE", "semester": 4, "aggregation": "avg"}},
  "student_name": null,
  "roll_no": null,
  "expanded_query": "average marks in DBMS for ECE semester 4",
  "is_followup": false,
  "active_student_followup": false,
  "confidence": 0.95,
  "ambiguities": [],
  "needs_clarification": false,
  "clarification_question": null,
  "clarification_options": [],
  "needs_templates": false,
  "reasoning": "Clear aggregate request — AVG over subject_marks filtered by branch + semester."
}}

Example for "DBMS marks":
{{
  "intent": "analytical",
  "operation": "unknown",
  "route": "AUTO",
  "entities": {{"subject": "DBMS"}},
  "student_name": null,
  "roll_no": null,
  "expanded_query": "DBMS marks",
  "is_followup": false,
  "active_student_followup": false,
  "confidence": 0.4,
  "ambiguities": ["average or top students or pass rate?"],
  "needs_clarification": true,
  "clarification_question": "What about DBMS marks did you want?",
  "clarification_options": ["Average marks in DBMS", "Top 10 students in DBMS", "Pass rate in DBMS"],
  "needs_templates": false,
  "reasoning": "Subject mentioned but no operation given — three distinct interpretations."
}}

Your output:"""


_VALID_INTENTS = {"student_lookup", "analytical", "followup", "concept"}
_VALID_OPERATIONS = {
    "lookup", "aggregate", "list", "comparison",
    "concept", "meta", "unknown",
}
_VALID_ROUTES = {"SQL", "RAG", "HYBRID", "AUTO"}
_VALID_AGGREGATIONS = {"avg", "sum", "count", "min", "max", "pass_rate"}


# Words that nearly every valid academic query contains at least one of.
# Used by the garbage detector to avoid calling the LLM on nonsense input
# (which the model otherwise hallucinates entities for).
_COMMON_QUERY_WORDS = frozenset({
    # English query verbs / interrogatives
    "show", "list", "get", "give", "tell", "find", "display", "fetch",
    "what", "which", "who", "how", "where", "when", "why",
    "is", "are", "was", "were", "do", "does", "did", "have", "has", "had",
    "can", "will", "would", "should",
    # quantifiers / comparators
    "top", "bottom", "best", "worst", "highest", "lowest", "first", "last",
    "more", "less", "above", "below", "over", "under", "between",
    "average", "avg", "mean", "median", "count", "total", "sum", "max", "min",
    "all", "any", "every", "each", "some",
    "compare", "vs", "versus", "rank", "ranking", "ratio",
    # academic nouns
    "student", "students", "mark", "marks", "grade", "grades",
    "result", "results", "subject", "subjects", "exam", "examination",
    "semester", "sem", "sgpa", "cgpa", "branch", "branches",
    "course", "courses", "session", "sessions", "batch", "year",
    "data", "info", "information", "details", "detail", "performance",
    "pass", "fail", "passed", "failed", "rate", "percent", "percentage",
    "backlog", "backlogs", "topper", "toppers",
    "internal", "external", "theory", "practical", "credit", "credits",
    # common Hinglish stopwords (queries may not be normalized yet)
    "kitne", "kitna", "kya", "kaun", "kahan", "kaise", "hai", "hain",
    "me", "mein", "ka", "ki", "ke", "ho", "kis",
    # generic articles / prepositions
    "the", "a", "an", "and", "or", "of", "for", "with", "by",
    "from", "to", "in", "on", "at", "as", "no", "not",
    "his", "her", "their", "this", "that", "these", "those",
    "him", "she", "he", "they", "them",
})

# Branch abbreviations users commonly type. Helps the garbage detector
# recognize "CSE", "ECE" etc. as valid even before LLM canonicalization.
_BRANCH_ABBREVS = frozenset({
    "cse", "ece", "ee", "me", "ce", "it", "eee", "che", "chem",
    "civil", "mech", "mca", "mba", "bca", "btech", "mtech",
})

# Common subject shorthand the LLM should recognize as a real token.
_COMMON_SUBJECTS = frozenset({
    "dbms", "dsa", "os", "cn", "coa", "oop", "oops",
    "ai", "ml", "evs", "maths", "math", "mathematics",
    "physics", "chemistry", "english", "hindi",
})


def _looks_like_garbage(query: str) -> bool:
    """
    True when the query is pure nonsense (e.g. 'asdfgh qwerty') so we can
    skip the LLM call and avoid entity hallucination.

    Conservative: only flags when EVERY alphabetic token is BOTH unknown
    to our vocab AND has vowel ratio below 0.20 (real words/names cluster
    above that).
    """
    from core.dataset_context import (
        get_branches, get_courses, get_subject_codes,
    )

    q = query.strip().lower()
    tokens = re.findall(r"[a-z0-9]+", q)
    if not tokens:
        return False  # all punctuation — handled separately
    if any(t.isdigit() for t in tokens):
        return False  # any digit makes it intentional

    branches_lc = {
        w.lower() for b in get_branches()
        for w in re.findall(r"[A-Za-z]+", b)
    }
    courses_lc = {
        w.lower() for c in get_courses()
        for w in re.findall(r"[A-Za-z]+", c)
    }
    subject_codes_lc = {c.lower() for c in get_subject_codes()}
    known = (
        _COMMON_QUERY_WORDS | _BRANCH_ABBREVS | _COMMON_SUBJECTS
        | branches_lc | courses_lc | subject_codes_lc
    )

    for tok in tokens:
        if tok in known:
            return False
        # Allow real-looking words/names: vowel ratio >= 0.20.
        if len(tok) >= 3:
            vowels = sum(1 for c in tok if c in "aeiou")
            if vowels / len(tok) >= 0.20:
                return False

    return True


def _too_short_or_punct(query: str) -> bool:
    """True when the query is empty, only punctuation, or has < 2 alnum chars."""
    if not query.strip():
        return True
    alnum = re.sub(r"[^A-Za-z0-9]", "", query)
    return len(alnum) < 2


def _clarify_plan(query: str, question: str, options: list[str], reason: str) -> dict:
    """A canned clarification plan returned by fast-paths (empty / garbage input)."""
    plan = _empty_plan(query)
    plan.update({
        "operation": "unknown",
        "confidence": 0.0,
        "ambiguities": ["query is unclear or empty"],
        "needs_clarification": True,
        "clarification_question": question,
        "clarification_options": options,
        "reasoning": reason,
    })
    return plan


def _empty_plan(query: str) -> dict:
    """The shape every plan must have — used for defaults and fallbacks."""
    return {
        "intent": "analytical",
        "operation": "unknown",
        "route": "AUTO",
        "entities": {},
        "student_name": None,
        "roll_no": None,
        "expanded_query": query,
        "is_followup": False,
        "active_student_followup": False,
        "confidence": 0.5,
        "ambiguities": [],
        "needs_clarification": False,
        "clarification_question": None,
        "clarification_options": [],
        "needs_templates": False,
        "reasoning": "",
    }


class QueryUnderstander:

    def __init__(self, llm: OllamaClient) -> None:
        self.llm = llm

    async def understand(
        self, query: str, chat_history: list[dict] | None = None,
    ) -> dict:
        """
        Plan the user's query.

        Returns the full structured plan. Never raises — on LLM failure,
        falls back to heuristics so the orchestrator always gets a plan.
        """
        # Fast-path: empty / punctuation-only / too short → ask the user
        # what they want. No LLM call, no hallucination risk.
        if _too_short_or_punct(query):
            return _clarify_plan(
                query,
                question="What would you like to know about the student database?",
                options=[
                    "Show me top students in a branch",
                    "Average SGPA in a branch",
                    "Tell me about a specific student",
                    "How many students passed in a session",
                ],
                reason="Empty or punctuation-only input — asking the user to clarify.",
            )

        # Fast-path: garbage input (random letters with no recognisable words) →
        # ask the user instead of letting the LLM invent entities.
        if _looks_like_garbage(query):
            return _clarify_plan(
                query,
                question="I didn't recognise that as a question. Could you rephrase?",
                options=[
                    "Show me top students in a branch",
                    "Average SGPA in a branch",
                    "Tell me about a specific student",
                ],
                reason="Garbage input detected — no recognisable academic tokens.",
            )

        # Fast-path: 13-digit roll number — no LLM needed
        roll_match = re.search(r'\b(\d{13})\b', query)
        if roll_match:
            plan = _empty_plan(query)
            plan.update({
                "intent": "student_lookup",
                "operation": "lookup",
                "route": "SQL",
                "roll_no": roll_match.group(1),
                "confidence": 1.0,
                "reasoning": "13-digit roll number detected — direct student lookup.",
            })
            return plan

        # Fast-path: single digit 1-9 after an options list
        q_stripped = query.strip()
        if q_stripped.isdigit() and 1 <= int(q_stripped) <= 9 and chat_history:
            for msg in reversed(chat_history):
                if msg.get("role") == "assistant" and "Reply with the number" in msg.get("content", ""):
                    plan = _empty_plan(query)
                    plan.update({
                        "intent": "student_lookup",
                        "operation": "lookup",
                        "route": "SQL",
                        "confidence": 1.0,
                        "reasoning": "Numeric selection from prior options list.",
                    })
                    plan["selection"] = int(q_stripped)
                    return plan

        history_block = self._format_history(chat_history)

        from core.dataset_context import get_dataset_context
        dataset_ctx = get_dataset_context()

        prompt = PLAN_PROMPT.format(
            query=query.replace('"', '\\"'),
            history_block=history_block,
            dataset_ctx=dataset_ctx,
        )

        try:
            response = await self.llm.generate(
                prompt=prompt,
                temperature=0.05,
                max_tokens=500,
                format="json",
                options={"temperature": 0.05},
            )
            plan = self._parse_response(response, query)
            logger.info(
                "Plan: intent=%s op=%s route=%s conf=%.2f clarify=%s tmpl=%s | %s",
                plan["intent"], plan["operation"], plan["route"],
                plan["confidence"], plan["needs_clarification"],
                plan["needs_templates"], plan["reasoning"][:80],
            )
            return plan

        except Exception as exc:
            logger.warning("Query planning failed (%s) — using fallback", exc)
            return self._fallback(query, chat_history)

    async def re_plan(
        self,
        query: str,
        first_plan: dict,
        chat_history: list[dict] | None = None,
    ) -> dict | None:
        """
        Adaptive escalation (Step 6). Called by the orchestrator when the
        first planner pass returned confidence < the routing floor and
        didn't already flag needs_clarification.

        Runs a second planner call with deterministic temperature and a
        bigger token budget, plus a prompt addendum that tells the model
        to commit to ONE reading with confidence ≥ 0.85, or otherwise
        flip to needs_clarification with concrete options.

        Returns the new plan, or None if the LLM call fails (caller keeps
        the first-pass plan).
        """
        history_block = self._format_history(chat_history)

        from core.dataset_context import get_dataset_context
        dataset_ctx = get_dataset_context()

        first_conf = float(first_plan.get("confidence") or 0.0)
        first_reasoning = str(first_plan.get("reasoning") or "(no reasoning)")[:300]
        first_amb = first_plan.get("ambiguities") or []

        replan_preamble = (
            "=== ESCALATION: YOUR FIRST ATTEMPT WAS UNCERTAIN ===\n"
            f"First-pass confidence: {first_conf:.2f} (below the 0.70 routing floor).\n"
            f"First-pass reasoning: {first_reasoning}\n"
            f"Ambiguities you flagged: {first_amb}\n"
            f"First-pass picked: intent={first_plan.get('intent')} "
            f"operation={first_plan.get('operation')} route={first_plan.get('route')}.\n"
            "\n"
            "Think step-by-step this time. Re-read the user query and the recent\n"
            "chat history. Use the dataset context to decide what's actually\n"
            "answerable. Pick the SINGLE most likely interpretation.\n"
            "\n"
            "You MUST do ONE of these — no middle ground:\n"
            "  (a) Commit to one reading with confidence ≥ 0.85, or\n"
            "  (b) Set needs_clarification=true with 2-4 concrete clarification_options\n"
            "      phrased as full, runnable queries (e.g. 'Average marks in DBMS',\n"
            "      not 'Average').\n"
            "Do NOT return confidence between 0.5 and 0.85 — that's the band that\n"
            "triggered this re-plan in the first place.\n"
            "==================================================\n\n"
        )

        prompt = replan_preamble + PLAN_PROMPT.format(
            query=query.replace('"', '\\"'),
            history_block=history_block,
            dataset_ctx=dataset_ctx,
        )

        try:
            response = await self.llm.generate(
                prompt=prompt,
                temperature=0.0,
                max_tokens=1500,
                format="json",
                options={"temperature": 0.0},
            )
            plan = self._parse_response(response, query)
            logger.info(
                "Re-plan: intent=%s op=%s route=%s conf=%.2f clarify=%s | %s",
                plan["intent"], plan["operation"], plan["route"],
                plan["confidence"], plan["needs_clarification"],
                plan["reasoning"][:80],
            )
            return plan
        except Exception as exc:
            logger.warning("Re-plan LLM call failed: %s", exc)
            return None

    def _format_history(self, chat_history: list[dict] | None) -> str:
        if not chat_history:
            return "Chat history: (none — this is the first message)"

        lines = ["=== RECENT CHAT HISTORY ==="]
        recent = chat_history[-4:]
        for msg in recent:
            role = msg.get("role", "?")
            content = msg.get("content", "")[:200]
            meta = msg.get("metadata", {})
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except Exception:
                    meta = {}
            hint = ""
            if meta.get("current_student_name"):
                hint = (
                    f" [active student: {meta['current_student_name']}, "
                    f"roll: {meta.get('current_student_roll', '?')}]"
                )
            elif meta.get("route_used"):
                hint = f" [route: {meta['route_used']}]"
            lines.append(f"{role}: {content}{hint}")

        return "\n".join(lines)

    def _parse_response(self, response: str, original_query: str) -> dict:
        """Parse LLM JSON response into the full plan shape, with defaults."""
        text = response.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*\n?", "", text)
            text = re.sub(r"\n?```$", "", text)
        text = text.strip()

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match:
                try:
                    parsed = json.loads(match.group())
                except json.JSONDecodeError:
                    logger.warning("Could not parse planner JSON: %s", text[:200])
                    return self._fallback(original_query, None)
            else:
                return self._fallback(original_query, None)

        plan = _empty_plan(original_query)

        # Copy + validate fields, falling back to defaults on bad values.
        intent = parsed.get("intent", plan["intent"])
        if intent in _VALID_INTENTS:
            plan["intent"] = intent

        operation = parsed.get("operation", plan["operation"])
        if operation in _VALID_OPERATIONS:
            plan["operation"] = operation

        route = parsed.get("route", plan["route"])
        if isinstance(route, str) and route.upper() in _VALID_ROUTES:
            plan["route"] = route.upper()

        entities = parsed.get("entities", {})
        leaked_name = None
        leaked_roll = None
        if isinstance(entities, dict):
            cleaned = {}
            for k, v in entities.items():
                if v is None or v == "":
                    continue
                if k == "aggregation":
                    if isinstance(v, str) and v.lower() in _VALID_AGGREGATIONS:
                        cleaned[k] = v.lower()
                elif k in ("semester", "top_n"):
                    try:
                        cleaned[k] = int(v)
                    except (TypeError, ValueError):
                        pass
                elif k == "student_name":
                    # Some models stuff the student name into entities even
                    # though it belongs at the top level. Lift it.
                    leaked_name = v
                elif k == "roll_no":
                    leaked_roll = v
                else:
                    cleaned[k] = v
            plan["entities"] = cleaned

        plan["student_name"] = self._clean_student_name(
            parsed.get("student_name") or leaked_name
        )
        plan["roll_no"] = parsed.get("roll_no") or leaked_roll

        expanded = parsed.get("expanded_query") or original_query
        if isinstance(expanded, str) and len(expanded.strip()) >= 3:
            plan["expanded_query"] = expanded.strip()

        plan["is_followup"] = bool(parsed.get("is_followup", False))
        plan["active_student_followup"] = bool(
            parsed.get("active_student_followup", False)
        )

        try:
            conf = float(parsed.get("confidence", plan["confidence"]))
            plan["confidence"] = max(0.0, min(1.0, conf))
        except (TypeError, ValueError):
            pass

        ambiguities = parsed.get("ambiguities", [])
        if isinstance(ambiguities, list):
            plan["ambiguities"] = [str(a) for a in ambiguities if a]

        plan["needs_clarification"] = bool(
            parsed.get("needs_clarification", False)
        )

        cq = parsed.get("clarification_question")
        if isinstance(cq, str) and cq.strip():
            plan["clarification_question"] = cq.strip()

        opts = parsed.get("clarification_options", [])
        if isinstance(opts, list):
            plan["clarification_options"] = [
                str(o).strip() for o in opts if str(o).strip()
            ][:4]

        plan["needs_templates"] = bool(parsed.get("needs_templates", False))

        reasoning = parsed.get("reasoning", "")
        if isinstance(reasoning, str):
            plan["reasoning"] = reasoning.strip()

        # Active-student followup is ALWAYS a student lookup, regardless of
        # what the LLM said. ("show all his marks", "his subject results"
        # after a prior turn that loaded a student.) Without this guard the
        # orchestrator's student-lookup short-circuit (which checks
        # intent == 'student_lookup') silently falls through to RAG.
        if plan["active_student_followup"]:
            plan["intent"] = "student_lookup"
            plan["operation"] = "lookup"
            plan["route"] = "SQL"

        # Guard: if needs_clarification is true but no question or options
        # were supplied, demote to false so we don't stall the pipeline.
        if plan["needs_clarification"] and not (
            plan["clarification_question"] and plan["clarification_options"]
        ):
            plan["needs_clarification"] = False

        return plan

    @staticmethod
    def _clean_student_name(name) -> str | None:
        if not name or not isinstance(name, str):
            return None
        name = name.strip()
        name = re.sub(
            r"\s*(?:'s)?\s*(?:full|whole|complete|all|detailed)?\s*"
            r"(?:result|results|marks|details|profile|performance|semester)?\s*$",
            "", name, flags=re.IGNORECASE,
        ).strip()
        return name if len(name) >= 2 else None

    def _fallback(self, query: str, chat_history: list[dict] | None) -> dict:
        """Heuristic fallback when the LLM fails. Conservative — defaults to
        AUTO routing so the existing router still picks a sensible pipeline."""
        plan = _empty_plan(query)
        q_lower = query.lower().strip()

        name_match = re.search(
            r"(?:result|marks|details|profile)\s+(?:of|for)\s+([a-z]+(?: [a-z]+){1,3})",
            q_lower,
        ) or re.search(
            r"^(?:show\s+(?:me\s+)?)?([a-z]+(?: [a-z]+){1,2})(?:'s)?\s+(?:full\s+)?(?:result|marks)",
            q_lower,
        )
        if name_match:
            name = name_match.group(1).strip()
            noise = {"full", "complete", "whole", "all", "the", "show", "me",
                     "give", "tell"}
            name_words = [w for w in name.split() if w not in noise]
            if name_words:
                plan.update({
                    "intent": "student_lookup",
                    "operation": "lookup",
                    "route": "SQL",
                    "student_name": " ".join(name_words),
                    "confidence": 0.7,
                    "reasoning": "Fallback: name pattern detected in query.",
                })
                return plan

        concept_phrases = (
            "what is sgpa", "what is cgpa", "what does cp mean",
            "what is back paper", "grading system", "passing criteria",
        )
        if any(p in q_lower for p in concept_phrases):
            plan.update({
                "intent": "concept",
                "operation": "concept",
                "route": "RAG",
                "confidence": 0.7,
                "reasoning": "Fallback: concept question detected.",
            })
            return plan

        plan["reasoning"] = "Fallback: defaults applied — router will decide."
        return plan
