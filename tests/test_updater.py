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


# ---------------------------------------------------------------------------- small portable (lite)
LITE_FILES = {updater.EXE_NAME: b"NEW-LAUNCHER", "AiBookReader.pyw": b"pyw", "app/main.py": b"main",
              "app/new_module.py": b"new", "assets/icon.png": b"PNG2", "runtime/requirements.txt": b"kokoro"}


@pytest.fixture
def fake_lite_release(monkeypatch, tmp_path):
    portable = _zip_bytes({"Ai Book Reader/x": b"big"})
    src = _zip_bytes(LITE_FILES)
    release = {"tag_name": "v9.1.0", "body": "", "html_url": "https://example/rel",
               "assets": [{"name": "update.json", "browser_download_url": "u-json"},
                          {"name": "AiBookReader-Portable-9.1.0.zip", "browser_download_url": "u-portable",
                           "size": len(portable)},
                          {"name": "AiBookReader-src-9.1.0.zip", "browser_download_url": "u-src", "size": len(src),
                           "digest": "sha256:" + hashlib.sha256(src).hexdigest()}]}
    manifest = {"version": "9.1.0", "runtime": "lite", "asset": "AiBookReader-Portable-9.1.0.zip",
                "sha256": hashlib.sha256(portable).hexdigest(),
                "lite": {"asset": "AiBookReader-src-9.1.0.zip", "sha256": hashlib.sha256(src).hexdigest(),
                         "requirements": "req-new"}}

    def get(url, headers=None, timeout=None, stream=False):
        return {updater.API_LATEST: lambda: FakeResp(data=release), "u-json": lambda: FakeResp(data=manifest),
                "u-src": lambda: FakeResp(body=src), "u-portable": lambda: FakeResp(body=portable)}[url]()
    monkeypatch.setattr(updater.requests, "get", get)
    monkeypatch.setenv("ABR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ABR_VERSION_OVERRIDE", "1.2.0")
    return manifest


def test_lite_client_gets_the_small_package(fake_lite_release, monkeypatch):
    from app import setup_runtime
    monkeypatch.setattr(updater, "local_runtime", lambda: "")
    monkeypatch.setattr(setup_runtime, "installed", lambda: {"requirements": "req-old"})
    rel = updater.check(lite=True)
    assert rel.zip_name == "AiBookReader-src-9.1.0.zip" and rel.runtime_ok and rel.needs_setup
    monkeypatch.setattr(setup_runtime, "installed", lambda: {"requirements": "req-new"})
    assert not updater.check(lite=True).needs_setup


def test_old_one_exe_build_is_sent_to_the_download_page(fake_lite_release, monkeypatch):
    monkeypatch.setattr(updater, "local_runtime", lambda: "84102c141dfd0d1e")
    rel = updater.check(lite=False)
    assert rel.zip_name == "AiBookReader-Portable-9.1.0.zip" and not rel.runtime_ok


def test_lite_client_and_a_release_without_lite_package(fake_lite_release, monkeypatch):
    del fake_lite_release["lite"]
    monkeypatch.setattr(updater, "local_runtime", lambda: "")
    rel = updater.check(lite=True)
    assert rel.full_download and not rel.runtime_ok


def test_lite_install_swaps_the_app_folders(fake_lite_release, tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "local_runtime", lambda: "")
    z = updater.download(updater.check(lite=True))
    target = tmp_path / "portable"
    (target / "app").mkdir(parents=True)
    (target / "app" / "main.py").write_bytes(b"old main")
    (target / "app" / "removed_module.py").write_bytes(b"gone")
    (target / "data").mkdir()
    (target / "data" / "library.db").write_bytes(b"DB")
    (target / "python").mkdir()
    (target / updater.EXE_NAME).write_bytes(b"OLD-LAUNCHER")
    exe = updater.install(z, target)
    assert exe.read_bytes() == b"NEW-LAUNCHER"
    assert (target / "app" / "main.py").read_bytes() == b"main"
    assert (target / "app" / "new_module.py").exists() and not (target / "app" / "removed_module.py").exists()
    assert (target / "app.old" / "removed_module.py").exists()
    assert (target / "data" / "library.db").read_bytes() == b"DB" and (target / "python").is_dir()
    assert (target / "runtime" / "requirements.txt").read_bytes() == b"kokoro"
    assert not (target / ".update-new").exists()
    monkeypatch.setattr(updater, "can_install", lambda: True)
    monkeypatch.setattr(updater, "app_dir", lambda: target)
    updater.cleanup_old()
    assert not (target / "app.old").exists() and not (target / (updater.EXE_NAME + ".old")).exists()
    assert (target / "app" / "main.py").exists()


def test_install_rejects_paths_outside_the_folder(tmp_path):
    bad = tmp_path / "bad.zip"
    bad.write_bytes(_zip_bytes({updater.EXE_NAME: b"x", "app/main.py": b"m", "../evil.txt": b"e"}))
    target = tmp_path / "t"
    target.mkdir()
    updater.install(bad, target)
    assert not (tmp_path / "evil.txt").exists()
