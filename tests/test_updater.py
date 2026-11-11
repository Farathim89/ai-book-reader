import hashlib
import io
import json
import zipfile

import pytest

from app import updater


def test_parse_version():
    assert updater.parse_version("v1.2.10") == (1, 2, 10)
    assert updater.parse_version("1.10") > updater.parse_version("1.9.9")
    assert updater.parse_version("2") == (2, 0, 0)


class FakeResp:
    def __init__(self, status=200, data=None, body=b""):
        self.status_code = status
        self._data = data
        self._body = body
        self.headers = {"Content-Length": str(len(body))}

    def json(self):
        return self._data

    def raise_for_status(self):
        pass

    def iter_content(self, n):
        for i in range(0, len(self._body), n):
            yield self._body[i:i + n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _zip_bytes(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


@pytest.fixture
def fake_github(monkeypatch, tmp_path):
    payload = _zip_bytes({updater.EXE_NAME: b"NEW-EXE", "_internal/assets/icon.png": b"PNG",
                          "_internal/runtime.json": json.dumps({"runtime": "abc"})})
    sha = hashlib.sha256(payload).hexdigest()
    release = {"tag_name": "v9.0.0", "body": "## Notes", "html_url": "https://example/rel",
               "assets": [{"name": "update.json", "browser_download_url": "u-json"},
                          {"name": "AiBookReader-app-9.0.0.zip", "browser_download_url": "u-zip",
                           "size": len(payload), "digest": f"sha256:{sha}"}]}
    manifest = {"version": "9.0.0", "runtime": "abc", "asset": "AiBookReader-app-9.0.0.zip", "sha256": sha}

    def get(url, headers=None, timeout=None, stream=False):
        if url == updater.API_LATEST:
            return FakeResp(data=release)
        if url == "u-json":
            return FakeResp(data=manifest)
        if url == "u-zip":
            return FakeResp(body=payload)
        raise AssertionError(url)
    monkeypatch.setattr(updater.requests, "get", get)
    monkeypatch.setenv("ABR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(updater, "local_runtime", lambda: "abc")
    return release, manifest, payload


def test_check_download_install(fake_github, tmp_path, monkeypatch):
    monkeypatch.setenv("ABR_VERSION_OVERRIDE", "1.1.0")
    rel = updater.check()
    assert rel and rel.version == "9.0.0" and rel.runtime_ok
    z = updater.download(rel)
    assert z.exists()
    app = tmp_path / "app"
    (app / "_internal").mkdir(parents=True)
    (app / updater.EXE_NAME).write_bytes(b"OLD-EXE")
    exe = updater.install(z, app)
    assert exe.read_bytes() == b"NEW-EXE"
    assert (app / (updater.EXE_NAME + ".old")).read_bytes() == b"OLD-EXE"
    assert (app / "_internal" / "assets" / "icon.png").read_bytes() == b"PNG"


def test_no_update_when_current(fake_github, monkeypatch):
    monkeypatch.setenv("ABR_VERSION_OVERRIDE", "9.0.0")
    assert updater.check() is None


def test_bad_fingerprint_is_rejected(fake_github, monkeypatch):
    release, manifest, payload = fake_github
    monkeypatch.setenv("ABR_VERSION_OVERRIDE", "1.0.0")
    rel = updater.check()
    rel.sha256 = "0" * 64
    with pytest.raises(updater.UpdateError, match="fingerprint"):
        updater.download(rel)


def test_runtime_change_needs_full_download(fake_github, monkeypatch):
    monkeypatch.setenv("ABR_VERSION_OVERRIDE", "1.0.0")
    monkeypatch.setattr(updater, "local_runtime", lambda: "something-else")
    assert not updater.check().runtime_ok
