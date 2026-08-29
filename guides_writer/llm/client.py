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


class JSONValidateError(LLMError):
    """Provider-side json_object validation failure.

    Carries the model's raw (invalid/truncated) generation so callers can
    attempt a targeted repair instead of a blind retry.
    """

    def __init__(self, message: str, failed_generation: str | None = None):
        super().__init__(message)
        self.failed_generation = failed_generation


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
        fallback_model: str | None = None,
        fallback_base_url: str | None = None,
        fallback_api_key: str | None = None,
        timeout: float = 180.0,
    ):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.fallback_model = fallback_model
        self.fallback_base_url = (fallback_base_url or base_url).rstrip("/") if fallback_model else None
        self.fallback_api_key = fallback_api_key or api_key
        self._client = httpx.Client(
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )
        self._fallback_client = None
        if fallback_model:
            self._fallback_client = httpx.Client(
                headers={
                    "Authorization": f"Bearer {self.fallback_api_key}",
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
            failed_gen = self._extract_failed_generation(resp.text)
            raise JSONValidateError(
                f"LLM JSON generation failed (400 json_validate_failed): {resp.text[:2000]}",
                failed_generation=failed_gen,
            )
        if resp.status_code != 200:
            raise RuntimeError(f"LLM API HTTP {resp.status_code}: {resp.text[:800]}")
        return resp.json()

    @staticmethod
    def _extract_failed_generation(text: str) -> str | None:
        try:
            body = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return None
        try:
            gen = body["error"]["failed_generation"]
            return gen if isinstance(gen, str) else None
        except (KeyError, TypeError):
            return None

    def _build_payload(self, model: str, messages: list[dict], temperature: float,
                       max_tokens: int | None, json_response: bool,
                       reasoning_effort: str | None) -> dict:
        payload: dict = {"model": model, "messages": messages, "temperature": temperature}
        if max_tokens:
            payload["max_tokens"] = max_tokens
        if json_response:
            payload["response_format"] = {"type": "json_object"}
        if reasoning_effort:
            model_lower = model.lower()
            if "compound" in model_lower:
                pass
            elif "qwen" in model_lower:
                payload["reasoning_effort"] = "none" if reasoning_effort == "low" else reasoning_effort
            elif "gpt-oss" in model_lower:
                payload["reasoning_effort"] = reasoning_effort
            else:
                payload["reasoning_effort"] = reasoning_effort
        return payload

    def _call_once(self, model: str, base_url: str, client: httpx.Client,
                   messages: list[dict], temperature: float, max_tokens: int | None,
                   json_response: bool, reasoning_effort: str | None) -> str:
        payload = self._build_payload(model, messages, temperature, max_tokens, json_response, reasoning_effort)
        orig_model, orig_base, orig_client = self.model, self.base_url, self._client
        self.model, self.base_url, self._client = model, base_url, client
        try:
            data = self._post(payload)
        finally:
            self.model, self.base_url, self._client = orig_model, orig_base, orig_client
        try:
            message = data["choices"][0]["message"]
            content = message.get("content")
        except (KeyError, IndexError) as exc:
            raise LLMError(f"unexpected response shape: {json.dumps(data)[:300]}") from exc
        if not content:
            raise RuntimeError(f"LLM returned empty content: {json.dumps(data)[:300]}")
        logger.info("llm_call_ok model=%s chars=%d", model, len(content))
        return content

    def chat(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int | None = None,
        json_response: bool = False,
        reasoning_effort: str | None = "low",
    ) -> str:
        try:
            return self._call_once(self.model, self.base_url, self._client,
                                   messages, temperature, max_tokens, json_response, reasoning_effort)
        except (LLMError, RuntimeError, ValueError, httpx.HTTPError) as exc:
            if not self.fallback_model or not self._fallback_client:
                raise
            logger.warning("llm_primary_failed model=%s err=%s — falling back to %s",
                           self.model, str(exc)[:150], self.fallback_model)
            return self._call_once(self.fallback_model, self.fallback_base_url, self._fallback_client,
                                   messages, temperature, max_tokens, json_response, reasoning_effort)

    def chat_json(
        self,
        messages: list[dict],
        schema: type[BaseModel],
        temperature: float = 0.2,
        max_tokens: int | None = None,
        retries: int = 3,
    ) -> BaseModel:
        last_exc: Exception | None = None
        for attempt in range(1, retries + 1):
            try:
                raw = self.chat(
                    messages, temperature=temperature, max_tokens=max_tokens,
                    json_response=True,
                )
                try:
                    return self._parse(raw, schema)
                except ValueError as exc:
                    last_exc = exc
                    # client-side parse/validation failure -> targeted repair
                    repair_messages = self._repair_messages(messages, raw)
                    logger.warning(
                        "llm_json_repair_retry error=%s", str(exc)[:200]
                    )
                    raw2 = self.chat(
                        repair_messages, temperature=temperature,
                        max_tokens=max_tokens, json_response=True,
                    )
                    return self._parse(raw2, schema)
            except JSONValidateError as exc:
                # provider-side json_object validation failure -> recoverable
                last_exc = exc
                logger.warning(
                    "llm_json_validate_retry attempt=%d err=%s",
                    attempt, str(exc)[:150],
                )
                if exc.failed_generation and attempt < retries:
                    # try a targeted completion of the garbled output first
                    try:
                        repair_messages = self._repair_messages(messages, exc.failed_generation)
                        raw2 = self.chat(
                            repair_messages, temperature=temperature,
                            max_tokens=max_tokens, json_response=True,
                        )
                        return self._parse(raw2, schema)
                    except (ValueError, JSONValidateError, LLMError, httpx.HTTPError) as exc2:
                        last_exc = exc2
                        continue
                if attempt < retries:
                    continue
            except (ValueError, LLMError, httpx.HTTPError, RuntimeError) as exc:
                # non-JSON transient provider errors -> plain retry
                last_exc = exc
                if attempt < retries:
                    logger.warning(
                        "llm_json_transient_retry attempt=%d err=%s",
                        attempt, str(exc)[:150],
                    )
                    continue
                raise
        raise last_exc if last_exc else LLMError("chat_json failed without error detail")

    def _repair_messages(self, messages: list[dict], garbled: str) -> list[dict]:
        return messages + [
            {"role": "assistant", "content": garbled},
            {
                "role": "user",
                "content": (
                    "Your previous JSON reply failed validation and may be "
                    "truncated or malformed. Return the complete, valid JSON "
                    "object matching the required schema. No prose, no markdown "
                    "fences — a single JSON object only."
                ),
            },
        ]

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
