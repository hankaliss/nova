"""Interactive UI tools — let the LLM present choices to the user in chat.

The implementations are bound to a request handler on the RPC layer, which
emits a `choicesRequested` event and blocks until the user selects an option
(see NovaRpc.request_choices / selectChoice).
"""

from collections.abc import Callable, Awaitable

from nova.tools.tool import RiskLevel, Tool

ChoicesHandler = Callable[[str, list[str]], Awaitable[str]]


def get_tools(request_choices: ChoicesHandler) -> list[Tool]:
    """Return the UI tools, bound to an RPC choices handler."""
    return [
        Tool(
            name="nova.presentChoices",
            description=(
                "Present a multiple-choice question to the user and wait for "
                "their selection. Use whenever the user must pick from a small "
                "set of concrete options before you continue (e.g. choosing "
                "what to do next). Returns the text of the selected option."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "The question to ask the user",
                    },
                    "options": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 2,
                        "maxItems": 9,
                        "description": "2-9 short, mutually exclusive options",
                    },
                },
                "required": ["question", "options"],
            },
            risk=RiskLevel.READ,
            permission="ui.choices",
            implementation=lambda question, options: request_choices(
                question, options
            ),
        )
    ]