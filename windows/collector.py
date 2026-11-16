"""Windows HTTPS receiver and Obsidian worker."""

import argparse
import ipaddress
import logging
import logging.handlers
import os
import re
import secrets
import socket
import ssl
import shutil
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
dependencies = PROJECT / ".venv/Lib/site-packages"
if dependencies.is_dir() and str(dependencies) not in sys.path:
    sys.path.insert(0, str(dependencies))

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from browser import DOUYIN_HOME, Reader
from public_page import permitted, platform_for
from storage import CHUNK_SIZE, IMPORTANCE, MAX_MANIFEST, Inbox, RequestError, atomic_write, json_bytes, read_json, write_json
from wiki import Wiki, capture_title, current_analysis

DEFAULT_VAULT = PROJECT.parent / "磨蚀研究知识库"
DEFAULT_STATE = PROJECT / ".runtime"
LOGGER = logging.getLogger("collector")


def show_auth_notice(state, source):
    """Show a clickable Windows prompt for an explicitly gated work."""
    if os.name != "nt":
        return
    capture = next((entry for entry in reversed(list(source["captures"].values()))
                    if entry["page"].get("auth_required")), None)
    if capture is None or any(entry["page"].get("scope") == "作者正文" for entry in source["captures"].values()):
        return
    if any(asset["kind"] == "video" for asset in source["assets"].values()):
        return
    url = capture["page"].get("canonical") or capture["url"]
    if not permitted(url) or platform_for(url) != "抖音":
        return
    receipt = state / "notices" / (source["id"] + "-douyin-login.json")
    if receipt.exists():
        return
    try:
        subprocess.Popen([sys.executable, str(PROJECT / "windows" / "auth_notice.py"), str(receipt), url,
                          source["title"][:95]], creationflags=subprocess.CREATE_NO_WINDOW)
    except OSError as exc:
        LOGGER.warning("%s 登录通知未发送（%s）", source["id"], type(exc).__name__)


def tls_files(state, host):
    def subject(name):
        return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])

    start = datetime.now(timezone.utc) - timedelta(days=1)
    end = start + timedelta(days=1095)
    root_key = ec.generate_private_key(ec.SECP256R1())
    server_key = ec.generate_private_key(ec.SECP256R1())
    root = (
        x509.CertificateBuilder().subject_name(subject("Research Collector Local CA"))
        .issuer_name(subject("Research Collector Local CA")).public_key(root_key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(start).not_valid_after(end)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(root_key, hashes.SHA256())
    )
    server = (
        x509.CertificateBuilder().subject_name(subject(host)).issuer_name(root.subject)
        .public_key(server_key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(start).not_valid_after(end)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(host))]), critical=False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(root_key, hashes.SHA256())
    )
    atomic_write(state / "ca.pem", root.public_bytes(serialization.Encoding.PEM))
    atomic_write(state / "server.pem", server.public_bytes(serialization.Encoding.PEM))
    atomic_write(state / "key.pem", server_key.private_bytes(serialization.Encoding.PEM,
                 serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))


def discover_ip():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.connect(("192.0.2.1", 9))
        return sock.getsockname()[0]


def configure(state, vault, host, port, web, models, rotate=False, browser=True):
    address = ipaddress.IPv4Address(host)
    if not address.is_private or address.is_unspecified or address.is_link_local:
        raise ValueError("监听地址应为电脑的私有局域网 IPv4 地址")
    vault = Path(vault).resolve()
    if not (vault / "首页.md").is_file() or not (vault / "wiki/index.md").is_file():
        raise ValueError("所选目录不是已初始化的知识库")
    path = state / "config.json"
    previous = read_json(path) if path.exists() else None
    if previous and Path(previous["vault"]) != vault and (state / "catalog.json").exists():
        raise ValueError("切换知识库时请使用独立的 --state 目录")
    if not previous or previous["host"] != host or rotate or not (state / "server.pem").exists():
        tls_files(state, host)
    config = {
        "host": host, "port": port, "vault": str(vault), "web": web, "models": models,
        "browser": browser,
        "token": previous["token"] if previous and not rotate else secrets.token_urlsafe(32),
    }
    write_json(path, config)
    pairing = {"version": 1, "url": f"https://{host}:{port}", "token": config["token"],
               "ca": (state / "ca.pem").read_text(encoding="ascii")}
    write_json(state / "配对.json", pairing)
    return config


class CollectorServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, state, config, inbox):
        self.state = state
        self.inbox = inbox
        self.token = config["token"]
        self.vault = config["vault"]
        self.models = config["models"]
        self.changed = threading.Event()
        super().__init__((config["host"], config["port"]), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(state / "server.pem", state / "key.pem")
        self.socket = context.wrap_socket(self.socket, server_side=True, do_handshake_on_connect=False)

    def get_request(self):
        client, address = super().get_request()
        client.settimeout(20)
        return client, address


def recent_captures(inbox, wiki):
    """Read committed captures and the current state of their published source cards."""
    sources = {capture_id: source for source in wiki.catalog.values() for capture_id in source["captures"]}
    receipts = sorted(inbox.directory.glob("*/committed.json"), key=lambda path: path.stat().st_mtime,
                      reverse=True)
    items = []
    seen = set()
    for receipt in receipts:
        folder = receipt.parent
        manifest = read_json(folder / "manifest.json")
        item = {"id": folder.name, "title": capture_title(manifest, {})[:160],
                "url": manifest["url"], "category": manifest.get("category", ""),
                "importance": manifest.get("importance", "中"),
                "date": wiki.capture_date(manifest),
                "status": "received", "details": []}
        if (folder / "processed.json").is_file():
            source = sources.get(folder.name)
            if source and (wiki.vault / source["note"]).is_file():
                if source["id"] in seen:
                    continue
                seen.add(source["id"])
                missing = wiki.coverage(source)
                analysis = current_analysis(source)
                item.update(title=source["title"][:160], source_id=source["id"],
                            category=source.get("user_category", source["category"]),
                            importance=source.get("importance", "中"),
                            details=missing, status="missing" if missing else "archived")
                if wiki.local_models and not analysis.get("done"):
                    item["details"] = missing + ["附件识别或摘要待重试" if analysis.get("errors") else "等待提取要点"]
                    if not missing:
                        item["status"] = "processing"
            else:
                item.update(status="processing", details=["来源卡暂不可读取，请检查电脑知识库"])
        elif inbox.processing == folder.name:
            item.update(status="processing", details=["正在查找原帖并整理来源卡"])
        items.append(item)
        if len(items) == 30:
            break
    return items


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        return

    def read_body(self, limit):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise RequestError(400, "无效的请求大小") from exc
        if not 0 <= length <= limit:
            raise RequestError(413, "请求太大")
        data = self.rfile.read(length)
        if len(data) != length:
            raise RequestError(400, "连接在传输完成前关闭")
        return data

    def dispatch(self):
        authorization = self.headers.get("Authorization", "")
        if not secrets.compare_digest(authorization.encode(), ("Bearer " + self.server.token).encode()):
            raise RequestError(401, "配对凭证无效")
        parts = self.path.strip("/").split("/")
        inbox = self.server.inbox
        if self.command == "GET" and parts == ["v1", "health"]:
            return {"version": 1, "status": "ready", "recent_captures": True, "record_details": True}
        if self.command == "GET" and len(parts) == 4 and parts[:2] == ["v1", "captures"] and parts[3] == "detail":
            from records import detail
            return {"record": detail(Wiki(self.server.vault, self.server.state), inbox, parts[2])}
        if self.command == "POST" and len(parts) == 4 and parts[:2] == ["v1", "captures"] and parts[3] == "retry":
            from records import detail
            record = detail(Wiki(self.server.vault, self.server.state), inbox, parts[2])
            write_json(self.server.state / "retry" / (record["id"] + ".json"), {"requested": secrets.token_hex(16)})
            self.server.changed.set()
            return {"accepted": True}
        if self.command == "POST" and parts == ["v1", "lookup"]:
            import json
            from records import lookup
            try:
                query = json.loads(self.read_body(8000))
                url = query["url"]
                if not isinstance(url, str) or len(url) > 4000:
                    raise ValueError
            except (ValueError, KeyError, TypeError) as exc:
                raise RequestError(400, "链接无效") from exc
            return {"record": lookup(Wiki(self.server.vault, self.server.state), inbox, url)}
        if self.command == "GET" and parts == ["v1", "captures", "recent"]:
            wiki = Wiki(self.server.vault, self.server.state, local_models=self.server.models)
            return {"items": recent_captures(inbox, wiki)}
        if self.command == "POST" and parts == ["v1", "captures"]:
            import json
            try:
                item = json.loads(self.read_body(MAX_MANIFEST))
            except (UnicodeError, ValueError, RecursionError) as exc:
                raise RequestError(400, "无法读取收集包") from exc
            return inbox.begin(item)
        if len(parts) == 4 and parts[:2] == ["v1", "captures"] and parts[3] == "commit" and self.command == "POST":
            result = inbox.commit(parts[2])
            self.server.changed.set()
            return result
        if len(parts) == 5 and parts[:2] == ["v1", "captures"] and parts[3] == "files" and self.command == "PUT":
            offset = self.headers.get("Upload-Offset", "")
            if not re.fullmatch(r"\d{1,10}", offset):
                raise RequestError(400, "无效的分块位置")
            return inbox.upload(parts[2], parts[4], int(offset), self.read_body(CHUNK_SIZE))
        raise RequestError(404, "接口不存在")

    def handle_api(self):
        try:
            data, status = self.dispatch(), 200
        except RequestError as exc:
            data, status = {"error": str(exc)}, exc.status
        except (TimeoutError, ConnectionError):
            return
        except Exception:
            LOGGER.exception("请求处理失败")
            data, status = {"error": "电脑端处理失败，手机可稍后重试"}, 500
        body = json_bytes(data)
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except (TimeoutError, ConnectionError):
            pass

    do_GET = do_POST = do_PUT = handle_api


def process_pending(inbox, wiki, extract=True):
    wiki.ensure_vault()
    def receipt_order(receipt):
        try:
            importance = read_json(receipt.with_name("manifest.json")).get("importance")
        except (ValueError, OSError):
            importance = None
        return -IMPORTANCE.get(importance, 2), receipt.stat().st_mtime

    for receipt in sorted(inbox.directory.glob("*/committed.json"), key=receipt_order):
        folder = receipt.parent
        done = folder / "processed.json"
        retry_path = wiki.state / "retry" / (folder.name + ".json")
        retry_done = retry_path.with_suffix(".done")
        retry = read_json(retry_path) if retry_path.exists() else None
        force = retry is not None and (not retry_done.exists() or read_json(retry_done) != retry)
        try:
            if done.exists():
                source_id = next((s["id"] for s in wiki.catalog.values() if folder.name in s["captures"]),
                                 read_json(done)["source_id"])
                wiki.search_existing(folder, source_id, force=force)
                if force and source_id in wiki.catalog:
                    source = wiki.catalog[source_id]
                    source["analysis"] = {**source.get("analysis", {}), "done": False, "retry_after": ""}
                    source.pop("drawing_version", None)
                    source.pop("visual_retry_after", None)
                    wiki.save()
            else:
                inbox.processing = folder.name
                source = wiki.process(folder)
                write_json(done, {"source_id": source["id"]})
                LOGGER.info("入库 %s", source["id"])
            for attachment in folder.glob("*.part"):
                attachment.unlink(missing_ok=True)
            if force:
                write_json(retry_done, retry)
        except Exception:
            LOGGER.exception("收集包 %s 暂未入库", folder.name)
        finally:
            inbox.processing = None
    if extract:
        for source in sorted(wiki.catalog.values(), key=lambda s: -IMPORTANCE.get(s.get("importance"), 2)):
            if source["captures"]:
                try:
                    show_auth_notice(wiki.state, source)
                    wiki.analyze(source)
                except Exception:
                    LOGGER.exception("来源 %s 暂未完成整理", source["id"])


def worker(server, wiki, stop):
    try:
        wiki.sync_daily_status(wiki.catalog.values())
    except OSError:
        LOGGER.exception("既有科研资料日报状态暂未同步")
    while not stop.is_set():
        server.changed.clear()
        try:
            process_pending(server.inbox, wiki)
        except Exception:
            LOGGER.exception("知识库暂不可用")
        server.changed.wait(60)


def launch_model(state):
    manifest = state / "ollama/manifests/registry.ollama.ai/library/qwen3/4b"
    if not manifest.is_file():
        return None
    import urllib.request
    local = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with local.open("http://127.0.0.1:11437/api/tags", timeout=2) as response:
            existing = response.read(16384)
        if b'"qwen3:4b"' in existing:
            os.environ["RESEARCH_COLLECTOR_OLLAMA"] = "project"
            return None
    except OSError:
        pass
    default_exe = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Ollama/ollama.exe"
    exe = shutil.which("ollama") or (str(default_exe) if default_exe.is_file() else "")
    if not exe:
        LOGGER.warning("项目模型已下载，但未找到 Ollama 程序")
        return None
    environment = {**os.environ, "OLLAMA_HOST": "127.0.0.1:11437",
                   "OLLAMA_MODELS": str(state / "ollama"), "OLLAMA_NO_CLOUD": "1"}
    with (state / "ollama.log").open("a", encoding="utf-8") as log:
        process = subprocess.Popen([exe, "serve"], env=environment, stdout=log, stderr=log,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    os.environ["RESEARCH_COLLECTOR_OLLAMA"] = "project"
    return process


def run(state, config):
    os.environ.setdefault("HF_HOME", str(state / "hf"))
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    handler = logging.handlers.RotatingFileHandler(state / "collector.log", maxBytes=1024 * 1024,
                                                   backupCount=2, encoding="utf-8")
    handlers = [handler]
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=handlers)
    inbox = Inbox(state / "inbox")
    session = Reader(state / "browser") if config.get("browser", True) else None
    wiki = Wiki(config["vault"], state, config["web"], config["models"], session)
    with CollectorServer(state, config, inbox) as server:
        stop = threading.Event()
        model = launch_model(state) if config["models"] else None
        thread = threading.Thread(target=worker, args=(server, wiki, stop), daemon=True)
        thread.start()
        LOGGER.info("科研资料接收已启动 %s:%s", config["host"], config["port"])
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            stop.set()
            server.changed.set()
            thread.join(timeout=5)
            if model and model.poll() is None:
                model.terminate()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    sub = parser.add_subparsers(dest="command", required=True)
    setup = sub.add_parser("setup", help="配置知识库、HTTPS 与手机配对")
    setup.add_argument("--vault", type=Path, default=DEFAULT_VAULT)
    setup.add_argument("--host")
    setup.add_argument("--port", type=int, default=8789)
    setup.add_argument("--no-web", action="store_true")
    setup.add_argument("--no-models", action="store_true")
    setup.add_argument("--no-browser", action="store_true")
    setup.add_argument("--rotate", action="store_true")
    sub.add_parser("serve", help="启动接收和入库")
    sub.add_parser("status", help="查看电脑接收和入库状态")
    login = sub.add_parser("login", help="在专用浏览器配置里登录小红书或抖音")
    login.add_argument("--platform", choices=("xiaohongshu", "douyin"))
    login.add_argument("--url")
    login.add_argument("--timeout", type=int, default=300)
    refetch = sub.add_parser("refetch", help="补查缺正文或缺视频的来源")
    refetch.add_argument("--source", help="只补查指定来源编号，例如 S0722")
    args = parser.parse_args()
    state = args.state.resolve()
    if args.command == "login":
        if not Reader.installed():
            parser.error("缺少 playwright，请先安装依赖：pip install playwright")
        reader = Reader(state / "browser")
        url = args.url or (DOUYIN_HOME if args.platform == "douyin" else "https://www.xiaohongshu.com/explore")
        platform = platform_for(url)
        if not permitted(url) or (args.platform and platform != {"douyin": "抖音", "xiaohongshu": "小红书"}[args.platform]):
            parser.error("请提供所选平台的 HTTPS 网址")
        if platform == "抖音":
            print("请在专用 Edge 窗口登录抖音。看到作品可以正常播放后，关闭这个窗口。", flush=True)
            if not reader.login(url, args.timeout, platform):
                parser.error("等待关闭抖音窗口超时。请重新运行登录命令，确认目标视频可播放后关闭窗口。")
            print("窗口已关闭，可补查来源以确认网页内容和视频是否可取得。")
            return
        print("已打开登录窗口，请在里面扫码或短信登录小红书；登录完成后窗口会自动关闭。")
        if reader.login(url, args.timeout):
            print("登录成功，接收端会直接复用该登录态。")
            return
        parser.error("未检测到登录状态，请重新运行 login")
    if args.command == "setup":
        if not 1024 <= args.port <= 65535:
            parser.error("端口应在 1024 到 65535 之间")
        config = configure(state, args.vault, args.host or discover_ip(), args.port,
                           not args.no_web, not args.no_models, args.rotate, not args.no_browser)
        print(f'配对文件 {state / "配对.json"}')
        print(f'接收地址 https://{config["host"]}:{config["port"]}')
        return
    if not (state / "config.json").is_file():
        parser.error("请先运行 setup")
    config = read_json(state / "config.json")
    if args.command == "status":
        packages = [p.parent for p in (state / "inbox").glob("*/manifest.json")]
        print(f'配对文件 {state / "配对.json"}')
        print(f'知识库 {config["vault"]}')
        print(f'接收地址 https://{config["host"]}:{config["port"]}')
        print(f'收集包 {len(packages)}，已入库 {sum((p / "processed.json").exists() for p in packages)}')
        profile = state / "browser/edge-profile"
        if not config.get("browser", True):
            print("登录态浏览器 未启用")
        else:
            print("登录态浏览器 " + ("已初始化，可用 " if profile.is_dir() else "未初始化，需先运行 login"))
        return
    if args.command == "refetch":
        session = Reader(state / "browser") if config.get("browser", True) else None
        wiki = Wiki(config["vault"], state, config["web"], False, session)
        if args.source and args.source not in wiki.catalog:
            parser.error("来源编号不存在")
        touched = wiki.refetch(args.source)
        print(f"补查完成，{len(touched)} 条来源取得新正文或附件")
        if touched:
            print("要点、配图识别和本地摘要由接收端下一次整理时补齐")
        return
    run(state, config)


if __name__ == "__main__":
    main()
