#!/usr/bin/env python3
"""Build editor/mermaid-editor.html: inline vendor/mermaid.min.js into editor.src.html.

    python editor/build.py
"""
from pathlib import Path
here = Path(__file__).resolve().parent
js = (here.parent / "vendor" / "mermaid.min.js").read_text(encoding="utf-8")
# keep the HTML parser from ending or mis-reading the inline script
js = js.replace("</script", "<\\/script").replace("<!--", "<\\!--")
src = (here / "editor.src.html").read_text(encoding="utf-8")
assert "/*__MERMAID_JS__*/" in src
out = src.replace("/*__MERMAID_JS__*/", js)
(here / "mermaid-editor.html").write_text(out, encoding="utf-8")
print(f"editor/mermaid-editor.html  {len(out.encode()) / 1e6:.2f} MB")
