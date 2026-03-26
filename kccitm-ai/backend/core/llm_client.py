"""
Async Ollama API client for KCCITM AI Assistant.

This is the ONLY interface to the LLM throughout the entire project.
Every other module that needs generation, embedding, or streaming imports
and uses OllamaClient.

Usage:
    from core.llm_client import OllamaClient

    llm = OllamaClient()
    response = await llm.generate("What is 2+2?")
    embedding = await llm.embed("Hello world")
    async for token in llm.stream_chat(messages):
        print(token, end="", flush=True)
"""

import asyncio
import json
import logging
from typing import AsyncGenerator

import httpx

from config import settings

logger = logging.getLogger(__name__)

# Retry settings
_MAX_RETRIES = 3
_RETRY_DELAYS = (1.0, 3.0, 9.0)


class OllamaClient:
    """
    Async client for Ollama REST API.
    Handles generation, streaming, embeddings, and health checks.

    A new httpx.AsyncClient is created per-request to avoid event loop
    lifecycle issues with long-lived async clients.
    """

    def __init__(
        self,
        base_url: str = None,
        model: str = None,
        embed_model: str = None,
    ) -> None:
        self.base_url = (base_url or settings.OLLAMA_HOST).rstrip("/")
        self.model = model or settings.OLLAMA_MODEL
        self.embed_model = embed_model or settings.OLLAMA_EMBED_MODEL
        # Generation can be slow on CPU — generous read timeout, fast connect
        self._timeout = httpx.Timeout(300.0, connect=10.0)

    # ── Public API ────────────────────────────────────────────────────────────

    async def generate(
        self,
        prompt: str,
        system: str = None,
        temperature: float = None,
        max_tokens: int = None,
        format: str = None,
        model: str = None,
    ) -> str:
        """
        Generate a complete response (non-streaming).

        Args:
            prompt:      User prompt text
            system:      System prompt (optional)
            temperature: Override settings.LLM_TEMPERATURE
            max_tokens:  Override settings.LLM_MAX_TOKENS
            format:      "json" to force JSON output
            model:       Override default model

        Returns:
            Complete response text as string
        """
        payload: dict = {
            "model": model or self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature if temperature is not None else settings.LLM_TEMPERATURE,
                "num_predict": max_tokens if max_tokens is not None else settings.LLM_MAX_TOKENS,
                "num_ctx": settings.LLM_NUM_CTX,
            },
        }
        if system:
            payload["system"] = system
        if format == "json":
            payload["format"] = "json"

        data = await self._post("/api/generate", payload)
        return data.get("response", "")

    async def chat(
        self,
        messages: list[dict],
        temperature: float = None,
        max_tokens: int = None,
        format: str = None,
        model: str = None,
    ) -> str:
        """
        Chat completion with message history (non-streaming).

        Args:
            messages:    List of {"role": "system"|"user"|"assistant", "content": "..."}
            temperature: Override default
            max_tokens:  Override default
            format:      "json" for JSON mode
            model:       Override default model

        Returns:
            Assistant's response text as string
        """
        payload: dict = {
            "model": model or self.model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temperature if temperature is not None else settings.LLM_TEMPERATURE,
                "num_predict": max_tokens if max_tokens is not None else settings.LLM_MAX_TOKENS,
                "num_ctx": settings.LLM_NUM_CTX,
            },
        }
        if format == "json":
            payload["format"] = "json"

        data = await self._post("/api/chat", payload)
        return data.get("message", {}).get("content", "")

    async def stream_chat(
        self,
        messages: list[dict],
        temperature: float = None,
        max_tokens: int = None,
        model: str = None,
    ) -> AsyncGenerator[str, None]:
        """
        Stream chat completion token by token.

        Args:
            messages:    Chat history
            temperature: Override default
            max_tokens:  Override default
            model:       Override default

        Yields:
            Individual tokens as they are generated
        """
        payload = {
            "model": model or self.model,
            "messages": messages,
            "stream": True,
            "options": {
                "temperature": temperature if temperature is not None else settings.LLM_TEMPERATURE,
                "num_predict": max_tokens if max_tokens is not None else settings.LLM_MAX_TOKENS,
                "num_ctx": settings.LLM_NUM_CTX,
            },
        }

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/api/chat",
                    json=payload,
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.strip():
                            continue
                        try:
                            chunk = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if chunk.get("done"):
                            break
                        token = chunk.get("message", {}).get("content", "")
                        if token:
                            yield token
            except httpx.ConnectError:
                raise ConnectionError(
                    f"Cannot connect to Ollama at {self.base_url}. Is it running?"
                )

    async def embed(self, text: str, model: str = None) -> list[float]:
        """
        Generate an embedding vector for a single text.

        Tries the newer /api/embed endpoint first; falls back to
        the legacy /api/embeddings if needed.

        Args:
            text:  Text to embed
            model: Override embed model (default: nomic-embed-text)

        Returns:
            768-dimensional embedding vector as list of floats
        """
        embed_model = model or self.embed_model
        # Try new /api/embed (Ollama ≥0.1.26)
        try:
            data = await self._post(
                "/api/embed",
                {"model": embed_model, "input": text},
            )
            vectors = data.get("embeddings")
            if vectors and isinstance(vectors, list) and len(vectors) > 0:
                return vectors[0]
        except Exception:
            pass

        # Fall back to legacy /api/embeddings
        data = await self._post(
            "/api/embeddings",
            {"model": embed_model, "prompt": text},
        )
        return data.get("embedding", [])

    async def embed_batch(
        self,
        texts: list[str],
        model: str = None,
    ) -> list[list[float]]:
        """
        Embed multiple texts with light parallelism (max 3 concurrent).

        Ollama doesn't support native batching, so we process with a semaphore
        to avoid overwhelming the local server.

        Args:
            texts: List of texts to embed
            model: Override embed model

        Returns:
            List of embedding vectors (same order as input)
        """
        semaphore = asyncio.Semaphore(3)

        async def _embed_one(text: str) -> list[float]:
            async with semaphore:
                return await self.embed(text, model=model)

        return await asyncio.gather(*[_embed_one(t) for t in texts])

    async def health_check(self) -> dict:
        """
        Check if Ollama is running and list available models.

        Returns:
            {"status": "ok", "models": ["qwen3:8b", ...]}
            or {"status": "error", "message": "..."}
        """
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
                response = await client.get(f"{self.base_url}/api/tags")
                response.raise_for_status()
                data = response.json()
                models = [m.get("name", "") for m in data.get("models", [])]
                return {"status": "ok", "models": models}
        except httpx.ConnectError:
            return {
                "status": "error",
                "message": f"Cannot connect to Ollama at {self.base_url}. Is it running?",
            }
        except Exception as exc:
            return {"status": "error", "message": str(exc)}

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _post(self, endpoint: str, payload: dict) -> dict:
        """
        POST to Ollama API with retry on transient errors.

        Retries 3 times with exponential backoff (1s, 3s, 9s) on
        connection errors and timeouts. Raises immediately on 404 (model not found).
        """
        url = f"{self.base_url}{endpoint}"
        last_exc: Exception = RuntimeError("No attempts made")

        for attempt, delay in enumerate((*_RETRY_DELAYS, None), start=1):
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(url, json=payload)

                if response.status_code == 404:
                    model = payload.get("model", "unknown")
                    raise ValueError(
                        f"Model '{model}' not found in Ollama. Run: ollama pull {model}"
                    )

                response.raise_for_status()
                return response.json()

            except ValueError:
                raise  # Don't retry model-not-found errors

            except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout) as exc:
                last_exc = exc
                if delay is None:
                    break  # Exhausted retries
                logger.warning(
                    "Ollama request failed (attempt %d/%d): %s — retrying in %.0fs",
                    attempt,
                    _MAX_RETRIES,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)

            except httpx.HTTPStatusError as exc:
                raise RuntimeError(
                    f"Ollama API error {exc.response.status_code}: {exc.response.text[:200]}"
                ) from exc

        raise ConnectionError(
            f"Cannot connect to Ollama at {self.base_url} after {_MAX_RETRIES} attempts. "
            "Is it running?"
        ) from last_exc
