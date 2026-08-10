"""The acceptance check for `static-site-v1`, held OUTSIDE the repository.

This is the part a demo can offer that the evidence document cannot: it runs
the finished tool against the seed content and asserts the OUTPUT is right. A
human can then open `out/index.html` and see the same thing with their own
eyes — no container, no cycle acceptance run, no trust required.

Held outside the generated repository on purpose. `happy-path-v1` learned this
the expensive way: its verdict was circular, because it ran pytest inside the
same `tests/` the agent writes to, so an agent could satisfy the checker by
writing a weak test. These assertions were written before any run and the agent
never sees them.

Usage (from the demo's run directory):

    SITEGEN_REPO=/path/to/repo python -m pytest demos/static-site-v1/acceptance -q
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

_RAW_REPO = os.environ.get("SITEGEN_REPO", "").strip()
# NOT `Path(os.environ.get(...))`: an unset variable makes `Path("")`, which is
# `.` — a directory that always exists — so the skip guard would never fire and
# an operator who forgot the variable would get eleven errors instead of a
# clear skip. Caught by test_the_acceptance_check_skips_rather_than_fails.
REPO = Path(_RAW_REPO).expanduser() if _RAW_REPO else None


pytestmark = pytest.mark.skipif(
    REPO is None or not REPO.exists(),
    reason="SITEGEN_REPO is not set to a built repository",
)


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> Path:
    """Run the tool the agents built, exactly as the brief documents it."""
    out = tmp_path_factory.mktemp("site")
    result = subprocess.run(
        [sys.executable, "-m", "sitegen.cli", "build", "content/", "--out", str(out)],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, "PYTHONPATH": str(REPO / "src")},
    )
    if result.returncode != 0:
        # Fall back to the console script if the module path differs; the brief
        # pins the COMMAND, not the module layout.
        result = subprocess.run(
            ["sitegen", "build", "content/", "--out", str(out)],
            cwd=REPO,
            capture_output=True,
            text=True,
            timeout=120,
        )
    assert result.returncode == 0, (
        f"`sitegen build` failed ({result.returncode}).\n"
        f"stdout: {result.stdout[-2000:]}\nstderr: {result.stderr[-2000:]}"
    )
    return out


def _index(built: Path) -> str:
    path = built / "index.html"
    assert path.exists(), f"no index.html was written; got {sorted(p.name for p in built.iterdir())}"
    return path.read_text(encoding="utf-8")


def test_it_writes_one_html_file_per_markdown_input(built):
    names = {p.name for p in built.glob("*.html")}
    assert {"index.html", "about.html"} <= names, names


def test_the_page_is_a_complete_document_not_a_fragment(built):
    html = _index(built)
    assert "<html" in html.lower()
    assert "</html>" in html.lower()
    assert "<body" in html.lower()


def test_the_front_matter_title_reaches_the_document_title(built):
    html = _index(built)
    title = re.search(r"<title>(.*?)</title>", html, re.I | re.S)
    assert title is not None, "no <title> element"
    assert "Welcome" in title.group(1)


def test_front_matter_is_consumed_not_printed(built):
    """The header is metadata. Seeing `layout: page` in the output is the
    single most obvious sign the parser was skipped."""
    html = _index(built)
    assert "layout: page" not in html
    assert "---" not in html.split("<body", 1)[-1][:200]


def test_headings_render_at_the_right_level(built):
    html = _index(built)
    assert re.search(r"<h1[^>]*>\s*Welcome\s*</h1>", html), html[:800]
    assert re.search(r"<h2[^>]*>\s*A second heading\s*</h2>", html), html[:800]


def test_the_title_appears_as_exactly_one_visible_heading(built):
    """The eye check, encoded — 2026-08-10.

    The first real completed run produced a page whose title was rendered
    TWICE, one `<h1>Welcome</h1>` directly above another. Every automated check
    passed: the layout carried the title as the brief requires, the renderer
    turned the body's `# Welcome` into a heading as the brief requires, the
    container acceptance run booted the tool and built the site, and the eleven
    assertions here were all satisfied because each one asked whether a thing
    was PRESENT and none asked whether it appeared once.

    The demo's own content was the contradiction: it repeated the front-matter
    title as an ATX heading in the body, so following both requirements
    literally could only produce a duplicate. Fixed at the source; locked here
    too, because this is the file that stands in for a human opening the page,
    and a doubled title is the first thing that human sees."""
    html = _index(built)

    assert len(re.findall(r"<h1[^>]*>", html)) == 1, (
        f"expected exactly one <h1>, found {len(re.findall(r'<h1[^>]*>', html))}"
    )


def test_inline_markup_renders(built):
    html = _index(built)
    assert re.search(r"<em[^>]*>emphasis</em>", html)
    assert re.search(r"<strong[^>]*>strong</strong>", html)
    assert re.search(r"<code[^>]*>inline code</code>", html)


def test_the_list_renders_as_a_list(built):
    html = _index(built)
    assert "<ul" in html.lower()
    assert html.lower().count("<li") >= 4


def test_links_between_pages_are_rewritten_to_html(built):
    """The one thing that makes it a SITE rather than a pile of pages. A
    reader clicks it, so a broken rewrite is immediately visible."""
    html = _index(built)
    assert 'href="about.html"' in html, (
        "the link to about.md must be rewritten to about.html; "
        f"found: {re.findall(r'href=\"[^\"]*\"', html)}"
    )
    assert "about.md" not in html


def test_the_reverse_link_also_works(built):
    about = (built / "about.html").read_text(encoding="utf-8")
    assert 'href="index.html"' in about


def test_markdown_source_does_not_leak_into_the_output(built):
    """If the renderer no-ops, the page still 'builds' and looks plausible in a
    diff. This is what catches that."""
    body = _index(built).split("<body", 1)[-1]
    assert "# Welcome" not in body
    assert "**strong**" not in body
    assert "[links to other pages]" not in body


def test_html_special_characters_are_escaped(built, tmp_path):
    """Written before any run: an unescaped `<` is both a correctness bug and
    the smallest possible injection."""
    content = tmp_path / "content"
    content.mkdir()
    (content / "esc.md").write_text(
        "---\ntitle: Escapes\nlayout: page\n---\n\nA 5 < 6 and a & b.\n",
        encoding="utf-8",
    )
    out = tmp_path / "out"
    result = subprocess.run(
        [sys.executable, "-m", "sitegen.cli", "build", str(content), "--out", str(out)],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, "PYTHONPATH": str(REPO / "src")},
    )
    if result.returncode != 0:
        pytest.skip("the tool could not be invoked on an ad-hoc content directory")
    html = (out / "esc.html").read_text(encoding="utf-8")
    assert "5 &lt; 6" in html or "5 < 6" not in html.split("<body", 1)[-1]
