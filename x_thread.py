#!/usr/bin/env python3
"""Reconstruct an X (Twitter) self-reply thread without auth.

The input may be any post in a thread. fxtwitter exposes the parent of a reply,
so we first walk same-author parents back to the root. It does not enumerate
subsequent replies, and X syndication `conversation_count` is unreliable (often
reports 1-2 for a 7-part thread).

This walks the thread via the twstalker.com mirror, which renders a *rolling
window* of the thread on each status page: the root tweet's page shows only the
first parts, but each subsequent status page extends the window by ~1 part. We
walk forward (jump to the highest thread id seen, re-fetch) until every part is
captured, accumulating verbatim "~k/N~ ..." bodies across pages, and enrich with
clean per-tweet text from fxtwitter where available.

Hard-won implementation notes:
  - MUST use curl, not urllib: twstalker/Cloudflare returns 403 to Python's TLS
    fingerprint even with a browser UA, but serves curl fine.
  - twstalker is flaky: a fetch often returns a tiny Cloudflare stub. Retry each
    fetch until the body is non-trivial (>50KB). A good render is ~200-500KB.
  - Explicit reply metadata is stronger evidence than text markers. Markers such
    as 1/7, 🧵, or "a thread" are only needed for forward discovery from a root.
  - An unnumbered parent chain proves the recovered prefix, but not that the last
    recovered post is the end. JSON and text output call that boundary out.

Usage:
  x_thread.py https://x.com/user/status/123        # full URL
  x_thread.py 123 --user screenname                # bare id + --user
  x_thread.py https://x.com/i/status/123           # /i/ URL (user auto-detected)
  x_thread.py <url> --json                         # JSON output
  x_thread.py <url> --hops 10                       # max walk hops (default 9)
  x_thread.py <url> --single                        # fetch exactly this post
"""
import argparse
import html as _html
import json
import re
import subprocess
import sys
import time

UA = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
FX = "https://api.fxtwitter.com"
TW = "https://twstalker.com"
TR = "https://threadreaderapp.com"
STUB = 50_000  # bytes; below this twstalker returned a Cloudflare stub
HTTP_OK = 200
MIN_THREAD_PARTS = 2
MAX_THREAD_PARTS = 50
MIN_BODY_LENGTH = 8


def curl(url, retries=10, min_bytes=0):
    """Fetch via curl.

    Twstalker rejects Python's TLS fingerprint and intermittently returns a
    small Cloudflare stub, so retry until the response meets ``min_bytes``.
    """
    for _ in range(retries):
        try:
            out = subprocess.run(
                ["curl", "-sL", "-A", UA, "--max-time", "20", url],
                capture_output=True, timeout=25, check=False,
            ).stdout.decode("utf-8", "replace")
        except Exception:
            out = ""
        if len(out) >= min_bytes:
            return out
        time.sleep(0.8)
    return out


def fx_tweet(user, tid, retries=3):
    """Fetch a post, retrying until fxtwitter returns the requested post."""
    requested_id = str(tid)
    for attempt in range(retries):
        raw = curl(f"{FX}/{user}/status/{requested_id}", retries=1, min_bytes=1)
        try:
            data = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            data = None

        if isinstance(data, dict) and data.get("code") == HTTP_OK:
            tweet = data.get("tweet")
            if isinstance(tweet, dict):
                author = tweet.get("author")
                screen_name = author.get("screen_name") if isinstance(author, dict) else None
                if str(tweet.get("id") or "") == requested_id and screen_name:
                    return {
                        "id": requested_id,
                        "text": (tweet.get("text") or "").strip(),
                        "ts": tweet.get("created_timestamp"),
                        "author": screen_name,
                        "replying_to_status": tweet.get("replying_to_status"),
                    }

        if attempt + 1 < retries:
            time.sleep(0.8)
    return None


def threadreader(root_id):
    """Return Thread Reader's forward unroll when one is available.

    The response has clean text and real IDs in one request, but many fresh or
    niche threads have not been unrolled, so the mirror walk remains a fallback.
    """
    h = curl(f"{TR}/thread/{root_id}.html", retries=3)
    starts = [m.start() for m in re.finditer(r'<div id="tweet_\d+"', h)]
    if not starts:
        return []
    out = []
    for i, pos in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else pos + 8000
        chunk = h[pos:end]
        m = re.search(r'data-tweet="(\d+)"', chunk)
        tid = m.group(1) if m else None
        body = chunk[chunk.find('dir="auto">') + len('dir="auto">'):]
        # stop at the first embedded media/entity/action container or the thread-end dots
        body = re.split(r'<span class="entity|<div class="[a-z_-]*(?:media|action|meta)'
                        r'|• • •|Missing some Tweet', body)[0]
        body = re.sub(r"<br\s*/?>", "\n", body)
        txt = _html.unescape(re.sub(r"<[^>]+>", "", body)).strip()
        if tid and txt:
            out.append({"id": tid, "text": txt})
    return out


