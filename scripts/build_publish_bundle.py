"""Workstation release builder. Preview is the default; never writes the source DB."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.publishing import build_bundle, ROOT


def main():
    parser = argparse.ArgumentParser(description="预览或生成网站发布包")
    parser.add_argument("--database", type=Path, default=ROOT / "drama.db")
    parser.add_argument("--data", type=Path, default=ROOT / "data")
    parser.add_argument("--output", type=Path, default=ROOT / "data/publish")
    parser.add_argument("--ranking", type=int, action="append", required=True)
    parser.add_argument("--release-id")
    parser.add_argument("--previous", type=Path)
    parser.add_argument("--all-episodes", action="store_true")
    parser.add_argument("--build", action="store_true", help="不指定时仅预览")
    args = parser.parse_args()
    try:
        result = build_bundle(args.database, args.data, args.output, args.ranking, preview=not args.build,
            release_id=args.release_id, previous=args.previous, representatives_only=not args.all_episodes)
    except (ValueError, OSError) as exc:
        parser.exit(1, str(exc) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
