# speedhack

Monorepo for the speedhack web app.

| Directory                | What                                         | Toolchain |
| ------------------------ | -------------------------------------------- | --------- |
| [`frontend/`](frontend/) | SvelteKit 3 + Svelte 5 + Vite 8 web app     | Bun       |

The root `package.json` is a [Bun workspace](https://bun.com/docs/pm/workspaces), so `bun install` at the root installs everything, and `bun.lock` lives here.

```sh
bun install
bun run dev    # SvelteKit dev server on http://localhost:5173
bun run check  # svelte-check
bun run build  # production build (frontend/build)
```
