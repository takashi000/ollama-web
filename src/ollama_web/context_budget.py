"""Request-local context estimates and tool result budgets (not exact tokenization)."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from typing import Any

DEFAULT_TOOL_SETTINGS = {"context_usage_limit": 0.8}
IMAGE_TOKENS = 4096
MESSAGE_TOKENS = 32


def validate_tool_settings(raw: Any) -> dict[str, float]:
    if not isinstance(raw, dict) or set(raw) != set(DEFAULT_TOOL_SETTINGS):
        raise ValueError("tools must contain context_usage_limit")
    value = raw["context_usage_limit"]
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not 0 < value < 1
    ):
        raise ValueError("tools.context_usage_limit must be between 0 and 1 (exclusive)")
    return {"context_usage_limit": float(value)}


def positive_int(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


def value_of(obj: Any, key: str, default: Any = None) -> Any:
    return obj.get(key, default) if isinstance(obj, Mapping) else getattr(obj, key, default)


@dataclass(frozen=True)
class ModelContext:
    capacity: int
    source: str
    estimated: bool
    requested: int | None
    cloud: bool = False

    @classmethod
    def resolve(cls, model: str, info: Any, options: Mapping[str, Any]) -> ModelContext:
        metadata = value_of(info, "model_info") or value_of(info, "modelinfo", {}) or {}
        if not isinstance(metadata, Mapping):
            metadata = {}
        architecture = metadata.get("general.architecture")
        advertised = positive_int(metadata.get(f"{architecture}.context_length"))
        requested = positive_int(options.get("num_ctx"))
        cloud = bool(value_of(info, "remote_host") or value_of(info, "remote_model"))
        cloud = cloud or model.endswith(":cloud") or model.endswith("-cloud")
        if advertised:
            capacity = advertised if cloud or requested is None else min(advertised, requested)
            source = "model" if capacity == advertised else "requested"
            return cls(capacity, source, not cloud, requested, cloud)
        return cls(
            requested or 8192, "requested" if requested else "fallback", True, requested, cloud
        )


@dataclass(frozen=True)
class ResultBudget:
    tokens: int
    factor: float = 1.0

    def estimate(self, text: str) -> int:
        return math.ceil(math.ceil(len(text.encode("utf-8")) / 2) * self.factor)

    def fits(self, text: str) -> bool:
        return self.estimate(text) <= self.tokens

    @property
    def max_chars(self) -> int:
        # Every Unicode character consumes at least one UTF-8 byte.
        return max(0, math.floor(self.tokens * 2 / self.factor))

    def prefix(self, text: str) -> str:
        low, high = 0, min(len(text), self.max_chars)
        while low < high:
            mid = (low + high + 1) // 2
            if self.fits(text[:mid]):
                low = mid
            else:
                high = mid - 1
        return text[:low]

    def trim(self, text: str) -> str:
        if self.fits(text):
            return text
        suffix = "\n[tool result truncated]"
        if not self.fits(suffix):
            return self.prefix("[truncated]")
        low, high = 0, min(len(text), self.max_chars)
        while low < high:
            mid = (low + high + 1) // 2
            if self.fits(text[:mid] + suffix):
                low = mid
            else:
                high = mid - 1
        return text[:low] + suffix


current_result_budget: ContextVar[ResultBudget | None] = ContextVar(
    "tool_result_budget",
    default=None,
)


def standalone_result_limit(requested: int) -> int | None:
    """Standalone tool calls obey their requested size, without a hidden fixed cap."""
    budget = current_result_budget.get()
    return budget.max_chars if budget else None


def _serialized(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(exclude_none=True)

    def encode(obj: Any) -> Any:
        return obj.model_dump(exclude_none=True) if hasattr(obj, "model_dump") else str(obj)

    return json.dumps(value, ensure_ascii=False, default=encode, separators=(",", ":"))


@dataclass
class ContextBudget:
    context: ModelContext
    context_usage_limit: float = DEFAULT_TOOL_SETTINGS["context_usage_limit"]
    num_predict: int | None = None
    factor: float = 1.0
    results: list[str] = field(default_factory=list)
    result_images: int = 0
    observed_floor: int = 0
    observed_tail: int = 0
    stop_reason: str | None = None
    notification_text: str = ""

    def text_tokens(self, text: str) -> int:
        return ResultBudget(0, self.factor).estimate(text)

    def message_tokens(self, message: Any) -> int:
        data = {
            k: value_of(message, k)
            for k in ("role", "content", "thinking", "tool_calls", "tool_name", "name")
            if value_of(message, k) is not None
        }
        images = value_of(message, "images", []) or []
        return (
            MESSAGE_TOKENS
            + self.text_tokens(_serialized(data))
            + math.ceil(len(images) * IMAGE_TOKENS * self.factor)
        )

    def input_tokens(self, messages: Sequence[Any], tools: Sequence[Any]) -> int:
        estimated = 256 + sum(self.message_tokens(m) for m in messages)
        if tools:
            estimated += self.text_tokens(_serialized(tools))
        return max(estimated, self.observed_floor + self.observed_tail)

    def observe(self, counts: Mapping[str, int] | None, sent_estimate: int) -> None:
        if not counts:
            return
        prompt = counts["prompt_eval_count"]
        if prompt > sent_estimate > 0:
            self.factor *= prompt / sent_estimate
        self.observed_floor = max(0, prompt) + max(0, counts["eval_count"])
        self.observed_tail = 0

    @property
    def input_limit(self) -> int:
        """Tool retrieval cutoff; this must never gate ordinary model generation."""
        return min(
            math.floor(self.context.capacity * self.context_usage_limit),
            self.context.capacity - (self.num_predict or 0),
        )

    @property
    def used(self) -> int:
        return sum(self.text_tokens(t) for t in self.results) + math.ceil(
            self.result_images * IMAGE_TOKENS * self.factor
        )

    @property
    def reserve(self) -> int:
        return self.context.capacity - self.input_limit

    def headroom(self, messages: Sequence[Any], tools: Sequence[Any]) -> int:
        return self.input_limit - self.input_tokens(messages, tools)

    def available(
        self, messages: Sequence[Any], tools: Sequence[Any], notice_reserve: int = 0
    ) -> ResultBudget:
        notification_reserve = self.text_tokens(self.notification_text) + MESSAGE_TOKENS
        headroom = self.headroom(messages, tools) - notice_reserve - notification_reserve
        # Size individual pages automatically so one source cannot fill the context at once.
        page_size = max(1, self.context.capacity // 20)
        tokens = max(0, min(page_size, headroom))
        return ResultBudget(tokens, self.factor)

    def add_result(self, message: Any) -> None:
        self.results.append(str(value_of(message, "content", "") or ""))
        self.result_images += len(value_of(message, "images", []) or [])
        self.observed_tail += self.message_tokens(message)

    def snapshot(self, messages: Sequence[Any], tools: Sequence[Any]) -> dict[str, Any]:
        return {
            "effective_context": self.context.capacity,
            "source": self.context.source,
            "estimated": True,
            "capacity_estimated": self.context.estimated,
            "requested_num_ctx": self.context.requested,
            "input_tokens": self.input_tokens(messages, tools),
            "tool_tokens": self.used,
            "tool_limit": self.input_limit,
            "input_limit": self.input_limit,
            "context_usage_limit": self.context_usage_limit,
            "next_result_tokens": self.available(messages, tools).tokens,
            "response_reserve": self.reserve,
            "correction_factor": self.factor,
            "stop_reason": self.stop_reason,
        }

    def update_runtime(self, response: Any, model: str) -> None:
        if self.context.cloud:
            return

        def canonical(name: str) -> str:
            return name if ":" in name else name + ":latest"

        for item in value_of(response, "models", []) or []:
            name = value_of(item, "model") or value_of(item, "name", "")
            capacity = positive_int(value_of(item, "context_length"))
            if capacity and canonical(str(name)) == canonical(model):
                if capacity <= self.context.capacity:
                    data = asdict(self.context)
                    data.update(capacity=capacity, source="runtime", estimated=False)
                    self.context = ModelContext(**data)
                break
