"""make-cloudflare: the hash is the contract, and the special files are not assets."""

from __future__ import annotations

import base64
import json

import pytest

from make.errors import MakeError
from make.testing import context
from make_cloudflare import pages


def build(root, files: dict[str, str]):
    for name, text in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    return root


class FakeHTTP:
    """Stands in for `make.http.request`, answering each endpoint in turn."""

    def __init__(self, *, missing: list[str] | None = None):
        self.calls: list[tuple[str, str, dict]] = []
        self.missing = missing

    def __call__(self, url, *, method="GET", headers=None, data=None, **kwargs):
        self.calls.append((method, url, {"headers": headers or {}, "data": data}))
        if url.endswith("/upload-token"):
            result = {"jwt": "jot"}
        elif url.endswith("/check-missing"):
            sent = json.loads(data)["hashes"]
            result = sent if self.missing is None else self.missing
        elif url.endswith("/deployments"):
            result = {"url": "https://abc123.example.pages.dev"}
        else:
            result = {}
        return pages.http.Response(
            url=url, status=200, body=json.dumps({"success": True, "errors": [], "result": result})
        )

    def urls(self, fragment: str) -> list[str]:
        return [url for _, url, _ in self.calls if fragment in url]

    def body(self, fragment: str):
        return next(call["data"] for _, url, call in self.calls if fragment in url)

    def auth(self, fragment: str) -> str:
        call = next(c for _, url, c in self.calls if fragment in url)
        return call["headers"]["Authorization"]


def deploy(monkeypatch, root, fake, **kwargs):
    monkeypatch.setattr(pages.http, "request", fake)
    return pages.deploy(root, project="demo", account="acct", token="tok", **kwargs)


# -- the hash --------------------------------------------------------------


def test_the_hash_is_blake3_of_the_base64_text_plus_the_extension():
    # Locked deliberately. Cloudflare's asset store is keyed on this value, so
    # a "simplification" to hashing the raw bytes -- the obvious reading -- would
    # upload files that nothing ever asks for, with every call still returning
    # 200. If this test fails, the deploy is broken, not the test.
    assert pages.file_hash(b"hello", "html") == "a2b82584e50075886b08927390f2f573"
    assert len(pages.file_hash(b"hello", "html")) == 32


def test_the_extension_is_part_of_the_hash():
    assert pages.file_hash(b"same", "html") != pages.file_hash(b"same", "css")
    assert pages.file_hash(b"same", "") != pages.file_hash(b"same", "html")


# -- walking the directory -------------------------------------------------


def test_collect_keys_with_a_leading_slash_and_skips_the_noise(tmp_path):
    build(
        tmp_path,
        {
            "index.html": "<h1>hi</h1>",
            "assets/app.css": "body{}",
            ".DS_Store": "junk",
            "node_modules/dep/index.js": "junk",
        },
    )
    assets, specials = pages.collect(tmp_path)
    assert pages.manifest_of(assets).keys() == {"/index.html", "/assets/app.css"}
    assert specials == {}


def test_the_special_files_are_fields_not_assets(tmp_path):
    build(
        tmp_path,
        {"index.html": "x", "_headers": "/*\n  X-Frame-Options: DENY\n", "_redirects": "/a /b 301\n"},
    )
    assets, specials = pages.collect(tmp_path)
    assert pages.manifest_of(assets).keys() == {"/index.html"}
    assert set(specials) == {"_headers", "_redirects"}


def test_a_functions_project_is_refused(tmp_path):
    build(tmp_path, {"index.html": "x", "_worker.js": "export default {}"})
    with pytest.raises(MakeError, match="Functions"):
        pages.collect(tmp_path)


def test_an_oversized_asset_is_named(tmp_path):
    (tmp_path / "big.bin").write_bytes(b"x" * (pages.MAX_ASSET_SIZE + 1))
    with pytest.raises(MakeError, match=r"big\.bin"):
        pages.collect(tmp_path)


def test_an_empty_directory_is_refused(tmp_path):
    with pytest.raises(MakeError, match="no files"):
        pages.collect(tmp_path)


# -- the four calls --------------------------------------------------------


def test_deploy_makes_the_four_calls_in_order(monkeypatch, tmp_path):
    build(tmp_path, {"index.html": "x"})
    fake = FakeHTTP()
    url = deploy(monkeypatch, tmp_path, fake)
    assert [u.rsplit("/", 1)[-1] for _, u, _ in fake.calls] == [
        "upload-token",
        "check-missing",
        "upload",
        "upsert-hashes",
        "deployments",
    ]
    assert url == "https://abc123.example.pages.dev"


