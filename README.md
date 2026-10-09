# claude-foundry-imagegen

Give Claude image generation through your own Microsoft Foundry deployment of a GPT-image model
(for example `gpt-image-2.5-flare`). Claude refines your idea into a model-ready prompt, asking a
few questions when it matters. It then generates or edits the image, saves the full-resolution
file, and shows it to you. In Claude Desktop the image appears in a gallery with download and copy buttons.

It works in **Claude Code** (terminal and the desktop app's Code tab) and in **Claude Desktop**
(Chat and Cowork).

## What you get

| Component | What it does |
|---|---|
| `imagine` skill (`/foundry-imagegen:imagine <idea>`) | Turns a rough idea into a structured prompt, asks up to 3 clarifying questions, picks size, quality, and format, reviews the result, and iterates |
| `generate_image` tool | Text to image: any size up to 4K, quality `low`…`max`, 1–10 variations, PNG/JPEG, transparent backgrounds |
| `edit_image` tool | Edits an image, or composes from up to 16 reference images, with an optional mask |
| `check_config` tool | Checks your endpoint, key, and deployments, and shows the rate-limit state |
| Gallery widget | In Claude Desktop, shows results with **Download**, **Copy image**, **Show in folder**, and **Copy path**, available as buttons and on right-click |
| Rate limiting | Queues requests to respect the deployment's requests-per-minute quota (gpt-image-2.5 allows at most 5, and a deployment may be provisioned lower). The queue is shared across all Claude sessions on the machine. It adopts a lower limit when the service reports one, and it honors the service's `Retry-After` |

Images are saved to `./generated-images/` in a Claude Code project. Outside a project they go to
`Foundry Images` in your Pictures folder, even when Pictures is redirected to OneDrive or a network
share. Each folder also gets an `index.jsonl` that records the prompt and settings for every image.

## Where each part runs

| Surface | Skill | Tools | Gallery | Install |
|---|---|---|---|---|
| Claude Code (CLI / Code tab) | ✓ | ✓ | – (gets file paths and previews) | the plugin |
| Claude Desktop – Cowork | ✓ | ✓ | ✓ | the plugin |
| Claude Desktop – Chat | ✓ | ✓ | ✓ | the plugin **and** the desktop extension (`.mcpb`) |
| claude.ai web / mobile | ✓ (skill only) | – | – | the plugin |

Claude Desktop's Chat tab can't run a plugin's local server. That's why the same server also
ships as a desktop extension.

## Prerequisites

- A Microsoft Foundry (or Azure OpenAI) resource with an image model deployed, for example
  `gpt-image-2.5-flare`. In the Foundry portal, note the resource **endpoint**, the **key**, and the
  **deployment name**.
- **For the plugin:** [uv](https://docs.astral.sh/uv/getting-started/installation/) on your `PATH`.
  On first start it installs the Python dependencies into the plugin's data folder.
- **For the desktop extension:** nothing extra. Claude Desktop provides the runtime for uv-type
  extensions.

## Install

### Claude Code

```bash
claude plugin marketplace add arkane-systems/claude-foundry-imagegen
```

```bash
claude plugin install foundry-imagegen@arkane-systems
```

When the plugin is enabled, Claude Code asks for its settings: endpoint, API key, and optionally
the deployment names and output folder. The key is kept in your system's secure credential store.
To change the settings later, use `/plugin` → **foundry-imagegen** → **Configure**.

### Claude Desktop

1. **Plugin (skill, plus tools in Cowork):** go to **Customize → Plugins → Add marketplace** and
   enter `arkane-systems/claude-foundry-imagegen`. Then add **foundry-imagegen**.
2. **Desktop extension (tools in Chat):** download `foundry-imagegen-<version>.mcpb` from the
   [releases](https://github.com/arkane-systems/claude-foundry-imagegen/releases). Double-click it,
   or open **Settings → Extensions** and install it there. Then fill in the endpoint and key.

If Cowork starts the plugin without asking for its settings, put them in the fallback config file
described below.

### Fallback config file

The server reads its settings from these places, highest priority first:

1. environment variables, which the plugin and extension settings fill in
2. `config.toml` in the user config directory: `~/.config/foundry-imagegen/` on Linux,
   `~/Library/Application Support/foundry-imagegen/` on macOS, and
   `%LOCALAPPDATA%\foundry-imagegen\` on Windows
3. built-in defaults

```toml
endpoint = "https://<resource>.services.ai.azure.com"
api_key = "<key>"
deployment = "gpt-image-2.5-flare"
extra_deployments = "gpt-image-2.5-sunburst"   # optional
rpm_limit = 5
max_wait_seconds = 240
# output_dir = "${PICTURES}/Foundry Images"   # also ${HOME}, ${DOCUMENTS}, ${DESKTOP}, ${DOWNLOADS}, ~
```

Run `check_config`, or ask Claude to "check the image generation setup", to see which source
each setting came from. The key is masked in that output.

## Settings

| Setting | Default | Notes |
|---|---|---|
| `endpoint` | – (required) | Any form works: `https://<res>.services.ai.azure.com`, `https://<res>.openai.azure.com`, a project URL, or a full target URI |
| `api_key` | – (required) | Stored securely by Claude Code and Claude Desktop |
| `deployment` | `gpt-image-2.5-flare` | Default deployment |
| `extra_deployments` | – | Comma-separated. Claude can choose these per request, for example Sunburst for final renders |
| `rpm_limit` | `5` | Your deployment's requests-per-minute quota. If the service reports a lower limit (`x-ratelimit-limit-requests`), that limit is used instead. `check_config` shows both |
| `max_wait_seconds` | `240` | Longest a request waits in the queue before it returns a "retry at" time instead |
| `output_dir` | auto | Empty means `./generated-images` in a project, otherwise `Foundry Images` in your Pictures folder, following any OneDrive or network-share redirection. `${PICTURES}`, `${DOCUMENTS}`, `${DESKTOP}`, `${DOWNLOADS}`, `${HOME}` and `~` are expanded |
| `api_version` | `2025-04-01-preview` | Used only if your resource doesn't serve the GA `/openai/v1` API |

## Usage

Just ask:

> Make a hero image for the README: a cozy reading nook in a lighthouse, warm evening light.

> /foundry-imagegen:imagine a flat vector logo for a bakery called "Crumb & Co"

> Take generated-images/…-lighthouse.png and make it a stormy night, keep everything else.

Claude shows you the final prompt and settings before generating. It reviews the result, offers
targeted follow-up edits, and links to the saved files.

## Troubleshooting

| Symptom | Fix |
|---|---|
| "Missing required setting(s)" | Configure the plugin or extension, or create the config file |
| "Authentication failed (401)" | Check the API key and that it belongs to this endpoint's resource |
| "Deployment … was not found" | Use the deployment name from the Foundry portal, not the model name, if they differ |
| "Rate limit: the next request slot opens at …" | The queue wait would exceed `max_wait_seconds`. Wait, raise the setting, or [request more quota](https://learn.microsoft.com/azure/foundry/openai/quotas-limits) |
| "The service's safety system blocked …" | The prompt or the output was filtered. Rephrase |
| Plugin tools missing in Claude Code | Check that `uv` is on `PATH`, then run `/mcp` to see the server status |

## Development

See [CLAUDE.md](CLAUDE.md) for the repository layout, tests, and how to build the gallery and the
desktop extension.

## License

[MIT](LICENSE)
