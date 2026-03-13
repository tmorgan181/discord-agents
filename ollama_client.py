"""
Ollama client for Discord bot interactions.
Handles communication with local Ollama instance.
Uses run_in_executor so blocking HTTP calls don't freeze the async event loop.
"""

import asyncio
import re
import time
import requests
from typing import List, Dict, Optional
import logging

logger = logging.getLogger(__name__)

_THINK_RE = re.compile(r'<think>.*?</think>', re.DOTALL)

def _strip_think(text: str) -> str:
    return _THINK_RE.sub('', text).strip()


class OllamaClient:
    """Client for interacting with Ollama API."""

    def __init__(self, base_url: str = "http://localhost:11434"):
        self.base_url = base_url
        self.generate_url = f"{base_url}/api/generate"
        self.chat_url = f"{base_url}/api/chat"

    def _post(self, url: str, payload: dict) -> dict:
        """Synchronous POST — always call via run_in_executor."""
        response = requests.post(url, json=payload, timeout=60)
        response.raise_for_status()
        return response.json()

    def _post_stream(self, url: str, payload: dict, key: str) -> str:
        """Synchronous streaming POST — prints tokens to terminal as they arrive."""
        import json, sys
        payload = {**payload, "stream": True}
        full = []
        with requests.post(url, json=payload, stream=True, timeout=120) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line:
                    continue
                chunk = json.loads(line)
                token = chunk.get(key) or ""
                if isinstance(token, dict):          # chat endpoint nests content
                    token = token.get("content", "")
                if token:
                    print(token, end="", flush=True)
                    full.append(token)
                if chunk.get("done"):
                    print()                          # newline after stream ends
                    break
        return "".join(full)

    async def generate_response(
        self,
        model: str,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.8,
        max_tokens: int = 500,
        stream: bool = False
    ) -> str:
        payload = {
            "model": model,
            "prompt": prompt,
            "system": system_prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens
            }
        }
        logger.info(f"[Ollama] generate → {model} | prompt: {prompt[:80].strip()!r}")
        t0 = time.monotonic()
        try:
            loop = asyncio.get_running_loop()
            if stream:
                text = await loop.run_in_executor(
                    None, lambda: self._post_stream(self.generate_url, payload, "response")
                )
            else:
                result = await loop.run_in_executor(None, lambda: self._post(self.generate_url, payload))
                text = result.get("response", "")
            text = _strip_think(text)
            elapsed = time.monotonic() - t0
            logger.info(f"[Ollama] generate ← {model} | {elapsed:.1f}s | {len(text)} chars")
            return text
        except Exception as e:
            elapsed = time.monotonic() - t0
            logger.error(f"[Ollama] generate error ({model}, {elapsed:.1f}s): {e}")
            return f"[Error: {str(e)}]"

    async def chat_response(
        self,
        model: str,
        messages: List[Dict[str, str]],
        temperature: float = 0.8,
        max_tokens: int = 500,
        stream: bool = False
    ) -> str:
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens
            }
        }
        last_user = next(
            (m["content"][:80] for m in reversed(messages) if m["role"] != "system"),
            ""
        )
        logger.info(f"[Ollama] chat → {model} | {len(messages)} msgs | last: {last_user.strip()!r}")
        t0 = time.monotonic()
        try:
            loop = asyncio.get_running_loop()
            if stream:
                text = await loop.run_in_executor(
                    None, lambda: self._post_stream(self.chat_url, payload, "message")
                )
            else:
                result = await loop.run_in_executor(None, lambda: self._post(self.chat_url, payload))
                text = result.get("message", {}).get("content", "")
            text = _strip_think(text)
            elapsed = time.monotonic() - t0
            logger.info(f"[Ollama] chat ← {model} | {elapsed:.1f}s | {len(text)} chars")
            return text
        except Exception as e:
            elapsed = time.monotonic() - t0
            logger.error(f"[Ollama] chat error ({model}, {elapsed:.1f}s): {e}")
            return f"[Error: {str(e)}]"

    def is_available(self) -> bool:
        """Check if Ollama server is running."""
        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=5)
            return response.status_code == 200
        except requests.exceptions.RequestException:
            return False

    def list_models(self) -> List[str]:
        """Get list of available models."""
        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=5)
            response.raise_for_status()
            data = response.json()
            return [model["name"] for model in data.get("models", [])]
        except requests.exceptions.RequestException as e:
            logger.error(f"Could not fetch models: {e}")
            return []
