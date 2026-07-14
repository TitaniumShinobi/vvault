# VVAULT Capsule Editor Decision

## Decision

Use Monaco for the first `.capsule` editor foundation.

## Evaluation

| Criterion | Monaco | CodeMirror | VVAULT decision |
| --- | --- | --- | --- |
| Editing quality | Mature IDE editor with strong selection, indentation, multi-cursor, and command behavior. | Excellent programmable editor, lighter and flexible. | Monaco gives VVAULT the strongest editor-first baseline immediately. |
| Keyboard behavior | VS Code-like defaults, including common find and undo/redo commands. | Strong defaults, more setup for IDE-like behavior. | Monaco best matches the intended file-product workflow. |
| Search | Built-in find UI through editor actions. | Available through extensions. | Monaco is lower-friction for the first slice. |
| Undo/redo | Built in. | Built in. | Tie; both satisfy the requirement. |
| Dirty state | Easy to derive from loaded text versus current editor text. | Easy to derive from loaded text versus current editor text. | Tie; VVAULT owns dirty state outside the editor. |
| Large document performance | Designed for large code files, with a heavier runtime. | Strong performance and smaller runtime. | Monaco is acceptable for `.capsule` scale; reassess if runtime size becomes the constraint. |
| Extensibility | Language services, custom actions, diagnostics, decorations. | Highly extensible with composable extensions. | Monaco fits future validation and syntax tooling with less initial glue. |
| Bundle/runtime impact | Heavier dependency. | Lighter dependency. | Accepted for this slice because editor fidelity is the product requirement. |
| Integration complexity | Already proven in the sibling `code/` repo with `@monaco-editor/react`. | Would add a second editor convention to the workspace. | Monaco reduces cross-repo divergence. |
| VVAULT UI fit | Dark IDE-like editor works inside the existing Vault panel. | Also viable, but would need separate conventions. | Monaco keeps VVAULT aligned with the existing local IDE direction. |

## Guardrails

- UTF-8 text is the source of truth.
- Parser, validation, anatomy, timeline, inspector, AI, and relationships are derived from current editor text.
- Parse errors must never block open, edit, save, search, undo, redo, or diff.
- Saves write back to the same VVAULT body database file record.
- No AST, visualization, cache, or metadata can become canonical.
