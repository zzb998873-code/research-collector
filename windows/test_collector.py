import hashlib
import http.client
import json
import ssl
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote

from bs4 import BeautifulSoup

from collector import CollectorServer, configure, process_pending
from public_page import (embedded_objects, find_url, full_post, identity, permitted, platform_for,
                         read_page, resolve_page, walk)
from storage import Inbox, read_json
from wiki import Wiki, capture_title


class FakeSession:
    """Stands in for the logged-in browser profile without starting one."""

    def __init__(self, html="<html></html>", final="", dom=None, ready=True):
        self.html, self.final, self.dom, self.state = html, final, dom or {}, ready

    def ready(self, platform="小红书"):
        return self.state

    def read(self, url):
        return {"html": self.html, "final": self.final or url, "dom": self.dom}

    def cookie_header(self, url):
        return "a1=1; web_session=2"


def sample(url, text="", data=None, name="palette.png", complete=False):
    capture = {"version": 1, "id": str(uuid.uuid4()), "captured_at": "2026-11-11T23:51:00+08:00",
               "trigger": "supplement" if data else "share", "title": "科研配色", "url": url,
               "text": text, "attachments": [], "complete": complete}
    if data:
        capture["attachments"].append({
            "id": str(uuid.uuid4()), "name": name, "kind": "image" if name.endswith(".png") else "video",
            "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
        })
    return capture


