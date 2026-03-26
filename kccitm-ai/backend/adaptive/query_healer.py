"""
Query healer — retries failed queries with modified strategies.

Healing strategies
------------------
no_data / sql_error → widen_sql_filters
    Retry with loosened filters (remove batch/semester constraints) or
    fall back to RAG-only execution.

incomplete / off_topic → expand_query
    Use the LLM to rewrite the query with more context, then rerun through
    the full pipeline.

hallucination → grounded_rag
    Force RAG route with stricter top_k and return only grounded chunks;
    prepend a "only use the provided context" system note.

The healer is called automatically from the feedback route when
quality_score < 0.35 (severe failures). For moderate failures (0.35-0.50),
it is scheduled via APScheduler in Phase 9.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Circular-import-safe — import orchestrator lazily inside functions.


async def heal_query(
    query: str,
    failure_category: str,
    original_response: str,
    session_id: Optional[str] = None,
) -> Optional[str]:
    """
    Attempt a healed response for a failed query.

    Returns the healed response string, or None if healing failed / was skipped.
    """
    logger.info("Healing query (category=%s): %.60s…", failure_category, query)

    if failure_category in ("no_data", "sql_error"):
        return await _heal_widen_filters(query, session_id)

    if failure_category in ("incomplete", "off_topic"):
        return await _heal_expand_query(query, session_id)

    if failure_category == "hallucination":
        return await _heal_grounded_rag(query, session_id)

    # Generic: re-run as RAG
    return await _heal_generic_rag(query, session_id)


# ── Private healing strategies ────────────────────────────────────────────────

async def _heal_widen_filters(query: str, session_id: Optional[str]) -> Optional[str]:
    """Force RAG route, bypassing SQL to avoid empty-result failures."""
    try:
        from api.deps import get_orchestrator
        from core.router import RouteResult

        orchestrator = get_orchestrator()
        # Build a forced RAG route result with no filters
        route_result = RouteResult(
            route="RAG",
            confidence=0.7,
            reasoning="healer: widen_filters — forced RAG",
            filters={},
            needs_filter=False,
        )
        rag_result = await orchestrator.rag_pipeline.run(query, route_result, None)
        if rag_result.success and rag_result.response:
            return rag_result.response
    except Exception as exc:
        logger.warning("widen_filters healing failed: %s", exc)
    return None


async def _heal_expand_query(query: str, session_id: Optional[str]) -> Optional[str]:
    """Ask the LLM to rewrite the query with more context, then rerun."""
    try:
        from api.deps import get_llm, get_orchestrator

        llm = get_llm()
        expand_prompt = (
            "You are a query rewriter. The following student/academic query returned "
            "an incomplete or off-topic answer. Rewrite it to be more specific and "
            "self-contained, adding implied context. Return ONLY the rewritten query "
            "with no explanation.\n\nOriginal query: " + query
        )
        rewritten = await llm.generate(expand_prompt, max_tokens=120)
        if not rewritten or len(rewritten) < 5:
            return None

        orchestrator = get_orchestrator()
        response = await orchestrator.process_query(
            rewritten.strip(), session_id=session_id
        )
        if response.success and response.response:
            return response.response
    except Exception as exc:
        logger.warning("expand_query healing failed: %s", exc)
    return None


async def _heal_grounded_rag(query: str, session_id: Optional[str]) -> Optional[str]:
    """Run RAG with a strict grounding system prompt to suppress hallucination."""
    try:
        from api.deps import get_orchestrator
        from core.router import RouteResult

        orchestrator = get_orchestrator()
        route_result = RouteResult(
            route="RAG",
            confidence=0.8,
            reasoning="healer: grounded_rag",
            filters={},
            needs_filter=False,
        )
        rag_result = await orchestrator.rag_pipeline.run(query, route_result, None)
        if rag_result.success and rag_result.chunk_count > 0:
            # Build a grounded answer using only the retrieved chunks
            context = "\n\n".join(
                c.get("content", "") for c in (rag_result.chunks or [])
            )
            if not context.strip():
                return None
            from api.deps import get_llm
            llm = get_llm()
            grounded_prompt = (
                "Using ONLY the context below, answer the question. "
                "If the context does not contain the answer, say 'I don't have "
                "enough information in the knowledge base to answer this.'\n\n"
                f"Context:\n{context}\n\nQuestion: {query}"
            )
            return await llm.generate(grounded_prompt, max_tokens=512)
    except Exception as exc:
        logger.warning("grounded_rag healing failed: %s", exc)
    return None


async def _heal_generic_rag(query: str, session_id: Optional[str]) -> Optional[str]:
    """Fallback: just re-run as RAG."""
    try:
        from api.deps import get_orchestrator
        from core.router import RouteResult

        orchestrator = get_orchestrator()
        route_result = RouteResult(
            route="RAG",
            confidence=0.6,
            reasoning="healer: generic_rag fallback",
            filters={},
            needs_filter=False,
        )
        rag_result = await orchestrator.rag_pipeline.run(query, route_result, None)
        if rag_result.success and rag_result.response:
            return rag_result.response
    except Exception as exc:
        logger.warning("generic_rag healing failed: %s", exc)
    return None
