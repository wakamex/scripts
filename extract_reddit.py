#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "beautifulsoup4>=4.13.4",
#   "html2text>=2025.4.15",
#   "requests>=2.32.4",
# ]
# ///
"""Extract a Reddit post and returned comment tree as Markdown."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlsplit, urlunsplit

import html2text
import requests
from bs4 import BeautifulSoup

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
HTTP_OK = 200
REDDIT_LISTING_COUNT = 2
COMMENT_REMAINDER_COUNT = 2
INSTANCE_LIST = "https://raw.githubusercontent.com/redlib-org/redlib-instances/main/instances.json"


class ExtractionError(RuntimeError):
    """No validated extraction route returned the requested content."""


def canonical_reddit_url(url: str) -> str:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or not (host == "reddit.com" or host.endswith(".reddit.com")):
        raise ExtractionError(f"not a Reddit URL: {url}")
    if "/comments/" not in parsed.path:
        raise ExtractionError(f"URL did not resolve to a Reddit comments path: {url}")
    path = re.sub(r"/+", "/", parsed.path).rstrip("/")
    return urlunsplit(("https", "www.reddit.com", path, "", ""))


def resolve_reddit_url(session: Any, url: str, timeout: float) -> str:
    parsed = urlsplit(url)
    if "/s/" not in parsed.path:
        return canonical_reddit_url(url)
    response = session.get(
        url,
        headers={"User-Agent": USER_AGENT},
        timeout=timeout,
        allow_redirects=True,
    )
    return canonical_reddit_url(response.url)


def reddit_json_url(canonical_url: str) -> str:
    parsed = urlsplit(canonical_url)
    return urlunsplit(("https", "old.reddit.com", f"{parsed.path.rstrip('/')}/.json", "", ""))


def request_json(
    session: Any,
    url: str,
    timeout: float,
    *,
    allow_text_content_type: bool = False,
) -> Any:
    response = session.get(
        url,
        headers={"User-Agent": USER_AGENT},
        timeout=timeout,
        allow_redirects=True,
    )


def permalink_ids(canonical_url: str) -> tuple[str, str | None]:
    segments = [segment for segment in urlsplit(canonical_url).path.split("/") if segment]
    try:
        comments_index = segments.index("comments")
        post_id = segments[comments_index + 1]
    except (ValueError, IndexError) as error:
        raise ExtractionError(f"invalid Reddit comments path: {canonical_url}") from error
    remainder = segments[comments_index + 2 :]
    comment_id = remainder[-1] if len(remainder) >= COMMENT_REMAINDER_COUNT else None
    return post_id, comment_id


def reddit_embed_url(canonical_url: str) -> str:
    return urlunsplit(("https", "embed.reddit.com", urlsplit(canonical_url).path, "", ""))
    if response.status_code != HTTP_OK:
        raise ExtractionError(f"{url} returned HTTP {response.status_code}")
    content_type = response.headers.get("content-type", "").lower()
    if "json" not in content_type and not allow_text_content_type:
        raise ExtractionError(f"{url} returned {content_type or 'an unknown content type'}, not JSON")
    try:
        return response.json()
    except ValueError as error:
        raise ExtractionError(f"{url} returned invalid JSON") from error


def format_time(timestamp: object) -> str:
    try:
        return datetime.fromtimestamp(float(timestamp), tz=UTC).isoformat()
    except (TypeError, ValueError, OSError):
        return "unknown"


def child_things(listing: object) -> list[dict[str, Any]]:
    if not isinstance(listing, dict):
        return []
    data = listing.get("data")
    if not isinstance(data, dict):
        return []
    children = data.get("children")
    return [item for item in children if isinstance(item, dict)] if isinstance(children, list) else []


def render_comment_json(item: dict[str, Any], depth: int = 0) -> list[str]:
    if item.get("kind") != "t1" or not isinstance(item.get("data"), dict):
        return []
    data = item["data"]
    author = data.get("author") or "[deleted]"
    heading = "#" * min(3 + depth, 6)
    lines = [
        f"{heading} Comment by u/{author}",
        "",
        f"- Score: {data.get('score', 'unknown')}",
        f"- Created: {format_time(data.get('created_utc'))}",
    ]
    permalink = data.get("permalink")
    if isinstance(permalink, str) and permalink:
        lines.append(f"- Permalink: [link](<https://www.reddit.com{permalink}>)")
    lines.extend(["", str(data.get("body") or "[deleted]").strip(), ""])
    replies = data.get("replies")
    if isinstance(replies, dict):
        for reply in child_things(replies):
            lines.extend(render_comment_json(reply, depth + 1))
    return lines


def render_reddit_json(payload: object, canonical_url: str) -> str:
    if not isinstance(payload, list) or len(payload) < REDDIT_LISTING_COUNT:
        raise ExtractionError("Reddit JSON did not contain post and comment listings")
    posts = child_things(payload[0])
    if len(posts) != 1 or not isinstance(posts[0].get("data"), dict):
        raise ExtractionError("Reddit JSON did not contain one post")
    post = posts[0]["data"]
    title = str(post.get("title") or "(untitled Reddit post)").strip()
    lines = [
        f"# {title}",
        "",
        f"- Source: [Reddit](<{canonical_url}>)",
        f"- Subreddit: r/{post.get('subreddit', 'unknown')}",
        f"- Author: u/{post.get('author') or '[deleted]'}",
        f"- Score: {post.get('score', 'unknown')}",
        f"- Created: {format_time(post.get('created_utc'))}",
    ]
    body = str(post.get("selftext") or "").strip()
    external_url = post.get("url_overridden_by_dest")
    if isinstance(external_url, str) and external_url:
        lines.append(f"- Linked URL: [link](<{external_url}>)")
    if body:
        lines.extend(["", "## Post", "", body])

    comments = child_things(payload[1])
    lines.extend(["", "## Comments", ""])
    if comments:
        for comment in comments:
            lines.extend(render_comment_json(comment))
    else:
        lines.append("[No comments were returned.]")
    return "\n".join(lines).rstrip() + "\n"


def html_fragment_to_markdown(fragment: Any, base_url: str) -> str:
    converter = html2text.HTML2Text(baseurl=base_url)
    converter.body_width = 0
    converter.ignore_images = False
    converter.ignore_links = False
    return converter.handle(str(fragment)).strip()


def render_reddit_embed(html: str, canonical_url: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    telemetry = soup.select_one("shreddit-screenview-data[data]")
    if telemetry is None:
        raise ExtractionError("official Reddit embed returned no source metadata")
    try:
        metadata = json.loads(telemetry.get("data", ""))
    except json.JSONDecodeError as error:
        raise ExtractionError("official Reddit embed metadata was invalid") from error
    post_id, comment_id = permalink_ids(canonical_url)

    if comment_id is not None:
        comment = metadata.get("comment")
        if not isinstance(comment, dict) or comment.get("id") != f"t1_{comment_id}":
            raise ExtractionError("official Reddit embed returned the wrong comment")
        wrapper = soup.select_one(f"#t1_{comment_id}-embed-wrapper")
        body = soup.select_one(f"#t1_{comment_id}-post-rtjson-content")
        author_node = wrapper.select_one('[data-testid="user-name"]') if wrapper else None
        if wrapper is None or body is None or author_node is None:
            raise ExtractionError("official Reddit embed omitted the requested comment body")
        author = " ".join(author_node.get_text(" ", strip=True).split())
        post_link = wrapper.select_one('[data-testid="post-link"]')
        post_url = post_link.get("href") if post_link else None
        if isinstance(post_url, str):
            post_url = canonical_reddit_url(post_url)
        score = comment.get("score", "unknown")
        created = format_time(float(comment.get("created_timestamp", 0)) / 1000)
        subreddit = metadata.get("subreddit", {})
        subreddit_name = subreddit.get("name", "unknown") if isinstance(subreddit, dict) else "unknown"
        lines = [
            f"# Reddit comment by u/{author}",
            "",
            f"- Source: [Reddit](<{canonical_url}>)",
            "- Scope: requested embedded comment only; replies are not included",
            f"- Subreddit: r/{subreddit_name}",
            f"- Author: u/{author}",
            f"- Score: {score}",
            f"- Created: {created}",
        ]
        if post_url:
            lines.append(f"- Parent post: [link](<{post_url}>)")
        lines.extend(
            [
                "",
                "## Comment",
                "",
                html_fragment_to_markdown(body, "https://www.reddit.com"),
            ]
        )
        return "\n".join(lines).rstrip() + "\n"

    post = metadata.get("post")
    if not isinstance(post, dict) or post.get("id") != f"t3_{post_id}":
        raise ExtractionError("official Reddit embed returned the wrong post")
    wrapper = soup.select_one(f"#t3_{post_id}-embed-wrapper")
    title_node = wrapper.select_one("#embed-title") if wrapper else None
    body = soup.select_one(f"#t3_{post_id}-post-rtjson-content")
    if wrapper is None or title_node is None:
        raise ExtractionError("official Reddit embed omitted the requested post")
    title = " ".join(title_node.get_text(" ", strip=True).split())
    author_node = wrapper.select_one('[data-testid="user-name"]')
    subreddit = metadata.get("subreddit", {})
    subreddit_name = subreddit.get("name", "unknown") if isinstance(subreddit, dict) else "unknown"
    lines = [
        f"# {title}",
        "",
        f"- Source: [Reddit](<{canonical_url}>)",
        "- Scope: embedded post only; comments are not included",
        f"- Subreddit: r/{subreddit_name}",
        f"- Created: {format_time(float(post.get('created_timestamp', 0)) / 1000)}",
    ]
    if author_node:
        lines.append(f"- Author: u/{author_node.get_text(' ', strip=True)}")
    if body and body.get_text(" ", strip=True):
        lines.extend(["", "## Post", "", html_fragment_to_markdown(body, "https://www.reddit.com")])
    return "\n".join(lines).rstrip() + "\n"


def request_reddit_embed(session: Any, canonical_url: str, timeout: float) -> str:
    target = reddit_embed_url(canonical_url)
    response = session.get(
        target,
        headers={"User-Agent": USER_AGENT},
        timeout=timeout,
        allow_redirects=True,
    )
    if response.status_code != HTTP_OK:
        raise ExtractionError(f"official Reddit embed returned HTTP {response.status_code}")
    return render_reddit_embed(response.text, canonical_url)


def render_redlib_html(html: str, canonical_url: str, instance: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    title_text = soup.title.get_text(" ", strip=True).lower() if soup.title else ""
    lowered = html[:100_000].lower()
    if any(
        marker in lowered or marker in title_text
        for marker in (
            "verifying your browser",
            "making sure you're not a bot",
            "checking you are not a bot",
            "just a moment",
        )
    ):
        raise ExtractionError(f"{instance} returned a browser challenge")

    title_node = soup.select_one("h1.post_title")
    if title_node is None:
        raise ExtractionError(f"{instance} returned no post title")
    title = " ".join(title_node.get_text(" ", strip=True).split())
    header = soup.select_one("p.post_header")
    author = header.select_one(".post_author") if header else None
    subreddit = header.select_one(".post_subreddit") if header else None
    created = header.select_one(".created") if header else None
    score = soup.select_one("div.post_score")
    body = soup.select_one("div.post_body")
    lines = [
        f"# {title}",
        "",
        f"- Source: [Reddit](<{canonical_url}>)",
        f"- Retrieved through: {instance}",
    ]
    if subreddit:
        lines.append(f"- Subreddit: {subreddit.get_text(' ', strip=True)}")
    if author:
        lines.append(f"- Author: {author.get_text(' ', strip=True)}")
    if score:
        lines.append(f"- Score: {score.get('title') or score.get_text(' ', strip=True)}")
    if created:
        lines.append(f"- Created: {created.get('title') or created.get_text(' ', strip=True)}")
    if body and body.get_text(" ", strip=True):
        lines.extend(["", "## Post", "", html_fragment_to_markdown(body, instance)])

    comments = soup.select("div.comment")
    lines.extend(["", "## Comments", ""])
    if not comments:
        lines.append("[No comments were returned.]")
    for comment in comments:
        depth = len(comment.find_parents("div", class_="comment"))
        details = comment.find("details", class_="comment_right", recursive=False)
        if details is None:
            continue
        comment_body = details.find("div", class_="comment_body", recursive=False)
        data = details.find("summary", class_="comment_data", recursive=False)
        author_node = data.select_one(".comment_author") if data else None
        created_node = data.select_one(".created") if data else None
        score_node = comment.find("p", class_="comment_score")
        heading = "#" * min(3 + depth, 6)
        lines.extend(
            [
                f"{heading} Comment by {author_node.get_text(' ', strip=True) if author_node else '[deleted]'}",
                "",
            ]
        )
        if score_node:
            lines.append(f"- Score: {score_node.get('title') or score_node.get_text(' ', strip=True)}")
        if created_node:
            lines.append(f"- Created: {created_node.get('title') or created_node.get_text(' ', strip=True)}")
        lines.extend(
            [
                "",
                html_fragment_to_markdown(comment_body, instance)
                if comment_body
                else "[Filtered or unavailable content]",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def redlib_instances(session: Any, timeout: float) -> list[str]:
    payload = request_json(
        session,
        INSTANCE_LIST,
        timeout,
        allow_text_content_type=True,
    )
    entries = payload.get("instances") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        raise ExtractionError("official Redlib instance list has an invalid shape")
    return [item["url"].rstrip("/") for item in entries if isinstance(item, dict) and isinstance(item.get("url"), str)]


def extract_reddit(
    url: str,
    *,
    timeout: float = 20.0,
    instance: str | None = None,
) -> tuple[str, str, str]:
    session = requests.Session()
    canonical = resolve_reddit_url(session, url, timeout)
    _, comment_id = permalink_ids(canonical)
    try:
        payload = request_json(session, reddit_json_url(canonical), timeout)
        return render_reddit_json(payload, canonical), canonical, "old Reddit JSON"
    except ExtractionError as error:
        print(f"old Reddit JSON unavailable: {error}", file=sys.stderr)

    if comment_id is not None:
        try:
            return (
                request_reddit_embed(session, canonical, timeout),
                canonical,
                "official Reddit embed (requested comment only)",
            )
        except ExtractionError as error:
            print(f"official Reddit embed unavailable: {error}", file=sys.stderr)

    instances = [instance.rstrip("/")] if instance else redlib_instances(session, timeout)
    path = urlsplit(canonical).path
    failures: list[str] = []
    for base_url in instances:
        target = f"{base_url}{path}"
        try:
            response = session.get(
                target,
                headers={"User-Agent": USER_AGENT},
                timeout=timeout,
                allow_redirects=True,
            )
            if response.status_code != HTTP_OK:
                raise ExtractionError(f"HTTP {response.status_code}")
            markdown = render_redlib_html(response.text, canonical, base_url)
            return markdown, canonical, base_url
        except (ExtractionError, requests.RequestException) as error:
            failures.append(f"{base_url}: {error}")
            print(f"Redlib unavailable: {failures[-1]}", file=sys.stderr)
    if comment_id is None:
        try:
            return (
                request_reddit_embed(session, canonical, timeout),
                canonical,
                "official Reddit embed (post only)",
            )
        except ExtractionError as error:
            print(f"official Reddit embed unavailable: {error}", file=sys.stderr)
    raise ExtractionError("no structured Reddit or Redlib route returned the requested content")


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
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--instance", help="test only this Redlib base URL after JSON")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        markdown, canonical, route = extract_reddit(
            args.url,
            timeout=args.timeout,
            instance=args.instance,
        )
        if args.output:
            atomic_write(args.output.resolve(), markdown)
            print(
                f"published {args.output.resolve()} from {canonical} via {route}",
                file=sys.stderr,
            )
        else:
            sys.stdout.write(markdown)
    except (OSError, ExtractionError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
