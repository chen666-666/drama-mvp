"""Run on the server; preview by default. Requires the project Python dependencies."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.site_release import activate, rollback


def main():
    parser = argparse.ArgumentParser(description="网站发布预检、激活与回退")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--domain")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--rollback", action="store_true")
    parser.add_argument("--inventory-confirmed", action="store_true")
    parser.add_argument("--rights-confirmed", action="store_true")
    args = parser.parse_args()
    try:
        if args.rollback:
            result = rollback(args.root, preview=not args.apply)
        elif args.bundle:
            result = activate(args.root, args.bundle, domain=args.domain, preview=not args.apply,
                inventory_confirmed=args.inventory_confirmed, rights_confirmed=args.rights_confirmed)
        else:
            parser.error("请选择 --bundle 或 --rollback")
    except (ValueError, RuntimeError, OSError) as exc:
        parser.exit(1, str(exc) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