class CollectorTest(unittest.TestCase):
    def test_document_upload_preserves_file_and_extracts_without_video(self):
        import io
        from docx import Document
        doc = Document()
        doc.add_paragraph("Abrasion measurement method")
        buffer = io.BytesIO()
        doc.save(buffer)
        data = buffer.getvalue()
        cap = sample("", "", data, "paper.docx")
        cap.update(trigger="share", title="", category="学科进展")
        cap["attachments"][0]["kind"] = "document"
        self.assertEqual(self.begin(cap)[0], 200)
        fid = cap["attachments"][0]["id"]
        self.assertEqual(self.request("PUT", f'/v1/captures/{cap["id"]}/files/{fid}', data, offset=0)[0], 200)
        self.assertEqual(self.commit(cap)[0], 200)
        wiki = Wiki(self.vault, self.state, False, True)
        with patch("wiki.video_text") as video, patch("wiki.summarize", return_value=("", [])):
            process_pending(self.inbox, wiki)
        source = next(iter(wiki.catalog.values()))
        self.assertEqual(source["title"], "paper.docx")
        self.assertEqual(wiki.coverage(source), [])
        self.assertEqual(source["analysis"]["rows"][0]["text"], "Abrasion measurement method")
        self.assertEqual((self.vault / next(iter(source["assets"].values()))["path"]).read_bytes(), data)
        video.assert_not_called()

    def test_detail_lookup_parent_supplement_and_retry(self):
        original = sample("", "图型：环形层叠图\n线宽 1.5 pt\n色值 #123456", b"image")
        original["trigger"] = "share"
        self.assertEqual(self.begin(original)[0], 200)
        fid = original["attachments"][0]["id"]
        self.request("PUT", f'/v1/captures/{original["id"]}/files/{fid}', b"image", offset=0)
        self.commit(original)
        wiki = Wiki(self.vault, self.state, False, False)
        process_pending(self.inbox, wiki)
        endpoint = f'/v1/captures/{original["id"]}/detail'
        self.assertEqual(self.request("GET", endpoint, token=False)[0], 401)
        detail = self.request("GET", endpoint)[1]["record"]
        self.assertIn("环形层叠图", detail["body"])
        self.assertIn("1.5 pt", detail["body"])
        supplement = dict(sample("", "补充参数：画布 180 mm，导出 600 dpi"),
                          parent_id=original["id"], trigger="supplement", category="科研绘图", importance="高")
        self.assertEqual(self.begin(supplement)[0], 200)
        self.commit(supplement)
        process_pending(self.inbox, wiki)
        self.assertEqual(len(wiki.catalog), 1)
        source = next(iter(wiki.catalog.values()))
        self.assertEqual(len(source["captures"]), 2)
        self.assertEqual(source["importance"], "高")
        self.assertIn("600 dpi", self.request("GET", endpoint)[1]["record"]["body"])
        self.assertEqual(self.request("POST", f'/v1/captures/{original["id"]}/retry', '{}')[0], 200)
        process_pending(self.inbox, wiki, extract=False)
        self.assertEqual(read_json(self.state / 'retry' / (original['id'] + '.json')),
                         read_json(self.state / 'retry' / (original['id'] + '.done')))
        linked = sample("https://www.xiaohongshu.com/explore/0123456789abcdef01234567?utm_source=test")
        self.begin(linked)
        self.commit(linked)
        process_pending(self.inbox, wiki, extract=False)
        result = self.request("POST", "/v1/lookup", json.dumps({"url": linked["url"].split('?')[0]}))[1]
        self.assertIsNotNone(result["record"])
        self.assertEqual(self.request("POST", "/v1/lookup", '{}')[0], 400)
        self.assertEqual(self.request("POST", "/v1/lookup", json.dumps({"url": "https://example.org/"}))[1]["record"], None)

    def test_drawing_new_type_keeps_evidence_and_missing_parameters(self):
        from drawing import drawing_profile
        source = {"title": "配色教程", "category": "配色", "assets": {"a": {"path": "raw/assets/a.png", "kind": "image"}}}
        rows = [{"text": "图型：环形层叠图\n配色 #AABBCC\n线宽 1.5 pt\n导出 SVG 600 dpi",
                 "where": "[[raw/assets/a.png|配图 1]]"}]
        profile = drawing_profile(source, rows)
        self.assertIn("环形层叠图", profile["types"])
        self.assertEqual(profile["parameters"]["布局"], [])
        self.assertEqual(profile["assets"][0]["types"], ["环形层叠图"])
        self.assertIn("1.5 pt", profile["parameters"]["线条与标记"][0]["text"])
        self.assertEqual(profile["parameters"]["配色"][0]["where"], rows[0]["where"])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.state, self.vault = root / "state", root / "vault"
        (self.vault / "wiki/sources").mkdir(parents=True)
        (self.vault / "首页.md").write_text("# 首页\n\n原有内容\n", encoding="utf-8")
        (self.vault / "wiki/index.md").write_text("# 索引\n", encoding="utf-8")
        (self.vault / "wiki/log.md").write_text("# 日志\n\n原有日志\n", encoding="utf-8")
        self.config = configure(self.state, self.vault, "127.0.0.1", 0, False, False)
        self.inbox = Inbox(self.state / "inbox")
        self.server = CollectorServer(self.state, self.config, self.inbox)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, method, endpoint, body=None, token=True, offset=None):
        context = ssl.create_default_context(cafile=self.state / "ca.pem")
        conn = http.client.HTTPSConnection("127.0.0.1", self.server.server_port, context=context)
        headers = {"Authorization": f'Bearer {self.config["token"]}'} if token else {}
        if offset is not None:
            headers["Upload-Offset"] = str(offset)
        try:
            conn.request(method, endpoint, body, headers)
            response = conn.getresponse()
            return response.status, json.loads(response.read())
        finally:
            conn.close()

    def begin(self, capture):
        return self.request("POST", "/v1/captures", json.dumps(capture))

    def commit(self, capture):
        return self.request("POST", f'/v1/captures/{capture["id"]}/commit')

    def test_resume_hash_recovery_and_ack(self):
        url = "https://www.douyin.com/video/7412345678901234567"
        data = b"abc123" * 10
        cap = sample(url, "视频", data, "clip.mp4")
        media = cap["attachments"][0]
        endpoint = f'/v1/captures/{cap["id"]}/files/{media["id"]}'
        self.assertEqual(self.request("GET", "/v1/health", token=False)[0], 401)
        self.assertEqual(self.begin(cap)[1]["offsets"][media["id"]], 0)
        self.assertEqual(self.request("PUT", endpoint, data[:13], offset=0)[1]["offset"], 13)
        self.assertEqual(self.commit(cap)[0], 409)
        self.server.inbox = Inbox(self.state / "inbox")
        self.assertEqual(self.begin(cap)[1]["offsets"][media["id"]], 13)
        self.assertEqual(self.request("PUT", endpoint, data[:13], offset=0)[1]["offset"], 13)
        self.assertEqual(self.request("PUT", endpoint, b"x" * (len(data) - 13), offset=13)[0], 200)
        self.assertEqual(self.commit(cap)[0], 409)
        self.assertEqual(self.begin(cap)[1]["offsets"][media["id"]], 0)
        self.assertEqual(self.request("PUT", endpoint, data, offset=0)[0], 200)
        self.assertTrue(self.commit(cap)[1]["committed"])
        self.assertTrue(self.begin(cap)[1]["committed"])
        self.assertTrue(self.commit(cap)[1]["committed"])

    def test_supplement_same_card_keep_comments_and_original(self):
        url = "https://www.xiaohongshu.com/explore/0123456789abcdef01234567"
        first = sample(url + "?utm_source=phone")
        recent = "/v1/captures/recent"
        self.assertEqual(self.request("GET", recent, token=False)[0], 401)
        self.assertEqual(self.begin(first)[0], 200)
        self.assertEqual(self.request("GET", recent)[1]["items"], [])
        self.assertEqual(self.commit(first)[0], 200)
        self.assertEqual(self.request("GET", recent)[1]["items"][0]["status"], "received")
        self.inbox.processing = first["id"]
        self.assertEqual(self.request("GET", recent)[1]["items"][0]["status"], "processing")
        wiki = Wiki(self.vault, self.state, False, False)
        process_pending(self.inbox, wiki)
        item = self.request("GET", recent)[1]["items"][0]
        self.assertEqual(item["status"], "missing")
        self.assertEqual(item["date"], "2026-11-11")
        self.assertIn("作者完整正文未核实", item["details"])
        card = next((self.vault / "wiki/sources").glob("S*.md"))
        self.assertIn("收录状态 待补", card.read_text(encoding="utf-8"))
        daily_path = self.vault / "wiki/topics/科研工具/每日/2026-11-11.md"
        with daily_path.open("a", encoding="utf-8") as handle:
            handle.write("\n- 人工核对 · 待补 · 等待另一个来源\n")
        raw = next((self.vault / "raw/inbox/科研工具").glob("*.md"))
        original = raw.read_bytes()
        with card.open("a", encoding="utf-8") as handle:
            handle.write("\n人工批注保留\n")
        data = b"sample attachment"
        second = sample(url, f"{url}\n科研绘图用 #145447", data, complete=True)
        self.assertEqual(self.begin(second)[0], 200)
        fid = second["attachments"][0]["id"]
        endpoint = f'/v1/captures/{second["id"]}/files/{fid}'
        self.assertEqual(self.request("PUT", endpoint, data, offset=0)[0], 200)
        self.assertEqual(self.commit(second)[0], 200)
        wiki = Wiki(self.vault, self.state, False, False)
        process_pending(self.inbox, wiki)
        process_pending(self.inbox, wiki)
        items = self.request("GET", recent)[1]["items"]
        self.assertEqual([(entry["source_id"], entry["status"]) for entry in items], [(item["source_id"], "archived")])
        self.server.models = True
        self.assertEqual(self.request("GET", recent)[1]["items"][0]["status"], "processing")
        self.assertEqual(list((self.vault / "wiki/sources").glob("S*.md")), [card])
        text = card.read_text(encoding="utf-8")
        self.assertIn("人工批注保留", text)
        self.assertIn("收录状态 已归档", text)
        self.assertIn("145447", text)
        self.assertEqual(raw.read_bytes(), original)
        self.assertEqual(len(list((self.vault / "raw/assets/科研工具").glob("*.png"))), 1)
        daily = daily_path.read_text(encoding="utf-8")
        self.assertIn("- 人工核对 · 待补 · 等待另一个来源", daily)
        self.assertNotIn("- 新增 · 待补", daily)
        self.assertEqual(daily.count("· 已归档"), 2)
        self.assertEqual(read_json(self.state / "catalog.json")["S0001"]["category"], "配色")
        self.assertIn("原有日志", (self.vault / "wiki/log.md").read_text(encoding="utf-8"))

    def test_video_and_images_point_to_local_evidence(self):
        url = "https://www.douyin.com/video/7412345678901234567"
        data = b"mock video container"
        cap = sample(url, "参数 600 dpi", data, "clip.mp4")
        self.begin(cap)
        fid = cap["attachments"][0]["id"]
        self.request("PUT", f'/v1/captures/{cap["id"]}/files/{fid}', data, offset=0)
        self.commit(cap)
        wiki = Wiki(self.vault, self.state, False, True)
        with patch("wiki.video_text", return_value=[("00:08", "Origin 绘图")]), patch(
            "wiki.video_frames", return_value=[("00:10", "600 DPI")]
        ), patch(
            "wiki.detailed_summary", return_value=[{"label": "工具", "text": "Origin", "evidence": [2]}]
        ):
            process_pending(self.inbox, wiki)
        note = next((self.vault / "wiki/sources").glob("*.md")).read_text(encoding="utf-8")
        self.assertIn("视频 00:08", note)
        self.assertIn("画面 00:10", note)
        self.assertIn("Origin（证据 2）", note)

    def test_douyin_short_link_keeps_share_boilerplate_out_of_analysis(self):
        short = "https://v.douyin.com/sample/"
        url = "https://www.douyin.com/video/7412345678901234567"
        cap = dict(sample(short, f"2.87 复制打开抖音，看看【作者的作品】机制图教程… {short}"), title="")
        self.begin(cap)
        self.commit(cap)
        page = dict(canonical=url, scope="网页预览", title="", text="抖音网页版欢迎你", kind="video", media=[])
        wiki = Wiki(self.vault, self.state, True, True)
        with patch("wiki.resolve_page", return_value=page), patch("wiki.summarize") as summarize:
            process_pending(self.inbox, wiki)
        summarize.assert_not_called()
        source = wiki.catalog["S0001"]
        note = (self.vault / source["note"]).read_text(encoding="utf-8")
        self.assertEqual(source["title"], "机制图教程")
        self.assertIn("完整视频未取得", note)
        self.assertIn("暂无可定位的工具要点", note)
        self.assertNotIn("复制打开抖音", note)

    def test_douyin_public_payload_reads_only_matching_work_without_browser(self):
        post_id = "7412345678901234567"
        url = f"https://www.douyin.com/video/{post_id}"
        video_url = "https://v1.douyinvod.com/original.mp4"
        state = {"feed": [{"aweme_id": "7412345678901234568", "desc": "其他作品"}],
                 "detail": {"aweme_id": post_id, "desc": "同一作品正文",
                            "video": {"download_addr": {"url_list": [video_url]}}}}
        payload = quote(json.dumps(state))
        markup = f'<script id="RENDER_DATA" type="application/json">{payload}</script>'
        with patch("public_page.load_public", return_value=(markup, url)), patch.object(FakeSession, "ready") as ready:
            page = read_page(url, FakeSession())
        ready.assert_not_called()
        self.assertEqual(page["text"], "同一作品正文")
        self.assertEqual(page["media"], [{"kind": "video", "url": video_url}])

    def test_douyin_auth_notice_requires_explicit_gate(self):
        url = "https://www.douyin.com/video/7412345678901234567"
        with patch("public_page.load_public", return_value=("<p>请先登录后观看</p>", url)):
            self.assertTrue(read_page(url)["auth_required"])
        with patch("public_page.load_public", return_value=("<html></html>", url)):
            self.assertFalse(read_page(url).get("auth_required"))

    def test_refetch_douyin_video_when_author_caption_is_already_saved(self):
        url = "https://www.douyin.com/video/7412345678901234567"
        cap = sample(url)
        self.begin(cap)
        self.commit(cap)
        page = dict(canonical=url, title="原帖标题", author="作者", text="作者描述", scope="作者正文",
                    media=[], kind="video", expected_images=None, media_known=True, via="", limit="")
        wiki = Wiki(self.vault, self.state, True, False)
        with patch("wiki.resolve_page", return_value=page):
            process_pending(self.inbox, wiki)
        source = wiki.catalog["S0001"]
        raw = self.vault / source["captures"][cap["id"]]["raw"]
        original = raw.read_bytes()
        candidate = {"kind": "video", "url": "https://v1.douyinvod.com/sample.mp4"}
        saved = {"path": "raw/assets/科研工具/video.mp4", "kind": "video", "origin": "公开分享页"}
        with patch("wiki.resolve_page", return_value=dict(page, media=[candidate])), patch.object(
            wiki, "copy_media", return_value={"asset-digest": saved}
        ):
            self.assertEqual(wiki.refetch("S0001"), ["S0001"])
        self.assertEqual(raw.read_bytes(), original)
        self.assertEqual(wiki.coverage(wiki.catalog["S0001"]), [])
        note = (self.vault / source["note"]).read_text(encoding="utf-8")
        self.assertIn("公开分享页 · 视频", note)
        self.assertNotIn("完整视频未取得", note)

    def test_explicit_matching_of_public_note(self):
        url = "https://www.xiaohongshu.com/discovery/item/0123456789abcdef01234567?xsec_token=abc"
        soup = BeautifulSoup('<script>window.__INITIAL_STATE__ = {"a":{"noteId":"0123456789abcdef01234567",'
                             '"desc":"全文 undefined","type":"normal","imageList":[{"urlDefault":'
                             '"https://sns.xhscdn.com/1"}]},"b":undefined}</script>', "html.parser")
        post = full_post([obj for root in embedded_objects(soup) for obj in walk(root)], "小红书", identity(url), url)
        self.assertEqual(post[1], "全文 undefined")
        self.assertEqual(post[3][0]["kind"], "image")
        self.assertEqual(post[4], 1)

    def test_harmony_xiaohongshu_share_short_link(self):
        url = "https://xhslink.cn/o/example"
        text = f"Python 环形柱状图... {url} 复制后打开小红书"
        item = {"title": "", "url": url, "text": text}
        self.assertEqual(find_url(f"https://example.org/other\n{text}"), url)
        self.assertEqual(platform_for(url), "小红书")
        self.assertTrue(permitted(url))
        self.assertFalse(permitted("https://xhslink.cn.example.org/o/example"))
        self.assertEqual(capture_title(item, {}), "Python 环形柱状图")

    def test_search_uses_work_id_and_never_ingests_same_title_other_note(self):
        short = "https://xhslink.cn/o/example"
        work = "https://www.xiaohongshu.com/explore/0123456789abcdef01234567"
        other = "https://www.xiaohongshu.com/explore/1123456789abcdef01234567"
        title = "用 Python 绘制分组环形柱状图"
        incomplete = {"canonical": work, "scope": "仅分享内容"}
        complete = {"canonical": work, "scope": "作者正文", "text": "原帖正文"}
        with patch("public_page.read_page", side_effect=[incomplete, complete]) as read, patch(
            "public_page.search_results", return_value=[(other, title), (work, title)]
        ) as search:
            self.assertEqual(resolve_page(short, title)["text"], "原帖正文")
            self.assertEqual(search.call_args.args, ("小红书", "0123456789abcdef01234567"))
            self.assertEqual([call.args[0] for call in read.call_args_list], [short, work])
        with patch("public_page.read_page", return_value=dict(incomplete, canonical=short)) as read, patch(
            "public_page.search_results", return_value=[(other, title)]
        ):
            result = resolve_page(short, title)
            self.assertEqual(result["scope"], "仅分享内容")
            self.assertEqual(result["search_candidates"], [other])
            self.assertEqual(read.call_args.args, (short, None))

    def test_online_search_enriches_old_source_without_changing_original(self):
        url = "https://www.xiaohongshu.com/explore/0123456789abcdef01234567"
        cap = sample(url)
        self.begin(cap)
        self.commit(cap)
        process_pending(self.inbox, Wiki(self.vault, self.state, False, False))
        original = next((self.vault / "raw/inbox/科研工具").glob("*.md"))
        saved = original.read_bytes()
        card = next((self.vault / "wiki/sources").glob("*.md"))
        with card.open("a", encoding="utf-8") as handle:
            handle.write("\n保留这条人工批注\n")
        page = dict(canonical=url, title="公开原帖标题", author="作者", text="确认取得的公开正文",
                    scope="作者正文", media=[], kind="image", expected_images=0, limit="",
                    search_status="公开搜索已核实同一作品并取得正文")
        wiki = Wiki(self.vault, self.state, True, False)
        with patch("wiki.resolve_page", return_value=page) as resolve:
            process_pending(self.inbox, wiki)
            process_pending(self.inbox, wiki)
            resolve.assert_called_once()
        self.assertEqual(original.read_bytes(), saved)
        snapshots = list(original.parent.glob("*-网页补查.md"))
        self.assertEqual(len(snapshots), 1)
        self.assertIn("确认取得的公开正文", snapshots[0].read_text(encoding="utf-8"))
        self.assertEqual(list(card.parent.glob("*.md")), [card])
        body = card.read_text(encoding="utf-8")
        self.assertIn("网页补查原文", body)
    def test_rendered_page_gives_body_and_media_without_public_markup(self):
        url = "https://www.xiaohongshu.com/explore/0123456789abcdef01234567"
        dom = {"title": "顶刊配色笔记", "desc": "作者正文：正文从渲染后的页面读取",
               "author": "某作者", "blocked": False,
               "images": ["https://sns.xhscdn.com/a.jpg", "https://sns.xhscdn.com/b.jpg"],
               "videos": [], "state": None}
        page = read_page(url, FakeSession(final=url, dom=dom))
        self.assertEqual(page["scope"], "作者正文")
        self.assertEqual(page["text"], "作者正文：正文从渲染后的页面读取")
        self.assertEqual(page["author"], "某作者")
        self.assertEqual(page["expected_images"], 2)
        self.assertEqual([item["kind"] for item in page["media"]], ["image", "image"])
        self.assertEqual(page["via"], "浏览器读取")

    def test_short_link_keeps_query_and_accepts_same_post_on_rednote(self):
        short = "https://xhslink.cn/o/example"
        post_id = "0123456789abcdef01234567"
        expanded = f"https://www.xiaohongshu.com/discovery/item/{post_id}?xsec_token=from-original-link"
        redirected = f"https://www.rednote.com/zh/discovery/item/{post_id}"
        session = FakeSession(final=redirected, dom={"desc": "从同一个作品的 RedNote 页面读取到的作者正文"})
        with patch("public_page.build_opener") as opener, patch("public_page.check_public"), patch(
            "public_page.short_redirect_over_lan", return_value=expanded
        ), patch.object(session, "read", wraps=session.read) as read:
            opener.return_value.open.side_effect = TimeoutError()
            page = read_page(short, session)
        read.assert_called_once_with(expanded)
        self.assertEqual(page["scope"], "作者正文")
        self.assertEqual(page["canonical"], f"https://www.xiaohongshu.com/explore/{post_id}")

    def test_browser_gate_falls_back_to_public_without_using_gate_text(self):
        url = "https://www.xiaohongshu.com/explore/0123456789abcdef01234567"
        session = FakeSession(final="https://www.rednote.com/zh/login", dom={"desc": "这是登录页里的提示内容和说明"})
        markup = ('<script>window.__INITIAL_STATE__={"noteId":"0123456789abcdef01234567",'
                  '"desc":"从公开原帖取得的正文"}</script>')
        with patch("public_page.check_public"), patch("public_page.load_public", return_value=(markup, url)) as public:
            page = read_page(url, session)
        public.assert_called_once()
        self.assertEqual(page["scope"], "作者正文")
        self.assertEqual(page["text"], "从公开原帖取得的正文")

    def test_rednote_media_from_matched_video_payload(self):
        post_id = "0123456789abcdef01234567"
        url = f"https://www.rednote.com/zh/discovery/item/{post_id}"
        media = ["https://sns-web-i10.rednotecdn.com/cover.webp", "https://sns-v27.rednotecdn.com/clip.mp4"]
        state = {"noteId": post_id, "desc": "视频正文", "type": "video",
                 "imageList": [{"urlDefault": media[0]}],
                 "video": {"media": {"stream": {"h264": [{"masterUrl": media[1]}]}}}}
        with patch("public_page.check_public"):
            page = read_page(url, FakeSession(final=url, dom={"state": state}))
        self.assertEqual(page["scope"], "作者正文")
        self.assertEqual([(x["kind"], x["url"]) for x in page["media"]], list(zip(("image", "video"), media)))

    def test_note_payload_upgrades_cdn_links_and_skips_empty_ones(self):
        url = "https://www.xiaohongshu.com/explore/0123456789abcdef01234567"
        markup = ('<script>window.__INITIAL_STATE__ = {"a":{"noteId":"0123456789abcdef01234567",'
                  '"desc":"作者正文","type":"normal","imageList":[{"urlDefault":""},'
                  '{"urlPre":"http://sns-webpic-qc.xhscdn.com/one.jpg"},'
                  '{"infoList":[{"url":"http://sns-webpic-qc.xhscdn.com/two.jpg"}]}]}}</script>')
        page = read_page(url, FakeSession(html=markup, final=url))
        self.assertEqual(page["scope"], "作者正文")
        self.assertEqual(page["expected_images"], 3)
        self.assertEqual([item["url"] for item in page["media"]],
                         ["https://sns-webpic-qc.xhscdn.com/one.jpg",
                          "https://sns-webpic-qc.xhscdn.com/two.jpg"])
        self.assertEqual(page["text"], "作者正文")

    def test_without_login_state_the_public_page_is_used(self):
        url = "https://www.xiaohongshu.com/explore/0123456789abcdef01234567"
        broken = FakeSession(ready=False)
        with patch("public_page.load_public", side_effect=ValueError("平台要求登录")) as public:
            page = read_page(url, broken)
        public.assert_called_once()
        self.assertEqual(page["scope"], "仅分享内容")
        self.assertIn("平台要求登录", page["limit"])

    def test_media_download_retries_once_with_session_cookie(self):
        wiki = Wiki(self.vault, self.state, False, False, FakeSession())
        folder = self.state / "inbox" / str(uuid.uuid4())
        folder.mkdir(parents=True)
        seen = []

        def fake(candidate, handle, remaining, cookie=""):
            seen.append(cookie)
            if not cookie:
                raise OSError("HTTP Error 403")
            handle.write(b"image-bytes")
            return ".jpg", 11

        with patch("wiki.download_media", side_effect=fake):
            assets = wiki.copy_media(folder, {"attachments": []},
                                     {"media": [{"url": "https://sns.xhscdn.com/a.jpg", "kind": "image"}]})
        self.assertEqual(seen, ["", "a1=1; web_session=2"])
        self.assertEqual([asset["kind"] for asset in assets.values()], ["image"])

    def test_refetch_rebuilds_card_after_login(self):
        url = "https://www.xiaohongshu.com/explore/0123456789abcdef01234567"
        cap = sample(url)
        self.begin(cap)
        self.commit(cap)
        process_pending(self.inbox, Wiki(self.vault, self.state, False, False))
        card = next((self.vault / "wiki/sources").glob("S*.md"))
        self.assertIn("收录状态 待补", card.read_text(encoding="utf-8"))
        page = dict(canonical=url, title="登录后读到的标题", author="作者", text="登录后读到的完整正文，长度足够",
                    scope="作者正文", media=[], kind="image", expected_images=0, limit="", via="浏览器读取",
                    media_known=True)
        wiki = Wiki(self.vault, self.state, True, False)
        with patch("wiki.resolve_page", return_value=page):
            self.assertEqual(wiki.refetch(), ["S0001"])
        body = card.read_text(encoding="utf-8")
        self.assertEqual(body.count("<!-- research-collector:begin -->"), 1)
        self.assertIn("收录状态 已归档", body)
        self.assertIn("登录后读到的完整正文", body)
        self.assertIn("读取方式 浏览器读取", body)
        self.assertNotIn("公开搜索未找到", body)
        daily = (self.vault / "wiki/topics/科研工具/每日/2026-11-11.md").read_text(encoding="utf-8")
        self.assertIn("- 新增 · 已归档 · ", daily)
        self.assertEqual(read_json(self.state / "catalog.json")["S0001"]["captures"][cap["id"]]["page"]["scope"],
                         "作者正文")

    def test_user_category_and_importance_are_persisted_and_prioritized(self):
        low = dict(sample("https://www.xiaohongshu.com/explore/1123456789abcdef01234567", "低优先正文"),
                   category="写作润色", importance="低")
        high = dict(sample("https://www.xiaohongshu.com/explore/2123456789abcdef01234567", "高优先正文"),
                    category="光学测量", importance="高")
        for capture in (low, high):
            self.assertEqual(self.begin(capture)[0], 200)
            self.assertEqual(self.commit(capture)[0], 200)
        wiki = Wiki(self.vault, self.state, False, True)
        with patch("wiki.detailed_summary", return_value=[]) as detailed, patch(
            "wiki.summarize", return_value=("科研绘图", [])
        ):
            process_pending(self.inbox, wiki)
        # Both sample titles explicitly refer to palette drawing, so both get full evidence review.
        self.assertEqual(detailed.call_count, 2)
        high_source = wiki.catalog["S0001"]
        self.assertEqual(high_source["category"], "光学测量")
        self.assertEqual(wiki.catalog["S0002"]["category"], "写作润色")
        card = self.vault / high_source["note"]
        text = card.read_text(encoding="utf-8")
        self.assertIn('#科研工具/高重要程度', text)
        self.assertIn('research_priority: 3', text)
        self.assertIn('research_category: "光学测量"', text)
        topic = (self.vault / "wiki/topics/科研工具.md").read_text(encoding="utf-8")
        self.assertIn("### 光学测量", topic)
        self.assertLess(topic.index("## 优先阅读"), topic.index("## 来源"))
        legacy = sample(high["url"], "旧版补交")
        self.assertEqual(self.begin(legacy)[0], 200)
        self.assertEqual(self.commit(legacy)[0], 200)
        process_pending(self.inbox, wiki, extract=False)
        self.assertIn('research_priority: 3', card.read_text(encoding="utf-8"))
        bad = dict(sample(high["url"]), importance="最高")
        self.assertEqual(self.begin(bad)[0], 400)


if __name__ == "__main__":
    unittest.main()
