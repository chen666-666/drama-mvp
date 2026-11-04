"""Private review batches: checked clips, explicit human decisions, original media unchanged."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
import re
import secrets
import shutil
import subprocess
import threading
import time

from sqlalchemy import select
from database import SessionLocal
from models import DownloadCatalog, DownloadEpisode, DownloadJob, MediaFile
from services import downloads
from services.media import digest, executable, validate

_lock = threading.RLock()
_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="quick-review")
TTL = 24 * 3600
PROCESS_ID = secrets.token_hex(16)


def folder():
    path = downloads.inside(downloads.DATA, "private/quick-reviews")
    path.mkdir(parents=True, exist_ok=True)
    return path


def read(batch_id):
    if not re.fullmatch(r"[a-f0-9]{32}", batch_id):
        raise ValueError("快审批次不存在")
    try:
        data = json.loads((folder() / (batch_id + ".json")).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError("快审批次不存在")
    if time.time() - data["created"] > TTL:
        raise ValueError("快审批次已过期，请重新准备")
    return data


def write(data):
    path = folder() / (data["id"] + ".json")
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    if os.name != "nt":
        temp.chmod(0o600)
    temp.replace(path)


def stamp(media):
    return [media.sha256, media.size, media.relative_path, media.manual_status,
            str(media.checked_at), media.manual_note]


def eligibility(episode, catalog):
    return [episode.catalog_id, episode.video_id, episode.title, episode.original_url,
            episode.episode_number, episode.status, episode.access, episode.review_history,
            catalog.ranking_id, catalog.series_id, catalog.published,
            *(["episode_range", episode.episode_range] if episode.episode_range else [])]


def source(session, batch, row, pending=True):
    media = session.get(MediaFile, row["media_id"])
    if not media or stamp(media) != row["stamp"]:
        raise ValueError("视频或抽检结论已变化，请重新准备本批次")
    job = session.get(DownloadJob, media.job_id)
    episode = session.get(DownloadEpisode, job.episode_id)
    catalog = session.get(DownloadCatalog, episode.catalog_id)
    if row.get("eligibility") is not None and eligibility(episode, catalog) != row["eligibility"]:
        raise ValueError("目录资格、归属或公开状态已变化，请重新准备并核对公开后果")
    if episode.catalog_id != batch["catalog_id"] or pending and media.manual_status != "pending":
        raise ValueError("视频已不属于本批待审范围")
    path = downloads.delivery_path(session, media)
    return media, path


def view(data):
    return {"id": data["id"], "catalog_id": data["catalog_id"], "status": data["status"],
            "message": data.get("message", ""), "rows": [{key: row.get(key) for key in
            ("media_id", "episode_id", "episode_number", "episode_range", "title", "state", "message", "segments", "watched", "warnings")}
            for row in data["rows"]]}


def create(session, catalog, limit=10, skip_media_ids=None, restart=False, episode_ids=None):
    if not 1 <= limit <= 10:
        raise ValueError("每批请选择 1—10 集")
    downloads.catalog_item(session, catalog)
    with _lock:
        cleanup()
        # A preparing batch is reusable; after a restart it can be replaced without changing reviews.
        for path in sorted(folder().glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:100]:
            try:
                old = read(path.stem)
                within = episode_ids is None or all(r['episode_id'] in episode_ids for r in old['rows'])
                overlaps = bool(set(skip_media_ids or []).intersection(r['media_id'] for r in old['rows']))
                if within and not overlaps and old["catalog_id"] == catalog.id and old["status"] == "preparing" and not old.get('cancel_requested') and old.get("process") == PROCESS_ID and time.time()-old["created"] < 7200:
                    return view(old)
                if within and not restart and old['catalog_id'] == catalog.id and old['status'] == 'ready' and len(old['rows']) <= limit and not overlaps:
                    media = [session.get(MediaFile, row['media_id']) for row in old['rows']]
                    if all(item and item.manual_status == 'pending' and stamp(item) == row['stamp'] for item, row in zip(media, old['rows'])):
                        return view(old)
            except ValueError:
                continue
        rows = session.execute(select(DownloadEpisode, MediaFile).join(DownloadJob, DownloadJob.episode_id == DownloadEpisode.id)
            .join(MediaFile, MediaFile.job_id == DownloadJob.id).where(DownloadEpisode.catalog_id == catalog.id,
            DownloadEpisode.status == "approved", DownloadEpisode.access == "public", DownloadJob.status == "verified",
            MediaFile.manual_status == "pending", MediaFile.id.not_in(skip_media_ids or []),
            DownloadEpisode.id.in_(episode_ids) if episode_ids is not None else True).order_by(DownloadEpisode.episode_number, DownloadEpisode.id).limit(limit)).all()
        if not rows:
            raise ValueError("暂无自动校验通过且待人工抽检的视频，请先准备视频")
        data = {"id": secrets.token_hex(16), "catalog_id": catalog.id, "created": time.time(), "process": PROCESS_ID,
                "status": "preparing", "message": "正在复核视频并准备带声音的短片段", "rows": []}
        for episode, media in rows:
            data["rows"].append({"media_id": media.id, "episode_id": episode.id, "episode_number": episode.episode_number, "episode_range": episode.episode_range,
                "title": episode.title, "stamp": stamp(media), "eligibility": eligibility(episode, catalog), "state": "waiting", "message": "等待自动检查", "segments": [], "watched": [], "warnings": []})
        write(data)
        dispatch(data["id"])
        return view(data)


def cleanup():
    """Remove only expired private batches and their own validated clip directory."""
    for path in folder().glob('*.json'):
        if not re.fullmatch(r'[a-f0-9]{32}', path.stem) or time.time()-path.stat().st_mtime <= TTL:
            continue
        cache = downloads.inside(downloads.DATA, f'private/quick-review-clips/{path.stem}')
        base = downloads.inside(downloads.DATA, 'private/quick-review-clips')
        if cache.is_relative_to(base) and cache != base and cache.exists():
            shutil.rmtree(cache)
        path.unlink(missing_ok=True)


def dispatch(batch_id):
    _pool.submit(prepare, batch_id)


def pause(batch_id):
    with _lock:
        try:
            data = read(batch_id)
        except ValueError:
            return
        if data['status'] == 'preparing':
            data['cancel_requested'] = True
            data['message'] = '已请求暂停，当前媒体在安全边界或超时后停止'
            write(data)
        if data.get('next_id'):
            pause(data['next_id'])


def prefetch(session, batch_id, skip_media_ids=None, episode_ids=None):
    with _lock:
        data = read(batch_id)
        if data['status'] not in ('ready', 'submitted'):
            raise ValueError('当前批次准备完成后才能预备下一批')
        if data.get('next_id'):
            try:
                next_data = read(data['next_id'])
                if next_data['status'] in ('preparing', 'ready'):
                    return view(next_data)
            except ValueError:
                pass
        catalog = session.get(DownloadCatalog, data['catalog_id'])
        result = create(session, catalog, skip_media_ids=list(set(skip_media_ids or []) | {r['media_id'] for r in data['rows']}), episode_ids=episode_ids)
        data['next_id'] = result['id']
        write(data)
        return result


def status(batch_id):
    with _lock:
        data = read(batch_id)
        if data['status'] == 'preparing' and data.get('process') != PROCESS_ID:
            data.update(status='interrupted', message='准备期间服务已重启，请点击“重新准备待审批次”。已保存的人工结论不受影响。')
            write(data)
        return view(data)


def ranges(duration):
    length = min(5., duration)
    return [("开头", 0., length), ("中段", max(0., (duration-length)/2), length),
            ("结尾", max(0., duration-length), length)]


def make_clips(path, media, batch_id):
    # Private files are not copied into public releases or exposed through static routes.
    cache = downloads.inside(downloads.DATA, f"private/quick-review-clips/{batch_id}/{media.id}")
    cache.mkdir(parents=True, exist_ok=True)
    ffmpeg = executable("ffmpeg")
    if not ffmpeg:
        raise ValueError("缺少 FFmpeg，无法准备快审片段")
    segments = []
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    for index, (label, start, length) in enumerate(ranges(media.duration)):
        target = cache / f"{index}.mp4"
        result = subprocess.run([ffmpeg, "-v", "error", "-nostdin", "-y", "-ss", str(start), "-i", str(path),
            "-t", str(length), "-map", "0:v:0", "-map", "0:a:0", "-vf", "scale=640:640:force_original_aspect_ratio=decrease:force_divisible_by=2",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "25", "-pix_fmt", "yuv420p", "-c:a", "aac",
            "-movflags", "+faststart", str(target)], capture_output=True, timeout=90, **flags)
        if result.returncode:
            raise ValueError("短片段生成失败，请使用逐集人工抽检")
        info = validate(target, length)
        segments.append({"index": index, "label": label, "start": start, "duration": info["duration"],
            "url": f"/api/downloads/quick-reviews/{batch_id}/clips/{media.id}/{index}",
            "sha256": info["sha256"], "size": info["size"]})
    return segments


def prepare(batch_id):
    with _lock:
        data = read(batch_id)
    seen = set()
    started = time.monotonic()
    for index in range(len(data["rows"])):
        try:
            with _lock:
                data = read(batch_id)
                if data.get('cancel_requested') or time.monotonic() - started > 900:
                    data.update(status='interrupted', message='准备已暂停或达到十五分钟上限，请重新准备；已提交结论保留')
                    write(data)
                    return
                if shutil.disk_usage(downloads.DATA).free < 512 * 1024 * 1024:
                    data.update(status='interrupted', message='临时磁盘可用空间不足 512 MB，请清理空间或缩小范围后重新准备')
                    write(data)
                    return
                data["rows"][index]["state"] = "checking"
                data["message"] = f"正在检查第 {index+1}/{len(data['rows'])} 条，完整解码可能需要一些时间"
                write(data)
            row = data["rows"][index]
            with SessionLocal() as session:
                media, path = source(session, data, row)
                from services.media_analysis import checked_decode, _lock as processing_lock
                info = checked_decode(path)
                with _lock:
                    latest = read(batch_id)
                    if latest.get('cancel_requested'):
                        latest.update(status='interrupted', message='准备已暂停，未继续生成片段；请重新准备')
                        write(latest)
                        return
                if info["sha256"] != media.sha256 or info["size"] != media.size:
                    raise ValueError("视频摘要或大小变化，请重新获取并校验")
                clip_root = downloads.inside(downloads.DATA, 'private/quick-review-clips')
                if clip_root.exists() and sum(p.stat().st_size for p in clip_root.rglob('*.mp4')) >= 2 * 1024**3:
                    raise ValueError('私有片段已达到 2 GB 上限，请清理过期批次或缩小范围')
                with processing_lock:
                    segments = make_clips(path, media, batch_id)
                warnings = ["与本批其他视频文件完全相同，请核对是否重复"] if media.sha256 in seen else []
                seen.add(media.sha256)
            with _lock:
                data = read(batch_id)
                data["rows"][index].update(state="ready", message="完整解码、文件摘要和三个带声音片段检查通过", segments=segments, warnings=warnings)
                write(data)
        except Exception as exc:
            with _lock:
                data = read(batch_id)
                message = str(exc) if isinstance(exc, ValueError) else "自动检查未完成，请使用逐集抽检或重新准备"
                data["rows"][index].update(state="error", message=message)
                write(data)
    with _lock:
        data = read(batch_id)
        data.update(status="interrupted" if data.get('cancel_requested') else "ready", message="准备完成。请实际观看片段并确认声音，再统一提交；异常项不会自动通过。" if not data.get('cancel_requested') else '准备已暂停，请重新准备；已有人工结论保留')
        write(data)


def clip(session, batch_id, media_id, index):
    with _lock:
        data = read(batch_id)
        if data["status"] == "submitted":
            raise ValueError("本批已提交，请从播放区观看")
        row = next((row for row in data["rows"] if row["media_id"] == media_id and row["state"] == "ready"), None)
        if not row or index not in (0, 1, 2):
            raise ValueError("快审片段尚未准备好")
        source(session, data, row)
        path = downloads.inside(downloads.DATA, f"private/quick-review-clips/{batch_id}/{media_id}/{index}.mp4")
        segment = row["segments"][index]
        if not path.is_file() or path.stat().st_size != segment["size"] or digest(path) != segment["sha256"]:
            raise ValueError("快审片段已变化，请重新准备")
        row.setdefault("served", {}).setdefault(str(index), time.time())
        write(data)
        return path


def watched(session, batch_id, media_id, index):
    with _lock:
        data = read(batch_id)
        row = next((row for row in data["rows"] if row["media_id"] == media_id), None)
        if data["status"] != "ready" or not row or row["state"] != "ready" or index not in (0, 1, 2):
            raise ValueError("本批或片段尚未准备完成")
        source(session, data, row)
        if time.time() - row.get("served", {}).get(str(index), time.time()) < row["segments"][index]["duration"] * .85:
            raise ValueError("请先正常播放完本片段")
        if index not in row["watched"]:
            row["watched"].append(index)
        write(data)
        return view(data)


def submit(session, batch_id, data):
    with _lock:
        batch = read(batch_id)
        if batch["status"] == "submitted":
            if batch.get('submission') and batch['submission'] != data:
                raise ValueError('本批已提交，当前内容与已保存结论不同，请使用逐集更正')
            return {"message": "本批已经提交，无需重复操作", **batch["result"]}
        if batch["status"] != "ready" or not data["note"].strip():
            raise ValueError("请等待准备完成，并填写本批抽检说明")
        if batch.get('pending_submission') and batch['pending_submission'] != data:
            raise ValueError('上次提交结果尚需查询，请使用相同内容重试；已保存后可从逐集抽检更正')
        decisions = data["decisions"]
        ids = [item["media_id"] for item in decisions]
        if len(ids) != len(set(ids)) or not 1 <= len(ids) <= 10:
            raise ValueError("每批选择 1—10 条，不要重复提交同一视频")
        rows = {row["media_id"]: row for row in batch["rows"]}
        existing = [session.get(MediaFile, item['media_id']) for item in decisions]
        if all(media and media.id in rows and media.manual_status == item['status'] and
               f"连续快审 {batch_id}；" in media.manual_note for media, item in zip(existing, decisions)):
            counts = {key: sum(item['status'] == key for item in decisions) for key in ('passed', 'rejected')}
            batch.update(status='submitted', result=counts, submission=batch.get('pending_submission', data))
            write(batch)
            return {**counts, 'message': '本批结果已保存，无需重复提交'}
        publication = preview(session, batch_id, decisions)
        # Preserve the operation intent before committing DB results, for a lost-response retry.
        for item in decisions:
            if item['status'] == 'passed' and (not data['sound'] or not data['confirmed']):
                raise ValueError('通过前须实际看完三个片段，并确认本批通过项声音画面正常')
            if item['status'] == 'rejected' and not item.get('reason', '').strip():
                raise ValueError('请填写问题集的具体原因')
        batch['pending_submission'] = data
        write(batch)
        with session.begin_nested():
            counts = {"passed": 0, "rejected": 0}
            for item in decisions:
                row = rows.get(item["media_id"])
                if not row or row["state"] != "ready" or item["status"] not in counts:
                    raise ValueError("请选择本批已准备好的视频")
                if item["status"] == "passed" and (set(row["watched"]) != {0, 1, 2} or not data["sound"] or not data["confirmed"]):
                    raise ValueError("通过前须实际看完三个片段，并确认本批通过项声音画面正常")
                if item["status"] == "rejected" and not item.get("reason", "").strip():
                    raise ValueError("请填写问题集的具体原因")
                media, _ = source(session, batch, row)
                note = f"连续快审 {batch_id}；完整解码与摘要复核通过；" + data["note"]
                if item["status"] == "rejected":
                    note += "；问题：" + item["reason"]
                downloads.check_media(session, media, {"status": item["status"], "note": note,
                    "beginning": True, "middle": True, "ending": True, "sound": data["sound"]})
                counts[item["status"]] += 1
        # Commit business results before recording completion so retry never silently loses decisions.
        session.commit()
        batch.update(status="submitted", result={**counts, 'public_preview': publication['counts']['public']}, submission=data)
        write(batch)
        return {**counts, "message": f"已保存：{counts['passed']} 集通过，{counts['rejected']} 集发现问题；未提交项继续待审。"}


def preview(session, batch_id, decisions):
    with _lock:
        batch = read(batch_id)
        if batch['status'] != 'ready':
            raise ValueError('请先完成本批准备')
        mapping = {r['media_id']: r for r in batch['rows']}
        ids = [d['media_id'] for d in decisions]
        if len(ids) != len(set(ids)):
            raise ValueError('请勿重复选择条目')
        catalog = session.get(DownloadCatalog, batch['catalog_id'])
        counts = {'passed': 0, 'rejected': 0, 'pending': len(batch['rows']) - len(ids), 'public': 0}
        items = []
        for decision in decisions:
            row = mapping.get(decision['media_id'])
            if not row or row['state'] != 'ready' or decision['status'] not in ('passed', 'rejected'):
                raise ValueError('请选择本批已准备好的视频')
            source(session, batch, row)
            if decision['status'] == 'passed' and set(row['watched']) != {0, 1, 2}:
                raise ValueError('通过前须看完三个片段')
            counts[decision['status']] += 1
            public = decision['status'] == 'passed' and (catalog.ranking_id is not None or catalog.published)
            counts['public'] += int(public)
            items.append({'media_id': row['media_id'], 'title': row['title'], 'status': decision['status'], 'expected_public': public})
        message = (f"本批拟通过 {counts['passed']} 集、拟拒绝 {counts['rejected']} 集、继续待审 {counts['pending']} 集。"
                   f"提交成功并满足文件与公开检查后，预计 {counts['public']} 集可向游客播放和保存。")
        if catalog.ranking_id is None and not catalog.published:
            message += '本目录尚未发布，本批通过后仍不公开，需在目录中发布。'
        return {'counts': counts, 'items': items, 'message': message}
