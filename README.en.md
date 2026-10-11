# web2api

**An OpenAI-compatible AI gateway with a tiny core and modules installed at runtime through git.**

The full documentation is in Vietnamese: **[README.md](README.md)**. This page is a short English summary.

## What it is

web2api runs in one small Docker container (about 32 MB of RAM idle) and exposes the standard OpenAI API
(`/v1/models`, `/v1/chat/completions`, with streaming). Any app that can talk to OpenAI — the `openai` Python
library, Home Assistant integrations with a configurable base URL, chat apps — only needs a new base URL
and the admin key as the API key.

The core ships **no AI source at all** — it is only a *translator*: it speaks the OpenAI protocol, picks a module
by the model prefix and hands the request over. Everything specific to a provider (endpoints, auth headers, error
shapes, free-tier hints, non-OpenAI native APIs) lives **inside that provider's module**, which carries its own
converter. Each source is a *module* under `modules/<id>/` of this repository.
Today there is one: `gemini-api` (Google Gemini through an AI Studio key). Models are addressed as
`<prefix>/<name>`, for example `gemini/gemini-2.5-flash`.

## Quick start

You need Docker (Engine 24+, with `docker compose`).

```bash
git clone https://github.com/TriTue2011/web2api.git
cd web2api
sudo mkdir -p /opt/c2a-moi/data && sudo chown 1000:1000 /opt/c2a-moi/data   # data dir, owned by uid 1000
docker compose up -d                                                         # builds the image on first run
```

Open `http://<host>:3031/`. With nothing installed it opens **System → Modules** (a grid of tiles grouped by module
type, plus a "Connect to web2api" block that disappears after the first link). On first start web2api generates an
admin key; read it with `docker exec web2api cat /data/admin_key` and paste it on the page. Click the `gemini-api`
tile: the detail panel shows what it needs to run (resources, Python packages, outbound hosts, instructions from the
manifest). Click **Install** (in the panel or directly on the tile) — no API key needed. A module that declares
`chat` takes you straight to **Studio** (a copy of the design demo's `Main.dc.html`: status pill, "What would you like
to do today?", one large composer with Draw image / Make video, a model chip and a round send button; below it Chat ·
New · History). Until a key is added the composer stays disabled and one small line says "No key for … — Add key",
linking to that module's card in System → AI → Accounts, instead of returning a raw `404`. **New** saves the current
conversation to **History** (stored in this browser's localStorage: text turns only, at most 50 conversations, no keys);
History lets you search and reopen one to continue. Demo parts with no real feature behind them yet (attachments,
voice, translate/subtitles/dubbing, other main tabs, the account avatar) are not drawn. Add your Google AI Studio key in **System → AI → Accounts**
(keys can be added or removed one by one later; an empty text field never overwrites a saved value). After saving,
the **Providers** tab (real models, per-key health) appears without a page reload and Studio is ready to chat. Studio
renders replies as safe markdown (bold, lists, code, http/https links, tables that scroll inside the bubble; raw HTML
shows as text) and attaches the current date and time to each chat call (one `system` message right before the last
user message, never shown or stored in the conversation). The time zone is the server's standard `TZ` variable (an IANA
name such as `Asia/Ho_Chi_Minh`, exposed as `mui_gio` in `GET /api/tinh-nang`), so the time is your home time wherever
the browser is; without `TZ`, or with an invalid one, Studio falls back to the browser's time zone. A
module that maps to no main tab (for example a "network" type) stays on System → Modules after install, with the
"Add account / API key" step open in its panel. Then call the API:

```bash
curl -s -H "Authorization: Bearer $K" -H "Content-Type: application/json" \
  -d '{"model": "gemini/gemini-2.5-flash", "messages": [{"role": "user", "content": "Hello"}]}' \
  http://<host>:3031/v1/chat/completions
```

Copy `.env.example` to `.env` to change the port, data directory or other settings — for example
`TZ=Asia/Ho_Chi_Minh` for the time zone Studio reports to the model.

## Images and video (paid keys only)

The core also exposes `POST /v1/images/generations` and the asynchronous video API `POST /v1/videos`,
`GET /v1/videos/{id}`, `GET /v1/videos/{id}/content` (the file is streamed). They are routed by the
model prefix like chat; a module without that capability answers `501`. Video IDs look like `<prefix>:<id>`.
`POST /v1/videos` accepts a JSON body or a `multipart/form-data` body with text fields only (the openai SDK sends
`videos.create` as multipart); file parts such as `input_reference` are rejected with `415`.

