"""
Master query orchestrator for KCCITM AI Assistant.

Routes queries to SQL / RAG / HYBRID pipelines and assembles a unified response.
This is the ONLY public interface for query processing from Phase 7 onward —
the API's /chat endpoint calls process_query() or process_query_stream().

Flow:
    1. Load session history (if session_id provided)
    2. Check cache (exact hash → semantic similarity)
    3. If cache hit → return instantly
    4. QueryRouter classifies query → SQL / RAG / HYBRID
    5a. SQL:    SQL pipeline → LLM summarizes results
    5b. RAG:    Milvus hybrid search → LLM generates from chunks
    5c. HYBRID: SQL + RAG run in parallel → LLM generates from merged context
    6. Store response in cache
    7. Save messages to session
    8. Return unified QueryResponse

Usage:
    orchestrator = Orchestrator(llm, router, sql_pipeline, rag_pipeline, milvus)
    response = await orchestrator.process_query("top 5 students by SGPA",
                                                session_id="...", user_id="...")
    print(response.response)
"""

import asyncio
import logging
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import AsyncGenerator, Optional

from config import settings
from core.cache import QueryCache, CacheHit
from core.context_builder import ContextBuilder
from core.llm_client import OllamaClient
from core.rag_pipeline import RAGPipeline, RAGResult, RESPONSE_GENERATOR_PROMPT
from core.router import QueryRouter, RouteResult
from core.session_manager import SessionManager
from core.sql_pipeline import SQLPipeline, SQLResult
from db.milvus_client import MilvusSearchClient

logger = logging.getLogger(__name__)


# ── Response model ────────────────────────────────────────────────────────────

@dataclass
class QueryResponse:
    """Unified response from the orchestrator."""
    success: bool
    response: str = ""
    route_used: str = ""
    sql_result: SQLResult | None = None
    rag_result: RAGResult | None = None
    route_result: RouteResult | None = None
    total_time_ms: float = 0.0
    token_usage: dict = field(default_factory=dict)
    error: str = ""
    metadata: dict = field(default_factory=dict)


# ── Orchestrator ──────────────────────────────────────────────────────────────

# ── Implicit signal tracker (per-session in-memory, lightweight) ──────────────

class _SignalTracker:
    """Lightweight per-session last-query/time tracker for implicit signal detection."""

    def __init__(self) -> None:
        self._data: dict[str, dict] = {}   # session_id → {query, ts, turn_count}

    def record(self, session_id: str, query: str) -> tuple[Optional[str], float, int]:
        """
        Store the current query and return (previous_query, time_gap_seconds, turn_count).
        """
        now = time.time()
        prev = self._data.get(session_id, {})
        prev_query = prev.get("query")
        prev_ts    = prev.get("ts", now)
        turn_count = prev.get("turn_count", 0) + 1
        self._data[session_id] = {"query": query, "ts": now, "turn_count": turn_count}
        return prev_query, (now - prev_ts), turn_count


_signal_tracker = _SignalTracker()


