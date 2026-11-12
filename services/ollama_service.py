import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class OllamaServiceError(RuntimeError):
    pass


class OllamaEmptyResponseError(OllamaServiceError):
    pass


def strip_thinking(content: str) -> str:
    if "</think>" in content:
        content = content.rsplit("</think>", maxsplit=1)[1]
    elif "<think>" in content:
        content = content.split("<think>", maxsplit=1)[0]
    return content.strip()


class OllamaService:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11434",
        model: str = "qwen3:4b",
        timeout_seconds: int = 180,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds

    def is_model_available(self) -> bool:
        data = self._request_json("GET", "/api/tags")
        return any(model.get("name") == self.model for model in data.get("models", []))

    def chat(
        self,
        prompt: str,
        system_prompt: str,
        format_schema: dict[str, Any] | None = None,
    ) -> str:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "think": False,
            "options": {"temperature": 0.1},
        }
        if format_schema is not None:
            body["format"] = format_schema

        result = self._request_json("POST", "/api/chat", body)
        content = strip_thinking(result.get("message", {}).get("content", ""))
        if not content:
            raise OllamaEmptyResponseError("Ollama returned an empty response.")
        return content

    def _request_json(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = Request(
            f"{self.base_url}{path}",
            data=data,
            headers={"Content-Type": "application/json"},
            method=method,
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                result = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise OllamaServiceError(
                f"Ollama returned HTTP {exc.code}: {detail[:500]}"
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise OllamaServiceError(f"Could not reach local Ollama: {exc}") from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OllamaServiceError("Ollama returned an invalid JSON response.") from exc

        if not isinstance(result, dict):
            raise OllamaServiceError("Ollama returned an unexpected response shape.")
        return result
