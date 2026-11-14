"""Read note pages in a dedicated Edge profile that keeps its own login state.

The profile lives under the runtime directory, so the browser used here is never the
profile someone browses with. Log in once with `collector.py login`; afterwards every
fetch reuses the stored session cookies.

Chromium's headless mode is refused by Xiaohongshu's risk control, so the window is
headed but parked far outside the visible desktop.
"""

import os
import shutil
import threading
import time
from pathlib import Path

from public_page import platform_for

CHANNEL = "msedge"
LAUNCH_ARGS = [
    "--no-first-run", "--no-default-browser-check",
    "--disable-blink-features=AutomationControlled",
    "--lang=zh-CN",
]
OFFSCREEN_ARGS = [
    "--window-position=-32000,-32000", "--window-size=1440,900",
    "--disable-backgrounding-occluded-windows", "--disable-renderer-backgrounding",
]
LOGIN_TIMEOUT = 300
DOUYIN_HOME = "https://www.douyin.com/"

READY_JS = (
    "() => !!document.querySelector('#detail-desc, #detail-title, .note-content,"
    " .note-scroller, [data-e2e=\"video-desc\"], #noteContainer')"
)
DOM_JS = r"""() => {
  const text = (selector) => {
    const node = document.querySelector(selector);
    return node ? (node.innerText || '').trim() : '';
  };
  const unique = (values) => Array.from(new Set(values.filter((value) => !!value)));
  const images = unique(Array.from(document.querySelectorAll(
    '#noteContainer img, .swiper-slide img, .note-slider img, .media-container img, [data-e2e="image-item"] img'
  )).map((node) => node.currentSrc || node.src || '')
    .filter((src) => /^https:\/\//.test(src))).slice(0, 64);
  const videos = unique(Array.from(document.querySelectorAll('video'))
    .map((node) => node.currentSrc || node.src || '')
    .filter((src) => /^https:\/\//.test(src))).slice(0, 4);
  let state = null;
  try {
    const raw = window.__INITIAL_STATE__ === undefined ? window._ROUTER_DATA : window.__INITIAL_STATE__;
    if (raw) {
      const encoded = JSON.stringify(raw);
      if (encoded && encoded.length <= 4000000) {
        state = JSON.parse(encoded);
      }
    }
  } catch (error) {
    state = null;
  }
  return {
    title: text('#detail-title') || (document.title || '').replace(/ ?- 小红书$/, ''),
    desc: text('#detail-desc') || text('.note-text') || text('[data-e2e="video-desc"]'),
    author: text('.author-wrapper .name') || text('.username') || text('.user-name'),
    images: images,
    videos: videos,
    blocked: !!document.querySelector('.login-container, .login-modal, [class*="login-modal"], #login-btn'),
    state: state,
  };
}"""


class BrowserError(RuntimeError):
    pass


