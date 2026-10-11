// Builds the test host around the built gallery.html and runs each scenario in headless Chromium,
// checking the log lines. Usage: npm run build && npm run test:widget  (needs chromium on PATH or CHROMIUM).
import { build } from "esbuild";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const gallery = readFileSync(new URL("../../plugins/foundry-imagegen/server/src/foundry_imagegen/ui/gallery.html", import.meta.url), "utf8");
// 64x48 red PNG.
const SAMPLE_PNG =
  "iVBORw0KGgoAAAANSUhEUgAAAEAAAAAwCAIAAAAuKetIAAAAUklEQVR4nO3PMQ0AIADAMEAN/vUgBhEcDcmqYJtn7/GzpQNeNaA1oDWgNaA1oDWgNaA1oDWgNaA1oDWgNaA1oDWgNaA1oDWgNaA1oDWgNaA1oF1WqwGM9tljIQAAAABJRU5ErkJggg==";
const out = await build({
  entryPoints: [new URL("host.ts", import.meta.url).pathname],
  bundle: true,
  format: "iife",
  write: false,
  define: { GALLERY: JSON.stringify(gallery), SAMPLE_PNG: JSON.stringify(SAMPLE_PNG) },
});
const dir = mkdtempSync(join(tmpdir(), "gallery-test-"));
const page = join(dir, "host.html");
writeFileSync(
  page,
  `<!doctype html><html><body><iframe id="app" sandbox="allow-scripts allow-same-origin" style="width:760px;height:480px"></iframe>` +
    `<pre id="log"></pre><script>${out.outputFiles[0].text.replaceAll("</script", "<\\/script")}</script></body></html>`,
);

const expectations = {
  gallery: ["figures=2 status_hidden=true", "menu=Download|Copy image|Show in folder|Copy path", "call copy_image_to_clipboard", "toast=Image copied"],
  upload: ["panel_visible=true purpose=the photo to restyle", "thumbs=1 send_enabled=true", "call stage_upload request_id=req123 files=holiday.png:true", "upload_status=Sent holiday.png to Claude. locked=true"],
  "upload-cancel": ["panel_visible=true", "call cancel_upload request_id=req123", "upload_status=Upload cancelled. locked=true"],
};

const chromium = process.env.CHROMIUM || "chromium";
let failed = 0;
for (const [scenario, expected] of Object.entries(expectations)) {
  const dom = execFileSync(chromium, ["--headless=new", "--no-sandbox", "--disable-gpu", "--virtual-time-budget=8000", "--dump-dom", `file://${page}#${scenario}`], {
    encoding: "utf8",
    stdio: ["ignore", "pipe", "ignore"],
    maxBuffer: 64 * 1024 * 1024, // the dump includes the widget bundle inside the iframe's srcdoc
  });
  const logText = (dom.match(/<pre id="log">([\s\S]*?)<\/pre>/) ?? [])[1] ?? "";
  const missing = [...expected, "done"].filter((line) => !logText.includes(line));
  console.log(`${missing.length ? "FAIL" : "ok  "} ${scenario}`);
  if (missing.length) {
    failed++;
    console.log("  missing:", missing, "\n  log:\n" + logText.replace(/^/gm, "    "));
  }
}
process.exit(failed ? 1 : 0);
