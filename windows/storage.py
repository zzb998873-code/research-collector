"""Durable, resumable inbox shared by the HTTPS receiver and the wiki worker."""

import hashlib
import json
import os
import re
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

CHUNK_SIZE = 1024 * 1024
MAX_FILE = 512 * CHUNK_SIZE
MAX_PACKAGE = 768 * CHUNK_SIZE
MAX_MANIFEST = 512 * 1024
CHINA = timezone(timedelta(hours=8))
ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
DIGEST = re.compile(r"[0-9a-f]{64}")
MEDIA_EXTENSIONS = {
    ".pdf": "document", ".docx": "document", ".doc": "document",
    ".jpg": "image", ".jpeg": "image", ".png": "image", ".webp": "image",
    ".gif": "image", ".heic": "image", ".heif": "image", ".bmp": "image",
    ".mp4": "video", ".mov": "video", ".webm": "video", ".m4v": "video",
}


class RequestError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def now():
    return datetime.now(CHINA)


def json_bytes(data):
    return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"


def read_json(path):
    return json.loads(path.read_bytes())


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.parent / (".collector-" + uuid.uuid4().hex + ".tmp")
    try:
        with temp.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def write_json(path, data):
    atomic_write(path, json_bytes(data))


def sha256(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def text_value(item, key, limit):
    value = item.get(key, "")
    if not isinstance(value, str) or len(value) > limit or "\0" in value:
        raise RequestError(400, f"无效字段 {key}")
    return value


def valid_id(value):
    return isinstance(value, str) and ID.fullmatch(value) is not None


IMPORTANCE = {"低": 1, "中": 2, "高": 3}


def validate_manifest(item):
    if not isinstance(item, dict) or item.get("version") != 1 or not valid_id(item.get("id")):
        raise RequestError(400, "无效的收集包")
    try:
        date = datetime.fromisoformat(text_value(item, "captured_at", 40))
        if date.utcoffset() is None or not 2000 <= date.year <= 2100:
            raise ValueError
    except ValueError as exc:
        raise RequestError(400, "采集时间必须带时区") from exc
    trigger = text_value(item, "trigger", 20)
    if trigger not in {"share", "paste", "supplement", "dual"}:
        raise RequestError(400, "无效的收集方式")
    attachments = item.get("attachments")
    if not isinstance(attachments, list) or len(attachments) > 64:
        raise RequestError(400, "附件应为列表，最多 64 个")
    result = {"version": 1, "id": item["id"], "captured_at": date.isoformat(), "trigger": trigger}
    complete = item.get("complete", False)
    if type(complete) is not bool:
        raise RequestError(400, "无效的完整性确认")
    result["complete"] = complete
    if item.get("parent_id"):
        if not valid_id(item["parent_id"]) or item["parent_id"] == item["id"] or trigger != "supplement":
            raise RequestError(400, "补充记录关联无效")
        result["parent_id"] = item["parent_id"]
    for key, limit in (("url", 4000), ("title", 500), ("text", 90000)):
        result[key] = text_value(item, key, limit).strip()
    if "category" in item:
        category = text_value(item, "category", 32).strip()
        if category and any(not char.isprintable() for char in category):
            raise RequestError(400, "资料类型必须是单行文字")
        if category:
            result["category"] = category
    if "importance" in item:
        importance = text_value(item, "importance", 1)
        if importance not in IMPORTANCE:
            raise RequestError(400, "重要程度应为低、中或高")
        result["importance"] = importance
    if not (result["text"] or result["url"] or attachments):
        raise RequestError(400, "收集包没有链接、文字或附件")
    if trigger == "supplement" and not result["url"] and not result.get("parent_id"):
        raise RequestError(400, "补传需要原帖链接")
    if complete and (trigger != "supplement" or not result["text"].replace(result["url"], "").strip()
                     or not attachments):
        raise RequestError(400, "确认已补齐时请提供原帖链接、正文和附件")
    parsed = []
    seen = set()
    total = 0
    for file in attachments:
        if not isinstance(file, dict):
            raise RequestError(400, "无效的附件")
        fid, digest, size = file.get("id"), file.get("sha256"), file.get("size")
        name = text_value(file, "name", 200)
        extension = Path(name).suffix.lower()
        kind = MEDIA_EXTENSIONS.get(extension)
        if (not valid_id(fid) or fid in seen or not isinstance(digest, str)
                or not DIGEST.fullmatch(digest) or type(size) is not int
                or not 0 < size <= MAX_FILE or not kind or file.get("kind") != kind
                or any(char in name for char in '/\\:')):
            raise RequestError(400, "附件名称、类型、尺寸或校验值无效")
        total += size
        seen.add(fid)
        parsed.append({"id": fid, "name": name, "kind": kind, "size": size, "sha256": digest})
    if total > MAX_PACKAGE:
        raise RequestError(413, "单次收集包超过 768 MiB")
    result["attachments"] = parsed
    return result


class Inbox:
    def __init__(self, directory):
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.processing = None

    def _folder(self, capture_id):
        if not valid_id(capture_id):
            raise RequestError(404, "收集包不存在")
        return self.directory / capture_id

    @staticmethod
    def _manifest(folder):
        try:
            return read_json(folder / "manifest.json")
        except FileNotFoundError as exc:
            raise RequestError(404, "收集包不存在") from exc

    def begin(self, item):
        item = validate_manifest(item)
        folder = self._folder(item["id"])
        with self.lock:
            path = folder / "manifest.json"
            if path.exists():
                if read_json(path) != item:
                    raise RequestError(409, "同一收集包编号已有不同内容")
            else:
                write_json(path, item)
            offsets = {}
            complete = (folder / "committed.json").exists()
            for file in item["attachments"]:
                path = folder / (file["id"] + ".part")
                offsets[file["id"]] = file["size"] if complete else (
                    path.stat().st_size if path.exists() else 0
                )
            return {"committed": complete, "offsets": offsets}

    def upload(self, capture_id, file_id, offset, data):
        folder = self._folder(capture_id)
        if not data or len(data) > CHUNK_SIZE:
            raise RequestError(400, "无效分块")
        with self.lock:
            manifest = self._manifest(folder)
            file = next((file for file in manifest["attachments"] if file["id"] == file_id), None)
            if file is None:
                raise RequestError(404, "附件不存在")
            if (folder / "committed.json").exists():
                return {"offset": file["size"]}
            path = folder / (file_id + ".part")
            length = path.stat().st_size if path.exists() else 0
            if offset != length:
                return {"offset": length}
            if length + len(data) > file["size"]:
                raise RequestError(400, "附件超出申报大小")
            with path.open("ab") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            return {"offset": length + len(data)}

    def commit(self, capture_id):
        folder = self._folder(capture_id)
        with self.lock:
            item = self._manifest(folder)
            receipt = folder / "committed.json"
            if not receipt.exists():
                for file in item["attachments"]:
                    path = folder / (file["id"] + ".part")
                    if not path.exists() or path.stat().st_size != file["size"]:
                        raise RequestError(409, "附件尚未收齐")
                    if sha256(path) != file["sha256"]:
                        path.unlink()
                        raise RequestError(409, "附件校验失败，需重新传输")
                write_json(receipt, {"received_at": now().isoformat()})
            return {"committed": True, "id": capture_id}