class Reader:
    """One-shot Edge session per read; releasing the profile keeps `login` usable."""

    def __init__(self, root, channel=CHANNEL, wait=8):
        self.root = Path(root)
        self.profile = self.root / "edge-profile"
        self.douyin_profile = self.root / "edge-profile-douyin"
        self.channel = channel
        self.wait = wait
        self.lock = threading.Lock()
        self._ready = None
        self._checked = 0.0

    def ready(self, platform="小红书", ttl=600):
        """Cache the session check; a stale result would start Edge for every capture."""
        if platform == "抖音":
            return self.installed()
        now = time.monotonic()
        if self._ready is not None and now - self._checked < ttl:
            return self._ready
        self._ready, self._checked = self.logged_in(), now
        return self._ready

    @staticmethod
    def installed():
        try:
            import playwright.sync_api  # noqa: F401
        except ImportError:
            return False
        return True

    def _launch(self, playwright, profile, visible):
        args = LAUNCH_ARGS if visible else LAUNCH_ARGS + OFFSCREEN_ARGS
        options = dict(user_data_dir=str(profile), headless=False, args=args,
                       locale="zh-CN", timezone_id="Asia/Shanghai", accept_downloads=False,
                       viewport={"width": 1440, "height": 900})
        try:
            return playwright.chromium.launch_persistent_context(channel=self.channel, **options)
        except Exception as exc:
            text = str(exc)
            if "distribution" in text or "Executable doesn't exist" in text or "channel" in text:
                return playwright.chromium.launch_persistent_context(**options)
            raise

    def _open(self, visible=False, platform="小红书"):
        profile = self.douyin_profile if platform == "抖音" else self.profile
        profile.mkdir(parents=True, exist_ok=True)
        playwright = self._start()
        try:
            context = self._launch(playwright, profile, visible)
        except Exception as exc:
            playwright.stop()
            text = str(exc)
            if "ProcessSingleton" in text or "already in use" in text or "Target page" in text:
                raise BrowserError("登录配置目录正被占用，请等接收端空闲后重试") from exc
            raise BrowserError(f"无法启动浏览器（{type(exc).__name__}）") from exc
        return playwright, context

    @staticmethod
    def _driver_fallback():
        """Some machines refuse to run the bundled driver from the project folder."""
        import playwright

        source = Path(playwright.__file__).parent / "driver" / "node.exe"
        target = Path(os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP", ".")) / "ResearchCollector" / "node.exe"
        if not source.is_file():
            return ""
        if not target.is_file() or target.stat().st_size != source.stat().st_size:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        return str(target)

    def _isolate_temp(self):
        """Point the driver's scratch space inside the runtime directory.

        A window-less host (pythonw, service, scheduled task) can be refused when the
        driver creates `playwright-artifacts-*` under the user's %TEMP%, which surfaces
        as `EPERM ... mkdtemp`. A directory the receiver owns avoids that entirely.
        """
        folder = self.root / "tmp"
        folder.mkdir(parents=True, exist_ok=True)
        for name in ("TEMP", "TMP", "TMPDIR"):
            os.environ[name] = str(folder)

    def _start(self):
        from playwright.sync_api import sync_playwright

        self._isolate_temp()
        try:
            return sync_playwright().start()
        except Exception as exc:
            if os.environ.get("PLAYWRIGHT_NODEJS_PATH"):
                raise
            driver = self._driver_fallback()
            if not driver:
                raise
            os.environ["PLAYWRIGHT_NODEJS_PATH"] = driver
            return sync_playwright().start()

    def logged_in(self):
        """A stored session cookie is what makes the note page return the author body."""
        if not self.installed():
            return False
        with self.lock:
            playwright, context = self._open()
            try:
                cookies = context.cookies("https://www.xiaohongshu.com")
            except Exception:
                return False
            finally:
                context.close()
                playwright.stop()
        return any(cookie["name"] == "web_session" and cookie["value"] for cookie in cookies)

    def read(self, url, wait=None):
        """Render one page and return its HTML, final URL and a tolerant DOM summary."""
        with self.lock:
            playwright, context = self._open(platform=platform_for(url))
            try:
                page = context.new_page()
                try:
                    response = page.goto(url, wait_until="commit", timeout=12000)
                    try:
                        page.wait_for_function(READY_JS, timeout=(wait or self.wait) * 1000)
                    except Exception:
                        pass
                    html = page.content()
                    final = page.url
                    dom = page.evaluate(DOM_JS)
                    redirects = []
                    request = response.request if response else None
                    while request:
                        redirects.append(request.url)
                        request = request.redirected_from
                finally:
                    page.close()
            finally:
                context.close()
                playwright.stop()
        return {"html": html, "final": final, "dom": dom or {}, "redirects": redirects[::-1]}

    def cookie_header(self, url, platform="小红书"):
        with self.lock:
            playwright, context = self._open(platform=platform)
            try:
                cookies = context.cookies(url)
            except Exception:
                return ""
            finally:
                context.close()
                playwright.stop()
        return "; ".join(f'{cookie["name"]}={cookie["value"]}' for cookie in cookies)

    def login(self, url="https://www.xiaohongshu.com/explore", timeout=LOGIN_TIMEOUT, platform="小红书"):
        """Persist a visible browser session for the selected platform."""
        from playwright.sync_api import TimeoutError as PlaywrightTimeout

        with self.lock:
            playwright, context = self._open(True, platform)
            closed = threading.Event()
            context.on("close", lambda _: closed.set())
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                if platform == "抖音":
                    try:
                        page.wait_for_event("close", timeout=timeout * 1000)
                    except PlaywrightTimeout:
                        return False
                    except Exception:
                        if not closed.is_set() and not page.is_closed():
                            raise
                    return True
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    cookies = context.cookies("https://www.xiaohongshu.com")
                    if any(cookie["name"] == "web_session" and cookie["value"] for cookie in cookies):
                        return True
                    time.sleep(1.5)
                return False
            finally:
                if not closed.is_set():
                    context.close()
                playwright.stop()
