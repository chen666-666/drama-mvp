"""Install a pinned, separate DouK runtime. Never overwrite a different version."""
from pathlib import Path
import io
import subprocess
import sys
import urllib.request
import venv
import zipfile

REVISION = "e327b9549d284a8b1613566f949fe2dbb8a6c2a1"
ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "data/tools/douk"


def main():
    marker = TARGET / "UPSTREAM_REVISION.txt"
    if TARGET.exists():
        if not marker.exists() or marker.read_text(encoding="utf-8").splitlines()[1] != REVISION:
            raise RuntimeError("Existing tool differs from pinned source; preserve it before installing")
    else:
        url = f"https://codeload.github.com/JoeanAmier/TikTokDownloader/zip/{REVISION}"
        with urllib.request.urlopen(url, timeout=90) as response:
            payload = response.read()
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            for item in archive.infolist():
                relative = Path(*Path(item.filename).parts[1:])
                destination = (TARGET / relative).resolve()
                if not destination.is_relative_to(TARGET.resolve()):
                    raise ValueError("Unsafe archive path")
                if item.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                else:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(archive.read(item))
        marker.write_text(f"https://github.com/JoeanAmier/TikTokDownloader\n{REVISION}\nGPL-3.0\n", encoding="utf-8")
    runtime = TARGET / ".runtime"
    executable = runtime / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if not executable.exists():
        venv.EnvBuilder(with_pip=True).create(runtime)
    subprocess.run([str(executable), "-m", "pip", "install", "-r", str(TARGET / "requirements.txt")], check=True)
    print(f"Installed separate DouK tool at {REVISION}; license: GPL-3.0 (see tool/LICENSE)")


if __name__ == "__main__":
    main()
