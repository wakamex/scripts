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
import hashlib
import json
import math
import os
import pathlib
import re
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_TIMEOUT_SECONDS = 120
CACHE_DIR = pathlib.Path(os.environ.get("XDG_CACHE_HOME", pathlib.Path.home() / ".cache")) / "openrouter"
CACHE_TTL_MODELS = 3600  # 1 hour for model list
CACHE_TTL_STATS = 1800  # 30 minutes for stats (matches OpenRouter's 30-min window)
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

    tps = subparsers.add_parser("tps", help="Show live throughput and latency stats from OpenRouter")
    tps.add_argument("model", nargs="?", help="Model id or slug (e.g. openai/gpt-4o-mini)")
    tps.add_argument("--all", action="store_true", help="Show top models sorted by throughput")
    tps.add_argument("--match", help="Filter models by regex (with --all)")
    tps.add_argument("--limit", type=int, default=0, help="Max models to show (0=all)")
    tps.add_argument("--provider", help="Filter to a specific provider slug (e.g. openai, azure)")
    tps.add_argument("--sort", choices=("throughput", "latency", "requests"), default="throughput", help="Sort key")
    tps.add_argument("--reverse", action="store_true", help="Reverse sort order")
    tps.add_argument("--no-cache", action="store_true", help="Bypass cache")

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
        if args.command == "tps":
            return cmd_tps(args)
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


def cmd_tps(args: argparse.Namespace) -> int:
    if args.all or args.model is None:
        return cmd_tps_all(args)
    return cmd_tps_model(args)


def cmd_tps_model(args: argparse.Namespace) -> int:
    permaslug = resolve_permaslug(args, args.model)
    endpoints = fetch_endpoint_stats(permaslug, args.timeout, args.no_cache)
    eff_pricing = fetch_effective_pricing(permaslug, args.timeout, args.no_cache)
    bench = fetch_benchmarks(args.model, args.timeout, args.no_cache)

    if args.provider:
        lowered = args.provider.lower()
        endpoints = [ep for ep in endpoints if ep.get("provider_slug", "").lower() == lowered]
        if not endpoints:
            raise OpenRouterError(f"no endpoint for provider '{args.provider}'")

    sort_keys = {
        "throughput": lambda ep: (ep.get("stats") or {}).get("p50_throughput", 0),
        "latency": lambda ep: (ep.get("stats") or {}).get("p50_latency", float("inf")),
        "requests": lambda ep: (ep.get("stats") or {}).get("request_count", 0),
    }
    endpoints.sort(key=sort_keys[args.sort], reverse=(args.sort != "latency") ^ args.reverse)

    if args.json:
        out = []
        for ep in endpoints:
            stats = ep.get("stats") or {}
            eff = eff_pricing.get(ep.get("provider_display_name", ""))
            entry: dict[str, Any] = {
                "provider": ep.get("provider_display_name", ""),
                "provider_slug": ep.get("provider_slug", ""),
                "quantization": ep.get("quantization", ""),
                "p50_throughput": stats.get("p50_throughput"),
                "p50_latency": stats.get("p50_latency"),
                "p99_latency": stats.get("p99_latency"),
                "request_count": stats.get("request_count"),
                "window_minutes": stats.get("window_minutes"),
                "pricing": ep.get("pricing"),
                "effective_pricing": eff,
                "max_completion_tokens": ep.get("max_completion_tokens"),
                "context_length": ep.get("context_length"),
                "status": ep.get("status"),
            }
            out.append(entry)
        print(json.dumps(out, indent=2))
        return 0

    rows = []
    for ep in endpoints:
        stats = ep.get("stats") or {}
        if not stats:
            continue
        pricing = ep.get("pricing") or {}
        eff = eff_pricing.get(ep.get("provider_display_name", ""))
        rows.append({
            "provider": ep.get("provider_display_name", ""),
            "p50_tps": fmt_num(stats.get("p50_throughput")),
            "p50_lat": fmt_ms(stats.get("p50_latency")),
            "p99_lat": fmt_ms(stats.get("p99_latency")),
            "reqs": fmt_num(stats.get("request_count")),
            "ctx": fmt_ctx(ep.get("context_length")),
            "in$/M": format_price_per_million(pricing.get("prompt")),
            "out$/M": format_price_per_million(pricing.get("completion")),
            "eff_in$/M": fmt_eff_price(eff, "effectiveInputPrice") if eff else "",
            "eff_out$/M": fmt_eff_price(eff, "effectiveOutputPrice") if eff else "",
            "cache%": fmt_pct(eff.get("cacheHitRate")) if eff else "",
            "quant": ep.get("quantization", ""),
        })
    if bench:
        parts = [f"{k}={v}" for k, v in bench.items() if v is not None]
        if parts:
            print(f"benchmarks: {', '.join(parts)}", file=sys.stderr)
    print_table(rows, ["provider", "p50_tps", "p50_lat", "p99_lat", "reqs", "ctx", "in$/M", "out$/M", "eff_in$/M", "eff_out$/M", "cache%", "quant"])
    return 0