class Orchestrator:
    """
    Master query orchestrator — ties router → SQL/RAG/HYBRID → response together.

    Phase 6 additions:
    - Session-aware: loads chat history from SessionManager, saves messages after each turn.
    - Cache-aware: checks QueryCache before running any pipeline; stores hits after success.

    Resilience:
    - SQL failures automatically fall back to RAG.
    - SQL 0-row results attempt RAG before returning empty response.
    - All error paths return graceful QueryResponse (no crashes).

    HYBRID:
    - SQL and RAG retrieval run in parallel via asyncio.gather().
    - Critical for latency — sequential would roughly double response time.
    """

    def __init__(
        self,
        llm: OllamaClient,
        router: QueryRouter,
        sql_pipeline: SQLPipeline,
        rag_pipeline: RAGPipeline,
        milvus: MilvusSearchClient,
        session_manager: SessionManager = None,
        cache: QueryCache = None,
    ) -> None:
        self.llm = llm
        self.router = router
        self.sql_pipeline = sql_pipeline
        self.rag_pipeline = rag_pipeline
        self.milvus = milvus
        self.context_builder = ContextBuilder()
        self.session_manager = session_manager or SessionManager()
        self.cache = cache or QueryCache(llm)

    # ── Public interface ──────────────────────────────────────────────────────

    async def process_query(
        self,
        query: str,
        chat_history: list[dict] | None = None,
        session_id: str | None = None,
        user_id: str | None = None,
    ) -> QueryResponse:
        """
        Process a user query end-to-end.

        Args:
            query: Natural language question
            chat_history: Explicit history list (takes priority over session_id history)
            session_id: Session ID for loading/saving conversation history
            user_id: User ID (only used when creating a new session implicitly)

        Returns:
            QueryResponse with answer, route used, and all metadata
        """
        t0 = time.time()

        try:
            # Step 1: Load session history (if session_id provided and no explicit history)
            if session_id and chat_history is None:
                raw_history = await self.session_manager.get_chat_history(session_id)
                chat_history = await self.context_builder.trim_and_summarize_history(
                    raw_history, self.llm
                )

            # Step 2: Check cache
            cache_hit = await self.cache.check(query)
            if cache_hit:
                response = QueryResponse(
                    success=True,
                    response=cache_hit.response,
                    route_used=f"CACHED ({cache_hit.cache_type})",
                    total_time_ms=(time.time() - t0) * 1000,
                    metadata={
                        "cache_hit": True,
                        "cache_type": cache_hit.cache_type,
                        "cache_confidence": cache_hit.confidence,
                        "original_route": cache_hit.route_used,
                    },
                )
                if session_id:
                    await self._save_to_session(
                        session_id, query, cache_hit.response, response.metadata
                    )
                return response

            # Step 2.5: FAQ check (after cache, before routing)
            try:
                from core.faq_engine import FAQEngine
                faq_engine = FAQEngine(self.llm, self.milvus)
                faq_match = await faq_engine.check(query)
                if faq_match:
                    faq_response = QueryResponse(
                        success=True,
                        response=faq_match["answer"],
                        route_used="FAQ",
                        total_time_ms=(time.time() - t0) * 1000,
                        metadata={
                            "faq_id": faq_match["faq_id"],
                            "faq_confidence": faq_match["confidence"],
                        },
                    )
                    if session_id:
                        await self._save_to_session(
                            session_id, query, faq_match["answer"],
                            {"route_used": "FAQ", "faq_id": faq_match["faq_id"]},
                        )
                    return faq_response
            except Exception as exc:
                logger.debug("FAQ engine check failed (non-critical): %s", exc)

            # Step 2.8: Student lookup (bypasses router for direct student queries)
            student_lookup = self._detect_student_lookup(query, chat_history)
            if student_lookup:
                try:
                    identifier = student_lookup["identifier"]
                    # Handle selection from previous options list
                    if student_lookup["type"] == "selection":
                        sel = int(identifier)
                        for msg in reversed(chat_history or []):
                            if msg.get("role") == "assistant" and "Reply with the number" in msg.get("content", ""):
                                roll_nums = re.findall(
                                    r"\|\s*\d+\s*\|[^|]+\|\s*(\d{13})\s*\|",
                                    msg["content"],
                                )
                                if sel <= len(roll_nums):
                                    identifier = roll_nums[sel - 1]
                                break

                    result = await self.sql_pipeline.search_student(identifier)

                    if result["found"] == 0:
                        resp_text = f"No student found matching '{identifier}'. Try a different name, roll number, or batch year."
                    elif result["found"] > 1:
                        resp_text = self._format_student_options(result["students"])
                    else:
                        resp_text = self._format_student_full_result(result["detail"])

                    sl_response = QueryResponse(
                        success=True,
                        response=resp_text,
                        route_used="STUDENT_LOOKUP",
                        total_time_ms=(time.time() - t0) * 1000,
                        metadata={
                            "student_lookup": True,
                            "current_student_roll": result["detail"]["student"]["roll_no"] if result.get("detail") else None,
                            "current_student_name": result["detail"]["student"]["name"] if result.get("detail") else None,
                        },
                    )
                    if session_id:
                        await self._save_to_session(
                            session_id, query, resp_text, sl_response.metadata,
                        )
                    return sl_response
                except Exception as exc:
                    logger.warning("Student lookup failed, falling through to router: %s", exc)

            # Step 3: Route the query
            route_result = await self.router.route(query, chat_history)

            # Step 3.5: Rewrite short follow-up queries using chat history
            expanded_query = await self._expand_followup(query, chat_history)

            # Step 4: Execute the appropriate pipeline (use expanded query for pipelines)
            route = route_result.route
            if route == "SQL":
                response = await self._handle_sql(expanded_query, route_result, chat_history)
            elif route == "RAG":
                response = await self._handle_rag(expanded_query, route_result, chat_history)
            elif route == "HYBRID":
                response = await self._handle_hybrid(expanded_query, route_result, chat_history)
            else:
                route_result.route = "RAG"
                response = await self._handle_rag(expanded_query, route_result, chat_history)

            response.route_result = route_result
            response.route_used = response.route_used or route_result.route
            response.total_time_ms = (time.time() - t0) * 1000

            # Step 5: Store in cache (only successful non-empty responses)
            if response.success and response.response:
                await self.cache.store(
                    query=query,
                    response=response.response,
                    route_used=response.route_used,
                    metadata={
                        "sql_query": response.sql_result.sql if response.sql_result else None,
                        "chunk_count": response.rag_result.chunk_count if response.rag_result else 0,
                    },
                )

            # Step 6: Save to session
            if session_id:
                msg_metadata = {
                    "route_used": response.route_used,
                    "total_time_ms": response.total_time_ms,
                    "cache_hit": False,
                }
                if response.sql_result:
                    msg_metadata["sql_query"] = response.sql_result.sql
                    msg_metadata["sql_row_count"] = response.sql_result.row_count
                if response.rag_result:
                    msg_metadata["chunk_count"] = response.rag_result.chunk_count
                    msg_metadata["chunks_used"] = [
                        c.get("chunk_id") for c in (response.rag_result.chunks or [])
                    ]
                await self._save_to_session(
                    session_id, query, response.response, msg_metadata
                )

            # Step 7: Detect and record implicit signals
            if session_id and response.success:
                await self._detect_implicit_signals(session_id, query)

            return response

        except Exception as exc:
            logger.error("Orchestrator error: %s", exc, exc_info=True)
            return QueryResponse(
                success=False,
                error=f"Orchestrator error: {exc}",
                total_time_ms=(time.time() - t0) * 1000,
            )

    async def process_query_stream(
        self,
        query: str,
        chat_history: list[dict] | None = None,
        session_id: str | None = None,
    ) -> AsyncGenerator[str, None]:
        """
        Process query with streaming response.

        Cache hits are yielded as a single token (no streaming needed).
        Routing + retrieval happen first (non-streaming, fast).
        Only the final LLM generation is streamed — tokens yield as generated.
        """
        # Load session history
        if session_id and chat_history is None:
            raw_history = await self.session_manager.get_chat_history(session_id)
            chat_history = await self.context_builder.trim_and_summarize_history(
                raw_history, self.llm
            )

        # Check cache — return full text as single yield
        cache_hit = await self.cache.check(query)
        if cache_hit:
            yield cache_hit.response
            if session_id:
                await self._save_to_session(
                    session_id, query, cache_hit.response,
                    {"cache_hit": True, "cache_type": cache_hit.cache_type},
                )
            return

        # Student lookup (non-streaming, returns full text)
        student_lookup = self._detect_student_lookup(query, chat_history)
        if student_lookup:
            try:
                identifier = student_lookup["identifier"]
                if student_lookup["type"] == "selection":
                    sel = int(identifier)
                    for msg in reversed(chat_history or []):
                        if msg.get("role") == "assistant" and "Reply with the number" in msg.get("content", ""):
                            roll_nums = re.findall(r"\|\s*\d+\s*\|[^|]+\|\s*(\d{13})\s*\|", msg["content"])
                            if sel <= len(roll_nums):
                                identifier = roll_nums[sel - 1]
                            break
                result = await self.sql_pipeline.search_student(identifier)
                if result["found"] == 0:
                    resp_text = f"No student found matching '{identifier}'."
                elif result["found"] > 1:
                    resp_text = self._format_student_options(result["students"])
                else:
                    resp_text = self._format_student_full_result(result["detail"])
                yield resp_text
                if session_id:
                    await self._save_to_session(session_id, query, resp_text, {"route_used": "STUDENT_LOOKUP"})
                return
            except Exception as exc:
                logger.warning("Student lookup stream failed: %s", exc)

        # Route (non-streaming, fast)
        route_result = await self.router.route(query, chat_history)

        # Retrieve / execute (non-streaming)
        context, context_type = await self._build_stream_context(query, route_result)

        # Stream the LLM response
        system_prompt = await self._get_response_prompt()
        user_message = (
            f"Based on the following data, answer the user's question.\n\n"
            f"{context}\n\n"
            f"Question: {query}"
        )

        messages = [{"role": "system", "content": system_prompt}]
        if chat_history:
            for msg in chat_history[-10:]:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                if role == "assistant" and len(content) > 800:
                    content = content[:800] + "\n... (truncated)"
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": user_message})

        full_response = []
        async for token in self.llm.stream_chat(messages):
            full_response.append(token)
            yield token

        # Save to cache + session after stream completes
        response_text = "".join(full_response)
        if response_text:
            await self.cache.store(query, response_text, context_type)
            if session_id:
                await self._save_to_session(
                    session_id, query, response_text,
                    {"route_used": context_type, "cache_hit": False},
                )

    # ── Route handlers ────────────────────────────────────────────────────────

    async def _handle_sql(
        self,
        query: str,
        route_result: RouteResult,
        chat_history: list[dict] | None,
    ) -> QueryResponse:
        """
        SQL route: run SQL pipeline → summarize results in natural language.
        Falls back to RAG if SQL fails or returns 0 rows.
        """
        sql_result = await self.sql_pipeline.run(query, route_result)

        if not sql_result.success:
            logger.info("SQL failed (%s) — falling back to RAG", sql_result.error)
            rag_result = await self.rag_pipeline.run(query, route_result, chat_history)
            return QueryResponse(
                success=rag_result.success,
                response=rag_result.response,
                route_used="RAG (SQL fallback)",
                sql_result=sql_result,
                rag_result=rag_result,
                error=rag_result.error if not rag_result.success else "",
                metadata={"fallback": True, "sql_error": sql_result.error},
            )

        if sql_result.row_count == 0:
            logger.info("SQL returned 0 rows — attempting RAG for context")
            rag_result = await self.rag_pipeline.run(query, route_result, chat_history)
            if rag_result.success and rag_result.chunk_count > 0:
                return QueryResponse(
                    success=True,
                    response=rag_result.response,
                    route_used="RAG (SQL empty)",
                    sql_result=sql_result,
                    rag_result=rag_result,
                    metadata={"fallback": True, "reason": "SQL returned 0 rows"},
                )
            return QueryResponse(
                success=True,
                response=(
                    "I couldn't find any matching data for your query. "
                    "The SQL query executed successfully but returned no results. "
                    "Could you try rephrasing or broadening your search?"
                ),
                route_used="SQL",
                sql_result=sql_result,
                metadata={"empty_result": True},
            )

        sql_context = self.context_builder.build_sql_context(sql_result)
        response_text = await self._generate_sql_summary(query, sql_context, chat_history)

        return QueryResponse(
            success=True,
            response=response_text,
            route_used="SQL",
            sql_result=sql_result,
        )

    async def _handle_rag(
        self,
        query: str,
        route_result: RouteResult,
        chat_history: list[dict] | None,
    ) -> QueryResponse:
        """RAG route: Milvus hybrid search → context assembly → LLM generation."""
        rag_result = await self.rag_pipeline.run(query, route_result, chat_history)
        return QueryResponse(
            success=rag_result.success,
            response=rag_result.response,
            route_used="RAG",
            rag_result=rag_result,
            error=rag_result.error if not rag_result.success else "",
        )

    async def _handle_hybrid(
        self,
        query: str,
        route_result: RouteResult,
        chat_history: list[dict] | None,
    ) -> QueryResponse:
        """HYBRID route: SQL and RAG run in parallel → merge contexts → LLM generation."""
        sql_task = asyncio.create_task(self.sql_pipeline.run(query, route_result))
        rag_task = asyncio.create_task(
            self.rag_pipeline.retrieve_only(query, route_result)
        )
        sql_result, rag_chunks = await asyncio.gather(sql_task, rag_task)

        sql_context = ""
        if sql_result.success and sql_result.row_count > 0:
            sql_context = self.context_builder.build_sql_context(sql_result)

        rag_context = ""
        if rag_chunks:
            rag_context = self.context_builder.build_rag_context(rag_chunks)

        combined_context = f"{sql_context}\n\n{rag_context}".strip() if (sql_context or rag_context) else ""

        if not combined_context:
            return QueryResponse(
                success=True,
                response=(
                    "I couldn't find sufficient data to answer this question "
                    "from either the database or the knowledge base."
                ),
                route_used="HYBRID",
                sql_result=sql_result,
            )

        response_text = await self._generate_hybrid_response(
            query, combined_context, chat_history
        )

        rag_result = RAGResult(
            success=True,
            chunks=rag_chunks or [],
            chunk_count=len(rag_chunks or []),
            context_text=rag_context,
            response=response_text,
        )

        return QueryResponse(
            success=True,
            response=response_text,
            route_used="HYBRID",
            sql_result=sql_result,
            rag_result=rag_result,
        )

    # ── Follow-up query expansion ──────────────────────────────────────────────

    # ── Student lookup helpers ─────────────────────────────────────────────────

    def _detect_student_lookup(
        self, query: str, chat_history: list[dict] | None = None,
    ) -> dict | None:
        """Detect if query is a student lookup. Returns dict or None."""
        q = query.strip()

        # Selection number (user picked from options list)
        if q.isdigit() and 1 <= int(q) <= 9 and chat_history:
            for msg in reversed(chat_history or []):
                if msg.get("role") == "assistant" and "Reply with the number" in msg.get("content", ""):
                    return {"type": "selection", "identifier": q}

        # Roll number (13 digits anywhere in query)
        roll_match = re.search(r"\b(\d{13})\b", q)
        if roll_match:
            return {"type": "roll_no", "identifier": roll_match.group(1)}

        # Batch year — only for simple batch listing, not analytical queries
        # Skip if query contains analytical keywords like compare, percentage, rate, average, etc.
        batch_match = re.search(r"\bbatch\s*(\d{4})\b", q, re.IGNORECASE)
        if batch_match:
            analytical_words = {"compare", "percentage", "percent", "rate", "average", "avg",
                                "top", "bottom", "rank", "count", "how many", "total", "between",
                                "highest", "lowest", "best", "worst", "subject", "grade", "fail",
                                "pass", "improve", "trend", "sgpa", "cgpa", "vs"}
            q_lower = q.lower()
            if not any(w in q_lower for w in analytical_words):
                return {"type": "batch", "identifier": batch_match.group(1)}

        # Name-based triggers
        triggers = [
            "tell me about", "show results for", "results of", "details of",
            "results for", "about student", "student profile", "profile of",
            "marks of", "performance of", "show me",
        ]
        q_lower = q.lower()
        for trigger in triggers:
            if q_lower.startswith(trigger):
                name = q[len(trigger):].strip().strip("?.,")
                if name and len(name) > 2:
                    return {"type": "name", "identifier": name}

        # Direct name (2-4 words, each starting uppercase, no common query words)
        words = q.split()
        skip_words = {"how", "what", "which", "top", "list", "show", "compare", "average", "count"}
        if 2 <= len(words) <= 4 and words[0].lower() not in skip_words:
            if all(w[0].isupper() for w in words if w):
                return {"type": "name", "identifier": q}

        return None

    def _format_student_options(self, students: list[dict]) -> str:
        """Format multiple student matches as numbered options."""
        lines = [f"Found {len(students)} students matching your search:\n"]
        lines.append("| # | Name | Roll No | Branch | Batch |")
        lines.append("|---|------|---------|--------|-------|")
        for i, s in enumerate(students, 1):
            batch = "20" + s["roll_no"][:2] if s.get("roll_no") else "—"
            branch = s.get("branch", "—")
            if len(branch) > 40:
                branch = branch[:37] + "..."
            lines.append(
                f"| {i} | {s.get('name', '—')} | {s.get('roll_no', '—')} | {branch} | {batch} |"
            )
        lines.append("\nReply with the number (1, 2, 3...) to see full results.")
        return "\n".join(lines)

    def _format_student_full_result(self, detail: dict) -> str:
        """Format complete student results in structured markdown tables."""
        s = detail["student"]
        semesters = detail["semesters"]
        subjects = detail["subjects"]

        lines: list[str] = []

        # Student details
        lines.append("## Student details\n")
        lines.append("| Field | Value |")
        lines.append("|-------|-------|")
        lines.append(f"| Name | {s.get('name', '—')} |")
        lines.append(f"| Roll No | {s.get('roll_no', '—')} |")
        lines.append(f"| Branch | {s.get('branch', '—')} |")
        lines.append(f"| Course | {s.get('course', '—')} |")
        lines.append(f"| Enrollment | {s.get('enrollment', '—')} |")
        lines.append(f"| Father's Name | {s.get('father_name', '—')} |")
        lines.append(f"| Gender | {s.get('gender', '—')} |")

        # Semester SGPA table
        if semesters:
            lines.append("\n## Semester-wise SGPA\n")
            lines.append("| Semester | Session | SGPA | Total Marks | Status | Subjects |")
            lines.append("|----------|---------|------|-------------|--------|----------|")
            for sem in semesters:
                lines.append(
                    f"| {sem.get('semester', '—')} "
                    f"| {sem.get('session', '—')} "
                    f"| {sem.get('sgpa', '—')} "
                    f"| {sem.get('total_marks', '—')} "
                    f"| {sem.get('result_status', '—')} "
                    f"| {sem.get('total_subjects', '—')} |"
                )

        # Subject marks grouped by semester
        by_sem: dict[int, list[dict]] = defaultdict(list)
        for subj in subjects:
            by_sem[subj["semester"]].append(subj)

        for sem_num in sorted(by_sem.keys()):
            sem_info = next((s for s in semesters if s["semester"] == sem_num), {})
            session = sem_info.get("session", "")
            lines.append(f"\n## Semester {sem_num} — Subject marks ({session})\n")
            lines.append("| Code | Subject | Type | Internal | External | Total | Grade | Back Paper |")
            lines.append("|------|---------|------|----------|----------|-------|-------|------------|")
            for subj in by_sem[sem_num]:
                internal = subj.get("internal_marks")
                external = subj.get("external_marks")
                if internal is not None and external is not None:
                    total = int(internal) + int(external)
                elif internal is not None:
                    total = internal
                elif external is not None:
                    total = external
                else:
                    total = "—"
                lines.append(
                    f"| {subj.get('subject_code', '—')} "
                    f"| {subj.get('subject_name', '—')} "
                    f"| {subj.get('type', '—')} "
                    f"| {internal if internal is not None else '—'} "
                    f"| {external if external is not None else '—'} "
                    f"| {total} "
                    f"| {subj.get('grade', '—')} "
                    f"| {subj.get('back_paper', '—')} |"
                )

        lines.append("\nFollow-up: Ask about a specific semester, subject, or compare with another student.")
        return "\n".join(lines)

    async def _expand_followup(
        self,
        query: str,
        chat_history: list[dict] | None,
    ) -> str:
        """Rewrite short follow-up queries into full standalone queries.

        Example: if history has "top 5 students by SGPA in semester 1" and
        the new query is "what about semester 4?", returns
        "top 5 students by SGPA in semester 4".

        Only rewrites if the query is short (<10 words) and history exists.
        Returns the original query if no rewriting is needed.
        """
        if not chat_history or len(query.split()) > 12:
            return query

        # Find the last user message in history
        last_user_msg = None
        for msg in reversed(chat_history):
            if msg.get("role") == "user":
                last_user_msg = msg.get("content", "")
                break

        if not last_user_msg:
            return query

        # Only rewrite if this looks like a follow-up (short, referential)
        followup_indicators = [
            "what about", "how about", "and for", "show me", "same for",
            "and the", "now for", "what if", "try", "do the same",
            "bottom", "instead", "also", "same but",
        ]
        q_lower = query.lower().strip()
        is_followup = any(q_lower.startswith(ind) for ind in followup_indicators)
        if not is_followup and len(query.split()) > 6:
            return query

        # Use LLM to rewrite
        try:
            rewrite_prompt = (
                f"The user previously asked: \"{last_user_msg}\"\n"
                f"Now they say: \"{query}\"\n\n"
                f"Rewrite this as a complete standalone question. "
                f"Keep the same intent as the previous query but apply the new parameters.\n"
                f"Reply with ONLY the rewritten question, nothing else."
            )
            expanded = await self.llm.generate(
                prompt=rewrite_prompt,
                temperature=0.10,
                max_tokens=100,
                options={"temperature": 0.10},
            )
            expanded = expanded.strip().strip('"').strip("'")
            if expanded and len(expanded) > 5:
                logger.info("Follow-up expanded: '%s' → '%s'", query, expanded)
                return expanded
        except Exception as exc:
            logger.debug("Follow-up expansion failed: %s", exc)

        return query

    # ── Implicit signal helpers ───────────────────────────────────────────────

    async def _detect_implicit_signals(self, session_id: str, query: str) -> None:
        """Detect and persist implicit quality signals for this turn."""
        try:
            from adaptive.feedback_collector import (
                detect_implicit_signals,
                record_implicit_signal,
            )
            prev_query, time_gap, turn_count = _signal_tracker.record(session_id, query)
            signals = detect_implicit_signals(query, prev_query, time_gap, turn_count)
            for signal in signals:
                await record_implicit_signal(
                    session_id=session_id,
                    signal_type=signal,
                    original_query=prev_query or "",
                    follow_up_query=query,
                    time_gap_seconds=time_gap,
                )
        except Exception as exc:
            logger.debug("Implicit signal detection failed: %s", exc)

    # ── Session helpers ───────────────────────────────────────────────────────

    async def _save_to_session(
        self,
        session_id: str,
        query: str,
        response: str,
        metadata: dict,
    ) -> None:
        """Save user query and assistant response to the session."""
        try:
            await self.session_manager.add_message(session_id, "user", query)
            await self.session_manager.add_message(session_id, "assistant", response, metadata)
        except Exception as exc:
            logger.warning("Failed to save to session %s: %s", session_id, exc)

    # ── LLM generation helpers ────────────────────────────────────────────────

    async def _get_response_prompt(self) -> str:
        """Load response generator prompt from prompts.db, fallback to hardcoded."""
        try:
            from db.sqlite_client import fetch_one
            row = await fetch_one(
                settings.PROMPTS_DB,
                "SELECT content FROM prompt_templates "
                "WHERE prompt_name = ? AND section_name = ? AND is_active = 1",
                ("response_generator", "system"),
            )
            if row and row.get("content"):
                return row["content"]
        except Exception:
            pass
        return RESPONSE_GENERATOR_PROMPT

    async def _generate_sql_summary(
        self,
        query: str,
        sql_context: str,
        chat_history: list[dict] | None,
    ) -> str:
        base_prompt = await self._get_response_prompt()
        system_prompt = base_prompt
        prompt = (
            f"Based on the following database query results, "
            f"answer the user's question in natural language.\n\n"
            f"{sql_context}\n\n"
            f"Question: {query}"
        )
        return await self._chat_or_generate(prompt, system_prompt, chat_history)

    async def _generate_hybrid_response(
        self,
        query: str,
        combined_context: str,
        chat_history: list[dict] | None,
    ) -> str:
        system_prompt = await self._get_response_prompt()
        prompt = (
            f"Based on the following data (from both database queries and student records), "
            f"provide a comprehensive answer.\n\n"
            f"{combined_context}\n\n"
            f"Question: {query}"
        )
        return await self._chat_or_generate(prompt, system_prompt, chat_history)

    async def _chat_or_generate(
        self,
        prompt: str,
        system_prompt: str,
        chat_history: list[dict] | None,
    ) -> str:
        """Use chat() with history or generate() without.

        Builds a conversation that gives the LLM full awareness of prior
        questions AND their answers so follow-up queries work correctly.
        """
        if chat_history:
            messages = [{"role": "system", "content": system_prompt}]

            # Include recent history pairs so the LLM can reference prior answers
            recent = chat_history[-10:]  # last 5 pairs (user+assistant)
            for msg in recent:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                # Truncate long assistant responses to save context space
                if role == "assistant" and len(content) > 800:
                    content = content[:800] + "\n... (truncated)"
                messages.append({"role": role, "content": content})

            # Append the current data-enriched prompt
            messages.append({"role": "user", "content": prompt})
            return await self.llm.chat(messages)
        return await self.llm.generate(
            prompt=prompt,
            system=system_prompt,
            max_tokens=settings.LLM_MAX_TOKENS,
            temperature=0.25,
            options={"temperature": 0.25},
        )

    # ── Streaming context builder ─────────────────────────────────────────────

    async def _build_stream_context(
        self,
        query: str,
        route_result: RouteResult,
    ) -> tuple[str, str]:
        """Build context string for streaming mode. Returns (context_text, context_type)."""
        filters = route_result.filters if route_result.needs_filter else None

        if route_result.route == "SQL":
            sql_result = await self.sql_pipeline.run(query, route_result)
            if sql_result.success and sql_result.row_count > 0:
                return self.context_builder.build_sql_context(sql_result), "SQL"
            query_embedding = await self.llm.embed(query)
            chunks = self.milvus.hybrid_search(
                query, query_embedding, k=settings.RAG_TOP_K, filters=filters
            )
            return self.context_builder.build_rag_context(chunks), "RAG"

        if route_result.route == "RAG":
            query_embedding = await self.llm.embed(query)
            chunks = self.milvus.hybrid_search(
                query, query_embedding, k=settings.RAG_TOP_K, filters=filters
            )
            return self.context_builder.build_rag_context(chunks), "RAG"

        # HYBRID — SQL and embed in parallel
        sql_task = asyncio.create_task(self.sql_pipeline.run(query, route_result))
        embed_task = asyncio.create_task(self.llm.embed(query))
        sql_result, query_embedding = await asyncio.gather(sql_task, embed_task)

        chunks = self.milvus.hybrid_search(
            query, query_embedding, k=settings.RAG_TOP_K, filters=filters
        )
        sql_ctx = self.context_builder.build_sql_context(sql_result) if sql_result.success else ""
        rag_ctx = self.context_builder.build_rag_context(chunks) if chunks else ""
        return f"{sql_ctx}\n\n{rag_ctx}".strip(), "HYBRID"
