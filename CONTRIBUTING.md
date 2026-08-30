# Contributing to Shorts Clipper

## Design principles

1. **Local-first and deterministic.** Core logic (segment selection, scoring) runs locally without LLM calls. Gemini is used only for metadata generation and semantic validation.
2. **Decoupled architecture.** Scout, Editorial Engine, Rendering, and Publishing are independent domains. Changes to one should not break another.
3. **Resilience.** Handle network failures, quota exhaustion, and API timeouts gracefully. Never let a single failed candidate block the pipeline.

## Development setup

```bash
git clone https://github.com/random-or/shorts-clipper.git
cd shorts-clipper
python -m venv env
source env/bin/activate
pip install -e ".[dev]"
```

## Running tests

```bash
# Unit tests + benchmarks
python -m pytest tests/ -v

# Lint
ruff check . && ruff format --check .
```

All tests must pass and linting must be clean before submitting a PR.

## Submitting a pull request

1. Fork the repo and create a branch from `main`.
2. Make your changes. Add tests for new functionality.
3. Run the full test suite and linter.
4. Submit a PR with a clear description of what changed and why.
