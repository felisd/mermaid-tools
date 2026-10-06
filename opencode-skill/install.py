#!/usr/bin/env python3
"""
Install the mermaid-diagram skill for opencode.

    python opencode-skill/install.py              # global: ~/.config/opencode
    python opencode-skill/install.py --project    # this project only: ./.opencode and ./AGENTS.md
    python opencode-skill/install.py --no-rules   # skip the AGENTS.md rule
    python opencode-skill/install.py --uninstall

It copies the skill together with the converter (mermaid2png.py and
vendor/mermaid.min.js from this repo), then writes the real path of this Python interpreter and
of the converter script into SKILL.md, the AGENTS.md rule and the /diagram
command, so the model gets one exact command to copy. Run it with the Python
that has Playwright installed. Safe to run again after moving or updating.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent  # mermaid2png.py and vendor/ live at the repo root
SKILL_NAME = "mermaid-diagram"
PLACEHOLDER = f"python ~/.config/opencode/skills/{SKILL_NAME}/scripts/mermaid2png.py"
BLOCK_RE = re.compile(r"\n*<!-- mermaid-diagram:start -->.*?<!-- mermaid-diagram:end -->\n*", re.DOTALL)


def global_config_dir() -> Path:
    if os.environ.get("OPENCODE_CONFIG_DIR"):
        return Path(os.environ["OPENCODE_CONFIG_DIR"])
    base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    return Path(base) / "opencode"


def quote(path: Path | str) -> str:
    text = Path(path).as_posix()  # forward slashes work in every shell opencode uses
    return f'"{text}"' if re.search(r"[^\w./:~+-]", text) else text


def strip_block(text: str) -> str:
    return BLOCK_RE.sub("\n\n", text).strip("\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", action="store_true", help="install into the current project instead of globally")
    ap.add_argument("--dir", help="opencode config directory to install into (overrides the default)")
    ap.add_argument("--python", default=sys.executable, help="Python interpreter the model should call")
    ap.add_argument("--no-rules", action="store_true", help="do not add the rule to AGENTS.md")
    ap.add_argument("--no-command", action="store_true", help="do not add the /diagram command")
    ap.add_argument("--uninstall", action="store_true")
    args = ap.parse_args()

    if args.dir:
        config = Path(args.dir).expanduser().resolve()
        rules = config / "AGENTS.md"
    elif args.project:
        config = Path.cwd() / ".opencode"
        rules = Path.cwd() / "AGENTS.md"
    else:
        config = global_config_dir()
        rules = config / "AGENTS.md"
    skill_dir = config / "skills" / SKILL_NAME
    command_file = config / "commands" / "diagram.md"

    if args.uninstall:
        shutil.rmtree(skill_dir, ignore_errors=True)
        command_file.unlink(missing_ok=True)
        if rules.is_file():
            rest = strip_block(rules.read_text(encoding="utf-8"))
            rules.write_text(rest + "\n" if rest else "", encoding="utf-8")
        print(f"removed {skill_dir}, {command_file} and the rule in {rules}")
        return 0

    # 1. the skill itself
    if skill_dir.exists():
        shutil.rmtree(skill_dir)
    shutil.copytree(HERE / SKILL_NAME, skill_dir, ignore=shutil.ignore_patterns("__pycache__"))
    scripts = skill_dir / "scripts"
    scripts.mkdir(exist_ok=True)
    for source in (REPO / "mermaid2png.py", REPO / "vendor" / "mermaid.min.js"):
        if not source.is_file():
            sys.exit(f"missing {source} - run install.py from inside the mermaid-tools repo")
        shutil.copyfile(source, scripts / source.name)
    command = f"{quote(args.python)} {quote(skill_dir / 'scripts' / 'mermaid2png.py')}"
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(skill_md.read_text(encoding="utf-8").replace(PLACEHOLDER, command), encoding="utf-8")
    print(f"skill    -> {skill_dir}")

    # 2. the always-loaded rule that makes the model use it
    if not args.no_rules:
        block = (HERE / "extras" / "AGENTS.snippet.md").read_text(encoding="utf-8").replace(PLACEHOLDER, command)
        existing = strip_block(rules.read_text(encoding="utf-8")) if rules.is_file() else ""
        rules.parent.mkdir(parents=True, exist_ok=True)
        rules.write_text((existing + "\n\n" if existing else "") + block.strip("\n") + "\n", encoding="utf-8")
        print(f"rule     -> {rules}")

    # 3. the /diagram command
    if not args.no_command:
        command_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(HERE / "extras" / "diagram.md", command_file)
        print(f"command  -> {command_file}  (use it as /diagram <what to draw>)")

    print(f"\nconvert command given to the model:\n  {command} diagrams/<name>.mmd")

    # 4. can this Python actually render?
    import subprocess

    probe = "import playwright.sync_api"
    if subprocess.run([args.python, "-c", probe], capture_output=True).returncode != 0:
        print("\nPlaywright is NOT installed for that Python. Run:\n"
              f"  {quote(args.python)} -m pip install playwright\n"
              f"  {quote(args.python)} -m playwright install chromium")
        return 1
    print("\nPlaywright found. If Chromium was never installed for it, also run:\n"
          f"  {quote(args.python)} -m playwright install chromium\n"
          "Restart opencode to pick up the skill.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
