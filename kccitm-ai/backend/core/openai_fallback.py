"""
OpenAI fallback for general knowledge queries.

SAFETY RULES:
- NEVER receives student data, SQL results, names, marks, or session context.
- Only receives the raw user query string.
- Only called when ALL local pipelines (cache, SQL, student lookup, local RAG) failed.
- Disabled by default (OPENAI_ENABLED = False).
"""

import logging
from typing import Optional

from config import settings

logger = logging.getLogger(__name__)


async def ask_openai(query: str) -> Optional[str]:
    """
    Send ONLY the raw query to OpenAI. Never send student data.
    Returns the response string, or None if disabled/failed.
    """
    if not settings.OPENAI_ENABLED or not settings.OPENAI_API_KEY:
        return None

    try:
        import httpx

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": settings.OPENAI_MODEL,
                    "max_tokens": settings.OPENAI_MAX_TOKENS,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "You are a helpful assistant for AKTU (Dr. A.P.J. Abdul Kalam "
                                "Technical University) and KCCITM (KCC Institute of Technology "
                                "and Management, Greater Noida). Answer questions about AKTU rules, "
                                "regulations, syllabus, notifications, exam patterns, and academic "
                                "policies. Be concise and factual. If you don't know something "
                                "specific, say so."
                            ),
                        },
                        {"role": "user", "content": query},
                    ],
                    "temperature": 0.3,
                },
            )

            if resp.status_code != 200:
                logger.warning("OpenAI fallback: HTTP %s — %s", resp.status_code, resp.text[:100])
                return None

            data = resp.json()
            content = data["choices"][0]["message"]["content"].strip()
            logger.info("OpenAI fallback used for: %s", query[:50])
            return content

    except Exception as exc:
        logger.warning("OpenAI fallback failed: %s", exc)
        return None


def is_general_knowledge_query(query: str) -> bool:
    """
    Return True if the query is general/AKTU knowledge (NOT student-specific data).

    General: AKTU rules, syllabus questions, exam policies, academic advice.
    NOT general: student names, roll numbers, SGPA, marks, semester results.
    """
    q = query.lower()

    # Must NOT be about specific student data
    student_signals = [
        "sgpa", "cgpa", "marks", "topper", "top 5", "top 10",
        "roll", "failed", "passed", "semester result",
        "how many students", "pass rate", "fail rate", "back paper",
        "batch 20", "student name",
    ]
    if any(s in q for s in student_signals):
        return False

    # General knowledge signals
    general_signals = [
        "aktu", "regulation", "ordinance", "notification",
        "exam rule", "academic calendar", "curriculum", "credit system",
        "grading policy", "attendance", "eligibility", "admission",
        "placement", "fee structure", "scholarship", "hostel",
        "what is", "explain", "define", "how does",
        "tell me about aktu", "difference between",
        "meaning of", "advantages of", "types of",
        "syllabus for", "course structure",
    ]
    return any(s in q for s in general_signals)
