"""Server-side release activation with preflight, health checks and rollback.

No SSH credentials, git operations or source database replacement. Docker images
are versioned so rollback restores the previous code as well as its public data.
"""
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import time
from urllib.request import Request, urlopen

from services.media import digest
from services.publishing import code_fingerprint, inside, release_lock, verify_bundle


def run(command, root):
    subprocess.run(command, cwd=root, check=True, timeout=1800)


def compose(config, *args):
    return ["docker", "compose", "--project-name", "drama-public", "-f", str(config), *args]


def atomic_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def smoke(base_url, manifest):
    """Checks actual bytes and seek responses, not merely /health."""
    base_url = base_url.rstrip("/")
    with urlopen(base_url + "/health", timeout=15) as response:
        if json.load(response).get("release_id") != manifest["release_id"]:
            raise ValueError("网站尚未运行本次发布版本")
    with urlopen(base_url + "/", timeout=15) as response:
        if response.status != 200:
            raise ValueError("网站首页检查失败")
    urls = []
    for ranking_id in manifest["ranking_ids"]:
        with urlopen(f"{base_url}/api/library/rankings/{ranking_id}", timeout=30) as response:
            detail = json.load(response)
        for catalog in detail["catalogs"]:
            urls.extend(episode["url"] for episode in catalog["episodes"])
    if len(set(urls)) != manifest["video_count"]:
        raise ValueError("网站实际可播数量与发布清单不同")
    evidence = []
    for url in sorted(set(urls)):
        with urlopen(Request(base_url + url, headers={"Range": "bytes=0-31"}), timeout=30) as response:
            if response.status != 206 or not response.headers.get("Content-Range", "").startswith("bytes 0-31/") or len(response.read()) != 32:
                raise ValueError("视频拖动与分段读取检查失败")
            evidence.append({"url": url, "status": response.status, "content_range": response.headers["Content-Range"]})
    return evidence


def _config(root, release, manifest):
    return {"services": {"drama-mvp": {"image": f"drama-site:{manifest['release_id']}", "container_name": "drama-mvp",
        "ports": ["127.0.0.1:8000:8000"], "environment": {"DRAMA_SITE_MODE": "public",
            "DRAMA_SITE_RELEASE": manifest["release_id"],
            "DRAMA_DATABASE_URL": "sqlite:///file:/app/drama.db?mode=ro&uri=true"},
        "read_only": True, "tmpfs": ["/tmp"], "security_opt": ["no-new-privileges:true"],
        "volumes": [{"type": "bind", "source": str(release / "public.db"), "target": "/app/drama.db", "read_only": True},
            {"type": "bind", "source": str(root / "data/videos"), "target": "/app/data/videos", "read_only": True},
            {"type": "bind", "source": str(release / "data/thumbnails"), "target": "/app/data/thumbnails", "read_only": True}],
        "restart": "unless-stopped"}}}


