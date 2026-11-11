"""Minimal Ollama client (local LLMs for character detection and comic/scan reading)."""
from __future__ import annotations

import base64
import json

import requests


class OllamaError(Exception):
    pass


class Ollama:
    def __init__(self, url: str = "http://127.0.0.1:11434"):
        self.url = url.rstrip("/")

    def models(self) -> list[str]:
        try:
            r = requests.get(f"{self.url}/api/tags", timeout=5)
            r.raise_for_status()
        except requests.RequestException as e:
            raise OllamaError(f"Ollama is not running at {self.url}. Start Ollama and try again.") from e
        return [m["name"] for m in r.json().get("models", [])]

    def available(self) -> bool:
        try:
            self.models()
            return True
        except OllamaError:
            return False

    def has_model(self, name: str) -> bool:
        names = self.models()
        base = name if ":" in name else f"{name}:latest"
        return name in names or base in names

    def pull(self, name: str, progress=None, cancel=None) -> None:
        """Download a model. progress(fraction, text) is called while it downloads."""
        with requests.post(f"{self.url}/api/pull", json={"model": name, "stream": True}, stream=True,
                           timeout=(10, None)) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if cancel is not None and cancel():
                    raise OllamaError("Cancelled")
                if not line:
                    continue
                msg = json.loads(line)
                if "error" in msg:
                    raise OllamaError(msg["error"])
                if progress:
                    total, done = msg.get("total"), msg.get("completed")
                    frac = (done / total) if total and done else 0.0
                    progress(frac, msg.get("status", ""))

    def chat_json(self, model: str, system: str, user: str, schema: dict | None = None,
                  images: list[bytes] | None = None, temperature: float = 0.1,
                  num_ctx: int = 8192, keep_alive: str = "5m", timeout: float = 600) -> dict:
        msg = {"role": "user", "content": user}
        if images:
            msg["images"] = [base64.b64encode(b).decode() for b in images]
        body = {
            "model": model,
            "messages": [{"role": "system", "content": system}, msg],
            "stream": False,
            "format": schema or "json",
            "keep_alive": keep_alive,
            "options": {"temperature": temperature, "num_ctx": num_ctx},
        }
        try:
            r = requests.post(f"{self.url}/api/chat", json=body, timeout=(10, timeout))
        except requests.RequestException as e:
            raise OllamaError(f"Ollama request failed: {e}") from e
        if r.status_code == 404:
            raise OllamaError(f"Model '{model}' is not installed in Ollama.")
        if r.status_code != 200:
            raise OllamaError(f"Ollama error {r.status_code}: {r.text[:300]}")
        content = r.json().get("message", {}).get("content", "")
        try:
            return json.loads(content)
        except ValueError:
            start, end = content.find("{"), content.rfind("}")
            if start >= 0 and end > start:
                try:
                    return json.loads(content[start:end + 1])
                except ValueError:
                    pass
            raise OllamaError("The model did not return valid JSON.")

    def unload(self, model: str) -> None:
        """Free the GPU memory a model is holding."""
        try:
            requests.post(f"{self.url}/api/generate", json={"model": model, "keep_alive": 0}, timeout=10)
        except requests.RequestException:
            pass