def parse_target(arg):
    m = re.search(r"(?:x|twitter)\.com/([^/]+)/status/(\d+)", arg)
    if m:
        u = m.group(1)
        return (None if u == "i" else u), m.group(2)
    m = re.search(r"(\d{15,})", arg)
    if m:
        return None, m.group(1)
    sys.exit(f"could not parse a tweet id from: {arg}")


def fetch_target(user_hint, target_id):
    """Fetch by ID, tolerating missing, renamed, or stale handles in URLs."""
    target = fx_tweet(user_hint or "i", target_id)
    if not target and user_hint:
        target = fx_tweet("i", target_id)
    return target


def parent_chain(target):
    """Return the same-author chain from its oldest recoverable post to target.

    The boolean is false only when a declared parent could not be fetched or a
    cycle was detected. A reply to somebody else is a valid boundary, not part
    of the author's self-reply thread.
    """
    chain = [target]
    seen = {target["id"]}
    author = (target.get("author") or "").casefold()

    while chain[-1].get("replying_to_status"):
        parent_id = chain[-1]["replying_to_status"]
        if parent_id in seen:
            return list(reversed(chain)), False

        parent = fx_tweet("i", parent_id)
        if not parent:
            return list(reversed(chain)), False
        if (parent.get("author") or "").casefold() != author:
            break

        seen.add(parent_id)
        chain.append(parent)

    return list(reversed(chain)), True


def thread_total(text):
    """Infer the expected length from root markers."""
    m = re.search(r"\b(\d+)\s*/\s*(\d+)\b", text)
    if m and m.group(1) == "1" and MIN_THREAD_PARTS <= int(m.group(2)) <= MAX_THREAD_PARTS:
        return int(m.group(2))
    if "🧵" in text or re.search(r"\bthread\b", text, re.I) or text.strip().startswith("1/"):
        return -1
    return 0


def bodies_from_html(html, n):
    """Extract '~k/N~ <text>' bodies. Returns {k:int -> text}. n=expected total (for /N match)."""
    out = {}
    pat = rf"~?(\d+)\s*/\s*{n if n > 0 else r'\d+'}\s*~?\s*([^<]{{3,500}})"
    for m in re.finditer(pat, html):
        k = int(m.group(1))
        txt = re.sub(r"\s+", " ", m.group(2)).strip()
        if len(txt) > MIN_BODY_LENGTH and (k not in out or len(txt) > len(out[k])):
            out[k] = txt
    return out


SNOWFLAKE_PER_SEC = 4_194_304_000  # twitter snowflake ids advance ~2^22 ms per second


def walk(user, root_id, n, hops, window):
    """Forward status-walk: accumulate part-bodies and ids until all N parts seen.

    Thread parts are consecutive snowflakes within seconds of the root, so the
    frontier only advances to ids within `window` seconds of the root id, so
    otherwise an unrelated later tweet on the same timeline hijacks the walk.
    """
    root_n = int(root_id)
    span = window * SNOWFLAKE_PER_SEC
    seen_ids = {root_id}
    parts = {}            # k -> text (from twstalker)
    frontier = root_id
    for _ in range(hops):
        html = curl(f"{TW}/{user}/status/{frontier}", min_bytes=STUB)
        ids = set(re.findall(r"status/(\d{15,})", html))
        seen_ids |= ids
        for k, txt in bodies_from_html(html, n).items():
            if k not in parts or len(txt) > len(parts[k]):
                parts[k] = txt
        if n > 0 and len(parts) >= n:
            break
        # advance to the largest id within the thread's snowflake window of the root
        near = [i for i in seen_ids if 0 <= int(i) - root_n <= span]
        nxt = max(near, key=int) if near else frontier
        if nxt == frontier:
            break  # no growth
        frontier = nxt
        time.sleep(0.4)
    return seen_ids, parts


def as_output_tweet(tweet, part, source="fxtwitter"):
    """Normalize a source record for stable text and JSON output."""
    return {
        "part": part,
        "id": tweet.get("id"),
        "text": tweet.get("text", ""),
        "ts": tweet.get("ts"),
        "author": tweet.get("author"),
        "source": source,
    }


