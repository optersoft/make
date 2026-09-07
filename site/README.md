# site/

The landing page for `mkrun` — **https://mkrun-dcd.pages.dev**.

Static files, no build step: what is in this directory is what Cloudflare Pages serves, so
`mk site.serve` shows exactly what a deploy publishes. There is no framework on purpose — the
page is a description, four links and a changelog, and a toolchain for that would be a second
thing to keep alive.

```console
$ mk site.serve          # http://localhost:8100
$ mk site.deploy         # production deployment (asks first)
$ mk site.smoke          # the live page answers, and still carries every link
```

| File | What it is |
|---|---|
| `index.html` | the whole page: description, example, comparison, changelog |
| `404.html` | served by Pages, with a 404 status, for any unmatched path |
| `style.css` | the only stylesheet; no fonts, no scripts, no third-party requests |
| `_headers` | CSP and caching, applied by Pages at the edge |
| `robots.txt`, `sitemap.xml`, `favicon.svg` | the usual |

## The changelog lives here

`index.html` is the single source for the changelog — there is no `CHANGELOG.md` at the repo
root to drift from it. Add an entry under **Unreleased** as the change lands, and rename that
block to the version when `mk dist.release` tags it.

## Deploying

The Pages project is `mkrun`, direct upload (no git provider connected). A push to `main` that
touches `site/` deploys through `.github/workflows/site.yml`, which needs the
`CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` repository secrets and skips itself with a
warning until they exist. `mk site.deploy` is the hand fallback and uses whatever
`wrangler login` cached.
