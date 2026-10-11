import {
  App,
  applyDocumentTheme,
  applyHostFonts,
  applyHostStyleVariables,
  type McpUiHostContext,
} from "@modelcontextprotocol/ext-apps/app-with-deps";
import { UploadPanel, type UploadRequestInfo } from "./upload";

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

const app = new App({ name: "foundry-imagegen-gallery", version: "0.1.3" }, { availableDisplayModes: ["inline", "fullscreen"] });

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

/** Copy text: async Clipboard API, then execCommand, then an inline field the user can copy from. */
async function copyText(text: string, anchor: HTMLElement): Promise<void> {
  try {
    await navigator.clipboard.writeText(text);
    toast("Path copied");
    return;
  } catch {
    // Clipboard API not permitted in this frame; fall through.
  }
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.style.position = "fixed";
  area.style.opacity = "0";
  document.body.append(area);
  area.select();
  let copied = false;
  try {
    copied = document.execCommand("copy");
  } catch {
    copied = false;
  }
  area.remove();
  if (copied) {
    toast("Path copied");
    return;
  }
  showManualCopy(text, anchor);
}

function showManualCopy(text: string, anchor: HTMLElement): void {
  const figure = anchor.closest("figure") ?? $("result");
  figure.querySelector(".manual-copy")?.remove();
  const field = document.createElement("input");
  field.className = "manual-copy";
  field.readOnly = true;
  field.value = text;
  field.setAttribute("aria-label", "File path — press Ctrl+C to copy");
  figure.append(field);
  field.focus();
  field.select();
  toast("Press Ctrl+C (⌘C) to copy the selected path");
}

async function pngBlob(info: ImageInfo): Promise<Blob> {
  const result = await app.callServerTool({ name: "fetch_image", arguments: { path: info.path } });
  const data = (result.structuredContent as { data?: string } | undefined)?.data;
  if (result.isError || !data) throw new Error(textOf(result.content as ContentBlock[]) || "The file could not be read.");
  const bytes = Uint8Array.from(atob(data), (c) => c.charCodeAt(0));
  const blob = new Blob([bytes], { type: info.mime });
  if (info.mime === "image/png") return blob;
  // The clipboard reliably accepts only PNG; re-encode JPEG/WebP through a canvas.
  const bitmap = await createImageBitmap(blob);
  const canvas = document.createElement("canvas");
  canvas.width = bitmap.width;
  canvas.height = bitmap.height;
  canvas.getContext("2d")!.drawImage(bitmap, 0, 0);
  return new Promise((resolve, reject) =>
    canvas.toBlob((png) => (png ? resolve(png) : reject(new Error("PNG conversion failed"))), "image/png"),
  );
}

/** Ask the server (a normal local process) to use the OS clipboard; sandboxed frames often can't. */
async function serverCopy(info: ImageInfo, content: "image" | "path"): Promise<string | null> {
  try {
    const result = await app.callServerTool({ name: "copy_image_to_clipboard", arguments: { path: info.path, content } });
    return result.isError ? textOf(result.content as ContentBlock[]) || "Copy failed" : null;
  } catch (err) {
    return (err as Error).message;
  }
}

async function copyImage(info: ImageInfo): Promise<void> {
  const serverError = await serverCopy(info, "image");
  if (serverError === null) {
    toast("Image copied");
    return;
  }
  if (navigator.clipboard?.write && typeof ClipboardItem !== "undefined") {
    try {
      await navigator.clipboard.write([new ClipboardItem({ "image/png": pngBlob(info) })]);
      toast("Image copied");
      return;
    } catch {
      // Not permitted in this frame either; report the server's reason below.
    }
  }
  toast(`Couldn't copy the image: ${serverError}`);
}

async function copyPath(info: ImageInfo, anchor: HTMLElement): Promise<void> {
  if ((await serverCopy(info, "path")) === null) {
    toast("Path copied");
    return;
  }
  await copyText(info.path, anchor);
}

type Action = { label: string; run: (anchor: HTMLButtonElement) => void };

function actionsFor(info: ImageInfo, canDownload: boolean): Action[] {
  const actions: Action[] = [];
  if (canDownload) actions.push({ label: "Download", run: (btn) => void download(info, btn) });
  actions.push({ label: "Copy image", run: () => void copyImage(info) });
  actions.push({ label: "Show in folder", run: () => void reveal(info) });
  actions.push({ label: "Copy path", run: (btn) => void copyPath(info, btn) });
  return actions;
}

let openMenu: HTMLElement | null = null;

function closeMenu(): void {
  openMenu?.remove();
  openMenu = null;
}

/** Our own context menu: the host's native image menu cannot copy images out of the sandboxed frame. */
function showMenu(event: MouseEvent, actions: Action[], figure: HTMLElement): void {
  event.preventDefault();
  closeMenu();
  const menu = document.createElement("div");
  menu.className = "menu";
  menu.setAttribute("role", "menu");
  for (const action of actions) {
    const item = document.createElement("button");
    item.type = "button";
    item.setAttribute("role", "menuitem");
    item.textContent = action.label;
    item.addEventListener("click", () => {
      closeMenu();
      action.run(figure.querySelector("button.action") as HTMLButtonElement);
    });
    menu.append(item);
  }
  document.body.append(menu);
  const { innerWidth, innerHeight } = window;
  const rect = menu.getBoundingClientRect();
  menu.style.left = `${Math.min(event.clientX, innerWidth - rect.width - 4)}px`;
  menu.style.top = `${Math.min(event.clientY, innerHeight - rect.height - 4)}px`;
  (menu.firstElementChild as HTMLElement | null)?.focus();
  openMenu = menu;
}

document.addEventListener("click", (e) => {
  if (openMenu && !openMenu.contains(e.target as Node)) closeMenu();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeMenu();
});
window.addEventListener("blur", closeMenu);

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
      const actions = actionsFor(info, canDownload);
      caption.append(...actions.map((a) => button(a.label, a.run)));
      frame.addEventListener("contextmenu", (e) => showMenu(e, actions, figure));
      figure.append(frame, caption);
      return figure;
    }),
  );
}

app.onhostcontextchanged = (ctx) => applyContext(ctx as McpUiHostContext);

const uploadPanel = new UploadPanel(app, toast);
let uploadMode = false;

app.ontoolinput = (params) => {
  const args = (params.arguments ?? {}) as { prompt?: string; n?: number; size?: string; quality?: string };
  // generate_image and edit_image always carry a prompt; upload_images never does. The panel itself is
  // drawn from the upload_images result (which carries the request id); hosts may not deliver this
  // input until then anyway.
  if (args.prompt === undefined) {
    uploadMode = true;
    showStatus("Opening the upload panel…");
    return;
  }
  const what = [args.n && args.n > 1 ? `${args.n} images` : "1 image", args.size, args.quality]
    .filter((x) => x && x !== "auto")
    .join(" · ");
  showStatus(`Generating ${what}…`, args.prompt ?? "");
};

app.ontoolcancelled = () => showError("The request was cancelled.");

app.ontoolresult = (result) => {
  const content = (result.content ?? []) as ContentBlock[];
  const upload = result.structuredContent as (UploadRequestInfo & { kind?: string; status?: string }) | undefined;
  if (upload?.kind === "upload" || uploadMode) {
    if (result.isError || !upload?.request_id) showError(textOf(content) || "Couldn't open the upload panel.");
    else uploadPanel.show(upload);
    return;
  }
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
