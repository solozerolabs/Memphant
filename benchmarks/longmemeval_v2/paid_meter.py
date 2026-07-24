"""Hard in-run caps around the shared append-before-call OpenAI meter."""

from __future__ import annotations

import asyncio
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import threading
from typing import Any


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class PaidRunCaps:
    def __init__(
        self,
        ledger: Any,
        *,
        context: dict[str, Any],
        max_attempts: int,
        max_spend_usd: Decimal,
        model_prices: dict[str, dict[str, Decimal]],
        model_output_caps: dict[str, int],
    ) -> None:
        if max_attempts <= 0 or max_spend_usd <= 0:
            raise ValueError("paid caps must be positive")
        self.ledger = ledger
        self.context = context
        self.max_attempts = max_attempts
        self.max_spend_usd = max_spend_usd
        self.model_prices = model_prices
        self.model_output_caps = model_output_caps
        self._lock = threading.RLock()
        self._reservations: dict[str, Decimal] = {}

    def _liability(self, kwargs: dict[str, Any]) -> tuple[str, Decimal]:
        model = kwargs.get("model")
        if model not in self.model_prices or model not in self.model_output_caps:
            raise RuntimeError(f"model is outside paid authorization: {model}")
        output_limit = kwargs.get("max_completion_tokens", kwargs.get("max_tokens"))
        if type(output_limit) is not int or not 0 < output_limit <= self.model_output_caps[model]:
            raise RuntimeError(f"model output cap drift: {model}")
        # UTF-8 bytes are a conservative tokenizer-independent upper bound on
        # billable input tokens for these text/image OpenAI-compatible calls.
        input_token_bound = len(
            json.dumps(kwargs, sort_keys=True, separators=(",", ":")).encode()
        )
        prices = self.model_prices[model]
        liability = (
            Decimal(input_token_bound) * prices["prompt"]
            + Decimal(output_limit) * prices["completion"]
        ) / Decimal(1_000_000)
        key = _sha256_json({"context": self.context, "request": kwargs})
        return key, liability

    def attempt_metadata(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        _key, liability = self._liability(kwargs)
        output_limit = kwargs.get("max_completion_tokens", kwargs.get("max_tokens"))
        input_token_bound = len(
            json.dumps(kwargs, sort_keys=True, separators=(",", ":")).encode()
        )
        return {
            "reserved_liability_usd": str(liability),
            "input_token_upper_bound": input_token_bound,
            "authorized_output_limit": output_limit,
        }

    def reserve(self, kwargs: dict[str, Any]) -> str:
        key, liability = self._liability(kwargs)
        with self._lock:
            snapshot = self.ledger.snapshot()
            attempts = snapshot["attempts"]
            if any(row.get("status") != "result" for row in attempts):
                raise RuntimeError("paid campaign has an interrupted attempt; abort, do not resume")
            prior_keys = {
                _sha256_json(
                    {
                        "context": {
                            field: row["start"].get(field)
                            for field in self.context
                        },
                        "request_sha256": row["start"].get("request_sha256"),
                    }
                )
                for row in attempts
            }
            duplicate_key = _sha256_json(
                {"context": self.context, "request_sha256": _sha256_json(kwargs)}
            )
            if duplicate_key in prior_keys or key in self._reservations:
                raise RuntimeError("duplicate paid request is blocked")
            if len(attempts) + len(self._reservations) >= self.max_attempts:
                raise RuntimeError("provider-attempt cap reached")
            settled = Decimal(str(snapshot["reported_cost_usd"]))
            projected = settled + sum(self._reservations.values(), Decimal(0)) + liability
            if projected > self.max_spend_usd:
                raise RuntimeError("in-run spend cap would be exceeded")
            self._reservations[key] = liability
        return key

    def release(self, key: str) -> None:
        with self._lock:
            self._reservations.pop(key, None)


def install_bounded_openai_meter(
    openai_module: Any,
    ledger_path: Path,
    *,
    context: dict[str, Any],
    ledger_context: dict[str, Any],
    generation_lookup: Any,
    max_attempts: int,
    max_spend_usd: Decimal,
    model_prices: dict[str, dict[str, Decimal]],
    model_output_caps: dict[str, int],
) -> Any:
    from provider_attempts import install_openai_meter

    ledger = install_openai_meter(
        openai_module,
        ledger_path,
        context=context,
        ledger_context=ledger_context,
        request_metadata=lambda kwargs: caps.attempt_metadata(kwargs),
        generation_lookup=generation_lookup,
    )
    caps = PaidRunCaps(
        ledger,
        context=context,
        max_attempts=max_attempts,
        max_spend_usd=max_spend_usd,
        model_prices=model_prices,
        model_output_caps=model_output_caps,
    )

    def install(name: str, *, is_async: bool) -> None:
        metered_constructor = getattr(openai_module, name, None)
        if metered_constructor is None:
            return

        def constructor(*args: Any, **kwargs: Any) -> Any:
            client = metered_constructor(*args, **kwargs)
            completions = client.chat.completions
            metered_create = completions.create
            if is_async:
                async def create(*create_args: Any, **create_kwargs: Any) -> Any:
                    reservation = caps.reserve(create_kwargs)
                    try:
                        return await metered_create(*create_args, **create_kwargs)
                    finally:
                        caps.release(reservation)
            else:
                def create(*create_args: Any, **create_kwargs: Any) -> Any:
                    reservation = caps.reserve(create_kwargs)
                    try:
                        return metered_create(*create_args, **create_kwargs)
                    finally:
                        caps.release(reservation)
            completions.create = create
            return client

        setattr(openai_module, name, constructor)

    install("OpenAI", is_async=False)
    install("AsyncOpenAI", is_async=True)
    return ledger
