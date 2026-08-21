# Nova Development Guide

## Environment

Target:

* CachyOS
* KDE Plasma
* NVIDIA RTX 2070
* 8 GB VRAM
* Ollama

## Development Workflow

Start by checking:

```bash
git status
```

Read:

```text
CLAUDE.md
PROJECT_STATE.md
ARCHITECTURE.md
```

Inspect the current implementation before making changes.

## Tests

The project will use pytest unless another testing framework is approved.

Run tests with:

```bash
pytest
```

## Git

Prefer small logical commits.

Examples:

```text
feat(llm): add ollama provider
feat(ui): add chat interface
feat(agent): add tool registry
test(llm): test streaming
```

## Multi-Session Development

At the end of every session:

1. Run relevant tests.
2. Inspect changes.
3. Update `PROJECT_STATE.md`.
4. Document decisions.
5. Document incomplete work.
6. Document the next recommended task.
7. Commit when appropriate.

## Important

Never assume that `PROJECT_STATE.md` is more authoritative than the actual repository.

When they conflict, inspect the code and git history and correct the state file.
