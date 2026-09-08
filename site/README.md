# site/

The landing page for `mkrun` — **https://make.optersoft.com** (Cloudflare Pages project
`mkrun`, mkrun-dcd.pages.dev).

An Astro static site on **`@optersoft/astro`**, the chrome every Optersoft site shares
(github.com/optersoft/astro): the layout, the header with the light / gray / dark toggle,
the footer with the company, the brand typeface and palette. It is a `file:../../astro`
path dependency on the sibling checkout, so `npm ci` needs `../../astro` to exist — the
deploy workflow checks the two repos out side by side.

```console
$ mk site.dev            # astro dev on :4321, hot reload
$ mk site.build          # dist/
$ mk site.deploy         # production deployment, by hand (asks first)
$ mk site.smoke          # the live page answers, and still carries every link
```

| File | What it is |
|---|---|
| `src/pages/index.astro` | the whole page: description, example, who it is for, why, direction |
| `src/pages/404.astro` | `dist/404.html`, served by Pages with a 404 status for any unmatched path |
| `src/layouts/Site.astro` | the chrome's Layout + Shell with this site's brand and links |
| `src/styles/global.css` | Tailwind, the chrome, and the terminal / code block styles |
| `public/_headers` | CSP and caching, applied by Pages at the edge |
| `public/robots.txt`, `sitemap.xml`, `favicon.svg` | the usual |

## What belongs here, and what does not

The page makes the case for using the tool: what it is, who it is for, why it is worth the
runtime it costs, and where it is going. It is not documentation — the authoring API lives in
the README, the reasoning in `docs/design.md`, the tutorial on
[academy](https://academy.optersoft.com/project/make) — and it is not a release feed; releases
are tags. Anything that has to be updated on every commit does not belong here.

## Deploying

The Pages project is `mkrun`, direct upload (no git provider connected), with
`make.optersoft.com` as its custom domain. A push to `main` that touches `site/` builds and
deploys through `.github/workflows/site.yml`, which needs the `CLOUDFLARE_API_TOKEN` and
`CLOUDFLARE_ACCOUNT_ID` repository secrets and skips itself with a warning until they exist.
`mk site.deploy` is the hand fallback and uses whatever `wrangler login` cached.
