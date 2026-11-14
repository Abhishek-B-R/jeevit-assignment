"""A small client for any OpenAI-compatible chat endpoint (Ollama, Groq, OpenRouter, Gemini).

JSON mode, temperature 0, retries on bad JSON, and a disk cache so reruns are free.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx


class LLMError(RuntimeError):
    pass


@dataclass
class LLMStats:
    calls: int = 0
    cache_hits: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    seconds: float = 0.0


@dataclass
class LLMClient:
    base_url: str
    model: str
    api_key: str = "none"
    timeout: float = 180.0
    cache_dir: Path | None = Path(".cache/llm")
    stats: LLMStats = field(default_factory=LLMStats)

    @classmethod
    def from_env(cls, use_cache: bool = True) -> "LLMClient":
        return cls(
            base_url=os.getenv("LLM_BASE_URL", "http://localhost:11434/v1").rstrip("/"),
            model=os.getenv("LLM_MODEL", "qwen2.5-coder:14b"),
            api_key=os.getenv("LLM_API_KEY", "ollama"),
            timeout=float(os.getenv("LLM_TIMEOUT", "180")),
            cache_dir=Path(".cache/llm") if use_cache else None,
        )

    def _cache_path(self, payload: dict) -> Path | None:
        if not self.cache_dir:
            return None
        key = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:32]
        return self.cache_dir / f"{key}.json"

    def chat_json(self, system: str, user: str, retries: int = 2) -> dict:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        last_err: Exception | None = None
        for attempt in range(retries + 1):
            payload = {
                "model": self.model,
                "messages": messages,
                "temperature": 0,
                "response_format": {"type": "json_object"},
            }
            raw = self._post(payload)
            try:
                return _parse_json(raw)
            except ValueError as e:
                last_err = e
                messages = messages + [
                    {"role": "assistant", "content": raw},
                    {"role": "user", "content": f"That was not valid JSON ({e}). Reply with only the JSON object."},
                ]
        raise LLMError(f"model returned invalid JSON after {retries + 1} tries: {last_err}")

    def chat_text(self, system: str, user: str) -> str:
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0.2,
        }
        return self._post(payload).strip()

    def _post(self, payload: dict) -> str:
        path = self._cache_path(payload)
        if path and path.exists():
            self.stats.cache_hits += 1
            return json.loads(path.read_text())["content"]

        started = time.perf_counter()
        for attempt in range(4):
            try:
                r = httpx.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    timeout=self.timeout,
                )
            except httpx.HTTPError as e:
                if attempt == 3:
                    raise LLMError(f"could not reach {self.base_url}: {e}") from e
                time.sleep(2 ** attempt)
                continue
            if r.status_code in (429, 500, 502, 503) and attempt < 3:
                time.sleep(2 ** (attempt + 1))
                continue
            if r.status_code >= 400:
                raise LLMError(f"{r.status_code} from {self.base_url}: {r.text[:300]}")
            break
        data = r.json()
        content = data["choices"][0]["message"]["content"] or ""
        usage = data.get("usage") or {}
        self.stats.calls += 1
        self.stats.prompt_tokens += usage.get("prompt_tokens", 0)
        self.stats.completion_tokens += usage.get("completion_tokens", 0)
        self.stats.seconds += time.perf_counter() - started
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"content": content, "usage": usage}))
        return content


def _parse_json(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw[raw.find("{"):]
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object found")
    obj = json.loads(raw[start : end + 1])
    if not isinstance(obj, dict):
        raise ValueError("top level is not an object")
    return obj
