"""Create a dedicated local SSH key without shell-dependent inline quoting."""
import argparse
from pathlib import Path
import subprocess


def ensure_key(path):
    path = Path(path).resolve()
    public = Path(str(path) + ".pub")
    if path.exists() or public.exists():
        if not path.is_file() or not public.is_file():
            raise ValueError("部署密钥文件不完整，请保留现有文件并联系维护人员")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "drama-site-deploy", "-f", str(path)],
                   check=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--key", type=Path, default=Path(__file__).resolve().parents[1] / "data/private/site-deploy-key")
    args = parser.parse_args()
    try:
        ensure_key(args.key)
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        parser.exit(1, str(exc) + "\n")
    print("Local deployment key is ready.")


if __name__ == "__main__":
    main()
