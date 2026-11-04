#!/usr/bin/env python3
"""批任务：只刷新 suggested_tags / suggested_at / suggested_asof / attr_evidence。

不写 confirmed_*。正式归因只能走 POST /api/liupan/decision_log/confirm。

  python3 scripts/refresh_suggested.py --date 2026-11-04 --code 000001.SZ \\
      --tags '["竞价高开"]' --evidence '{"note":"批任务示例"}'
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import liupan_store  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description="刷新 suggested_*（禁止写 confirmed_*）")
    parser.add_argument("--date", required=True)
    parser.add_argument("--code", required=True)
    parser.add_argument("--tags", required=True, help="JSON 字符串数组")
    parser.add_argument("--evidence", help="JSON 对象或数组")
    parser.add_argument("--asof")
    parser.add_argument("--name")
    args = parser.parse_args(argv)
    try:
        tags = json.loads(args.tags)
    except Exception:
        print("tags 不是 JSON 数组", file=sys.stderr)
        return 2
    evidence = None
    if args.evidence:
        try:
            evidence = json.loads(args.evidence)
        except Exception:
            print("evidence 不是 JSON", file=sys.stderr)
            return 2
    try:
        body = liupan_store.refresh_suggested(
            args.date, args.code, tags, evidence=evidence, asof=args.asof, name=args.name,
        )
    except liupan_store.StoreError as exc:
        print(f"{exc.code}: {exc.message}", file=sys.stderr)
        return 2
    print(json.dumps(body, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
