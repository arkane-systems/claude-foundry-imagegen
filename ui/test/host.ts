// Test host for the gallery widget: plays the host side of MCP Apps with AppBridge and stubbed
// server tools, then drives the widget. Scenario is chosen by location.hash. Results go to #log.
import { AppBridge, PostMessageTransport } from "@modelcontextprotocol/ext-apps/app-bridge";

declare const GALLERY: string;
declare const SAMPLE_PNG: string;

const log = (m: string) => {
  document.getElementById("log")!.textContent += m + "\n";
};
const iframe = document.getElementById("app") as HTMLIFrameElement;
const scenario = location.hash.slice(1) || "gallery";
const later = (ms: number) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const bridge = new AppBridge(null, { name: "TestHost", version: "1" }, { serverTools: {}, downloadFile: {} }, {
    hostContext: { theme: "light", availableDisplayModes: ["inline", "fullscreen"], displayMode: "inline" },
  } as never);
  bridge.oncalltool = async (p) => {
    const a = (p.arguments ?? {}) as { request_id?: string; files?: { name: string; data: string }[]; path?: string; content?: string };
    const files = (a.files ?? []).map((f) => `${f.name}:${f.data.length > 0}`).join(",");
    log(`call ${p.name} request_id=${a.request_id ?? "-"} files=${files || "-"} path=${a.path ?? "-"} content=${a.content ?? "-"}`);
    return { content: [{ type: "text", text: "ok" }], structuredContent: { file_name: "x.png", mime: "image/png", data: SAMPLE_PNG } };
  };
  bridge.ondownloadfile = async (p) => {
    log(`download ${p.contents.length}`);
    return {};
  };
  bridge.oninitialized = async () => {
    log("initialized");
    const doc = () => iframe.contentDocument!;
    const win = () => iframe.contentWindow as unknown as typeof window;
    if (scenario === "gallery") {
      await bridge.sendToolInput({ arguments: { prompt: "A sun", n: 2, size: "1536x1024" } });
      await bridge.sendToolResult({
        content: [{ type: "text", text: "done" }, { type: "image", data: SAMPLE_PNG, mimeType: "image/png" }, { type: "image", data: SAMPLE_PNG, mimeType: "image/png" }],
        structuredContent: {
          operation: "generate", prompt: "A sun", deployment: "gpt-image-2.5-flare", duration_s: 12, output_dir: "/out",
          images: [1, 2].map((i) => ({ path: `/out/sun-${i}.png`, file_name: `sun-${i}.png`, mime: "image/png", width: 64, height: 48, bytes: 1000 })),
        },
      } as never);
      await later(300);
      log(`figures=${doc().querySelectorAll("#grid figure").length} status_hidden=${doc().getElementById("status")!.hidden}`);
      const frame = doc().querySelector(".frame") as HTMLElement;
      frame.dispatchEvent(new (win().MouseEvent)("contextmenu", { bubbles: true, cancelable: true, clientX: 30, clientY: 30 }));
      log(`menu=${[...doc().querySelectorAll(".menu button")].map((b) => b.textContent).join("|")}`);
      ([...doc().querySelectorAll(".menu button")].find((b) => b.textContent === "Copy image") as HTMLButtonElement).click();
      await later(300);
      log(`toast=${doc().getElementById("toast")!.textContent}`);
    } else if (scenario === "upload" || scenario === "upload-cancel") {
      // Like Claude Desktop: the widget gets the input and the finished upload_images result together.
      await bridge.sendToolInput({ arguments: { purpose: "the photo to restyle", max_files: 1 } });
      await bridge.sendToolResult({
        content: [{ type: "text", text: "panel open" }],
        structuredContent: { kind: "upload", status: "awaiting", request_id: "req123", purpose: "the photo to restyle", max_files: 1 },
      } as never);
      await later(200);
      log(`panel_visible=${!doc().getElementById("upload")!.hidden} purpose=${doc().getElementById("upload-purpose")!.textContent}`);
      if (scenario === "upload-cancel") {
        (doc().getElementById("cancel") as HTMLButtonElement).click();
      } else {
        const bytes = Uint8Array.from(atob(SAMPLE_PNG), (c) => c.charCodeAt(0));
        const file = new (win().File)([bytes], "holiday.png", { type: "image/png" });
        const dt = new (win().DataTransfer)();
        dt.items.add(file);
        doc().getElementById("dropzone")!.dispatchEvent(new (win().DragEvent)("drop", { bubbles: true, cancelable: true, dataTransfer: dt }));
        await later(300);
        log(`thumbs=${doc().querySelectorAll("#picked figure").length} send_enabled=${!(doc().getElementById("send") as HTMLButtonElement).disabled}`);
        (doc().getElementById("send") as HTMLButtonElement).click();
      }
      await later(400);
      log(`upload_status=${doc().getElementById("upload-status")!.textContent} locked=${doc().getElementById("upload")!.classList.contains("locked")}`);
    }
    log("done");
  };
  await bridge.connect(new PostMessageTransport(iframe.contentWindow!, iframe.contentWindow!));
  iframe.srcdoc = GALLERY; // only after the bridge is listening, or the widget's ui/initialize is lost
})();
