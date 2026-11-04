"""Build a filtered, independently checked public release from a SQLite backup.

The live source is opened read-only. Public IDs and ranking snapshots never change.
Only explicitly listed columns can leave the workstation.
"""
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import subprocess
import tempfile
import uuid
import zlib

from sqlalchemy import create_engine
from services.migration import REVISION, upgrade_database
from services.media import digest

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_APPLICATION_ID = 0x44524D41
FIELDS = {
    "dramas": "id title cover_url description official_url ai_summary ai_score tags category created_at",
    "collection_runs": "id name mode cutoff_at window_hours keywords rule_version official_metadata created_at",
    "authors": "id platform account_id name homepage_url",
    "series": "id author_id series_key title collection_url title_note ai_type ai_basis review_status",
    "episodes": "id platform video_id series_id title original_url episode_number episode_range content_kind content_note published_at review_status",
    "metric_snapshots": "id run_id episode_id sampled_at likes comments favorites shares missing_reason method",
    "ranking_runs": "id run_id version mode scoring_mode snapshot created_at",
    "ranking_items": "id ranking_id series_id episode_id metric_snapshot_id position score detail",
    "download_catalogs": "id ranking_id series_id cursor source has_more checked total_episodes note cover_url updated_at published source_url",
    "download_episodes": "id catalog_id video_id title original_url episode_number episode_range source_duration status access note evidence_url review_history reused_media_id reused_sha256",
    "download_jobs": "id episode_id status trial attempts max_attempts message tool bytes_downloaded created_at updated_at",
    "media_files": "id job_id relative_path size duration format sha256 tracks validation_note manual_status manual_note checked_at created_at",
}
CLEAR = {"download_catalogs": {"cursor": "0", "source": "", "source_url": "", "note": "", "cover_url": ""},
         "download_episodes": {"note": "", "evidence_url": "", "review_history": "[]"},
         "download_jobs": {"message": "", "tool": ""},
         "media_files": {"validation_note": "", "manual_note": ""}}
SECRET = re.compile(r"(?i)(?:bearer\s+[a-z0-9._-]{12,}|sk-[a-z0-9_-]{16,}|-----BEGIN .*PRIVATE KEY|"
                    r"(?:cookie|sessionid|authorization|api[_-]?key|msToken|X-Bogus|signature)\s*[\"']?\s*[:=]\s*[\"']?\S+)")


def checked_text(value):
    if isinstance(value, str) and SECRET.search(value):
        raise ValueError("公开资料中疑似包含登录凭证或签名链接，已停止构建；请在本机审查文本")
    return value


def code_fingerprint(root=ROOT):
    """Identify the actual source tree, including changes that have not been committed."""
    root = Path(root)
    paths = set(root.glob("*.py"))
    for directory in ("services", "sources", "workers", "scripts", "migrations", "templates", "static"):
        paths.update(path for path in (root / directory).rglob("*") if path.is_file() and
                     "__pycache__" not in path.parts and path.suffix in (".py", ".html", ".js", ".css", ".mako"))
    paths.update(path for path in (root / "Dockerfile", root / "requirements.txt", root / "alembic.ini") if path.is_file())
    sha = hashlib.sha256()
    for path in sorted(paths, key=lambda path: path.relative_to(root).as_posix()):
        sha.update(path.relative_to(root).as_posix().encode() + b"\0")
        # Git checkouts may use CRLF on Windows and LF on the server.
        sha.update(path.read_bytes().replace(b"\r\n", b"\n") + b"\0")
    return sha.hexdigest()


def relative_path(value):
    value = value.replace("\\", "/")
    path = PurePosixPath(value)
    if not value or path.is_absolute() or any(p in ("..", ".", "") or ":" in p for p in value.split("/")):
        raise ValueError("发布文件路径无效")
    if path.parts[0] not in ("videos", "thumbnails"):
        raise ValueError("只能发布视频和封面目录中的文件")
    return path.as_posix()


def inside(root, relative):
    root = Path(root).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError("发布文件路径越界")
    return path


