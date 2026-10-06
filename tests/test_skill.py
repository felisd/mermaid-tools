#!/usr/bin/env python3
"""Checks the skill package: frontmatter rules, installer, and the exact command the model is told to run."""
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "opencode-skill"
fails = []


def check(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))
    if not ok:
        fails.append(name)


def run(cmd, cwd=None):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, shell=isinstance(cmd, str), timeout=300)


print("[1] SKILL.md follows opencode's rules")
text = (ROOT / "mermaid-diagram" / "SKILL.md").read_text(encoding="utf-8")
m = re.match(r"---\n(.*?)\n---\n", text, re.DOTALL)
check("has YAML frontmatter", bool(m))
front = dict(line.split(": ", 1) for line in m.group(1).splitlines())
check("only name and description", set(front) == {"name", "description"}, str(set(front)))
check("name is valid and matches the folder",
      bool(re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", front["name"])) and len(front["name"]) <= 64
      and front["name"] == "mermaid-diagram")
check("description is 1-1024 characters", 1 <= len(front["description"]) <= 1024, f"{len(front['description'])} chars")
check("description needs no YAML quoting", ": " not in front["description"] and "#" not in front["description"])
check("skill body is short", len(text.split()) < 600, f"{len(text.split())} words")

with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    cfg, proj = tmp / "cfg dir" / "opencode", tmp / "project"  # space in path on purpose
    proj.mkdir()
    cfg.mkdir(parents=True)
    (cfg / "AGENTS.md").write_text("# My rules\n\nAlways answer in English.\n")

    print("\n[2] installer")
    p = run([sys.executable, str(ROOT / "install.py"), "--dir", str(cfg)])
    skill = cfg / "skills" / "mermaid-diagram"
    check("installer exits 0", p.returncode == 0, p.stdout.strip().splitlines()[-1] if p.stdout else p.stderr)
    check("skill files copied", all((skill / f).is_file() for f in
                                    ("SKILL.md", "scripts/mermaid2png.py", "scripts/mermaid.min.js")))
    check("/diagram command installed", "$ARGUMENTS" in (cfg / "commands" / "diagram.md").read_text())
    installed = (skill / "SKILL.md").read_text()
    rules = (cfg / "AGENTS.md").read_text()
    check("placeholder path replaced everywhere", "~/.config" not in installed and "~/.config" not in rules)
    check("existing AGENTS.md content kept", rules.startswith("# My rules\n\nAlways answer in English."))
    run([sys.executable, str(ROOT / "install.py"), "--dir", str(cfg)])
    check("re-install does not duplicate the rule",
          (cfg / "AGENTS.md").read_text().count("mermaid-diagram:start") == 1 and (cfg / "AGENTS.md").read_text() == rules)

    print("\n[3] the exact command from the installed SKILL.md works")
    cmd = re.search(r"```\n(.*?) diagrams/<short-name>\.mmd\n```", installed, re.DOTALL).group(1)
    same = re.search(r"`([^`\n]*mermaid2png\.py\"?) diagrams/<short-name>\.mmd`", rules).group(1)
    check("SKILL.md and AGENTS.md give the same command", cmd == same, cmd)
    (proj / "diagrams").mkdir()
    (proj / "diagrams" / "login-flow.mmd").write_text("flowchart TD\n  A[Start] --> B{OK?}\n  B -- yes --> C[Done]\n")
    p = run(f"{cmd} diagrams/login-flow.mmd", cwd=proj)
    png = proj / "diagrams" / "login-flow.png"
    check("good file: prints ok, exit 0, PNG written",
          p.returncode == 0 and p.stderr.startswith("ok") and png.read_bytes()[:4] == b"\x89PNG", p.stderr.strip())
    (proj / "diagrams" / "bad.mmd").write_text("flowchart TD\n  A[Start] --> B{OK?\n  B --> C\n")
    p = run(f"{cmd} diagrams/bad.mmd", cwd=proj)
    check("bad file: prints FAIL + line number, exit 1, no PNG",
          p.returncode == 1 and p.stderr.startswith("FAIL") and "syntax error" in p.stderr
          and re.search(r"line \d+", p.stderr) and not (proj / "diagrams" / "bad.png").exists(),
          p.stderr.strip().splitlines()[1].strip() if p.stderr else "")
    (proj / "diagrams" / "bad.mmd").write_text("flowchart TD\n  A[Start] --> B{OK?}\n  B --> C\n")
    p = run(f"{cmd} diagrams/bad.mmd diagrams/login-flow.mmd -t dark -w 800", cwd=proj)
    check("after fixing: rerun succeeds, several files + options", p.returncode == 0 and p.stderr.count("ok ") == 2)

    print("\n[4] project install and uninstall")
    p = run([sys.executable, str(ROOT / "install.py"), "--project"], cwd=proj)
    check("--project writes .opencode/skills, .opencode/commands and AGENTS.md",
          (proj / ".opencode/skills/mermaid-diagram/SKILL.md").is_file()
          and (proj / ".opencode/commands/diagram.md").is_file() and "mermaid-diagram" in (proj / "AGENTS.md").read_text())
    run([sys.executable, str(ROOT / "install.py"), "--dir", str(cfg), "--uninstall"])
    check("--uninstall removes skill, command and rule but keeps other rules",
          not skill.exists() and not (cfg / "commands" / "diagram.md").exists()
          and (cfg / "AGENTS.md").read_text() == "# My rules\n\nAlways answer in English.\n")

print(f"\n{'ALL PASSED' if not fails else 'FAILED: ' + ', '.join(fails)}")
sys.exit(1 if fails else 0)
