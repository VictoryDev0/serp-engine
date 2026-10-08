"""Headful google-chrome under Xvfb, driven over raw DevTools protocol, through the SOCKS proxy."""
import json
import os
import signal
import subprocess
import time
import urllib.parse
import urllib.request

from _common import TMP, google_base, log

CHROME = os.environ.get("SERP_CHROME", "/usr/bin/google-chrome")
DISPLAY = os.environ.get("SERP_DISPLAY", ":78")
LANG = os.environ.get("SERP_CHROME_LANG", "en-US")
PORT = int(os.environ.get("SERP_CDP_PORT", "9333"))
PROFILE = os.environ.get("SERP_PROFILE", os.path.join(TMP, "chrome-profile-serp"))


class Chrome:
    def __init__(self, proxy="socks5://127.0.0.1:1080"):
        self.proxy = proxy
        self.xvfb = None
        self.proc = None
        self.ws = None
        self.mid = 0
        self.tab = None

    def start(self):
        from websocket import create_connection
        disp = DISPLAY.lstrip(":")
        if not os.path.exists(f"/tmp/.X11-unix/X{disp}"):
            self.xvfb = subprocess.Popen(["Xvfb", DISPLAY, "-screen", "0", "1366x900x24", "-ac"],
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            time.sleep(1.5)
        subprocess.run(["pkill", "-f", f"user-data-dir={PROFILE}"], capture_output=True)
        args = [CHROME, f"--user-data-dir={PROFILE}", f"--proxy-server={self.proxy}", f"--lang={LANG}",
                "--window-size=1366,900", "--no-first-run", "--no-default-browser-check", "--disable-gpu",
                "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled", "--no-sandbox",
                f"--remote-debugging-port={PORT}", "--remote-allow-origins=*", "about:blank"]
        env = dict(os.environ, DISPLAY=DISPLAY)
        self.proc = subprocess.Popen(args, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        for _ in range(30):
            time.sleep(1)
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/version", timeout=2).read()
                break
            except Exception:  # noqa: BLE001
                continue
        else:
            raise RuntimeError("Chrome did not open the CDP port")
        self.tab = json.loads(urllib.request.urlopen(urllib.request.Request(
            f"http://127.0.0.1:{PORT}/json/new?about:blank", method="PUT"), timeout=5).read())
        self.ws = create_connection(self.tab["webSocketDebuggerUrl"], suppress_origin=True, timeout=60)
        self.send("Page.enable")
        self.send("Network.enable")
        log(f"chrome started (pid {self.proc.pid}, display {DISPLAY})")
        return self

    def send(self, method, params=None):
        self.mid += 1
        self.ws.send(json.dumps({"id": self.mid, "method": method, "params": params or {}}))
        while True:
            r = json.loads(self.ws.recv())
            if r.get("id") == self.mid:
                return r

    def eval(self, expr):
        r = self.send("Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": True})
        return r.get("result", {}).get("result", {}).get("value")

    def navigate(self, url, settle=6.0):
        self.send("Page.navigate", {"url": url})
        time.sleep(settle)
        return self.eval("location.href")

    def wait_text_stable(self, max_wait=25, quiet=3):
        last, stable = -1, 0
        for _ in range(int(max_wait)):
            n = self.eval("document.body ? document.body.innerText.length : 0") or 0
            stable = stable + 1 if n == last else 0
            last = n
            if stable >= quiet:
                break
            time.sleep(1)

    def html(self):
        return self.eval("document.documentElement.outerHTML") or ""

    def screenshot(self, path):
        r = self.send("Page.captureScreenshot", {"format": "png"})
        data = r.get("result", {}).get("data")
        if data:
            import base64
            with open(path, "wb") as f:
                f.write(base64.b64decode(data))
            return path
        return None

    def cookies(self, url=None):
        r = self.send("Network.getCookies", {"urls": [url or google_base()]})
        return r.get("result", {}).get("cookies", [])

    def stop(self):
        try:
            if self.ws:
                self.ws.close()
        except Exception:  # noqa: BLE001
            pass
        for p in (self.proc, self.xvfb):
            if p:
                try:
                    os.killpg(os.getpgid(p.pid), signal.SIGTERM)
                except Exception:  # noqa: BLE001
                    pass
        time.sleep(1)
        subprocess.run(["pkill", "-9", "-f", f"user-data-dir={PROFILE}"], capture_output=True)
        log("chrome stopped")
