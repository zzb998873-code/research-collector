"""Read the public share page, with explicit coverage for previews and missing media."""

import base64
import http.client
import ipaddress
import json
import os
import re
import socket
import ssl
import subprocess
from contextlib import closing
from urllib.parse import parse_qs, quote, unquote, urlencode, urljoin, urlsplit, urlunsplit
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from xml.etree import ElementTree

from bs4 import BeautifulSoup

from storage import MAX_FILE

PLATFORMS = {
    "小红书": ("xiaohongshu.com", "xhslink.com", "xhslink.cn", "rednote.com"),
    "抖音": ("douyin.com", "iesdouyin.com"),
}
CDNS = ("xhscdn.com", "rednotecdn.com", "douyinvod.com", "douyinpic.com", "douyinstatic.com", "byteimg.com")
FAKE_IP_PROXY_RANGES = (ipaddress.ip_network("198.18.0.0/15"), ipaddress.ip_network("2001:2::/48"))
XHS_SHORT_HOSTS = {"xhslink.cn", "xhslink.com", "www.xhslink.cn", "www.xhslink.com"}
URL_PATTERN = re.compile(r'https?://[^\s<>"\u3000，。！、；（）【】]+', re.I)
DOUYIN_SHARE = re.compile(r"复制\s*(?:此链接\s*)?打开\s*抖音")
IMAGE_TYPES = {
    "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif",
    "image/heic": ".heic", "image/heif": ".heif", "image/bmp": ".bmp",
}
VIDEO_TYPES = {"video/mp4": ".mp4", "video/quicktime": ".mov", "video/webm": ".webm"}


def matches(host, domain):
    return host == domain or host.endswith("." + domain)


def platform_for(url):
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        return ""
    for platform, domains in PLATFORMS.items():
        if any(matches(host, domain) for domain in domains):
            return platform
    return ""


def find_url(text):
    urls = [m.group().rstrip(").,;]") for m in URL_PATTERN.finditer(text)]
    return next((url for url in urls if platform_for(url)), urls[0] if urls else "")


def identity(url):
    """Use a stable work URL when known. Keep opaque links and their queries intact."""
    try:
        p = urlsplit(url)
        if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password:
            return ""
        platform = platform_for(url)
        if platform == "小红书":
            match = re.fullmatch(r"/(?:[a-z]{2}(?:-[a-z]{2})?/)?(?:explore|discovery/item|user/profile/[\da-f]+)/([\da-f]{24})/?", p.path, re.I)
            if match:
                return "https://www.xiaohongshu.com/explore/" + match[1].lower()
        if platform == "抖音":
            match = re.search(r"/(video|note|slides)/([0-9]{10,})(?:/|$)", p.path)
            if match:
                return "https://www.douyin.com/" + ("video" if match[1] == "video" else "note") + "/" + match[2]
            match = re.search(r"/share/(?:video|note)/([0-9]{10,})(?:/|$)", p.path)
            if match:
                kind = "note" if "/share/note/" in p.path else "video"
                return f"https://www.douyin.com/{kind}/{match[1]}"
        return urlunsplit((p.scheme, p.netloc.lower(), p.path.rstrip("/") or "/", p.query, ""))
    except ValueError:
        return ""


def aliases_for(*urls):
    aliases = set()
    for url in urls:
        if normalized := identity(url):
            aliases.add(normalized)
            if normalized.startswith(("https://www.douyin.com/note/", "https://www.douyin.com/video/")):
                parts = urlsplit(normalized).path.split("/")
                if len(parts) == 3 and parts[-1].isdigit():
                    aliases.add("douyin:" + parts[-1])
    return sorted(aliases)


def permitted(url, media=False):
    try:
        p = urlsplit(url)
        return (p.scheme == "https" and p.port in (None, 443) and not p.username and not p.password
                and bool(platform_for(url) or (media and any(matches(p.hostname or "", d) for d in CDNS))))
    except ValueError:
        return False


