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
import hashlib
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
    ("Cv",       "cv",                "CV — Genuine Basil NT",
     "Backend engineer — Rust, real-time collaboration, event-driven services."),
    ("Notfound", "404",               "404 — genuinebasil.dev",
     "Nothing is routed here."),
]


# Injected into every page's <head>, after the artboard's own <helmet> styles so
# it wins on order as well as specificity. The canvas composes at desktop width
# and emits inline style="" attributes, so each override needs !important to
# beat them. Kept here rather than in the artboards because a re-export from the
# canvas would drop anything hand-added there.
# The site's own motif as a favicon: three systems around a hub, which is what
# the landing page's topology diagram draws. A data URI so there is no second
# request and nothing to 404. '#' must be percent-encoded inside one.
FAVICON = (
    "data:image/svg+xml,"
    "%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E"
    "%3Crect width='32' height='32' rx='7' fill='%230A0C0F'/%3E"
    "%3Ccircle cx='16' cy='16' r='3.2' fill='%23E7EAEC'/%3E"
    "%3Ccircle cx='7' cy='9' r='2.6' fill='%239C87ED'/%3E"
    "%3Ccircle cx='25' cy='9' r='2.6' fill='%23E0985A'/%3E"
    "%3Ccircle cx='16' cy='26' r='2.6' fill='%233FC79A'/%3E"
    "%3C/svg%3E"
)

# Each page already carries an ambient blob in its own accent — the wash sitting
# behind panels like "ONE QUERY, TRACED". This adds one that follows the pointer
# and, unlike the artboard's, sits *under* the grid, so the grid lines read as
# etched glass with the light moving behind them. Landing and CV cycle all three
# system accents, the same rekey their chrome does; a project page keeps its own.
GLOW_ACCENT = {
    ".":                 "rekey",
    "cv":                "rekey",
    "projects/blog":     "63,199,154",
    "projects/marginal": "156,135,237",
    "projects/cairn":    "224,152,90",
}

# Dye colours for the fluid. The landing and CV pages carry all three systems,
# so their fluid does too; a project page stays its own colour.
ALL_THREE = "224,152,90|156,135,237|63,199,154"

GLOW_CSS = """
<style>
@property --mglow-rgb { syntax: "<number>#"; inherits: true; initial-value: 224,152,90 }
.mfluid { position: absolute; top: 0; left: 0; pointer-events: none; }
/* The simulation renders at viewport size and is fixed, so the fluid stays with
   the reader rather than scrolling off the top of a long page. Behind the grid,
   like everything else in this layer. */
.mfluid-gl {
  position: fixed; top: 0; left: 0;
  width: 100%; height: 100%;
  pointer-events: none;
  /* It sits behind the text, so it has to lose the contrast fight. */
  opacity: .5;
}
  background: radial-gradient(circle, rgba(var(--mglow-rgb), .17), transparent 72%); }
@keyframes mglow-rekey {
  0%,26%   { --mglow-rgb: 224,152,90 }
  33%,59%  { --mglow-rgb: 156,135,237 }
  66%,92%  { --mglow-rgb: 63,199,154 }
  100%     { --mglow-rgb: 224,152,90 }
}
.mfluid-rekey { animation: mglow-rekey 15s ease-in-out infinite; }


/* Selecting text on a dark page otherwise gets the browser's default blue. */
::selection { background: rgba(var(--mglow-rgb), .3); color: #F2F5F7; }

/* Keyboard focus should be as considered as hover. */
:focus-visible {
  outline: 2px solid rgba(var(--mglow-rgb), .8);
  outline-offset: 3px;
  border-radius: 3px;
}

/* A card catches the light where the pointer is over it. --cx/--cy are set on
   the card itself, so only the hovered one ever repaints. */
.card { position: relative; }
.card::after {
  content: "";
  position: absolute; inset: 0;
  border-radius: inherit;
  pointer-events: none;
  opacity: 0;
  transition: opacity .28s ease;
  background: radial-gradient(260px circle at var(--cx, 50%) var(--cy, 50%),
                              rgba(var(--mglow-rgb), .09), transparent 70%);
}
.card:hover::after { opacity: 1; }
</style>
"""

GLOW_JS = """
<script>
(function () {
  // The card spotlight is independent of the fluid and stays whatever happens
  // to WebGL — one delegated listener, only the hovered card written to.
  addEventListener('pointermove', function (e) {
    var card = e.target.closest && e.target.closest('.card');
    if (!card) return;
    var r = card.getBoundingClientRect();
    card.style.setProperty('--cx', (e.clientX - r.left) + 'px');
    card.style.setProperty('--cy', (e.clientY - r.top) + 'px');
  }, { passive: true });

  // The simulation reads its dye colours off the page wrapper.
  var grid = document.querySelector('.gridpan');
  if (grid && grid.parentNode) grid.parentNode.setAttribute('data-fluid', '%(dye)s');
})();
</script>
<script src="%(fluid_src)s" defer></script>
"""

