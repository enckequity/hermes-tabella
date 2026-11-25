"""tabella plugin — registration.

Four document tools in one toolset. The tool table and dispatcher live in
tabella/toolspec.py and are shared with the optional MCP servers, so every
transport serves the same schemas and results.
"""

import logging
import shutil

from .tabella.toolspec import TOOLS, handle_tool

logger = logging.getLogger(__name__)

TOOLSET = "tabella"


def _pandoc_available() -> bool:
    return shutil.which("pandoc") is not None


# Tools that need a system binary are only offered when it is present; every
# other missing dependency is reported by the tool itself in plain words.
_CHECKS = {"doc_convert": _pandoc_available}


def _make_handler(name):
    def handler(args: dict, **kwargs) -> str:
        return handle_tool(name, args or {})

    handler.__name__ = f"{name}_handler"
    return handler


def register(ctx):
    for tool in TOOLS:
        name = tool["name"]
        ctx.register_tool(
            name=name,
            toolset=TOOLSET,
            schema={
                "name": name,
                "description": tool["description"],
                "parameters": tool["inputSchema"],
            },
            handler=_make_handler(name),
            check_fn=_CHECKS.get(name),
            description=tool["description"],
        )
    logger.debug("tabella: registered %s", ", ".join(t["name"] for t in TOOLS))
