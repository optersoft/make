# site/

The landing page for `mkrun` — **https://make.optersoft.com** (Cloudflare Pages project
`mkrun`, mkrun-dcd.pages.dev).

A **[frontage](https://github.com/optersoft/frontage) static site**: the pages are Python,
the chrome is a Python package, the deploy is Python, and there is **no `node_modules` and
no JavaScript fetched**. Two pages in well under a second.

```console
$ mk site.dev            # frontage serve, live reload
$ mk site.check          # ruff + the tests, which build the site and read the output
$ mk site.build          # dist/
$ mk site.deploy         # production deployment, by hand (asks first)
$ mk site.csp            # recompute the CSP hashes after a chrome change
$ mk site.smoke          # the live page answers, and still carries every link
```

| File | What it is |
|---|---|
| `site.py` | `BASE` and `STATIC` — what the whole site knows about itself |
| `index.html` | the document template frontage renders into |
| `pages/index.py` | `/` — the whole site, bar the 404 |
| `pages/404.py` | `dist/404.html`, served by Pages with a 404 status for any unmatched path |
| `layouts/site.py` | the head and the chrome, with this site's brand and links |
| `components/landing.py` | the page: the hero and its five bands |
| `components/parts.py` | the band, the card, and the two kinds of code block |
| `tailwind.css` | Tailwind, the chrome, and the terminal / code block styles |
| `public/_headers` | CSP and caching, applied by Pages at the edge |
| `public/robots.txt`, `sitemap.xml`, `favicon.svg` | the usual |
| `tests/` | the built output: no fetched JavaScript, the canonical, the CSP hashes |

## The chrome is shared

Everything that makes this look like Optersoft rather than like this page —
the head, the sticky header with the light / gray / dark toggle, the footer with the
company, the brand typeface and palette, the fonts — is **`optersoft_brand`**, the repo at
`../../brand` (github.com/optersoft/brand). optersoft.com is built on the same package,
which is what makes the sites one company on sight and one theme preference across them.

It is a **path dependency**, the fleet's convention for its Rust crates applied to Python:
nothing to bump, a change there reaches this site on its next build, and uncommitted edits
there ship with a deploy — so commit the chrome alongside the site. A host that clones only
this repository has no sibling; `mk site.build` clones it shallow when it is absent, and the
CI job checks the two out side by side.

⚠ The chrome carries **two inline scripts** — the theme, applied before first paint, and the
toggle's clicks — and the CSP allows them **by hash**. A change in `../brand` therefore
needs `mk site.csp` and a rebuild. Nothing else notices: a stale hash breaks neither the
build nor the deploy, the browser simply refuses to run the theme. The test suite is what
catches it.

## What belongs here, and what does not

The page makes the case for using the tool: what it is, who it is for, why it is worth the
runtime it costs, and where it is going. It is not documentation — the authoring API lives in
the README, the reasoning in `docs/design.md`, the tutorial on
[academy](https://academy.optersoft.com/project/make) — and it is not a release feed;
releases are tags. Anything that has to be updated on every commit does not belong here.

## Deploying

The Pages project is `mkrun`, direct upload (no git provider connected), with
`make.optersoft.com` as its custom domain. A push to `main` that touches `site/` builds and
deploys through `.github/workflows/site.yml`, which needs the `CLOUDFLARE_API_TOKEN` and
`CLOUDFLARE_ACCOUNT_ID` repository secrets and skips itself with a warning until they exist.

`mk site.deploy` is the hand fallback. It publishes through **`make-cloudflare`**, this
repository's own workspace member — the four direct-upload API calls, no wrangler.

⚠ **That needs an API token, where wrangler needed a login.** Until 2026-09-14 the hand
deploy rode on whatever `wrangler login` had cached in `~/.wrangler`; an OAuth session is
not something the API can be handed, so the token is now the only route. Create one with
**Cloudflare Pages: Edit**, then `mk secure.set CLOUDFLARE_API_TOKEN <value>` (or put it in
`~/.make/secrets.env`, where `CLOUDFLARE_ACCOUNT_ID` already lives).

## History

| when | what |
|---|---|
| 2026-09 | a landing page on Cloudflare Pages: hand-written static files |
| → 2026-09-14 | Astro 7 on `@optersoft/astro`, deployed with wrangler |
| 2026-09-14 → | **rebuilt on frontage**, deployed with `make-cloudflare`. The site of a Python task runner had a `node_modules` and shelled out to a JavaScript CLI to publish itself; now it has neither, and the deploy path is a package this repository ships |
