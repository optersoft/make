# make-marketplace

VS Code Marketplace tasks for [`mkrun`](https://github.com/optersoft/make):
build an extension's `.vsix`, see what is already published, ask CI whether its
token can publish at all, and cut a release.

```python
# Makefile.py
# /// script
# requires-python = ">=3.11"
# dependencies = ["mkrun>=0.4", "make-marketplace>=0.1"]
#
# [tool.uv.sources]
# make-marketplace = { git = "https://github.com/optersoft/make.git", subdirectory = "marketplace" }
# ///
from make import task
from make_marketplace import marketplace

marketplace.Marketplace.configure(
    repo="optersoft/kotlin",          # whose CI holds the token
    environment="marketplace",        # the GitHub environment it is scoped to
    paths=["kotlin", "Makefile.py"],  # what must be committed to release
)


@task(name="gate", group="marketplace", override=True)
def gate(ext: str = "") -> None:
    """The repo's own tests. `release` runs this, and refuses to guess what it is."""
    test(ext)
```

```
mk marketplace.package [EXT]        build the .vsix
mk marketplace.published [EXT]      what is already on the Marketplace
mk marketplace.verify-token         can CI's token actually publish?
mk marketplace.release VERSION      tag a release; CI publishes it
```

## The one design decision

**No task here can publish**, and that is the point. The credential is a
`VSCE_PAT` secret on a GitHub environment, used only by a tag-triggered publish
job, so a release is a *push*. `release` writes the version, rolls the
changelog, packages, commits, tags and pushes; CI does the rest. No laptop ever
needs the ability to publish, and no task in this package has it.

What `release` mostly does is refuse, cheapest check first — a mistyped version
costs a second rather than a test run:

| | |
|---|---|
| the version | semver, or the publish job rejects it after the tag is pushed |
| the tree | clean, in `paths` |
| the branch | `branch`, because the listing should describe it |
| `marketplace.preflight` | the repo's own refusals, if it has any |
| the tag | unused, here and on `origin` |
| the changelog | has a `## [Unreleased]` section — checked **before** anything is written |
| the version, again | not already on the Marketplace |
| the token | present **and** confirmed able to publish |
| `marketplace.gate` | the repo's tests |

## Three things that are not obvious

**A published version is permanent.** It cannot be replaced, withdrawn or
re-uploaded — only superseded by a higher one. `0.2.0` is the one and only shot
at `0.2.0`. That single sentence is why the preflight is as long as it is, and
why `published_versions` spends one HTTP call rather than letting CI find out.

**A present token and a working token are different things.** A PAT that
authenticates but is not permitted fails with `Access Denied ... Publish new
extensions to an existing publisher` — either its scopes are too narrow
(Organization: *All accessible organizations*, Scopes: *Marketplace > Manage*)
or its identity is not a member of the publisher. Checking that the secret
*exists* passed three times in a row while every publish failed. The token is in
CI by design, so CI is the only place the question can be asked: `release`
dispatches `verify_workflow` and waits for the answer.

**An environment secret is invisible to `gh secret list --repo`.** It prints an
empty list, which reads exactly like "no token configured" — a false negative
that once blocked a release whose token was there all along. `secret_names` asks
both scopes.

## Hooks

`marketplace.gate` is `abstract=True`: it lists as unimplemented and refuses to
run with a message saying what to write, rather than this package deciding what
"tested" means for a repo it knows nothing about.

`marketplace.preflight` does nothing by default and exists for refusals that are
nobody else's business. `optersoft/kotlin` uses it to require a tree that has
been run on a real Windows desktop — Node cannot start a `.bat` since the fix
for CVE-2024-27980, which shipped two broken releases with every test green.

## Scope

Marketplace and Open VSX are both published by the *CI job*, not from here; this
package only gets a correct tag onto `origin`. Open VSX typically publishes
behind an `OVSX_PAT` the job skips when absent, so a Marketplace release never
depends on having registered there.
