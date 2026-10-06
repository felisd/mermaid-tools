# mermaid-diagram skill for opencode

Makes the model answer every diagram request by writing a Mermaid `.mmd` file
and converting it to PNG, instead of replying with ASCII art or bare code.

## Install

From the repo root, with the Python that has Playwright:

    python opencode-skill/install.py              # global, all projects
    python opencode-skill/install.py --project    # only the project in the current folder

Restart opencode afterwards. `--uninstall` removes everything again and leaves
the rest of your `AGENTS.md` alone.

## What gets installed

| File | Purpose |
|---|---|
| `skills/mermaid-diagram/SKILL.md` | The three-step procedure: write `.mmd`, run converter, report the PNG |
| `skills/mermaid-diagram/scripts/` | Copies of `mermaid2png.py` and `mermaid.min.js` from this repo |
| `AGENTS.md` (a marked block is appended) | Always in the model's context. Tells it to load the skill for any diagram request, and repeats the command as a fallback |
| `commands/diagram.md` | `/diagram <what to draw>` for triggering it yourself |

## How the forcing works

A skill on its own is optional: the model sees its name and description and
decides whether to load it. Small models often skip that step, so there are
three layers:

1. The skill description lists the trigger words and diagram types.
2. The `AGENTS.md` rule is loaded into every conversation and says to load the
   skill first. It also contains the convert command, so the model can still
   do the job if it never calls the skill tool.
3. `/diagram` puts the instruction directly in the prompt.

Skip layer 2 with `--no-rules` if you do not want `AGENTS.md` touched.

## Notes

- Diagrams are written to `diagrams/<name>.mmd` and `diagrams/<name>.png` in
  the project opencode is running in.
- A global install puts the script outside the project. If opencode asks for
  permission to run it, allow it, or use `--project`.
- The installed copies do not update themselves: run `install.py` again after
  changing `mermaid2png.py` or `vendor/mermaid.min.js`.