MOTION_CSS = """
<style>
/* Cross-document view transitions. Every page here is same-origin, so this one
   rule turns navigation between them into a morph rather than a cut. Browsers
   without support just navigate as they do today. */
@view-transition { navigation: auto; }

/* The rail wordmark is on every page, so naming it lets it travel across the
   navigation instead of cross-fading with the rest. Exactly one element per
   page may carry a given name — a duplicate makes the browser skip the
   transition entirely. */
[style*="height:48px"] > div:first-child { view-transition-name: rail-identity; }

/* .rise fires on load, so every section below the fold finished animating long
   before anyone scrolled to it. Drive it from the element's own position in the
   viewport instead. Anything already on screen at load is past the range and
   renders in its final state.

   Only these one-shot reveals move to a timeline. The looping animations —
   rekey, pulse, scan, marquee, drift — are deliberately left alone. */
@supports (animation-timeline: view()) {
  .rise {
    animation-timeline: view();
    animation-range: entry 5% cover 30%;
  }
}
</style>
"""

RESPONSIVE_CSS = """
<style>
/* Never let the page itself scroll sideways; wide tables scroll in their own
   wrapper, which the artboards already mark overflow-x:auto. */
html, body { max-width: 100%; overflow-x: hidden; }

@media (max-width: 760px) {
  /* auto-fit only drops empty tracks — it never shrinks one below its floor,
     so a minmax(420px,1fr) track stays 420px on a 375px phone and everything
     inside it gets clipped. Collapse these to a single column. */
  [style*="minmax(420px"], [style*="minmax(340px"], [style*="minmax(300px"],
  [style*="minmax(280px"], [style*="minmax(260px"], [style*="minmax(230px"],
  [style*="minmax(150px"],
  [style*="repeat(3, minmax(0, 1fr))"], [style*="repeat(3,minmax(0,1fr))"],
  [style*="repeat(2, minmax(0, 1fr))"], [style*="repeat(2,minmax(0,1fr))"] {
    grid-template-columns: 1fr !important;
  }

  /* ── the status rail ──────────────────────────────────────────────────
     Three groups: [0] wordmark, [1] six nav links, [2] a decorative status
     readout. The rail is pinned to 48px with its link group set to wrap, so
     on a phone the wrapped rows overlap the wordmark. Let it grow, and give
     each group its own row rather than letting them reflow into each other. */
  [style*="height:48px"] {
    height: auto !important;
    flex-wrap: wrap !important;
    row-gap: 2px !important;
    padding-top: 9px !important;
    padding-bottom: 9px !important;
    align-items: flex-start !important;
  }
  [style*="height:48px"] > div {
    flex: 0 0 100% !important;
  }
  /* Tap targets. 11px links 6px apart are not touchable; this gets each to
     roughly 32px of vertical hit area without changing the type size much. */
  [style*="height:48px"] a {
    padding: 5px 0 !important;
    font-size: 11.5px !important;
  }
  /* The artboard sets a single `gap`, which applies to rows too — combined with
     the tap padding that opened a large hole between wrapped link rows. Split
     the two axes: tight rows, comfortable columns. */
  [style*="height:48px"] > div:nth-child(2) {
    row-gap: 0 !important;
    column-gap: 13px !important;
  }

  /* The CV timeline puts a fixed date column beside the content. On a phone
     that leaves under 200px for the text, which wraps to three words a line.
     Stack the date above its entry instead and keep the accent rail. */
  .cv-tl { grid-template-columns: 1fr !important; }
  .cv-when {
    margin-top: 22px;
    letter-spacing: .12em;
  }
  .cv-what {
    padding-left: 18px !important;
    padding-bottom: 20px !important;
  }

  /* Fixed two-up rows (icon gutter + content) read better stacked. */
  [style*="grid-template-columns:64px 1fr auto"] {
    grid-template-columns: 1fr !important;
  }
}

/* On the narrowest screens the rail's third group — the colour legend on the
   landing page, the phase counter on a project page — is decoration competing
   with navigation for a whole row. Drop it before dropping any link. */
/* The collaborator name tags sit beside a caret at a line end. On the narrowest
   screens there is no line short enough for the tag to fit inside the mockup, so
   the tag goes and the caret keeps blinking. */
@media (max-width: 560px) {
  .cur-tag { display: none; }
}

@media (max-width: 560px) {
  [style*="height:48px"] > div:nth-child(3) {
    display: none !important;
  }
}

/* Between phone and desktop a three-up grid is cramped but two fits. */
@media (min-width: 761px) and (max-width: 1024px) {
  [style*="repeat(3, minmax(0, 1fr))"], [style*="repeat(3,minmax(0,1fr))"] {
    grid-template-columns: repeat(2, minmax(0, 1fr)) !important;
  }
}
</style>
"""

APEX = "https://genuinebasil.dev"
FLUID_URL = "/fluid.js"

