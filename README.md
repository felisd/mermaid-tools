# mermaid-tools

Tools for working with [Mermaid](https://mermaid.js.org) diagrams (text that
describes a flowchart, sequence diagram, etc.). Pick the one that fits what you
want to do:

| I want to... | Use | Command |
|---|---|---|
| Turn Mermaid text into a PNG image | [Converter](#converter) | `python mermaid2png.py diagram.mmd` |
| Draw or edit a diagram in a browser | [Editor](#editor) | open `editor/mermaid-editor.html` in Chrome or Edge |
| Have an [opencode](https://opencode.ai) AI model draw diagrams for me | [opencode skill](#opencode-skill) | `python opencode-skill/install.py` |

A `.mmd` file is a plain text file containing Mermaid code. All three tools
share one bundled copy of mermaid.js (`vendor/mermaid.min.js`, v11.16.1), so
diagrams look the same everywhere and nothing needs the internet after setup.

## Converter

Turns `.mmd` files, folders or Markdown into PNG.

### Setup

    pip install playwright
    python -m playwright install chromium

If Chrome or Edge is already installed, the converter falls back to it, so the
second command can be skipped (`--chrome PATH` picks a specific browser).

### Usage

    python mermaid2png.py diagram.mmd                 # -> diagram.png
    python mermaid2png.py diagram.mmd -w 1920         # exact pixel width
    python mermaid2png.py docs/README.md -o images/   # every ```mermaid block
    python mermaid2png.py samples/ -o out/ -t dark    # whole folder, dark theme

A folder run converts every `.mmd`/`.mermaid` file and every Markdown file that
contains `mermaid` blocks (Markdown files without any are skipped). A Markdown
file named directly must contain at least one block.

Themes: `default`, `neutral`, `dark`, `forest`, `base`. A theme named inside
the diagram file wins over `-t`. Run with `-h` for all options.

From Python:

    from mermaid2png import MermaidRenderer, mermaid_to_png
    mermaid_to_png("graph TD; A-->B", "ab.png")
    with MermaidRenderer(theme="forest") as r:
        r.render_file("a.mmd", "a.png")

## Editor

A visual editor in one HTML file; the saved file is plain Mermaid.

### Setup

None, just a browser.

### Usage

Open `editor/mermaid-editor.html`. Flowcharts can be edited by clicking and
dragging; other diagram types are edited as text with a live preview. Mermaid
places the boxes itself, so they cannot be dragged to a new position. The
first visual edit rewrites the code in a standard layout (undo restores it).

### Rebuilding

After changing `editor/editor.src.html` or replacing mermaid.js (standard
library only, nothing to install):

    python editor/build.py

## opencode skill

Makes a local model answer diagram requests with a `.mmd` file plus a PNG.

### Setup

The skill calls the converter, so it needs the same install as the
[Converter](#setup). Run `install.py` with the Python that has Playwright.

### Usage

    python opencode-skill/install.py             # all projects
    python opencode-skill/install.py --project   # current project only
    python opencode-skill/install.py --uninstall

It copies `SKILL.md`, `mermaid2png.py` and `vendor/mermaid.min.js` into
opencode's skills folder, adds a rule to `AGENTS.md` (skip with `--no-rules`)
and a `/diagram` command, and writes the real interpreter and script paths into
them. Run it again after updating the converter. See `opencode-skill/README.md`
for how the forcing works.

Keep your own diagrams outside this folder, as `diagrams/<name>.mmd` in each
project (the path the skill uses). Commit the PNGs only where a document links
to them.

## Development

### Layout

    mermaid2png.py            converter: command line and Python module
    vendor/mermaid.min.js     the only copy of mermaid.js
    samples/                  test diagrams (every diagram type, edge cases, one invalid file)
    editor/
      editor.src.html         editor source
      build.py                inlines vendor/mermaid.min.js
      mermaid-editor.html     built single-file editor (committed so it can be opened directly)
    opencode-skill/
      install.py              installs skill + AGENTS.md rule + /diagram command
      mermaid-diagram/SKILL.md
      extras/                 AGENTS.md rule and /diagram command templates
    tests/                    one test script per tool

### Tests

    python tests/run_tests.py     # converter: renders samples/ into out/, 60+ checks
    python tests/test_editor.py   # editor: real clicks, drags and key presses
    python tests/test_skill.py    # skill: frontmatter, installer, installed command

The first two need the converter install plus `pip install pillow`.
`test_skill.py` needs nothing.

### Upgrading mermaid.js

Replace `vendor/mermaid.min.js`, run `python editor/build.py`, run the three
test scripts, then re-run `opencode-skill/install.py`. The editor's flowchart
editing relies on mermaid's internal parser output, so the editor test is the
one to watch after an upgrade.
