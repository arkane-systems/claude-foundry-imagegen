import {
  App,
  applyDocumentTheme,
  applyHostFonts,
  applyHostStyleVariables,
  type McpUiHostContext,
} from "@modelcontextprotocol/ext-apps/app-with-deps";

interface ImageInfo {
  path: string;
  file_name: string;
  mime: string;
  width: number;
  height: number;
  bytes: number;
}

interface ResultData {
  operation: string;
  prompt: string;
  deployment: string;
  duration_s: number;
  output_dir: string;
  images: ImageInfo[];
}

type ContentBlock = { type: string; text?: string; data?: string; mimeType?: string };

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;

const app = new App({ name: "foundry-imagegen-gallery", version: "0.1.0" }, { availableDisplayModes: ["inline", "fullscreen"] });

function applyContext(ctx: McpUiHostContext | undefined): void {
  if (!ctx) return;
  if (ctx.theme) applyDocumentTheme(ctx.theme);
  if (ctx.styles?.variables) applyHostStyleVariables(ctx.styles.variables);
  if (ctx.styles?.css?.fonts) applyHostFonts(ctx.styles.css.fonts);
}

function toast(message: string): void {
  const el = $("toast");
  el.textContent = message;
  el.hidden = false;
  window.setTimeout(() => (el.hidden = true), 2500);
}

function showStatus(title: string, detail = ""): void {
  $("status").hidden = false;
  $("status-title").textContent = title;
  $("status-detail").textContent = detail;
}

function showError(message: string): void {
  $("status").hidden = true;
  const el = $("error");
  el.textContent = message;
  el.hidden = false;
}

function formatBytes(bytes: number): string {
  return bytes >= 1_048_576 ? `${(bytes / 1_048_576).toFixed(1)} MB` : `${Math.round(bytes / 1024)} KB`;
}

function textOf(content: ContentBlock[] | undefined): string {
  return (content ?? []).filter((c) => c.type === "text" && c.text).map((c) => c.text).join("\n");
}

async function download(info: ImageInfo, button: HTMLButtonElement): Promise<void> {
  button.disabled = true;
  try {
    const result = await app.callServerTool({ name: "fetch_image", arguments: { path: info.path } });
    const data = (result.structuredContent as { data?: string; mime?: string } | undefined)?.data;
    if (result.isError || !data) throw new Error(textOf(result.content as ContentBlock[]) || "The file could not be read.");
    const { isError } = await app.downloadFile({
      contents: [{ type: "resource", resource: { uri: `file:///${info.file_name}`, mimeType: info.mime, blob: data } }],
    });
    if (isError) toast("Download cancelled");
  } catch (err) {
    toast(`Download failed: ${(err as Error).message}`);
  } finally {
    button.disabled = false;
  }
}

async function reveal(info: ImageInfo): Promise<void> {
  const result = await app.callServerTool({ name: "reveal_image", arguments: { path: info.path } });
  if (result.isError) toast(textOf(result.content as ContentBlock[]) || "Could not open the folder");
}

async function copyPath(info: ImageInfo): Promise<void> {
  try {
    await navigator.clipboard.writeText(info.path);
    toast("Path copied");
  } catch {
    window.prompt("Copy the file path:", info.path);
  }
}

function button(label: string, onClick: (btn: HTMLButtonElement) => void): HTMLButtonElement {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "action";
  btn.textContent = label;
  btn.addEventListener("click", () => onClick(btn));
  return btn;
}

async function toggleFullscreen(): Promise<void> {
  const ctx = app.getHostContext();
  if (!ctx?.availableDisplayModes?.includes("fullscreen")) return;
  const next = ctx.displayMode === "fullscreen" ? "inline" : "fullscreen";
  await app.requestDisplayMode({ mode: next });
}

function render(data: ResultData, previews: ContentBlock[]): void {
  $("status").hidden = true;
  $("error").hidden = true;
  $("result").hidden = false;
  $("prompt").textContent = data.prompt;
  $("details").textContent =
    `${data.deployment} · ${data.images.length} image${data.images.length === 1 ? "" : "s"} · ` +
    `${Math.round(data.duration_s)} s · saved to ${data.output_dir}`;

  const canDownload = Boolean(app.getHostCapabilities()?.downloadFile);
  const canFullscreen = Boolean(app.getHostContext()?.availableDisplayModes?.includes("fullscreen"));
  const grid = $("grid");
  grid.className = data.images.length === 1 ? "grid single" : "grid";
  grid.replaceChildren(
    ...data.images.map((info, i) => {
      const figure = document.createElement("figure");
      const frame = document.createElement("button");
      frame.type = "button";
      frame.className = "frame";
      frame.title = canFullscreen ? "Toggle full screen" : info.file_name;
      frame.addEventListener("click", () => void toggleFullscreen());
      const img = document.createElement("img");
      const preview = previews[i];
      if (preview?.data) img.src = `data:${preview.mimeType ?? "image/png"};base64,${preview.data}`;
      img.alt = `Generated image ${i + 1}: ${data.prompt.slice(0, 120)}`;
      frame.append(img);

      const caption = document.createElement("figcaption");
      const name = document.createElement("span");
      name.className = "name";
      name.textContent = `${info.file_name} — ${info.width}×${info.height}, ${formatBytes(info.bytes)}`;
      caption.append(name);
      if (canDownload) caption.append(button("Download", (btn) => void download(info, btn)));
      caption.append(button("Show in folder", () => void reveal(info)));
      caption.append(button("Copy path", () => void copyPath(info)));
      figure.append(frame, caption);
      return figure;
    }),
  );
}

app.onhostcontextchanged = (ctx) => applyContext(ctx as McpUiHostContext);

app.ontoolinput = (params) => {
  const args = (params.arguments ?? {}) as { prompt?: string; n?: number; size?: string; quality?: string };
  const what = [args.n && args.n > 1 ? `${args.n} images` : "1 image", args.size, args.quality]
    .filter((x) => x && x !== "auto")
    .join(" · ");
  showStatus(`Generating ${what}…`, args.prompt ?? "");
};

app.ontoolcancelled = () => showError("The request was cancelled.");

app.ontoolresult = (result) => {
  const content = (result.content ?? []) as ContentBlock[];
  const data = result.structuredContent as ResultData | undefined;
  if (result.isError || !data?.images?.length) {
    showError(textOf(content) || "The image request failed.");
    return;
  }
  render(data, content.filter((c) => c.type === "image"));
};

app
  .connect()
  .then(() => applyContext(app.getHostContext()))
  .catch((err: unknown) => showError(`Could not connect to the host: ${String(err)}`));
