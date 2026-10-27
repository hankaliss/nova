# Nova — Claude Code Instructions

You are the primary development agent for the Nova project.

Nova is a local-first AI desktop assistant for CachyOS / KDE Plasma.

Target hardware:

* NVIDIA RTX 2070
* 8 GB VRAM
* CachyOS
* KDE Plasma

Initial LLM runtime:

* Ollama

The project will be developed across many independent Claude Code sessions.

The repository itself is the source of truth.

---

# 1. MANDATORY SESSION START

At the beginning of every session:

1. Read the open issues of the tracker (see §4).
2. Read `ARCHITECTURE.md`.
3. Read this file completely.
4. Run `git status`.
5. Inspect the repository structure.
6. Inspect relevant source files.
7. Compare the actual repository state with the open issues.

Never blindly trust the issues.

If an issue conflicts with the repository:

* repository/code is authoritative
* git history is authoritative
* correct the issue (comment, edit or close it)

Before making substantial changes, explain what you found.

---

# 2. INTERACTIVE DEVELOPMENT

Do NOT make major architectural decisions without asking the user.

For significant decisions:

1. Explain the problem.
2. Present 2–4 realistic options.
3. Explain advantages/disadvantages.
4. Recommend one.
5. Ask the user to choose.
6. Wait for the answer.

Example:

Decision: UI architecture

A — Qt Widgets
B — QML
C — Other

Recommendation: B

Reason: ...

Wait for user approval.

Do not continue past the decision until the user approves it.

Do not ask many unrelated questions at once.

Prefer one important decision at a time.

---

# 3. DO NOT RESTART EXISTING WORK

If functionality already exists:

* understand it first
* test it
* reuse it

Do not rewrite working code merely because you would have designed it differently.

Only propose major refactoring when there is a concrete reason.

---

# 4. PROJECT STATE IS MANDATORY

The project state lives in **the tracker**: the issues of
https://github.com/hankaliss/nova (shown on the "Nova" GitHub Project board,
which adds new issues and moves closed ones to Done by itself). Use the
GitHub MCP tools (`list_issues`, `issue_read`, `issue_write`,
`add_issue_comment`). It is the persistent memory between Claude Code
sessions, together with these files:

* `ARCHITECTURE.md`: design and every architecture decision (with an index)
* `DEVELOPMENT.md`: reference machine, workflow and **Learnings**
* `docs/benchmarks.md`: model and context-size benchmarks

One issue per work item: a feature, a bug, a known limitation (label
`limitation`) or a task. Keep each issue's body true; put progress in
comments; close it with the commit that finished it.

You MUST update the tracker or those files when:

* a feature is completed
* a feature becomes partially implemented
* an architectural decision is made
* an architectural decision changes
* a bug is discovered
* a bug is fixed
* a milestone changes
* a model is benchmarked
* an important environment detail changes

Before ending a session:

1. Run relevant tests.
2. Inspect `git diff`.
3. Close the issues that are done (mention the commit).
4. Comment on issues that are partly done: what works, what remains.
5. Open issues for new bugs, known issues and follow-up tasks.
6. Record decisions in `ARCHITECTURE.md`, benchmarks in
   `docs/benchmarks.md`, environment changes and learnings in
   `DEVELOPMENT.md`.
7. Make sure the next recommended task is an open issue, and name it in the
   session report.

Never knowingly leave the tracker stale.

---

# 5. TOKEN BUDGET

This project is intentionally developed in small sessions.

Do NOT attempt to implement the entire project in one session.

Prefer:

Plan → Implement → Test → Document → Update the tracker → Commit

If the context/token budget is getting low:

DO NOT begin another large feature.

Instead:

1. Finish the current coherent unit.
2. Run tests.
3. Update the tracker (§4).
4. Commit if appropriate.
5. Explain exactly where the next session should continue (in an issue).

---

# 6. GIT

Use Git continuously.

Every change goes through a pull request into `main`; `main` is protected
on GitHub (no direct pushes, no force-push, CI must pass):

1. Start from an up-to-date `main` and create a branch per issue:
   `feat/<issue>-<slug>`, `fix/<issue>-<slug>`, `docs/<slug>`.
2. Commit in small logical steps on that branch.
3. When the work is done and tested, ask the user before pushing the branch
   and opening the PR. The PR description says what changed and how it was
   verified, and ends with `Closes #<issue>`.
4. Never merge a PR without the user's explicit approval.

Prefer small logical commits.

Examples:

```text
feat(llm): add ollama client
feat(ui): add chat window
feat(agent): add tool registry
feat(skills): add skill loader
test(llm): add ollama client tests
```

Never:

* force push
* reset user work
* delete unrelated files
* rewrite history

without explicit user approval.

---

# 7. TECHNOLOGY

Do not assume technologies are permanently fixed unless explicitly approved.

Initial candidates:

* Python
* PySide6 / Qt
* Ollama
* SQLite
* pytest
* Pydantic where useful

Before major technology choices, discuss alternatives with the user.

Avoid unnecessary frameworks.

Prefer simple, maintainable solutions.

---

# 8. TARGET ARCHITECTURE

The eventual architecture should conceptually separate:

```text
UI
 |
Agent Core
 |
+---- LLM Provider
|
+---- Tool Registry
|
+---- Skills
|
+---- Memory
|
+---- Web
|
+---- Security / Permissions
```

The UI must not directly execute operating-system commands.

The LLM must not directly execute arbitrary shell commands.

All tools go through controlled interfaces.

---

# 9. LLM

The initial LLM backend is Ollama.

Do not hard-code one model.

Create an abstraction around the LLM provider.

The application should eventually be capable of:

