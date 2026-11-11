"""Check a built Mac package without opening its WebView or using developer runtimes.

Run with the build environment's Python. All application work runs in the frozen
sidecar with a minimal PATH, an unrelated working directory, and isolated data.
"""
from __future__ import annotations

import http.cookiejar
import json
import os
import select
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image


def check_session(app: Path, data: Path, cwd: Path, source: Path | None) -> None:
    sidecar = app / "Contents/Resources/sidecar/memesort-sidecar-aarch64-apple-darwin"
    env = {key: value for key, value in os.environ.items() if not key.startswith(("MEMESORT_", "PYTHON"))}
    env["PATH"] = "/usr/bin:/bin"
    with (cwd / "sidecar-stderr.log").open("w") as errors:
        process = subprocess.Popen(
            [str(sidecar), "--data-root", str(data)], cwd=cwd, env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors,
            text=True, encoding="utf-8",
        )
        try:
            assert process.stdout is not None and process.stdin is not None
            assert select.select([process.stdout], [], [], 30)[0], "sidecar handshake timed out"
            handshake = json.loads(process.stdout.readline())
            assert handshake["protocol_version"] == 1
            assert Path(handshake["library_root"]) == data / "library"
            origin = handshake["origin"]
            assert origin.startswith("http://127.0.0.1:")
            try:
                urllib.request.urlopen(origin + "/api/state", timeout=5)
                raise AssertionError("unauthenticated state was exposed")
            except urllib.error.HTTPError as error:
                assert error.code == 401
            opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
            with opener.open(handshake["bootstrap_url"], timeout=5) as response:
                state = json.load(response)
            assert state["setup"]["missing"] == ["semantic runtime", "OCR environment"]
            assert state["library_status"]["total_assets"] == (0 if source else 1)

            def request(path: str, payload: dict | None = None):
                body = None if payload is None else json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(origin + path, data=body, headers={"Origin": origin, "Content-Type": "application/json"})
                with opener.open(req, timeout=10) as response:
                    return json.load(response)

            if source:
                request("/api/import/start", {"sources": [str(source)], "indexing_policy": "never"})
                deadline = time.monotonic() + 15
                while True:
                    imported = request("/api/import")
                    if not imported["running"]:
                        assert imported["status"] == "completed", imported
                        assert imported["succeeded_files"] == 1, imported
                        break
                    assert time.monotonic() < deadline, "import timed out"
                    time.sleep(0.05)
            assets = request("/api/assets")["assets"]
            assert len(assets) == 1
            managed = (data / "library" / assets[0]["library_path"]).resolve()
            assert managed.is_relative_to(data / "library") and managed.is_file()
            if source:
                assert managed.read_bytes() == source.read_bytes()
            with opener.open(origin + assets[0]["library_url"], timeout=5) as response:
                assert response.read() == managed.read_bytes()
            if source:
                process.stdin.write('{"command":"shutdown"}\n')
                process.stdin.flush()
            process.stdin.close()  # The second session exercises parent EOF.
            assert process.wait(timeout=20) == 0
            assert not process.stdout.read(), "sidecar emitted more than its handshake"
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
    assert "sidecar_stopped clean=True" in (data / "library/logs/sidecar.log").read_text()


def main() -> None:
    app = Path(sys.argv[1]).resolve()
    assert app.is_dir(), f"Missing app bundle: {app}"
    assert (app / "Contents/Resources/sidecar/setup_macos_runtime.sh").is_file(), "Missing bundled Mac runtime setup launcher"
    subprocess.run(["/usr/bin/lipo", "-verify_arch", "arm64", str(app / "Contents/MacOS/memesort-desktop")], check=True)
    subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)], check=True)
    with tempfile.TemporaryDirectory(prefix="MemeSort package 中文 ") as temporary:
        root = Path(temporary).resolve()
        moved = root / "Moved app 中文.app"
        shutil.copytree(app, moved, symlinks=True)
        data = root / "Application Support/MemeSort"
        cwd = root / "Unrelated working directory"
        cwd.mkdir()
        source = root / "Source 图片.png"
        Image.new("RGB", (3, 2), (200, 30, 100)).save(source)
        check_session(moved, data, cwd, source)
        source.unlink()
        replacement = root / "Replacement app.app"
        moved.rename(replacement)
        check_session(replacement, data, cwd, None)
    print("Mac package check passed: arm64/signature, frozen sidecar, isolated paths, authenticated import/media, source removal, relocation, shutdown and EOF relaunch.")
    print("Not checked: WebView, native window controls/focus, Finder launch/paste, or native single-instance behavior.")


if __name__ == "__main__":
    main()