class Redirects(HTTPRedirectHandler):
    def __init__(self, media, destinations=None):
        self.media = media
        self.destinations = destinations

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if newurl.startswith("http://"):
            newurl = "https://" + newurl[7:]
        check_public(newurl, self.media)
        if self.destinations is not None:
            self.destinations.append(newurl)
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected and urlsplit(req.full_url).hostname != urlsplit(newurl).hostname:
            redirected.remove_header("Cookie")
        return redirected


def check_public(url, media):
    if not permitted(url, media):
        raise ValueError("链接超出支持的 HTTPS 域名")
    # A platform hostname pointing at a private address must not reach a local service.
    host = urlsplit(url).hostname
    addresses = {entry[4][0] for entry in socket.getaddrinfo(host, 443)}
    def routed(address):
        ip = ipaddress.ip_address(address)
        return ip.is_global or any(ip in network for network in FAKE_IP_PROXY_RANGES)
    # TUN DNS proxies may synthesize addresses in this range. HTTPS still verifies the platform hostname.
    if not addresses or any(not routed(addr) for addr in addresses):
        raise ValueError("公开域名未解析到公网")


def open_public(url, media=False, destinations=None, cookie=""):
    check_public(url, media)
    headers = {"User-Agent": "Mozilla/5.0", "Accept-Language": "zh-CN,zh;q=0.9"}
    if cookie:
        headers["Cookie"] = cookie
    request = Request(url, headers=headers)
    return build_opener(ProxyHandler({}), Redirects(media, destinations)).open(request, timeout=12 if media else 8)


def clean(text):
    return text.strip() if isinstance(text, str) else ""


def share_title(text, platform):
    title = URL_PATTERN.split(text, maxsplit=1)[0].strip()
    if platform == "抖音" and DOUYIN_SHARE.search(title):
        match = re.search(r"【[^】]+的作品】[：:\s]*(.+)", title)
        title = match[1] if match else ""
    return re.split(r"…|\.{3,}", title, maxsplit=1)[0].strip()


def candidate(url, kind, base):
    if not isinstance(url, str) or not url.strip():
        return None
    url = urljoin(base, url.strip())
    if url.startswith("http://"):
        # The note payload still lists http CDN links; the CDN serves them over https.
        url = "https://" + url[7:]
    if permitted(url, True):
        return {"url": url, "kind": kind}
    return None


def first_candidate(urls, kind, base):
    if not isinstance(urls, (list, tuple)):
        return None
    for url in urls:
        if result := candidate(url, kind, base):
            return result
    return None


def embedded_objects(soup):
    for script in soup.find_all("script"):
        source = script.string or ""
        if script.get("id") == "RENDER_DATA" or script.get("type") == "application/ld+json":
            try:
                yield json.loads(unquote(source) if script.get("id") == "RENDER_DATA" else source)
            except ValueError:
                continue
        elif re.match(r"\s*(?:window\.)?(?:__INITIAL_STATE__|_ROUTER_DATA|__RENDER_DATA__)\s*=", source):
            expression = source.split("=", 1)[1].lstrip()
            expression = re.sub(r'("(?:[^"\\]|\\.)*")|\bundefined\b', lambda m: m[1] or "null", expression)
            try:
                yield json.JSONDecoder().raw_decode(expression)[0]
            except ValueError:
                continue


def walk(value):
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            yield item
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)


def note_image(image, base):
    if not isinstance(image, dict):
        return None
    urls = [image.get("urlDefault"), image.get("urlPre"), image.get("url")]
    urls.extend(item.get("url") for item in image.get("infoList") or [] if isinstance(item, dict))
    urls.extend(image.get("url_list") or [])
    return first_candidate(urls, "image", base)