def activate(root, incoming, *, inventory_confirmed=False, rights_confirmed=False,
             domain=None, preview=True, runner=run, checker=smoke):
    root, incoming = Path(root).resolve(), Path(incoming).resolve()
    site = root / "data/site"
    # This boundary applies even when a CLI user supplies a different path.
    if not incoming.is_relative_to(site / "incoming"):
        raise ValueError("发布包必须位于 data/site/incoming 下")
    with release_lock(site):
        manifest = verify_bundle(incoming)
        current_path = site / "current.json"
        current = json.loads(current_path.read_text(encoding="utf-8")) if current_path.exists() else None
        if current and current["manifest"]["release_id"] == manifest["release_id"]:
            if current["manifest"] != manifest:
                raise ValueError("同名版本内容改变，请创建新版本")
            return {"status": "already_active", "release_id": manifest["release_id"]}
        if current:
            old = current["manifest"]
            if old["source_instance"] != manifest["source_instance"]:
                raise ValueError("来源实例改变，已停止激活")
            if any(manifest["ranking_snapshots"].get(key) != value for key, value in old["ranking_snapshots"].items()):
                raise ValueError("历史榜单缺失或被修改")
            if any(manifest.get("legacy_records", {}).get(key) != value for key, value in old.get("legacy_records", {}).items()):
                raise ValueError("已发布红果短剧资料缺失或被修改")
            files = {row["path"]: row["sha256"] for row in manifest["files"]}
            if any(files.get(row["path"]) != row["sha256"] for row in old["files"] if row["path"].startswith("videos/")):
                raise ValueError("已发布视频缺失或被修改")
        # Reject conflicting existing media before changing containers or copying bytes.
        for item in manifest["files"]:
            if item["path"].startswith("videos/"):
                path = inside(root / "data", item["path"])
                if path.exists() and (not path.is_file() or digest(path) != item["sha256"]):
                    raise ValueError("已有视频与本次文件冲突，不覆盖旧文件")
        result = {"status": "preview", "release_id": manifest["release_id"], "video_count": manifest["video_count"], "gaps": manifest["gaps"]}
        if preview:
            return result
        if code_fingerprint(root) != manifest["code_fingerprint"]:
            raise ValueError("服务器代码与发布包对应的代码不同，请先部署并核对本次代码")
        if not rights_confirmed or not current and not inventory_confirmed:
            raise ValueError("首次激活需确认服务器资料盘点已完成；每次公开发布均需确认视频展示许可")
        if not domain or not domain.startswith("https://") or "@" in domain:
            raise ValueError("激活需提供经确认的 HTTPS 网站地址用于验收")
        release = site / "releases" / manifest["release_id"]
        if release.exists():
            if verify_bundle(release) != manifest:
                raise ValueError("已归档的同名版本内容不同")
        else:
            release.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=release.parent) as staging:
                # Copy only validated files; arbitrary extra incoming content is never used.
                for relative in ["public.db", "manifest.json", *["data/" + row["path"] for row in manifest["files"]]]:
                    target = inside(staging, relative)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(inside(incoming, relative), target)
                verify_bundle(staging)
                Path(staging).rename(release)
        for item in manifest["files"]:
            if item["path"].startswith("videos/"):
                path = inside(root / "data", item["path"])
                if not path.exists():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    temporary = path.with_name(path.name + ".publish-part")
                    shutil.copyfile(release / "data" / item["path"], temporary)
                    if digest(temporary) != item["sha256"]:
                        raise ValueError("视频落盘后摘要不一致")
                    temporary.replace(path)
        (root / "data/videos").mkdir(parents=True, exist_ok=True)
        (release / "data/thumbnails").mkdir(parents=True, exist_ok=True)
        config = release / "compose.json"
        atomic_json(config, _config(root, release, manifest))
        # Build before downtime. Each release retains its image for code rollback.
        runner(["docker", "build", "-t", f"drama-site:{manifest['release_id']}", "."], root)
        previous_path = site / "previous.json"
        backup = site / "backups" / manifest["release_id"]
        backup.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            backup.chmod(0o700)
        legacy_config = root / "docker-compose.yml"
        legacy_stopped = legacy_renamed = old_stopped = new_started = False
        legacy_name = "drama-mvp-before-" + manifest["release_id"]
        try:
            if current:
                runner(compose(current["config"], "down"), root)
                old_stopped = True
                atomic_json(backup / "previous.json", current)
            else:
                runner(["docker", "compose", "-f", str(legacy_config), "stop"], root)
                legacy_stopped = True
                if (root / "drama.db").is_file():
                    with closing(sqlite3.connect((root / "drama.db").as_uri() + "?mode=ro", uri=True)) as source, closing(sqlite3.connect(backup / "original.db")) as target:
                        source.backup(target)
                    if os.name != "nt":
                        (backup / "original.db").chmod(0o600)
                shutil.copyfile(legacy_config, backup / "docker-compose.yml")
                runner(["docker", "rename", "drama-mvp", legacy_name], root)
                legacy_renamed = True
            new_started = True
            runner(compose(config, "up", "-d", "--force-recreate", "--no-build"), root)
            evidence = None
            for attempt in range(15):
                try:
                    evidence = checker("http://127.0.0.1:8000", manifest)
                    break
                except Exception:
                    if attempt == 14:
                        raise
                    time.sleep(2)
            external = checker(domain, manifest)
            # Recreate a second time to verify persisted data and actual playback after restart.
            runner(compose(config, "restart"), root)
            for attempt in range(15):
                try:
                    checker(domain, manifest)
                    break
                except Exception:
                    if attempt == 14:
                        raise
                    time.sleep(2)
            state = {"manifest": manifest, "config": str(config), "domain": domain,
                     "activated_at": datetime.now(timezone.utc).isoformat(), "previous": current,
                     "legacy_container": legacy_name if not current else current.get("legacy_container"),
                     "evidence": {"local": evidence, "domain": external, "restart": True}}
            if current:
                atomic_json(previous_path, current)
            atomic_json(current_path, state)
            atomic_json(release / "activation.json", state)
            return {"status": "active", "release_id": manifest["release_id"], "video_count": manifest["video_count"]}
        except Exception as exc:
            try:
                if new_started:
                    runner(compose(config, "down"), root)
                if current and old_stopped:
                    runner(compose(current["config"], "up", "-d", "--force-recreate", "--no-build"), root)
                elif legacy_stopped:
                    if legacy_renamed:
                        runner(["docker", "rename", legacy_name, "drama-mvp"], root)
                    runner(["docker", "compose", "-f", str(legacy_config), "start"], root)
            except Exception as recovery:
                raise RuntimeError("发布失败，自动恢复也未完成；请管理员检查容器，原数据库和视频均保留") from recovery
            raise RuntimeError("发布失败，已恢复原服务；失败包保留供排查") from exc


def rollback(root, *, preview=True, runner=run, checker=smoke):
    root = Path(root).resolve()
    site = root / "data/site"
    with release_lock(site):
        current = json.loads((site / "current.json").read_text(encoding="utf-8"))
        previous = current.get("previous")
        if not previous:
            raise ValueError("尚无已验收的公开版本可回退；首次切换原服务仍保留，需管理员处理")
        verify_bundle(Path(previous["config"]).parent)
        if preview:
            return {"status": "preview", "release_id": previous["manifest"]["release_id"]}
        runner(compose(current["config"], "down"), root)
        try:
            runner(compose(previous["config"], "up", "-d", "--force-recreate", "--no-build"), root)
            for attempt in range(15):
                try:
                    checker(previous["domain"], previous["manifest"])
                    break
                except Exception:
                    if attempt == 14:
                        raise
                    time.sleep(2)
            atomic_json(site / "current.json", previous)
            return {"status": "rolled_back", "release_id": previous["manifest"]["release_id"]}
        except Exception as exc:
            runner(compose(previous["config"], "down"), root)
            runner(compose(current["config"], "up", "-d", "--force-recreate", "--no-build"), root)
            raise RuntimeError("回退未通过验收，已恢复回退前版本") from exc
