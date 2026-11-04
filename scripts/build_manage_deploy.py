"""Build a source-only deployment snapshot; never copy workstation data or secrets."""
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
FILES = ("main.py", "auth.py", "api_downloads.py", "api_control.py", "api_workflows.py", "api_discovery.py", "api_publishing.py", "database.py", "models.py",
         "legacy.py", "collector.py", "spider.py", "requirements.txt", "alembic.ini", "Dockerfile", "Dockerfile.douk",
         "docker-compose.yml", ".dockerignore")
DIRECTORIES = ("services", "sources", "workers", "scripts", "templates", "static", "migrations", "tests")


def build(root=ROOT):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    target = root / "data/deploy" / ("manage-" + stamp)
    target.mkdir(parents=True, exist_ok=False)
    paths = [root / name for name in FILES]
    for directory in DIRECTORIES:
        paths.extend(path for path in (root / directory).rglob("*") if path.is_file() and
                     "__pycache__" not in path.parts and path.suffix not in (".pyc", ".pyo"))
    records = []
    for path in sorted(set(paths)):
        relative = path.relative_to(root)
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("Deployment source must not contain external links")
        if path.name.startswith(".env") or path.suffix in (".db", ".sqlite3", ".pem", ".key", ".mp4", ".docx"):
            raise ValueError("Private or generated files cannot enter the deployment bundle")
        records.append({"path": relative.as_posix(), "size": path.stat().st_size,
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
    manifest = {"id": target.name, "base_revision": revision, "includes_uncommitted_source": True,
                "site_origin": "https://drama-mvp.cfd", "mode": "manage", "files": records}
    manifest_path = target / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    archive = target / "source.tar.gz"
    with tarfile.open(archive, "w:gz", format=tarfile.PAX_FORMAT) as output:
        for row in records:
            output.add(root / row["path"], arcname=row["path"], recursive=False)
        output.add(manifest_path, arcname="DEPLOY_MANIFEST.json")
    # Verify bytes from the actual archive, before offering it for transfer.
    with tarfile.open(archive) as check:
        for row in records:
            if hashlib.sha256(check.extractfile(row["path"]).read()).hexdigest() != row["sha256"]:
                raise RuntimeError("Deployment snapshot verification failed")
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    (target / "source.sha256").write_text(checksum + "  source.tar.gz\n", encoding="ascii")
    return {"directory": str(target), "archive": str(archive), "sha256": checksum, "files": len(records)}


if __name__ == "__main__":
    print(json.dumps(build(), ensure_ascii=False))
