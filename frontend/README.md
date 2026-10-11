# frontend

A vanilla [SvelteKit 3](https://svelte.dev/docs/kit) + [Svelte 5](https://svelte.dev/docs/svelte) + [Vite 8](https://vite.dev) app running on [Bun](https://bun.com). It was scaffolded with [`sv`](https://svelte.dev/docs/cli) (minimal template, TypeScript, no add-ons). Its only dependency beyond the framework is [Valibot](https://valibot.dev), which validates remote function input.

## Requirements

- Bun 1.4 or newer (required by `@sveltejs/adapter-bun`)

`bunfig.toml` sets `[run] bun = true`, so `bun run <script>` executes Vite and the Svelte tooling on the Bun runtime instead of Node. Bun only reads the `bunfig.toml` in the directory it runs from, so the root `package.json` delegates with `bun --cwd=frontend run <script>`.

## Developing

```sh
bun install
bun run dev        # http://localhost:5173
bun run dev --open # ...and open it in a browser
```

The `/items` page needs the backend running. `bun run dev` at the repository root starts both.

## Scripts

| Command             | What it does                                               |
| ------------------- | ---------------------------------------------------------- |
| `bun run dev`       | Start the Vite dev server with HMR                         |
| `bun run build`     | Build a standalone Bun server into `build/`                |
| `bun run preview`   | Preview the production build with Vite                     |
| `bun run start`     | Run the built server (`bun ./build`, port 3000 by default) |
| `bun run check`     | Type-check `.ts` and `.svelte` files with `svelte-check`   |

The server reads `HOST`, `PORT` and other variables at startup — see the [adapter-bun docs](https://svelte.dev/docs/kit/adapter-bun#Environment-variables). It assumes it runs behind HTTPS, so form submissions to `bun run start` over plain `http://localhost` are rejected as cross-site. Use `bun run preview` to try the production build locally.

## Environment variables

Declared in `src/env.ts` with `defineEnvVars`, and read through `$app/env/private`:

| Variable  | Default                 | Purpose                                                    |
| --------- | ----------------------- | ---------------------------------------------------------- |
| `API_URL` | `http://127.0.0.1:8000` | Base URL of the FastAPI backend, as reached from this server |

## Project structure

```
src/
├ app.html                  page template
├ app.d.ts                  ambient App.* types
├ env.ts                    environment variable declarations
├ lib/                      shared code, imported via #lib/...
│ ├ assets/favicon.svg
│ ├ components/Counter.svelte
│ └ server/api.ts           server-only fetch helper for the FastAPI backend
└ routes/
  ├ +layout.svelte          app shell: header, nav, footer
  ├ layout.css              global styles
  ├ +page.svelte, +page.ts  / (prerendered)
  ├ about/                  /about (prerendered)
  ├ items/                  /items, backed by FastAPI
  │ ├ +page.svelte
  │ ├ items.remote.ts       remote functions: getItems, addItem, deleteItem
  │ └ schema.ts             Valibot schema shared by addItem and its preflight
  └ +error.svelte           error page
static/                     files served as-is
vite.config.ts              Vite + SvelteKit config (adapter, compiler options, experimental flags)
bunfig.toml                 Bun config
```

## Remote functions

`/items` uses [remote functions](https://svelte.dev/docs/kit/remote-functions) instead of `load` functions and form actions. They are experimental in SvelteKit 3 and enabled in `vite.config.ts` (`experimental.remoteFunctions`, plus `compilerOptions.experimental.async` for `await` in components).

- **`getItems`** (`query`) is awaited directly in the page. During SSR the server waits for it, so the HTML arrives with data. A `<svelte:boundary>` turns a failed request into an inline error with a retry button.
- **`addItem`** (`form`) is spread onto the `<form>`, so it also works without JavaScript. Input is validated by the Valibot schema on the server, and in the browser through `addItem.preflight(...)`. If FastAPI rejects the data with a 422, its message is attached to the field with `invalid(...)`.
- **`deleteItem`** (`command`) is called from a button's click handler.

Both mutations call `getItems().refresh()`, which sends the updated list back in the same response, so each one costs a single request.

## SvelteKit 3 conventions

- SvelteKit is configured through the `sveltekit()` plugin in `vite.config.ts`. There is no `svelte.config.js`.
- `src/lib` is imported via the `#lib` [subpath import](https://svelte.dev/docs/kit/$lib) declared in `package.json` (`$lib` no longer exists), with explicit file extensions.
- `tsconfig.json` extends the generated `$app/tsconfig`.
- Components use runes (`$state`, `$derived`, `$props`), `onclick`-style event attributes, `{@render children()}` and declaration tags (`{const x = $derived(...)}`).
- Internal links use `resolve()` from `$app/paths`, which type-checks route IDs. The active nav link is derived from `page.route.id` (`$app/state`).
- `+error.svelte` receives the error as a typed `error` prop (`ErrorProps`).
- Pages without dynamic data opt into prerendering with `export const prerender = true`. Pages that use queries must not be prerendered.

## Versions

Every package is on its latest release, except TypeScript, which is kept on 6.x: `@sveltejs/kit` 3 and `svelte-check` declare `typescript@^6` as a peer, and TypeScript 7 (the native compiler) doesn't ship the JS API that `svelte-kit sync` and `svelte-check` rely on.
