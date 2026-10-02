"""A small MCP server for tests and demos: arithmetic and city facts.

python -m easychain.testing.mcp_server                 # stdio
python -m easychain.testing.mcp_server --http 8766     # streamable HTTP at /mcp
"""

from __future__ import annotations

import argparse

from mcp.server.fastmcp import FastMCP

CITIES = {
    "lisbon": "Lisbon is the capital of Portugal and sits on the Tagus river.",
    "berlin": "Berlin is the capital of Germany and has about 3.7 million people.",
    "nairobi": "Nairobi is the capital of Kenya, next to a national park.",
}


def build(port: int = 8766) -> FastMCP:
    server = FastMCP("Easy Chain test tools", port=port, log_level="WARNING")

    @server.tool()
    def add(a: float, b: float) -> float:
        """Add two numbers."""
        return a + b

    @server.tool()
    def city_facts(city: str) -> str:
        """Facts about a city: its country and something to know."""
        return CITIES.get(city.strip().lower(), f"I don't know {city}.")

    return server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--http", type=int, default=None, help="serve over HTTP on this port")
    args = parser.parse_args()
    if args.http:
        build(args.http).run(transport="streamable-http")
    else:
        build().run(transport="stdio")


if __name__ == "__main__":
    main()