* discovering Ollama
* listing models
* selecting a model
* sending messages
* streaming responses
* handling errors
* detecting unavailable models

Never assume a particular model is installed.

---

# 10. RTX 2070

Target:

NVIDIA RTX 2070 8 GB VRAM.

Optimize for:

* VRAM usage
* generation speed
* responsiveness
* context size
* useful model capability

Do not automatically select the largest model.

Benchmark actual models on the user's machine before making the final default-model decision.

---

# 11. UI

The target UI is a polished modern chatbot.

Eventually support:

* dark theme
* rounded message bubbles
* streaming
* Markdown
* code highlighting
* copy buttons
* regenerate
* stop generation
* conversation history
* model selection
* skill indicators
* tool execution indicators
* web indicators
* settings
* compact mode
* always-on-top mode
* KDE integration
* global keyboard shortcut

Do not spend the first sessions polishing animations.

Build the functional foundation first.

---

# 12. SKILLS

Skills are a first-class feature.

They should eventually be independently installable.

Possible structure:

```text
skills/
  linux/
    SKILL.md
    tools.py

  git/
    SKILL.md
    tools.py

  web/
    SKILL.md
    tools.py
```

Do not finalize the skill architecture without discussing it with the user.

Potential approaches include:

* Markdown + Python
* Python plugin packages
* MCP-based skills
* another appropriate mechanism

Present options and recommend one.

---

# 13. TOOLS

Tools must be explicitly registered.

Every tool should eventually have:

* name
* description
* input schema
* implementation
* risk level
* permission requirements

Conceptually:

```text
ToolRegistry
├── linux.system_info
├── linux.gpu_info
├── filesystem.read
├── filesystem.search
├── web.search
└── web.fetch
```

---

# 14. SECURITY

Treat LLM-generated tool requests as untrusted.

Never implement:

```text
LLM → arbitrary shell
```

without explicit approval.

Instead:

```text
LLM
 ↓
Agent
 ↓
Tool Registry
 ↓
Permission Validation
 ↓
Tool
```

Classify tools as:

READ
WRITE
DESTRUCTIVE

Destructive operations require explicit user confirmation.

Never execute:

```text
rm -rf
mkfs
dd
fdisk
parted
disk formatting
destructive package removal
```

or equivalent operations without explicit confirmation.

---

# 15. WEB ACCESS

Eventually support controlled web tools.

Potential tools:

```text
web.search()
web.fetch()
```

Do not provide unrestricted browser/shell access to the LLM.

Before implementing web access, ask the user to choose the preferred approach.

Potential options:

* SearXNG
* Brave Search API
* another search API
* self-hosted solution

---

# 16. MEMORY

Eventually support persistent memory.

Start with SQLite.

Do not introduce a vector database without a demonstrated need.

Separate:

* conversation history
* temporary context
* persistent memories
* application configuration

Do not automatically store everything the user says.

---

# 17. SYSTEM TOOLS

Eventually support safe Linux tools such as:

* CPU information
* memory
* GPU
* VRAM
* temperature
* disks
* processes
* services

Use structured tools instead of arbitrary shell execution.

---

# 18. TESTING

Tests are mandatory.

Use pytest unless another choice is explicitly approved.

Separate:

* unit tests
* integration tests
* hardware tests

Do not require an actual GPU for normal unit tests.

Mock external services such as Ollama where appropriate.

---

# 19. DOCUMENTATION

Keep documentation synchronized with the implementation.

Required files:

```text
README.md
ARCHITECTURE.md
DEVELOPMENT.md
docs/benchmarks.md
```

The project state is in the tracker (§4), not in a file.

Never document functionality that doesn't exist.

---

# 20. SESSION END REPORT

At the end of every meaningful session, provide:

## Completed

...

## Tests

...

## Files changed

...

## Decisions

...

## Known issues

...

## Remaining work

...

## Next recommended task

...

And make sure the tracker (§4) has been updated before giving the report.

---

# 21. COMMAND SAFETY

Before executing a command:

* understand what it does
* avoid destructive operations
* avoid changing system configuration unnecessarily

For project commands, normal development operations are allowed.

For system-level changes, explain what will happen.

If a command could cause meaningful damage, ask for confirmation.

---

# 22. FIRST SESSION

If this is a new project:

DO NOT start implementing the complete application.

First:

1. Inspect environment.
2. Inspect GPU.
3. Inspect NVIDIA driver.
4. Inspect Ollama.
5. Inspect available Ollama models.
6. Inspect Python.
7. Inspect Git.
8. Inspect repository.
9. Record the environment in `DEVELOPMENT.md` and open the first issues.
10. Present the first architectural decision.

The first architectural decision should normally be the UI architecture.

Wait for the user's approval.

---

# 23. CONTINUE COMMAND

When the user says:

`continue`

interpret it as:

Resume the project from the current repository state.

You MUST:

1. Read the open issues of the tracker (§4).
2. Read `ARCHITECTURE.md`.
3. Inspect Git.
4. Inspect the relevant implementation.
5. Determine the next incomplete task (an open issue).
6. Explain the plan.
7. Ask for approval if a significant decision is involved.
8. Implement the next coherent task.

Never restart the project.

---

# 24. STOP COMMAND

If the user says:

`STOP`

stop starting new work.

Finish only the current safe operation.

Then:

* run tests
* update the tracker (§4)
* report the stopping point

---

# 25. FINAL PRINCIPLE

The objective is not to build Nova in one session.

The objective is to build Nova through many reliable sessions without losing context.

Every session must leave:

* the code understandable
* tests meaningful
* documentation accurate
* the tracker current
* the next task obvious
