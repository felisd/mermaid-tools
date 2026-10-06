---
name: mermaid-diagram
description: Draw a diagram as a PNG image. Use this skill EVERY time the user asks to draw, sketch, diagram, visualize, chart, map out or illustrate something Mermaid can draw - flowchart, process or workflow, sequence diagram, class diagram, state machine, ER or database diagram, Gantt chart, pie chart, mind map, timeline, user journey, git graph, quadrant chart, bar or line chart, sankey, requirement diagram, C4 or architecture diagram, kanban board, block diagram, packet layout. The skill writes a Mermaid .mmd file and converts it to PNG with a command. Never answer a diagram request with ASCII art or with Mermaid code alone.
---

# Diagram request = .mmd file + PNG file

A diagram request is finished only when a PNG file exists. Always do all three steps, in this order.

## Step 1 - Write the Mermaid file

Write the diagram to `diagrams/<short-name>.mmd` (lowercase name with hyphens).
The file holds Mermaid code only: no Markdown code fences, no explanation text.

## Step 2 - Convert it to PNG

Run this command:

```
python ~/.config/opencode/skills/mermaid-diagram/scripts/mermaid2png.py diagrams/<short-name>.mmd
```

It writes `diagrams/<short-name>.png` next to the source and prints one line per file:

- `ok ...` - the PNG was written. Go to step 3.
- `FAIL ... syntax error` - Mermaid rejected the file. The message gives the line number. Fix the `.mmd` file and run the same command again. Repeat until it prints `ok`.
- `error: ...` (Playwright or browser missing) - stop. Show the message to the user. Do not retry.

## Step 3 - Answer the user

Reply with the PNG path, the `.mmd` path and one sentence saying what the diagram shows.
Do not paste the Mermaid code unless the user asks for it.

## Rules

- Never reply with only Mermaid code, ASCII art or a text description of the diagram.
- Never say the diagram is done before the command has printed `ok` for it.
- To change a diagram, edit its `.mmd` file and run the command again. The PNG is overwritten.
- Several diagrams: one `.mmd` file each. All files can go in one command.
- If Mermaid cannot draw what was asked (circuit schematic, map, freehand picture, scientific plot), say so instead of forcing it.

## Options (only when the user asks)

| Need | Add to the command |
|---|---|
| Theme: default, neutral, dark, forest, base | `-t dark` |
| Transparent background | `-b transparent` |
| Sharper image (default is 2) | `-s 3` |
| Exact width in pixels | `-w 1920` |
| Different output file | `-o path/name.png` |
