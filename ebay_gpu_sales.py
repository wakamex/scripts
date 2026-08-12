#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "beautifulsoup4>=4.13",
#   "playwright>=1.54",
# ]
# ///
"""Refresh the eBay GPU sold-listing snapshot used by mihaicosma.com.

The sold-search page requires an eBay session. Run --login once in a graphical
session, complete any eBay verification, then use --refresh for headless runs.
Saved credentials and browser state never enter the generated data.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import sqlite3
import statistics
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup


SITE_DIR = Path("/code/website")
SOURCE_PATH = SITE_DIR / "gpu-sales-source.json"
DATA_PATH = SITE_DIR / "gpu-sales-data.js"
STATE_PATH = SITE_DIR / ".private" / "ebay-state.json"
SNAPSHOT_DIR = SITE_DIR / ".private" / "ebay-snapshots"
SEARCHES = {
    "3090": "https://www.ebay.ca/sch/i.html?_nkw=rtx+3090+-ti+-parts+-repair&_sacat=27386&LH_Sold=1&LH_Complete=1&LH_ItemCondition=3000&_sop=13",
    "3080": "https://www.ebay.ca/sch/i.html?_nkw=rtx+3080+10gb+-ti+-parts+-repair+-laptop&_sacat=27386&LH_Sold=1&LH_Complete=1&LH_ItemCondition=3000&_sop=13",
    "4080-32": "https://www.ebay.ca/sch/i.html?_nkw=rtx+4080+32gb+-5090+-parts+-repair&_sacat=27386&LH_Sold=1&LH_Complete=1&_sop=13",
}
RESEARCH_QUERIES = {"3090": "rtx 3090", "3080": "rtx 3080 10gb"}
EBAY_FINAL_VALUE_FEE = 0.136
EBAY_PER_ORDER_FEE_CAD = 0.40

BAD_TITLE = re.compile(
    r"\b(?:for parts|not working|repair|water\s*block|cooler only|box only|gaming pc|desktop|laptop|"
    r"3090\s*ti|3080\s*ti|egpu|enclosure|as[ -]is|untested|broken|defective|read|no display|"
    r"only\s+displayport|only\s+\w+\s+works|passive server|watercooled|waterforce\s+wb|"
    r"lot of|\d+\s*(?:x|pcs?)\b)\b",
    re.I,
)
ITEM_ID = re.compile(r"/itm/(?:[^/?]+/)?(\d{9,15})")
MONEY = re.compile(r"(?:(C|US)\s*)?\$\s*([\d,]+(?:\.\d{1,2})?)", re.I)
SOLD_DATE = re.compile(
    r"(?:Sold\s+)?(?:on\s+)?(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)?,?\s*"
    r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{1,2})",
    re.I,
)
SOLD_DATE_DMY = re.compile(
    r"\bSold\s+(\d{1,2})\s+"
    r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{4})\b",
    re.I,
)


def atomic_write(path: Path, content: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False, encoding="utf-8") as handle:
        handle.write(content)
        temporary = Path(handle.name)
    temporary.chmod(mode)
    temporary.replace(path)


def clean_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme or "https", parts.netloc or "www.ebay.ca", parts.path, "", ""))


def parse_amount(text: str) -> tuple[float, str] | None:
    match = MONEY.search(text.replace("\xa0", " "))
    if not match:
        return None
    currency = "CAD" if (match.group(1) or "").upper() == "C" else "USD"
    return float(match.group(2).replace(",", "")), currency


def card_nodes(soup: BeautifulSoup) -> list[Any]:
    selectors = ["li.s-card", "li.s-item", "div.s-card", "div.s-item"]
    for selector in selectors:
        nodes = soup.select(selector)
        if nodes:
            return nodes
    return []


def infer_gpu(title: str) -> str | None:
    if BAD_TITLE.search(title):
        return None
    if re.search(r"\b4080(?:\s+super)?\b", title, re.I) and re.search(r"\b32\s*GB\b", title, re.I):
        return "4080-32"
    if re.search(r"\bmodified\b", title, re.I):
        return None
    if re.search(r"\b3090\b", title, re.I):
        return "3090"
    if re.search(r"\b3080\b", title, re.I) and not re.search(r"\b12\s*GB\b", title, re.I):
        return "3080"
    return None


def parse_sold_date(text: str, retrieved: datetime) -> str | None:
    day_first = SOLD_DATE_DMY.search(text)
    if day_first:
        return datetime.strptime(
            f"{day_first.group(1)} {day_first.group(2)} {day_first.group(3)}", "%d %b %Y"
        ).isoformat()
    match = SOLD_DATE.search(text)
    if not match:
        return None
    date = datetime.strptime(f"{match.group(1)} {match.group(2)} {retrieved.year}", "%b %d %Y")
    if date.date() > retrieved.date():
        date = date.replace(year=date.year - 1)
    return date.isoformat()


def first_text(node: Any, selectors: list[str]) -> str:
    for selector in selectors:
        child = node.select_one(selector)
        if child:
            value = child.get_text(" ", strip=True)
            if value:
                return value
    return ""


def parse_html(path: Path, retrieved: datetime) -> list[dict[str, Any]]:
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
    results: list[dict[str, Any]] = []
    for card in card_nodes(soup):
        link = card.select_one('a[href*="/itm/"]')
        if not link or not link.get("href"):
            continue
        id_match = ITEM_ID.search(link["href"])
        if not id_match:
            continue
        title = first_text(card, [".s-card__title", ".s-item__title", "h3"])
        title = re.sub(r"\s*Opens in a new window or tab\s*$", "", title, flags=re.I)
        title = re.sub(r"^New Listing\s+", "", title, flags=re.I).replace("\u2014", "-")
        gpu = infer_gpu(title)
        if not gpu:
            continue
        price_text = first_text(card, [".s-card__price", ".s-item__price"])
        price = parse_amount(price_text)
        if not price:
            continue
        attribute_rows = [node.get_text(" ", strip=True) for node in card.select(".s-card__attribute-row")]
        if any(re.search(r"best offer accepted", value, re.I) for value in attribute_rows):
            continue
        shipping_text = next(
            (
                value
                for value in attribute_rows
                if re.search(r"(?:free|\$[\d,.]+)\s+(?:shipping|delivery)", value, re.I)
            ),
            first_text(card, [".s-card__shipping", ".s-item__shipping", ".s-item__logisticsCost"]),
        )
        if not shipping_text:
            continue
        if re.search(r"free\s+(?:shipping|delivery)", shipping_text, re.I):
            shipping = (0.0, price[1])
        else:
            shipping = parse_amount(shipping_text) or (0.0, price[1])
        sold_text = first_text(card, [".s-card__caption", ".s-item__title--tagblock", ".POSITIVE", ".s-item__ended-date"])
        sold_at = parse_sold_date(sold_text, retrieved)
        if not sold_at:
            continue
        age_days = (retrieved.date() - datetime.fromisoformat(sold_at).date()).days
        maximum_age = 120 if gpu == "4080-32" else 45
        if not 0 <= age_days <= maximum_age:
            continue
        item_cad = price[0] if price[1] == "CAD" else price[0] * 1.4
        if gpu == "3090" and not 1200 <= item_cad <= 2300:
            continue
        if gpu == "3080" and not 250 <= item_cad <= 750:
            continue
        if gpu == "4080-32" and not 1800 <= item_cad <= 4500:
            continue
        condition = first_text(card, [".s-card__subtitle", ".SECONDARY_INFO"]) or "Used"
        seller_text = next((value for value in reversed(attribute_rows) if "% positive" in value), "")
        seller = re.sub(r"\s+\d+(?:\.\d+)?% positive.*$", "", seller_text).strip() or "unknown"
        origin_text = next(
            (value for value in attribute_rows if value.lower().startswith("from ")),
            "",
        )
        seller_country = origin_text[5:].strip() if origin_text else "Canada"
        results.append(
            {
                "id": id_match.group(1),
                "gpu": gpu,
                "sold_at": sold_at,
                "title": title,
                "item_price": price[0],
                "item_currency": price[1],
                "shipping": shipping[0],
                "shipping_currency": shipping[1],
                "shipping_label": shipping_text or "No separate charge shown",
                "condition": condition,
                "seller": seller,
                "seller_country": seller_country,
                "url": clean_url(link["href"]),
            }
        )
    return results


def cad_rate(currency: str, rates: dict[str, float]) -> float:
    if currency == "CAD":
        return 1.0
    try:
        return rates[currency]
    except KeyError as error:
        raise ValueError(f"Missing CAD conversion rate for {currency}") from error


def normalize(records: list[dict[str, Any]], rates: dict[str, float]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in sorted(records, key=lambda row: row["sold_at"], reverse=True):
        if record["id"] in seen:
            continue
        seen.add(record["id"])
        if "item_cad" not in record:
            record["item_cad"] = round(
                record.pop("item_price") * cad_rate(record.pop("item_currency"), rates), 2
            )
        if "shipping_cad" not in record:
            record["shipping_cad"] = round(
                record.pop("shipping") * cad_rate(record.pop("shipping_currency"), rates), 2
            )
        record["total_cad"] = round(record["item_cad"] + record["shipping_cad"], 2)
        record.setdefault("seller_country", "Canada")
        normalized.append(record)
    return normalized


def shipping_model(
    records: list[dict[str, Any]], buyer_locations: dict[str, Any] | None = None
) -> dict[str, Any]:
    north_american = [
        row for row in records if row["seller_country"] in {"Canada", "United States"}
    ]
    fallback_us_share = sum(row["seller_country"] == "United States" for row in north_american) / len(
        north_american
    )
    by_gpu: dict[str, dict[str, float]] = {}
    for gpu in ("3090", "3080"):
        gpu_rows = [row for row in records if row["gpu"] == gpu]
        domestic = [
            row["shipping_cad"]
            for row in gpu_rows
            if row["seller_country"] == "Canada" and row["shipping_cad"] > 0
        ]
        cross_border = [
            row["shipping_cad"]
            for row in gpu_rows
            if row["seller_country"] == "United States" and row["shipping_cad"] > 0
        ]
        if not domestic or not cross_border:
            raise ValueError(f"Insufficient Canada/US shipping observations for RTX {gpu}")
        domestic_median = statistics.median(domestic)
        cross_border_median = statistics.median(cross_border)
        counts = (buyer_locations or {}).get("counts", {}).get(gpu, {})
        north_american_buyers = counts.get("Canada", 0) + counts.get("United States", 0)
        us_share = (
            counts.get("United States", 0) / north_american_buyers
            if north_american_buyers
            else fallback_us_share
        )
        blended = us_share * cross_border_median + (1 - us_share) * domestic_median
        by_gpu[gpu] = {
            "canada_cad": round(domestic_median, 2),
            "us_cad": round(cross_border_median, 2),
            "blended_cad": round(blended, 2),
            "us_buyer_weight": round(us_share, 4),
            "buyer_counts": counts,
        }
    return {
        "weight_source": "eBay Product Research buyer locations" if buyer_locations else "seller-origin proxy",
        "us_origin_sales": sum(row["seller_country"] == "United States" for row in records),
        "canada_origin_sales": sum(row["seller_country"] == "Canada" for row in records),
        "by_gpu": by_gpu,
    }


def percentile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("Cannot calculate a percentile of an empty sample")
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def seller_weights(rows: list[dict[str, Any]], window_days: int = 14) -> list[float]:
    dates = [datetime.fromisoformat(row["sold_at"]).date().toordinal() for row in rows]
    weights: list[float] = []
    for index, row in enumerate(rows):
        seller = row.get("seller", "unknown")
        if seller == "unknown":
            weights.append(1.0)
            continue
        nearby = sum(
            other.get("seller") == seller and abs(dates[index] - dates[other_index]) <= window_days
            for other_index, other in enumerate(rows)
        )
        weights.append(1 / math.sqrt(max(1, nearby)))
    return weights


def weighted_median(values: list[float], weights: list[float]) -> float:
    ordered = sorted(zip(values, weights), key=lambda pair: pair[0])
    threshold = sum(weights) / 2
    cumulative = 0.0
    for value, weight in ordered:
        cumulative += weight
        if cumulative >= threshold:
            return value
    return ordered[-1][0]


def local_log_fit(
    x_values: list[float],
    log_prices: list[float],
    target: float,
    base_weights: list[float],
    robust_weights: list[float],
    bandwidth_days: float = 14,
    minimum_points: int = 8,
) -> float:
    distances = sorted(abs(value - target) for value in x_values)
    adaptive = distances[min(minimum_points - 1, len(distances) - 1)] if distances else bandwidth_days
    bandwidth = max(bandwidth_days, adaptive, 0.001)
    weights = []
    for value, base, robust in zip(x_values, base_weights, robust_weights):
        ratio = abs(value - target) / bandwidth
        kernel = (1 - ratio**3) ** 3 if ratio < 1 else 0.0
        weights.append(kernel * base * robust)
    total = sum(weights)
    if total <= 0:
        nearest = min(range(len(x_values)), key=lambda index: abs(x_values[index] - target))
        return log_prices[nearest]
    mean_x = sum(weight * value for weight, value in zip(weights, x_values)) / total
    mean_y = sum(weight * value for weight, value in zip(weights, log_prices)) / total
    denominator = sum(weight * (value - mean_x) ** 2 for weight, value in zip(weights, x_values))
    slope = (
        sum(
            weight * (x_value - mean_x) * (y_value - mean_y)
            for weight, x_value, y_value in zip(weights, x_values, log_prices)
        )
        / denominator
        if denominator > 1e-12
        else 0.0
    )
    return mean_y + slope * (target - mean_x)


def robust_weights_for(
    rows: list[dict[str, Any]], base_weights: list[float], price_field: str
) -> list[float]:
    x_values = [datetime.fromisoformat(row["sold_at"]).date().toordinal() for row in rows]
    log_prices = [math.log(row[price_field]) for row in rows]
    robust = [1.0] * len(rows)
    for _ in range(2):
        fitted = [
            local_log_fit(x_values, log_prices, target, base_weights, robust)
            for target in x_values
        ]
        residuals = [abs(actual - estimate) for actual, estimate in zip(log_prices, fitted)]
        scale = statistics.median(residuals)
        if scale <= 1e-12:
            break
        robust = [
            (1 - (residual / (6 * scale)) ** 2) ** 2 if residual < 6 * scale else 0.0
            for residual in residuals
        ]
    return robust


def blended_estimate(
    rows: list[dict[str, Any]],
    target: float,
    base_weights: list[float],
    robust_weights: list[float],
    price_field: str,
    half_life_days: float = 10,
) -> float:
    x_values = [datetime.fromisoformat(row["sold_at"]).date().toordinal() for row in rows]
    log_prices = [math.log(row[price_field]) for row in rows]
    lowess = math.exp(local_log_fit(x_values, log_prices, target, base_weights, robust_weights))
    eligible = [index for index, value in enumerate(x_values) if value <= target]
    if not eligible:
        eligible = [min(range(len(x_values)), key=lambda index: abs(x_values[index] - target))]
    median_values = [rows[index][price_field] for index in eligible]
    median_weights = [
        base_weights[index] * 0.5 ** ((target - x_values[index]) / half_life_days)
        for index in eligible
    ]
    recent_median = weighted_median(median_values, median_weights)
    return 0.70 * lowess + 0.30 * recent_median


def trend_for(
    rows: list[dict[str, Any]], price_field: str, bootstrap_samples: int = 200
) -> dict[str, Any]:
    rows = sorted(rows, key=lambda row: row["sold_at"])
    timestamps = [datetime.fromisoformat(row["sold_at"]).date().toordinal() for row in rows]
    start_day, end_day = math.floor(min(timestamps)), math.ceil(max(timestamps))
    grid = list(range(start_day, end_day + 1))
    base = seller_weights(rows)
    robust = robust_weights_for(rows, base, price_field)
    fitted = [blended_estimate(rows, target, base, robust, price_field) for target in grid]

    by_day: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_day.setdefault(row["sold_at"][:10], []).append(row)
    blocks = list(by_day.values())
    rng = random.Random(f"ebay-gpu-{rows[0]['gpu']}-{rows[-1]['sold_at'][:10]}")
    bootstrap_curves: list[list[float]] = []
    for _ in range(bootstrap_samples):
        sample = [dict(row) for _ in blocks for row in rng.choice(blocks)]
        sample_base = seller_weights(sample)
        sample_robust = robust_weights_for(sample, sample_base, price_field)
        bootstrap_curves.append(
            [
                blended_estimate(sample, target, sample_base, sample_robust, price_field)
                for target in grid
            ]
        )
    points = []
    for index, target in enumerate(grid):
        distribution = [curve[index] for curve in bootstrap_curves]
        points.append(
            {
                "date": date.fromordinal(target).isoformat(),
                "value_cad": round(fitted[index], 2),
                "lower_cad": round(percentile(distribution, 0.10), 2),
                "upper_cad": round(percentile(distribution, 0.90), 2),
            }
        )
    return {
        "gpu": rows[0]["gpu"],
        "metric": price_field,
        "latest_cad": points[-1]["value_cad"],
        "lower_cad": points[-1]["lower_cad"],
        "upper_cad": points[-1]["upper_cad"],
        "points": points,
    }


def midpoint_trend(buyer: dict[str, Any], seller: dict[str, Any]) -> dict[str, Any]:
    points = []
    for buyer_point, seller_point in zip(buyer["points"], seller["points"]):
        if buyer_point["date"] != seller_point["date"]:
            raise ValueError("Buyer and seller trend dates do not match")
        points.append(
            {
                "date": buyer_point["date"],
                "value_cad": round((buyer_point["value_cad"] + seller_point["value_cad"]) / 2, 2),
                "lower_cad": seller_point["value_cad"],
                "upper_cad": buyer_point["value_cad"],
            }
        )
    return {
        "gpu": buyer["gpu"],
        "metric": "buy_sell_midpoint_cad",
        "latest_cad": points[-1]["value_cad"],
        "lower_cad": points[-1]["lower_cad"],
        "upper_cad": points[-1]["upper_cad"],
        "points": points,
    }


def observed_trend(rows: list[dict[str, Any]], price_field: str) -> dict[str, Any]:
    by_day: dict[str, list[float]] = {}
    for row in rows:
        by_day.setdefault(row["sold_at"][:10], []).append(row[price_field])
    points = []
    for sold_day, values in sorted(by_day.items()):
        value = round(statistics.median(values), 2)
        points.append(
            {"date": sold_day, "value_cad": value, "lower_cad": value, "upper_cad": value}
        )
    return {
        "gpu": rows[0]["gpu"],
        "metric": f"observed_{price_field}",
        "latest_cad": points[-1]["value_cad"],
        "lower_cad": points[-1]["value_cad"],
        "upper_cad": points[-1]["value_cad"],
        "points": points,
    }


def publish(source_path: Path, data_path: Path) -> int:
    document = json.loads(source_path.read_text(encoding="utf-8"))
    document["sales"] = normalize(document["sales"], document.get("cad_rates", {"USD": 1.40}))
    shipping = shipping_model(document["sales"], document.get("buyer_locations"))
    for row in document["sales"]:
        if row["gpu"] == "4080-32":
            row["seller_net_cad"] = None
            row["midpoint_cad"] = None
            continue
        expected_shipping = shipping["by_gpu"][row["gpu"]]["blended_cad"]
        row["seller_net_cad"] = round(
            row["total_cad"] * (1 - EBAY_FINAL_VALUE_FEE)
            - EBAY_PER_ORDER_FEE_CAD
            - expected_shipping,
            2,
        )
        row["midpoint_cad"] = round(
            (row["total_cad"] + row["seller_net_cad"]) / 2,
            2,
        )
    document["model"] = {
        "description": "70% robust local-linear LOWESS on log price plus 30% exponentially weighted median",
        "lowess_bandwidth_days": 14,
        "median_half_life_days": 10,
        "seller_window_days": 14,
        "bootstrap_samples": 200,
        "interval": 0.80,
    }
    document["seller_estimate"] = {
        "final_value_fee": EBAY_FINAL_VALUE_FEE,
        "per_order_fee_cad": EBAY_PER_ORDER_FEE_CAD,
        "shipping": shipping,
        "shipping_assumption": "Product Research supplies buyer geography; positive US-to-Canada quotes proxy Canada-to-US cost",
    }
    buyer_trends = {
        gpu: trend_for(
            [row for row in document["sales"] if row["gpu"] == gpu], "total_cad"
        )
        for gpu in ("3090", "3080")
    }
    seller_trends = {
        gpu: trend_for(
            [row for row in document["sales"] if row["gpu"] == gpu], "seller_net_cad"
        )
        for gpu in ("3090", "3080")
    }
    document["trends"] = [
        midpoint_trend(buyer_trends[gpu], seller_trends[gpu]) for gpu in ("3090", "3080")
    ]
    modded_rows = [row for row in document["sales"] if row["gpu"] == "4080-32"]
    if modded_rows:
        document["trends"].append(observed_trend(modded_rows, "total_cad"))
    document["benchmarks"] = {
        gpu: {
            "buyer_cad": buyer_trends[gpu]["latest_cad"],
            "seller_cad": seller_trends[gpu]["latest_cad"],
            "midpoint_cad": round(
                (buyer_trends[gpu]["latest_cad"] + seller_trends[gpu]["latest_cad"]) / 2, 2
            ),
        }
        for gpu in ("3090", "3080")
    }
    if modded_rows:
        modded_trend = next(trend for trend in document["trends"] if trend["gpu"] == "4080-32")
        document["benchmarks"]["4080-32"] = {"buyer_cad": modded_trend["latest_cad"]}
    payload = json.dumps(document, ensure_ascii=False, separators=(",", ":"))
    atomic_write(data_path, f"window.GPU_SALES_DATA={payload};\n")
    return len(document["sales"])


def local_credentials() -> dict[str, str]:
    credentials = {key: os.environ[key] for key in ("EBAY_LOGIN", "EBAY_PASSWORD") if key in os.environ}
    env_path = SITE_DIR / ".env"
    if env_path.exists():
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key in {"EBAY_LOGIN", "EBAY_PASSWORD"}:
                credentials.setdefault(key, value)
    return credentials


def import_firefox_cookies(cookie_database: Path, state_path: Path) -> int:
    """Convert eBay cookies from a Firefox profile into Playwright storage state."""
    connection = sqlite3.connect(f"file:{cookie_database}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT name, value, host, path, expiry, isSecure, isHttpOnly, sameSite
            FROM moz_cookies
            WHERE host LIKE '%ebay.%' AND isPartitionedAttributeSet = 0
            """
        ).fetchall()
    finally:
        connection.close()
    if not rows:
        raise SystemExit(f"No eBay cookies found in {cookie_database}.")
    same_site = {0: "None", 1: "Lax", 2: "Strict"}
    cookies = [
        {
            "name": row["name"],
            "value": row["value"],
            "domain": row["host"],
            "path": row["path"],
            "expires": (row["expiry"] / 1000 if row["expiry"] > 100_000_000_000 else row["expiry"])
            if row["expiry"] > 0
            else -1,
            "httpOnly": bool(row["isHttpOnly"]),
            "secure": bool(row["isSecure"]),
            "sameSite": same_site.get(row["sameSite"], "None"),
        }
        for row in rows
    ]
    state_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(state_path, json.dumps({"cookies": cookies, "origins": []}), mode=0o600)
    return len(cookies)


