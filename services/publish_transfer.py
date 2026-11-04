"""Local UI transfer jobs use the reviewed PowerShell publisher and SSH configuration."""
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import uuid

from services.publishing import ROOT, verify_bundle

CONFIG = ROOT / "data/private/site-publish.json"
JOBS = ROOT / "data/publish/operations"
_processes = {}
_lock = threading.Lock()


def configuration():
    if not CONFIG.is_file():
        return None
    value = json.loads(CONFIG.read_text(encoding="utf-8"))
    patterns = {"server": r"[A-Za-z0-9_.-]+@[A-Za-z0-9.-]+", "remote_project": r"/[A-Za-z0-9_./-]+",
                "remote_python": r"[A-Za-z0-9_./-]+", "domain": r"https://[A-Za-z0-9.-]+(?::[0-9]+)?/?"}
    value.setdefault("remote_python", "data/site-runtime/bin/python")
    value.setdefault("port", 22)
    for key, pattern in patterns.items():
        if not isinstance(value.get(key), str) or not re.fullmatch(pattern, value[key]) or ".." in value[key]:
            raise ValueError("本机发布配置无效，请由管理员核对 site-publish.json")
    if type(value["port"]) != int or not 1 <= value["port"] <= 65535:
        raise ValueError("发布端口配置无效")
    if value.get("rclone_remote") and not re.fullmatch(r"[A-Za-z0-9_-]+", value["rclone_remote"]):
        raise ValueError("传输工具连接名称无效")
    if value.get("identity_file") and not Path(value["identity_file"]).is_file():
        raise ValueError("配置的 SSH 密钥文件不存在，请管理员核对本机路径")
    return value


def start_transfer(release_id, *, activate=False, rights_confirmed=False):
    if os.name != "nt":
        raise ValueError("自动传输入口仅在 Windows 本机工作台提供")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", release_id):
        raise ValueError("发布版本名称无效")
    config = configuration()
    if config is None:
        raise ValueError("尚未配置服务器连接，请先由管理员完成本机发布配置")
    if activate and (not rights_confirmed or config.get("inventory_confirmed") is not True):
        raise ValueError("上线前须确认视频展示许可及服务器资料盘点")
    verify_bundle(ROOT / "data/publish" / release_id)
    command = ["powershell.exe", "-NoProfile", "-NonInteractive", "-File", str(ROOT / "publish-site.ps1"),
        "-ReleaseId", release_id, "-Server", config["server"], "-RemoteProject", config["remote_project"],
        "-Port", str(config["port"]), "-RemotePython", config["remote_python"], "-Domain", config["domain"]]
    for key, flag in (("identity_file", "-IdentityFile"), ("rclone_remote", "-RcloneRemote")):
        if config.get(key):
            command.extend([flag, config[key]])
    if activate:
        command.extend(["-Apply", "-InventoryConfirmed", "-RightsConfirmed"])
    with _lock:
        if any(process.poll() is None for process in _processes.values()):
            raise ValueError("已有上传或发布正在进行，请等待完成")
        JOBS.mkdir(parents=True, exist_ok=True)
        identifier = uuid.uuid4().hex
        with (JOBS / (identifier + ".log")).open("wb") as log:
            process = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                       creationflags=subprocess.CREATE_NO_WINDOW)
        _processes[identifier] = process
        record = {"id": identifier, "release_id": release_id, "activate": activate, "status": "running"}
        (JOBS / (identifier + ".json")).write_text(json.dumps(record), encoding="utf-8")
        return record


def transfer_status(identifier):
    if not re.fullmatch(r"[a-f0-9]{32}", identifier):
        raise ValueError("发布操作编号无效")
    path = JOBS / (identifier + ".json")
    if not path.is_file():
        raise ValueError("发布操作记录不存在")
    record = json.loads(path.read_text(encoding="utf-8"))
    process = _processes.get(identifier)
    if process:
        result = process.poll()
        record["status"] = "running" if result is None else "completed" if result == 0 else "failed"
        path.write_text(json.dumps(record), encoding="utf-8")
    elif record["status"] == "running":
        # A server restart loses process ownership. Never guess that publication succeeded.
        record["status"] = "unknown"
    return record
