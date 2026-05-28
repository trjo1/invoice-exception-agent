"""ModelClient — the single entry point for all LLM calls in the agent.

Every LLM call across the project routes through this client. This is what
enforces the open-source-first discipline (per docs/model_strategy.md):
default models live in config/models.yaml, the client picks the right
provider per model, logs cost per call, and enforces per-call cost ceilings.

No other code in this project may call provider SDKs directly.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from openai import (
    APIConnectionError,
    APITimeoutError,
    AsyncOpenAI,
    InternalServerError,
    RateLimitError,
)
from pydantic import BaseModel
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)


REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclass
class CompletionResult:
    output_text: str
    parsed: BaseModel | None
    model_used: str
    provider: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int
    raw_response: dict[str, Any] | None = None


class CostCeilingExceeded(Exception):
    """Raised when a call's cost exceeds the configured ceiling."""


class ModelConfig:
    """Wraps config/models.yaml. Pure read-side; safe to share across threads."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._data: dict[str, Any] = yaml.safe_load(path.read_text())

    def model_for_task(self, task: str) -> str:
        tasks = self._data.get("tasks", {})
        if task not in tasks:
            raise KeyError(f"No model configured for task {task!r} in {self._path}")
        return tasks[task]["model"]

    def fallback_for_task(self, task: str) -> str | None:
        return self._data.get("tasks", {}).get(task, {}).get("fallback")

    def provider_for_model(self, model_id: str) -> tuple[str, dict[str, Any]]:
        providers = self._data.get("providers", {})
        # Explicit handler match first (more specific than the default).
        explicit: tuple[str, dict[str, Any]] | None = None
        default: tuple[str, dict[str, Any]] | None = None
        for name, conf in providers.items():
            if conf.get("default"):
                default = (name, conf)
            for pat in conf.get("handles", []):
                if pat == model_id:
                    return name, conf
                if pat.endswith("/*") and model_id.startswith(pat[:-1]):
                    # Prefer non-default explicit matches over the wildcard default.
                    if not conf.get("default"):
                        explicit = (name, conf)
        if explicit is not None:
            return explicit
        if default is not None:
            return default
        raise KeyError(f"No provider matches model {model_id!r}")

    def price_per_million(self, model_id: str) -> tuple[float, float]:
        prices = self._data.get("prices", {})
        if model_id not in prices:
            raise KeyError(f"No price entry for model {model_id!r} in {self._path}")
        entry = prices[model_id]
        return float(entry["input"]), float(entry["output"])


class ModelClient:
    """Single entry point for LLM calls."""

    def __init__(
        self,
        config_path: Path | None = None,
        log_path: Path | None = None,
    ) -> None:
        self._config_path = config_path or (REPO_ROOT / "config" / "models.yaml")
        # Default log path honors DATA_DIR so on Railway (volume mounted at
        # /data, DATA_DIR=/data) the cost ledger persists across redeploys.
        # An explicit LLM_CALL_LOG_PATH still wins over both.
        data_dir = Path(os.environ.get("DATA_DIR") or (REPO_ROOT / "logs"))
        default_log = data_dir / "llm_calls.jsonl"
        log_env = os.environ.get("LLM_CALL_LOG_PATH")
        self._log_path = log_path or (Path(log_env) if log_env else default_log)
        self._soft_ceiling_usd = float(os.environ.get("LLM_CALL_SOFT_CEILING_USD", "0.10"))
        self._hard_ceiling_usd = float(os.environ.get("LLM_CALL_HARD_CEILING_USD", "1.00"))
        # Daily budget cap (USD). 0 = disabled (default). Used to bound the
        # cost of a hosted demo where anyone on the internet can trigger
        # LLM calls. Aggregation reads `logs/llm_calls.jsonl` via Stage9Reader.
        self._daily_cap_usd = float(os.environ.get("DAILY_BUDGET_CAP_USD", "0") or 0)
        self._config = ModelConfig(self._config_path)
        self._clients: dict[str, AsyncOpenAI] = {}
        self._stage9_reader: Any = None  # lazy: imported only when daily cap is active

    def _get_client(self, provider_name: str, provider_conf: dict[str, Any]) -> AsyncOpenAI:
        if provider_name in self._clients:
            return self._clients[provider_name]
        key_env = provider_conf["api_key_env"]
        api_key = os.environ.get(key_env)
        if not api_key:
            raise RuntimeError(
                f"Missing env var {key_env} for provider {provider_name!r}. "
                f"Set it in .env or export it before running.",
            )
        client = AsyncOpenAI(
            api_key=api_key,
            base_url=provider_conf["base_url"],
        )
        self._clients[provider_name] = client
        return client

    def _today_cost_usd(self) -> float:
        """Sum of all logged LLM call costs in the last 24 hours.

        Reads `logs/llm_calls.jsonl` via `Stage9Reader`, which mtime-caches
        so repeated calls are cheap. Returns 0.0 if the log doesn't exist
        yet or can't be read.
        """
        try:
            if self._stage9_reader is None:
                from p2p_agent.stage9.recorder import Stage9Reader
                self._stage9_reader = Stage9Reader(self._log_path)
            summary = self._stage9_reader.cost_summary(window="1d")
            return float(summary.get("total_usd", 0.0) or 0.0)
        except Exception:  # noqa: BLE001 — never let cost-check failure block the call beyond the cap itself
            return 0.0

    def _wire_model_id(self, model_id: str, provider_name: str) -> str:
        """Translate the canonical model_id to what the wire API expects."""
        if provider_name == "openrouter":
            return model_id
        # Direct provider APIs typically want the bare model name.
        return model_id.split("/", 1)[-1]

    async def complete(
        self,
        task: str,
        messages: list[dict[str, str]],
        response_model: type[BaseModel] | None = None,
        max_cost_usd: float | None = None,
        model_override: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 2048,
        case_id: str | None = None,
    ) -> CompletionResult:
        model_id = self._resolve_model(task, model_override)
        provider_name, provider_conf = self._config.provider_for_model(model_id)
        client = self._get_client(provider_name, provider_conf)
        wire_model = self._wire_model_id(model_id, provider_name)

        # Daily budget cap (for hosted demo cost protection). Checked BEFORE
        # the LLM call so we don't burn an additional call when already over.
        # Worst-case overshoot is one call (~$0.01 typical) — fine for a demo.
        if self._daily_cap_usd > 0:
            today_total = self._today_cost_usd()
            if today_total >= self._daily_cap_usd:
                raise CostCeilingExceeded(
                    f"Daily LLM budget cap reached "
                    f"(${today_total:.4f} of ${self._daily_cap_usd:.2f}). "
                    f"Try again tomorrow, or browse past runs."
                )

        # Inject prompt-cache hint on the system message when enabled. DeepSeek
        # auto-caches prefixes natively, but the explicit cache_control marker
        # helps providers that need it (Anthropic via OpenRouter) and is a
        # no-op for those that don't. Off by default — enable with
        # `LLM_PROMPT_CACHE_HINT=1` once verified safe on the live provider.
        wire_messages = self._maybe_inject_cache_control(messages, provider_name)

        start = time.monotonic()
        response = await self._call_with_retry(
            client=client,
            model=wire_model,
            messages=wire_messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        latency_ms = int((time.monotonic() - start) * 1000)

        usage = getattr(response, "usage", None)
        input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        cached_tokens = _extract_cached_tokens(usage)

        in_price, out_price = self._config.price_per_million(model_id)
        cost_usd = (
            input_tokens / 1_000_000 * in_price
            + output_tokens / 1_000_000 * out_price
        )

        if cost_usd > self._hard_ceiling_usd:
            raise CostCeilingExceeded(
                f"Call cost ${cost_usd:.4f} exceeded hard ceiling ${self._hard_ceiling_usd:.2f}",
            )
        if max_cost_usd is not None and cost_usd > max_cost_usd:
            raise CostCeilingExceeded(
                f"Call cost ${cost_usd:.4f} exceeded per-call cap ${max_cost_usd:.2f}",
            )

        output_text = response.choices[0].message.content or ""

        self._record_call(
            task=task,
            model=model_id,
            provider=provider_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            case_id=case_id,
            cached_tokens=cached_tokens,
        )

        if response_model is not None:
            raise NotImplementedError(
                "response_model parsing not yet wired. Parse output_text in the caller.",
            )

        return CompletionResult(
            output_text=output_text,
            parsed=None,
            model_used=model_id,
            provider=provider_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
        )

    async def _call_with_retry(
        self,
        client: AsyncOpenAI,
        model: str,
        messages: list[dict[str, Any]],
        temperature: float,
        max_tokens: int,
    ) -> Any:
        # Tightened from (4 attempts, 2-30s backoff) to (3 attempts, 1-8s).
        # Old defaults hid intermittent OpenRouter blips behind 30s+ waits
        # that looked indistinguishable from "agent is stuck." Fail fast.
        @retry(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=1, max=8),
            retry=retry_if_exception_type(
                (APIConnectionError, APITimeoutError, RateLimitError, InternalServerError),
            ),
            reraise=True,
        )
        async def _do() -> Any:
            return await client.chat.completions.create(
                model=model,
                messages=messages,  # type: ignore[arg-type]
                temperature=temperature,
                max_tokens=max_tokens,
            )

        return await _do()

    def _maybe_inject_cache_control(
        self,
        messages: list[dict[str, Any]],
        provider_name: str,
    ) -> list[dict[str, Any]]:
        """Rewrite system messages to the structured-content form with a
        `cache_control` hint.

        Defaults: ON for `openrouter` (DeepSeek's auto-cache + explicit hint),
        OFF otherwise. Override with `LLM_PROMPT_CACHE_HINT=1` (force on) or
        `LLM_PROMPT_CACHE_HINT=0` (force off).
        """
        env = os.environ.get("LLM_PROMPT_CACHE_HINT")
        if env == "0":
            return messages
        if env != "1" and provider_name != "openrouter":
            return messages
        if not messages or messages[0].get("role") != "system":
            return messages
        sys_msg = messages[0]
        content = sys_msg.get("content")
        if not isinstance(content, str):
            return messages  # already structured or unsupported shape
        rewritten = {
            "role": "system",
            "content": [
                {
                    "type": "text",
                    "text": content,
                    "cache_control": {"type": "ephemeral"},
                },
            ],
        }
        return [rewritten, *messages[1:]]

    def _resolve_model(self, task: str, override: str | None) -> str:
        if override is not None:
            return override
        env_key = f"MODEL_OVERRIDE_{task}"
        if env_key in os.environ:
            return os.environ[env_key]
        return self._config.model_for_task(task)

    def _record_call(
        self,
        task: str,
        model: str,
        provider: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
        latency_ms: int,
        case_id: str | None = None,
        cached_tokens: int = 0,
    ) -> None:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "task": task,
            "model": model,
            "provider": provider,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cached_tokens": cached_tokens,
            "cost_usd": round(cost_usd, 6),
            "latency_ms": latency_ms,
            "case_id": case_id,
        }
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        with self._log_path.open("a") as f:
            f.write(json.dumps(record) + "\n")


def _extract_cached_tokens(usage: Any) -> int:
    """Pull the cached-prompt-token count from an OpenAI-shaped usage object.

    OpenAI exposes `usage.prompt_tokens_details.cached_tokens`. OpenRouter
    surfaces the same shape when the upstream provider supports caching
    (DeepSeek, Anthropic, etc.). Returns 0 if the field is absent.
    """
    if usage is None:
        return 0
    details = getattr(usage, "prompt_tokens_details", None)
    if details is None:
        return 0
    return int(getattr(details, "cached_tokens", 0) or 0)


def run_sync(coro: Any) -> Any:
    """Convenience for scripts: run an async ModelClient call from sync code."""
    return asyncio.run(coro)