def research_buyer_locations(state_path: Path, source_path: Path) -> dict[str, Any]:
    from playwright.sync_api import sync_playwright

    if not state_path.exists():
        raise SystemExit(f"No browser state at {state_path}. Run --login first.")
    counts: dict[str, dict[str, int]] = {}
    with sync_playwright() as playwright:
        browser = playwright.firefox.launch(headless=True)
        context = browser.new_context(
            storage_state=state_path,
            locale="en-CA",
            timezone_id="America/Toronto",
        )
        for gpu, query in RESEARCH_QUERIES.items():
            parameters = {
                "marketplace": "EBAY-CA",
                "keywords": query,
                "dayRange": 90,
                "categoryId": 27386,
                "offset": 0,
                "limit": 50,
                "tabName": "SOLD",
                "tz": "America/Toronto",
            }
            page = context.new_page()
            page.goto(
                f"https://www.ebay.ca/sh/research?{urlencode(parameters)}",
                wait_until="domcontentloaded",
                timeout=90_000,
            )
            page.wait_for_timeout(7_000)
            if "signin" in page.url:
                raise SystemExit("Seller Hub sign-in is required; refresh the saved Firefox cookies.")
            if "Total sold" not in page.locator("body").inner_text():
                raise SystemExit(f"Product Research returned no sold summary for RTX {gpu}.")
            page.get_by_role("button", name="More filters", exact=True).click()
            dialog = page.get_by_role("dialog")
            dialog.locator("input").nth(0).click()
            page.wait_for_timeout(400)
            location_text = page.get_by_role("listbox").inner_text()
            counts[gpu] = {
                country: int(value)
                for country, value in re.findall(r"^(.+?) \((\d+)\)$", location_text, re.M)
                if int(value)
            }
            page.close()
        context.storage_state(path=state_path)
        browser.close()
    os.chmod(state_path, 0o600)
    document = json.loads(source_path.read_text(encoding="utf-8"))
    document["buyer_locations"] = {
        "retrieved_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": "eBay Canada Seller Hub Product Research",
        "day_range": 90,
        "counts": counts,
    }
    atomic_write(source_path, json.dumps(document, indent=2, ensure_ascii=False) + "\n")
    return document["buyer_locations"]


