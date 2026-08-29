#!/usr/bin/env python3
"""Convert Claude Design canvas artboards (.dc.html) into standalone pages.

The canvas format wraps everything in <x-dc>, keeps head content in a <helmet>
block, and loads the editor runtime from ./support.js. None of that is needed
once the page is served on its own; the body content is already plain HTML.

Output layout mirrors the URL layout, so Caddy needs no path rewriting beyond
appending index.html:

    sites/index.html                  genuinebasil.dev/
    sites/projects/blog/index.html    genuinebasil.dev/projects/blog
    sites/projects/marginal/...       genuinebasil.dev/projects/marginal
    sites/projects/cairn/...          genuinebasil.dev/projects/cairn

The artboards' own /projects/* links are therefore correct as drawn and are
left alone. The live apps live on subdomains (blog./marginal./cairn.) and the
artboards already link to those absolutely.
"""
import re
import sys
import pathlib

PAGES = [
    # artboard      output dir           <title>                    description
    ("Main",     ".",                 "genuinebasil.dev",
     "Systems engineering — a blog, Marginal, and Cairn."),
    ("Blog",     "projects/blog",     "Blog — genuinebasil.dev",
     "Writing on systems, databases, and Rust."),
    ("Marginal", "projects/marginal", "Marginal — genuinebasil.dev",
     "A collaborative document engine built on Go microservices."),
    ("Cairn",    "projects/cairn",    "Cairn — genuinebasil.dev",
     "Cairn — in development."),
]


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
        sys.exit(f"{src}: canvas markup left in body")

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


def main() -> None:
    srcdir = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    outdir = pathlib.Path(sys.argv[2] if len(sys.argv) > 2 else "sites")

    for stem, slug, title, desc in PAGES:
        dest = outdir / slug
        dest.mkdir(parents=True, exist_ok=True)
        html = convert(srcdir / f"{stem}.dc.html", title, desc)
        (dest / "index.html").write_text(html, encoding="utf-8")
        print(f"{stem}.dc.html -> {(dest / 'index.html')}  ({len(html):,} bytes)")


if __name__ == "__main__":
    main()
