"""
Hypothetical Document Embeddings (HyDE) for KCCITM AI Assistant.

Bridges the vocabulary gap between natural language queries and stored chunk text.
Instead of embedding the raw query, we generate a hypothetical answer that
resembles the kind of text found in actual chunks, then embed that.

Usage:
    hyde = HyDEGenerator(llm)
    hyde_text, embedding = await hyde.generate_and_embed("students with poor SGPA")
"""

import logging

from core.llm_client import OllamaClient

logger = logging.getLogger(__name__)

HYDE_PROMPT = """Given this question about student academic data at KCCITM institute, write a short hypothetical answer paragraph that would be found in a student result record.

Include specific details like: SGPA values, grades (A+, A, B+, B, C), subject names and codes, internal/external marks, semester numbers, result status (PASS/CP/FAIL), and branch names.

Be specific and realistic — use plausible numbers and subject names from an engineering college.

Question: {query}

Hypothetical answer (1 paragraph, be specific):"""


class HyDEGenerator:
    """
    Hypothetical Document Embeddings (HyDE).

    Instead of embedding the raw query (low vocab overlap with stored chunks),
    generate a hypothetical answer with the same vocabulary as actual chunks,
    then embed that for retrieval.

    Example:
      Query:  "who's doing badly?"
      HyDE:   "Student X has SGPA 5.2, scored C in Design and Analysis of
               Algorithms (34 external), grade C in Web Technology (KCS601)..."
      → High overlap with stored chunk vocabulary → better retrieval matches
    """

    def __init__(self, llm: OllamaClient) -> None:
        self.llm = llm

    async def generate(self, query: str) -> str:
        """
        Generate a hypothetical answer document for the given query.

        Returns:
            Hypothetical answer text (~100-200 words)
        """
        prompt = HYDE_PROMPT.format(query=query)
        try:
            response = await self.llm.generate(
                prompt=prompt,
                temperature=0.40,
                max_tokens=200,
                options={"temperature": 0.40},
            )
            return response.strip()
        except Exception as exc:
            logger.warning("HyDE generation failed: %s — falling back to raw query", exc)
            return query

    async def generate_and_embed(self, query: str) -> tuple[str, list[float]]:
        """
        Generate hypothetical doc AND embed it in one call.

        Returns:
            (hypothetical_text, 768-dim embedding vector)
        """
        hyde_text = await self.generate(query)
        embedding = await self.llm.embed(hyde_text)
        return hyde_text, embedding