`gemini-api` 0.2.0 implements both: images through Google's OpenAI-compatible endpoint, video through the native
Veo API (`:predictLongRunning`). According to Google's pricing page on 11/10/2026, **no image or video model is
available on the free tier** — a free key still chats but cannot generate images or video, and every generation
on a paid key costs real money (for example Veo 3.1 Fast about USD 0.10 per second at 720p). When Google refuses a
key for images or video (HTTP 401/403, or 400 while another key succeeds) web2api remembers it for 24 hours **per
key and per capability** (chat with that key keeps working), tries the next key, and, if every key is refused,
returns `403 no_key_permitted` saying a paid key is needed. HTTP 429 pauses the key per `Retry-After` if it has
worked for that capability before, otherwise it is treated as refused for 24 hours (a zero free-tier quota is
likely reported as 429). The
module health endpoint reports `chua_ro` (unknown, never probed with a paid call) / `dung_duoc` (works) /
`bi_tu_choi` (refused) per key and capability. **Not yet tested against real Google**: the actual error shape for
free keys, the Veo flow, and which model IDs are still live.

```bash
curl -s -H "Authorization: Bearer $K" -H "Content-Type: application/json" \
  -d '{"model": "gemini/gemini-3.1-flash-lite-image", "prompt": "A cat in a straw hat", "response_format": "b64_json"}' \
  http://<host>:3031/v1/images/generations
```

## Speech, transcription and image edits

The core also opens `POST /v1/audio/speech` (JSON in, binary audio out, streamable), `POST /v1/audio/transcriptions`
and `POST /v1/images/edits` (both `multipart/form-data`). The core parses the multipart body itself — total size cap
`WEB2API_TEP_TOI_DA_MB` (default 25 MB, `413` above it, counted on the bytes actually received), temporary files closed
before the module runs — and hands the module a `YeuCauDaPhan` (text fields + files). Modules opt in with the optional
methods `noi`, `nghe`, `anh_sua` (contract at the top of `app/kham_pha.py`); no bundled module implements them yet, so
these routes answer `501` today. Verified with the real openai SDK 3.28 against a fake module in the test suite.

## Adding modules via git

Put a module in `modules/<id>/` (a `manifest.json` and a `module.py`) and push it to the watched branch.
web2api polls git (every 300 s by default), shows a "new module" notice, and an admin installs it with one
click or one `curl` — no image rebuild, no restart. Auto-install is **off** by default. A service that
already speaks the OpenAI protocol can instead be registered as a *remote module* by URL.

The web UI shows tabs by **what modules declare**, never by module name: the open endpoint `GET /api/tinh-nang`
counts, per code, the installed and the linked modules, where a module contributes its `loai` (type, an open code
such as `ai`, `app`, `nha`) and its `cung_cap` codes (capabilities such as `chat`, `anh`, `video`). Optional manifest
fields: `loai_ten` (display name of the type), `loai_thu_tu` (sort order), and `muc` — sub-tabs `{id, nhan, can}`
shown only when every code in `can` is provided by a linked module. Combo (ordering several AI sources) has no
backend yet and is never shown. See [CONTRIBUTING.md](CONTRIBUTING.md) (Vietnamese) and README sections 2–3 (architecture
diagrams, components, writing a module) and 9.

## Security notes

- The admin key opens everything (model calls, installing and removing modules). Keep it secret and do **not**
  expose the port to the Internet without HTTPS and a reverse proxy.
- **Modules run inside the core process with the core's privileges.** Anyone who can push to the watched git
  branch can run code in web2api. Follow only repositories you trust; keep auto-install off, or require
  verified commit signatures.
- API keys and tokens are stored separately (mode 0600) and never appear in API responses or logs.
- This repository is public: never commit keys or private data. Report vulnerabilities privately, see
  [SECURITY.md](SECURITY.md).

## Status

Early stage (`0.1.0-khung`). The multi-arch Docker image (amd64 + arm64) is published to
`ghcr.io/tritue2011/web2api` by CI on every push to `main`.

License: proprietary, **all rights reserved** by the repository owner (`TriTue2011`), see [LICENSE](LICENSE).
The source is visible on GitHub for transparency; using, copying, modifying, distributing or deploying it
(including the published image) requires the owner's written permission.
