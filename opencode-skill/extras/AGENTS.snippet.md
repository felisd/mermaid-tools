<!-- mermaid-diagram:start -->
## Diagrams

When the user asks to draw, sketch, diagram, visualize or chart something (flowchart, sequence, class, state, ER, Gantt, pie, mind map, timeline, git graph, architecture and similar):

1. Load the skill first: call the `skill` tool with name `mermaid-diagram`, then follow it exactly.
2. If the skill cannot be loaded, do this instead: write the Mermaid code to `diagrams/<short-name>.mmd`, then run
   `python ~/.config/opencode/skills/mermaid-diagram/scripts/mermaid2png.py diagrams/<short-name>.mmd`
   and fix the file and rerun until the command prints `ok`.

Never answer a diagram request with ASCII art or with Mermaid code alone. The answer must name the PNG file that was created.
<!-- mermaid-diagram:end -->
