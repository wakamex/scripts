from __future__ import annotations

import pytest

import extract_web as web


def page(html: str):
    return web.FetchedPage(html, "https://example.test/", "text/html")


def test_explicit_content_preserves_biography_header_sidebar_and_table():
    result = web.render_markdown(page("""<html><head><title>Profile</title></head><body>
    <header><h1>Example Person</h1></header><table><tr><td>Engineer at Example Lab</td></tr></table>
    <aside>Previously Professor at Example University</aside>
    <nav>Navigation noise</nav><script>script noise</script>
    </body></html>"""), min_chars=20, content_xpath="//body")
    assert "Example Person" in result
    assert "Engineer at Example Lab" in result
    assert "Previously Professor" in result
    assert "Navigation noise" not in result
    assert "script noise" not in result


@pytest.mark.parametrize("selector", ["//missing", "//p", "//p/text()"])
def test_explicit_content_rejects_missing_ambiguous_or_non_element(selector):
    with pytest.raises(ValueError, match="exactly one HTML element"):
        web.render_markdown(page("<html><body><p>First</p><p>Second</p></body></html>"), content_xpath=selector)


def test_default_stays_automatic_and_explicit_selection_is_opt_in():
    assert web.parse_args(["https://example.test/"]).content_xpath is None
    assert web.parse_args(["https://example.test/", "--content-xpath", "//main"]).content_xpath == "//main"


def test_invalid_xpath_has_a_clear_error():
    with pytest.raises(ValueError, match="invalid content XPath"):
        web.render_markdown(page("<html><body><p>Text</p></body></html>"), content_xpath="//[")


def test_explicit_content_does_not_disable_challenge_validation():
    with pytest.raises(RuntimeError, match="challenge"):
        web.validate_page(page("<html><title>Just a moment</title><body>Blocked</body></html>"))
