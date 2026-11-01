"""
The sanitizer for the model's HTML.

The model has just read a picture, and a picture can say "ignore your
instructions". Whatever it writes back is treated as hostile.
"""

from __future__ import annotations

import pytest

from api.modules.aiassist.internal._sanitize import clean_html, clean_text


def test_the_formatting_the_instructions_ask_for_survives():
    html = (
        '<div class="verdict">✅ Ответ: <strong>B</strong></div>'
        '<div class="opt right">✅ <b>B</b> — потому что <code>x</code></div>'
        '<div class="opt wrong">❌ A</div>'
        '<div class="tip">💡 <em>идея</em></div><div class="note">📘 раздел</div>'
        '<div class="warn">⚠️ нечитаемо</div>'
        "<table><thead><tr><th>Пункт</th></tr></thead><tbody><tr><td>x</td></tr></tbody></table>"
        "<ul><li>раз</li></ul><ol><li>два</li></ol><h3>t</h3><pre>a\nb</pre>"
    )

    assert clean_html(html) == html


def test_a_small_diagram_in_svg_survives():
    svg = (
        '<svg viewBox="0 0 120 60" width="240" role="img" aria-label="VNet">'
        '<g stroke="#333" fill="none"><rect x="2" y="2" width="50" height="20" rx="4"></rect>'
        '<circle cx="80" cy="12" r="8"></circle><line x1="52" y1="12" x2="72" y2="12"></line>'
        '<polyline points="0,0 10,10"></polyline><polygon points="0,0 5,5 0,5"></polygon>'
        '<ellipse cx="5" cy="5" rx="2" ry="3"></ellipse><path d="M0 0 L10 10"></path></g>'
        '<text x="5" y="40" font-size="10">VNet</text><title>diagram</title></svg>'
    )

    cleaned = clean_html(svg)

    assert 'viewBox="0 0 120 60"' in cleaned
    for shape in ("<rect", "<circle", "<line", "<polyline", "<polygon", "<ellipse", "<path", "<text"):
        assert shape in cleaned


@pytest.mark.parametrize(
    ("hostile", "must_be_gone"),
    [
        ("<p>a</p><script>alert(1)</script>", ["<script", "alert(1)"]),
        ('<p onclick="x()">a</p>', ["onclick"]),
        ('<img src="http://evil/x.png" onerror="x()">', ["<img", "evil", "onerror"]),
        ('<a href="javascript:alert(1)">l</a>', ["javascript:", "href", "<a"]),
        ('<a href="https://evil.example/">l</a>', ["evil.example", "href"]),
        ('<iframe src="//evil.example">t</iframe>', ["<iframe", "evil.example"]),
        ('<div style="position:fixed;top:0">x</div>', ["style", "position"]),
        ("<style>body{display:none}</style><p>a</p>", ["<style", "display"]),
        ('<object data="x"></object><embed src="x">', ["<object", "<embed"]),
        ('<form action="//evil"><input name="x"></form>', ["<form", "<input", "evil"]),
        ("<!-- <script>x</script> --><p>a</p>", ["<!--", "<script"]),
        ('<svg onload="x()" viewBox="0 0 1 1"><script>1</script></svg>', ["onload", "<script"]),
        ('<svg><use href="#a"></use><image href="http://evil/x"></image></svg>', ["<use", "<image", "evil"]),
        ("<svg><foreignObject><p>f</p></foreignObject></svg>", ["foreignObject"]),
        ('<svg><circle cx="1" cy="1" r="1" style="fill:red" onclick="x()"></circle></svg>', ["style", "onclick"]),
        ('<svg><a href="javascript:alert(1)"><text>t</text></a></svg>', ["javascript:", "href"]),
    ],
)
def test_anything_active_or_external_is_removed(hostile, must_be_gone):
    cleaned = clean_html(hostile)

    for fragment in must_be_gone:
        assert fragment not in cleaned, cleaned


def test_unknown_classes_are_dropped_but_known_ones_stay():
    cleaned = clean_html('<div class="opt right evil-class position-fixed">x</div>')

    assert cleaned == '<div class="opt right">x</div>'


def test_text_inside_an_unknown_tag_is_kept():
    assert clean_html("<section>kept</section>") == "kept"


def test_the_short_fields_become_one_line_of_plain_text():
    assert clean_text("<b>Hello</b> &amp; <script>x</script>\n  world", 140) == "Hello & world"
    assert clean_text("a" * 300, 140) == "a" * 140
    assert clean_text("&amp;lt;", 140) == "&lt;"  # unescaped once, not twice
