"""Evidence-linked drawing vocabulary and parameter records. Unknowns stay explicit."""

import re
from vision import coverage

VERSION = 2
TYPES = {
    "技术路线图": ("技术路线图", "研究路线图"),
    "流程图": ("流程图", "flowchart"),
    "机制示意图": ("机制图", "机理图", "机制示意图", "机理示意图"),
    "实验装置图": ("装置图", "装置示意图"),
    "折线图": ("折线图", "line plot", "line chart"),
    "散点图": ("散点图", "scatter plot"),
    "柱状图": ("柱状图", "条形图", "bar chart", "bar plot"),
    "箱线图": ("箱线图", "箱型图", "box plot", "boxplot"),
    "小提琴图": ("小提琴图", "violin plot"),
    "热图": ("热力图", "热图", "heatmap"),
    "桑基图": ("桑基图", "sankey"),
    "弦图": ("弦图", "chord diagram"),
    "雷达图": ("雷达图", "radar chart"),
    "气泡图": ("气泡图", "bubble chart"),
    "山脊图": ("山脊图", "岭线图", "ridgeline"),
    "等高线图": ("等高线图", "contour plot"),
    "三维曲面图": ("三维曲面图", "3d surface"),
    "分布图": ("分布图", "distribution plot"),
    "组合图": ("组合图", "复合图", "多面板图"),
    "色板": ("色板", "色卡", "调色板", "palette"),
}
FIELDS = {
    "布局": ("布局", "横向", "纵向", "网格", "面板", "间距", "比例", "留白"),
    "配色": ("色值", "配色", "颜色", "色板", "渐变", "rgb", "cmyk", "hex"),
    "字体": ("字体", "字号", "font", "pt"),
    "线条与标记": ("线宽", "线型", "标记", "透明度", "linewidth", "alpha", "marker"),
    "数据映射": ("坐标", "轴", "尺度", "归一化", "映射", "分组", "误差", "图例", "log"),
    "软件与步骤": ("工具", "软件", "版本", "步骤", "代码", "python", "origin", "matplotlib", "ggplot"),
    "导出": ("导出", "分辨率", "dpi", "svg", "pdf", "tiff", "像素", "画布"),
    "用途与限制": ("适用", "用途", "场景", "限制", "避免", "不适合"),
}


def drawing_profile(source, rows):
    corpus = "\n".join([source["title"], source.get("user_category", ""), source.get("category", ""),
                        *(row["text"] for row in rows)]).lower()
    if not (any(word in corpus for word in ("绘图", "作图", "配色", "图表", "示意图"))
            or any(alias in corpus for aliases in TYPES.values() for alias in aliases)):
        return {}
    # The source title identifies the topic, never proves what a particular image shows.
    evidence = [{"text": source["title"], "where": "来源标题"}, *rows]
    types, parameters = {}, {key: [] for key in FIELDS}
    for row in evidence:
        text, where = row["text"], row["where"]
        lower = text.lower()
        names = [name for name, aliases in TYPES.items() if any(alias in lower for alias in aliases)]
        # Explicitly named new types are retained verbatim and can later receive their own examples.
        names += re.findall(r"(?:图型|图类型|图表类型|图片类型)\s*[：:]\s*([^\n，。；;]{1,40})", text)
        for name in dict.fromkeys(names):
            name = next((key for key, aliases in TYPES.items() if name.strip().lower() in aliases), name.strip())
            citation = {"text": text[:1500], "where": where}
            if citation not in types.setdefault(name, []):
                types[name].append(citation)
        if where == "来源标题":
            continue
        for line in text.splitlines():
            for field, words in FIELDS.items():
                if any(word in line.lower() for word in words) or (field == "配色" and re.search(r"#[0-9a-fA-F]{6}\b", line)):
                    value = {"text": line.strip()[:1500], "where": where}
                    if value not in parameters[field]:
                        parameters[field].append(value)
    display = ["图型（原文提及，实际图像待核对）  " + ("、".join(types) or "待识别")]
    for field, values in parameters.items():
        display.append(field + ("  " + "；".join(value["text"] for value in values[:4]) if values else "  原文未取得，待补"))
    assets = []
    for asset in source["assets"].values():
        mentions = [name for name, citations in types.items()
                    if any(asset["path"] in c["where"] for c in citations)]
        assets.append({"path": asset["path"], "kind": asset["kind"], "types": mentions,
                       "type_status": ("该素材文字提及 " + "、".join(mentions) + "，图像结构待核对")
                       if mentions else "图型待识别，待核对实际图像"})
    visual = coverage(source)
    display.insert(0, f'逐图视觉分析 {visual["reviewed"]}/{visual["total"]} 张（自动分析需复核；仅统计本地已保存图片）')
    if not visual["total"]:
        display.insert(1, "无可分析图片；视频文字识别不计作逐图视觉分析")
    return {"version": VERSION, "visual_coverage": visual, "visual_reviews": source.get("visual_reviews", {}), "types": types, "new_types": [name for name in types if name not in TYPES], "parameters": parameters,
            "assets": assets, "display": display}


def profile_lines(profile, link, plain):
    if not profile:
        return []
    lines = ["", "## 绘图灵感与复用参数", "",
             "文字证据与逐图视觉分析分别记录；模型观察需人工复核，复现建议不是作者原始步骤。", "",
             *profile.get("display", [])[:1], ""]
    for digest, review in profile.get("visual_reviews", {}).items():
        lines += ["", "### 逐图分析 " + link(review["path"]), ""]
        if review.get("status") == "reviewed":
            lines.append("- 模型：" + plain(review.get("model", "")))
            lines.append("- 复核：" + ("已由助手对照原图核对" if review.get("verification") == "assistant_checked" else "自动视觉分析，待人工核对"))
            lines.extend(f'- **{plain(key)}**：{plain(value)}' for key, value in review["observations"].items())
        else:
            lines.append("- 待分析：" + plain(review.get("error", "尚未完成")))
    if not profile["types"]:
        lines.append("- 图型待识别")
    for name, citations in profile["types"].items():
        lines.append(f'- 提及图型 **{plain(name)}**' + (" · 新类型名称，待核对命名与复现参数" if name in profile.get("new_types", []) else ""))
        lines.extend(f'  - {plain(c["text"])} — {c["where"]}' for c in citations)
    for field, values in profile["parameters"].items():
        lines += ["", f"### {field}", ""]
        lines.extend(f'- {plain(v["text"])} — {v["where"]}' for v in values)
        if not values:
            lines.append("- 原文未取得，待补。")
    lines += ["", "### 对应图片与视频", ""]
    lines.extend(f'- {link(a["path"])} · {a["type_status"]}' for a in profile["assets"])
    if not profile["assets"]:
        lines.append("- 原图或视频待补。")
    return lines
