"""Compile the inbox into source notes without rewriting raw files or manual paragraphs."""

import hashlib
import json
import logging
import os
import re
import tempfile
import uuid
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

from enrich import HEX_COLOR, category_for, detailed_summary, image_text, literal_points, summarize, video_frames, video_text
from public_page import (DOUYIN_SHARE, aliases_for, download_media, find_url, identity, platform_for,
                         resolve_page, share_title, work_key)
from storage import CHINA, IMPORTANCE, MAX_PACKAGE, atomic_write, now, read_json, sha256, write_json
from drawing import VERSION as DRAWING_VERSION, drawing_profile, profile_lines
from documents import document_text
from vision import VERSION as VISION_VERSION, review_image

TOPIC = "wiki/topics/科研工具"
START = "<!-- research-collector:begin -->"
END = "<!-- research-collector:end -->"
LOGGER = logging.getLogger(__name__)


def fingerprint(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def encode(value):
    return json.dumps(value, ensure_ascii=False)


def plain(value):
    value = " ".join(str(value).split())
    value = value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return re.sub(r"([\\`*_{}\[\]()#!|])", r"\\\1", value)


def link(path, title=""):
    path = str(path).removesuffix(".md")
    return f"[[{path}|{plain(title).replace('|', '｜')}]]" if title else f"[[{path}]]"


def web_link(url, label="打开原帖"):
    return f"[{label}]({quote(url, safe=':/?&=%+#@,;~!-_')})" if identity(url) else "未提供有效原帖链接"


def fenced(value):
    longest = max((len(part) for part in re.findall(r"`+", value)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}text\n{value}\n{fence}\n"


def safe_title(title):
    title = re.sub(r'[<>:"/\\|?*\x00-\x1f#\[\]^]', " ", title)
    return " ".join(title.split())[:46].strip(" .") or "待补资料"


def capture_title(item, page):
    title = page.get("title") or item["title"]
    if title:
        return title
    documents = [file["name"] for file in item.get("attachments", []) if file["kind"] == "document"]
    if documents and not item["text"].strip():
        return documents[0]
    first_line = next(iter(item["text"].splitlines()), "")
    platform = platform_for(item["url"]) or "主动分享"
    shared_title = share_title(first_line, platform)
    return safe_title(shared_title) if shared_title else f"{platform}待补资料"


def douyin_video(capture):
    url = capture["page"].get("canonical") or identity(capture["url"])
    return capture["platform"] == "抖音" and url.startswith("https://www.douyin.com/video/")


def current_analysis(source):
    analysis = source.get("analysis", {})
    if analysis.get("video_pipeline") == 1:
        return analysis
    if any(capture["platform"] == "抖音" for capture in source["captures"].values()):
        return {}
    return analysis


def front(title, kind, date, tags, extra=()):
    values = [
        ("title", title), ("type", kind), ("status", "draft" if kind == "source" else "active"),
        ("created", date), ("updated", date), ("tags", tags), ("sources", []),
        ("review_status", "unreviewed"), *extra,
    ]
    return "---\n" + "".join(f"{key}: {encode(value)}\n" for key, value in values) + "---\n\n"


def update_file(path, transform):
    for _ in range(3):
        original = path.read_bytes() if path.exists() else None
        before = original.decode("utf-8-sig") if original is not None else ""
        after = transform(before)
        if before == after:
            return
        current = path.read_bytes() if path.exists() else None
        if current == original:
            prefix = b"\xef\xbb\xbf" if original and original.startswith(b"\xef\xbb\xbf") else b""
            atomic_write(path, prefix + after.encode("utf-8"))
            return
    raise OSError(f"文件正在编辑，稍后重试 {path.name}")


def newline_for(before):
    return "\r\n" if "\r\n" in before else "\n"


def append_once(path, marker, line):
    def append(before):
        if marker in before:
            return before
        newline = newline_for(before)
        suffix = line.replace("\n", newline)
        return before + ("" if not before or before.endswith(newline) else newline) + suffix + newline
    update_file(path, append)


def update_daily_status(path, statuses):
    def replace(before):
        pattern = r'(?m)^(- (?:新增|补传) · )(?:待补|已归档)( · [^\r\n]*?<!-- research-collector:([0-9a-f-]{36}) -->)'
        return re.sub(pattern, lambda m: m[1] + statuses[m[3]] + m[2] if m[3] in statuses else m[0], before)
    update_file(path, replace)


def apply_capture_preferences(source):
    captures = sorted(source["captures"].values(),
                      key=lambda c: datetime.fromisoformat(c.get("captured_at") or c["received"]), reverse=True)
    for key in ("category", "importance"):
        value = next((c[key] for c in captures if c.get(key)), "")
        if value:
            source["user_category" if key == "category" else key] = value


def replace_block(path, prefix, body, prior=None, properties=None):
    section = f"{START}\n{body.strip()}\n{END}"
    def replace(before):
        if not before:
            return prefix + section + "\n"
        newline = newline_for(before)
        block = section.replace("\n", newline)
        left = before.find(START)
        right = before.find(END, left + len(START)) if left >= 0 else -1
        if left < 0 or right < 0:
            raise ValueError(f"自动维护区缺失 {path.name}")
        right += len(END)
        old = before[left:right].replace("\r\n", "\n")
        if prior and fingerprint(old) != prior and old != section:
            raise ValueError(f"自动维护区有人工修改 {path.name}")
        if old == section:
            return before
        heading = before[:left]
        if heading.startswith("---" + newline):
            end = heading.find(newline + "---", len("---" + newline))
            if end >= 0:
                updated = heading[:end]
                for key, value in (properties or {}).items():
                    field = f"{key}: {encode(value)}"
                    updated, count = re.subn(rf'(?m)^{key}:[^\r\n]*', lambda _: field, updated, count=1)
                    if not count:
                        updated += newline + field
                updated = re.sub(r'^updated: "\d{4}-\d{2}-\d{2}"(?=\r?$)',
                                 "updated: " + encode(now().date().isoformat()), updated, count=1, flags=re.M)
                heading = updated + heading[end:]
        return heading + block + before[right:]
    update_file(path, replace)
    return fingerprint(section)


class Wiki:
    def __init__(self, vault, state, web=True, local_models=True, session=None):
        self.vault = Path(vault)
        self.state = Path(state)
        self.web = web
        self.local_models = local_models
        self.session = session
        self.catalog_path = self.state / "catalog.json"
        self.catalog = read_json(self.catalog_path) if self.catalog_path.exists() else {}

    def ensure_vault(self):
        if not (self.vault / "首页.md").is_file() or not (self.vault / "wiki/index.md").is_file():
            raise OSError("知识库当前不可访问")
        self.refresh_topic()
        topic = link(TOPIC, "科研工具")
        for relative in ("首页.md", "wiki/index.md"):
            path = self.vault / relative
            if f"[[{TOPIC}" not in path.read_text(encoding="utf-8-sig"):
                append_once(path, f"[[{TOPIC}", f"\n## 科研工具\n\n- {topic}")

    def sync_daily_status(self, sources):
        days = {}
        for source in sources:
            if not (self.vault / source["note"]).is_file():
                continue
            status = "待补" if self.coverage(source) else "已归档"
            for capture_id, capture in source["captures"].items():
                days.setdefault(capture["date"], {})[capture_id] = status
        for day, statuses in days.items():
            path = self.vault / f"{TOPIC}/每日/{day}.md"
            if path.is_file():
                update_daily_status(path, statuses)

    def save(self):
        write_json(self.catalog_path, self.catalog)

    def lookup(self, aliases):
        return [source for source in self.catalog.values() if set(aliases).intersection(source["aliases"])]

    def allocate(self, item, title, aliases):
        used = [int(key[1:]) for key in self.catalog]
        for path in (self.vault / "wiki/sources").glob("S*-*.md"):
            if match := re.match(r"S(\d+)-", path.name):
                used.append(int(match[1]))
        source_id = f"S{max(used, default=0) + 1:04}"
        source = {
            "id": source_id, "note": f"wiki/sources/{source_id}-{safe_title(title)}.md",
            "title": title, "aliases": aliases, "captures": {}, "assets": {},
            "category": "其他科研工具", "date": self.capture_date(item), "block": "",
        }
        self.catalog[source_id] = source
        self.save()
        return source

    @staticmethod
    def capture_date(item):
        return datetime.fromisoformat(item["captured_at"]).astimezone(CHINA).date().isoformat()

    def asset(self, src, digest, extension):
        folder = self.vault / "raw/assets/科研工具"
        folder.mkdir(parents=True, exist_ok=True)
        path = next(folder.glob(digest + ".*"), folder / (digest + extension))
        if not path.exists():
            temp = folder / (".collector-" + uuid.uuid4().hex + ".tmp")
            try:
                with temp.open("xb") as handle:
                    with src.open("rb") as source:
                        for chunk in iter(lambda: source.read(1024 * 1024), b""):
                            handle.write(chunk)
                    handle.flush()
                    os.fsync(handle.fileno())
                if not path.exists():
                    os.replace(temp, path)
            finally:
                temp.unlink(missing_ok=True)
        return path.relative_to(self.vault).as_posix()

    def fetch_media(self, candidate, remaining, use_browser=True):
        """Download one candidate, retrying once with the browser session cookies."""
        cookie = ""
        for attempt in (0, 1):
            descriptor, name = tempfile.mkstemp(dir=self.state, suffix=".media")
            handle = os.fdopen(descriptor, "wb")
            temp = Path(name)
            try:
                extension, size = download_media(candidate, handle, remaining, cookie)
            except (OSError, ValueError):
                extension, size = "", 0
            except BaseException:
                handle.close()
                temp.unlink(missing_ok=True)
                raise
            finally:
                if not handle.closed:
                    handle.close()
            if extension:
                return temp, extension, size
            temp.unlink(missing_ok=True)
            if attempt or self.session is None or not use_browser:
                break
            try:
                cookie = self.session.cookie_header(candidate["url"])
            except Exception:
                cookie = ""
            if not cookie:
                break
        return None, "", 0

    def copy_media(self, folder, item, page):
        assets = {}
        for file in item["attachments"]:
            rel = self.asset(folder / (file["id"] + ".part"), file["sha256"], Path(file["name"]).suffix.lower())
            assets[file["sha256"]] = {"path": rel, "kind": file["kind"], "origin": "用户分享", "name": file["name"]}
        remaining = MAX_PACKAGE
        platform = platform_for(page.get("canonical", "")) or platform_for(item.get("url", ""))
        for candidate in page.get("media", []):
            if remaining <= 0:
                break
            temp, extension, size = self.fetch_media(candidate, remaining, use_browser=platform != "抖音")
            if not extension:
                continue
            try:
                remaining -= size
                digest = sha256(temp)
                assets[digest] = {"path": self.asset(temp, digest, extension),
                                  "kind": candidate["kind"], "origin": "公开分享页",
                                  "preview": candidate.get("preview", False)}
            finally:
                temp.unlink(missing_ok=True)
        return assets

    def raw_note(self, item, page, assets, original="", snapshot=""):
        suffix = "-网页补查" if original else ""
        if snapshot:
            suffix += f"-{snapshot}"
        name = f'raw/inbox/科研工具/{self.capture_date(item)}-{item["id"]}{suffix}.md'
        path = self.vault / name
        if not path.exists():
            text = [
                "# 科研资料原始记录", "", f'- 采集时间 {item["captured_at"]}',
                f'- 收集方式 {item["trigger"]}', f'- {web_link(item["url"])}', "",
            ]
            if item.get("category"):
                text += [f'- 资料类型 {plain(item["category"])}']
            if item.get("importance"):
                text += [f'- 重要程度 {item["importance"]}']
            if original:
                text += [f'- 补查时间 {now().isoformat()}', f'- {link(original, "手机分享原始记录")}', ""]
            else:
                text += ["## 手机分享内容", "", fenced(item["text"] or "未附文字")]
            if page.get("title") or page.get("text"):
                if work_key(page.get("canonical", "")):
                    text += [f'- {web_link(page["canonical"], "网页出处")}', ""]
                text += [f'## {page["scope"]}', "", fenced(
                    "\n".join(value for value in (page["title"], page.get("author"), page["text"]) if value))]
            text += ["## 本地附件", ""]
            text.extend("- " + link(asset["path"]) for asset in assets.values())
            atomic_write(path, ("\n".join(text) + "\n").encode("utf-8"))
        return name

    def process(self, folder):
        self.ensure_vault()
        item = read_json(folder / "manifest.json")
        item["url"] = item["url"] or find_url(item["text"])
        existing = next((s for s in self.catalog.values() if item["id"] in s["captures"]), None)
        if existing:
            self.publish_capture(existing, existing["captures"][item["id"]], item["id"])
            return existing
        page = {}
        if self.web and item["url"]:
            page = resolve_page(item["url"], item["title"] or item["text"].split("\n", 1)[0], session=self.session)
        aliases = aliases_for(item["url"], page.get("canonical", ""))
        if not aliases:
            key = item["text"] or "|".join(sorted(a["sha256"] for a in item["attachments"]))
            aliases = ["sha256:" + fingerprint(key)]
        matches = self.lookup(aliases)
        if item.get("parent_id"):
            parent = next((s for s in self.catalog.values() if item["parent_id"] in s["captures"]), None)
            if parent is None:
                raise ValueError("原记录尚未入库，稍后重试补充")
            if any(s["id"] != parent["id"] for s in matches):
                raise ValueError("补充链接与原记录冲突，请核对")
            matches = [parent]
        if len(matches) > 1:
            raise ValueError("该链接对应多个已有来源，需要人工核对")
        platform = platform_for(item["url"]) or "主动分享"
        title = capture_title(item, page)
        assets = self.copy_media(folder, item, page)
        raw = self.raw_note(item, page, assets)
        source = matches[0] if matches else self.allocate(item, title, aliases)
        source["aliases"] = sorted(set(source["aliases"] + aliases))
        for digest, asset in assets.items():
            existing_asset = source["assets"].get(digest)
            if not existing_asset or (asset["origin"] == "公开分享页" and not asset.get("preview")):
                source["assets"][digest] = asset
        if page.get("title") or (item["title"] and not item.get("parent_id")):
            source["title"] = title
        source["date"] = min(source["date"], self.capture_date(item))
        capture = {
            "raw": raw, "url": item["url"], "title": title, "platform": platform, "page": page,
            "shared_text": item["text"], "date": self.capture_date(item),
            "captured_at": item["captured_at"],
            "received": read_json(folder / "committed.json")["received_at"],
            "event": "补传" if source["captures"] else "新增",
            "complete": item.get("complete", False),
        }
        capture.update({key: item[key] for key in ("category", "importance") if key in item})
        source["captures"][item["id"]] = capture
        apply_capture_preferences(source)
        source.pop("analysis", None)
        source.pop("drawing_version", None)
        self.save()
        self.publish_capture(source, capture, item["id"])
        return source

    def search_existing(self, folder, source_id, force=False):
        if not self.web or source_id not in self.catalog:
            return
        current = self.catalog[source_id]
        capture_id = folder.name
        old = current["captures"].get(capture_id)
        if not old or not platform_for(old["url"]) or (old.get("complete") and not force):
            return
        prior = old["page"]
        if not force and (prior.get("scope") == "作者正文" or "search_status" in prior):
            return
        item = read_json(folder / "manifest.json")
        item["url"] = old["url"]
        page = resolve_page(item["url"], item["title"] or item["text"].split("\n", 1)[0], prior, self.session)
        source = deepcopy(current)
        capture = source["captures"][capture_id]
        aliases = aliases_for(item["url"], page.get("canonical", ""))
        enriched = False
        conflict = any(match["id"] != source_id for match in self.lookup(aliases))
        if conflict:
            capture["page"]["search_status"] = "找到对应其他来源卡的作品链接，待核对"
        else:
            # Original phone files were consumed after ingestion. Reuse the assets already in the vault.
            assets = self.copy_media(folder, {"attachments": []}, page) if page["scope"] == "作者正文" else {}
            fresh_body = page["scope"] == "作者正文" and prior.get("scope") != "作者正文"
            new_assets = {digest: asset for digest, asset in assets.items()
                          if digest not in source["assets"] or (source["assets"][digest].get("preview") and not asset.get("preview"))}
            enriched = bool(fresh_body or new_assets)
            if enriched:
                snapshot = fingerprint(encode([page["canonical"], page["text"], sorted(assets)]))[:12] if capture.get("page_raw") else ""
                capture["page_raw"] = self.raw_note(item, page, assets, original=capture["raw"], snapshot=snapshot)
                capture["page"] = page
                source["assets"].update(new_assets)
                if page.get("title"):
                    source["title"] = capture["title"] = page["title"]
                source.pop("analysis", None)
                source.pop("drawing_version", None)
            else:
                if work_key(page.get("canonical", "")):
                    capture["page"]["canonical"] = page["canonical"]
                if page.get("scope") != "作者正文" and prior.get("scope") != "作者正文":
                    capture["page"]["auth_required"] = page.get("auth_required", False)
                    capture["page"]["limit"] = page.get("limit", "")
                if page.get("search_status"):
                    capture["page"]["search_status"] = page["search_status"]
                    capture["page"]["search_candidates"] = page.get("search_candidates", [])
            source["aliases"] = sorted(set(source["aliases"] + aliases))
        self.catalog[source_id] = source
        try:
            self.publish_capture(source, capture, capture_id)
        except Exception:
            self.catalog[source_id] = current
            raise
        if enriched:
            marker = f'<!-- research-collector:web-search:{capture_id}:{Path(capture["page_raw"]).stem} -->'
            append_once(self.vault / "wiki/log.md", marker,
                        f'\n## [{now().date().isoformat()}] ingest | 科研资料网页补查\n\n'
                        f'- {link(source["note"])} {marker}')
        return enriched

    def refetch(self, selected=None):
        """Re-read captures missing text or a Douyin video."""
        touched = []
        receipts = sorted((self.state / "inbox").glob("*/processed.json"), key=lambda p: p.stat().st_mtime)
        for receipt in receipts:
            source_id = read_json(receipt)["source_id"]
            if selected and source_id != selected:
                continue
            source = self.catalog.get(source_id)
            old = source["captures"].get(receipt.parent.name) if source else None
            if not old:
                continue
            needs_video = douyin_video(old) and not any(asset["kind"] == "video" for asset in source["assets"].values())
            if (old.get("complete") or old["page"].get("scope") == "作者正文") and not needs_video:
                continue
            try:
                if self.search_existing(receipt.parent, source_id, force=True):
                    touched.append(source_id)
            except Exception:
                LOGGER.exception("来源 %s 补查失败", source_id)
                continue
        for source_id in dict.fromkeys(touched):
            try:
                self.analyze(self.catalog[source_id])
            except Exception:
                LOGGER.exception("来源 %s 整理失败", source_id)
        return list(dict.fromkeys(touched))

    def coverage(self, source):
        if source["assets"] and all(a["kind"] == "document" for a in source["assets"].values()) and not any(c["url"] for c in source["captures"].values()):
            return []  # A standalone paper has no social-post image count to verify.
        pages = [c["page"] for c in source["captures"].values()]
        image_count = sum(asset["kind"] == "image" and asset["origin"] == "公开分享页"
                          and not asset.get("preview") for asset in source["assets"].values())
        video_count = sum(asset["kind"] == "video" for asset in source["assets"].values())
        known_douyin_video = any(douyin_video(capture) for capture in source["captures"].values())
        if any(capture.get("complete") for capture in source["captures"].values()):
            return ["完整视频未取得"] if known_douyin_video and not video_count else []
        expected = max((p["expected_images"] or 0 for p in pages if p.get("kind") == "image"), default=0)
        video = video_count or any(p.get("kind") == "video" for p in pages) or known_douyin_video
        known = any(p.get("media_known") for p in pages)
        missing = []
        if not any(p.get("scope") == "作者正文" for p in pages):
            missing.append("作者完整正文未核实")
        if video and not video_count:
            missing.append("完整视频未取得")
        if expected > image_count:
            missing.append(f"作者配图已核实 {image_count}/{expected} 张，补传附件见下")
        elif not expected and not video and not known:
            missing.append("配图总数未核实")
        return missing

    def evidence(self, source, media=False):
        rows = []
        for c in source["captures"].values():
            page = c["page"]
            douyin = c["platform"] == "抖音"
            if page.get("text") and (not douyin or page.get("scope") == "作者正文"):
                rows.append({"text": page["text"], "where": link(c.get("page_raw", c["raw"]), page["scope"])})
            shared = re.sub(r'https?://\S+', '', c["shared_text"]).strip()
            if shared and not (douyin and DOUYIN_SHARE.search(shared)):
                rows.append({"text": c["shared_text"], "where": link(c["raw"], "分享文字")})
        errors = []
        if media:
            for n, (digest, asset) in enumerate(source["assets"].items(), 1):
                cache = self.state / "extract" / (digest + ".json")
                try:
                    if cache.exists():
                        values = read_json(cache)
                    elif asset["kind"] == "image":
                        values = [["", line] for line in image_text(self.vault / asset["path"])]
                        write_json(cache, values)
                    elif asset["kind"] == "document":
                        values = list(document_text(self.vault / asset["path"]))
                        write_json(cache, values)
                    else:
                        values = list(video_text(self.vault / asset["path"]))
                        write_json(cache, values)
                    for time, value in values:
                        where = link(asset["path"], ("文档 " + time) if asset["kind"] == "document" else ("视频 " + time if time else f"配图 {n}"))
                        rows.append({"text": value, "where": where})
                except Exception as exc:
                    LOGGER.warning("附件提取待重试 %s %s", digest[:10], type(exc).__name__)
                    errors.append(f"附件 {n} 待提取（{str(exc)[:160] if isinstance(exc, ValueError) else type(exc).__name__}）")
                if asset["kind"] == "video":
                    thorough = source.get("importance") == "高"
                    profile = "dense" if thorough else "standard"
                    frame_cache = cache.with_name(f"{digest}-frames-v1-{profile}.json")
                    try:
                        if frame_cache.exists():
                            frames = read_json(frame_cache)
                        else:
                            frames = list(video_frames(self.vault / asset["path"], thorough=thorough))
                            write_json(frame_cache, frames)
                        rows.extend({"text": value, "where": link(asset["path"], "画面 " + time)} for time, value in frames)
                    except Exception as exc:
                        LOGGER.warning("视频画面识别待重试 %s %s", digest[:10], type(exc).__name__)
                        errors.append(f"视频 {n} 画面待识别（{type(exc).__name__}）")
        return rows, errors

    def analyze_images(self, source):
        if not self.local_models or not drawing_profile(source, current_analysis(source).get("rows", [])):
            return source
        if source.get("visual_retry_after", "") > now().isoformat():
            return source
        updated = deepcopy(source)
        reviews = updated.setdefault("visual_reviews", {})
        changed = False
        for digest, asset in source["assets"].items():
            if asset["kind"] != "image":
                continue
            if reviews.get(digest, {}).get("status") == "reviewed" and reviews[digest].get("version") == VISION_VERSION:
                continue
            cache = self.state / "extract" / f"{digest}-vision-v{VISION_VERSION}.json"
            try:
                result = read_json(cache) if cache.exists() else review_image(self.vault / asset["path"])
                write_json(cache, result)
                reviews[digest] = {**result, "path": asset["path"]}
            except Exception as exc:
                reviews[digest] = {"status": "pending", "path": asset["path"], "error": type(exc).__name__}
                LOGGER.warning("逐图视觉分析待重试 %s %s", digest[:10], type(exc).__name__)
            changed = True
        if changed:
            updated["visual_retry_after"] = (now() + timedelta(hours=1)).isoformat()
            updated.pop("drawing_version", None)
            self.catalog[source["id"]] = updated
            self.save()
            return updated
        return source

    def analyze(self, source):
        source = self.analyze_images(source)
        analysis = current_analysis(source)
        if source.get("drawing_version") != DRAWING_VERSION:
            rows = analysis.get("rows") or self.evidence(source)[0]
            updated = {**source, "drawing": drawing_profile(source, rows), "drawing_version": DRAWING_VERSION}
            self.catalog[source["id"]] = updated
            try:
                self.publish_source(updated)
            except Exception:
                self.catalog[source["id"]] = source
                raise
            source = updated
            self.refresh_topic()
            if source["drawing"]:
                marker = f'<!-- research-collector:drawing:{DRAWING_VERSION}:{source["id"]} -->'
                append_once(self.vault / "wiki/log.md", marker,
                            f'- {now().date().isoformat()} 登记绘图类型、参数依据与待补项 {link(source["note"])} {marker}')
        if analysis.get("done") or not self.local_models:
            return
        if analysis.get("retry_after", "") > now().isoformat():
            return
        rows, errors = self.evidence(source, media=True)
        text = "\n".join([source["title"], *(row["text"] for row in rows)])
        category = category_for(text)
        # A post that spells out hex values is a palette; the model tends to call it 科研绘图.
        palette = bool(HEX_COLOR.search(text))
        points = literal_points(rows)
        if rows:
            try:
                user_category = source.get("user_category", "")
                if source.get("importance") == "高" or source.get("drawing"):
                    if source.get("drawing") and user_category not in ("科研绘图", "配色"):
                        user_category = "科研绘图"
                    points += detailed_summary(rows, category=user_category)
                else:
                    model_category, model_points = summarize(rows, category=user_category) if user_category else summarize(rows)
                    if model_category and not palette:
                        category = model_category
                    points += model_points
            except Exception as exc:
                LOGGER.warning("本地摘要待重试 %s %s", source["id"], type(exc).__name__)
                errors.append(f"本地摘要待生成（{type(exc).__name__}）")
        updated = {**source, "category": source.get("user_category") or category,
                   "drawing": drawing_profile(source, rows), "drawing_version": DRAWING_VERSION,
                   "analysis": {"rows": rows, "points": points, "errors": errors, "done": not errors, "video_pipeline": 1,
                                "retry_after": (now() + timedelta(hours=1)).isoformat()}}
        self.catalog[source["id"]] = updated
        try:
            self.publish_source(updated)
        except Exception:
            self.catalog[source["id"]] = source
            raise
        self.refresh_topic()

    def source_body(self, source):
        recent = list(source["captures"].values())[-1]
        missing = self.coverage(source)
        rows, _ = self.evidence(source)
        analysis = current_analysis(source) or {"rows": rows, "points": literal_points(rows), "errors": []}
        title = plain(source["title"])
        author = next((c["page"]["author"] for c in source["captures"].values() if c["page"].get("author")), "")
        url = next((c["url"] for c in source["captures"].values() if identity(c["url"])), "")
        lines = [
            "## 出处与读取范围", "",
            f"- 标题 {title}", f'- 平台 {recent["platform"]}', f"- {web_link(url)}",
            f"- 作者 {plain(author) if author else '未取得'}",
            f'- 采集日期 {source["date"]}',
            f'- 收录状态 {"待补" if missing else "已归档"}',
            *("- " + plain(value) for value in missing),
        ]
        if source.get("user_category"):
            lines.append(f'- 资料类型 {plain(source["user_category"])}')
        if source.get("importance"):
            lines.append(f'- 重要程度 {source["importance"]}')
        if source.get("importance") == "高":
            lines.append("- #科研工具/高重要程度")
        for c in source["captures"].values():
            lines.append(f'- {link(c["raw"], "原始记录 " + c["date"])}')
            if c.get("page_raw"):
                lines.append(f'- {link(c["page_raw"], "网页补查原文")}')
            if c["page"].get("via"):
                lines.append(f'- 读取方式 {plain(c["page"]["via"])}')
            if search := c["page"].get("search_status"):
                lines.append(f"- 网上补查 {plain(search)}")
            canonical = c["page"].get("canonical", "")
            if work_key(canonical) and canonical != c["url"]:
                lines.append(f'- {web_link(canonical, "核实的作品链接")}')
            if c["page"].get("auth_required"):
                lines.append(f'- {web_link(canonical or c["url"], "登录后查看原帖")}')
            for candidate in c["page"].get("search_candidates", []):
                lines.append(f'- {web_link(candidate, "标题相符的候选原帖 · 待核对")}')
            if c.get("complete"):
                lines.append("- 用户确认本次补传包含完整正文和全部原始媒体")
            if limit := c["page"].get("limit"):
                lines.append(f"- {plain(limit)}")
        lines += ["", "## 已保存的附件", ""]
        lines.extend(f'- {link(asset["path"], asset["origin"] + " · " + {"image": "图片", "video": "视频", "document": "文档"}.get(asset["kind"], "附件"))}'
                     for asset in source["assets"].values())
        if not source["assets"]:
            lines.append("- 暂无可读取附件")
        if "完整视频未取得" in missing:
            lines.append("- 可用同一条作品链接补传视频文件，转录和画面识别会更新这张来源卡。")
        lines += ["", "## 科研工具要点", ""]
        for point in analysis["points"]:
            refs = "、".join(f'证据 {i}' for i in point["evidence"])
            lines.append(f'- **{point["label"]}** {plain(point["text"])}（{refs}）')
        lines.extend("- " + plain(error) for error in analysis["errors"])
        if not analysis["points"]:
            lines.append("- 暂无可定位的工具要点")
        lines += ["", "### 证据定位", ""]
        for i, row in enumerate(analysis["rows"], 1):
            lines.append(f'{i}. {row["where"]} — {plain(row["text"])}')
        lines += ["", f'关联专题 {link(TOPIC, "科研工具")}']
        lines += profile_lines(source.get("drawing", {}), link, plain)
        return "\n".join(lines)

    def publish_source(self, source):
        path = self.vault / source["note"]
        if source["block"] and not path.exists():
            found = list((self.vault / "wiki/sources").glob(source["id"] + "-*.md"))
            if len(found) != 1:
                raise OSError(f'已登记来源文件不可用 {source["id"]}')
            path = found[0]
            source["note"] = path.relative_to(self.vault).as_posix()
        first = next(iter(source["captures"].values()))
        extra = [("source_id", source["id"]), ("raw_path", first["raw"])]
        properties = {}
        if any(douyin_video(capture) for capture in source["captures"].values()):
            properties["source_kind"] = "video"
        else:
            extra.append(("source_kind", "article"))
        if source.get("user_category"):
            properties["research_category"] = source["user_category"]
        if source.get("importance"):
            properties["research_importance"] = source["importance"]
            properties["research_priority"] = IMPORTANCE[source["importance"]]
        extra.extend(properties.items())
        prefix = front(source["title"], "source", now().date().isoformat(), ["科研工具"], extra)
        prefix += f'# {plain(source["title"])}\n\n'
        source["block"] = replace_block(path, prefix, self.source_body(source), source["block"], properties)
        self.save()

    def publish_capture(self, source, capture, capture_id):
        if "analysis" not in source:
            rows, _ = self.evidence(source)
            source["category"] = source.get("user_category") or category_for(
                "\n".join([source["title"], *(row["text"] for row in rows)]))
        self.publish_source(source)
        marker = f"<!-- research-collector:{capture_id} -->"
        status = "待补" if self.coverage(source) else "已归档"
        date = capture["date"]
        daily = self.vault / f"{TOPIC}/每日/{date}.md"
        if not daily.exists():
            header = front(f"科研资料 {date}", "topic", now().date().isoformat(), ["科研工具/每日"])
            atomic_write(daily, (header + f"# 科研资料 {date}\n").encode("utf-8"))
        item = f'- {capture["event"]} · {status} · {link(source["note"], capture["title"])} · 接收 {capture["received"][:16]} {marker}'
        append_once(daily, marker, item)
        self.sync_daily_status((source,))
        append_once(self.vault / "wiki/log.md", marker, f'- {now().date().isoformat()} 科研资料{capture["event"]} {link(source["note"])} {marker}')
        self.refresh_topic()

    def refresh_topic(self):
        path = self.vault / (TOPIC + ".md")
        if not self.vault.is_dir():
            return
        prefix = front("科研工具", "topic", now().date().isoformat(), ["科研工具"]) + "# 科研工具\n\n"
        lines = ["## 来源", ""]
        sources = sorted((s for s in self.catalog.values() if (self.vault / s["note"]).exists()),
                         key=lambda s: (IMPORTANCE.get(s.get("importance"), 2), s["date"], s["id"]), reverse=True)
        high = [s for s in sources if s.get("importance") == "高"]
        if high:
            lines = ["## 优先阅读", "", *(f'- {link(s["note"], s["title"])}' +
                     (" · 待补" if self.coverage(s) else "") for s in high), "", *lines]
        presets = ("科研绘图", "写作润色", "学科进展", "配色", "数据处理", "其他科研工具")
        categories = (*presets, *sorted({s["category"] for s in sources} - set(presets)))
        for category in categories:
            group = [s for s in sources if s["category"] == category]
            if group:
                lines += [f"### {plain(category)}", ""]
                for s in group:
                    status = " · 待补" if self.coverage(s) else ""
                    lines.append(f'- {link(s["note"], s["title"])} · {s["date"]}{status}')
                lines.append("")
        if not self.catalog:
            lines += ["暂无入库资料。", ""]
        daily = sorted((self.vault / TOPIC / "每日").glob("*.md"), reverse=True)
        drawing_sources = [s for s in sources if s.get("drawing")]
        if drawing_sources:
            lines += ["## 绘图灵感索引", "", "按原文提及图型检索，实际图片类型及缺失参数仍需核对。", ""]
            names = sorted({name for s in drawing_sources for name in s["drawing"]["types"]})
            for name in names:
                lines += [f"### {plain(name)}", ""]
                for s in drawing_sources:
                    if name in s["drawing"]["types"]:
                        missing_fields = [key for key, values in s["drawing"]["parameters"].items() if not values]
                        lines.append(f'- {link(s["note"], s["title"])}' +
                                     (" · 待补 " + "、".join(missing_fields) if missing_fields else " · 已登记原文参数"))
            unknown = [s for s in drawing_sources if not s["drawing"]["types"]]
            if unknown:
                lines += ["### 图型待识别", "", *(f'- {link(s["note"], s["title"])}' for s in unknown)]
        lines += ["## 每日记录", ""]
        lines.extend(f"- {link(p.relative_to(self.vault).as_posix(), p.stem)}" for p in daily)
        marker = self.state / "topic-block.txt"
        previous = marker.read_text(encoding="ascii") if marker.exists() else ""
        try:
            block = replace_block(path, prefix, "\n".join(lines), previous)
        except ValueError as exc:
            LOGGER.warning("%s，保留人工内容，来源卡和日志仍会更新", exc)
            return
        if block != previous:
            atomic_write(marker, block.encode("ascii"))
