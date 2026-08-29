#!/usr/bin/env python3
"""Convert a Claude Design canvas artboard (.dc.html) into a standalone web page.

The canvas format wraps everything in <x-dc>, keeps head content in a <helmet>
block, and loads the editor runtime from ./support.js. None of that is needed
once the page is served on its own, and the body content is already plain HTML.
"""
import re, sys, pathlib

# The canvas was drawn as a single site with /projects/* paths, but each page
# is served on its own host. Absolute URLs are the only form that works from
# every one of them. Longest prefix first so /projects/blog wins over /projects.
LINKS = [
    ("/projects/blog",     "https://blog.genuinebasil.dev/"),
    ("/projects/marginal", "https://marginal.genuinebasil.dev/"),
    ("/projects/cairn",    "https://cairn.genuinebasil.dev/"),
    # No /projects index exists; the apex lists the systems inline.
    ("/projects",          "https://genuinebasil.dev/#systems"),
    ("/",                  "https://genuinebasil.dev/"),
]


def retarget(html: str) -> str:
    """Point site-root hrefs at the host that actually serves them."""
    for path, url in LINKS:
        html = html.replace(f'href="{path}"', f'href="{url}"')
    return html


def convert(src: pathlib.Path, title: str, desc: str) -> str:
    raw = src.read_text(encoding="utf-8")

    m = re.search(r"<helmet>(.*?)</helmet>", raw, re.S)
    if not m:
        sys.exit(f"{src}: no <helmet> block")
    head = m.group(1).strip()

    after = raw[m.end():]
    end = after.rfind("</x-dc>")
    if end == -1:
        sys.exit(f"{src}: no closing </x-dc>")
    body = after[:end].strip()

    if "<x-dc" in body or "support.js" in body:
        sys.exit(f"{src}: unexpected canvas markup left in body")

    body = retarget(body)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<meta name="description" content="{desc}">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{desc}">
<meta property="og:type" content="website">
<link rel="icon" href="data:,">
{head}
</head>
<body>
{body}
</body>
</html>
"""

PAGES = [
    ("Main",     "landing",  "genuinebasil.dev",     "Systems engineering — blog, Marginal, and Cairn."),
    ("Marginal", "marginal", "Marginal",             "A collaborative document engine built on Go microservices."),
    ("Cairn",    "cairn",    "Cairn",                "Cairn — in development."),
    ("Blog",     "_blog",    "Blog — genuinebasil.dev", "Writing on systems, databases, and Rust."),
]

srcdir = pathlib.Path(sys.argv[1])
outdir = pathlib.Path(sys.argv[2])

for stem, slug, title, desc in PAGES:
    out = outdir / slug
    out.mkdir(parents=True, exist_ok=True)
    html = convert(srcdir / f"{stem}.dc.html", title, desc)
    (out / "index.html").write_text(html, encoding="utf-8")
    print(f"{stem}.dc.html -> {slug}/index.html  ({len(html):,} bytes)")
