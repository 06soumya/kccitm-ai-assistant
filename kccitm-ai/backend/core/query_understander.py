"""
Query understander for KCCITM AI Assistant.

Single LLM call that reads the FULL query + chat history and produces a
structured understanding. Replaces the brittle pattern-matching in
_expand_followup and _detect_student_lookup.

Output:
    {
        "intent": "student_lookup" | "analytical" | "followup" | "concept",
        "expanded_query": "...",          # full standalone question
        "student_name": "..." | null,     # if looking up a specific student
        "roll_no": "..." | null,          # if a 13-digit roll was mentioned
        "is_followup": true/false,        # references prior conversation
        "active_student_followup": true/false  # about the same student
    }

Usage:
    from core.query_understander import QueryUnderstander
    understander = QueryUnderstander(llm)
    result = await understander.understand("full result of bipasa sarkar", history)
"""

import json
import logging
import re

from core.llm_client import OllamaClient

logger = logging.getLogger(__name__)


UNDERSTAND_PROMPT = """You are a query classifier for a university student database system.

DATABASE: Students, semester results, subject marks for KCCITM university.

Given the user's query and recent chat history, produce a JSON classification.

=== INTENT TYPES ===
- "student_lookup": User wants info about a SPECIFIC student (by name or roll number).
  Examples: "show me bipasa sarkar's result", "full result of om singh", "2304920100001", "tell me about aakash"
- "analytical": User wants aggregated data, rankings, comparisons, rates, counts across MULTIPLE students.
  Examples: "top 5 toppers", "pass rate of batch 2021", "how many students failed", "average sgpa"
- "followup": User is continuing a previous conversation — references "them", "those", "same", "what about", etc.
  Examples: "what about semester 4?", "and the bottom 5?", "show their marks", "same for ECE"
- "concept": User asks about definitions, methodology, or how things work.
  Examples: "what is SGPA", "how does grading work", "what does CP mean"

=== RULES ===
1. READ THE FULL QUERY — don't just look at the first few words.
2. "show me [name]'s result" is student_lookup, NOT a followup, even if it starts with "show me".
3. If a student was JUST discussed (check history for [active student: ...]) and user asks for
   "detailed breakdown", "full result", "show all semesters", "complete marks", "subject marks",
   "show everything", or similar — this is student_lookup with active_student_followup=true.
   NOT followup, NOT analytical. The user wants MORE DATA about the SAME student.
4. If a student name appears ANYWHERE in the query, it's student_lookup.
5. Extract the student name CLEANLY — strip "result", "marks", "full", "show me", "of", "for", etc.
6. If the query has a 13-digit number, that's a roll_no — always student_lookup.
7. For followups (analytical context like "what about batch 2022?"), rewrite expanded_query as a
   complete standalone question using chat history context.
8. For student_lookup, expanded_query should be the clean query (e.g., "show full results for bipasa sarkar").
9. A number 1-9 by itself after a "Reply with the number" message is student_lookup (selection).
10. active_student_followup=true means: use the SAME student from history, don't search for a new one.

{history_block}

User query: "{query}"

Respond with ONLY a JSON object, no explanation:
{{"intent": "...", "expanded_query": "...", "student_name": null, "roll_no": null, "is_followup": false, "active_student_followup": false}}"""


