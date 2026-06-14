# Sound Vault landing page

Static, single-file landing page (`index.html`) with pairing instructions, the iOS
Shortcut recipe, and a live global leaderboard widget.

## Configure the API base

The leaderboard widget reads the relay base URL from a meta tag:

```html
<meta name="sv-api-base" content="https://your-relay.example" />
```

Leave it empty during development — the widget shows a friendly "goes live when the
relay is connected" placeholder instead of erroring.

## Deploy

This is a plain static page; deploy the `web/` directory anywhere.

- **Netlify:** point a site at this repo with publish directory `web` (no build command).
- **Vercel:** import the repo, set the root/output to `web` (framework preset: "Other").

The relay/leaderboard API (FastAPI) deploys separately — see `docs/relay-deployment.md`.
On Vercel the relay runs on the Python runtime backed by Neon Postgres; set
`sv-api-base` to that deployment's URL once it's live.
