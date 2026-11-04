"""Install official rclone into ignored project data, verifying the published checksum."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.request import Request, urlopen
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DESTINATION = ROOT / "data/tools/rclone"


def download(url):
    return urlopen(Request(url, headers={"User-Agent": "drama-mvp-installer/1.0"}), timeout=60)


def main():
    DESTINATION.mkdir(parents=True, exist_ok=True)
    executable = DESTINATION / "rclone.exe"
    if executable.exists():
        subprocess.run([str(executable), "version"], check=True)
        return
    try:
        with download("https://downloads.rclone.org/version.txt") as response:
            value = response.read(1000).decode().strip()
        match = re.fullmatch(r"rclone v(\d+\.\d+\.\d+)", value)
        if not match:
            raise ValueError("官方下载版本信息无法确认，已停止安装")
        version = match[1]
        filename = f"rclone-v{version}-windows-amd64.zip"
        base = f"https://downloads.rclone.org/v{version}/"
        checksum_url, archive_url = base + "SHA256SUMS", base + filename
        checksum_response = download(checksum_url)
    except OSError:
        with download("https://api.github.com/repos/rclone/rclone/releases/latest") as response:
            release = json.load(response)
        tag = release["tag_name"]
        if not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
            raise ValueError("GitHub 官方发布版本无效")
        version = tag[1:]
        filename = f"rclone-{tag}-windows-amd64.zip"
        assets = {item["name"]: item["browser_download_url"] for item in release["assets"]}
        checksum_url, archive_url = assets["SHA256SUMS"], assets[filename]
        if not all(url.startswith("https://github.com/rclone/rclone/releases/download/") for url in (checksum_url, archive_url)):
            raise ValueError("GitHub 下载来源不符")
        checksum_response = download(checksum_url)
    with checksum_response as response:
        checksums = response.read(200000).decode()
    expected = next((parts[0] for line in checksums.splitlines() if len(parts := line.split()) >= 2 and parts[-1].lstrip("*") == filename), None)
    if not expected or not re.fullmatch(r"[a-fA-F0-9]{64}", expected):
        raise ValueError("未找到官方压缩包校验值，已停止安装")
    with tempfile.TemporaryDirectory(dir=DESTINATION) as directory:
        archive = Path(directory) / filename
        sha = hashlib.sha256()
        with download(archive_url) as response, archive.open("wb") as stream:
            while block := response.read(1024 * 1024):
                sha.update(block); stream.write(block)
        if sha.hexdigest().lower() != expected.lower():
            raise ValueError("下载校验失败，未安装")
        with zipfile.ZipFile(archive) as source:
            names = [name for name in source.namelist() if name.endswith("/rclone.exe")]
            if len(names) != 1:
                raise ValueError("官方压缩包内容不符合预期")
            executable.write_bytes(source.read(names[0]))
        try:
            subprocess.run([str(executable), "version"], check=True, timeout=15)
        except Exception:
            executable.unlink(missing_ok=True)
            raise
    (DESTINATION / "installation.json").write_text(json.dumps({"version":version,"url":archive_url,
        "archive_sha256":expected,"license":"MIT","source":"https://github.com/rclone/rclone"},indent=2),encoding="utf-8")
    print("rclone 已安装至 data/tools/rclone，不包含服务器连接配置。")


if __name__ == "__main__":
    main()