@contextmanager
def release_lock(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / ".publish.lock"
    # Kernel locks release after crashes; a persistent file is not a stale lock.
    with path.open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0"); handle.flush()
        handle.seek(0)
        try:
            if __import__("os").name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError):
            raise ValueError("已有发布操作正在进行，请稍后重试")
        try:
            yield
        finally:
            handle.seek(0)
            if __import__("os").name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def db_check(db):
    if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or db.execute("PRAGMA foreign_key_check").fetchall():
        raise ValueError("发布数据库完整性检查失败")
    if db.execute("SELECT version_num FROM alembic_version").fetchone()[0] != REVISION:
        raise ValueError("发布数据库迁移版本不匹配")


def _copy_public(source, target, ranking_ids, source_data, destination=None, representatives_only=True, preserve_paths=()):
    copied, files, gaps, snapshots, legacy = set(), {}, [], {}, {}
    target.row_factory = source.row_factory = sqlite3.Row

    def resource(episode):
        job = source.execute("SELECT * FROM download_jobs WHERE episode_id=?", (episode["id"],)).fetchone()
        media = source.execute("SELECT * FROM media_files WHERE job_id=?", (job["id"],)).fetchone() if job else None
        if episode["reused_media_id"]:
            media = source.execute("SELECT * FROM media_files WHERE id=?", (episode["reused_media_id"],)).fetchone()
            job = source.execute("SELECT * FROM download_jobs WHERE id=?", (media["job_id"],)).fetchone() if media else None
            original = source.execute("SELECT e.*,c.series_id FROM download_episodes e JOIN download_catalogs c ON c.id=e.catalog_id WHERE e.id=?", (job["episode_id"],)).fetchone() if job else None
            series_id = source.execute("SELECT series_id FROM download_catalogs WHERE id=?", (episode["catalog_id"],)).fetchone()[0]
            if (not original or original["series_id"] != series_id or original["video_id"] != episode["video_id"] or
                    original["status"] != "approved" or original["access"] != "public" or
                    media["sha256"] != episode["reused_sha256"]):
                return None, None
        return job, media

    def copy_row(table, identifier, overrides=None):
        if (table, identifier) in copied:
            return
        if table not in FIELDS:
            raise ValueError("发布范围包含未允许的数据表")
        row = source.execute(f'SELECT * FROM "{table}" WHERE id=?', (identifier,)).fetchone()
        if row is None:
            raise ValueError("发布资料缺少关联记录")
        values = {key: row[key] for key in FIELDS[table].split()}
        values.update(CLEAR.get(table, {}))
        values.update(overrides or {})
        for value in values.values():
            checked_text(value)
        for fk in source.execute(f'PRAGMA foreign_key_list("{table}")'):
            if values.get(fk[3]) is not None:
                copy_row(fk[2], values[fk[3]])
        columns = list(values)
        target.execute(f'INSERT INTO "{table}" ({",".join(columns)}) VALUES ({",".join("?" for _ in columns)})',
                       [values[key] for key in columns])
        copied.add((table, identifier))

    def add_file(relative, expected_size=None, expected_hash=None):
        relative = relative_path(relative)
        path = inside(source_data, relative)
        if not path.is_file():
            raise ValueError("文件缺失")
        size, sha = path.stat().st_size, digest(path)
        if expected_size is not None and (size != expected_size or sha != expected_hash):
            raise ValueError("文件大小或摘要不符")
        files[relative] = {"path": relative, "size": size, "sha256": sha}
        if destination:
            output = inside(destination, "data/" + relative)
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, output)
            if output.stat().st_size != size or digest(output) != sha:
                raise ValueError("复制后校验失败，请重新构建")
        return relative

    for row in source.execute("SELECT id FROM dramas"):
        copy_row("dramas", row[0])
        public_row = target.execute("SELECT * FROM dramas WHERE id=?", (row[0],)).fetchone()
        legacy[str(row[0])] = hashlib.sha256(json.dumps(dict(public_row), sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    for ranking_id in sorted(set(ranking_ids)):
        ranking = source.execute("SELECT * FROM ranking_runs WHERE id=?", (ranking_id,)).fetchone()
        if not ranking:
            raise ValueError(f"榜单 {ranking_id} 不存在，不能丢弃历史版本")
        copy_row("ranking_runs", ranking_id)
        snapshots[str(ranking_id)] = hashlib.sha256(ranking["snapshot"].encode()).hexdigest()
        snapshot = json.loads(ranking["snapshot"])
        for item in snapshot["items"]:
            if not item["selected"]:
                continue
            # Preserve selected item dependencies (including its sampled metric), never regenerate a ranking.
            for row in source.execute("SELECT id FROM ranking_items WHERE ranking_id=? AND series_id=?", (ranking_id, item["series_id"])):
                copy_row("ranking_items", row[0])
            catalog = source.execute("SELECT * FROM download_catalogs WHERE ranking_id=? AND series_id=?", (ranking_id, item["series_id"])).fetchone()
            label = {"ranking_id": ranking_id, "series_id": item["series_id"], "series_title": item["series_title"],
                     "video_id": item["video_id"]}
            if not catalog:
                gaps.append({**label, "reason": "目录尚未建立"})
                continue
            copy_row("download_catalogs", catalog["id"])
            episodes = source.execute("SELECT * FROM download_episodes WHERE catalog_id=? ORDER BY id", (catalog["id"],)).fetchall()
            if representatives_only:
                kept = []
                for row in episodes:
                    _, resource_media = resource(row)
                    if row["video_id"] == item["video_id"] or row["reused_media_id"] or resource_media and relative_path(resource_media["relative_path"]) in preserve_paths:
                        kept.append(row)
                episodes = kept
            if not episodes:
                gaps.append({**label, "reason": "代表集未收入目录"})
            for episode in episodes:
                job, media = resource(episode)
                reason = None
                if episode["status"] != "approved" or episode["access"] != "public":
                    reason = "尚未确认为公开正片"
                elif not job or job["status"] != "verified" or not media:
                    reason = "下载或自动校验未完成"
                elif media["manual_status"] != "passed":
                    reason = "人工抽检尚未通过"
                else:
                    try:
                        relative = add_file(media["relative_path"], media["size"], media["sha256"])
                    except (ValueError, OSError):
                        reason = "视频文件缺失、越界或摘要异常"
                if reason:
                    gaps.append({**label, "video_id": episode["video_id"], "reason": reason})
                    continue
                copy_row("media_files", media["id"], {"relative_path": relative})
                copy_row("download_episodes", episode["id"])
            cover = f"thumbnails/catalog-{catalog['id']}.jpg"
            if inside(source_data, cover).is_file():
                add_file(cover)
    # Independent collections use the same public-byte and review gates.
    for catalog in source.execute("SELECT c.* FROM download_catalogs c JOIN series s ON s.id=c.series_id WHERE c.ranking_id IS NULL AND c.published=1 AND s.review_status='approved'"):
        copy_row("download_catalogs", catalog["id"])
        label = {"catalog_id": catalog["id"], "series_id": catalog["series_id"]}
        for episode in source.execute("SELECT * FROM download_episodes WHERE catalog_id=?", (catalog["id"],)):
            job = source.execute("SELECT * FROM download_jobs WHERE episode_id=?", (episode["id"],)).fetchone()
            media = source.execute("SELECT * FROM media_files WHERE job_id=?", (job["id"],)).fetchone() if job else None
            if episode["status"] != "approved" or episode["access"] != "public" or not job or job["status"] != "verified" or not media or media["manual_status"] != "passed":
                gaps.append({**label, "video_id": episode["video_id"], "reason": "公开正片尚未完成自动校验与人工抽检"})
                continue
            try:
                relative = add_file(media["relative_path"], media["size"], media["sha256"])
            except (ValueError, OSError):
                gaps.append({**label, "video_id": episode["video_id"], "reason": "视频文件缺失、越界或摘要异常"})
                continue
            copy_row("media_files", media["id"], {"relative_path": relative})
        cover = f"thumbnails/catalog-{catalog['id']}.jpg"
        if inside(source_data, cover).is_file():
            add_file(cover)
    target.commit()
    db_check(target)
    return files, gaps, snapshots, legacy


def build_bundle(source_path, source_data, output_root, ranking_ids, *, preview=True,
                 release_id=None, previous=None, representatives_only=True):
    source_path, source_data, output_root = Path(source_path).resolve(), Path(source_data).resolve(), Path(output_root).resolve()
    if not source_path.is_file():
        raise ValueError("源数据库不存在")
    if not ranking_ids:
        with closing(sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True)) as checked:
            if not checked.execute("SELECT count(*) FROM download_catalogs WHERE ranking_id IS NULL AND published=1").fetchone()[0]:
                raise ValueError("请选择至少一个榜单，或先发布指定收录作品")
    release_id = release_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", release_id):
        raise ValueError("发布版本名称无效")
    with release_lock(output_root), tempfile.TemporaryDirectory(dir=output_root) as temp:
        temp = Path(temp)
        identity_path = output_root / "source-instance.json"
        if identity_path.exists():
            identity = json.loads(identity_path.read_text(encoding="utf-8"))
            if identity["database"] != str(source_path):
                raise ValueError("来源数据库已改变，请先人工核对来源与历史ID")
        else:
            identity = {"id": str(uuid.uuid4()), "database": str(source_path)}
            if not preview:
                identity_path.write_text(json.dumps(identity), encoding="utf-8")
        previous = previous or (output_root / "last-published.json" if (output_root / "last-published.json").exists() else None)
        older = json.loads(Path(previous).read_text(encoding="utf-8")) if previous else None
        if older:
            if older["source_instance"] != identity["id"]:
                raise ValueError("来源实例不一致，已停止发布")
            ranking_ids = sorted(set([*ranking_ids, *older["ranking_ids"]]))
        with closing(sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True)) as live, closing(sqlite3.connect(temp / "source.db")) as source:
            live.backup(source)
            db_check(source)
            # Reused bytes retain their original review/job and immutable ranking dependencies.
            source.row_factory = sqlite3.Row
            required = set(ranking_ids)
            while True:
                additional = set()
                for identifier in required:
                    additional.update(row[0] for row in source.execute(
                        "SELECT DISTINCT origin.ranking_id FROM download_episodes e "
                        "JOIN download_catalogs c ON c.id=e.catalog_id "
                        "JOIN media_files m ON m.id=e.reused_media_id "
                        "JOIN download_jobs j ON j.id=m.job_id "
                        "JOIN download_episodes original ON original.id=j.episode_id "
                        "JOIN download_catalogs origin ON origin.id=original.catalog_id "
                        "WHERE c.ranking_id=? AND origin.ranking_id IS NOT NULL", (identifier,)))
                if additional <= required:
                    break
                required.update(additional)
            ranking_ids = sorted(required)
            engine = create_engine(f"sqlite:///{(temp / 'public.db').as_posix()}")
            try:
                upgrade_database(engine)
            finally:
                engine.dispose()
            with closing(sqlite3.connect(temp / "public.db")) as target:
                target.execute("PRAGMA foreign_keys=ON")
                files, gaps, snapshots, legacy = _copy_public(source, target, ranking_ids, source_data,
                    None if preview else temp, representatives_only,
                    {item["path"] for item in older["files"]} if older else set())
                target.execute(f"PRAGMA application_id={PUBLIC_APPLICATION_ID}")
                target.execute(f"PRAGMA user_version={zlib.crc32(release_id.encode()) & 0x7fffffff}")
                target.commit()
        if older and any(snapshots.get(key) != value for key, value in older["ranking_snapshots"].items()):
            raise ValueError("历史榜单快照发生改变，已停止构建")
        if older and any(legacy.get(key) != value for key, value in older.get("legacy_records", {}).items()):
            raise ValueError("已发布红果短剧资料丢失或同号记录改变，请先核对来源与历史资料")
        # Previously published episodes must remain available, even when a later release selects representatives only.
        if older and any(item["path"] not in files or files[item["path"]]["sha256"] != item["sha256"]
                         for item in older["files"] if item["path"].startswith("videos/")):
            raise ValueError("本次会丢失或改变已发布视频，请选择包含全部已通过剧集或先人工核对")
        try:
            commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            commit = "unknown"
        manifest = {"format": 1, "release_id": release_id, "created_at": datetime.now(timezone.utc).isoformat(),
                    "source_instance": identity["id"], "code_commit": commit, "code_fingerprint": code_fingerprint(), "revision": REVISION,
                    "ranking_ids": sorted(set(ranking_ids)), "ranking_snapshots": snapshots,
                    "legacy_records": legacy,
                    "files": list(files.values()), "video_count": sum(p.startswith("videos/") for p in files), "gaps": gaps,
                    "database": {"path": "public.db", "size": (temp / "public.db").stat().st_size, "sha256": digest(temp / "public.db")}}
        if preview:
            return manifest
        final = output_root / release_id
        if final.exists():
            raise ValueError("该发布版本已存在，请使用新版本名；已有包可直接重传")
        (temp / "source.db").unlink()
        (temp / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        verify_bundle(temp)
        temp.rename(final)
        # TemporaryDirectory tolerates its renamed source being absent.
        return manifest


def verify_bundle(directory):
    directory = Path(directory).resolve()
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("format") != 1 or manifest.get("revision") != REVISION:
        raise ValueError("发布包版本不兼容")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", manifest["release_id"]):
        raise ValueError("发布版本名无效")
    if manifest["database"]["path"] != "public.db":
        raise ValueError("发布数据库路径无效")
    records = [(manifest["database"], directory / "public.db")]
    paths = set()
    for item in manifest["files"]:
        relative = relative_path(item["path"])
        if relative in paths:
            raise ValueError("发布清单存在重复路径")
        paths.add(relative)
        records.append((item, inside(directory, "data/" + relative)))
    for record, path in records:
        if not path.is_file() or path.stat().st_size != record["size"] or digest(path) != record["sha256"]:
            raise ValueError("发布包文件缺失或摘要不符")
    with closing(sqlite3.connect((directory / "public.db").as_uri() + "?mode=ro", uri=True)) as db:
        db_check(db)
        if db.execute("PRAGMA application_id").fetchone()[0] != PUBLIC_APPLICATION_ID or db.execute("PRAGMA user_version").fetchone()[0] != (zlib.crc32(manifest["release_id"].encode()) & 0x7fffffff):
            raise ValueError("数据库并非本次公开发布副本")
        ids = {row[0] for row in db.execute("SELECT id FROM ranking_runs")}
        if ids != set(manifest["ranking_ids"]):
            raise ValueError("发布榜单与清单不一致")
        for identifier, snapshot in db.execute("SELECT id, snapshot FROM ranking_runs"):
            checked_text(snapshot)
            if hashlib.sha256(snapshot.encode()).hexdigest() != manifest["ranking_snapshots"].get(str(identifier)):
                raise ValueError("榜单快照摘要不一致")
        media_rows = db.execute("SELECT m.relative_path,m.size,m.sha256,m.manual_status,j.status,e.status,e.access,c.ranking_id,c.series_id "
            "FROM media_files m JOIN download_jobs j ON j.id=m.job_id JOIN download_episodes e ON e.id=j.episode_id "
            "JOIN download_catalogs c ON c.id=e.catalog_id").fetchall()
        listed = {row["path"]: row for row in manifest["files"]}
        for path, size, sha, manual, job_status, status, access, ranking_id, series_id in media_rows:
            if manual != "passed" or job_status != "verified" or status != "approved" or access != "public":
                raise ValueError("发布数据库包含未通过检查的视频")
            if listed.get(path) != {"path": path, "size": size, "sha256": sha}:
                raise ValueError("媒体记录与文件清单不一致")
            if ranking_id is None:
                if not db.execute("SELECT count(*) FROM download_catalogs c JOIN series s ON s.id=c.series_id WHERE c.ranking_id IS NULL AND c.series_id=? AND c.published=1 AND s.review_status='approved'", (series_id,)).fetchone()[0]:
                    raise ValueError("发布媒体不属于已公开的指定收录作品")
            else:
                snapshot = json.loads(db.execute("SELECT snapshot FROM ranking_runs WHERE id=?", (ranking_id,)).fetchone()[0])
                if not any(row["selected"] and row["series_id"] == series_id for row in snapshot["items"]):
                    raise ValueError("发布媒体不属于入选作品")
        for row in db.execute("SELECT e.video_id,e.status,e.access,e.reused_sha256,c.series_id,c.ranking_id,"
                "original.video_id,origin.series_id,origin.ranking_id,m.sha256 "
                "FROM download_episodes e JOIN download_catalogs c ON c.id=e.catalog_id "
                "JOIN media_files m ON m.id=e.reused_media_id JOIN download_jobs j ON j.id=m.job_id "
                "JOIN download_episodes original ON original.id=j.episode_id "
                "JOIN download_catalogs origin ON origin.id=original.catalog_id"):
            video, status, access, expected, series, ranking, original_video, original_series, original_ranking, sha = row
            if (status != 'approved' or access != 'public' or expected != sha or video != original_video or
                    series != original_series or ranking is None or original_ranking is None or original_ranking >= ranking):
                raise ValueError("沿用资源与原作品、视频或审核摘要不一致")
        if len(media_rows) != manifest["video_count"] or len(media_rows) != sum(p.startswith("videos/") for p in paths):
            raise ValueError("实际视频数与清单不一致")
    return manifest
