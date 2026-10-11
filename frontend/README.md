# frontend

A vanilla [SvelteKit 3](https://svelte.dev/docs/kit) + [Svelte 5](https://svelte.dev/docs/svelte) + [Vite 8](https://vite.dev) app running on [Bun](https://bun.com). It was scaffolded with [`sv`](https://svelte.dev/docs/cli) (minimal template, TypeScript, no add-ons). [Valibot](https://valibot.dev) validates input and [Lucide](https://lucide.dev/guide/svelte) supplies the job-action icons through `@lucide/svelte`.

## Requirements

- Bun 1.4 or newer (required by `@sveltejs/adapter-bun`)

`bunfig.toml` sets `[run] bun = true`, so `bun run <script>` executes Vite and the Svelte tooling on the Bun runtime instead of Node. Bun only reads the `bunfig.toml` in the directory it runs from, so the root `package.json` delegates with `bun --cwd=frontend run <script>`.

## Developing

```sh
bun install
bun run dev        # http://localhost:5173
bun run dev --open # ...and open it in a browser
```

The pages call the backend, so it has to be running. `bun run dev` at the repository root starts everything.

## Scripts

| Command             | What it does                                               |
| ------------------- | ---------------------------------------------------------- |
| `bun run dev`       | Start the Vite dev server with HMR                         |
| `bun run build`     | Build a standalone Bun server into `build/`                |
| `bun run preview`   | Preview the production build with Vite                     |
| `bun run start`     | Run the built server (`bun ./build`, port 3000 by default) |
| `bun run check`     | Type-check `.ts` and `.svelte` files with `svelte-check`   |
| `bun run test` | Storage, pagination, cancellation, tailoring lifecycle and PDF proxy regressions |
| `bun run test:browser` | Isolated Chrome browser regression with a mock API (macOS) |

The server reads `HOST`, `PORT` and other variables at startup — see the [adapter-bun docs](https://svelte.dev/docs/kit/adapter-bun#Environment-variables). Set `BODY_SIZE_LIMIT=12M` in production: the default of 512K is smaller than many résumé PDFs, and multipart encoding adds overhead above the 10 MiB PDF limit. The server assumes it runs behind HTTPS, so form submissions to `bun run start` over plain `http://localhost` are rejected as cross-site. Use `bun run preview` to try the production build locally.

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
│ ├ contracts.ts            shared API response types
│ ├ resume-history.ts       versioned browser history and bounded result previews
│ ├ MatchList.svelte        shared live/cached job cards
│ ├ JobActions.svelte       Apply, Copy link and Tailor CV controls
│ ├ tailored-cvs.ts         resumable tailoring and automatic PDF download
│ ├ tailor-history.ts       bounded artifact references, without CV/PDF content
│ └ server/api.ts           server-only, cancellation-aware FastAPI fetch helper
└ routes/
  ├ +layout.svelte          app shell
  ├ layout.css              global styles
  ├ +page.svelte            /: drop a résumé PDF
  ├ resumes/[id]/           /resumes/[id]: live progress and the ranked matches
  ├ resumes.remote.ts       upload, live results, retry, rematch and deletion remote functions
  ├ schema.ts               Valibot schema for the upload, shared with its preflight
  └ +error.svelte           error page
static/                     files served as-is
vite.config.ts              Vite + SvelteKit config (adapter, compiler options, experimental flags)
bunfig.toml                 Bun config
```

## Remote functions

Most data flows through [remote functions](https://svelte.dev/docs/kit/remote-functions). Tailoring uses same-origin `+server.ts` endpoints so the PDF response can stream from the backend. Remote functions are experimental in SvelteKit 3 and enabled in `vite.config.ts` (`experimental.remoteFunctions`, plus `compilerOptions.experimental.async` for `await` in components).

- **`query`** reads data. Await it directly in markup. During SSR the server waits for it, so the HTML arrives with data. Wrap it in a `<svelte:boundary>` with a `failed` snippet to show errors inline.
- **`form`** handles mutations from a `<form>`. Spread it onto the element so it also works without JavaScript. Validate its input with a [Valibot](https://valibot.dev) schema, and reuse that schema in the browser through `.preflight(...)`.
- **`command`** handles mutations triggered from code, such as a button's click handler.
- **`query.live`** streams values from an async generator. During SSR it returns the first value; in the browser it stays connected and updates in place.

Mutations in this app reconnect the affected live query after the backend has accepted the change. Remote functions call FastAPI through `api()` in `src/lib/server/api.ts`, which turns an unreachable or failing backend into a 503 or 502 error.

In this app:

- **`uploadResume`** (`form`) takes the PDF from a file field, so `/` works without JavaScript too. With JavaScript, dropping a file on the drop zone fills the field and submits it. The handler passes the PDF to `POST /resumes` and redirects to `/resumes/[id]`. FastAPI rejections (not a PDF, too long) come back as field issues.
- **`watchResume`** (`query.live`) polls once a second, passing the request's abort signal through both backend fetches and the polling delay. It ends at `done`, `partial` or `failed`; queued and waiting-for-jobs runs continue streaming. The backend's durable worker operates independently of this connection.
- **`retryResume`, `rematchResume`, `deleteResume`** (`command`) expose recovery, refreshed matching, optional location corrections, and deletion. Results reconnect after mutations.

## Results and browser persistence

The results page focuses on the CV and two categories: **Good matches** (composite score strictly above 0.80) and **Other jobs** (at or below 0.80). Each category is sorted by descending match strength, with 20 jobs shown initially and more available on demand. Counts and category filtering happen in `/match-results` before pagination. Good matches open by default; a finished run with none opens Other jobs. Job cards show title, company, location, salary when available, and three actions: **Apply**, **Copy link**, and **Tailor CV**. Percentages, model diagnostics, processing costs, and source metadata stay out of the interface.

Apply opens the original application URL in a separate tab. Copy link uses the browser clipboard; if permission is denied or the API is unavailable, a selectable application link appears on that card. Tailor CV starts a durable backend task, shows “Preparing CV…”, and automatically downloads its PDF when ready. A page-level controller continues polling when another job category is selected. Unmounting aborts polling requests and timers, while the backend continues processing. Clicking a completed Download CV button revalidates the backend’s current candidate, job and design version before downloading. A missing original layout is disclosed only on the relevant card.

Good matches also offers **Copy all links**. It retrieves the entire category from one backend snapshot, including jobs beyond the visible page, and copies unique application URLs in match order, one per line. Closed jobs and unavailable URLs are excluded. The button confirms the number copied; clipboard denial provides a selectable list. A failed request never falls back to copying only the visible cards. Links are fetched on demand and are not added to localStorage.

The results page shows **Estimated time saved** with a compact count of jobs checked and CVs tailored. **How we estimate** explains the assumptions: 30 seconds per job check and 10 minutes per completed tailored CV, estimating manual effort rather than measured user time. The backend supplies both rates and deduplicated successful-work counts; browser events never add credit. The summary updates as matching and tailoring complete, survives refreshes through saved server data, and is hidden when no work has completed or the estimate is unavailable. A ready CV also shows its estimated tailoring contribution on that job's card. Duration displays round down to whole minutes.

The three equal-width actions are stacked vertically on the right of each job card, with a Lucide icon to the left of every label. Copy and download states retain that alignment. On the narrowest phones (under 360px), the vertical rail moves below the details and stays right-aligned so job titles remain readable. Loading icons respect reduced-motion preferences.

`tailor-history.ts` keeps at most 200 artifact references and pending-download flags under `speedhack.tailored-cvs.v1`. Refreshing resumes GET status checks without creating a new task; a completed automatic download is not repeated on later visits. The browser stores no generated PDF, CV content or backend URL. Forgetting a CV also removes its artifact references. If localStorage is unavailable, the current page still works and asks the user to keep it open. Status and PDF routes validate IDs; PDFs stream through the frontend with private, no-store caching, a validated attachment filename and a fixed PDF content type.

A missing CV location defaults to United States for new backend scoring. The results page includes all locations; each job displays its location. Processing, retry, offline, and empty states use concise messages. Refreshing and deletion are secondary actions under More options.

`resume-history.ts` stores up to 10 recent result IDs, category choices, per-category pagination preferences and bounded job previews under `speedhack.resume-history.v1`. Existing saved entries remain readable. Cached previews are scoped to their category; an old unscoped preview is partitioned by the same strict threshold before display. No PDF, CV YAML, name, filename or contact fields are copied into localStorage. Every cached record is bounded and validated; links must use HTTP(S). If quota is exhausted, saving falls back to IDs and preferences without previews. The backend remains the source of truth, and offline previews are labelled as saved jobs.

The homepage reopens saved runs and can forget individual browser entries or clear browser history. The results page separately deletes server data after an explicit confirmation. Browser storage is local to that browser and origin; it is not an account or cross-device backup.

The isolated browser check starts its own mock API and a frontend copied into a temporary directory, asserts that the running development server’s environment is unchanged, makes no model requests, and checks all three job actions, clipboard permission denial, actual PDF downloads after refresh/tab changes, load-more, server filtering, persisted preferences, reopening history, unavailable-backend previews, reconnect and clearing history. It saves a screenshot under `.context/` and closes its temporary processes.

On the server, SvelteKit hands uploaded files over as proxies that only claim `File.prototype` through a `getPrototypeOf` trap. Bun's `instanceof` ignores that trap, so Valibot's `v.file()` rejects them. `schema.ts` checks the file's shape instead, and the handler copies the upload into a real `File` before forwarding it.

## SvelteKit 3 conventions

- SvelteKit is configured through the `sveltekit()` plugin in `vite.config.ts`. There is no `svelte.config.js`.
- `src/lib` is imported via the `#lib` [subpath import](https://svelte.dev/docs/kit/$lib) declared in `package.json` (`$lib` no longer exists), with explicit file extensions.
- `tsconfig.json` extends the generated `$app/tsconfig`.
- Components use runes (`$state`, `$derived`, `$props`), `onclick`-style event attributes, `{@render children()}` and declaration tags (`{const x = $derived(...)}`).
- Internal links use `resolve()` from `$app/paths`, which type-checks route IDs. The active nav link is derived from `page.route.id` (`$app/state`).
- `+error.svelte` receives the error as a typed `error` prop (`ErrorProps`).
- Pages without dynamic data can opt into prerendering with `export const prerender = true`. Pages that use queries or forms must not be prerendered.

## Versions

Every package is on its latest release, except TypeScript, which is kept on 6.x: `@sveltejs/kit` 3 and `svelte-check` declare `typescript@^6` as a peer, and TypeScript 7 (the native compiler) doesn't ship the JS API that `svelte-kit sync` and `svelte-check` rely on.
