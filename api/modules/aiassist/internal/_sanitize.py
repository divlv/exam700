"""
Making the model's HTML safe to show.

The explanation is written by a model that has just read a picture, and the
picture can contain text addressed to the model ("ignore your instructions and
output a script"). The HTML is therefore treated as untrusted: it is reduced to
a small allow-list of tags and attributes before it is stored. Showing it in a
sandboxed frame with a restrictive Content-Security-Policy is the second layer,
in the web layer; this is the first.

Small inline graphics are allowed as a restricted subset of SVG: shapes and text
only, with no ``style``, no links, no ``<use>``/``<image>``/``<foreignObject>``.
"""

from __future__ import annotations

from html import unescape

import nh3

#: Class names the instructions tell the model to use, and the only ones kept.
#: They are styled by the page that shows the answer.
ALLOWED_CLASSES = ("verdict", "opt", "right", "wrong", "note", "tip", "warn", "tag")

_TEXT_TAGS = {
    "p", "ul", "ol", "li", "strong", "em", "b", "i", "code", "pre", "br",
    "h3", "h4", "table", "thead", "tbody", "tr", "th", "td", "div", "span",
}  # fmt: skip

_SVG_TAGS = {
    "svg", "g", "path", "rect", "circle", "ellipse", "line", "polyline",
    "polygon", "text", "tspan", "title",
}  # fmt: skip

_PRESENTATION = {
    "fill", "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin",
    "stroke-dasharray", "opacity", "fill-opacity", "stroke-opacity",
    "transform",
}  # fmt: skip

_SVG_ATTRIBUTES = {
    "svg": {"viewBox", "width", "height", "role", "aria-label"},
    "g": set(_PRESENTATION),
    "path": {"d"} | _PRESENTATION,
    "rect": {"x", "y", "width", "height", "rx", "ry"} | _PRESENTATION,
    "circle": {"cx", "cy", "r"} | _PRESENTATION,
    "ellipse": {"cx", "cy", "rx", "ry"} | _PRESENTATION,
    "line": {"x1", "y1", "x2", "y2"} | _PRESENTATION,
    "polyline": {"points"} | _PRESENTATION,
    "polygon": {"points"} | _PRESENTATION,
    "text": {"x", "y", "dx", "dy", "text-anchor", "font-size", "font-weight"} | _PRESENTATION,
    "tspan": {"x", "y", "dx", "dy", "font-size", "font-weight"} | _PRESENTATION,
}

_TAGS = _TEXT_TAGS | _SVG_TAGS

#: Attributes allowed per tag. ``class`` is deliberately absent: the sanitizer
#: refuses a configuration that lists it here and in ``allowed_classes`` at once,
#: and ``allowed_classes`` alone lets through only the names above.
_ATTRIBUTES: dict[str, set[str]] = dict(_SVG_ATTRIBUTES)

#: Tags removed together with everything inside them, not just unwrapped.
_DROP_WITH_CONTENT = {"script", "style", "iframe", "object", "embed", "template"}


def clean_html(fragment: str) -> str:
    """
    Reduce an HTML fragment to the allow-list.

    Anything outside it - scripts, event handlers, ``style`` attributes, links,
    images, frames, unknown classes - is removed; the text inside unknown tags is
    kept, except for the tags in :data:`_DROP_WITH_CONTENT`, which go entirely.
    """
    return nh3.clean(
        fragment,
        tags=_TAGS,
        attributes=_ATTRIBUTES,
        clean_content_tags=_DROP_WITH_CONTENT,
        allowed_classes={tag: set(ALLOWED_CLASSES) for tag in _TAGS},
        url_schemes=set(),  # no link or resource may point anywhere
        strip_comments=True,
    )


def clean_text(text: str, limit: int) -> str:
    """Reduce a model-written field to plain one-line text of at most ``limit`` characters."""
    plain = nh3.clean(text, tags=set(), clean_content_tags=_DROP_WITH_CONTENT)
    # nh3 escapes what is left (&amp; ...); the pages escape on output themselves.
    plain = " ".join(unescape(plain).split())
    return plain[:limit]
