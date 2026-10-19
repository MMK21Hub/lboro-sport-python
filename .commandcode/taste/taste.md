# Taste
- Wants Python work built with `uv` (project + dependency management, `uv sync` / `uv run`) rather than pip/poetry/venv-by-hand. Confidence: 0.75
- Wants modern Python: current-version syntax and modern idioms/typing (`X | None`, `from __future__ import annotations`, `datetime.UTC`, dataclass/pydantic models) instead of legacy patterns. Confidence: 0.7
- Prefers real importable packages/modules (src layout, `pyproject.toml`, console-script entry point, thin script wrapper) over one-off single-file scripts. Confidence: 0.65
- Writes short, outcome-focused prompts (e.g. "decompile X", "find out how Y works from an API perspective") and expects the agent to choose the approach and carry the task through end to end without step-by-step supervision. Confidence: 0.55
- Wants to be told which tools/dependencies need installing so they can do it themselves, rather than the agent installing system software unilaterally. Confidence: 0.5
- Emphasises correctness ("make no mistakes") and follows up with "why didn't you do X?" questions; expects scope boundaries and design decisions to be justified explicitly rather than silently chosen. Confidence: 0.5
- Dislikes silent, slow-running tools: wants visible step-by-step progress output (print/status lines) while multi-second commands run, so they can see what the tool is doing and where it's spending time. Confidence: 0.55
- Prefers compact, scannable CLI/terminal output over wide bordered tables: group rows (e.g. by day), drop redundant columns, fold repeated prefixes into a heading, and keep detail behind an opt-in flag (e.g. `--wide`) rather than showing everything by default. Confidence: 0.5
