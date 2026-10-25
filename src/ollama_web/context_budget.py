"""Request-local context estimates and tool result budgets (not exact tokenization)."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from typing import Any

DEFAULT_RATIOS = {
    "total_ratio": 0.5,
    "per_result_ratio": 0.05,
    "response_reserve_ratio": 0.2,
    "safety_margin_ratio": 0.1,
}
IMAGE_TOKENS = 4096
MESSAGE_TOKENS = 32


def validate_ratios(raw: Any) -> dict[str, float]:
    if not isinstance(raw, dict) or set(raw) != set(DEFAULT_RATIOS):
        raise ValueError("tool_budget must contain all four ratio fields")
    result: dict[str, float] = {}
    for key, value in raw.items():
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value <= 0
        ):
            raise ValueError(f"tool_budget.{key} must be a finite positive number")
        result[key] = float(value)
    if result["per_result_ratio"] > result["total_ratio"]:
        raise ValueError("tool_budget.per_result_ratio must not exceed total_ratio")
    if (
        math.fsum(
            result[k] for k in ("total_ratio", "response_reserve_ratio", "safety_margin_ratio")
        )
        >= 1
    ):
        raise ValueError("tool_budget total, response reserve and safety margin must sum to < 1")
    return result


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
    ratios: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_RATIOS))
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
    def total_limit(self) -> int:
        return math.floor(self.context.capacity * self.ratios["total_ratio"])

    @property
    def used(self) -> int:
        return sum(self.text_tokens(t) for t in self.results) + math.ceil(
            self.result_images * IMAGE_TOKENS * self.factor
        )

    @property
    def reserve(self) -> int:
        return max(
            math.ceil(self.context.capacity * self.ratios["response_reserve_ratio"]),
            self.num_predict or 0,
        )

    @property
    def safety(self) -> int:
        return math.ceil(self.context.capacity * self.ratios["safety_margin_ratio"])

    def headroom(self, messages: Sequence[Any], tools: Sequence[Any]) -> int:
        return (
            self.context.capacity - self.input_tokens(messages, tools) - self.reserve - self.safety
        )

    def available(
        self, messages: Sequence[Any], tools: Sequence[Any], notice_reserve: int = 0
    ) -> ResultBudget:
        total = self.total_limit - self.used
        notification_reserve = self.text_tokens(self.notification_text) + MESSAGE_TOKENS
        headroom = self.headroom(messages, tools) - notice_reserve - notification_reserve
        tokens = max(
            0,
            min(
                math.floor(self.context.capacity * self.ratios["per_result_ratio"]), total, headroom
            ),
        )
        if total <= 0:
            self.stop_reason = "tool_total_limit"
        elif headroom <= 0:
            self.stop_reason = "context_headroom"
        elif tokens <= 0:
            self.stop_reason = "result_metadata_too_large"
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
            "tool_limit": self.total_limit,
            "next_result_tokens": self.available(messages, tools).tokens,
            "response_reserve": self.reserve,
            "safety_margin": self.safety,
            "correction_factor": self.factor,
            "stop_reason": self.stop_reason,
            "ratios": dict(self.ratios),
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