def login(state_path: Path) -> None:
    from playwright.sync_api import sync_playwright

    state_path.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.firefox.launch(headless=False)
        context = browser.new_context(locale="en-CA", timezone_id="America/Toronto")
        page = context.new_page()
        page.goto("https://signin.ebay.ca/ws/eBayISAPI.dll?SignIn", wait_until="domcontentloaded", timeout=90_000)
        credentials = local_credentials()
        user = page.locator("#userid:visible")
        if user.count() and credentials.get("EBAY_LOGIN"):
            user.fill(credentials["EBAY_LOGIN"])
            page.get_by_role("button", name="Continue", exact=True).click()
            password = page.locator('input[type="password"]:visible')
            try:
                password.wait_for(timeout=20_000)
            except Exception:
                pass
            if password.count() and credentials.get("EBAY_PASSWORD"):
                password.fill(credentials["EBAY_PASSWORD"])
                page.get_by_role("button", name="Sign in", exact=True).click()
        print("Complete any eBay sign-in or verification in the browser window, then open the sold results.")
        print(SEARCHES["3090"])
        input("When sold results are visible, press Enter here to save the session: ")
        if "signin" in page.url or "captcha" in page.url:
            raise SystemExit("Sold results are not visible; session was not saved.")
        context.storage_state(path=state_path)
        browser.close()
    os.chmod(state_path, 0o600)


