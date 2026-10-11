# web2api

**An OpenAI-compatible AI gateway with a tiny core and modules installed at runtime through git.**

The full documentation is in Vietnamese: **[README.md](README.md)**. This page is a short English summary.

## What it is

web2api runs in one small Docker container (about 32 MB of RAM idle) and exposes the standard OpenAI API
(`/v1/models`, `/v1/chat/completions`, with streaming). Any app that can talk to OpenAI — the `openai` Python
library, Home Assistant integrations with a configurable base URL, chat apps — only needs a new base URL
and the admin key as the API key.

The core ships **no AI source at all**. Each source is a *module* under `modules/<id>/` of this repository.
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

Open `http://<host>:3031/`. On first start web2api generates an admin key; read it with
`docker exec web2api cat /data/admin_key`. Paste it into the **Module** page and install `gemini-api` — installing needs no
API key. Then add your Google AI Studio key on the web (keys can be added or removed one by one later), and call the API:

```bash
curl -s -H "Authorization: Bearer $K" -H "Content-Type: application/json" \
  -d '{"model": "gemini/gemini-2.5-flash", "messages": [{"role": "user", "content": "Hello"}]}' \
  http://<host>:3031/v1/chat/completions
```

Copy `.env.example` to `.env` to change the port, data directory or other settings.

## Adding modules via git

Put a module in `modules/<id>/` (a `manifest.json` and a `module.py`) and push it to the watched branch.
web2api polls git (every 300 s by default), shows a "new module" notice, and an admin installs it with one
click or one `curl` — no image rebuild, no restart. Auto-install is **off** by default. A service that
already speaks the OpenAI protocol can instead be registered as a *remote module* by URL. See
[CONTRIBUTING.md](CONTRIBUTING.md) (Vietnamese) and README sections 8 and 15.

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