def reconstruct(target_id, user_hint=None, hops=9, window=900, enrich=True, use_threadreader=True, single=False):  # noqa: PLR0912, PLR0913, PLR0915
    """Recover a post or its self-reply thread and return output plus scope metadata."""
    target = fetch_target(user_hint, target_id)
    if not target:
        who = f" for @{user_hint}" if user_hint else ""
        raise RuntimeError(f"could not fetch post {target_id}{who}")

    user = target.get("author") or user_hint
    if single:
        return {
            "user": user,
            "input_id": target_id,
            "root_id": target_id,
            "thread_detected": False,
            "expected_parts": None,
            "recovered": 1,
            "complete": True,
            "recovery": "single",
            "warnings": [],
            "tweets": [as_output_tweet(target, 1)],
        }

    chain, parents_complete = parent_chain(target)
    root = chain[0]
    root_id = root["id"]
    n = thread_total(root["text"])
    expected = n if n > 0 else None
    warnings = []
    if not parents_complete:
        warnings.append("same-author parent recovery stopped before reaching a verified boundary")

    tr = threadreader(root_id) if use_threadreader else []
    tr_ids = {tweet["id"] for tweet in tr}
    tr_valid = (
        len(tr) > 1
        and tr[0]["id"] == root_id
        and target_id in tr_ids
        and (n <= 0 or len(tr) >= n)
    )
    if tr_valid:
        out = [
            {
                "part": i + 1,
                "id": tweet["id"],
                "text": tweet["text"],
                "ts": None,
                "author": user,
                "source": "threadreader",
            }
            for i, tweet in enumerate(tr)
        ]
        complete = n > 0 and len(out) >= n
        if n <= 0:
            warnings.append("thread length is unnumbered; forward completion cannot be proven")
        recovery = "threadreader"
    elif n != 0:
        seen_ids, parts = walk(user, root_id, n, hops, window)
        known = {}
        for fallback_part, tweet in enumerate(chain, 1):
            marker = re.match(r"~?(\d+)\s*/\s*\d+", tweet["text"])
            part = int(marker.group(1)) if marker else fallback_part
            known[part] = tweet
            parts.setdefault(part, tweet["text"])

        enriched = {}
        if enrich:
            span = window * SNOWFLAKE_PER_SEC
            for tid in sorted(seen_ids, key=int):
                if not (0 <= int(tid) - int(root_id) <= span):
                    continue
                tweet = fx_tweet("i", tid)
                if not tweet or (tweet.get("author") or "").casefold() != user.casefold():
                    continue
                marker = re.match(r"~?(\d+)\s*/\s*\d+", tweet["text"])
                if marker:
                    enriched[int(marker.group(1))] = tweet
                time.sleep(0.12)

        total = n if n > 0 else max([*parts, *known, *enriched, 1])
        out = []
        for part in range(1, total + 1):
            if part in enriched:
                out.append(as_output_tweet(enriched[part], part))
            elif part in known:
                out.append(as_output_tweet(known[part], part))
            elif part in parts:
                out.append({
                    "part": part,
                    "id": None,
                    "text": parts[part],
                    "ts": None,
                    "author": user,
                    "source": "twstalker",
                })

        complete = n > 0 and {tweet["part"] for tweet in out} >= set(range(1, n + 1))
        if not complete:
            warnings.append("forward thread recovery is incomplete or its length is unknown")
        recovery = "numbered_walk" if n > 0 else "marker_walk"
    else:
        out = [as_output_tweet(tweet, i + 1) for i, tweet in enumerate(chain)]
        complete = len(chain) == 1 and parents_complete
        recovery = "parent_chain" if len(chain) > 1 else "single"
        if len(chain) > 1:
            warnings.append("thread length is unnumbered; recovered through the supplied post only")

    return {
        "user": user,
        "input_id": target_id,
        "root_id": root_id,
        "thread_detected": len(out) > 1 or n != 0,
        "expected_parts": expected,
        "recovered": len(out),
        "complete": complete,
        "recovery": recovery,
        "warnings": warnings,
        "tweets": out,
    }


def main():
    ap = argparse.ArgumentParser(description="Reconstruct an X self-reply thread (no auth).")
    ap.add_argument("target", help="tweet URL or bare id")
    ap.add_argument("--user", help="screen-name hint (normally auto-detected from the post ID)")
    ap.add_argument("--window", type=int, default=900,
                    help="seconds around root ts to keep as same thread (default 900)")
    ap.add_argument("--hops", type=int, default=9, help="max walk hops (default 9)")
    enrichment = ap.add_mutually_exclusive_group()
    enrichment.add_argument("--enrich", dest="enrich", action="store_true", default=True,
                            help="enrich discovered posts via fxtwitter (default)")
    enrichment.add_argument("--no-enrich", dest="enrich", action="store_false",
                            help="skip per-post fxtwitter enrichment during mirror discovery")
    ap.add_argument("--no-threadreader", action="store_true",
                    help="skip the threadreaderapp primary source; go straight to the twstalker walk")
    ap.add_argument("--single", action="store_true",
                    help="fetch exactly the supplied post without thread discovery")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    user_hint, target_id = parse_target(args.target)
    user_hint = user_hint or args.user
    try:
        result = reconstruct(
            target_id,
            user_hint=user_hint,
            hops=args.hops,
            window=args.window,
            enrich=args.enrich,
            use_threadreader=not args.no_threadreader,
            single=args.single,
        )
    except RuntimeError as exc:
        sys.exit(str(exc))

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    out = result["tweets"]
    expected = result["expected_parts"]
    hdr = f"@{result['user']} " + ("thread" if result["thread_detected"] else "post")
    if expected:
        hdr += f" - {len(out)}/{expected} parts recovered"
    elif result["thread_detected"]:
        hdr += f" - {len(out)} parts recovered (completion unknown)"
    if result["input_id"] != result["root_id"]:
        hdr += f"; root {result['root_id']}"
    print(hdr)
    print("=" * 60)
    for warning in result["warnings"]:
        print(f"warning: {warning}")
    for t in out:
        src = f" [{t.get('source','')}]" if t.get("source") else ""
        print(f"\n--- {t['part']}. id={t.get('id') or '?'}{src} ---")
        print(t["text"])


if __name__ == "__main__":
    main()
