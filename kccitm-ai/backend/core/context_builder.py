"""
Token budget manager for KCCITM AI Assistant.

Manages the 32K context window, ensuring we never overflow the LLM's context
limit by tracking token usage across system prompt, chat history, RAG chunks,
and SQL results.

Usage:
    from core.context_builder import ContextBuilder
    cb = ContextBuilder()
    rag_ctx = cb.build_rag_context(chunks)
    sql_ctx = cb.build_sql_context(sql_result)
    usage = cb.estimate_total_usage(system_prompt, history, rag_ctx, sql_ctx, query)
"""

import logging

import tiktoken

logger = logging.getLogger(__name__)


class ContextBuilder:
    """
    Manages the LLM context window budget (32K tokens for qwen2.5:7b-instruct).

    Budget allocation:
    - System prompt:     ~800 tokens
    - Chat history:      ~3000 tokens (sliding window)
    - History summary:   ~500 tokens  (condensed older messages, Phase 6)
    - RAG context:       ~4000 tokens (retrieved chunks; ~2500 after Phase 5 compression)
    - SQL results:       ~1000 tokens
    - Generation buffer: ~2500 tokens (space for LLM output)
    - Total used budget: ~11800 tokens, ~20K headroom
    """

    MAX_CONTEXT_TOKENS = 32000
    SYSTEM_PROMPT_BUDGET = 800
    CHAT_HISTORY_BUDGET = 3000
    HISTORY_SUMMARY_BUDGET = 500
    RAG_CONTEXT_BUDGET = 4000
    SQL_RESULTS_BUDGET = 1000
    GENERATION_BUFFER = 2500

    def __init__(self) -> None:
        # cl100k_base is a close approximation; Qwen uses a similar BPE vocabulary.
        # Within ~10% accuracy — budgets are conservative enough that this is fine.
        self.encoder = tiktoken.get_encoding("cl100k_base")

    # ── Token counting ────────────────────────────────────────────────────────

    def count_tokens(self, text: str) -> int:
        """Count tokens in a text string."""
        if not text:
            return 0
        return len(self.encoder.encode(text))

    def truncate_to_budget(self, text: str, max_tokens: int) -> str:
        """Truncate text to fit within a token budget."""
        tokens = self.encoder.encode(text)
        if len(tokens) <= max_tokens:
            return text
        truncated = tokens[:max_tokens]
        return self.encoder.decode(truncated) + "\n... (truncated)"

    # ── Context builders ──────────────────────────────────────────────────────

    def build_rag_context(
        self,
        chunks: list[dict],
        max_tokens: int | None = None,
    ) -> str:
        """
        Build RAG context from retrieved chunks, fitting within budget.

        Adds chunks one by one until the token budget is exhausted.
        Each chunk gets a metadata header line + the chunk text.

        Returns the assembled context string.
        """
        max_tokens = max_tokens or self.RAG_CONTEXT_BUDGET
        header = f"RETRIEVED STUDENT DATA ({len(chunks)} records):\n"
        total_tokens = self.count_tokens(header)
        parts = [header]

        for i, chunk in enumerate(chunks):
            meta = chunk.get("metadata", {})
            entry_header = (
                f"[{i + 1}] Student: {meta.get('name', 'N/A')} | "
                f"Roll: {meta.get('roll_no', 'N/A')} | "
                f"Branch: {meta.get('branch', 'N/A')} | "
                f"Sem: {meta.get('semester', 'N/A')} | "
                f"SGPA: {meta.get('sgpa', 'N/A')}"
            )
            entry_text = chunk.get("text", "")
            entry = f"\n{entry_header}\n{entry_text}\n"
            entry_tokens = self.count_tokens(entry)

            if total_tokens + entry_tokens > max_tokens:
                remaining = len(chunks) - i
                parts.append(
                    f"\n... and {remaining} more relevant records (truncated for context length)"
                )
                break

            parts.append(entry)
            total_tokens += entry_tokens

        return "".join(parts)

    def build_sql_context(self, sql_result) -> str:
        """
        Build SQL result context, fitting within budget.

        Args:
            sql_result: SQLResult from the SQL pipeline

        Returns:
            Formatted SQL context string, or empty string on failure.
        """
        if not sql_result or not sql_result.success:
            return ""

        parts = ["SQL QUERY RESULTS:\n"]
        parts.append(f"Query: {sql_result.sql}\n")
        if sql_result.explanation:
            parts.append(f"Explanation: {sql_result.explanation}\n")

        if sql_result.formatted_table:
            table_text = sql_result.formatted_table
            if self.count_tokens(table_text) > self.SQL_RESULTS_BUDGET:
                table_text = self.truncate_to_budget(table_text, self.SQL_RESULTS_BUDGET)
            parts.append(f"\n{table_text}\n")
        elif sql_result.formatted_text:
            text = sql_result.formatted_text
            if self.count_tokens(text) > self.SQL_RESULTS_BUDGET:
                text = self.truncate_to_budget(text, self.SQL_RESULTS_BUDGET)
            parts.append(f"\n{text}\n")

        return "".join(parts)

    # ── Chat history trimming ─────────────────────────────────────────────────

    def trim_chat_history(
        self,
        messages: list[dict],
        max_tokens: int | None = None,
    ) -> list[dict]:
        """
        Trim chat history to fit within budget using a sliding window.

        Strategy:
        - Always keep the last 8 messages (4 user+assistant pairs)
        - If history exceeds budget, drop oldest messages
        - Phase 6 will enhance this with summarization instead of dropping

        Returns the trimmed message list.
        """
        max_tokens = max_tokens or self.CHAT_HISTORY_BUDGET
        if not messages:
            return []

        keep_last = 8
        if len(messages) <= keep_last:
            return messages

        recent = messages[-keep_last:]
        recent_tokens = sum(self.count_tokens(m.get("content", "")) for m in recent)

        if recent_tokens >= max_tokens:
            # Recent messages alone exceed budget — trim from the front
            trimmed: list[dict] = []
            token_count = 0
            for msg in reversed(recent):
                msg_tokens = self.count_tokens(msg.get("content", ""))
                if token_count + msg_tokens > max_tokens:
                    break
                trimmed.insert(0, msg)
                token_count += msg_tokens
            return trimmed

        # Budget remaining after recent messages — include older messages if possible
        remaining_budget = max_tokens - recent_tokens
        older = messages[:-keep_last]
        included_older: list[dict] = []

        for msg in reversed(older):
            msg_tokens = self.count_tokens(msg.get("content", ""))
            if remaining_budget - msg_tokens < 0:
                break
            included_older.insert(0, msg)
            remaining_budget -= msg_tokens

        return included_older + recent

    # ── Budget estimation ─────────────────────────────────────────────────────

    def estimate_total_usage(
        self,
        system_prompt: str,
        chat_history: list[dict],
        rag_context: str,
        sql_context: str,
        query: str,
    ) -> dict:
        """
        Estimate total token usage across all context components.

        Returns a dict with per-component counts, total, and headroom.
        Useful for debugging and monitoring context overflow.
        """
        system_tokens = self.count_tokens(system_prompt)
        history_tokens = sum(
            self.count_tokens(m.get("content", "")) for m in (chat_history or [])
        )
        rag_tokens = self.count_tokens(rag_context)
        sql_tokens = self.count_tokens(sql_context)
        query_tokens = self.count_tokens(query)
        total = system_tokens + history_tokens + rag_tokens + sql_tokens + query_tokens

        return {
            "system_prompt": system_tokens,
            "chat_history": history_tokens,
            "rag_context": rag_tokens,
            "sql_context": sql_tokens,
            "query": query_tokens,
            "total_input": total,
            "remaining_for_generation": self.MAX_CONTEXT_TOKENS - total,
            "within_budget": total < (self.MAX_CONTEXT_TOKENS - self.GENERATION_BUFFER),
        }

    # ── Sliding window with summarization (Phase 6) ───────────────────────────

    async def trim_and_summarize_history(
        self,
        messages: list[dict],
        llm,
        max_tokens: int = None,
    ) -> list[dict]:
        """
        Trim chat history with intelligent summarization.

        Strategy:
        1. If total tokens fit within budget → return as-is.
        2. Always keep last 8 messages (4 pairs) verbatim.
        3. If older messages exist and budget is exceeded:
           a. Summarize older messages into a single system message.
           b. Prepend the summary to the recent messages.

        The summary preserves: entity names, roll numbers, filters,
        established facts, and conversation trajectory.

        Args:
            messages: Full message history [{role, content}, ...]
            llm: OllamaClient for summarization
            max_tokens: Budget for history (default: CHAT_HISTORY_BUDGET)

        Returns:
            Trimmed message list, potentially with a summary prepended.
        """
        max_tokens = max_tokens or self.CHAT_HISTORY_BUDGET

        if not messages:
            return []

        total_tokens = sum(self.count_tokens(m.get("content", "")) for m in messages)
        if total_tokens <= max_tokens:
            return messages

        keep_last = 8
        recent = messages[-keep_last:]
        older = messages[:-keep_last]

        if not older:
            return self.trim_chat_history(recent, max_tokens)

        recent_tokens = sum(self.count_tokens(m.get("content", "")) for m in recent)
        summary_budget = min(max_tokens - recent_tokens, self.HISTORY_SUMMARY_BUDGET)

        if summary_budget <= 50:
            return recent

        summary = await self._summarize_messages(older, llm, summary_budget)
        if summary:
            summary_msg = {
                "role": "system",
                "content": f"[Summary of earlier conversation: {summary}]",
            }
            return [summary_msg] + recent

        return recent

    async def _summarize_messages(
        self,
        messages: list[dict],
        llm,
        max_tokens: int,
    ) -> str:
        """
        Summarize older messages into a concise context paragraph.

        Preserves: student names, roll numbers, branches, semesters,
        key data points, filters, and conversation direction.
        """
        conversation = []
        for msg in messages:
            role = msg.get("role", "user").capitalize()
            content = msg.get("content", "")[:300]
            conversation.append(f"{role}: {content}")

        conversation_text = "\n".join(conversation)

        prompt = f"""Summarize this earlier conversation about student academic data in 2-3 sentences.
Preserve: any student names, roll numbers, branches, semesters, key data points, and established criteria.

Conversation:
{conversation_text}

Concise summary:"""

        try:
            summary = await llm.generate(
                prompt=prompt,
                temperature=0.1,
                max_tokens=min(max_tokens, 200),
            )
            return summary.strip()
        except Exception:
            return ""
