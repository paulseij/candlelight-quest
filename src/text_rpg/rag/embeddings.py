from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)


class EmbeddingUnavailableError(Exception):
    """The embedding backend could not produce a vector.

    Raised instead of returning zero vectors — writing garbage vectors into
    the store silently corrupts retrieval forever.
    """


class LiteLLMEmbeddings:
    """Generate embeddings via LiteLLM (OpenAI text-embedding-3-small, etc.)."""

    DEFAULT_DIM = 1536  # text-embedding-3-small output dimension

    def __init__(self, model: str = "text-embedding-3-small", api_key: str | None = None) -> None:
        self.model = model
        self._api_key = api_key

    @property
    def model_tag(self) -> str:
        """Stable per-model tag used to namespace vector collections."""
        return f"{self.model.replace('/', '_')}-{self.DEFAULT_DIM}"

    def embed(self, text: str) -> list[float]:
        """Embed a single text string."""
        return self._call(text)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple texts in one API call."""
        try:
            import litellm

            kwargs: dict[str, Any] = {"model": self.model, "input": texts}
            if self._api_key:
                kwargs["api_key"] = self._api_key
            response = litellm.embedding(**kwargs)
            return [item["embedding"] for item in response.data]
        except Exception as exc:
            raise EmbeddingUnavailableError(f"LiteLLM batch embedding failed: {exc}") from exc

    def _call(self, text: str) -> list[float]:
        try:
            import litellm

            kwargs: dict[str, Any] = {"model": self.model, "input": [text]}
            if self._api_key:
                kwargs["api_key"] = self._api_key
            response = litellm.embedding(**kwargs)
            return response.data[0]["embedding"]
        except Exception as exc:
            raise EmbeddingUnavailableError(f"LiteLLM embedding failed: {exc}") from exc

    def is_available(self) -> bool:
        """Return True if an API key is configured."""
        return bool(self._api_key)


class OllamaEmbeddings:
    """Generate embeddings using the Ollama local API."""

    DEFAULT_DIM = 768  # nomic-embed-text output dimension

    def __init__(
        self,
        model: str = "nomic-embed-text",
        base_url: str = "http://localhost:11434",
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")

    @property
    def model_tag(self) -> str:
        """Stable per-model tag used to namespace vector collections."""
        return f"{self.model.replace('/', '_')}-{self.DEFAULT_DIM}"

    def embed(self, text: str) -> list[float]:
        """Embed a single text string."""
        return self._call_ollama(text)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple texts sequentially."""
        return [self._call_ollama(t) for t in texts]

    def _call_ollama(self, text: str) -> list[float]:
        """Call the Ollama embedding endpoint and return the vector."""
        url = f"{self.base_url}/api/embed"
        payload = json.dumps({"model": self.model, "input": text}).encode()
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                result: dict[str, Any] = json.loads(resp.read())
                return result["embeddings"][0]
        except (urllib.error.URLError, ConnectionError, TimeoutError) as exc:
            raise EmbeddingUnavailableError(f"Ollama embedding failed: {exc}") from exc
        except (KeyError, IndexError) as exc:
            raise EmbeddingUnavailableError(f"Unexpected Ollama response: {exc}") from exc

    def is_available(self) -> bool:
        """Return True if Ollama is running and the model is available."""
        try:
            url = f"{self.base_url}/api/tags"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=5) as resp:
                data: dict[str, Any] = json.loads(resp.read())
                models = [
                    m.get("name", "").split(":")[0]
                    for m in data.get("models", [])
                ]
                return self.model in models
        except Exception:
            return False