def refresh(state_path: Path, source_path: Path, headless: bool) -> int:
    from playwright.sync_api import sync_playwright

    if not state_path.exists():
        raise SystemExit(f"No browser state at {state_path}. Run --login first.")
    retrieved = datetime.now().astimezone()
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    with sync_playwright() as playwright:
        browser = playwright.firefox.launch(headless=headless)
        context = browser.new_context(
            storage_state=state_path,
            locale="en-CA",
            timezone_id="America/Toronto",
        )
        for gpu, url in SEARCHES.items():
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=90_000)
            page.wait_for_timeout(4_000)
            if "signin" in page.url or "captcha" in page.url or page.title().startswith("Error Page"):
                raise SystemExit("eBay session expired or verification is required. Run --login again.")
            path = SNAPSHOT_DIR / f"{retrieved:%Y%m%d-%H%M}-{gpu}.html"
            atomic_write(path, page.content(), mode=0o600)
            paths.append(path)
            page.close()
        context.storage_state(path=state_path)
        browser.close()

    records: list[dict[str, Any]] = []
    for path in paths:
        parsed = parse_html(path, retrieved)
        if not parsed:
            raise SystemExit(f"No valid sold listings parsed from {path}; leaving published data unchanged.")
        records.extend(parsed)
    counts = {gpu: sum(row["gpu"] == gpu for row in records) for gpu in SEARCHES}
    if counts["3090"] < 5 or counts["3080"] < 5 or counts["4080-32"] < 1:
        raise SystemExit(f"Implausibly small sample {counts}; leaving published data unchanged.")
    document = {
        "retrieved_at": retrieved.isoformat(timespec="seconds"),
        "destination": "Canada",
        "method": "Authenticated eBay Canada sold searches, used working cards only.",
        "cad_rates": {"USD": 1.40},
        "sales": records,
    }
    if source_path.exists():
        previous = json.loads(source_path.read_text(encoding="utf-8"))
        if "buyer_locations" in previous:
            document["buyer_locations"] = previous["buyer_locations"]
    atomic_write(source_path, json.dumps(document, indent=2, ensure_ascii=False) + "\n")
    return len(records)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--login", action="store_true", help="interactively create an eBay browser session")
    parser.add_argument("--refresh", action="store_true", help="retrieve sold-search results using saved session")
    parser.add_argument(
        "--research-buyer-locations",
        action="store_true",
        help="retrieve 90-day Product Research buyer-location counts",
    )
    parser.add_argument("--headed", action="store_true", help="show the browser during --refresh")
    parser.add_argument("--html", action="append", type=Path, default=[], help="parse a saved eBay sold-search HTML file")
    parser.add_argument("--firefox-cookies", type=Path, help="import eBay session cookies from a Firefox cookies.sqlite")
    parser.add_argument("--source", type=Path, default=SOURCE_PATH)
    parser.add_argument("--output", type=Path, default=DATA_PATH)
    parser.add_argument("--state", type=Path, default=STATE_PATH)
    args = parser.parse_args()

    if args.firefox_cookies:
        count = import_firefox_cookies(args.firefox_cookies, args.state)
        print(f"Imported {count} eBay cookies into {args.state}.")
    if args.login:
        login(args.state)
    if args.refresh:
        count = refresh(args.state, args.source, not args.headed)
        print(f"Collected {count} sold listings.")
    if args.html:
        retrieved = datetime.now().astimezone()
        records = [row for path in args.html for row in parse_html(path, retrieved)]
        if not records:
            raise SystemExit("No valid sold listings found; source was not changed.")
        document = {
            "retrieved_at": retrieved.isoformat(timespec="seconds"),
            "destination": "Canada",
            "method": "User-saved authenticated eBay Canada sold-search HTML.",
            "cad_rates": {"USD": 1.40},
            "sales": records,
        }
        if args.source.exists():
            previous = json.loads(args.source.read_text(encoding="utf-8"))
            if "buyer_locations" in previous:
                document["buyer_locations"] = previous["buyer_locations"]
        atomic_write(args.source, json.dumps(document, indent=2, ensure_ascii=False) + "\n")
    if args.research_buyer_locations:
        locations = research_buyer_locations(args.state, args.source)
        print(f"Collected Product Research buyer locations: {locations['counts']}.")
    count = publish(args.source, args.output)
    print(f"Published {count} records to {args.output}.")


if __name__ == "__main__":
    main()
