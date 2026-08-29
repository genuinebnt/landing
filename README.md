# landing

Static sites served at the edge of genuinebasil.dev.

| Host                        | Served from      |
|-----------------------------|------------------|
| `genuinebasil.dev`          | `sites/landing`  |
| `marginal.genuinebasil.dev` | `sites/marginal` |
| `cairn.genuinebasil.dev`    | `sites/cairn`    |

`blog.genuinebasil.dev` is **not** served from here — it is the Next.js app in
`genuinebnt/genuine.dev`.

## Sources

`*.dc.html` are Claude Design canvas artboards. They are not directly servable:
the canvas wraps the page in `<x-dc>`, keeps head content in a `<helmet>` block,
and loads an editor runtime from `./support.js`.

`build.py` strips that wrapper and emits standalone pages into `sites/`:

```sh
python3 build.py . ./sites
```

Edit the artboards in the canvas, re-run the build, commit both.

## Deploy

The VPS pulls this repo; Caddy bind-mounts `sites/` read-only at `/srv`.
Gateway config lives in `genuinebnt/genuine.dev` (`Caddyfile`).

```sh
ssh deploy@genuinebasil.dev 'cd ~/landing && git pull'
```

No restart needed — Caddy reads the files per request.

`sites/_blog` is the designed blog artboard, kept for reference. Nothing routes to it.
