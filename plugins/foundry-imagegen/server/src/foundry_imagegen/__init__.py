"""Microsoft Foundry image generation as an MCP server for Claude."""

__version__ = "0.1.4"


def main() -> None:
    from .server import run

    run()
