"""Private, single-worker media analysis. Findings never change human decisions."""
import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import secrets
from database import SessionLocal
from models import MediaFile
from services import downloads
from services.media import digest, executable, validate

_lock = threading.Lock()
_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='media-analysis')
_jobs_lock = threading.RLock()
PROCESS_ID = secrets.token_hex(16)
PARAMETERS = {'version': 1, 'black_seconds': 2, 'silence_seconds': 3, 'freeze_seconds': 3,
              'black_pixel': .10, 'silence_db': -50, 'freeze_noise': '-60dB'}


def identity():
    path = executable('ffmpeg')
    if not path:
        raise ValueError('缺少 FFmpeg，不能分析视频')
    flags = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
    result = subprocess.run([path, '-version'], capture_output=True, timeout=5, **flags)
    if result.returncode:
        raise ValueError('FFmpeg 无法运行')
    return result.stdout.decode('utf-8', 'replace').splitlines()[0]


def analyze(path, scan=False):
    # This lock is shared by decode-only quick reviews and opt-in anomaly scans.
    with _lock:
        started = time.perf_counter()
        info = validate(path)
        version = identity()
        config = {'sha256': info['sha256'], 'tool': version, 'scan': scan, 'parameters': PARAMETERS}
        key = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
        folder = downloads.inside(downloads.DATA, 'private/media-analysis')
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / (key + '.json')
        if target.is_file():
            try:
                cached = json.loads(target.read_text(encoding='utf-8'))
                if cached['config'] == config and cached['info']['sha256'] == info['sha256']:
                    return {**cached, 'cached': True, 'cache_check_seconds': round(time.perf_counter()-started, 3)}
            except (ValueError, KeyError):
                pass
        if shutil.disk_usage(folder).free < 512 * 1024 * 1024:
            raise ValueError('可用空间不足 512 MB，已暂停分析')
        flags = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
        args = [executable('ffmpeg'), '-hide_banner', '-nostdin', '-v', 'info' if scan else 'error', '-xerror', '-i', str(path)]
        if scan:
            args += ['-vf', 'blackdetect=d=2:pix_th=0.10,freezedetect=n=-60dB:d=3',
                     '-af', 'silencedetect=noise=-50dB:d=3']
        try:
            result = subprocess.run(args + ['-f', 'null', '-'], capture_output=True, timeout=300, **flags)
        except subprocess.TimeoutExpired:
            raise ValueError('媒体处理达到五分钟上限，已停止；请缩小范围或使用逐集抽检')
        if result.returncode:
            raise ValueError('完整解码失败，请重新获取或排查视频；未修改人工结论')
        if digest(path) != info['sha256']:
            raise ValueError('分析期间文件已变化，请重新准备')
        findings = parse(result.stderr.decode('utf-8', 'replace'), info['duration']) if scan else []
        data = {'config': config, 'info': info, 'findings': findings, 'cached': False,
                'processing_seconds': round(time.perf_counter()-started, 3),
                'message': '发现配置范围内的复核线索，不代表视频损坏' if findings else
                           '未发现所配置的异常' if scan else '完整解码通过'}
        temp = target.with_suffix('.tmp')
        temp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        temp.replace(target)
        return data


def checked_decode(path):
    return analyze(path)['info']


def parse(log, duration):
    findings = []
    for start, end in re.findall(r'black_start:([\d.]+)\s+black_end:([\d.]+)', log):
        findings.append({'kind': '黑屏', 'start': float(start), 'end': float(end)})
    for kind, prefix in [('静音', 'silence'), ('冻结', 'freeze')]:
        start = None
        for match in re.finditer(prefix + r'_(start|end):\s*([\d.]+)', log):
            if match[1] == 'start':
                start = float(match[2])
            elif start is not None:
                findings.append({'kind': kind, 'start': start, 'end': float(match[2])})
                start = None
        if start is not None:
            findings.append({'kind': kind, 'start': start, 'end': duration})
    merged = []
    for kind in ('黑屏', '静音', '冻结'):
        for row in sorted([f for f in findings if f['kind'] == kind], key=lambda r: r['start']):
            if merged and merged[-1]['kind'] == kind and row['start'] - merged[-1]['end'] <= .5:
                merged[-1]['end'] = max(merged[-1]['end'], row['end'])
            else:
                merged.append(row)
    return sorted(merged, key=lambda r: r['start'])


def job_file(media_id):
    folder = downloads.inside(downloads.DATA, 'private/media-analysis-jobs')
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f'{int(media_id)}.json'


def write_job(media_id, data):
    path = job_file(media_id)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    temporary.replace(path)


def status(session, media):
    downloads.delivery_path(session, media)
    path = job_file(media.id)
    if not path.is_file():
        return {'status': 'none', 'message': '尚未运行手动异常检测，不影响规定的人工观看'}
    with _jobs_lock:
        data = json.loads(path.read_text(encoding='utf-8'))
        if data['sha256'] != media.sha256 or data['size'] != media.size:
            return {'status': 'stale', 'message': '文件已更换，请重新运行检测'}
        if data['status'] == 'running' and data['process'] != PROCESS_ID:
            return {'status': 'interrupted', 'message': '检测期间服务重启，请手动重新运行'}
        return {k: v for k, v in data.items() if k not in ('process', 'size', 'sha256')}


def request_scan(session, media):
    downloads.delivery_path(session, media)
    with _jobs_lock:
        current = status(session, media)
        if current['status'] == 'running':
            return current
        write_job(media.id, {'status': 'running', 'message': '检测已排队，单并发，单集最多五分钟；仅提供复核线索',
                            'process': PROCESS_ID, 'sha256': media.sha256, 'size': media.size})
        _pool.submit(scan_job, media.id, media.sha256, media.size)
    return {'status': 'running', 'message': '已开始手动异常检测，规定观看仍需完成'}


def scan_job(media_id, sha256, size):
    try:
        with SessionLocal() as session:
            media = session.get(MediaFile, media_id)
            if not media or media.sha256 != sha256 or media.size != size:
                raise ValueError('文件已变化，请重新运行检测')
            path = downloads.delivery_path(session, media)
            result = analyze(path, scan=True)
            session.expire_all()
            media = session.get(MediaFile, media_id)
            downloads.delivery_path(session, media)
            if media.sha256 != sha256:
                raise ValueError('文件已变化，请重新运行检测')
        data = {'status': 'ready', 'message': result['message'], 'findings': result['findings'],
                'parameters': PARAMETERS, 'tool': result['config']['tool'], 'cached': result['cached'],
                'processing_seconds': result.get('processing_seconds'), 'cache_check_seconds': result.get('cache_check_seconds')}
    except Exception as exc:
        data = {'status': 'error', 'message': str(exc) if isinstance(exc, ValueError) else '检测未完成，请核对工具和文件后重试'}
    with _jobs_lock:
        write_job(media_id, {**data, 'sha256': sha256, 'size': size, 'process': PROCESS_ID})