def xiaohongshu_post(obj, base):
    images = obj.get("imageList")
    media = [note_image(image, base) for image in images] if isinstance(images, list) else []
    kind = "video" if obj.get("type") == "video" else "image"
    if kind == "video":
        urls = [entry.get("masterUrl") for entry in walk(obj.get("video", {}))]
        if video := first_candidate(urls, "video", base):
            media.append(video)
    author = obj.get("user")
    name = clean(author.get("nickname") or author.get("nickName")) if isinstance(author, dict) else ""
    expected = len(images) if isinstance(images, list) else None
    return clean(obj.get("title")), clean(obj.get("desc")), kind, media, expected, name


def douyin_post(obj, base):
    author = obj.get("author")
    name = clean(author.get("nickname")) if isinstance(author, dict) else ""
    images = obj.get("images")
    image_post = obj.get("image_post_info")
    if not images and isinstance(image_post, dict):
        images = image_post.get("images")
    if isinstance(images, list) and images:
        media = [note_image(image, base) for image in images]
        return clean(obj.get("item_title")), clean(obj["desc"]), "image", media, len(images), name
    video = obj.get("video")
    download = video.get("download_addr") if isinstance(video, dict) else None
    media = [first_candidate(download.get("url_list", []), "video", base)] if isinstance(download, dict) else []
    return clean(obj.get("item_title")), clean(obj["desc"]), "video", media, 1, name


def full_post(objects, platform, canonical, base):
    stable = re.search(r"/(?:explore|video|note)/([\da-f]{10,})$", canonical)
    if not stable:
        return None
    work_id = stable[1]
    best = None
    for obj in objects:
        # The same note appears as a feed card and as the detail payload; keep the richest copy.
        if platform == "小红书" and obj.get("noteId") == work_id and "desc" in obj:
            post = xiaohongshu_post(obj, base)
        elif platform == "抖音" and str(obj.get("aweme_id")) == work_id and "desc" in obj:
            post = douyin_post(obj, base)
        else:
            continue
        media = [item for item in post[3] if item]
        score = (len(media), len(post[1] or ""))
        if best is None or score > best[0]:
            best = (score, post)
    return best[1] if best else None


def work_key(url):
    canonical = identity(url)
    path = urlsplit(canonical).path
    platform = platform_for(canonical)
    if platform == "小红书" and (match := re.fullmatch(r"/explore/([a-f0-9]{24})", path)):
        return platform, match[1]
    if platform == "抖音" and (match := re.fullmatch(r"/(?:video|note)/([0-9]{10,})", path)):
        return platform, match[1]
    return None


def short_redirect_over_lan(url):
    """Resolve a Xiaohongshu short link over the physical Windows LAN when the default route times out."""
    parsed = urlsplit(url)
    host = parsed.hostname
    if os.name != "nt" or host not in XHS_SHORT_HOSTS or not permitted(url):
        return ""
    script = r"""
$physical = Get-NetAdapter -Physical | Where-Object Status -EQ 'Up' | Select-Object -ExpandProperty ifIndex
$adapter = Get-NetIPConfiguration | Where-Object {
    $_.InterfaceIndex -in $physical -and $_.IPv4DefaultGateway -and $_.IPv4Address
} | Sort-Object @{Expression = { $_.NetIPv4Interface.InterfaceMetric }} | Select-Object -First 1
if (-not $adapter) { exit 1 }
$address = @($adapter.IPv4Address)[0].IPAddress
$server = @($adapter.DNSServer | Where-Object AddressFamily -EQ 2 | ForEach-Object ServerAddresses)[0]
if (-not $server) { $server = $adapter.IPv4DefaultGateway.NextHop }
$answers = @(Resolve-DnsName -Name 'SHORT_HOST' -Server $server -Type A -DnsOnly -QuickTimeout -ErrorAction Stop |
    Where-Object Type -EQ A | Select-Object -ExpandProperty IPAddress)
@{address=$address; answers=$answers} | ConvertTo-Json -Compress
""".replace("SHORT_HOST", host)
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                            capture_output=True, text=True, timeout=6, check=True,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    route = json.loads(result.stdout)
    address = str(ipaddress.IPv4Address(route["address"]))
    for remote in route["answers"][:2]:
        if not ipaddress.IPv4Address(remote).is_global:
            continue
        context = ssl.create_default_context()
        path = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
        try:
            with socket.create_connection((remote, 443), timeout=3, source_address=(address, 0)) as raw:
                with context.wrap_socket(raw, server_hostname=host) as tls:
                    with closing(http.client.HTTPSConnection(host, timeout=3, context=context)) as client:
                        client.sock = tls
                        client.request("GET", path, headers={"User-Agent": "Mozilla/5.0"})
                        with client.getresponse() as response:
                            if response.status in (301, 302, 303, 307, 308):
                                return response.getheader("Location", "")
        except (OSError, http.client.HTTPException):
            continue
    return ""


class ShortLinkRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def resolve_xhs_short(url):
    if not permitted(url) or urlsplit(url).hostname not in XHS_SHORT_HOSTS:
        return ""
    location = ""
    try:
        check_public(url, False)
        request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with build_opener(ProxyHandler({}), ShortLinkRedirect()).open(request, timeout=4):
            pass
    except HTTPError as exc:
        with closing(exc):
            if exc.code in (301, 302, 303, 307, 308):
                location = exc.headers.get("Location", "")
    except OSError:
        try:
            location = short_redirect_over_lan(url)
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    except ValueError:
        return ""
    try:
        target = urljoin(url, location)
    except ValueError:
        return ""
    if target.startswith("http://"):
        target = "https://" + target[7:]
    key = work_key(target)
    return target if key and key[0] == "小红书" and permitted(target) else ""


def blank_page(url):
    return {"canonical": identity(url), "title": "", "author": "", "text": "", "scope": "仅分享内容",
            "media": [], "kind": "", "expected_images": None, "video_known": False, "limit": "",
            "via": "", "media_known": False}


class LoginRequired(ValueError):
    pass


def load_public(url, destinations):
    with open_public(url, destinations=destinations) as response:
        destinations.append(response.url)
        if re.match(r"/(?:login|signin|auth)(?:/|$)", urlsplit(response.url).path, re.I):
            raise LoginRequired("平台要求登录")
        if unavailable_path(response.url):
            raise ValueError("原帖暂不可访问")
        if response.headers.get_content_type() != "text/html":
            raise ValueError("分享链接未返回网页")
        html = response.read(4 * 1024 * 1024 + 1)
        if len(html) > 4 * 1024 * 1024:
            raise ValueError("分享页超过读取上限")
        return html, response.url


def unavailable_path(url):
    return re.match(r"/(?:[a-z]{2}(?:-[a-z]{2})?/)?(?:login|signin|auth|404|error)(?:/|$)", urlsplit(url).path, re.I)


