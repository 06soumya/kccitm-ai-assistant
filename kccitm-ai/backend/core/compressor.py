"""
Contextual compression for KCCITM AI Assistant.

Strips irrelevant content from retrieved chunks before sending to the LLM,
reducing token usage by 30-60% while preserving all query-relevant data.

All chunks are compressed in a SINGLE batched LLM call (numbered entries),
not one call per chunk — critical for latency.

Usage:
    compressor = ContextualCompressor(llm)
    compressed = await compressor.compress("programming performance", chunks)
    savings = compressor.estimate_savings(chunks, compressed)
"""

import logging
import re

from core.llm_client import OllamaClient

logger = logging.getLogger(__name__)

COMPRESSION_PROMPT = """You are a data extraction assistant. Given a user's question and numbered student records, extract ONLY the information relevant to the question from each record.

Rules:
- Keep specific data points: SGPA values, marks (internal+external), grades, subject names
- Remove subjects and details that are NOT relevant to the question
- If a record has nothing relevant, write "IRRELEVANT" for that number
- Keep the student's name, roll number, branch, and semester in each extract
- Be concise but preserve all relevant numbers
- Return numbered extracts matching the input numbers

Question: {query}

Records:
{numbered_records}

Relevant extracts (numbered, one per line):"""


class ContextualCompressor:
    """
    Compresses retrieved chunks by removing irrelevant content before LLM generation.

    Instead of 10 full chunks (each ~400 tokens with all subjects),
    the compressor extracts only query-relevant parts (typically ~60-150 tokens).
    All chunks are processed in a SINGLE batched LLM call.

    Example:
      Query: "How did Aakash do in programming?"
      Full chunk: 10 subjects × 4 lines each ≈ 400 tokens
      Compressed: "AAKASH SINGH (2104920100002): Programming B (47+43=90), Lab A+ (24+23=47)"
      Reduction: ~85%

    Graceful fallback: if compression fails, returns original chunks unchanged.
    """

    def __init__(self, llm: OllamaClient) -> None:
        self.llm = llm

    async def compress(
        self,
        query: str,
        chunks: list[dict],
        text_field: str = "text",
    ) -> list[dict]:
        """
        Compress chunks by extracting only query-relevant content.

        Args:
            query: User's question
            chunks: List of chunk dicts with text
            text_field: Key containing the chunk text

        Returns:
            Chunks with text replaced by compressed version.
            Chunks marked IRRELEVANT are removed.
            Returns original chunks unchanged on any error.
        """
        if not chunks:
            return []

        numbered = [f"{i + 1}. {chunk.get(text_field, '')}" for i, chunk in enumerate(chunks)]
        numbered_text = "\n\n".join(numbered)

        prompt = COMPRESSION_PROMPT.format(
            query=query,
            numbered_records=numbered_text,
        )

        try:
            response = await self.llm.generate(
                prompt=prompt,
                temperature=0.1,
                max_tokens=1500,
            )
            return self._parse_compressed(response, chunks, text_field)
        except Exception as exc:
            logger.warning("Compression failed (%s) — returning original chunks", exc)
            return chunks

    def _parse_compressed(
        self,
        response: str,
        original_chunks: list[dict],
        text_field: str,
    ) -> list[dict]:
        """
        Parse numbered compressed output and map back to original chunks.

        Expected format:
          1. AAKASH SINGH (2104920100002): Programming B (47+43=90), Lab A+
          2. IRRELEVANT
          3. RAHUL KUMAR (roll): Programming C (35+28=63)

        IRRELEVANT chunks are excluded. Unparseable entries fall back to original text.
        """
        compressed_map: dict[int, str] = {}
        current_num: int | None = None
        current_lines: list[str] = []

        for line in response.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            match = re.match(r"^(\d+)[.\):\s]+(.+)", line)
            if match:
                if current_num is not None:
                    compressed_map[current_num] = "\n".join(current_lines).strip()
                current_num = int(match.group(1))
                current_lines = [match.group(2)]
            elif current_num is not None:
                current_lines.append(line)

        if current_num is not None:
            compressed_map[current_num] = "\n".join(current_lines).strip()

        result: list[dict] = []
        for i, chunk in enumerate(original_chunks):
            compressed_text = compressed_map.get(i + 1, "")

            if "IRRELEVANT" in compressed_text.upper():
                continue

            chunk_copy = chunk.copy()
            if compressed_text and len(compressed_text) > 10:
                chunk_copy[text_field] = compressed_text
                chunk_copy["compressed"] = True
            else:
                chunk_copy["compressed"] = False

            result.append(chunk_copy)

        # Safety net: if everything was removed, return originals
        if not result:
            logger.warning("Compressor removed all chunks — returning originals")
            return original_chunks

        return result

    def estimate_savings(
        self,
        original_chunks: list[dict],
        compressed_chunks: list[dict],
        text_field: str = "text",
    ) -> dict:
        """
        Calculate token savings from compression.
        Used for monitoring and the admin dashboard (Phase 9).
        """
        from core.context_builder import ContextBuilder
        cb = ContextBuilder()

        orig_tokens = sum(cb.count_tokens(c.get(text_field, "")) for c in original_chunks)
        comp_tokens = sum(cb.count_tokens(c.get(text_field, "")) for c in compressed_chunks)
        saved = orig_tokens - comp_tokens
        pct = (saved / orig_tokens * 100) if orig_tokens > 0 else 0.0

        return {
            "original_tokens": orig_tokens,
            "compressed_tokens": comp_tokens,
            "saved_tokens": saved,
            "savings_percent": round(pct, 1),
            "chunks_removed": len(original_chunks) - len(compressed_chunks),
        }
