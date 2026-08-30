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
import json
import re
import sys

from palette_map import PALETTE
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
# The dark accents are built to glow on near-black; on paper they have to be
# ink instead, so light mode gets the light palette's versions.
ALL_THREE_LIGHT = "154,91,18|91,63,209|14,138,99"
LIGHT_DYE = {
    "63,199,154":  "14,138,99",
    "156,135,237": "91,63,209",
    "224,152,90":  "154,91,18",
}

# The sweep, applied by what an element *is* rather than what it is called.
# The artboards give every panel one of a few border colours inline, so those
# attribute selectors catch panels regardless of tag or class — including the
# <a class="card"> ones a markup pass keyed on <div> missed, and any panel added
# later. :has() excludes anything that already carries a .scan child, so the two
# approaches cannot double up.
SWEEP_CSS = """
<style>
[style*="border:1px solid #1D232A"],
[style*="border:1px solid #191E25"],
[style*="border:1px solid #2A323B"],
[style*="border:1px solid #232A32"],
[style*="border-top:1px solid #191E25"] { position: relative; }

[style*="border:1px solid #1D232A"]:not(:has(> .scan))::before,
[style*="border:1px solid #191E25"]:not(:has(> .scan))::before,
[style*="border:1px solid #2A323B"]:not(:has(> .scan))::before,
[style*="border:1px solid #232A32"]:not(:has(> .scan))::before,
[style*="border-top:1px solid #191E25"]:not(:has(> .scan))::before {
  content: "";
  position: absolute; inset: 0;
  border-radius: inherit;
  pointer-events: none;
  background: linear-gradient(90deg, transparent, %(sweep)s0d, transparent);
  animation: scan 5.5s cubic-bezier(.45, 0, .55, 1) infinite;
}
</style>
"""

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
  opacity: .66;
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
  if (grid && grid.parentNode) {
    grid.parentNode.setAttribute('data-fluid', '%(dye)s');
    grid.parentNode.setAttribute('data-fluid-light', '%(dye_light)s');
  }
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

# ── theming ────────────────────────────────────────────────────────────────
# The artboards write colours as literal hex inside style="" attributes — over
# a thousand of them — and an inline style beats any stylesheet, so no media
# query or class can repaint them. Each hex becomes a custom property instead,
# with the original as the fallback, so a missing token degrades to the dark
# value rather than to nothing.
def tokenise(html: str) -> str:
    """Replace literal hex with var(--cNN, #original)."""
    def sub(m):
        hexval = m.group(0).upper()
        if hexval not in TOKEN:
            return m.group(0)
        return f"var({TOKEN[hexval]},{hexval})"
    # 6 digits not followed by another, so #RRGGBBAA alpha values are untouched
    return re.sub(r"#[0-9A-Fa-f]{6}(?![0-9A-Fa-f])", sub, html)


TOKEN = {h: f"--c{i}" for i, h in enumerate(sorted(PALETTE))}

THEME_CSS = "<style>\n:root{" + "".join(
    f"{TOKEN[h]}:{h};" for h in sorted(PALETTE)
) + "}\n[data-theme=\"light\"]{" + "".join(
    f"{TOKEN[h]}:{PALETTE[h]};" for h in sorted(PALETTE)
) + "}\n" + """
/* rgba() literals are not hex, so the tokeniser leaves them be — and a few of
   them are structural. The sticky rail carries the page colour at 90%, and the
   panel shadows are near-black, which on paper reads as soot. Both are matched
   on the literal the artboards wrote. */
[data-theme="light"] [style*="rgba(10,12,15,.9)"] {
  background: rgba(244, 245, 246, .86) !important;
  backdrop-filter: saturate(1.6) blur(6px);
}
[data-theme="light"] [style*="rgba(0,0,0,.95)"],
[data-theme="light"] [style*="rgba(0,0,0,.9)"] {
  box-shadow: 0 24px 60px -32px rgba(24, 32, 45, .18) !important;
}
[data-theme="light"] [style*="rgba(0,0,0,.5)"],
[data-theme="light"] [style*="rgba(0,0,0,.4)"] {
  box-shadow: 0 14px 34px -20px rgba(24, 32, 45, .16) !important;
}

/* The fluid is additive dye designed for near-black. On paper it has to darken
   what is under it instead of adding light, or it reads as a bleached stain. */
[data-theme="light"] .mfluid-gl { opacity: .42; }
[data-theme="light"] ::selection { background: rgba(var(--mglow-rgb), .22); color: #12151A; }
.theme-btn {
  display: inline-flex; align-items: center; justify-content: center;
  width: 26px; height: 26px; flex: none;
  border: 1px solid var(--c-border, #262C33);
  border-radius: 5px; background: transparent; cursor: pointer;
  color: inherit; padding: 0;
  transition: border-color .2s ease, color .2s ease;
}
.theme-btn:hover { color: #E7EAEC; }
[data-theme="light"] .theme-btn:hover { color: #12151A; }
.theme-btn svg { width: 14px; height: 14px; }
.theme-btn .moon { display: none; }
[data-theme="light"] .theme-btn .moon { display: block; }
[data-theme="light"] .theme-btn .sun { display: none; }
</style>"""

