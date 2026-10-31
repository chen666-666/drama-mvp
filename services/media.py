"""Conservative media verification; existence alone never means success."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from functools import lru_cache

if os.name == "nt":
    import ctypes
    # Service subprocesses must report missing DLLs as failures, without blocking on a Windows dialog.
    ctypes.windll.kernel32.SetErrorMode(0x8003)

ROOT = Path(__file__).resolve().parents[1]


def executable(name):
    configured = os.getenv(name.upper() + "_PATH")
    local = ROOT / "data/tools/ffmpeg/bin" / (name + (".exe" if os.name == "nt" else ""))
    return configured or (str(local) if local.is_file() else shutil.which(name))


def media_tools():
    result = {}
    for name in ("ffprobe", "ffmpeg"):
        path = executable(name)
        try:
            result[name] = bool(path) and tool_works(path, Path(path).stat().st_mtime_ns)
        except OSError:
            result[name] = False
    return result


@lru_cache(maxsize=8)
def tool_works(path, modified):
    try:
        flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        result = subprocess.run([path, "-version"], capture_output=True, timeout=5, **flags)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def digest(path):
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(block)
    return sha.hexdigest()


def thumbnail(video, target):
    """Optional cover from the downloaded original; never blocks media acceptance."""
    ffmpeg = executable("ffmpeg")
    if not ffmpeg:
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.stem + ".tmp.jpg")
    try:
        flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        result = subprocess.run([ffmpeg, "-v", "error", "-y", "-ss", "0", "-i", str(video),
                                 "-frames:v", "1", "-vf", "scale=640:-2", str(temporary)], capture_output=True, timeout=20, **flags)
        if result.returncode or not temporary.is_file():
            return False
        temporary.replace(target)
        return True
    except (OSError, subprocess.TimeoutExpired):
        return False
    finally:
        temporary.unlink(missing_ok=True)


def validate(path, source_duration=None, decode=False):
    probe = executable("ffprobe")
    if not probe:
        raise ValueError("缺少 ffprobe，请先安装媒体工具再下载；不会将未检查文件标记成功")
    if not path.is_file() or path.stat().st_size < 1024:
        raise ValueError("文件不存在或过小，无法作为有效视频")
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    result = subprocess.run([probe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
                            capture_output=True, timeout=60, **flags)
    try:
        data = json.loads(result.stdout)
        duration = float(data.get("format", {}).get("duration", 0))
        tracks = [row.get("codec_type") for row in data.get("streams", [])]
        format_name = data["format"]["format_name"]
    except (ValueError, KeyError, TypeError):
        raise ValueError("ffprobe 无法读取容器或时长，文件可能无效")
    if result.returncode or duration <= 0 or "video" not in tracks or "audio" not in tracks:
        raise ValueError("媒体检查未通过：须有可读取的容器、有效时长和音视频轨道")
    if source_duration and abs(duration - source_duration) > max(2, source_duration * .03):
        raise ValueError("本地时长与来源时长不符，请检查截断或来源记录")
    note = "容器、音视频轨道、时长检查通过；" + ("来源时长比对通过" if source_duration else "来源时长未知，无法比对")
    if decode:
        ffmpeg = executable("ffmpeg")
        if not ffmpeg:
            raise ValueError("缺少 ffmpeg，无法完整解码")
        checked = subprocess.run([ffmpeg, "-v", "error", "-xerror", "-i", str(path), "-f", "null", "-"],
                                 capture_output=True, timeout=3600, **flags)
        if checked.returncode:
            raise ValueError("完整解码检查发现视频损坏或截断")
        note += "；完整解码通过"
    suffix = "mp4" if "mp4" in format_name else "mkv" if "matroska" in format_name else "webm" if "webm" in format_name else "mov" if "mov" in format_name else None
    if not suffix:
        raise ValueError("媒体容器未在支持列表中，需人工检查")
    return {"size": path.stat().st_size, "duration": duration, "format": format_name, "tracks": json.dumps(tracks),
            "sha256": digest(path), "validation_note": note, "suffix": suffix}
