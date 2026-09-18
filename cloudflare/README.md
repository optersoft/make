# make-cloudflare

Cloudflare Pages **direct upload** for [`mkrun`](https://github.com/optersoft/make), in
Python: publish a built directory with no Node on the machine and no `wrangler`
in the pipeline.

```python
# Makefile.py in a consuming repo
# /// script
# requires-python = ">=3.11"
# dependencies = ["mkrun>=0.4", "make-cloudflare>=0.1"]
# ///
from make_cloudflare import cloudflare  # importing is what registers the group
```

```console
$ mk cloudflare.deploy site/dist --project mkrun     # production (branch main)
$ mk cloudflare.deploy site/dist --branch try        # a preview, on its own URL
$ mk cloudflare.projects                             # what the account has
$ mk cloudflare.deployments --limit 5                # one project's recent ones
$ mk -n cloudflare.deploy site/dist                  # the plan; nothing is sent
```

Credentials are the two variables wrangler already reads, so a repository that
deploys from CI needs no new secret: `CLOUDFLARE_ACCOUNT_ID` and
`CLOUDFLARE_API_TOKEN` (a token with **Cloudflare Pages: Edit**). The token's
name ends in `TOKEN`, so mkrun treats it as a credential — it is withheld from
the environment until a task asks for it, redacted in everything printed, and
readable from the encrypted store. The project can come from `--project` or from
`CLOUDFLARE_PAGES_PROJECT` in the repo's env layer.

## Why this exists

`wrangler pages deploy` is four HTTPS calls and a hash. Reaching them through
`npx` costs a Node toolchain in repositories that otherwise have none — a Rust
one, a [frontage](https://github.com/optersoft/frontage) one — and a
`node_modules` in the deploy image of every one of them.

```
POST /accounts/<acct>/pages/projects/<p>/upload-token   -> a short-lived JWT
POST /pages/assets/check-missing                        -> what is not stored yet
POST /pages/assets/upload                               -> the files, batched
POST /pages/assets/upsert-hashes
POST /accounts/<acct>/pages/projects/<p>/deployments    -> the manifest
```

Three things make a naive port wrong, and all three fail **silently** — every
call returns 200 and the site serves the old bytes, or none:

- **The hash is BLAKE3 of a strange input**: the base64 *text* of the contents
  with the extension (no dot) appended, first 32 hex characters. Not of the
  bytes, and not SHA-256. `tests/test_pages.py` locks it with a golden value.
- **`_headers`, `_redirects` and `_routes.json` are not assets.** They are
  fields on the deployment. Walk them in as ordinary files and the deploy
  reports success while the site's CSP stops applying.
- **`check-missing`, `upload` and `upsert-hashes` take the JWT, not the account
  token, and carry no `/accounts/<id>` prefix** — the JWT already names the
  account.

## What it does not do

**Pages Functions.** A directory holding `_worker.js` or `functions/` is
refused by name rather than half-deployed: bundling a Worker is a build step,
not an upload, and wrangler should keep doing it.

**Steps 2 and 3 are undocumented.** They exist because wrangler uses them, and
Cloudflare can change them without a changelog; the documented `deployments`
endpoint alone cannot upload a file. That is the real cost of dropping
wrangler, and it is the reason this is a small module with a golden test rather
than a wrapper nobody reads.

Nothing here is specific to any owner or account. The group name is
`cloudflare`, and it merges with a repo's own `cloudflare.*` tasks — groups are
namespaces — so only a same-named task collides; this package claims `deploy`,
`projects` and `deployments`, nothing else. `blake3` is the one dependency, and
it lives here rather than in the runner so `mkrun` itself stays dependency-free.