def fill_page(page, html, base, platform, destinations, dom=None):
    soup = BeautifulSoup(html, "html.parser")
    og = {tag.get("property"): tag.get("content", "") for tag in soup.find_all("meta", property=True)}
    canonical = soup.find("link", rel="canonical")
    declared = urljoin(base, canonical.get("href", "")) if canonical else base
    declared_key = work_key(declared)
    linked_key = next((key for u in destinations if (key := work_key(u)) and key[0] == platform), None)
    final_key = work_key(base)
    if linked_key and any(key and key != linked_key for key in (final_key, declared_key)):
        raise ValueError("公开页指向了不同作品")
    if final_key and final_key[0] == platform:
        page["canonical"] = identity(base)
    elif declared_key and declared_key[0] == platform:
        page["canonical"] = identity(declared)
    objects = [obj for root in embedded_objects(soup) for obj in walk(root)]
    if dom and dom.get("state"):
        # The rendered page exposes its live state; it is newer than the inline script.
        objects = [obj for obj in walk(dom["state"])] + objects
    page.update(title=clean(og.get("og:title")), text=clean(og.get("og:description")),
                scope="网页预览", kind="video" if og.get("og:type", "").startswith("video") else "")
    post = full_post(objects, platform, page["canonical"], base)
    if post:
        title, desc, kind, media, expected, author = post
        page.update(title=title or page["title"], author=author, text=desc, scope="作者正文", kind=kind,
                    media=[m for m in media if m], expected_images=expected if kind == "image" else None,
                    video_known=kind == "video", media_known=expected is not None)
    if platform != "抖音" and page["kind"] != "image":
        videos = [og.get("og:video:secure_url"), og.get("og:video")]
        videos.extend(o.get("contentUrl") for o in objects if o.get("@type") == "VideoObject")
        videos.extend(tag.get("src") for tag in soup.select("video[src], video source[src]"))
        if direct := first_candidate(videos, "video", base):
            page["kind"] = "video"
            if not any(m["kind"] == "video" for m in page["media"]):
                page["media"].append(direct)
    if not any(m["kind"] == "image" for m in page["media"]):
        if cover := candidate(og.get("og:image"), "image", base):
            cover["preview"] = True
            page["media"].append(cover)
    if platform != "抖音" and page["scope"] != "作者正文" and dom and final_key and final_key[0] == platform:
        fill_from_dom(page, dom)
    if platform == "抖音" and page["scope"] != "作者正文":
        visible = soup.get_text(" ", strip=True)
        if re.search(r"(?:请先登录后?|登录后)\s*(?:继续)?(?:观看|查看|播放)", visible):
            page.update(auth_required=True, limit="抖音要求登录后查看原帖")
    page["media"] = page["media"][:64]


def fill_from_dom(page, dom):
    """Text and media read from the rendered page, used when the markup has no state."""
    text = clean(dom.get("desc"))
    images = [url for url in dom.get("images") or [] if permitted(url, True)]
    videos = [url for url in dom.get("videos") or [] if permitted(url, True)]
    if len(text) < 12:
        return
    page.update(title=clean(dom.get("title")) or page["title"], author=clean(dom.get("author")),
                text=text, scope="作者正文", via="浏览器读取", media_known=True)
    media = [{"url": url, "kind": "image"} for url in images]
    if videos:
        media.insert(0, {"url": videos[0], "kind": "video"})
    if media:
        page["media"] = media
        page["expected_images"] = len(images) or None
        page["kind"] = "video" if videos else "image"
    elif not page["kind"]:
        page["kind"] = "image" if images else ""


def read_page(url, session=None):
    page = blank_page(url)
    platform = platform_for(url)
    if not platform:
        page["limit"] = "当前仅尝试补齐小红书和抖音的公开分享页"
        return page
    target = resolve_xhs_short(url) or url
    destinations = [url, target]
    ready = False
    if session is not None and platform == "小红书":
        try:
            ready = session.ready(platform)
        except Exception as exc:
            page["limit"] = f"登录态读取失败（{type(exc).__name__}）"
    if ready:
        try:
            check_public(target, False)
            rendered = session.read(target)
            base = rendered["final"]
            destinations.extend(u for u in rendered.get("redirects", []) if permitted(u))
            destinations.append(base)
            if not permitted(base) or platform_for(base) != platform or unavailable_path(base):
                raise ValueError("浏览器未打开可读取的原帖")
            rendered_page = blank_page(target)
            rendered_page["via"] = "浏览器读取"
            fill_page(rendered_page, rendered["html"], base, platform, destinations, rendered.get("dom"))
            if rendered_page["scope"] == "作者正文":
                return finish_page(rendered_page, destinations, platform)
            page = rendered_page
        except Exception as exc:
            page["limit"] = f"登录态读取失败（{type(exc).__name__}）"
    try:
        html, base = load_public(target, destinations)
        if platform_for(base) != platform:
            raise ValueError("分享页跳转至其他平台")
        public_page = blank_page(target)
        fill_page(public_page, html, base, platform, destinations)
        if public_page["scope"] == "作者正文" or not page["text"]:
            page = public_page
    except LoginRequired:
        page.update(auth_required=True, limit="平台要求登录后查看原帖")
    except HTTPError as exc:
        if exc.code == 401:
            page.update(auth_required=True, limit="平台要求登录后查看原帖")
        else:
            page["limit"] = f"公开分享页暂不可读（HTTP {exc.code}）"
    except ValueError as exc:
        page["limit"] = f"公开分享页暂不可读（{exc}）"
    except (OSError, UnicodeError, RecursionError) as exc:
        page["limit"] = f"公开分享页暂不可读（{type(exc).__name__}）"
    return finish_page(page, destinations, platform)


