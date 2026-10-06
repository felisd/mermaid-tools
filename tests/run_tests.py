#!/usr/bin/env python3
"""
Test suite for mermaid2png.

    python tests/run_tests.py            # everything
    python tests/run_tests.py --no-network   # skip the download test

Renders every file in samples/ into out/, checks each PNG (valid, not blank,
not clipped), then exercises scaling, backgrounds, error handling, timeout
recovery, Markdown extraction and the command line. Also writes
out/contact_sheet.png so the results can be eyeballed in one image.

Needs Pillow in addition to Playwright:  pip install pillow
"""
from __future__ import annotations

import io
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageChops  # noqa: E402

Image.MAX_IMAGE_PIXELS = None  # the large-diagram test is big on purpose

import mermaid2png as m  # noqa: E402

SAMPLES = ROOT / "samples"
OUT = ROOT / "out"
SCRIPT = ROOT / "mermaid2png.py"

results: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: str = "") -> bool:
    results.append((name, bool(condition), detail))
    print(f"  {'PASS' if condition else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))
    return bool(condition)


def open_png(data: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(data))
    img.load()
    return img


def border_is_clean(img: Image.Image, ring: int = 4) -> bool:
    """The outer `ring` pixels must be a single colour.

    Every render has padding, so anything touching the edge means the diagram
    was clipped or measured too small.
    """
    rgba = img.convert("RGBA")
    w, h = rgba.size
    strips = [
        rgba.crop((0, 0, w, ring)),
        rgba.crop((0, h - ring, w, h)),
        rgba.crop((0, 0, ring, h)),
        rgba.crop((w - ring, 0, w, h)),
    ]
    colours = set()
    for strip in strips:
        colours.update(c for _, c in strip.getcolors(maxcolors=1 << 20))
    return len(colours) == 1


def ink_ratio(img: Image.Image) -> float:
    """Fraction of pixels that differ from the corner (background) colour."""
    small = img.convert("RGBA")
    small.thumbnail((400, 400))
    bg = small.getpixel((0, 0))
    counts = dict((colour, n) for n, colour in small.getcolors(maxcolors=1 << 20))
    total = small.width * small.height
    return (total - counts.get(bg, 0)) / total


def contact_sheet(files: list[Path], dest: Path, cell: int = 420, cols: int = 5) -> None:
    from PIL import ImageDraw

    rows = -(-len(files) // cols)
    label_h = 26
    sheet = Image.new("RGB", (cols * cell, rows * (cell + label_h)), "#f0f0f0")
    draw = ImageDraw.Draw(sheet)
    for i, f in enumerate(files):
        img = Image.open(f).convert("RGBA")
        img.thumbnail((cell - 16, cell - 16), Image.LANCZOS)
        x = (i % cols) * cell
        y = (i // cols) * (cell + label_h)
        tile = Image.new("RGB", (cell - 8, cell - 8), "white")
        tile.paste(img, ((tile.width - img.width) // 2, (tile.height - img.height) // 2), img)
        sheet.paste(tile, (x + 4, y + label_h))
        draw.text((x + 8, y + 6), f.stem, fill="black")
    sheet.save(dest)


def main() -> int:
    use_network = "--no-network" not in sys.argv
    OUT.mkdir(exist_ok=True)
    for old in OUT.glob("*.png"):
        old.unlink()
    started = time.time()

    with m.MermaidRenderer() as r:
        # ------------------------------------------------------------------ #
        print("\n[1] every sample renders to a sane PNG")
        sample_files = sorted(SAMPLES.glob("*.mmd"))
        rendered: list[Path] = []
        for src in sample_files:
            if src.stem.startswith("9"):  # 9x_ files are expected failures
                continue
            dest = OUT / f"{src.stem}.png"
            try:
                res = r.render_file(src, dest, details=True)
            except m.MermaidError as exc:
                check(f"{src.name} renders", False, str(exc).splitlines()[0])
                continue
            img = open_png(res.png)
            ok = (
                dest.is_file()
                and img.size == (res.width, res.height)
                and min(img.size) >= 100
                and ink_ratio(img) > 0.004
                and border_is_clean(img)
            )
            check(f"{src.name}", ok, f"{res.width}x{res.height} {res.diagram_type} ink={ink_ratio(img):.1%}")
            rendered.append(dest)

        # ------------------------------------------------------------------ #
        print("\n[2] resolution controls")
        code = (SAMPLES / "01_flowchart.mmd").read_text(encoding="utf-8")
        s1 = r.render(code, scale=1, details=True)
        s2 = r.render(code, scale=2, details=True)
        s4 = r.render(code, scale=4, details=True)
        check("scale=4 is exactly 2x the pixels of scale=2",
              (s4.width, s4.height) == (2 * s2.width, 2 * s2.height),
              f"{s2.width}x{s2.height} -> {s4.width}x{s4.height}")
        check("scale=1 is half of scale=2", abs(2 * s1.width - s2.width) <= 2 and abs(2 * s1.height - s2.height) <= 2,
              f"{s1.width}x{s1.height}")
        got = {want: r.render(code, width=want, details=True).width for want in (333, 500, 1920, 4000, 4001, 7777)}
        check("width=N gives exactly N pixels", all(k == v for k, v in got.items()), str(got))
        big = r.render((SAMPLES / "25_large_flowchart.mmd").read_text(), scale=8, details=True)
        check("huge diagram at scale=8 is capped, not broken",
              max(big.width, big.height) <= m.MAX_SIDE_PX
              and big.width * big.height <= m.MAX_TOTAL_PX * 1.01
              and big.scale < 8 and border_is_clean(open_png(big.png)) and bool(r.warnings),
              f"{big.width}x{big.height} at scale {big.scale}")
        pad0 = r.render(code, scale=1, padding=0, details=True)
        pad40 = r.render(code, scale=1, padding=40, details=True)
        check("padding adds exactly 2*padding", (pad40.width - pad0.width, pad40.height - pad0.height) == (80, 80),
              f"{pad0.width}->{pad40.width}")

        # ------------------------------------------------------------------ #
        print("\n[3] themes and backgrounds")
        white = open_png(r.render(code)).convert("RGBA").getpixel((0, 0))
        check("default background is opaque white", white == (255, 255, 255, 255), str(white))
        clear = open_png(r.render(code, background="transparent")).convert("RGBA").getpixel((0, 0))
        check("transparent background has alpha 0", clear[3] == 0, str(clear))
        red = open_png(r.render(code, background="#ff0000")).convert("RGBA").getpixel((0, 0))
        check("custom background colour is applied", red == (255, 0, 0, 255), str(red))
        dark = open_png(r.render(code, theme="dark")).convert("RGBA").getpixel((0, 0))
        check("theme=dark gets a dark background automatically", dark == (30, 30, 30, 255), str(dark))
        fm = open_png(r.render((SAMPLES / "16_dark_frontmatter.mmd").read_text())).convert("RGBA").getpixel((0, 0))
        check("dark theme set in front matter is detected", fm == (30, 30, 30, 255), str(fm))
        again = open_png(r.render(code)).convert("RGBA").getpixel((0, 0))
        check("theme does not leak into the next diagram", again == (255, 255, 255, 255), str(again))
        sizes = {}
        for theme in m.THEMES:
            res = r.render(code, OUT / f"theme_{theme}.png", theme=theme, details=True)
            sizes[theme] = (res.width, res.height)
        check("all five built-in themes render", len(sizes) == 5, str(sizes))
        a, b = open_png(r.render(code)).convert("RGBA"), open_png(r.render(code)).convert("RGBA")
        check("output is deterministic (identical pixels twice)",
              a.size == b.size and ImageChops.difference(a, b).getbbox() is None)
        custom = r.render(code, config={"themeVariables": {"fontSize": "24px"}}, details=True)
        check("config overrides reach mermaid (bigger font => bigger image)", custom.width > s1.width * 2,
              f"{custom.width} vs {s1.width * 2}")
        narrow = r.render((SAMPLES / "06_gantt.mmd").read_text(), page_width=600, scale=1, details=True)
        wide = r.render((SAMPLES / "06_gantt.mmd").read_text(), page_width=1600, scale=1, details=True)
        check("page_width controls stretchy diagrams (gantt)", wide.width > narrow.width + 500,
              f"{narrow.width} vs {wide.width}")

        # ------------------------------------------------------------------ #
        print("\n[4] errors are reported and the renderer keeps working")
        bad = (SAMPLES / "90_invalid_syntax.mmd").read_text()
        try:
            r.render(bad, OUT / "should_not_exist.png")
            check("invalid syntax raises MermaidSyntaxError", False, "no exception")
        except m.MermaidSyntaxError as exc:
            check("invalid syntax raises MermaidSyntaxError", "line" in str(exc).lower(), str(exc).splitlines()[0])
        check("no file is written for a failed render", not (OUT / "should_not_exist.png").exists())
        for label, text in (("empty", ""), ("whitespace", "  \n\n"), ("unknown type", "notADiagram\n  a --> b")):
            try:
                r.render(text)
                check(f"{label} input raises", False, "no exception")
            except m.MermaidError as exc:
                check(f"{label} input raises", True, type(exc).__name__)
        try:
            r.render((SAMPLES / "25_large_flowchart.mmd").read_text(), timeout=0.02)
            check("timeout raises MermaidTimeoutError", False, "no exception")
        except m.MermaidTimeoutError as exc:
            check("timeout raises MermaidTimeoutError", True, str(exc))
        after = r.render(code, details=True)
        check("renderer still works after errors and a timeout", after.width == 2 * s1.width, f"{after.width}")
        crlf = r.render(code.replace("\n", "\r\n"), scale=1, details=True)
        bom = r.render("﻿" + code, scale=1, details=True)
        check("CRLF line endings and a BOM are tolerated",
              (crlf.width, crlf.height) == (s1.width, s1.height) == (bom.width, bom.height))

    # ---------------------------------------------------------------------- #
    print("\n[5] Markdown extraction")
    md = (SAMPLES / "26_markdown_doc.md").read_text(encoding="utf-8")
    blocks = m.extract_mermaid_blocks(md)
    check("finds exactly the 3 mermaid blocks", len(blocks) == 3, f"found {len(blocks)}")
    check("block order and content preserved",
          len(blocks) == 3 and blocks[0].startswith("flowchart LR") and blocks[1].startswith("sequenceDiagram")
          and blocks[2].startswith("pie"))
    check("indented block is dedented", len(blocks) == 3 and not blocks[2].startswith(" "))
    check("python block is ignored", all("print(" not in b for b in blocks))

    # ---------------------------------------------------------------------- #
    print("\n[6] command line")
    env = dict(os.environ)

    def cli(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(SCRIPT), *args], input=stdin, env=env,
                              capture_output=True, text=True, timeout=300)

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        p = cli(str(SAMPLES / "07_pie.mmd"), "-o", str(tmp / "pie.png"))
        check("single file -> named png, exit 0", p.returncode == 0 and (tmp / "pie.png").stat().st_size > 5000,
              p.stderr.strip().splitlines()[-1] if p.stderr.strip() else "")
        p = cli(str(SAMPLES / "26_markdown_doc.md"), "-o", str(tmp / "md"))
        made = sorted(f.name for f in (tmp / "md").glob("*.png"))
        check("markdown -> one png per block", p.returncode == 0 and made ==
              ["26_markdown_doc-1.png", "26_markdown_doc-2.png", "26_markdown_doc-3.png"], str(made))
        p = cli("-", "-o", str(tmp / "stdin.png"), "-t", "forest", "-b", "transparent", stdin="graph LR; A-->B-->C")
        ok = p.returncode == 0 and Image.open(tmp / "stdin.png").convert("RGBA").getpixel((0, 0))[3] == 0
        check("stdin + theme + transparent", ok)
        p = cli(str(SAMPLES), "-o", str(tmp / "all"), "-s", "1", "-q")
        made = list((tmp / "all").glob("*.png"))
        n_good = len([f for f in SAMPLES.glob("*.mmd") if not f.stem.startswith("9")])
        check("folder: good files rendered, bad one reported, exit 1",
              p.returncode == 1 and len(made) == n_good and "90_invalid_syntax.mmd" in p.stderr
              and "Parse error" in p.stderr, f"{len(made)} pngs, exit {p.returncode}")
        p = cli(str(tmp / "missing.mmd"))
        check("missing input -> exit 2 with message", p.returncode == 2 and "not found" in p.stderr)
        (tmp / "cfg.json").write_text('{"flowchart": {"curve": "stepAfter"}, "themeVariables": {"fontSize": "20px"}}')
        (tmp / "x.css").write_text(".node rect { stroke-width: 4px !important; }")
        p = cli(str(SAMPLES / "01_flowchart.mmd"), "-o", str(tmp / "cfg.png"), "-c", str(tmp / "cfg.json"),
                "--css", str(tmp / "x.css"), "-w", "1600")
        check("--config, --css and --width together",
              p.returncode == 0 and Image.open(tmp / "cfg.png").width == 1600)

        # ------------------------------------------------------------------ #
        print("\n[7] one-shot helper and first-run download")
        data = m.mermaid_to_png("graph TD; A-->B", tmp / "oneshot.png", scale=1)
        check("mermaid_to_png(code, path)", data[:4] == b"\x89PNG" and (tmp / "oneshot.png").read_bytes() == data)
        import asyncio

        async def inside_loop() -> bytes:
            return m.mermaid_to_png("graph TD; A-->B", scale=1)

        check("mermaid_to_png works inside a running asyncio loop (Jupyter)",
              asyncio.run(inside_loop())[:4] == b"\x89PNG")
        if use_network:
            cache = tmp / "cache"
            dest = cache / f"mermaid-{m.MERMAID_VERSION}.min.js"
            try:
                got = m._download_mermaid_js(m.MERMAID_VERSION, dest)
                fresh = m.mermaid_to_png("graph TD; A-->B", mermaid_js=got, scale=1)
                check("mermaid.js downloads from scratch and renders",
                      got.stat().st_size > 1_000_000 and fresh[:4] == b"\x89PNG",
                      f"{got.stat().st_size / 1e6:.1f} MB")
            except m.MermaidError as exc:
                check("mermaid.js downloads from scratch and renders", False, str(exc))
        else:
            print("  SKIP  download test (--no-network)")

    sheet_files = sorted(f for f in OUT.glob("[0-9]*.png"))
    contact_sheet(sheet_files, OUT / "contact_sheet.png")

    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed in {time.time() - started:.1f}s")
    for name, _, detail in failed:
        print(f"  FAILED: {name}  [{detail}]")
    print(f"PNGs and contact sheet are in {OUT}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
