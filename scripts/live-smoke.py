"""Live smoke test against a real Foundry deployment (spends a few requests of quota).

Reads settings the same way the server does (FOUNDRY_IMAGEGEN_* env vars, then config.toml).
Run from the repo root:

    cd plugins/foundry-imagegen/server && uv run python ../../../scripts/live-smoke.py [--burst]

Steps: check_config probe → low-quality generation → edit of that output → transparent PNG
(alpha check). With --burst, also fires 7 generations concurrently to exercise the rate limiter.
"""

import argparse
import asyncio
import tempfile
import time
from pathlib import Path

from mcp.client import Client
from PIL import Image

from foundry_imagegen import server


def show(title: str, result) -> dict | None:
    text = next((c.text for c in result.content if c.type == "text"), "")
    print(f"\n=== {title} ({'ERROR' if result.is_error else 'ok'}) ===\n{text}")
    return result.structured_content


async def main(burst: bool) -> None:
    out = Path(tempfile.mkdtemp(prefix="foundry-imagegen-smoke-"))
    async with Client(server.mcp) as client:
        show("check_config", await client.call_tool("check_config", {"probe": True}))

        common = {"size": "1024x1024", "quality": "low", "output_dir": str(out)}
        gen = show(
            "generate",
            await client.call_tool("generate_image", {"prompt": "A red bicycle leaning on a yellow wall, flat illustration", **common}),
        )
        if not gen:
            return
        first = gen["images"][0]["path"]

        show(
            "edit",
            await client.call_tool(
                "edit_image",
                {"prompt": "Change only the bicycle color to blue. Keep everything else the same.", "images": [first], **common},
            ),
        )

        logo = show(
            "transparent",
            await client.call_tool(
                "generate_image",
                {"prompt": "Minimal flat logo mark: a single green leaf, centered, generous padding",
                 "background": "transparent", **common},
            ),
        )
        if logo:
            with Image.open(logo["images"][0]["path"]) as img:
                alpha = img.getchannel("A").getextrema() if "A" in img.getbands() else None
            print(f"alpha channel extrema: {alpha} (expect min 0 for real transparency)")

        if burst:
            started = time.time()
            calls = [
                client.call_tool("generate_image", {"prompt": f"Simple icon of the number {i}", **common})
                for i in range(7)
            ]
            results = await asyncio.gather(*calls)
            for i, result in enumerate(results):
                text = next((c.text for c in result.content if c.type == "text"), "")
                print(f"burst {i}: {'ERROR' if result.is_error else 'ok'} — {text.splitlines()[0]}")
            print(f"burst took {time.time() - started:.0f} s")
    print(f"\nOutputs in {out}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--burst", action="store_true", help="also exercise the rate limiter with 7 concurrent calls")
    asyncio.run(main(parser.parse_args().burst))
