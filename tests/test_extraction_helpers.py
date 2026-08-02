from __future__ import annotations

import json
from email.message import EmailMessage
from pathlib import Path
from types import SimpleNamespace

import pytest

import download_reddit_video
import extract_eml
import extract_reddit
import extract_web

ASK_HISTORIANS_SHARE = "https://www.reddit.com/r/AskHistorians/s/2FOuLaPS78"
ASK_HISTORIANS_COMMENT = "https://www.reddit.com/r/AskHistorians/comments/1vczain/comment/p16iq6q"


def test_extract_eml_selects_complete_html_and_decodes_attachment(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    markdown_dir = tmp_path / "md"
    raw_dir.mkdir()
    source = raw_dir / "message.eml"

    message = EmailMessage()
    message["Subject"] = "Test message"
    message["From"] = "sender@example.com"
    message["To"] = "reader@example.com"
    message["Date"] = "Sat, 2 Aug 2026 12:00:00 -0400"
    message["Message-ID"] = "<test@example.com>"
    message.set_content("Open ${link} now.")
    message.add_alternative(
        '<html><body><p>Open <a href="https://example.com/action">the action</a> now.</p></body></html>',
        subtype="html",
    )
    message.add_attachment(
        b"attachment bytes",
        maintype="application",
        subtype="pdf",
        filename="../agreement.pdf",
    )
    source.write_bytes(message.as_bytes())

    output = markdown_dir / "message.md"
    markdown, count = extract_eml.extract_message(source, output)

    assert count == 1
    assert "`${link}`" not in markdown
    assert "[the action](https://example.com/action)" in markdown
    assert "Selected body: `text/html`" in markdown
    attachments = list((raw_dir / "message.attachments").iterdir())
    assert len(attachments) == 1
    assert attachments[0].name.endswith("agreement.pdf")
    assert attachments[0].read_bytes() == b"attachment bytes"
    assert attachments[0].name in output.read_text()


def test_render_web_markdown_preserves_metadata_and_main_content() -> None:
    body = " ".join(["Substantive article paragraph."] * 20)
    page = extract_web.FetchedPage(
        html=f"""
            <html><head><title>Article title</title>
            <link rel="canonical" href="https://example.com/canonical">
            <meta name="author" content="Example Author">
            <meta property="article:published_time" content="2026-08-02">
            </head><body><nav>Navigation</nav><article><p>{body}</p>
            <a href="/more">Read more</a></article></body></html>
        """,
        final_url="https://example.com/redirected",
        content_type="text/html; charset=utf-8",
    )

    markdown = extract_web.render_markdown(page)

    assert markdown.startswith("# Article title\n")
    assert "https://example.com/canonical" in markdown
    assert "Example Author" in markdown
    assert body in markdown
    assert "[Read more](https://example.com/more)" in markdown
    assert "Navigation" not in markdown


def test_web_rejects_challenge_page() -> None:
    page = extract_web.FetchedPage(
        "<html><title>Reddit - Please wait for verification</title></html>",
        "https://www.reddit.com/",
        "text/html",
    )
    with pytest.raises(RuntimeError, match="challenge"):
        extract_web.validate_page(page)


def reddit_payload() -> list[dict[str, object]]:
    return [
        {
            "data": {
                "children": [
                    {
                        "kind": "t3",
                        "data": {
                            "title": "Historical question",
                            "subreddit": "AskHistorians",
                            "author": "questioner",
                            "score": 42,
                            "created_utc": 1_754_150_400,
                            "selftext": "What happened?",
                        },
                    }
                ]
            }
        },
        {
            "data": {
                "children": [
                    {
                        "kind": "t1",
                        "data": {
                            "author": "historian",
                            "score": 99,
                            "created_utc": 1_754_154_000,
                            "body": "A sourced answer.",
                            "permalink": "/r/AskHistorians/comments/post/comment/answer/",
                            "replies": "",
                        },
                    }
                ]
            }
        },
    ]


def test_render_reddit_json_uses_post_and_comment_content() -> None:
    canonical = "https://www.reddit.com/r/AskHistorians/comments/post/comment/answer"
    markdown = extract_reddit.render_reddit_json(reddit_payload(), canonical)
    assert markdown.startswith("# Historical question\n")
    assert "What happened?" in markdown
    assert "### Comment by u/historian" in markdown
    assert "A sourced answer." in markdown


def test_render_redlib_html_preserves_nested_comment_order() -> None:
    html = """
      <p class="post_header"><a class="post_subreddit">r/AskHistorians</a>
        <a class="post_author">u/questioner</a><span class="created" title="date">now</span></p>
      <h1 class="post_title">Historical question</h1>
      <div class="post_body"><p>What happened?</p></div><div class="post_score" title="42">42</div>
      <div id="answer" class="comment"><div class="comment_left"><p class="comment_score" title="99">99</p></div>
        <details class="comment_right"><summary class="comment_data"><a class="comment_author">u/historian</a></summary>
          <div class="comment_body"><p>A sourced answer.</p></div>
          <blockquote class="replies"><div id="reply" class="comment"><div class="comment_left"><p class="comment_score">3</p></div>
            <details class="comment_right"><summary class="comment_data"><a class="comment_author">u/reader</a></summary>
              <div class="comment_body"><p>Thank you.</p></div></details></div></blockquote>
        </details></div>
    """
    markdown = extract_reddit.render_redlib_html(
        html,
        "https://www.reddit.com/r/AskHistorians/comments/post/title",
        "https://redlib.example",
    )
    assert "### Comment by u/historian" in markdown
    assert "#### Comment by u/reader" in markdown
    assert markdown.index("A sourced answer.") < markdown.index("Thank you.")


def test_render_official_reddit_embed_labels_comment_only_scope() -> None:
    metadata = {
        "comment": {
            "id": "t1_p16iq6q",
            "score": 104,
            "created_timestamp": 1_785_636_970_594,
        },
        "subreddit": {"name": "askhistorians"},
    }
    html = f"""
      <shreddit-screenview-data data='{json.dumps(metadata)}'></shreddit-screenview-data>
      <div id="t1_p16iq6q-embed-wrapper">
        <a data-testid="user-name">Fluxxed0</a>
        <a data-testid="post-link" href="https://www.reddit.com/r/AskHistorians/comments/1vczain/title/">post</a>
        <div id="t1_p16iq6q-post-rtjson-content"><p>The requested answer.</p></div>
      </div>
    """
    markdown = extract_reddit.render_reddit_embed(
        html,
        "https://www.reddit.com/r/AskHistorians/comments/1vczain/comment/p16iq6q",
    )
    assert markdown.startswith("# Reddit comment by u/Fluxxed0\n")
    assert "requested embedded comment only; replies are not included" in markdown
    assert "The requested answer." in markdown


def test_share_url_canonicalization_removes_tracking_query() -> None:
    result = extract_reddit.canonical_reddit_url(
        "https://www.reddit.com/r/AskHistorians/comments/1vczain/comment/p16iq6q/?share_id=secret&utm_source=share"
    )
    assert result == ("https://www.reddit.com/r/AskHistorians/comments/1vczain/comment/p16iq6q")


def test_askhistorians_share_example_resolves_to_comment() -> None:
    class FakeSession:
        @staticmethod
        def get(url: str, **_: object) -> SimpleNamespace:
            assert url == ASK_HISTORIANS_SHARE
            return SimpleNamespace(url=f"{ASK_HISTORIANS_COMMENT}/?utm_source=share")

    assert extract_reddit.resolve_reddit_url(FakeSession(), ASK_HISTORIANS_SHARE, 8) == ASK_HISTORIANS_COMMENT


def test_parse_reddit_dash_manifest() -> None:
    expected_duration = 33.5
    manifest = b"""<?xml version="1.0"?>
      <MPD xmlns="urn:mpeg:dash:schema:mpd:2011" mediaPresentationDuration="PT33.5S">
        <Period><AdaptationSet contentType="video"><Representation><BaseURL>CMAF_720.mp4</BaseURL></Representation></AdaptationSet>
        <AdaptationSet contentType="audio"><Representation><BaseURL>CMAF_AUDIO_128.mp4</BaseURL></Representation></AdaptationSet></Period>
      </MPD>"""
    info = download_reddit_video.parse_manifest(manifest)
    assert info.duration_seconds == expected_duration
    assert info.has_video is True
    assert info.has_audio is True
    assert info.stream_names == ("CMAF_720.mp4", "CMAF_AUDIO_128.mp4")


def test_manifest_without_video_is_rejected() -> None:
    with pytest.raises(extract_reddit.ExtractionError, match="no video"):
        download_reddit_video.parse_manifest(b'<MPD><Period><AdaptationSet contentType="audio"/></Period></MPD>')
