"""Phone-facing details from saved evidence, without fetching external pages."""

import re

from public_page import aliases_for
from storage import RequestError, read_json, valid_id
from wiki import current_analysis


def detail(wiki, inbox, capture_id):
    if not valid_id(capture_id):
        raise RequestError(404, "记录不存在")
    source = next((s for s in wiki.catalog.values() if capture_id in s["captures"]), None)
    folder = inbox.directory / capture_id
    if source is None:
        if not (folder / "committed.json").exists():
            raise RequestError(404, "记录尚未收到")
        item = read_json(folder / "manifest.json")
        return {"id": capture_id, "title": item["title"], "url": item["url"],
                "category": item.get("category", ""), "importance": item.get("importance", "中"),
                "body": "电脑已收到，正在等待入库。", "details": []}
    capture = source["captures"][capture_id]
    analysis = current_analysis(source)
    missing = wiki.coverage(source)
    lines = ["资料类型  " + source.get("user_category", source["category"]),
             "重要程度  " + source.get("importance", "中"), "知识库编号  " + source["id"], "", "已保存素材"]
    assets = list(source["assets"].values())
    image_count = sum(a["kind"] == "image" for a in assets)
    video_count = sum(a["kind"] == "video" for a in assets)
    if assets:
        lines.append(f"图片 {image_count} 张 · 视频 {video_count} 个")
    if not assets:
        lines.append("暂无图片或视频")
    lines += ["", "待补资料", *(missing or ["原文与附件已齐全"]), "", "处理要点"]
    for point in analysis.get("points", []):
        lines.append(point["label"] + "  " + point["text"])
        rows = analysis.get("rows", [])
        lines.extend("依据  " + re.sub(r"\[\[[^\]|]+\|([^\]]+)\]\]", r"\1", rows[i - 1]["where"])
                     for i in point["evidence"] if 0 < i <= len(rows))
    if not analysis.get("points"):
        lines.append("暂无已提取要点")
    lines.extend(analysis.get("errors", []))
    drawing = source.get("drawing", {})
    if drawing:
        lines += ["", "绘图灵感", *drawing.get("display", [])]
    return {"id": capture_id, "source_id": source["id"], "title": source["title"],
            "url": capture["page"].get("canonical") or capture["url"],
            "category": source.get("user_category", source["category"]),
            "importance": source.get("importance", "中"), "details": missing,
            "body": "\n".join(lines)[:60000]}


def lookup(wiki, inbox, url):
    aliases = set(aliases_for(url))
    if not aliases:
        return None
    for source in wiki.catalog.values():
        if aliases.intersection(source["aliases"]):
            return detail(wiki, inbox, next(reversed(source["captures"])))
    for path in inbox.directory.glob("*/committed.json"):
        item = read_json(path.with_name("manifest.json"))
        if aliases.intersection(aliases_for(item["url"])):
            return detail(wiki, inbox, path.parent.name)
    return None
