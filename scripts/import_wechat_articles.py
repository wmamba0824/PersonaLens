"""Offline, opt-in import of an existing WeChat JSON article export."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path, help="Directory containing original per-article JSON exports")
    parser.add_argument("--uid", required=True, help="Creator's Bilibili UID used as the existing local identity anchor")
    parser.add_argument("--account-name", required=True)
    parser.add_argument("--confirm-same-author", action="store_true")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    from app.ingest.identity import parse_creator
    from app.ingest.store import CreatorStore
    from app.ingest.wechat import read_wechat_export, import_wechat_articles
    store = CreatorStore(parse_creator(args.uid))
    if args.execute:
        result = import_wechat_articles(store, args.source, args.account_name, args.confirm_same_author)
    else:
        result = read_wechat_export(args.source, store.identity, args.account_name, args.confirm_same_author)
    print(json.dumps({"executed": args.execute, "uid": store.identity.uid, "cloud_api_calls": 0,
                      "summary": result["source_summary"]}, ensure_ascii=True), flush=True)
    if not args.execute:
        print("Dry run; --execute saves only the local runtime copy. Never generates or approves a portrait.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as exc:
        print("Local import refused: " + type(exc).__name__, file=sys.stderr)
        raise SystemExit(1)
