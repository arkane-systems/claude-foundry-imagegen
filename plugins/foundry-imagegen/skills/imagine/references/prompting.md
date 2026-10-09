# Prompting the GPT Image 2.5 models

Condensed from OpenAI's GPT Image 2.5 prompting guide and Microsoft Foundry's image-generation docs.

## Principles
1. **Define the result first.** Name the subject, the intended use, the composition, and placement
   constraints. For complex requests, use labeled sections (Scene / Subject / Details / Text /
   Constraints). No special syntax is needed — prose, sections, or tag lists all work; pick what is
   easiest to revise.
2. **Describe visible details.** Materials, textures, lighting (direction, softness, color
   temperature), color palette, scale, medium. Say "photorealistic photograph" or "real photograph"
   when you want photography. Camera terms (35mm, shallow depth of field, f/1.8) are appearance cues,
   not physics. Replace mood words with what creates the mood: "low warm side light, long shadows,
   haze" instead of "moody".
3. **People and action.** Framing (full body visible / head-and-shoulders), gaze direction, pose,
   what the hands hold and how. For groups, count and position people explicitly.
4. **Text in images.** Put required copy in quotes, state where it goes, its size relative to the
   canvas, and the typography (e.g. "bold geometric sans-serif, white"). Spell unusual names letter
   by letter if they keep failing. Add "no other text, no watermarks". Use `high` quality for small
   or dense text, and always proofread the result.
5. **Constraints and exclusions.** List what must not appear in a separate Constraints line.
6. **Parameters stay out of the prompt.** Size, quality, background, and format are API parameters.
7. **Iterate in small steps.** Change one thing at a time and restate what to keep.

## Patterns by request type

**Photograph** — subject + setting + light + lens/framing + realism cues.
> Photorealistic photograph of an elderly fisherman mending a net on a wooden pier at dawn. Waist-up,
> three-quarter view, eyes on his hands. Weathered skin, wool sweater with frayed cuffs. Low golden
> side light from the left, light mist over the water, shallow depth of field. Natural color, no heavy
> retouching. No text.

**Illustration / concept art** — medium and style first, then subject and palette.
> Flat vector illustration in a mid-century travel-poster style: a red cable car climbing a steep
> green hill above a bay. Limited palette of 5 colors (cream, teal, coral, navy, mustard), clean
> geometric shapes, subtle grain texture. Generous sky area at the top. No text.

**Logo / icon** — shapes and silhouette, simplicity, padding; set `background: transparent`.
> Minimal logo mark for "Northwind": a stylized compass rose formed by four overlapping leaf shapes,
> single color deep teal, flat, no gradients, centered with generous padding, crisp edges. No text.
> (Generate 3–4 variants with n.)

**Diagram / infographic** — name the process, the audience, and every component and label; ask for
a clean layout. Verify the labels and the factual relationships, not just the looks.

**Slides, charts, UI mockups** — treat the prompt as a spec: canvas, hierarchy, the real text and
numbers, visual language. Say "shipped product screenshot" rather than "concept" for UI.
Use `high` quality for small text, legends, and footnotes.

**Comics / sequences** — one concrete, action-focused beat per panel; define characters once and
repeat their defining traits in every panel.

**Character consistency** — first create a reference image of the character with defining details
(outfit, proportions, palette, expression). Then use it as `images[0]` / a reference in later
`edit_image` calls, repeating the constraints: "Do not redesign the character."

**Historical or technical accuracy** — name the place and date; check clothing, objects, and staging
in the result.

## Common failure modes
- Dense or tiny text, multiple fonts, exact labels → proofread every output; raise quality.
- Vague style words → generic results; name a medium and concrete visual traits.
- Too many requirements in one prompt → prioritize; move secondary changes to follow-up edits.
- A drawn checkerboard is not transparency — use `background: transparent` and check the alpha.
