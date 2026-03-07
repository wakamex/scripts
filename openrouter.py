#!/usr/bin/env python3
"""Lean OpenRouter CLI helper.

Supports:
- listing/filtering models
- showing model details
- one-shot chat completions
- lightweight multi-run latency probes
"""

from __future__ import annotations

import argparse
import json
import math
import os
import pathlib
import re
import statistics
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_TIMEOUT_SECONDS = 120
DEFAULT_PROBE_PROMPT = "Reply with exactly OK."
ENV_KEYS = {
    "OPENROUTER_API_KEY",
    "OPENROUTER_BASE_URL",
    "OPENROUTER_HTTP_REFERER",
    "OPENROUTER_X_TITLE",
}


class OpenRouterError(RuntimeError):
    """Raised for HTTP or API-level failures."""


@dataclass
class ProbeResult:
    model: str
    run: int
    ok: bool
    duration_ms: float
    prompt_tokens: int | None
    completion_tokens: int | None
    error: str | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="OpenRouter helper for model discovery and lightweight benchmarking."
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("OPENROUTER_BASE_URL", DEFAULT_BASE_URL),
        help="OpenRouter API base URL",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help="HTTP timeout in seconds",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("OPENROUTER_API_KEY"),
        help="OpenRouter API key (defaults to OPENROUTER_API_KEY)",
    )
    parser.add_argument(
        "--referer",
        default=os.environ.get("OPENROUTER_HTTP_REFERER"),
        help="Optional HTTP-Referer header",
    )
    parser.add_argument(
        "--title",
        default=os.environ.get("OPENROUTER_X_TITLE", "openrouter.py"),
        help="Optional X-Title header",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print JSON output where supported",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    models = subparsers.add_parser("models", help="List and filter OpenRouter models")
    models.add_argument("--match", help="Case-insensitive regex on id/name")
    models.add_argument("--supports", help="Require a supported parameter, e.g. structured_outputs")
    models.add_argument("--min-context", type=int, default=0, help="Minimum context length")
    models.add_argument(
        "--max-prompt-per-mtok",
        type=float,
        help="Maximum prompt price in USD per 1M tokens",
    )
    models.add_argument(
        "--max-completion-per-mtok",
        type=float,
        help="Maximum completion price in USD per 1M tokens",
    )
    models.add_argument(
        "--sort",
        choices=("id", "context", "prompt", "completion", "created"),
        default="id",
        help="Sort key",
    )
    models.add_argument("--reverse", action="store_true", help="Reverse sort order")
    models.add_argument("--limit", type=int, default=25, help="Maximum rows to print")

    show = subparsers.add_parser("show", help="Show details for one model")
    show.add_argument("model", help="Model id or canonical slug")

    subparsers.add_parser("credits", help="Show account credit totals for the current API key")

    chat = subparsers.add_parser("chat", help="Run a one-shot chat completion")
    chat.add_argument("--model", required=True, help="Model id")
    chat.add_argument("--prompt", help="User prompt (defaults to stdin if omitted)")
    chat.add_argument("--system", help="Optional system prompt")
    chat.add_argument("--max-tokens", type=int, default=256, help="Maximum completion tokens")
    chat.add_argument("--temperature", type=float, default=0.0, help="Sampling temperature")
    chat.add_argument(
        "--provider-sort",
        choices=("throughput", "price", "latency"),
        help="Provider routing preference",
    )
    chat.add_argument("--nitro", action="store_true", help="Append :nitro to the model id")

    probe = subparsers.add_parser("probe", help="Run small repeated completions and time them")
    probe.add_argument("--model", action="append", required=True, help="Model id; may be repeated")
    probe.add_argument("--prompt", default=DEFAULT_PROBE_PROMPT, help="Probe prompt")
    probe.add_argument("--system", help="Optional system prompt")
    probe.add_argument("--runs", type=int, default=3, help="Runs per model")
    probe.add_argument("--max-tokens", type=int, default=32, help="Maximum completion tokens")
    probe.add_argument("--temperature", type=float, default=0.0, help="Sampling temperature")
    probe.add_argument(
        "--provider-sort",
        choices=("throughput", "price", "latency"),
        help="Provider routing preference",
    )
    probe.add_argument("--nitro", action="store_true", help="Append :nitro to each model id")
    probe.add_argument("--delay", type=float, default=0.0, help="Sleep between probe calls in seconds")

    return parser.parse_args()


def main() -> int:
    load_local_env()
    args = parse_args()
    try:
        if args.command == "models":
            return cmd_models(args)
        if args.command == "show":
            return cmd_show(args)
        if args.command == "credits":
            return cmd_credits(args)
        if args.command == "chat":
            return cmd_chat(args)
        if args.command == "probe":
            return cmd_probe(args)
        raise OpenRouterError(f"unsupported command: {args.command}")
    except OpenRouterError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def cmd_models(args: argparse.Namespace) -> int:
    models = fetch_models(args)
    filtered = [model for model in models if model_matches(model, args)]
    filtered.sort(key=lambda model: model_sort_key(model, args.sort), reverse=args.reverse)
    if args.limit > 0:
        filtered = filtered[: args.limit]
    if args.json:
        print(json.dumps(filtered, indent=2))
        return 0
    rows = []
    for model in filtered:
        rows.append(
            {
                "id": model.get("id", ""),
                "ctx": str(model.get("context_length", "")),
                "max_out": str(model.get("top_provider", {}).get("max_completion_tokens", "")),
                "prompt$/1M": format_price_per_million(model.get("pricing", {}).get("prompt")),
                "completion$/1M": format_price_per_million(model.get("pricing", {}).get("completion")),
                "modality": model.get("architecture", {}).get("modality", ""),
            }
        )
    print_table(rows, ["id", "ctx", "max_out", "prompt$/1M", "completion$/1M", "modality"])
    print(f"\nmodels={len(filtered)}", file=sys.stderr)
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    models = fetch_models(args)
    model = find_model(models, args.model)
    if model is None:
        raise OpenRouterError(f"model not found: {args.model}")
    if args.json:
        print(json.dumps(model, indent=2))
        return 0
    summary = {
        "id": model.get("id"),
        "name": model.get("name"),
        "canonical_slug": model.get("canonical_slug"),
        "context_length": model.get("context_length"),
        "max_completion_tokens": model.get("top_provider", {}).get("max_completion_tokens"),
        "pricing_per_1m": {
            "prompt": format_price_per_million(model.get("pricing", {}).get("prompt")),
            "completion": format_price_per_million(model.get("pricing", {}).get("completion")),
        },
        "architecture": model.get("architecture"),
        "supported_parameters": model.get("supported_parameters"),
        "description": model.get("description"),
    }
    print(json.dumps(summary, indent=2))
    return 0


def cmd_credits(args: argparse.Namespace) -> int:
    api_key = require_api_key(args)
    response = api_request(args, "credits", api_key=api_key)
    if args.json:
        print(json.dumps(response, indent=2))
        return 0
    data = response.get("data", {})
    total_credits = data.get("total_credits")
    total_usage = data.get("total_usage")
    remaining = None
    if total_credits is not None and total_usage is not None:
        try:
            remaining = float(total_credits) - float(total_usage)
        except (TypeError, ValueError):
            remaining = None
    print(
        json.dumps(
            {
                "total_credits": total_credits,
                "total_usage": total_usage,
                "remaining_credits": remaining,
            },
            indent=2,
        )
    )
    return 0


def cmd_chat(args: argparse.Namespace) -> int:
    api_key = require_api_key(args)
    prompt = read_prompt(args.prompt)
    model = routed_model(args.model, args.nitro)
    payload = chat_payload(
        model=model,
        prompt=prompt,
        system=args.system,
        max_tokens=args.max_tokens,
        temperature=args.temperature,
        provider_sort=args.provider_sort,
    )
    response = api_request(
        args,
        "chat/completions",
        method="POST",
        payload=payload,
        api_key=api_key,
    )
    if args.json:
        print(json.dumps(response, indent=2))
        return 0
    print(extract_text(response))
    return 0


def cmd_probe(args: argparse.Namespace) -> int:
    api_key = require_api_key(args)
    results: list[ProbeResult] = []
    for raw_model in args.model:
        model = routed_model(raw_model, args.nitro)
        for run in range(1, args.runs + 1):
            start = time.monotonic()
            try:
                response = api_request(
                    args,
                    "chat/completions",
                    method="POST",
                    payload=chat_payload(
                        model=model,
                        prompt=args.prompt,
                        system=args.system,
                        max_tokens=args.max_tokens,
                        temperature=args.temperature,
                        provider_sort=args.provider_sort,
                    ),
                    api_key=api_key,
                )
                usage = response.get("usage", {})
                elapsed_ms = (time.monotonic() - start) * 1000
                results.append(
                    ProbeResult(
                        model=model,
                        run=run,
                        ok=True,
                        duration_ms=elapsed_ms,
                        prompt_tokens=int_or_none(usage.get("prompt_tokens")),
                        completion_tokens=int_or_none(usage.get("completion_tokens")),
                    )
                )
            except Exception as exc:  # noqa: BLE001
                elapsed_ms = (time.monotonic() - start) * 1000
                results.append(
                    ProbeResult(
                        model=model,
                        run=run,
                        ok=False,
                        duration_ms=elapsed_ms,
                        prompt_tokens=None,
                        completion_tokens=None,
                        error=str(exc),
                    )
                )
            if args.delay > 0 and (run != args.runs or raw_model != args.model[-1]):
                time.sleep(args.delay)
    if args.json:
        print(json.dumps(build_probe_report(results), indent=2))
        return 0
    print_probe_report(results)
    return 0


def load_local_env() -> None:
    env_path = pathlib.Path(__file__).resolve().with_name(".env")
    if not env_path.exists():
        return
    try:
        contents = env_path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in contents.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, raw_value = stripped.split("=", 1)
        key = key.strip()
        if key not in ENV_KEYS or key in os.environ:
            continue
        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ[key] = value


def fetch_models(args: argparse.Namespace) -> list[dict[str, Any]]:
    response = api_request(args, "models", api_key=args.api_key)
    data = response.get("data")
    if not isinstance(data, list):
        raise OpenRouterError("models response did not contain a data list")
    return data


def model_matches(model: dict[str, Any], args: argparse.Namespace) -> bool:
    if model.get("context_length", 0) < args.min_context:
        return False
    if args.supports:
        supported = model.get("supported_parameters") or []
        if args.supports not in supported:
            return False
    if args.match:
        haystack = " ".join(
            str(value)
            for value in (model.get("id", ""), model.get("canonical_slug", ""), model.get("name", ""))
        )
        if re.search(args.match, haystack, flags=re.IGNORECASE) is None:
            return False
    prompt_price = price_per_million_decimal(model.get("pricing", {}).get("prompt"))
    completion_price = price_per_million_decimal(model.get("pricing", {}).get("completion"))
    if args.max_prompt_per_mtok is not None and prompt_price is not None and prompt_price > Decimal(str(args.max_prompt_per_mtok)):
        return False
    if (
        args.max_completion_per_mtok is not None
        and completion_price is not None
        and completion_price > Decimal(str(args.max_completion_per_mtok))
    ):
        return False
    return True


def model_sort_key(model: dict[str, Any], sort_key: str) -> Any:
    if sort_key == "context":
        return model.get("context_length", 0)
    if sort_key == "prompt":
        return price_per_million_decimal(model.get("pricing", {}).get("prompt")) or Decimal("Infinity")
    if sort_key == "completion":
        return price_per_million_decimal(model.get("pricing", {}).get("completion")) or Decimal("Infinity")
    if sort_key == "created":
        return model.get("created", 0)
    return model.get("id", "")


def find_model(models: list[dict[str, Any]], needle: str) -> dict[str, Any] | None:
    lowered = needle.lower()
    for field in ("id", "canonical_slug", "name"):
        for model in models:
            value = str(model.get(field, ""))
            if value.lower() == lowered:
                return model
    partials = []
    for model in models:
        haystack = " ".join(str(model.get(field, "")) for field in ("id", "canonical_slug", "name")).lower()
        if lowered in haystack:
            partials.append(model)
    if len(partials) == 1:
        return partials[0]
    return None


def chat_payload(
    *,
    model: str,
    prompt: str,
    system: str | None,
    max_tokens: int,
    temperature: float,
    provider_sort: str | None,
) -> dict[str, Any]:
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if provider_sort:
        payload["provider"] = {"sort": provider_sort}
    return payload


def routed_model(model: str, nitro: bool) -> str:
    if nitro and not model.endswith(":nitro"):
        return f"{model}:nitro"
    return model


def extract_text(response: dict[str, Any]) -> str:
    choices = response.get("choices") or []
    if not choices:
        raise OpenRouterError("chat response had no choices")
    message = choices[0].get("message", {})
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_parts = [part.get("text", "") for part in content if isinstance(part, dict)]
        return "".join(text_parts)
    raise OpenRouterError("chat response had no printable text content")


def api_request(
    args: argparse.Namespace,
    path: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    base = args.base_url.rstrip("/")
    url = path if path.startswith("http") else f"{base}/{path.lstrip('/')}"
    headers = {
        "Accept": "application/json",
        "User-Agent": "openrouter.py/1",
    }
    if args.referer:
        headers["HTTP-Referer"] = args.referer
    if args.title:
        headers["X-Title"] = args.title
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=args.timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise OpenRouterError(f"HTTP {exc.code} for {url}: {body}") from exc
    except urllib.error.URLError as exc:
        raise OpenRouterError(f"request failed for {url}: {exc}") from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise OpenRouterError(f"non-JSON response from {url}: {raw[:300]}") from exc


def require_api_key(args: argparse.Namespace) -> str:
    if args.api_key:
        return args.api_key
    raise OpenRouterError("missing API key; set OPENROUTER_API_KEY or pass --api-key")


def read_prompt(prompt: str | None) -> str:
    if prompt is not None:
        return prompt
    stdin = sys.stdin.read()
    if not stdin.strip():
        raise OpenRouterError("no prompt provided; pass --prompt or pipe stdin")
    return stdin


def price_per_million_decimal(raw: Any) -> Decimal | None:
    if raw in (None, ""):
        return None
    try:
        return Decimal(str(raw)) * Decimal("1000000")
    except (InvalidOperation, ValueError):
        return None


def format_price_per_million(raw: Any) -> str:
    value = price_per_million_decimal(raw)
    if value is None:
        return ""
    if value <= 0:
        return "free"
    return f"{value:.4f}"


def print_table(rows: list[dict[str, str]], columns: list[str]) -> None:
    if not rows:
        print("(no rows)")
        return
    widths = {column: len(column) for column in columns}
    for row in rows:
        for column in columns:
            widths[column] = max(widths[column], len(str(row.get(column, ""))))
    header = "  ".join(column.ljust(widths[column]) for column in columns)
    print(header)
    print("  ".join("-" * widths[column] for column in columns))
    for row in rows:
        print("  ".join(str(row.get(column, "")).ljust(widths[column]) for column in columns))


def build_probe_report(results: list[ProbeResult]) -> dict[str, Any]:
    grouped = group_probe_results(results)
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "runs": [
            {
                "model": result.model,
                "run": result.run,
                "ok": result.ok,
                "duration_ms": round(result.duration_ms, 3),
                "prompt_tokens": result.prompt_tokens,
                "completion_tokens": result.completion_tokens,
                "error": result.error,
            }
            for result in results
        ],
        "summary": [summarize_probe_group(model, group) for model, group in grouped.items()],
    }


def print_probe_report(results: list[ProbeResult]) -> None:
    grouped = group_probe_results(results)
    rows = []
    for model, group in grouped.items():
        summary = summarize_probe_group(model, group)
        rows.append(
            {
                "model": model,
                "ok": str(summary["ok_runs"]),
                "err": str(summary["error_runs"]),
                "mean_ms": f"{summary['mean_ms']:.1f}",
                "p50_ms": f"{summary['p50_ms']:.1f}",
                "p95_ms": f"{summary['p95_ms']:.1f}",
                "tok/s": "" if summary["mean_completion_tok_s"] is None else f"{summary['mean_completion_tok_s']:.2f}",
            }
        )
    print_table(rows, ["model", "ok", "err", "mean_ms", "p50_ms", "p95_ms", "tok/s"])


def group_probe_results(results: list[ProbeResult]) -> dict[str, list[ProbeResult]]:
    grouped: dict[str, list[ProbeResult]] = {}
    for result in results:
        grouped.setdefault(result.model, []).append(result)
    return grouped


def summarize_probe_group(model: str, results: list[ProbeResult]) -> dict[str, Any]:
    durations = [result.duration_ms for result in results]
    ok_runs = [result for result in results if result.ok]
    completion_tps = []
    for result in ok_runs:
        if result.completion_tokens is not None and result.duration_ms > 0:
            completion_tps.append(result.completion_tokens / (result.duration_ms / 1000.0))
    return {
        "model": model,
        "runs": len(results),
        "ok_runs": len(ok_runs),
        "error_runs": len(results) - len(ok_runs),
        "mean_ms": statistics.fmean(durations) if durations else 0.0,
        "p50_ms": percentile(durations, 0.50),
        "p95_ms": percentile(durations, 0.95),
        "mean_completion_tok_s": statistics.fmean(completion_tps) if completion_tps else None,
    }


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if q <= 0:
        return ordered[0]
    if q >= 1:
        return ordered[-1]
    index = max(0, min(len(ordered) - 1, math.ceil(q * len(ordered)) - 1))
    return ordered[index]


def int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    sys.exit(main())