def cmd_tps_all(args: argparse.Namespace) -> int:
    models = fetch_frontend_models("throughput-high-to-low", args.timeout, args.no_cache)

    if args.match:
        models = [m for m in models if re.search(args.match, m.get("slug", ""), re.IGNORECASE)]

    seen_slugs: set[str] = set()
    deduped = []
    for m in models:
        slug = m.get("slug", "")
        if slug not in seen_slugs:
            seen_slugs.add(slug)
            deduped.append(m)
    models = deduped

    permaslugs = [m.get("permaslug", m.get("slug", "")) for m in models]
    stats_map = fetch_stats_batch(permaslugs, args.timeout, args.no_cache)
    eff_map = fetch_effective_pricing_batch(permaslugs, args.timeout, args.no_cache)
    model_slugs = [m.get("slug", "") for m in models]
    bench_map = fetch_benchmarks_batch(model_slugs, args.timeout, args.no_cache)

    # Build results with resolved stats, then sort client-side
    entries = []
    for m in models:
        ps = m.get("permaslug", "")
        best = pick_best_endpoint(stats_map.get(ps, []), args.sort, args.provider)
        stats = (best.get("stats") or {}) if best else {}
        eff = eff_map.get(ps, {}).get(best.get("provider_display_name", "")) if best else None
        slug = m.get("slug", "")
        bench = bench_map.get(slug, {})
        entries.append({"model": m, "endpoint": best, "stats": stats, "eff": eff, "bench": bench})

    sort_keys = {
        "throughput": lambda e: e["stats"].get("p50_throughput", 0) or 0,
        "latency": lambda e: e["stats"].get("p50_latency", float("inf")) or float("inf"),
        "requests": lambda e: e["stats"].get("request_count", 0) or 0,
    }
    reverse = (args.sort != "latency") ^ args.reverse
    entries.sort(key=sort_keys[args.sort], reverse=reverse)
    entries = [e for e in entries if e["stats"]]
    if args.limit > 0:
        entries = entries[: args.limit]

    if not entries:
        raise OpenRouterError("no models with stats found")

    if args.json:
        out = []
        for e in entries:
            slug = e["model"].get("slug", "")
            best = e["endpoint"]
            stats = e["stats"]
            eff = e.get("eff")
            out.append({
                "model": slug,
                "provider": best.get("provider_display_name", "") if best else "",
                "p50_throughput": stats.get("p50_throughput"),
                "p50_latency": stats.get("p50_latency"),
                "p99_latency": stats.get("p99_latency"),
                "request_count": stats.get("request_count"),
                "pricing": (best.get("pricing") or {}) if best else {},
                "effective_pricing": eff,
                "context_length": best.get("context_length") if best else e["model"].get("context_length"),
                "quantization": best.get("quantization", "") if best else "",
            })
        print(json.dumps(out, indent=2))
        return 0

    rows = []
    for e in entries:
        slug = e["model"].get("slug", "")
        best = e["endpoint"]
        stats = e["stats"]
        pricing = (best.get("pricing") or {}) if best else {}
        eff = e.get("eff")
        bench = e.get("bench", {})
        ctx = best.get("context_length") if best else e["model"].get("context_length")
        rows.append({
            "model": slug,
            "provider": best.get("provider_display_name", "") if best else "",
            "p50_tps": fmt_num(stats.get("p50_throughput")),
            "p50_lat": fmt_ms(stats.get("p50_latency")),
            "p99_lat": fmt_ms(stats.get("p99_latency")),
            "reqs": fmt_num(stats.get("request_count")),
            "ctx": fmt_ctx(ctx),
            "in$/M": format_price_per_million(pricing.get("prompt")),
            "out$/M": format_price_per_million(pricing.get("completion")),
            "eff_in$/M": fmt_eff_price(eff, "effectiveInputPrice") if eff else "",
            "eff_out$/M": fmt_eff_price(eff, "effectiveOutputPrice") if eff else "",
            "cache%": fmt_pct(eff.get("cacheHitRate")) if eff else "",
            "intel": fmt_score(bench.get("intelligence")),
            "code": fmt_score(bench.get("coding")),
            "agent": fmt_score(bench.get("agentic")),
        })
    print_table(rows, ["model", "provider", "p50_tps", "p50_lat", "p99_lat", "reqs", "ctx", "in$/M", "out$/M", "eff_in$/M", "eff_out$/M", "cache%", "intel", "code", "agent"])
    return 0


