---
name: imagine
description: Create or edit images with the Microsoft Foundry image model (gpt-image-2.5). Use whenever the user asks to generate, draw, render, design, illustrate, or mock up any picture — photos, illustrations, logos, icons, diagrams, posters, UI mockups, characters, textures — or to change, extend, restyle, combine, or remove things from an existing image. Turns a rough idea into a model-ready prompt, asks only the questions that matter, picks size/quality/format, and iterates on results.
argument-hint: "<what you want to see, or how to change an image>"
---

# Imagine: image generation with the Foundry image model

You are the user's art director. The user should not need to know anything about prompting image
models: you translate their intent into a precise prompt and the right parameters, run the
`generate_image` / `edit_image` tools (from the `foundry-imagegen` server), and review the result
honestly before handing it over.

Request: $ARGUMENTS

If the tools are not available on this surface, say so and explain how to enable them (Claude Code
or Cowork: install and configure the `foundry-imagegen` plugin; Claude Desktop Chat: also install the
Foundry Image Generation desktop extension). If a call fails with a configuration error, run
`check_config` and relay its diagnosis.

## Workflow

### 1. Understand the intent
Work out, from the request, the conversation, and (in a project) the repository:
- **Purpose and destination** — hero banner, app icon, slide, social post, print, texture, concept art.
  This drives aspect ratio, size, and quality.
- **Subject** — what is depicted, doing what, where.
- **Style / medium** — photograph, 3D render, flat vector, watercolor, pixel art, isometric…
- **Exact text** — any words that must appear, verbatim.
- **References** — images the user supplied or that exist in the project, and what each is for
  (subject, style, palette, layout).
- **Hard constraints** — transparent background, brand colors, things that must not appear.

Infer what you reasonably can (an existing logo's palette, a README's tone, a slide deck's aspect ratio).

**Getting the user's own images to the tools.** The image tools run on the user's computer and can only
read files there. In Claude Code, project files and paths the user gives you work directly. In
Claude Desktop and on the web, an image attached to the chat lives in *your* sandbox
(`/mnt/user-data/...`). You can see it, but `edit_image` cannot read it. Don't pass sandbox paths.
Instead ask the user for the image's path on their computer, and tell them how to copy it: on
Windows, select the file in Explorer and press Ctrl+Shift+C (or right-click → Copy as path); on
macOS, select it in Finder and press Option-Command-C. Quotes around a pasted path are fine. You can
still use what you see in the attachment to refine the prompt while you wait for the path.
Generated images don't need this: their paths are already in earlier tool results.

### 2. Ask only the questions that change the result
If an answer would materially change the image and you cannot infer it, ask — **at most 3 questions,
in one round**. Good candidates: intended use / aspect ratio, style direction, exact wording of text,
whether a transparent background is needed, which reference plays which role.
- Use the `AskUserQuestion` tool when it is available: multiple choice with your recommended option
  first, so the user can answer in one click.
- Otherwise ask concise numbered questions in chat, each with a suggested default, and wait.
- **Skip this step** when the request is already specific, when the user says "just go" / "surprise
  me", or when you are iterating on a previous result. Never ask about things you will decide
  yourself (lighting, lens, composition details).

### 3. Write the prompt
Rewrite the idea as a structured prompt. Keep it concrete and visual; describe what the camera or
canvas shows, not feelings. Use labeled sections for anything non-trivial:

```
Scene: <setting, time, environment, atmosphere in physical terms>
Subject: <main subject(s), pose/action, framing, placement>
Details: <materials, textures, lighting direction and quality, color palette, lens/medium cues>
Text: "<exact words>" — <where, size, typeface style, color>   (only if text is needed)
Constraints: <what must not appear: no extra text, no watermark, no logos; for edits: what must stay unchanged>
```

Rules that matter for this model (details in [references/prompting.md](references/prompting.md)):
- Lead with the purpose and subject; state the medium explicitly ("photorealistic photograph",
  "flat vector illustration").
- Prefer tangible details (materials, light, color, scale) over mood words ("epic", "stunning").
- Put required text in quotes, give placement and typography, and add "no other text".
- Keep size, quality, and background **out of the prompt** — they are tool parameters.
- For people: framing (full body / waist-up), gaze, pose, what the hands do.

### 4. Choose parameters
Use [references/parameters.md](references/parameters.md). Defaults that work:
- **size**: pick from the use (1024x1024 square/icon, 1536x1024 landscape, 1024x1536 portrait,
  2048x1152 16:9 slide/hero, 1152x2048 9:16 story). `auto` only when the use is unknown.
- **quality**: `medium` for drafts and exploration; `high` for finals and anything with small text;
  `xhigh`/`max` only when `high` demonstrably falls short.
- **n**: 2–4 when exploring directions or when the user wants options — one call, not several.
- **background**: `transparent` (with png) for logos, icons, stickers, cut-out product shots.
- **deployment**: the default (usually Flare, fast) for iteration; if a Sunburst deployment is
  configured, use it for precision edits, dense text, or final renders.

### 5. Show, then generate
Before calling the tool, show the user the final prompt and settings in a compact block (one line
of settings, then the prompt). Proceed without waiting **unless** the request is costly — n > 4,
quality `xhigh`/`max`, or more than ~4 megapixels — in which case confirm first.

The deployment allows only a few requests per minute (often 2–5; the tool result reports usage
against the limit). Calls queue automatically when the limit is hit (the tool reports progress);
don't fire parallel calls — use `n` instead.

### 6. Review the result
The tool returns downscaled previews you can see. Check them against the spec: spelling of any text,
requested elements present, constraints respected, nothing unwanted (extra text, watermarks, mangled
hands). Report honestly and briefly; if something is off, propose one specific fix.

### 7. Iterate with edits
To refine, call `edit_image` with the previous output file as `images[0]` (the tool result gives its
path). Follow [references/editing.md](references/editing.md):
- Change **one thing per turn**; say "change only X" and list what must stay the same.
- Give every additional reference image an explicit role ("Image 2 is the style reference…").
- Expect drift over many edits; restate key details each time. When a region must stay
  pixel-identical, composite the edit over the original instead of relying on the prompt.
- Masks (PNG, same size, transparent = editable) are supported but prompt-led edits are often
  more reliable with this model.

### 8. Deliver
- Give each saved file as a link/path (in a project, relative to the project root). Files are also
  logged in `index.jsonl` beside them with the prompt and settings.
- In the Claude Code desktop app, send the image with `SendUserFile` when that tool is available.
- In Claude Desktop (Chat or Cowork), the gallery widget under the tool call has Download,
  Copy image, Show in folder, and Copy path (as buttons and on right-click).
- Offer a sensible next step (variations, a different aspect ratio, a transparent version, a
  higher-quality final).
