# Editing and composing with reference images

`edit_image` sends one or more images plus a prompt. `images[0]` is the image being edited; any
further images are references. Up to 16 images, PNG/JPEG/WebP, each under 50 MB.

## Core rules
- **Separate the change from the constraints.** "Change only X. Keep everything else the same:
  <identity, pose, layout, lighting, camera angle, background, text>."
- **Assign every reference a role** by number: "Image 1 is the room to edit. Image 2 is the sofa to
  place in it. Image 3 is the color palette reference." Say how they combine and what moves where.
- **One change per turn.** Several changes at once make regressions hard to spot and fix.
- **Restate what to preserve every time.** Repeated edits drift; "same style as before" helps but is
  not enough over many turns.
- **Pixel-exact regions:** prompting cannot guarantee them. If an area must stay identical, composite
  the approved edit back onto the original instead.

## Recipes
- **Style transfer:** "Use the visual style of image 1 (palette, brush texture, line weight) to
  depict <new subject> on a white background."
- **Identity-preserving change (clothing, setting):** "Change only the jacket to a navy wool peacoat.
  Do not change the face, skin tone, hairstyle, body shape, pose, background, or camera angle. Match
  the existing lighting and shadows."
- **Remove an object:** "Remove the coffee cup from the table. Fill the area naturally to match the
  wood grain. Do not change anything else."
- **Replace an object:** "Replace the floor lamp with a tall green plant in a terracotta pot, same
  position and scale, same lighting and shadow direction. Keep all other furniture unchanged."
- **Insert a person/product into a scene:** describe lighting match, scale, gaze, contact shadows,
  and what of the inserted subject must stay identical.
- **Sketch to render:** "Turn this sketch into a photorealistic render. Preserve the layout,
  proportions, and perspective exactly. Add realistic materials and lighting. Do not add new elements
  or text."
- **Translate text:** "Translate all text to German. Keep the layout, fonts, colors, and imagery
  unchanged." Then check for untranslated words.
- **Extend / outpaint:** request the new canvas size via `size` and describe what fills the new area
  while keeping the original content unchanged.
- **Multi-turn refinement:** chain outputs — each new `edit_image` uses the previous result as
  `images[0]` and changes one condition (time of day, weather, color, expression).

## Masks
- A mask is a PNG with an alpha channel, the same pixel size as `images[0]`.
- **Fully transparent pixels (alpha 0) mark the area that may change**; opaque pixels are protected.
- The mask guides the model rather than strictly fencing it — content outside the mask can still
  shift, and some deployments handle masks poorly. Prefer prompt-led edits; use a mask when the
  region is hard to describe in words, and check the result closely.
- To make a mask: copy the image, erase (make transparent) the editable region, save as PNG.

## Transparency in edits
Repeat transparency requirements in every follow-up edit ("keep the background fully transparent")
and keep `background: transparent` with png output.
