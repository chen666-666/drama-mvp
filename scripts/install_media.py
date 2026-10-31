"""Install FFmpeg and ffprobe locally from the Windows build linked by ffmpeg.org."""
import hashlib
from pathlib import Path
import shutil
import tempfile
import zipfile
import httpx

ROOT = Path(__file__).resolve().parents[1]
URL = "https://github.com/GyanD/codexffmpeg/releases/download/9.0.2/ffmpeg-9.0.2-essentials_build.zip"


def main():
    destination = ROOT / "data/tools/ffmpeg/bin"
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
        archive = Path(temporary) / "ffmpeg.zip"
        print("Downloading FFmpeg tools from the pinned GyanD release...", flush=True)
        with httpx.stream("GET", URL, follow_redirects=True, timeout=30, trust_env=False) as response:
            response.raise_for_status()
            with archive.open("wb") as output:
                for chunk in response.iter_bytes(1024 * 1024):
                    output.write(chunk)
        checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
        with zipfile.ZipFile(archive) as bundle:
            for name in ("ffmpeg.exe", "ffprobe.exe"):
                matches = [value for value in bundle.namelist() if value.endswith("/bin/" + name)]
                if len(matches) != 1:
                    raise RuntimeError("Unexpected FFmpeg archive structure")
                with bundle.open(matches[0]) as source, (destination / name).open("wb") as target:
                    shutil.copyfileobj(source, target)
        (destination.parent / "SOURCE.txt").write_text(f"{URL}\nSHA-256 {checksum}\n", encoding="utf-8")
    print("FFmpeg and ffprobe installed to data/tools/ffmpeg/bin", flush=True)


if __name__ == "__main__":
    main()