def test_the_asset_endpoints_use_the_jwt_and_omit_the_account(monkeypatch, tmp_path):
    build(tmp_path, {"index.html": "x"})
    fake = FakeHTTP()
    deploy(monkeypatch, tmp_path, fake)
    for endpoint in ("/assets/check-missing", "/assets/upload", "/assets/upsert-hashes"):
        assert fake.auth(endpoint) == "Bearer jot"
        assert "/accounts/" not in fake.urls(endpoint)[0]
    assert fake.auth("/deployments") == "Bearer tok"
    assert "/accounts/acct/" in fake.urls("/deployments")[0]


def test_only_the_missing_hashes_are_uploaded(monkeypatch, tmp_path):
    build(tmp_path, {"index.html": "x", "second.html": "y"})
    wanted = pages.file_hash(b"y", "html")
    fake = FakeHTTP(missing=[wanted])
    deploy(monkeypatch, tmp_path, fake)
    uploaded = json.loads(fake.body("/assets/upload"))
    assert [entry["key"] for entry in uploaded] == [wanted]
    assert base64.b64decode(uploaded[0]["value"]) == b"y"
    assert uploaded[0]["metadata"]["contentType"].startswith("text/html")
    # ... and the manifest still names both, or the unchanged one would 404.
    assert (
        len(
            json.loads(
                fake.body("/deployments")
                .decode()
                .split('name="manifest"')[1]
                .split("\r\n\r\n")[1]
                .split("\r\n")[0]
            )
        )
        == 2
    )


def test_nothing_uploads_when_cloudflare_already_has_it_all(monkeypatch, tmp_path):
    build(tmp_path, {"index.html": "x"})
    fake = FakeHTTP(missing=[])
    deploy(monkeypatch, tmp_path, fake)
    assert fake.urls("/assets/upload") == []
    assert fake.urls("/deployments")  # the deployment is still created


def test_the_deployment_carries_the_manifest_the_branch_and_the_headers(monkeypatch, tmp_path):
    build(tmp_path, {"index.html": "x", "_headers": "/*\n  X-Frame-Options: DENY\n"})
    fake = FakeHTTP()
    deploy(monkeypatch, tmp_path, fake, branch="preview")
    body = fake.body("/deployments").decode()
    assert 'name="manifest"' in body and "/index.html" in body
    assert 'name="branch"\r\n\r\npreview' in body
    assert 'name="_headers"' in body and "X-Frame-Options" in body


def test_a_cloudflare_error_names_its_own_message(monkeypatch, tmp_path):
    build(tmp_path, {"index.html": "x"})

    def angry(url, **kwargs):
        return pages.http.Response(
            url=url,
            status=403,
            body=json.dumps({"success": False, "errors": [{"code": 10000, "message": "no permission"}]}),
        )

    monkeypatch.setattr(pages.http, "request", angry)
    with pytest.raises(MakeError, match="no permission"):
        pages.deploy(tmp_path, project="demo", account="acct", token="tok")


def test_dry_run_sends_nothing(tmp_path):
    build(tmp_path, {"index.html": "x"})
    with context(tmp_path, dry_run=True):
        # The real http module, which refuses to make a request under --dry-run:
        # a plan is printed and the fallback URL comes back.
        url = pages.deploy(tmp_path, project="demo", account="acct", token="tok")
    assert url == "https://main.demo.pages.dev"


# -- batching --------------------------------------------------------------


def test_uploads_are_batched_by_count_and_by_size(tmp_path):
    assets = [
        pages.Asset(
            key=f"/{i}.txt", file=tmp_path / f"{i}.txt", hash=f"h{i}", size=1, content_type="text/plain"
        )
        for i in range(pages.BATCH_FILES * 2 + 3)
    ]
    assert [len(b) for b in pages._batches(assets)] == [pages.BATCH_FILES, pages.BATCH_FILES, 3]

    fat = [
        pages.Asset(key="/a", file=tmp_path / "a", hash="a", size=pages.BATCH_BYTES, content_type="x"),
        pages.Asset(key="/b", file=tmp_path / "b", hash="b", size=pages.BATCH_BYTES, content_type="x"),
    ]
    assert [len(b) for b in pages._batches(fat)] == [1, 1]