THEME_JS_T = """
<script>
var THEME_ICONS = %(icons)s;
(function () {
  // The rail is the one 48px flex bar on every page; its last cluster is the
  // right-hand status group. Injecting here rather than editing six artboards
  // means the button lands in the same place on all of them, including any
  // page added later.
  var rail = document.querySelector('[style*="height:48px"]');
  if (!rail) return;
  // Into the rail's last group, not the rail itself: the rail is a
  // space-between flex row, and a fourth direct child pushes the status
  // cluster off the right edge.
  var group = rail.lastElementChild || rail;

  var btn = document.createElement('button');
  btn.className = 'theme-btn';
  btn.type = 'button';
  btn.setAttribute('aria-label', 'Switch colour theme');
  btn.title = 'Switch theme';
  btn.innerHTML = THEME_ICONS;
  group.appendChild(btn);

  btn.addEventListener('click', function () {
    var next = document.documentElement.getAttribute('data-theme') === 'light' ? 'dark' : 'light';
    document.documentElement.setAttribute('data-theme', next);
    try { localStorage.setItem('theme', next); } catch (e) {}
    // the fluid samples the accent per frame, so it picks the change up on its own
  });
})();
</script>
"""

# Runs before the body paints, so a light-mode reader never sees a dark flash.
THEME_BOOT = """<script>
(function(){try{var t=localStorage.getItem('theme');
if(!t)t=matchMedia('(prefers-color-scheme: light)').matches?'light':'dark';
document.documentElement.setAttribute('data-theme',t);}catch(e){}})();
</script>"""

THEME_ICONS_HTML = ('<svg class="sun" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round">'
             '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>'
             '<svg class="moon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
             '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>')

RESPONSIVE_CSS = """
<style>
/* Never let the page itself scroll sideways; wide tables scroll in their own
   wrapper, which the artboards already mark overflow-x:auto. */
/* clip, not hidden: overflow-x:hidden forces overflow-y to auto, which makes
   body a scroll container and traps position:sticky. clip stops the sideways
   scroll without creating one. */
html, body { max-width: 100%; overflow-x: clip; }

/* No scrollbars anywhere — the page still scrolls, the gutter just is not
   drawn. Safe here because the reading-progress bar across the top already
   shows position, which is the job the scrollbar was doing. Applied to the
   inner wrappers too, so the wide tables scroll without a bar under them. */
html { scrollbar-width: none; -ms-overflow-style: none; }
html::-webkit-scrollbar { width: 0; height: 0; display: none; }
[style*="overflow-x:auto"], [style*="overflow-x: auto"],
[style*="overflow:auto"], [style*="overflow: auto"] {
  scrollbar-width: none;
  -ms-overflow-style: none;
}
[style*="overflow-x:auto"]::-webkit-scrollbar,
[style*="overflow-x: auto"]::-webkit-scrollbar,
[style*="overflow:auto"]::-webkit-scrollbar,
[style*="overflow: auto"]::-webkit-scrollbar { width: 0; height: 0; display: none; }

/* The artboards clip horizontal overflow on the page wrapper, which also traps
   position:sticky — the rail was declared sticky and scrolled away regardless.
   The clipping is redundant now that body does it, so the wrapper releases it
   and the rail can actually stick. */
[style*="overflow-x:clip"] { overflow-x: visible !important; }

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
  /* ...but never the theme switcher, which is a control, not decoration. It
     lives inside that group, so hide the group's other children instead. */
  [style*="height:48px"] > div:nth-child(3):has(> .theme-btn) {
    display: flex !important;
  }
  [style*="height:48px"] > div:nth-child(3) > *:not(.theme-btn) {
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
    sweep_hex = {"63,199,154": "#3FC79A", "156,135,237": "#9C87ED",
                 "224,152,90": "#E0985A"}.get(accent, "#9C87ED")
    sweep_css = SWEEP_CSS % {"sweep": sweep_hex}
    # cv is on its own host, so it needs the apex URL; everything else is
    # served from the same root as the script.
    theme_js = THEME_JS_T % {"icons": json.dumps(THEME_ICONS_HTML)}
    glow_js = GLOW_JS % {
        "dye": ALL_THREE if accent == "rekey" else accent,
        "dye_light": ALL_THREE_LIGHT if accent == "rekey" else LIGHT_DYE.get(accent, accent),
        "fluid_src": (APEX + FLUID_URL) if slug in OWN_HOST else FLUID_URL,
    }

    if slug in OWN_HOST:
        canonical = OWN_HOST[slug] + "/"
        body = absolutise(body)
    else:
        canonical = f"{APEX}/{slug_url}"

    head = tokenise(head)
    body = tokenise(body)

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
{THEME_BOOT}
{head}
{THEME_CSS}
{RESPONSIVE_CSS}
{MOTION_CSS}
{GLOW_CSS}
{sweep_css}
</head>
<body>
{body}
{glow_js}
{theme_js}
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
