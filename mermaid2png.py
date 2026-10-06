#!/usr/bin/env python3
"""
mermaid2png - convert Mermaid diagrams to PNG.

Renders with the real mermaid.js inside headless Chromium (via Playwright),
which is the same approach the VS Code Mermaid preview and the online live
editor use. Because a real browser does the layout and painting, every diagram
type, HTML labels, emoji, CJK text and custom themes come out the way they do
in those tools.

Install once:
    pip install playwright
    python -m playwright install chromium

Command line:
    python mermaid2png.py diagram.mmd                  # -> diagram.png
    python mermaid2png.py diagram.mmd -o out.png -s 3  # 3x resolution
    python mermaid2png.py docs/README.md -o images/    # every ```mermaid block
    python mermaid2png.py samples/ -o out/ -t dark     # whole folder
    cat diagram.mmd | python mermaid2png.py - -o out.png

Python:
    from mermaid2png import MermaidRenderer, mermaid_to_png

    mermaid_to_png("graph TD; A-->B", "ab.png")        # one-off

    with MermaidRenderer(theme="forest", scale=2) as r:  # many diagrams, one browser
        r.render_file("a.mmd", "a.png")
        png_bytes = r.render("sequenceDiagram\\n A->>B: hi")
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import tarfile
import tempfile
import textwrap
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

__version__ = "1.0.0"
__all__ = [
    "MermaidRenderer",
    "mermaid_to_png",
    "extract_mermaid_blocks",
    "find_mermaid_js",
    "MermaidError",
    "MermaidSyntaxError",
    "MermaidTimeoutError",
    "RenderResult",
]

# mermaid.js release used when the library has to be downloaded. Pinned so the
# same input always produces the same picture; override with --mermaid-version.
MERMAID_VERSION = "11.16.1"

THEMES = ("default", "neutral", "dark", "forest", "base")
DARK_BACKGROUND = "#1e1e1e"  # VS Code's dark editor background
LIGHT_BACKGROUND = "white"

# Chromium cannot reliably capture surfaces beyond these limits, so the scale is
# reduced (with a warning) instead of producing a blank or truncated image.
MAX_SIDE_PX = 16000
MAX_TOTAL_PX = 120_000_000

MERMAID_SUFFIXES = {".mmd", ".mermaid"}
MARKDOWN_SUFFIXES = {".md", ".markdown", ".mdx", ".qmd", ".rmd"}


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
class MermaidError(RuntimeError):
    """Base class for everything this module raises."""


class MermaidSyntaxError(MermaidError):
    """mermaid.js rejected the diagram source."""


class MermaidTimeoutError(MermaidError):
    """Rendering did not finish within the timeout."""


@dataclass
class RenderResult:
    """What a render produced. `png` is always set; `path` only when saved."""

    png: bytes
    width: int  # pixels in the PNG
    height: int
    scale: float  # device scale factor actually used
    diagram_type: str
    path: Path | None = None


# --------------------------------------------------------------------------- #
# Locating / downloading mermaid.js
# --------------------------------------------------------------------------- #
def _cache_dir() -> Path:
    if os.environ.get("MERMAID2PNG_CACHE"):
        return Path(os.environ["MERMAID2PNG_CACHE"])
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "mermaid2png"


def _http_get(url: str, timeout: float = 60.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": f"mermaid2png/{__version__}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (https only)
        return resp.read()


def _download_from_npm(version: str) -> bytes:
    """Pull dist/mermaid.min.js out of the official npm tarball."""
    meta = json.loads(_http_get(f"https://registry.npmjs.org/mermaid/{version}"))
    tarball = _http_get(meta["dist"]["tarball"], timeout=180.0)
    with tarfile.open(fileobj=io.BytesIO(tarball), mode="r:gz") as tf:
        member = tf.extractfile("package/dist/mermaid.min.js")
        if member is None:
            raise MermaidError("mermaid.min.js missing from npm tarball")
        return member.read()


def _download_mermaid_js(version: str, dest: Path) -> Path:
    sources = [
        ("jsDelivr", lambda: _http_get(f"https://cdn.jsdelivr.net/npm/mermaid@{version}/dist/mermaid.min.js")),
        ("unpkg", lambda: _http_get(f"https://unpkg.com/mermaid@{version}/dist/mermaid.min.js")),
        ("npm registry", lambda: _download_from_npm(version)),
    ]
    failures = []
    for name, fetch in sources:
        try:
            data = fetch()
            if len(data) < 200_000 or b"mermaid" not in data[:200_000]:
                raise MermaidError(f"unexpected content ({len(data)} bytes)")
        except Exception as exc:  # try the next mirror
            failures.append(f"  {name}: {exc}")
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=dest.parent, suffix=".part")
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, dest)  # atomic: a half-written file never gets used
        return dest
    raise MermaidError(
        f"Could not download mermaid.js {version}:\n" + "\n".join(failures) + "\n"
        "Download mermaid.min.js yourself and pass --mermaid-js PATH "
        "(or put it next to mermaid2png.py)."
    )


def find_mermaid_js(
    path: str | os.PathLike | None = None,
    version: str = MERMAID_VERSION,
    allow_download: bool = True,
) -> Path:
    """Return a local mermaid.min.js, downloading and caching it if needed.

    Lookup order: explicit path, $MERMAID_JS, a copy beside this script, a
    local node_modules install, the cache, then a download (jsDelivr, unpkg,
    npm registry). After the first run no network access is needed.
    """
    if path:
        p = Path(path).expanduser()
        if not p.is_file():
            raise MermaidError(f"mermaid.js not found: {p}")
        return p
    if os.environ.get("MERMAID_JS"):
        p = Path(os.environ["MERMAID_JS"]).expanduser()
        if not p.is_file():
            raise MermaidError(f"$MERMAID_JS points to a missing file: {p}")
        return p

    here = Path(__file__).resolve().parent
    cached = _cache_dir() / f"mermaid-{version}.min.js"
    candidates = [
        here / "mermaid.min.js",
        here / "vendor" / "mermaid.min.js",
        Path.cwd() / "node_modules" / "mermaid" / "dist" / "mermaid.min.js",
        cached,
    ]
    for cand in candidates:
        if cand.is_file() and cand.stat().st_size > 200_000:
            return cand
    if not allow_download:
        raise MermaidError("mermaid.min.js not found locally and downloading is disabled")
    return _download_mermaid_js(version, cached)


# --------------------------------------------------------------------------- #
# Markdown support
# --------------------------------------------------------------------------- #
_FENCE_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?P<fence>`{3,}|~{3,})[ \t]*\{?\.?mermaid\b[^\n]*\n"
    r"(?P<body>.*?)"
    r"^[ \t]*(?P=fence)[`~]*[ \t]*$",
    re.MULTILINE | re.DOTALL | re.IGNORECASE,
)


def extract_mermaid_blocks(markdown: str) -> list[str]:
    """Return the source of every ```mermaid (or ~~~mermaid) block, in order."""
    markdown = markdown.replace("\r\n", "\n")
    blocks = []
    for m in _FENCE_RE.finditer(markdown):
        body = textwrap.dedent(m.group("body")).strip("\n")
        if body.strip():
            blocks.append(body)
    return blocks


# --------------------------------------------------------------------------- #
# The page that hosts mermaid.js
# --------------------------------------------------------------------------- #
_PAGE_HTML = """<!doctype html>
<html><head><meta charset="utf-8">
<style>
  html, body { margin: 0; padding: 0; background: transparent; }
  #holder { display: inline-block; line-height: normal; }
  #holder > svg { display: block; }
</style>
<style id="user-css"></style>
</head><body><div id="holder"></div></body></html>
"""

# Runs in the browser. It never throws: the outcome is stored on window.__m2p so
# Python can wait for it with a hard timeout even if mermaid hangs.
_RENDER_JS = r"""
(args) => {
  window.__m2p = undefined;
  const run = async () => {
    const { code, id, config, padding, background, css } = args;
    const holder = document.getElementById('holder');
    holder.innerHTML = '';
    holder.style.cssText = 'padding:' + padding + 'px';
    document.getElementById('user-css').textContent = css || '';
    document.body.style.background = 'transparent';

    let fontsChanged = false;
    const onFonts = () => { fontsChanged = true; };
    document.fonts.addEventListener('loadingdone', onFonts);
    await document.fonts.ready;

    mermaid.initialize(config);
    try {
      await mermaid.parse(code);
    } catch (e) {
      return { ok: false, kind: 'syntax', message: String((e && e.message) || e) };
    }

    const draw = async (n) => {
      const out = await mermaid.render(id + '-' + n, code);
      holder.innerHTML = out.svg;
      if (out.bindFunctions) { try { out.bindFunctions(holder); } catch (_) {} }
      return out.diagramType || '';
    };

    let diagramType;
    try {
      diagramType = await draw(0);
      // Text is measured during layout. If a web font finished loading while
      // we were drawing, the measurements are stale: lay the diagram out again.
      fontsChanged = false;
      await document.fonts.ready;
      await new Promise(r => setTimeout(r, 0));
      if (fontsChanged) diagramType = await draw(1);
    } catch (e) {
      return { ok: false, kind: 'render', message: String((e && e.message) || e) };
    } finally {
      document.fonts.removeEventListener('loadingdone', onFonts);
    }

    const svg = holder.querySelector('svg');
    if (!svg) return { ok: false, kind: 'render', message: 'mermaid produced no SVG' };

    // Mermaid emits width="100%" + max-width so diagrams shrink to fit a web
    // page. For an image we want the natural size, which is the viewBox.
    let w = 0, h = 0;
    const vb = svg.viewBox && svg.viewBox.baseVal;
    if (vb && vb.width > 0 && vb.height > 0) { w = vb.width; h = vb.height; }
    else { const r = svg.getBoundingClientRect(); w = r.width; h = r.height; }
    if (!(w > 0 && h > 0)) return { ok: false, kind: 'render', message: 'diagram has zero size' };
    svg.style.maxWidth = 'none';
    svg.setAttribute('width', w);
    svg.setAttribute('height', h);

    // Wait for any images used inside labels, then for two painted frames.
    const pending = [...holder.querySelectorAll('img')].filter(i => !i.complete)
      .map(i => new Promise(r => { i.addEventListener('load', r); i.addEventListener('error', r); }));
    await Promise.race([Promise.all(pending), new Promise(r => setTimeout(r, 5000))]);
    await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));

    // The diagram itself may select a theme (front matter / %%{init}%%).
    let dark = false;
    try {
      const cfg = mermaid.mermaidAPI.getConfig();
      dark = cfg.theme === 'dark' || cfg.darkMode === true
          || !!(cfg.themeVariables && cfg.themeVariables.darkMode === true);
    } catch (_) {}

    let bg = background;
    if (bg === 'auto') bg = dark ? args.darkBackground : args.lightBackground;
    if (bg !== 'transparent') document.body.style.background = bg;

    // Snap the frame to whole CSS pixels: the capture area is integral, and a
    // fractional box would shave the last pixel off the right/bottom padding.
    const box = holder.getBoundingClientRect();
    const fw = Math.ceil(box.width - 0.001), fh = Math.ceil(box.height - 0.001);
    holder.style.boxSizing = 'border-box';
    holder.style.width = fw + 'px';
    holder.style.height = fh + 'px';
    return { ok: true, width: fw, height: fh, diagramType, dark };
  };
  // setTimeout so this call returns immediately and Python owns the timeout.
  setTimeout(() => {
    run().then(r => { window.__m2p = r; })
         .catch(e => { window.__m2p = { ok: false, kind: 'render', message: String((e && e.message) || e) }; });
  }, 0);
  return true;
}
"""


# --------------------------------------------------------------------------- #
# Renderer
# --------------------------------------------------------------------------- #
class MermaidRenderer:
    """Keeps one headless Chromium alive and renders any number of diagrams.

    Parameters (all can be overridden per call to render()):
        theme         default | neutral | dark | forest | base
        background    CSS colour, "transparent", or "auto" (white, or dark grey
                      when the diagram uses a dark theme)
        scale         device scale factor; 2 = retina-sharp, 1 = screen size
        padding       blank border around the diagram, in CSS px
        page_width    width of the virtual page; only matters for diagrams that
                      stretch to their container (gantt, timeline, ...)
        config        dict merged into mermaid.initialize() (themeVariables,
                      fontFamily, flowchart: {...}, ...)
        css           extra CSS injected into the page
        timeout       seconds allowed per diagram
        mermaid_js    path to mermaid.min.js (auto-located/downloaded if None)
        executable_path  a specific Chrome/Chromium binary to use
    """

    def __init__(
        self,
        theme: str = "default",
        background: str = "auto",
        scale: float = 2.0,
        padding: int = 16,
        page_width: int = 1000,
        config: dict[str, Any] | None = None,
        css: str | None = None,
        timeout: float = 60.0,
        mermaid_js: str | os.PathLike | None = None,
        mermaid_version: str = MERMAID_VERSION,
        executable_path: str | os.PathLike | None = None,
    ) -> None:
        self.theme = theme
        self.background = background
        self.scale = float(scale)
        self.padding = int(padding)
        self.page_width = int(page_width)
        self.config = dict(config or {})
        self.css = css or ""
        self.timeout = float(timeout)
        self._mermaid_js = find_mermaid_js(mermaid_js, mermaid_version)
        self._executable_path = executable_path or os.environ.get("MERMAID2PNG_CHROME")
        self._pw = None
        self._browser = None
        self._pages: dict[float, Any] = {}  # scale -> (context, page)
        self._counter = 0
        self.warnings: list[str] = []

    # -- lifecycle ---------------------------------------------------------- #
    def __enter__(self) -> "MermaidRenderer":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def start(self) -> None:
        if self._browser is not None:
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise MermaidError(
                "Playwright is required:\n"
                "    pip install playwright\n"
                "    python -m playwright install chromium"
            ) from exc
        self._pw = sync_playwright().start()
        try:
            self._browser = self._launch()
        except Exception:
            self._pw.stop()
            self._pw = None
            raise

    def _launch(self):
        args = ["--force-color-profile=srgb", "--hide-scrollbars"]
        if self._executable_path:
            attempts = [{"executable_path": str(self._executable_path)}]
        else:
            # Playwright's own Chromium first, then an installed Chrome or Edge.
            attempts = [{}, {"channel": "chrome"}, {"channel": "msedge"}]
        errors = []
        for extra in attempts:
            try:
                return self._pw.chromium.launch(headless=True, args=args, **extra)
            except Exception as exc:
                first_line = (str(exc).strip().splitlines() or ["unknown error"])[0]
                errors.append(f"  {extra or 'bundled chromium'}: {first_line}")
        raise MermaidError(
            "Could not start a Chromium browser:\n" + "\n".join(errors) + "\n"
            "Install one with:  python -m playwright install chromium"
        )

    def close(self) -> None:
        for ctx, _page in list(self._pages.values()):
            try:
                ctx.close()
            except Exception:
                pass
        self._pages.clear()
        if self._browser is not None:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._pw is not None:
            try:
                self._pw.stop()
            except Exception:
                pass
            self._pw = None

    # -- pages -------------------------------------------------------------- #
    def _page(self, scale: float):
        """One page per device scale factor (it is fixed per browser context)."""
        key = round(scale, 6)
        if key in self._pages:
            return self._pages[key][1]
        ctx = self._browser.new_context(
            viewport={"width": self.page_width, "height": 800},
            device_scale_factor=key,
        )
        page = ctx.new_page()
        page.set_content(_PAGE_HTML, wait_until="load")
        page.add_script_tag(path=str(self._mermaid_js))
        if not page.evaluate("() => typeof mermaid !== 'undefined' && !!mermaid.render"):
            ctx.close()
            raise MermaidError(f"{self._mermaid_js} did not define a usable `mermaid` object")
        self._pages[key] = (ctx, page)
        # Odd scales (from width=...) would otherwise pile up browser contexts.
        keep = round(self.scale, 6)
        for stale in [k for k in self._pages if k not in (key, keep)][:-3]:
            self._discard_page(stale)
        return page

    def _discard_page(self, scale: float) -> None:
        ctx_page = self._pages.pop(round(scale, 6), None)
        if ctx_page:
            try:
                ctx_page[0].close()
            except Exception:
                pass

    # -- rendering ---------------------------------------------------------- #
    def _draw(self, scale: float, code: str, opts: dict[str, Any], timeout: float) -> dict:
        from playwright.sync_api import Error as PWError, TimeoutError as PWTimeout

        page = self._page(scale)
        page.set_viewport_size({"width": opts["page_width"], "height": 800})
        self._counter += 1
        payload = {
            "code": code,
            "id": f"m2p{self._counter}",
            "config": opts["config"],
            "padding": opts["padding"],
            "background": opts["background"],
            "css": opts["css"],
            "darkBackground": DARK_BACKGROUND,
            "lightBackground": LIGHT_BACKGROUND,
        }
        try:
            page.evaluate(_RENDER_JS, payload)
            page.wait_for_function("() => window.__m2p !== undefined", timeout=timeout * 1000)
            info = page.evaluate("() => window.__m2p")
        except PWTimeout:
            self._discard_page(scale)  # the page may be stuck; start fresh next time
            raise MermaidTimeoutError(f"rendering did not finish within {timeout:g}s") from None
        except PWError as exc:
            self._discard_page(scale)
            raise MermaidError(f"browser error: {exc}") from exc
        if not info.get("ok"):
            message = (info.get("message") or "unknown error").strip()
            if info.get("kind") == "syntax":
                raise MermaidSyntaxError(message)
            raise MermaidError(message)
        return info

    def _fit_scale(self, scale: float, width: float, height: float) -> float:
        """Largest scale <= requested that Chromium can capture in one piece."""
        fitted = min(
            scale,
            MAX_SIDE_PX / width,
            MAX_SIDE_PX / height,
            (MAX_TOTAL_PX / (width * height)) ** 0.5,
        )
        return max(round(fitted - 0.00005, 4), 0.05) if fitted < scale else scale

    def render(
        self,
        code: str,
        output: str | os.PathLike | None = None,
        *,
        theme: str | None = None,
        background: str | None = None,
        scale: float | None = None,
        width: int | None = None,
        padding: int | None = None,
        page_width: int | None = None,
        config: dict[str, Any] | None = None,
        css: str | None = None,
        timeout: float | None = None,
        details: bool = False,
    ) -> bytes | RenderResult:
        """Render Mermaid source to PNG.

        Returns the PNG bytes (or a RenderResult when details=True) and also
        writes them to `output` if given. `width` asks for an exact output
        width in pixels and overrides `scale`.
        """
        if not code or not code.strip():
            raise MermaidSyntaxError("diagram source is empty")
        self.start()

        merged_config: dict[str, Any] = {
            "theme": theme or self.theme,
            "securityLevel": "strict",
            "logLevel": "fatal",
        }
        merged_config.update(self.config)
        merged_config.update(config or {})
        # These must win, or mermaid would auto-run / draw its own error picture.
        merged_config["startOnLoad"] = False
        merged_config["suppressErrorRendering"] = True

        opts = {
            "config": merged_config,
            "padding": self.padding if padding is None else int(padding),
            "background": background or self.background,
            "css": self.css if css is None else css,
            "page_width": int(page_width or self.page_width),
        }
        timeout = self.timeout if timeout is None else float(timeout)
        want = float(scale or self.scale)
        if want <= 0:
            raise ValueError("scale must be positive")

        # Strip a BOM and normalise newlines so Windows files parse the same.
        code = code.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")

        info = self._draw(want, code, opts, timeout)
        used = round(want, 6)
        # Text metrics can shift very slightly with the scale factor, so after
        # changing scale the size is measured again; this settles in a pass or two.
        for _ in range(4):
            target = max((width + 0.02) / info["width"], 0.05) if width else want
            fitted = self._fit_scale(target, info["width"], info["height"])
            if fitted < target and not width:
                note = (
                    f"diagram is {info['width']:.0f}x{info['height']:.0f} CSS px; "
                    f"scale reduced from {target:g} to {fitted:g} to stay within browser limits"
                )
                if note not in self.warnings[-1:]:
                    self.warnings.append(note)
            elif fitted < target:
                note = f"width {width}px is beyond browser limits for this diagram; using scale {fitted:g}"
                if note not in self.warnings[-1:]:
                    self.warnings.append(note)
            if round(fitted, 6) == used:
                break
            used = round(fitted, 6)
            info = self._draw(used, code, opts, timeout)

        from playwright.sync_api import Error as PWError

        page = self._page(used)
        try:
            # Size the viewport to the diagram so the capture is one clean frame.
            page.set_viewport_size({"width": int(info["width"]), "height": int(info["height"])})
            png = page.screenshot(
                type="png",
                clip={"x": 0, "y": 0, "width": info["width"], "height": info["height"]},
                omit_background=(opts["background"] == "transparent"),
                timeout=timeout * 1000,
                animations="disabled",
            )
        except PWError as exc:
            self._discard_page(used)
            raise MermaidError(f"screenshot failed: {exc}") from exc

        px_w, px_h = _png_size(png)
        path = None
        if output is not None:
            path = Path(output)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".part")
            tmp.write_bytes(png)
            os.replace(tmp, path)
        result = RenderResult(png, px_w, px_h, used, info.get("diagramType", ""), path)
        return result if details else png

    def render_file(self, source: str | os.PathLike, output: str | os.PathLike | None = None, **kw):
        """Render a .mmd/.mermaid file. Output defaults to the same name + .png."""
        src = Path(source)
        out = Path(output) if output else src.with_suffix(".png")
        return self.render(_read_text(src), out, **kw)


def _png_size(png: bytes) -> tuple[int, int]:
    if png[:8] != b"\x89PNG\r\n\x1a\n":
        raise MermaidError("browser returned something that is not a PNG")
    return int.from_bytes(png[16:20], "big"), int.from_bytes(png[20:24], "big")


def _read_text(path: Path) -> str:
    data = path.read_bytes()
    for enc in ("utf-8-sig", "utf-16"):
        try:
            return data.decode(enc)
        except UnicodeError:
            continue
    return data.decode("latin-1")


def mermaid_to_png(code_or_path: str | os.PathLike, output: str | os.PathLike | None = None, **options) -> bytes:
    """One-shot helper: starts a browser, renders one diagram, shuts down.

    `code_or_path` is Mermaid source or the path of a .mmd file. Constructor
    options of MermaidRenderer (theme, scale, background, ...) are accepted.
    Safe to call from Jupyter / inside a running asyncio loop.
    """
    text = str(code_or_path)
    is_path = isinstance(code_or_path, os.PathLike) or ("\n" not in text and len(text) < 1024 and Path(text).is_file())
    code = _read_text(Path(text)) if is_path else text
    if is_path and output is None:
        output = Path(text).with_suffix(".png")
    width = options.pop("width", None)

    def work() -> bytes:
        with MermaidRenderer(**options) as renderer:
            return renderer.render(code, output, width=width)

    try:
        import asyncio

        asyncio.get_running_loop()
    except RuntimeError:
        return work()
    # Playwright's sync API refuses to run inside an event loop: use a thread.
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(work).result()


# --------------------------------------------------------------------------- #
# Command line
# --------------------------------------------------------------------------- #
@dataclass
class _Job:
    label: str
    code: str
    output: Path


def _collect_jobs(inputs: Iterable[str], output: str | None) -> list[_Job]:
    """Expand files / folders / stdin / Markdown into (source, output) pairs."""
    found: list[tuple[str, str, Path, str]] = []  # label, code, base dir, stem
    for item in inputs:
        if item == "-":
            found.append(("<stdin>", sys.stdin.read(), Path.cwd(), "diagram"))
            continue
        p = Path(item)
        if p.is_dir():
            files = sorted(f for f in p.rglob("*") if f.suffix.lower() in MERMAID_SUFFIXES | MARKDOWN_SUFFIXES)
            if not files:
                raise MermaidError(f"no .mmd/.mermaid/Markdown files in {p}")
            from_folder = True
        elif p.is_file():
            files = [p]
            from_folder = False
        else:
            raise MermaidError(f"input not found: {item}")
        for f in files:
            text = _read_text(f)
            if f.suffix.lower() in MARKDOWN_SUFFIXES:
                blocks = extract_mermaid_blocks(text)
                if not blocks:
                    if from_folder:  # e.g. a README with no diagrams: just skip it
                        continue
                    raise MermaidError(f"no ```mermaid blocks in {f}")
                for i, block in enumerate(blocks, 1):
                    stem = f.stem if len(blocks) == 1 else f"{f.stem}-{i}"
                    found.append((f"{f} (block {i})", block, f.parent, stem))
            else:
                found.append((str(f), text, f.parent, f.stem))

    out = Path(output) if output else None
    single_file = out is not None and len(found) == 1 and out.suffix.lower() == ".png" and not out.is_dir()
    if out is not None and len(found) > 1 and out.suffix.lower() == ".png":
        raise MermaidError("-o must be a folder when there is more than one diagram")
    jobs, used = [], set()
    for label, code, base, stem in found:
        if single_file:
            dest = out
        else:
            folder = out if out is not None else base
            dest = folder / f"{stem}.png"
            n = 2
            while dest in used:  # same file name from two folders
                dest = folder / f"{stem}_{n}.png"
                n += 1
        used.add(dest)
        jobs.append(_Job(label, code, dest))
    return jobs


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="mermaid2png",
        description="Convert Mermaid diagrams (.mmd files, folders, Markdown, or stdin) to PNG.",
    )
    ap.add_argument("inputs", nargs="+", help=".mmd/.mermaid file, Markdown file, folder, or - for stdin")
    ap.add_argument("-o", "--output", help="output .png (single diagram) or folder")
    ap.add_argument("-t", "--theme", default="default", choices=THEMES)
    ap.add_argument("-b", "--background", default="auto",
                    help='CSS colour, "transparent", or "auto" (default: white / dark grey for dark themes)')
    ap.add_argument("-s", "--scale", type=float, default=2.0, help="resolution multiplier (default 2)")
    ap.add_argument("-w", "--width", type=int, help="exact output width in pixels (overrides --scale)")
    ap.add_argument("-p", "--padding", type=int, default=16, help="border in px (default 16)")
    ap.add_argument("--page-width", type=int, default=1000,
                    help="virtual page width for diagrams that stretch, e.g. gantt (default 1000)")
    ap.add_argument("-c", "--config", help="JSON file merged into mermaid.initialize()")
    ap.add_argument("--css", help="CSS file injected into the page")
    ap.add_argument("--timeout", type=float, default=60.0, help="seconds per diagram (default 60)")
    ap.add_argument("--mermaid-js", help="path to a local mermaid.min.js")
    ap.add_argument("--mermaid-version", default=MERMAID_VERSION,
                    help=f"version to download if none is found locally (default {MERMAID_VERSION})")
    ap.add_argument("--chrome", help="path to a Chrome/Chromium executable")
    ap.add_argument("-q", "--quiet", action="store_true")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    say = (lambda *a: None) if args.quiet else (lambda *a: print(*a, file=sys.stderr))
    try:
        config = json.loads(Path(args.config).read_text(encoding="utf-8")) if args.config else None
        css = Path(args.css).read_text(encoding="utf-8") if args.css else None
        jobs = _collect_jobs(args.inputs, args.output)
        renderer = MermaidRenderer(
            theme=args.theme, background=args.background, scale=args.scale, padding=args.padding,
            page_width=args.page_width, config=config, css=css, timeout=args.timeout,
            mermaid_js=args.mermaid_js, mermaid_version=args.mermaid_version, executable_path=args.chrome,
        )
        renderer.start()
    except (MermaidError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    failed = 0
    with renderer:
        for job in jobs:
            seen = len(renderer.warnings)
            try:
                res = renderer.render(job.code, job.output, width=args.width, details=True)
            except MermaidError as exc:
                failed += 1
                kind = "syntax error" if isinstance(exc, MermaidSyntaxError) else "failed"
                print(f"FAIL  {job.label}: {kind}\n{textwrap.indent(str(exc), '      ')}", file=sys.stderr)
                continue
            say(f"ok    {job.label} -> {job.output}  ({res.width}x{res.height}, {res.diagram_type})")
            for warning in renderer.warnings[seen:]:
                say(f"      note: {warning}")
    say(f"{len(jobs) - failed} of {len(jobs)} diagram(s) rendered")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
