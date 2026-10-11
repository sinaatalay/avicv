# frontend

A vanilla [SvelteKit 3](https://svelte.dev/docs/kit) + [Svelte 5](https://svelte.dev/docs/svelte) + [Vite 8](https://vite.dev) app running on [Bun](https://bun.com). It was scaffolded with [`sv`](https://svelte.dev/docs/cli) (minimal template, TypeScript, no add-ons), and has no dependencies beyond the framework itself.

## Requirements

- Bun 1.4 or newer (required by `@sveltejs/adapter-bun`)

`bunfig.toml` sets `[run] bun = true`, so `bun run <script>` executes Vite and the Svelte tooling on the Bun runtime instead of Node. Bun only reads the `bunfig.toml` in the directory it runs from, so the root `package.json` delegates with `bun --cwd=frontend run <script>`.

## Developing

```sh
bun install
bun run dev        # http://localhost:5173
bun run dev --open # ...and open it in a browser
```

## Scripts

| Command             | What it does                                               |
| ------------------- | ---------------------------------------------------------- |
| `bun run dev`       | Start the Vite dev server with HMR                         |
| `bun run build`     | Build a standalone Bun server into `build/`                |
| `bun run preview`   | Preview the production build with Vite                     |
| `bun run start`     | Run the built server (`bun ./build`, port 3000 by default) |
| `bun run check`     | Type-check `.ts` and `.svelte` files with `svelte-check`   |

The server reads `HOST`, `PORT` and other variables at startup — see the [adapter-bun docs](https://svelte.dev/docs/kit/adapter-bun#Environment-variables).

## Project structure

```
src/
├ app.html                  page template
├ app.d.ts                  ambient App.* types
├ lib/                      shared code, imported via #lib/...
│ ├ assets/favicon.svg
│ └ components/Counter.svelte
└ routes/
  ├ +layout.svelte          app shell: header, nav, footer
  ├ layout.css              global styles
  ├ +page.svelte, +page.ts  / (prerendered)
  ├ about/                  /about (prerendered)
  └ +error.svelte           error page
static/                     files served as-is
vite.config.ts              Vite + SvelteKit config (adapter, compiler options)
bunfig.toml                 Bun config
```

## SvelteKit 3 conventions

- SvelteKit is configured through the `sveltekit()` plugin in `vite.config.ts`. There is no `svelte.config.js`.
- `src/lib` is imported via the `#lib` [subpath import](https://svelte.dev/docs/kit/$lib) declared in `package.json` (`$lib` no longer exists), with explicit file extensions.
- `tsconfig.json` extends the generated `$app/tsconfig`.
- Components use runes (`$state`, `$derived`, `$props`), `onclick`-style event attributes and `{@render children()}`.
- Internal links use `resolve()` from `$app/paths`, which type-checks route IDs. The active nav link is derived from `page.route.id` (`$app/state`).
- `+error.svelte` receives the error as a typed `error` prop (`ErrorProps`).
- Pages without dynamic data opt into prerendering with `export const prerender = true`.

## Versions

Every package is on its latest release, except TypeScript, which is kept on 6.x: `@sveltejs/kit` 3 and `svelte-check` declare `typescript@^6` as a peer, and TypeScript 7 (the native compiler) doesn't ship the JS API that `svelte-kit sync` and `svelte-check` rely on.
