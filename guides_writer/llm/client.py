import json
import logging

import httpx
from pydantic import BaseModel, ValidationError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


def _strip_fences(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        first_newline = cleaned.find("\n")
        if first_newline != -1:
            cleaned = cleaned[first_newline + 1 :]
        if cleaned.rstrip().endswith("```"):
            cleaned = cleaned.rstrip()[:-3]
    return cleaned.strip()


class LLMClient:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        timeout: float = 180.0,
    ):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )

    @retry(
        retry=retry_if_exception_type((httpx.HTTPError, LLMError)),
        wait=wait_exponential(multiplier=8, min=8, max=70),
        stop=stop_after_attempt(6),
        reraise=True,
    )
    def _post(self, payload: dict) -> dict:
        resp = self._client.post(f"{self.base_url}/chat/completions", json=payload)
        if resp.status_code in (413, 429, 500, 502, 503, 504):
            raise LLMError(f"LLM API HTTP {resp.status_code}: {resp.text[:800]}")
        if resp.status_code == 400 and "json_validate_failed" in resp.text:
            raise ValueError(f"LLM JSON generation failed (400 json_validate_failed): {resp.text[:2000]}")
        if resp.status_code != 200:
            raise RuntimeError(f"LLM API HTTP {resp.status_code}: {resp.text[:800]}")
        return resp.json()

    def chat(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int | None = None,
        json_response: bool = False,
        reasoning_effort: str | None = "low",
    ) -> str:
        payload: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens
        if json_response:
            payload["response_format"] = {"type": "json_object"}
        if reasoning_effort:
            model_lower = self.model.lower()
            if "qwen" in model_lower:
                payload["reasoning_effort"] = "none" if reasoning_effort == "low" else reasoning_effort
            elif "gpt-oss" in model_lower or "groq/compound" in model_lower:
                payload["reasoning_effort"] = reasoning_effort
            else:
                payload["reasoning_effort"] = reasoning_effort
        data = self._post(payload)
        try:
            message = data["choices"][0]["message"]
            content = message.get("content")
        except (KeyError, IndexError) as exc:
            raise LLMError(f"unexpected response shape: {json.dumps(data)[:300]}") from exc
        if not content:
            raise RuntimeError(f"LLM returned empty content: {json.dumps(data)[:300]}")
        logger.info("llm_call_ok model=%s chars=%d", self.model, len(content))
        return content

    def chat_json(
        self,
        messages: list[dict],
        schema: type[BaseModel],
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> BaseModel:
        raw = self.chat(
            messages, temperature=temperature, max_tokens=max_tokens, json_response=True
        )
        try:
            return self._parse(raw, schema)
        except ValueError as exc:
            repair_error = str(exc)

        logger.warning("llm_json_repair_retry error=%s", repair_error[:200])
        repair_messages = messages + [
            {"role": "assistant", "content": raw},
            {
                "role": "user",
                "content": (
                    "Your previous reply failed validation:\n"
                    f"{repair_error}\n\n"
                    "Return the corrected JSON object only. "
                    "No prose, no markdown fences."
                ),
            },
        ]
        raw2 = self.chat(
            repair_messages,
            temperature=temperature,
            max_tokens=max_tokens,
            json_response=True,
        )
        return self._parse(raw2, schema)

    def _parse(self, raw: str, schema: type[BaseModel]) -> BaseModel:
        text = _strip_fences(raw)
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("no JSON object found in response")
        candidate = text[start : end + 1]
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON: {exc}") from exc
        try:
            return schema.model_validate(data)
        except ValidationError as exc:
            raise ValueError(f"schema mismatch: {exc}") from exc