def pick_best_endpoint(
    endpoints: list[dict[str, Any]], sort: str, provider_filter: str | None
) -> dict[str, Any] | None:
    if not endpoints:
        return None
    candidates = endpoints
    if provider_filter:
        lowered = provider_filter.lower()
        candidates = [ep for ep in endpoints if ep.get("provider_slug", "").lower() == lowered]
    candidates = [ep for ep in candidates if ep.get("stats")]
    if not candidates:
        return None
    if sort == "latency":
        return min(candidates, key=lambda ep: (ep.get("stats") or {}).get("p50_latency", float("inf")))
    return max(candidates, key=lambda ep: (ep.get("stats") or {}).get("p50_throughput", 0))


def fetch_endpoint_stats(
    permaslug: str, timeout: float, no_cache: bool
) -> list[dict[str, Any]]:
    cache_key = f"stats_{cache_key_for_url(permaslug)}"
    if not no_cache:
        cached = cache_get(cache_key, CACHE_TTL_STATS)
        if cached is not None:
            return cached

    url = "https://openrouter.ai/api/frontend/stats/endpoint"
    request = urllib.request.Request(
        f"{url}?{urllib.parse.urlencode({'permaslug': permaslug})}",
        headers={"Accept": "application/json", "User-Agent": "openrouter.py/1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise OpenRouterError(f"HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise OpenRouterError(f"request failed: {exc}") from exc

    endpoints = raw.get("data", [])
    cache_set(cache_key, endpoints)
    if not endpoints:
        raise OpenRouterError(f"no endpoints found for {permaslug}")
    return endpoints


def fetch_frontend_models(
    order: str, timeout: float, no_cache: bool
) -> list[dict[str, Any]]:
    cache_key = f"frontend_models_{order}"
    if not no_cache:
        cached = cache_get(cache_key, CACHE_TTL_MODELS)
        if cached is not None:
            return cached

    url = f"https://openrouter.ai/api/frontend/models?order={urllib.parse.quote(order)}"
    request = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": "openrouter.py/1"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise OpenRouterError(f"HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise OpenRouterError(f"request failed: {exc}") from exc

    models = raw.get("data", [])
    cache_set(cache_key, models)
    return models


def fetch_effective_pricing(
    permaslug: str, timeout: float, no_cache: bool
) -> dict[str, dict[str, Any]]:
    """Returns {providerName: {effectiveInputPrice, effectiveOutputPrice, cacheHitRate}}."""
    cache_key = f"effprice_{cache_key_for_url(permaslug)}"
    if not no_cache:
        cached = cache_get(cache_key, CACHE_TTL_STATS)
        if cached is not None:
            return cached

    url = "https://openrouter.ai/api/frontend/stats/effective-pricing"
    request = urllib.request.Request(
        f"{url}?{urllib.parse.urlencode({'permaslug': permaslug})}",
        headers={"Accept": "application/json", "User-Agent": "openrouter.py/1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError):
        return {}

    result: dict[str, dict[str, Any]] = {}
    for summary in raw.get("data", {}).get("providerSummaries", []):
        name = summary.get("providerName", "")
        if name:
            result[name] = summary
    cache_set(cache_key, result)
    return result


def fetch_benchmarks(slug: str, timeout: float, no_cache: bool) -> dict[str, float]:
    """Returns {intelligence: score, coding: score, agentic: score} for a model."""
    cache_key = f"bench_{cache_key_for_url(slug)}"
    if not no_cache:
        cached = cache_get(cache_key, CACHE_TTL_MODELS)
        if cached is not None:
            return cached

    url = "https://openrouter.ai/api/internal/v1/artificial-analysis-benchmarks"
    request = urllib.request.Request(
        f"{url}?{urllib.parse.urlencode({'slug': slug})}",
        headers={"Accept": "application/json", "User-Agent": "openrouter.py/1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError):
        cache_set(cache_key, {})
        return {}

    result: dict[str, float] = {}
    entries = raw.get("data", [])
    if entries:
        # Use first entry (highest capability variant)
        evals = entries[0].get("benchmark_data", {}).get("evaluations", {})
        for key, short in [
            ("artificial_analysis_intelligence_index", "intelligence"),
            ("artificial_analysis_coding_index", "coding"),
            ("artificial_analysis_agentic_index", "agentic"),
        ]:
            if key in evals and evals[key] is not None:
                result[short] = evals[key]
    cache_set(cache_key, result)
    return result


def fetch_benchmarks_batch(
    slugs: list[str], timeout: float, no_cache: bool
) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    to_fetch: list[str] = []
    for slug in slugs:
        if not no_cache:
            cached = cache_get(f"bench_{cache_key_for_url(slug)}", CACHE_TTL_MODELS)
            if cached is not None:
                result[slug] = cached
                continue
        to_fetch.append(slug)

    def _fetch_one(slug: str) -> tuple[str, dict[str, float]]:
        return slug, fetch_benchmarks(slug, timeout, no_cache=True)

    if to_fetch:
        print(f"fetching benchmarks for {len(to_fetch)} models...", file=sys.stderr)
        with ThreadPoolExecutor(max_workers=20) as pool:
            futures = {pool.submit(_fetch_one, s): s for s in to_fetch}
            for future in as_completed(futures):
                slug, data = future.result()
                result[slug] = data
    return result


def fetch_effective_pricing_batch(
    permaslugs: list[str], timeout: float, no_cache: bool
) -> dict[str, dict[str, dict[str, Any]]]:
    """Returns {permaslug: {providerName: summary}}."""
    result: dict[str, dict[str, dict[str, Any]]] = {}
    to_fetch: list[str] = []
    for ps in permaslugs:
        if not no_cache:
            cached = cache_get(f"effprice_{cache_key_for_url(ps)}", CACHE_TTL_STATS)
            if cached is not None:
                result[ps] = cached
                continue
        to_fetch.append(ps)

    def _fetch_one(ps: str) -> tuple[str, dict[str, dict[str, Any]]]:
        try:
            return ps, fetch_effective_pricing(ps, timeout, no_cache=True)
        except Exception:
            cache_set(f"effprice_{cache_key_for_url(ps)}", {})
            return ps, {}

    if to_fetch:
        print(f"fetching effective pricing for {len(to_fetch)} models...", file=sys.stderr)
        with ThreadPoolExecutor(max_workers=20) as pool:
            futures = {pool.submit(_fetch_one, ps): ps for ps in to_fetch}
            for future in as_completed(futures):
                ps, data = future.result()
                result[ps] = data
    return result


def fetch_stats_batch(
    permaslugs: list[str], timeout: float, no_cache: bool
) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    to_fetch: list[str] = []
    for ps in permaslugs:
        if not no_cache:
            cached = cache_get(f"stats_{cache_key_for_url(ps)}", CACHE_TTL_STATS)
            if cached is not None:
                result[ps] = cached
                continue
        to_fetch.append(ps)

    def _fetch_one(ps: str) -> tuple[str, list[dict[str, Any]]]:
        try:
            return ps, fetch_endpoint_stats(ps, timeout, no_cache=True)
        except OpenRouterError:
            cache_set(f"stats_{cache_key_for_url(ps)}", [])
            return ps, []

    if to_fetch:
        print(f"fetching stats for {len(to_fetch)} models...", file=sys.stderr)
        with ThreadPoolExecutor(max_workers=20) as pool:
            futures = {pool.submit(_fetch_one, ps): ps for ps in to_fetch}
            for future in as_completed(futures):
                ps, endpoints = future.result()
                result[ps] = endpoints
    return result


def fmt_num(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.1f}"
    return str(value)


def fmt_ms(value: Any) -> str:
    if value is None:
        return ""
    return f"{value:.0f}ms"


def fmt_eff_price(eff: dict[str, Any], key: str) -> str:
    val = eff.get(key)
    if val is None:
        return ""
    return f"{val:.4f}"


def fmt_pct(value: Any) -> str:
    if value is None:
        return ""
    return f"{value * 100:.0f}%"


def fmt_score(value: Any) -> str:
    if value is None:
        return ""
    return f"{value:.1f}" if isinstance(value, float) else str(value)


def fmt_ctx(value: Any) -> str:
    if value is None:
        return ""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return str(value)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.0f}M"
    if n >= 1_000:
        return f"{n // 1_000}k"
    return str(n)


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


def cache_get(key: str, ttl: float) -> Any | None:
    path = CACHE_DIR / f"{key}.json"
    try:
        if not path.exists():
            return None
        age = time.time() - path.stat().st_mtime
        if age > ttl:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def cache_set(key: str, data: Any) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = CACHE_DIR / f"{key}.json"
        path.write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        pass


def cache_key_for_url(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:16]


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


def resolve_permaslug(args: argparse.Namespace, needle: str) -> str:
    models = fetch_models(args)
    model = find_model(models, needle)
    if model is None:
        raise OpenRouterError(f"model not found: {needle}")
    return model.get("canonical_slug") or model.get("id", needle)


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
