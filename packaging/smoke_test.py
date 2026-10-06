"""Smoke-test a frozen KherveLAB: start it every way it can start.

    python packaging/smoke_test.py dist/KherveLAB/KherveLAB.exe --version 0.28.N
    python packaging/smoke_test.py dist/KherveLAB.app/Contents/MacOS/KherveLAB

A freeze fails quietly: a module or data file PyInstaller's analysis missed
is only found when the code that needs it runs. So, against a throw-away
data folder, this drives the real executable through:

1. ``--serve`` (headless web pages, waitress): the first-run /setup page
   creates a made-up lab (manager + the example instruments), the static
   calendar script is served, and the templates render;
2. ``--mcp-server --as <manager>``: the version it reports is the stamped
   ``<major>.<minor>.<n>`` (not the ``.0`` fallback), the tools are listed,
   and a booking plus the finance report run (logic, analytics);
3. the web exports, logged in as that manager: lab report PDF and Excel,
   statement PDF and Excel, CSV (reportlab, openpyxl, charts);
   (with ``--server-exe``, also the Windows console ``KherveLAB-server.exe``:
   it must print the address and serve /login);
4. the desktop app on Qt's offscreen platform: it must still be running
   after a few seconds, i.e. PyQt6 and the GUI modules load.

Nothing touches the user's own ~/KherveLAB-data. Exits non-zero on the
first failure.

Copyright (C) 2026 Gwilherm Kerherve
Licensed under the GNU General Public License v3.0 or later (see LICENSE).
"""

import argparse
import http.cookiejar
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path


def _fail(message: str):
    print(f"SMOKE TEST FAILED: {message}", flush=True)
    raise SystemExit(1)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Web:
    def __init__(self, base: str):
        self.base = base
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def get(self, path: str):
        with self.opener.open(self.base + path, timeout=60) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read()

    def post(self, path: str, fields: dict):
        data = urllib.parse.urlencode(fields).encode()
        with self.opener.open(self.base + path, data=data, timeout=60) as r:
            return r.status, r.geturl(), r.read()


def _csrf(html: bytes) -> str:
    m = re.search(rb'name="csrf" value="([^"]+)"', html)
    if not m:
        _fail("no CSRF token on the page")
    return m.group(1).decode()


