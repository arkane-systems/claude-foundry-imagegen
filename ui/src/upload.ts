// Upload panel for the upload_images tool: the user drops, picks, or pastes images, and the widget
// sends them straight to the server (stage_upload), so the bytes never pass through the model.
import type { App } from "@modelcontextprotocol/ext-apps/app-with-deps";

interface UploadArgs {
  purpose?: string;
  max_files?: number;
}

interface Picked {
  file: File;
  dataUrl: string;
}

const MAX_BYTES = 50 * 1024 * 1024;
const TYPES = ["image/png", "image/jpeg", "image/webp"];

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;

export class UploadPanel {
  private picked: Picked[] = [];
  private maxFiles = 1;
  private active = false;
  private finished = false;

  constructor(
    private readonly app: App,
    private readonly toast: (message: string) => void,
  ) {
    const zone = $("dropzone");
    const input = $<HTMLInputElement>("file-input");
    $("choose").addEventListener("click", (e) => {
      e.stopPropagation();
      input.click();
    });
    zone.addEventListener("click", () => input.click());
    zone.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        input.click();
      }
    });
    input.addEventListener("change", () => {
      void this.add([...(input.files ?? [])]);
      input.value = "";
    });
    zone.addEventListener("dragover", (e) => {
      e.preventDefault();
      zone.classList.add("over");
    });
    zone.addEventListener("dragleave", () => zone.classList.remove("over"));
    zone.addEventListener("drop", (e) => {
      e.preventDefault();
      zone.classList.remove("over");
      void this.add([...(e.dataTransfer?.files ?? [])]);
    });
    document.addEventListener("paste", (e) => {
      if (!this.active) return;
      const files = [...(e.clipboardData?.files ?? [])];
      if (files.length) {
        e.preventDefault();
        void this.add(files.map((f, i) => (f.name && f.name !== "image.png" ? f : rename(f, `pasted-image-${i + 1}.png`))));
      }
    });
    $("send").addEventListener("click", () => void this.send());
    $("cancel").addEventListener("click", () => void this.cancel());
  }

  show(args: UploadArgs): void {
    this.active = true;
    this.finished = false;
    this.picked = [];
    this.maxFiles = Math.max(1, Math.min(16, args.max_files ?? 1));
    $<HTMLInputElement>("file-input").multiple = this.maxFiles > 1;
    $("upload-title").textContent = this.maxFiles > 1 ? `Add up to ${this.maxFiles} images for Claude` : "Add an image for Claude";
    $("upload-purpose").textContent = args.purpose ?? "";
    $("status").hidden = true;
    $("result").hidden = true;
    $("error").hidden = true;
    $("upload").hidden = false;
    this.renderPicked();
    this.setStatus("");
  }

  /** Called with the upload_images tool result. */
  finish(status: "received" | "cancelled" | "error", detail: string): void {
    this.finished = true;
    this.active = false;
    $("upload").classList.add("locked");
    $<HTMLButtonElement>("send").disabled = true;
    $<HTMLButtonElement>("cancel").disabled = true;
    this.setStatus(status === "received" ? `Claude received ${detail}.` : status === "cancelled" ? "Upload cancelled." : detail);
  }

  private requestId(): string | undefined {
    const id = this.app.getHostContext()?.toolInfo?.id;
    return id === undefined || id === null ? undefined : String(id);
  }

  private async add(files: File[]): Promise<void> {
    if (this.finished) return;
    for (const file of files) {
      if (!TYPES.includes(file.type)) {
        this.toast(`${file.name || "That file"} isn't a PNG, JPEG, or WebP image`);
        continue;
      }
      if (file.size > MAX_BYTES) {
        this.toast(`${file.name} is over 50 MB`);
        continue;
      }
      if (this.maxFiles === 1) this.picked = [];
      if (this.picked.length >= this.maxFiles) {
        this.toast(`Only ${this.maxFiles} image${this.maxFiles === 1 ? "" : "s"} can be added`);
        break;
      }
      this.picked.push({ file, dataUrl: await readAsDataUrl(file) });
    }
    this.renderPicked();
  }

  private renderPicked(): void {
    const list = $("picked");
    list.replaceChildren(
      ...this.picked.map((p, i) => {
        const figure = document.createElement("figure");
        const img = document.createElement("img");
        img.src = p.dataUrl;
        img.alt = p.file.name;
        const caption = document.createElement("figcaption");
        caption.textContent = `${p.file.name} · ${formatSize(p.file.size)}`;
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "linkish";
        remove.textContent = "Remove";
        remove.addEventListener("click", () => {
          this.picked.splice(i, 1);
          this.renderPicked();
        });
        caption.append(" ", remove);
        figure.append(img, caption);
        return figure;
      }),
    );
    $<HTMLButtonElement>("send").disabled = this.finished || this.picked.length === 0;
  }

  private async send(): Promise<void> {
    const button = $<HTMLButtonElement>("send");
    button.disabled = true;
    this.setStatus("Sending…");
    try {
      const files = this.picked.map((p) => ({
        name: p.file.name,
        data: p.dataUrl.slice(p.dataUrl.indexOf(",") + 1),
        // Some hosts (older Electron) expose the real path; the server uses the file in place if so.
        original_path: (p.file as File & { path?: unknown }).path || undefined,
      }));
      const result = await this.app.callServerTool({ name: "stage_upload", arguments: { files, request_id: this.requestId() } });
      if (result.isError) throw new Error(textOf(result.content) || "The server rejected the upload.");
      $("upload").classList.add("locked");
      this.setStatus("Sent — Claude is continuing.");
    } catch (err) {
      button.disabled = false;
      this.setStatus(`Upload failed: ${(err as Error).message}`);
    }
  }

  private async cancel(): Promise<void> {
    $<HTMLButtonElement>("cancel").disabled = true;
    try {
      await this.app.callServerTool({ name: "cancel_upload", arguments: { request_id: this.requestId() } });
      this.setStatus("Cancelling…");
    } catch (err) {
      $<HTMLButtonElement>("cancel").disabled = false;
      this.setStatus(`Couldn't cancel: ${(err as Error).message}`);
    }
  }

  private setStatus(text: string): void {
    $("upload-status").textContent = text;
  }
}

function rename(file: File, name: string): File {
  return new File([file], name, { type: file.type });
}

function readAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error ?? new Error("Could not read the file"));
    reader.readAsDataURL(file);
  });
}

function formatSize(bytes: number): string {
  return bytes >= 1_048_576 ? `${(bytes / 1_048_576).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

function textOf(content: unknown): string {
  return ((content as { type: string; text?: string }[] | undefined) ?? [])
    .filter((c) => c.type === "text" && c.text)
    .map((c) => c.text)
    .join("\n");
}