def finish_page(page, destinations, platform):
    for destination in destinations:
        if (key := work_key(destination)) and key[0] == platform:
            page["canonical"] = identity(destination)
            break
    if platform == "抖音" and page["canonical"].startswith("https://www.douyin.com/video/"):
        page["kind"] = "video"
    if page["scope"] != "作者正文" and not page["limit"]:
        page["limit"] = "公开分享页未提供可核实的作品正文或媒体"
    return page


def search_results(platform, keyword):
    """Read same-platform results, then Bing. Snippets are never used as evidence."""
    if platform == "小红书":
        search_url = "https://www.xiaohongshu.com/search_result/?" + urlencode({"keyword": keyword, "source": "web_explore_feed"})
    else:
        search_url = "https://www.douyin.com/search/" + quote(keyword, safe="") + "?type=video"
    try:
        with open_public(search_url) as response:
            base = response.url
            body = response.read(1024 * 1024 + 1)
        if len(body) <= 1024 * 1024:
            for anchor in BeautifulSoup(body, "html.parser").select("a[href]"):
                url = urljoin(base, anchor["href"])
                key = work_key(url)
                if key and key[0] == platform and permitted(url):
                    yield url, anchor.get_text(" ", strip=True)
    except (OSError, ValueError, RecursionError):
        pass
    domain = PLATFORMS[platform][0]
    query = urlencode({"q": f"site:{domain} {keyword}", "format": "rss"})
    request = Request("https://cn.bing.com/search?" + query, headers={
        "User-Agent": "Mozilla/5.0", "Accept-Language": "zh-CN,zh;q=0.9",
    })
    with build_opener().open(request, timeout=7) as response:
        body = response.read(512 * 1024 + 1)
    if len(body) > 512 * 1024:
        raise ValueError("搜索结果超过读取上限")
    try:
        rss = ElementTree.fromstring(body)
    except ElementTree.ParseError:
        soup = BeautifulSoup(body, "html.parser")
        if not soup.select_one("#b_results"):
            raise ValueError("搜索服务未返回可读结果") from None
        records = [(anchor.get("href", ""), anchor.get_text(" ", strip=True))
                   for anchor in soup.select("#b_results li.b_algo h2 a[href]")]
    else:
        records = [(item.findtext("link", ""), item.findtext("title", "")) for item in rss.findall("./channel/item")]
    for url, title in records:
        try:
            parsed = urlsplit(url)
            if parsed.hostname in ("cn.bing.com", "www.bing.com") and parsed.path == "/ck/a":
                params = parse_qs(parsed.query)
                encoded = params.get("u", [""])[0]
                url = params.get("url", [""])[0]
                if not url and encoded.startswith("a1"):
                    url = base64.urlsafe_b64decode(encoded[2:] + "===").decode("utf-8")
            key = work_key(url)
            if key and key[0] == platform and permitted(url):
                yield url, title
        except ValueError:
            continue


