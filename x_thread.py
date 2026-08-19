#!/usr/bin/env python3
"""Reconstruct an X (Twitter) self-reply thread without auth.

fxtwitter does NOT follow self-replies (its `replying_to` is null on the root
and it never returns subsequent tweets), and X syndication `conversation_count`
is unreliable (often reports 1-2 for a 7-part thread).

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
    fetch until the body is non-trivial (>50KB) — a good render is ~200-500KB.
  - Detect "is this a thread" from the root text markers (1/7, 🧵, "a thread"),
    NOT conversation_count.

Usage:
  x_thread.py https://x.com/user/status/123        # full URL
  x_thread.py 123 --user screenname                # bare id + --user
  x_thread.py https://x.com/i/status/123           # /i/ URL (user auto-detected)
  x_thread.py <url> --json                         # JSON output
  x_thread.py <url> --hops 10                       # max walk hops (default 9)
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


def curl(url, retries=10, min_bytes=0):
    """Fetch via curl (twstalker 403s Python TLS; ~40% of fetches return an empty
    Cloudflare stub at random, so retry hard until non-stub when min_bytes set)."""
    for _ in range(retries):
        try:
            out = subprocess.run(
                ["curl", "-sL", "-A", UA, "--max-time", "20", url],
                capture_output=True, timeout=25,
            ).stdout.decode("utf-8", "replace")
        except Exception:
            out = ""
        if len(out) >= min_bytes:
            return out
        time.sleep(0.8)
    return out


def fx_tweet(user, tid):
    raw = curl(f"{FX}/{user}/status/{tid}", retries=2)
    try:
        d = json.loads(raw)
    except Exception:
        return None
    if d.get("code") != 200:
        return None
    t = d.get("tweet", {})
    return {
        "id": t.get("id"),
        "text": (t.get("text") or "").strip(),
        "ts": t.get("created_timestamp"),
        "author": (t.get("author") or {}).get("screen_name"),
    }


def threadreader(root_id):
    """Primary forward source: threadreaderapp's unroll. Returns [{id, text}, ...] in
    order, or [] if the thread is not unrolled there. Clean full text with real ids,
    one fast request, no Cloudflare wall — but only ~half of threads are unrolled, so
    the twstalker walk remains the fallback. Auto-unroll seems to fire for popular
    threads on GET; niche/fresh ones may return nothing."""
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


def thread_total(text):
    """Expected thread length N from root markers; -1 if thread of unknown len; 0 if not a thread."""
    m = re.search(r"\b(\d+)\s*/\s*(\d+)\b", text)
    if m and m.group(1) == "1" and 2 <= int(m.group(2)) <= 50:
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
        if len(txt) > 8 and (k not in out or len(txt) > len(out[k])):
            out[k] = txt
    return out


SNOWFLAKE_PER_SEC = 4_194_304_000  # twitter snowflake ids advance ~2^22 ms per second


def walk(user, root_id, n, hops, window):
    """Forward status-walk: accumulate part-bodies and ids until all N parts seen.

    Thread parts are consecutive snowflakes within seconds of the root, so the
    frontier only advances to ids within `window` seconds of the root id —
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


def main():
    ap = argparse.ArgumentParser(description="Reconstruct an X self-reply thread (no auth).")
    ap.add_argument("target", help="tweet URL or bare id")
    ap.add_argument("--user", help="screen name (needed for bare id or /i/ url)")
    ap.add_argument("--window", type=int, default=900,
                    help="seconds around root ts to keep as same thread (default 900)")
    ap.add_argument("--hops", type=int, default=9, help="max walk hops (default 9)")
    ap.add_argument("--enrich", action="store_true",
                    help="enrich verbatim text via fxtwitter per-id (slower; twstalker bodies are primary)")
    ap.add_argument("--no-threadreader", action="store_true",
                    help="skip the threadreaderapp primary source; go straight to the twstalker walk")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    user, root_id = parse_target(args.target)
    user = user or args.user
    if not user:
        r = fx_tweet("i", root_id)
        if r and r.get("author"):
            user = r["author"]
        else:
            sys.exit("could not resolve username; pass --user")

    root = fx_tweet(user, root_id)
    if not root:
        sys.exit(f"could not fetch root tweet {root_id} for @{user}")

    n = thread_total(root["text"])
    # Primary: threadreaderapp unroll (clean, full, real ids, one request). Falls
    # through to the twstalker walk when the thread is not unrolled there.
    tr = [] if args.no_threadreader else threadreader(root_id)
    if tr and (n <= 0 or len(tr) >= n):
        out = [{"part": i + 1, "id": t["id"], "text": t["text"], "source": "threadreader"}
               for i, t in enumerate(tr)]
        n = len(tr) if n <= 0 else n
    elif n == 0:
        out = [{"part": 1, **root}]
    else:
        seen_ids, parts = walk(user, root_id, n, args.hops, args.window)
        # part 1 verbatim text always comes clean from the root fetch
        parts.setdefault(1, root["text"])
        # optionally enrich verbatim text via fxtwitter for ids in the root's time window
        fx_by_part = {}
        if args.enrich:
            root_ts = root["ts"] or 0
            span = args.window * SNOWFLAKE_PER_SEC
            for tid in sorted(seen_ids, key=int):
                if not (0 <= int(tid) - int(root_id) <= span):
                    continue
                t = fx_tweet(user, tid)
                if not t or t.get("author") != root["author"]:
                    continue
                mk = re.match(r"~?(\d+)\s*/\s*\d+", t["text"])
                if mk:
                    fx_by_part[int(mk.group(1))] = t
                time.sleep(0.12)
        # merge: prefer full fxtwitter text, fall back to twstalker body
        total = n if n > 0 else max([*parts, *fx_by_part, 1])
        out = []
        for k in range(1, total + 1):
            if k in fx_by_part:
                t = fx_by_part[k]
                out.append({"part": k, "id": t["id"], "text": t["text"], "source": "fxtwitter"})
            elif k in parts:
                out.append({"part": k, "id": None, "text": parts[k], "source": "twstalker"})

    if args.json:
        print(json.dumps({"user": user, "root_id": root_id,
                          "expected_parts": n, "recovered": len(out),
                          "tweets": out}, indent=2, ensure_ascii=False))
        return

    hdr = f"@{user} thread"
    if n > 0:
        hdr += f" — {len(out)}/{n} parts recovered"
    elif n == -1:
        hdr += f" — {len(out)} parts recovered (unknown length)"
    print(hdr)
    print("=" * 60)
    for t in out:
        src = f" [{t.get('source','')}]" if t.get("source") else ""
        print(f"\n--- {t['part']}. id={t.get('id') or '?'}{src} ---")
        print(t["text"])


if __name__ == "__main__":
    main()
