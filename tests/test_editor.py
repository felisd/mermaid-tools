#!/usr/bin/env python3
"""End-to-end test of editor/mermaid-editor.html in headless Chromium: real clicks, drags and key presses.

    python editor/build.py && python tests/test_editor.py     (needs playwright and pillow)
"""
import io, json, os, sys, tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
URL = (ROOT / "editor" / "mermaid-editor.html").as_uri()
SAMPLES = ROOT / "samples"
WORK = Path(tempfile.mkdtemp(prefix="editor-test-"))  # scratch files stay out of the repo
os.chdir(WORK)
fails = []

def check(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))
    if not ok: fails.append(name)

EXTRA = '''flowchart TD
    %% a comment
    A[Start] --> B{"x > 0 && y < 10?"}
    B -- "yes #quot;q#quot;" --> C(["Stadium <b>bold</b><br/>two"])
    B -.->|no| D[(DB)]
    C ==> E((circle)) & F>odd]
    E --o G{{hex}}
    G x--x H[/lean/]
    A ~~~ H
    A ---> I["`**md** text`"]
    I <--> K[[sub]] 
    K o--o L[\\\\lean left\\\\]
    L -.-o M[/trap\\\\]
    M ==x N[\\\\inv/]
    N <-.-> O(((dbl)))
    O <==> A
    subgraph sg1 ["My group"]
        direction LR
        C
        D
        subgraph inner [Inner]
            E
        end
    end
    J@{ shape: doc, label: "Document" }
    I --> J
    classDef hot fill:#f96,stroke:#333,stroke-width:2px,color:#000
    class A,B hot
    D:::hot
    style C fill:#bbf,stroke:#f66
    style sg1 fill:#eef,stroke:#00f
    linkStyle 1 stroke:#f00,stroke-width:3px
    click A href "https://example.com" "tip"
'''.replace("\\\\\\\\", "\\")

