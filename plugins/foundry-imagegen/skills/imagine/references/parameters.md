# Parameters for the Foundry GPT image tools

## size
`auto` or `WIDTHxHEIGHT`. Rules for GPT Image 2 / 2.5:
- both edges are multiples of 16; neither edge exceeds 3840 px
- aspect ratio between 1:3 and 3:1
- total pixels between 655,360 and 8,294,400
- anything above 2560x1440 (3,686,400 px) is experimental; larger images take longer and cost more

| Use | Size |
|---|---|
| Square: avatar, icon, social post, logo | 1024x1024 (2048x2048 for print-quality) |
| Landscape photo / illustration (3:2) | 1536x1024 |
| Portrait (2:3), poster, book cover | 1024x1536 |
| 16:9 slide, hero banner, video thumbnail | 2048x1152 (1536x864 for drafts) |
| 9:16 phone wallpaper, story, reel cover | 1152x2048 |
| Ultra-wide banner (3:1) | 2304x768 |
| 4K wallpaper (experimental) | 3840x2160 |

## quality
`auto` (default), `low`, `medium`, `high`, `xhigh`, `max`.
- `low`/`medium`: drafts, exploring compositions, quick variations.
- `high`: finals, small or dense text, faces, product detail.
- `xhigh`/`max`: only when `high` measurably falls short — slower and more expensive, and a higher
  setting does not guarantee a better image for every prompt.
- Labels are not comparable across models (Flare `high` ≠ Sunburst `high`).

## n
1–10 images in **one request**. With a quota of ~5 requests per minute, asking for `n: 4` once is far
better than four separate calls.

## output_format and output_compression
- `png` (default, lossless, supports transparency), `jpeg` (smaller; no alpha). `webp` is accepted by
  the tool but not documented for Azure deployments — fall back to png/jpeg if it errors.
- `output_compression` (0–100) applies to jpeg/webp only.

## background
`auto`, `opaque`, `transparent`. Transparent requires png. Ask for an isolated subject with no
backdrop, floor, or cast shadow in the prompt, and check edges (hair, glass, soft shadows) in the
result.

## deployment
Omit to use the configured default. Typical pairing:
- **gpt-image-2.5-flare** — fastest; great for everyday generation and iteration.
- **gpt-image-2.5-sunburst** — most capable; precision edits, dense text, demanding finals.
Only deployments listed in the plugin/extension settings can be used (`check_config` lists them).

## Rate limit and timing
- gpt-image-2.5 GlobalStandard deployments allow **5 requests per minute**. The server queues calls
  when the limit is reached, shares the quota between all sessions on the machine, and honors the
  service's Retry-After on 429s. If the wait would exceed the configured maximum, the tool returns the
  time when the next slot opens.
- A render typically takes 10–60 s; large or `xhigh`/`max` renders take longer.

## Content filtering
The service's safety system may block a prompt or a generated image (error mentions the safety
system). Rephrase to avoid the flagged content rather than retrying the same prompt.