def resolve_page(url, title, prior=None, session=None):
    """Read the shared URL, then try same-platform search when the full body is absent."""
    page = read_page(url, session)
    platform = platform_for(url)
    previous_key = work_key((prior or {}).get("canonical", ""))
    current_key = work_key(page["canonical"])
    if previous_key and current_key and previous_key != current_key:
        return dict(prior, search_status="链接现在指向不同作品，待核对")
    canonical = page["canonical"] if current_key else (prior or {}).get("canonical", "")
    canonical_key = work_key(canonical)
    if platform == "抖音" and canonical != url and canonical_key and canonical_key[0] == platform:
        has_video = any(item["kind"] == "video" for item in page.get("media", []))
        if page["scope"] != "作者正文" or (page.get("kind") == "video" and not has_video):
            original = read_page(canonical, session)
            if work_key(original["canonical"]) == canonical_key:
                original_has_video = any(item["kind"] == "video" for item in original.get("media", []))
                if original["scope"] == "作者正文" and (page["scope"] != "作者正文" or original_has_video):
                    return original
                page["canonical"] = canonical
                if original.get("auth_required") and page["scope"] != "作者正文":
                    page.update(auth_required=True, limit=original["limit"])
    needs_video = platform == "抖音" and page.get("kind") == "video" and not any(
        item["kind"] == "video" for item in page.get("media", []))
    if not platform or (page["scope"] == "作者正文" and not needs_video):
        return page
    expected = current_key or previous_key
    if expected and expected[0] != platform:
        expected = None
    title = share_title(title, platform)
    title = re.sub(r'[^\w\s-]', ' ', title)
    title = " ".join(title.split())[:110]
    terms = list(dict.fromkeys(term for term in (expected[1] if expected else "", title) if term))
    if not terms:
        return dict(page, search_status="链接未解析出作品 ID，也没有可用的分享标题")
    candidates = {}
    failed = False
    tried = {url, canonical} if platform == "抖音" else set()
    checked = set()
    normalized = "".join(re.findall(r"\w", title.casefold()))
    for term in terms:
        try:
            for result_url, result_title in search_results(platform, term):
                result_key = work_key(result_url)
                if expected and result_key == expected and result_url not in tried and result_url not in checked:
                    if len(checked) >= 3:
                        break
                    checked.add(result_url)
                    found = read_page(result_url, session)
                    found_video = any(item["kind"] == "video" for item in found.get("media", []))
                    if (found["scope"] == "作者正文" and work_key(found["canonical"]) == expected
                            and (page["scope"] != "作者正文" or found_video)):
                        return dict(found, search_status="公开搜索已核实同一作品并取得正文")
                elif not expected and len(candidates) < 3:
                    match_title = "".join(re.findall(r"\w", result_title.casefold()))
                    if len(normalized) >= 8 and normalized in match_title:
                        candidates[identity(result_url)] = None
        except (OSError, ValueError, RecursionError):
            failed = True
    if checked and page["scope"] != "作者正文":
        status = "搜索核实到同一作品，公开页暂未提供正文"
    elif expected and platform == "抖音":
        status = "已核实作品链接，网页正文或视频仍待补"
    elif candidates:
        status = "搜到标题相符的候选，原短链尚未解析出作品 ID，待核对"
    elif failed:
        status = "本次公开搜索未返回可核实的原帖"
    else:
        status = "公开搜索未找到可核实的原帖"
    return dict(page, search_status=status, search_candidates=list(candidates))


def download_media(item, handle, remaining=MAX_FILE, cookie=""):
    with open_public(item["url"], True, cookie=cookie) as response:
        if response.status != 200:
            raise ValueError("公开媒体响应不完整")
        types = IMAGE_TYPES if item["kind"] == "image" else VIDEO_TYPES
        extension = types.get(response.headers.get_content_type())
        if not extension:
            raise ValueError("分享页未提供可下载媒体")
        declared = response.headers.get("Content-Length")
        max_bytes = min(MAX_FILE, remaining)
        if declared is not None and not 0 < int(declared) <= max_bytes:
            raise ValueError("公开附件大小超限或无效")
        length = 0
        while chunk := response.read(1024 * 1024):
            length += len(chunk)
            if length > max_bytes:
                raise ValueError("公开附件超过当前大小上限")
            handle.write(chunk)
        if length == 0 or (declared is not None and length != int(declared)):
            raise ValueError("公开附件为空或传输不完整")
        return extension, length
