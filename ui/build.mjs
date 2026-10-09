// Bundles src/gallery.ts and inlines it into src/gallery.html, producing the single-file
// MCP App served by the Python server. The output is committed so users never need Node.
import { build } from "esbuild";
import { readFile, writeFile } from "node:fs/promises";

const OUT = new URL("../plugins/foundry-imagegen/server/src/foundry_imagegen/ui/gallery.html", import.meta.url);

const result = await build({
  entryPoints: ["src/gallery.ts"],
  bundle: true,
  format: "iife",
  target: "es2022",
  minify: true,
  legalComments: "none",
  write: false,
});
const script = result.outputFiles[0].text.replaceAll("</script", "<\\/script");
const template = await readFile(new URL("src/gallery.html", import.meta.url), "utf8");
const css = await readFile(new URL("src/gallery.css", import.meta.url), "utf8");
const html = template.replace("/*STYLE*/", () => css).replace("/*SCRIPT*/", () => script);
await writeFile(OUT, html);
console.log(`wrote ${OUT.pathname} (${(html.length / 1024).toFixed(1)} KB)`);
