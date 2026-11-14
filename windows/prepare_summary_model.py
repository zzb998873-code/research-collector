"""Download and verify the official Ollama model into the project's local cache."""

import hashlib
import json
import re
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from storage import atomic_write

ORIGIN = "https://registry.ollama.ai/v2/library/qwen3"
STATE = Path(__file__).resolve().parents[1] / ".runtime"
CACHE = STATE / "ollama"
MODEL = "4b"
PARTS = 8


def curl(url, *args):
    return subprocess.run(["curl.exe", "--fail", "--silent", "--show-error", "--location",
                           "--proto", "=https", "--proto-redir", "=https", "--retry", "2",
                           "--connect-timeout", "18", *args, url], check=True, stdout=subprocess.PIPE).stdout


def fetch_piece(url, folder, number, start, end):
    piece = folder / f"{number}.part"
    if not piece.exists() or piece.stat().st_size != end - start + 1:
        curl(url, "--range", f"{start}-{end}", "--max-filesize", str(end - start + 1), "--output", str(piece))
    if piece.stat().st_size != end - start + 1:
        raise OSError(f"模型分块 {number} 不完整")
    return piece


def verify(path, size, digest):
    if not path.exists() or path.stat().st_size != size:
        return False
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest() == digest


def fetch_layer(layer):
    digest = layer["digest"]
    size = layer["size"]
    if not re.fullmatch("sha256:[0-9a-f]{64}", digest) or type(size) is not int or not 0 < size < 4 * 1024**3:
        raise ValueError("模型清单包含无效的下载项")
    path = CACHE / "blobs" / digest.replace(":", "-")
    path.parent.mkdir(parents=True, exist_ok=True)
    if verify(path, size, digest[7:]):
        return
    url = f"{ORIGIN}/blobs/{digest}"
    if size < 16 * 1024**2:
        data = curl(url, "--max-filesize", str(size))
        if len(data) != size or hashlib.sha256(data).hexdigest() != digest[7:]:
            raise OSError("模型元数据校验失败")
        atomic_write(path, data)
        return
    folder = STATE / "downloads" / digest[7:]
    folder.mkdir(parents=True, exist_ok=True)
    print(f"下载 Qwen3 4B 模型，{size / 1024**3:.2f} GiB", flush=True)
    with ThreadPoolExecutor(max_workers=PARTS) as pool:
        pieces = list(pool.map(lambda i: fetch_piece(url, folder, i, i * size // PARTS,
                                                     (i + 1) * size // PARTS - 1), range(PARTS)))
    import shutil
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as output:
        temp = Path(output.name)
        try:
            for piece in pieces:
                with piece.open("rb") as data:
                    shutil.copyfileobj(data, output, 1024**2)
        except BaseException:
            output.close()
            temp.unlink(missing_ok=True)
            raise
    try:
        if not verify(temp, size, digest[7:]):
            for piece in pieces:
                piece.unlink()
            raise OSError("下载的模型未通过 SHA-256 校验")
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)
    for piece in pieces:
        piece.unlink()
    folder.rmdir()


if __name__ == "__main__":
    raw = curl(f"{ORIGIN}/manifests/{MODEL}", "--max-filesize", "65536")
    manifest = json.loads(raw)
    for layer in [manifest["config"], *manifest["layers"]]:
        fetch_layer(layer)
    atomic_write(CACHE / "manifests/registry.ollama.ai/library/qwen3" / MODEL, raw)
    print("本地摘要模型已校验并保存。")