# Pages served from their own subdomain rather than a path on the apex. Their
# root-relative links point at the apex, so they have to be absolutised — on
# another origin "/" is that origin's root, not this site's.
OWN_HOST = {
    "cv": "https://cv.genuinebasil.dev",
}


# Only links that genuinely point at apex content. A blanket rewrite of every
# root-relative href would also drag the page's own assets — the CV PDF sits
# beside the page on its own host, and "/" there is that host's root, correctly.
APEX_PATHS = ('href="/"', 'href="/#', 'href="/projects/')


def absolutise(html: str) -> str:
    """Point apex-bound hrefs at the apex, for pages served on another origin."""
    for prefix in APEX_PATHS:
        html = html.replace(prefix, prefix.replace('href="/', f'href="{APEX}/'))
    return html


def convert(src: pathlib.Path, title: str, desc: str, slug: str) -> str:
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

    # "." is the site root; every other slug is its own path.
    slug_url = "" if slug == "." else slug
    og = "landing" if slug == "." else slug.replace("/", "-")

    accent = GLOW_ACCENT.get(slug, "224,152,90")
    # cv is on its own host, so it needs the apex URL; everything else is
    # served from the same root as the script.
    glow_js = GLOW_JS % {
        "dye": ALL_THREE if accent == "rekey" else accent,
        "fluid_src": (APEX + FLUID_URL) if slug in OWN_HOST else FLUID_URL,
    }

    if slug in OWN_HOST:
        canonical = OWN_HOST[slug] + "/"
        body = absolutise(body)
    else:
        canonical = f"{APEX}/{slug_url}"

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
<meta property="og:url" content="{canonical}">
<link rel="canonical" href="{canonical}">
<meta property="og:image" content="https://genuinebasil.dev/og/{og}.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<link rel="icon" href="{FAVICON}">
{head}
{RESPONSIVE_CSS}
{MOTION_CSS}
{GLOW_CSS}
</head>
<body>
{body}
{glow_js}
</body>
</html>
"""


def fluid_asset(srcdir: pathlib.Path, outdir: pathlib.Path) -> str:
    """Write the simulation under a content-hashed name and return its URL.

    A stable name is a liability here: /fluid.js had already been 301'd to the
    blog before it existed, Cloudflare cached that redirect, and every later
    deploy kept serving the redirect to browsers while a cache-busted request
    returned the file. A hashed name sidesteps a stale entry entirely, and
    changes on its own whenever the file does.
    """
    src = srcdir / "static" / "fluid.js"
    if not src.is_file():
        return ""
    raw = src.read_bytes()
    name = f"fluid.{hashlib.sha256(raw).hexdigest()[:10]}.js"
    (outdir / name).write_bytes(raw)
    # Keep the previous builds. Cloudflare serves HTML for a while after a
    # deploy, and that stale HTML points at the previous hash — deleting it
    # immediately is what turns a harmless cache lag into a 404. Three is
    # plenty of overlap at 17KB each.
    olds = sorted((f for f in outdir.glob("fluid.*.js") if f.name != name),
                  key=lambda f: f.stat().st_mtime, reverse=True)
    for stale in olds[2:]:
        stale.unlink()
    print(f"static: static/fluid.js -> {outdir / name}")
    return "/" + name


def copy_static(srcdir: pathlib.Path, outdir: pathlib.Path) -> None:
    """Mirror static/ into the output tree, preserving structure.

    Anything not generated from an artboard lives there — the CV PDF, for one —
    so sites/ stays reproducible from sources rather than accumulating files
    that only exist because someone once dropped them in the build output.
    """
    static = srcdir / "static"
    if not static.is_dir():
        return
    for src in static.rglob("*"):
        if not src.is_file() or src.name in {"README.md", ".gitkeep", "fluid.js"}:
            continue
        dest = outdir / src.relative_to(static)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(src.read_bytes())
        print(f"static: {src.relative_to(srcdir)} -> {dest}")


def main() -> None:
    srcdir = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    outdir = pathlib.Path(sys.argv[2] if len(sys.argv) > 2 else "sites")
    outdir.mkdir(parents=True, exist_ok=True)
    global FLUID_URL
    FLUID_URL = fluid_asset(srcdir, outdir)

    for stem, slug, title, desc in PAGES:
        html = convert(srcdir / f"{stem}.dc.html", title, desc, slug)
        if slug == "404":
            # Caddy's handle_errors rewrites to a file, so this one is not a
            # directory with an index inside it.
            out = outdir / "404.html"
            out.parent.mkdir(parents=True, exist_ok=True)
        else:
            dest = outdir / slug
            dest.mkdir(parents=True, exist_ok=True)
            out = dest / "index.html"
        out.write_text(html, encoding="utf-8")
        print(f"{stem}.dc.html -> {out}  ({len(html):,} bytes)")

    copy_static(srcdir, outdir)


if __name__ == "__main__":
    main()
