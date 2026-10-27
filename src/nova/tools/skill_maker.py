"""Skill-maker tools for Nova.

Lets the LLM create, iterate on, and delete Nova skills from conversation.
The engine (file writing, module loading, registration) lives in
``SkillLoader``; this module only wires it up as Tool objects so the usual
permission rules gate every operation.

Tools:

* ``nova.skill.create``  (DESTRUCTIVE) — write a new skill and register it
* ``nova.skill.update``  (DESTRUCTIVE) — overwrite files of an existing skill
* ``nova.skill.delete``  (DESTRUCTIVE) — remove a skill entirely
* ``nova.skill.get``     (READ)        — read an existing skill's files

create/update write Python that is loaded into Nova's process immediately,
which is arbitrary code execution — so they are DESTRUCTIVE: the user confirms
every call (no auto-approve, no "always allow").
"""

import json

from nova.skills.loader import SkillLoader
from nova.tools.tool import RiskLevel, Tool


async def _smoke_read_tools(loader: SkillLoader, name: str) -> list[str]:
    """Run READ tools with empty args and return any failures.

    Lets the LLM see a broken tool immediately and fix it within the same
    turn. WRITE/DESTRUCTIVE tools are never smoke-tested.
    """
    problems: list[str] = []
    if loader.registry is None:
        return problems
    skill = next((s for s in loader.discover() if s.name == name), None)
    if skill is None:
        return problems
    for tool_name in skill.tool_names:
        try:
            tool = loader.registry.get(tool_name)
        except KeyError:
            problems.append(f"{tool_name}: not registered")
            continue
        if tool.risk != RiskLevel.READ:
            continue
        try:
            result = await loader.registry.execute(tool_name, {})
            if not result.success:
                problems.append(f"{tool_name}: {result.error}")
        except Exception as e:
            problems.append(f"{tool_name}: {type(e).__name__}: {e}")
    return problems


async def _create(
    loader: SkillLoader, store, name: str, skill_md: str, tools_py: str
) -> str:
    count, desc = loader.create_skill(name, skill_md, tools_py)
    summary = (
        f"Created skill '{name}': {desc!r} — {count} tool(s) registered. "
        "The skill is enabled and appears in the Skills menu."
    )
    problems = await _smoke_read_tools(loader, name)
    if problems:
        summary += (
            " SMOKE TEST FAILED — the created tools have bugs and must be "
            "fixed: " + "; ".join(problems)
        )
    return summary


async def _update(
    loader: SkillLoader, name: str, skill_md: str, tools_py: str
) -> str:
    count = loader.update_skill(
        name,
        skill_md if skill_md.strip() else None,
        tools_py if tools_py.strip() else None,
    )
    summary = (
        f"Updated skill '{name}' — {count} tool(s) registered. "
        "The changes are live."
    )
    problems = await _smoke_read_tools(loader, name)
    if problems:
        summary += (
            " SMOKE TEST FAILED — the updated tools have bugs and must be "
            "fixed: " + "; ".join(problems)
        )
    return summary


def _delete(loader: SkillLoader, store, name: str) -> str:
    removed = loader.delete_skill(name)
    if store is not None:
        store.delete_setting(f"skill_enabled_{name}")
    summary = f"Deleted skill '{name}' — removed {removed} tool(s)."
    return summary


def _get(loader: SkillLoader, name: str) -> str:
    content = loader.get_skill_content(name)
    if content is None:
        raise ValueError(f"Skill '{name}' not found")
    return json.dumps(content, indent=2)


def build(loader: SkillLoader, store=None) -> list[Tool]:
    """Build the skill-maker tools bound to a SkillLoader + store."""
    return [
        Tool(
            name="nova.skill.create",
            description=(
                "Create a new Nova skill and register its tools. Params: "
                "name (lowercase, underscores), skill_md (the SKILL.md content "
                "starting with a '# ' heading), tools_py (Python defining "
                "get_tools() returning Tool instances). Show the code to the "
                "user and get approval before calling this. The skill appears "
                "in the Skills menu and can be toggled like any other skill."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Skill name: lowercase letters, digits, underscores.",
                    },
                    "skill_md": {
                        "type": "string",
                        "description": "Full SKILL.md content, first line a '# ' heading.",
                    },
                    "tools_py": {
                        "type": "string",
                        "description": "Full tools.py content defining get_tools().",
                    },
                },
                "required": ["name", "skill_md", "tools_py"],
            },
            risk=RiskLevel.DESTRUCTIVE,
            permission="skill_maker.create",
            implementation=lambda name, skill_md, tools_py: _create(
                loader, store, name, skill_md, tools_py
            ),
        ),
        Tool(
            name="nova.skill.update",
            description=(
                "Update an existing Nova skill. Pass only the files that "
                "changed: non-empty skill_md and/or tools_py (empty strings "
                "are ignored). Reads the current files first via nova.skill.get, "
                "shows the user what will change, and gets approval before calling."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "skill_md": {
                        "type": "string",
                        "description": "New SKILL.md content, or '' to keep current.",
                    },
                    "tools_py": {
                        "type": "string",
                        "description": "New tools.py content, or '' to keep current.",
                    },
                },
                "required": ["name", "skill_md", "tools_py"],
            },
            risk=RiskLevel.DESTRUCTIVE,
            permission="skill_maker.update",
            implementation=lambda name, skill_md, tools_py: _update(
                loader, name, skill_md, tools_py
            ),
        ),
        Tool(
            name="nova.skill.delete",
            description=(
                "Delete a Nova skill permanently (removes its directory and "
                "unregisters its tools). Requires explicit user confirmation; "
                "only call after the user clearly asks."
            ),
            parameters={
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
            risk=RiskLevel.DESTRUCTIVE,
            permission="skill_maker.delete",
            implementation=lambda name: _delete(loader, store, name),
        ),
        Tool(
            name="nova.skill.get",
            description=(
                "Read an existing skill's files (SKILL.md and tools.py) as "
                "JSON so you can propose changes. Call this before updating "
                "a skill."
            ),
            parameters={
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
            risk=RiskLevel.READ,
            permission="skill_maker.get",
            implementation=lambda name: _get(loader, name),
        ),
    ]


def get_tools(loader: SkillLoader, store=None) -> list[Tool]:
    """Return the skill-maker tools (alias for backward compatibility)."""
    return build(loader, store)