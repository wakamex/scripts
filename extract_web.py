#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "html2text>=2025.4.15",
#   "playwright>=1.54.0",
#   "readability-lxml>=0.8.4.1",
#   "requests>=2.32.4",
# ]
# ///
"""Extract the main content of an HTTP(S) page as Markdown."""

from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from html import unescape
from pathlib import Path
from typing import Sequence
from urllib.parse import urljoin, urlparse

import html2text
import requests
from lxml import html as lxml_html
from readability import Document

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
CHALLENGE_MARKERS = (
    "just a moment",
    "please wait for verification",
    "verifying your browser",
    "making sure you're not a bot",
    "attention required! | cloudflare",
)


@dataclass(frozen=True)
class FetchedPage:
    """Fetched HTML plus response metadata needed for validation."""

    html: str
    final_url: str
    content_type: str


def validate_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"expected an HTTP(S) URL: {url}")


def challenge_title(html: str) -> str | None:
    match = re.search(r"<title[^>]*>(.*?)</title>", html[:50_000], re.I | re.S)
    title = unescape(re.sub(r"\s+", " ", match.group(1))).strip().lower() if match else ""
    if any(marker in title for marker in CHALLENGE_MARKERS):
        return title
    lowered = html[:50_000].lower()
    if "challenge-platform" in lowered or "cf-chl-" in lowered:
        return title or "browser challenge"
    return None


def validate_page(page: FetchedPage) -> None:
    content_type = page.content_type.partition(";")[0].strip().lower()
    if content_type not in {"text/html", "application/xhtml+xml"}:
        raise RuntimeError(f"expected HTML but received {page.content_type or 'no type'}")
    challenge = challenge_title(page.html)
    if challenge is not None:
        raise RuntimeError(f"received a challenge page: {challenge}")


def fetch_direct(url: str, timeout: float) -> FetchedPage:
    response = requests.get(
        url,
        headers={"User-Agent": USER_AGENT},
        timeout=timeout,
        allow_redirects=True,
    )
    response.raise_for_status()
    page = FetchedPage(
        html=response.text,
        final_url=response.url,
        content_type=response.headers.get("content-type", ""),
    )
    validate_page(page)
    return page


def fetch_browser(url: str, timeout: float, browser_name: str) -> FetchedPage:
    from playwright.sync_api import sync_playwright  # noqa: PLC0415

    with sync_playwright() as playwright:
        browser_type = getattr(playwright, browser_name)
        browser = browser_type.launch(headless=True)
        try:
            context = browser.new_context(user_agent=USER_AGENT)
            page = context.new_page()
            response = page.goto(
                url,
                timeout=int(timeout * 1000),
                wait_until="domcontentloaded",
            )
            if response is None:
                raise RuntimeError("browser navigation returned no response")
            if not response.ok:
                raise RuntimeError(f"browser navigation returned HTTP {response.status}")
            fetched = FetchedPage(
                html=page.content(),
                final_url=page.url,
                content_type=response.header_value("content-type") or "text/html",
            )
            validate_page(fetched)
            return fetched
        finally:
            browser.close()


def first_xpath(tree: object, expressions: Sequence[str]) -> str | None:
    for expression in expressions:
        values = tree.xpath(expression)
        for value in values:
            text = " ".join(str(value).split())
            if text:
                return text
    return None


def render_markdown(page: FetchedPage, min_chars: int = 200) -> str:
    tree = lxml_html.fromstring(page.html)
    canonical = first_xpath(
        tree,
        ("//link[translate(@rel,'CANONICAL','canonical')='canonical']/@href",),
    )
    author = first_xpath(
        tree,
        (
            "//meta[@name='author']/@content",
            "//meta[@property='article:author']/@content",
        ),
    )
    published = first_xpath(
        tree,
        (
            "//meta[@property='article:published_time']/@content",
            "//meta[@name='date']/@content",
            "//article//time/@datetime",
        ),
    )

    for element in tree.xpath("//nav|//header|//footer|//aside|//script|//style|//noscript"):
        parent = element.getparent()
        if parent is not None:
            parent.remove(element)
    cleaned_html = lxml_html.tostring(tree, encoding="unicode")
    document = Document(cleaned_html)
    title = " ".join(document.short_title().split())

    converter = html2text.HTML2Text(baseurl=page.final_url)
    converter.body_width = 0
    converter.ignore_images = False
    converter.ignore_links = False
    body = converter.handle(document.summary(html_partial=True)).strip()
    visible = re.sub(r"\s+", " ", re.sub(r"[#*_>`\[\]()]", "", body)).strip()
    if len(visible) < min_chars:
        raise RuntimeError(f"main-content extraction returned only {len(visible)} visible characters")

    source = urljoin(page.final_url, canonical) if canonical else page.final_url
    lines = [f"# {title or '(untitled page)'}", "", f"- Source: [link](<{source}>)"]
    if author:
        lines.append(f"- Author: {author}")
    if published:
        lines.append(f"- Published: {published}")
    lines.extend(["", body, ""])
    return "\n".join(lines)


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as output:
            temporary = Path(output.name)
            output.write(text)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("-o", "--output", type=Path)
    parser.add_argument(
        "--browser",
        choices=("firefox", "chromium", "webkit"),
        help="render with this browser instead of fetching HTML directly",
    )
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--min-chars", type=int, default=200)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        validate_url(args.url)
        page = (
            fetch_browser(args.url, args.timeout, args.browser)
            if args.browser
            else fetch_direct(args.url, args.timeout)
        )
        markdown = render_markdown(page, min_chars=args.min_chars)
        if args.output:
            atomic_write(args.output.resolve(), markdown)
            print(f"published {args.output.resolve()}", file=sys.stderr)
        else:
            sys.stdout.write(markdown)
    except (OSError, ValueError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