class Mcp:
    def __init__(self, exe, data, user, env):
        self.proc = subprocess.Popen([exe, "--mcp-server", "--data", str(data), "--as", user],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, env=env)
        self._id = 0

    def request(self, method, params=None):
        self._id += 1
        self.proc.stdin.write((json.dumps({"jsonrpc": "2.0", "id": self._id, "method": method,
                                           "params": params or {}}) + "\n").encode())
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        if not line:
            err = self.proc.stderr.read().decode(errors="replace")[-2000:]
            _fail(f"the MCP server closed while waiting for {method}: {err}")
        reply = json.loads(line)
        if "error" in reply:
            _fail(f"{method}: {reply['error']}")
        return reply["result"]

    def call(self, tool, **arguments):
        res = self.request("tools/call", {"name": tool, "arguments": arguments})
        text = res["content"][0]["text"]
        if res.get("isError"):
            _fail(f"{tool}({arguments}) -> {text}")
        return json.loads(text)

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            self.proc.kill()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("exe", help="the frozen executable")
    parser.add_argument("--version", help="the <major>.<minor>.<n> it must report")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--server-exe", help="Windows: the console KherveLAB-server.exe; it "
                        "must print the address and serve the pages")
    args = parser.parse_args()

    exe = str(Path(args.exe).resolve())
    if not Path(exe).is_file():
        _fail(f"{exe} not found")
    work = Path(tempfile.mkdtemp(prefix="klab_smoke_"))
    data = work / "data"
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", HOME=str(work),
               USERPROFILE=str(work))
    watchdog = threading.Timer(args.timeout, lambda: os._exit(2))
    watchdog.daemon = True
    watchdog.start()
    procs = []
    try:
        # 1. headless web pages -------------------------------------------------
        port = _free_port()
        serve = subprocess.Popen([exe, "--serve", "--data", str(data), "--host", "127.0.0.1",
                                  "--port", str(port)], env=env,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        procs.append(serve)
        web = Web(f"http://127.0.0.1:{port}")
        deadline = time.time() + 120
        while True:
            if serve.poll() is not None:
                _fail(f"--serve exited with {serve.returncode}: "
                      f"{serve.stdout.read().decode(errors='replace')[-2000:]}")
            try:
                status, _, page = web.get("/setup")
                break
            except OSError:
                if time.time() > deadline:
                    _fail("--serve did not answer within 120 s")
                time.sleep(0.5)
        print(f"--serve is up on port {port}", flush=True)
        password = secrets.token_urlsafe(12)
        fields = {"csrf": _csrf(page), "lab_name": "Smoke Test Lab", "currency": "£",
                  "username": "manager", "full_name": "Test Manager",
                  "email": "manager@example.org", "password": password,
                  "password2": password, "ex_XPS": "on", "ex_BET": "on"}
        status, url, _ = web.post("/setup", fields)
        if "/admin/instruments" not in url:
            _fail(f"/setup did not create the lab (ended at {url})")
        print("created a made-up lab through /setup", flush=True)
        status, ctype, js = web.get("/static/khervecal.js")
        if status != 200 or len(js) < 1000:
            _fail("static/khervecal.js was not served (static files not bundled)")

        # 2. MCP ----------------------------------------------------------------
        mcp = Mcp(exe, data, "manager", env)
        procs.append(mcp.proc)
        info = mcp.request("initialize", {"protocolVersion": "2025-06-18"})
        version = info["serverInfo"]["version"]
        print(f"version reported: {version}", flush=True)
        if re.fullmatch(r"\d+\.\d+\.0", version) or not re.fullmatch(r"\d+\.\d+\.\d+", version):
            _fail(f"the frozen app reports {version!r}: khervelab/VERSION was not bundled")
        if args.version and version != args.version:
            _fail(f"expected version {args.version}, the app reports {version}")
        tools = {t["name"] for t in mcp.request("tools/list")["tools"]}
        for needed in ("lab_overview", "book", "finance_report", "import_ppms"):
            if needed not in tools:
                _fail(f"tool {needed} missing from tools/list")
        overview = mcp.call("lab_overview")
        names = [i["name"] for i in overview["instruments"]]
        if "XPS" not in names:
            _fail(f"lab_overview instruments: {names}")
        day = date.today() + timedelta(days=1)
        mcp.call("book", instrument="XPS", user="manager", start=f"{day}T10:00",
                 end=f"{day}T12:00", purpose="smoke test")
        mcp.call("finance_report", **{"from": str(day), "to": str(day)})
        mcp.close()
        print("MCP: overview, booking, finance report", flush=True)

        # 3. exports --------------------------------------------------------------
        q = f"from={day}&to={day}"
        for fmt, magic in (("lab-pdf", b"%PDF"), ("pdf", b"%PDF"), ("lab-xlsx", b"PK"),
                           ("xlsx", b"PK"), ("csv", b"")):
            status, ctype, body = web.get(f"/admin/reports/export?{q}&format={fmt}")
            if status != 200 or not body.startswith(magic) or len(body) < 50:
                _fail(f"export {fmt}: HTTP {status}, {len(body)} bytes")
            print(f"export {fmt:9} {len(body):>8} bytes", flush=True)
        status, _, page = web.get("/admin/reports")
        if b"<svg" not in page:
            _fail("the reports page has no charts")
        serve.terminate()

        # 3b. the console web server (Windows) ----------------------------------
        if args.server_exe:
            sport = _free_port()
            srv = subprocess.Popen([str(Path(args.server_exe).resolve()), "--data", str(data),
                                    "--host", "127.0.0.1", "--port", str(sport)], env=env,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            procs.append(srv)
            lines = []
            while not any("on this PC" in ln for ln in lines):
                line = srv.stdout.readline().decode(errors="replace")
                if not line:
                    _fail(f"KherveLAB-server.exe printed no address: {lines}")
                lines.append(line.rstrip())
            print("server exe says: " + " | ".join(lines), flush=True)
            if f"localhost:{sport}" not in "".join(lines):
                _fail("KherveLAB-server.exe printed the wrong address")
            for _ in range(60):                 # it prints just before it listens
                try:
                    status, _, page = Web(f"http://127.0.0.1:{sport}").get("/login")
                    break
                except OSError:
                    time.sleep(0.5)
            else:
                _fail("KherveLAB-server.exe never answered")
            if status != 200 or b"csrf" not in page:
                _fail("KherveLAB-server.exe did not serve /login")
            srv.terminate()
            print("KherveLAB-server.exe prints its address and serves", flush=True)

        # 4. the desktop app ------------------------------------------------------
        gui = subprocess.Popen([exe, "--data", str(data)], env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        procs.append(gui)
        time.sleep(12)
        if gui.poll() is not None:
            _fail(f"the desktop app exited with {gui.returncode}: "
                  f"{gui.stdout.read().decode(errors='replace')[-2000:]}")
        print("desktop app is running (offscreen)", flush=True)
    finally:
        watchdog.cancel()
        for p in procs:
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    p.kill()
        shutil.rmtree(work, ignore_errors=True)
    print("SMOKE TEST PASSED", flush=True)


if __name__ == "__main__":
    main()