class QueryUnderstander:

    def __init__(self, llm: OllamaClient) -> None:
        self.llm = llm

    async def understand(
        self, query: str, chat_history: list[dict] | None = None,
    ) -> dict:
        """
        Understand the user's query in context of chat history.

        Returns a structured dict with intent, expanded_query, student_name, etc.
        Falls back to heuristics if LLM fails.
        """
        # Fast-path: 13-digit roll number — no LLM needed
        roll_match = re.search(r'\b(\d{13})\b', query)
        if roll_match:
            return {
                "intent": "student_lookup",
                "expanded_query": query,
                "student_name": None,
                "roll_no": roll_match.group(1),
                "is_followup": False,
                "active_student_followup": False,
            }

        # Fast-path: single digit 1-9 after options list
        q_stripped = query.strip()
        if q_stripped.isdigit() and 1 <= int(q_stripped) <= 9 and chat_history:
            for msg in reversed(chat_history):
                if msg.get("role") == "assistant" and "Reply with the number" in msg.get("content", ""):
                    return {
                        "intent": "student_lookup",
                        "expanded_query": query,
                        "student_name": None,
                        "roll_no": None,
                        "is_followup": False,
                        "active_student_followup": False,
                        "selection": int(q_stripped),
                    }

        # Build history block for prompt
        history_block = self._format_history(chat_history)

        prompt = UNDERSTAND_PROMPT.format(
            query=query.replace('"', '\\"'),
            history_block=history_block,
        )

        try:
            response = await self.llm.generate(
                prompt=prompt,
                temperature=0.05,
                max_tokens=200,
                format="json",
                options={"temperature": 0.05},
            )
            result = self._parse_response(response, query)
            logger.info(
                "Query understood: intent=%s, student=%s, followup=%s, expanded='%s'",
                result["intent"], result.get("student_name"),
                result["is_followup"], result["expanded_query"][:60],
            )
            return result

        except Exception as exc:
            logger.warning("Query understanding failed (%s) — using fallback", exc)
            return self._fallback(query, chat_history)

    def _format_history(self, chat_history: list[dict] | None) -> str:
        if not chat_history:
            return "Chat history: (none — this is the first message)"

        lines = ["=== RECENT CHAT HISTORY ==="]
        # Last 4 messages max to keep prompt small
        recent = chat_history[-4:]
        for msg in recent:
            role = msg.get("role", "?")
            content = msg.get("content", "")[:200]
            # Include metadata hints for context
            meta = msg.get("metadata", {})
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except Exception:
                    meta = {}
            hint = ""
            if meta.get("current_student_name"):
                hint = f" [active student: {meta['current_student_name']}, roll: {meta.get('current_student_roll', '?')}]"
            elif meta.get("route_used"):
                hint = f" [route: {meta['route_used']}]"
            lines.append(f"{role}: {content}{hint}")

        return "\n".join(lines)

    def _parse_response(self, response: str, original_query: str) -> dict:
        """Parse LLM JSON response, with robust fallback."""
        text = response.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*\n?", "", text)
            text = re.sub(r"\n?```$", "", text)
        text = text.strip()

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            # Try to extract JSON from mixed output
            match = re.search(r'\{[^{}]+\}', text)
            if match:
                parsed = json.loads(match.group())
            else:
                return self._fallback(original_query, None)

        # Validate required fields
        result = {
            "intent": parsed.get("intent", "analytical"),
            "expanded_query": parsed.get("expanded_query", original_query),
            "student_name": parsed.get("student_name"),
            "roll_no": parsed.get("roll_no"),
            "is_followup": bool(parsed.get("is_followup", False)),
            "active_student_followup": bool(parsed.get("active_student_followup", False)),
        }

        # Validate intent
        valid_intents = {"student_lookup", "analytical", "followup", "concept"}
        if result["intent"] not in valid_intents:
            result["intent"] = "analytical"

        # Clean student name — strip noise the LLM might have left
        if result["student_name"]:
            name = result["student_name"].strip()
            name = re.sub(
                r"\s*(?:'s)?\s*(?:full|whole|complete|all|detailed)?\s*"
                r"(?:result|results|marks|details|profile|performance|semester)?\s*$",
                "", name, flags=re.IGNORECASE,
            ).strip()
            result["student_name"] = name if len(name) >= 2 else None

        # If no expanded_query, use original
        if not result["expanded_query"] or len(result["expanded_query"]) < 3:
            result["expanded_query"] = original_query

        return result

    def _fallback(self, query: str, chat_history: list[dict] | None) -> dict:
        """Heuristic fallback when LLM fails."""
        q_lower = query.lower().strip()

        # Check for student name patterns
        name_match = re.search(
            r"(?:result|marks|details|profile)\s+(?:of|for)\s+([a-z]+(?: [a-z]+){1,3})",
            q_lower,
        )
        if not name_match:
            name_match = re.search(
                r"^(?:show\s+(?:me\s+)?)?([a-z]+(?: [a-z]+){1,2})(?:'s)?\s+(?:full\s+)?(?:result|marks)",
                q_lower,
            )

        if name_match:
            name = name_match.group(1).strip()
            # Filter out common non-name words
            noise = {"full", "complete", "whole", "all", "the", "show", "me", "give", "tell"}
            name_words = [w for w in name.split() if w not in noise]
            if name_words:
                return {
                    "intent": "student_lookup",
                    "expanded_query": query,
                    "student_name": " ".join(name_words),
                    "roll_no": None,
                    "is_followup": False,
                    "active_student_followup": False,
                }

        # Concept questions
        concept_phrases = [
            "what is sgpa", "what is cgpa", "what does cp mean",
            "what is back paper", "grading system", "passing criteria",
        ]
        if any(p in q_lower for p in concept_phrases):
            return {
                "intent": "concept",
                "expanded_query": query,
                "student_name": None,
                "roll_no": None,
                "is_followup": False,
                "active_student_followup": False,
            }

        # Default: analytical
        return {
            "intent": "analytical",
            "expanded_query": query,
            "student_name": None,
            "roll_no": None,
            "is_followup": False,
            "active_student_followup": False,
        }