with sync_playwright() as pw:
    b = pw.chromium.launch()
    ctx = b.new_context(viewport={"width": 1440, "height": 860}, accept_downloads=True)
    p = ctx.new_page()
    errs = []
    p.on("pageerror", lambda e: errs.append(str(e)))
    p.goto(URL); p.wait_for_function("window.__editor && __editor.S.model")

    def load(text, name="t.mmd"):
        p.evaluate("([t, n]) => __editor.loadDoc(t, n, null)", [text, name]); settle()
    def settle():
        p.wait_for_timeout(350)
        p.evaluate("() => __editor.refresh()")
    def text(): return p.evaluate("__editor.S.text")
    def model(): return p.evaluate("__editor.S.model")
    def center(sel_js):
        return p.evaluate("(code) => { const e = eval(code); const r = e.getBoundingClientRect(); return [r.left + r.width/2, r.top + r.height/2, r.width, r.height]; }", sel_js)
    def node_xy(nid): return center(f"[...document.querySelectorAll('#sheet g.node')].find(g => new RegExp('-flowchart-{nid}-\\\\d+$').test(g.id))")
    def click_node(nid, shift=False):
        x, y, *_ = node_xy(nid)
        if shift: p.keyboard.down("Shift")
        p.mouse.click(x, y)
        if shift: p.keyboard.up("Shift")
        p.wait_for_timeout(60)
    def dbl_node(nid):
        x, y, *_ = node_xy(nid); p.mouse.click(x, y); p.wait_for_timeout(80); p.mouse.click(x, y); p.wait_for_timeout(150)
    def sel(): return p.evaluate("__editor.S.sel")

    print("[1] every flowchart sample survives the tidy rewrite")
    flows = {f.name: f.read_text() for f in sorted(SAMPLES.glob("*.mmd")) if not f.name.startswith("9") and (f.read_text().lstrip("-% \n").startswith(("flowchart", "graph")) or "flowchart" in f.read_text()[:400])}
    flows["extra-syntax"] = EXTRA
    for name, src in flows.items():
        load(src, name)
        st = p.evaluate("({can: __editor.S.canEdit, why: __editor.S.whyNot, st: document.querySelector('#status').textContent})")
        if not st["can"]:
            check(f"{name}: editable", False, st["why"] + " | " + st["st"][:120]); continue
        r = p.evaluate("""async () => { const E = __editor; const t1 = E.serialize(E.S.model); const m2 = await E.parseFlow(t1); const t2 = E.serialize(m2);
            const a = await mermaid.render('ta', E.S.text), b2 = await mermaid.render('tb', t1);
            const cnt = (s) => [(s.match(/class="node /g)||[]).length, (s.match(/flowchart-link/g)||[]).length, (s.match(/class="cluster/g)||[]).length];
            return {same: E.signature(m2) === E.signature(E.S.model), idem: t1 === t2, a: cnt(a.svg), b: cnt(b2.svg), t1}; }""")
        check(f"{name}: same structure, idempotent, same drawn elements", r["same"] and r["idem"] and r["a"] == r["b"], f"nodes/links/groups {r['a']}")
        if name == "extra-syntax":
            t1 = r["t1"]; Path("extra_tidy.mmd").write_text(t1)
            check("  keeps comment, click, classDef, style, linkStyle, quotes, <br/>",
                  all(s in t1 for s in ['%% a comment', 'click A href', 'classDef hot', 'style C fill:#bbf', 'style sg1', 'linkStyle 1 stroke:#f00', '#quot;q#quot;', '<br/>', 'x > 0 && y < 10', 'direction LR', 'J@{ shape: doc', '"`**md** text`"']), "")

    print("\n[2] non-flowcharts and errors")
    n_other = 0
    for f in sorted(SAMPLES.glob("*.mmd")):
        if f.name in flows or f.name.startswith("9"): continue
        load(f.read_text(), f.name); n_other += 1
        st = p.evaluate("({can: __editor.S.canEdit, svg: !!document.querySelector('#sheet svg'), err: document.querySelector('#status').classList.contains('err'), note: document.querySelector('#note').textContent})")
        if st["can"] or not st["svg"] or st["err"] or "code pane" not in st["note"]:
            check(f"{f.name} previews with a text-edit note", False, json.dumps(st))
    check(f"{n_other} other diagram types preview, with a note that they are edited as text", True)
    load((SAMPLES / "90_invalid_syntax.mmd").read_text(), "bad.mmd")
    st = p.evaluate("({err: document.querySelector('#status').classList.contains('err'), msg: document.querySelector('#status').textContent, can: __editor.S.canEdit})")
    check("invalid code shows the parse error and disables editing", st["err"] and "Parse error" in st["msg"] and not st["can"], st["msg"].splitlines()[0])

    print("\n[3] editing by mouse and keyboard")
    load("flowchart TD\n    A[Start] --> B{Check}\n    B -->|ok| C[Done]\n", "edit.mmd")
    click_node("A")
    check("click selects a node and shows its properties", sel() == [{"kind": "node", "id": "A"}] and p.inner_text("#insp h2") == "Node")
    check("connector handles appear next to it", p.evaluate("document.querySelectorAll('.handle.on').length") == 2)
    dbl_node("A")
    check("double-click opens the in-place editor with the label", p.evaluate("document.querySelector('#inline').classList.contains('on') && document.querySelector('#inline').value") == "Start")
    p.keyboard.press("Control+A"); p.keyboard.type('Begin "here"'); p.keyboard.press("Shift+Enter"); p.keyboard.type("x > 1 && y"); p.keyboard.press("Enter"); settle()
    check("typed label (quotes, newline, > and &&) is written as valid Mermaid", 'A["Begin #quot;here#quot;<br/>x > 1 && y"]' in text(), [l for l in text().splitlines() if l.strip().startswith("A")][0])
    check("...and reads back as typed", model()["nodes"][0]["label"] == 'Begin "here"<br/>x > 1 && y')

    # drag handle A -> C
    click_node("A"); hx, hy, *_ = center("document.querySelector('#h1')"); cx, cy, *_ = node_xy("C")
    p.mouse.move(hx, hy); p.mouse.down(); p.mouse.move((hx + cx) / 2, (hy + cy) / 2, steps=4); p.mouse.move(cx, cy, steps=4)
    tgt = p.evaluate("document.querySelectorAll('.m-target').length")
    p.mouse.up(); settle()
    check("dragging the + onto another node adds a link", "A --> C" in text() and tgt == 1 and len(model()["edges"]) == 3)
    check("the new link is selected", sel() == [{"kind": "edge", "id": "2"}] and p.inner_text("#insp h2") == "Link")

    # drag handle to empty space -> new node
    click_node("C"); hx, hy, *_ = center("document.querySelector('#h1')")
    p.mouse.move(hx, hy); p.mouse.down(); p.mouse.move(hx + 160, hy + 40, steps=5); p.mouse.up(); settle(); p.wait_for_timeout(200)
    check("dropping the + on empty space adds a connected node and starts naming it",
          "C --> n1" in text() and p.evaluate("document.querySelector('#inline').classList.contains('on')"))
    p.keyboard.type("Archive"); p.keyboard.press("Enter"); settle()
    check("new node gets the typed name", 'n1["Archive"]' in text())

    click_node("n1"); p.keyboard.press("Tab"); settle(); p.wait_for_timeout(200); p.keyboard.type("Notify"); p.keyboard.press("Enter"); settle()
    check("Tab adds a connected node", 'n2["Notify"]' in text() and "n1 --> n2" in text())

    # shape + colours via inspector
    click_node("B"); p.click('#insp .shapes button[title="Hexagon"]'); settle()
    check("shape button changes the shape", 'B{{"Check"}}' in text())
    click_node("B"); p.evaluate("""() => { const i = document.querySelector('[data-f=nfill]'); i.value = '#ffcc00'; i.dispatchEvent(new Event('change', {bubbles: true})); }"""); settle()
    p.evaluate("""() => { const i = document.querySelector('[data-f=nstroke]'); i.value = '#aa0000'; i.dispatchEvent(new Event('change', {bubbles: true})); }"""); settle()
    check("colour pickers write a style line", "style B fill:#ffcc00,stroke:#aa0000" in text())
    fill = p.evaluate("getComputedStyle([...document.querySelectorAll('#sheet g.node')].find(g => /-flowchart-B-/.test(g.id)).querySelector('polygon, rect, path')).fill")
    check("...and the node is drawn in that colour", fill == "rgb(255, 204, 0)", fill)
    p.click('#insp .color:first-child .x'); settle()
    check("clearing a colour removes it from the style", "style B stroke:#aa0000" in text())

    # rename id
    click_node("C"); p.fill('[data-f=nid]', "done"); p.keyboard.press("Enter"); settle()
    check("renaming an ID updates every link", 'done["Done"]' in text() and "A --> done" in text() and "done --> n1" in text() and " C" not in text().replace("TD", ""))
    click_node("done"); p.fill('[data-f=nid]', "end"); p.keyboard.press("Enter"); settle()
    check("reserved word 'end' is refused as an ID", "done[" in text() and "bad" in p.get_attribute("#toast", "class"))

    # edge: click the line, edit label, style
    px, py = p.evaluate("""() => { const pth = [...document.querySelectorAll('#sheet path.flowchart-link')].find(x => x.getAttribute('data-id') === 'L_A_B_0'); const pt = pth.getPointAtLength(pth.getTotalLength() * 0.5); const m = pth.getScreenCTM(); return [pt.x * m.a + pt.y * m.c + m.e, pt.x * m.b + pt.y * m.d + m.f]; }""")
    p.mouse.click(px + 5, py); p.wait_for_timeout(80)
    check("clicking near a line selects the link", sel() == [{"kind": "edge", "id": "0"}], json.dumps(sel()))
    p.keyboard.press("Enter"); p.wait_for_timeout(100); p.keyboard.type("go"); p.keyboard.press("Enter"); settle()
    check("Enter on a link edits its label", 'A -->|"go"| B' in text())
    p.click('[data-f=estrokedotted]'); settle(); p.click('[data-f=eheadcircle]'); settle(); p.click('[data-f="elen2"]'); settle()
    check("line style, end marker and length", 'A -..-o|"go"| B' in text(), [l.strip() for l in text().splitlines() if l.strip().startswith("A -")][0])
    p.click('[data-f=edbl]'); settle()
    check("marker on both ends", 'A o-..-o|"go"| B' in text())
    p.click("text=Reverse direction"); settle()
    check("reverse direction", 'B o-..-o|"go"| A' in text())
    p.evaluate("""() => { const i = document.querySelector('[data-f=ecolor]'); i.value = '#0055ff'; i.dispatchEvent(new Event('change', {bubbles: true})); }"""); settle()
    check("link colour writes linkStyle with the right index", "linkStyle 0 stroke:#0055ff" in text())

    # grouping
    click_node("n2")
    inview = p.evaluate("""() => { const c = document.querySelector('#canvas').getBoundingClientRect(); return [...document.querySelectorAll('#sheet g.node')].filter(g => /-flowchart-n[12]-/.test(g.id)).every(g => { const r = g.getBoundingClientRect(); return r.left >= c.left && r.right <= c.right && r.top >= c.top && r.bottom <= c.bottom; }); }""")
    check("nodes added past the edge of the window were scrolled into view", inview)
    p.click("#zfit"); p.wait_for_timeout(80)
    click_node("n1"); click_node("n2", shift=True)
    check("shift-click selects several nodes", len(sel()) == 2 and p.inner_text("#insp h2") == "2 nodes selected")
    p.click("#bgroup"); settle(); p.wait_for_timeout(200)
    check("Group selected wraps them in a subgraph and asks for a title", "subgraph group1" in text() and p.evaluate("document.querySelector('#inline').classList.contains('on')"))
    p.keyboard.type("Follow-up"); p.keyboard.press("Enter"); settle()
    check("group title is set", 'subgraph group1 ["Follow-up"]' in text())
    m = model(); sub = m["subs"][0]
    check("both nodes are inside the group", sorted(sub["nodes"]) == ["n1", "n2"])
    gx, gy = p.evaluate("""() => { const r = document.querySelector('#sheet g.cluster rect').getBoundingClientRect(); return [r.left + 6, r.top + 6]; }""")
    p.mouse.click(gx, gy); p.wait_for_timeout(80)
    check("clicking the frame selects the group", sel() == [{"kind": "sub", "id": "group1"}] and p.inner_text("#insp h2") == "Group")
    p.click('[data-f=gdirLR]'); settle()
    check("direction inside the group", "direction LR" in text())
    click_node("done"); p.select_option('[data-f=ngroup]', "group1"); settle()
    check("a node can be moved into a group from its properties", sorted(model()["subs"][0]["nodes"]) == ["done", "n1", "n2"])
    p.mouse.click(*p.evaluate("""() => { const r = document.querySelector('#sheet g.cluster rect').getBoundingClientRect(); return [r.left + 6, r.top + 6]; }""")); p.wait_for_timeout(80)
    p.keyboard.press("Delete"); settle()
    check("Delete on a group removes the frame and keeps the nodes", "subgraph" not in text() and len(model()["nodes"]) == 5)

    # delete node, undo, redo
    before = text(); n_edges = len(model()["edges"])
    click_node("n1"); p.keyboard.press("Delete"); settle()
    check("Delete removes the node and its links, and keeps link colours on the right link",
          "n1" not in text() and len(model()["edges"]) == n_edges - 2 and "linkStyle 0 stroke:#0055ff" in text())
    p.keyboard.press("Control+z"); settle()
    check("Ctrl+Z restores it", text() == before)
    p.keyboard.press("Control+y"); settle()
    check("Ctrl+Y removes it again", "n1" not in text())

    # direction + theme
    p.keyboard.press("Escape"); p.click('[data-f=dirLR]'); settle()
    check("direction buttons rewrite the first line", text().startswith("flowchart LR"))
    p.select_option("#themesel", "forest"); settle()
    check("theme is stored as front matter in the file", text().startswith("---\nconfig:\n  theme: forest\n---\nflowchart LR") and p.evaluate("__editor.S.canEdit"))
    p.select_option("#themesel", "dark"); settle()
    check("changing theme again edits it in place; dark theme darkens the canvas", "theme: dark" in text() and text().count("theme:") == 1 and "dark" in p.get_attribute("#canvas", "class"))

    # add node button, typing in code pane
    p.click("#baddnode"); settle(); p.wait_for_timeout(200); p.keyboard.type("Loose"); p.keyboard.press("Enter"); settle()
    check("Add node creates an unconnected node", '["Loose"]' in text())
    p.click("#src"); p.keyboard.press("Control+End"); p.keyboard.type("    Loose2[typed] --> A\n"); p.wait_for_timeout(700)
    check("typing in the code pane updates the picture and the model", any(n["id"] == "Loose2" for n in model()["nodes"]))
    ids = [n["id"] for n in model()["nodes"]]

    # pan / zoom
    v0 = p.evaluate("({...__editor.S.view})"); c = center("document.querySelector('#canvas')")
    p.mouse.move(c[0] + 300, c[1] + 300); p.mouse.down(); p.mouse.move(c[0] + 360, c[1] + 330, steps=3); p.mouse.up()
    v1 = p.evaluate("({...__editor.S.view})"); p.mouse.wheel(0, -300); p.wait_for_timeout(50); v2 = p.evaluate("({...__editor.S.view})")
    check("drag pans, wheel zooms", abs(v1["x"] - v0["x"] - 60) < 2 and abs(v1["y"] - v0["y"] - 30) < 2 and v2["k"] > v1["k"])

    print("\n[4] files and export")
    with p.expect_download() as d: p.click("#bpng")
    data = Path(d.value.path()).read_bytes(); img = Image.open(io.BytesIO(data)); img.save("export_test.png")
    colours = len(img.convert("RGB").resize((200, 200)).getcolors(40000))
    check("Export PNG downloads a real, non-blank image", data[:4] == b"\x89PNG" and img.width > 300 and colours > 20 and d.value.suggested_filename == "edit.png", f"{img.width}x{img.height}, {colours} colours")
    with p.expect_download() as d: p.click("#bsvg")
    svg = Path(d.value.path()).read_text()
    check("Export SVG downloads the drawing without editor overlays", svg.startswith("<svg") and "m-hit" not in svg and "m-sel" not in svg)
    check("unsaved marker is on", "on" in p.get_attribute("#dirty", "class"))
    saved = text()
    p.evaluate("() => { window.showSaveFilePicker = undefined; window.prompt = () => 'my-flow'; }")
    with p.expect_download() as d: p.click("#bsaveas")
    check("Save as (fallback) downloads the Mermaid text as .mmd", Path(d.value.path()).read_text() == saved and d.value.suggested_filename == "my-flow.mmd")
    check("unsaved marker clears after saving", "on" not in p.get_attribute("#dirty", "class") and p.inner_text("#fname") == "my-flow.mmd")
    md = "# Doc\n\n```mermaid\nflowchart LR\n    X --> Y\n```\n"
    p.evaluate("() => { window.showOpenFilePicker = undefined; }")
    with p.expect_file_chooser() as fc: p.click("#bopen")
    Path("doc.md").write_text(md); fc.value.set_files("doc.md"); settle()
    check("opening a Markdown file pulls out its mermaid block", text() == "flowchart LR\n    X --> Y\n" and p.inner_text("#fname") == "doc.mmd")
    p.reload(); p.wait_for_function("window.__editor && __editor.S.model")
    check("the draft is restored after reloading the page", p.evaluate("__editor.S.text") == "flowchart LR\n    X --> Y\n")

    print("\n[5] saved file renders with mermaid2png.py")
    Path("saved_by_editor.mmd").write_text(saved)
    check("no page errors during the whole run", not errs, "; ".join(errs[:3]))
    p.evaluate("([t]) => __editor.loadDoc(t, 'final.mmd', null)", [saved]); settle(); p.keyboard.press("Escape")
    p.screenshot(path="shot_final.png")
    b.close()

import subprocess
r = subprocess.run([sys.executable, str(ROOT / "mermaid2png.py"), "saved_by_editor.mmd", "extra_tidy.mmd"], capture_output=True, text=True)
check("mermaid2png.py converts files written by the editor", r.returncode == 0 and r.stderr.count("ok ") == 2, r.stderr.strip().splitlines()[-1])
print(f"\n{'ALL PASSED' if not fails else str(len(fails)) + ' FAILED: ' + '; '.join(fails)}")
sys.exit(1 if fails else 0)
