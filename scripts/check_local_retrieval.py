"""Opt-in real model smoke test, using synthetic text and no chat API."""

import argparse
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.rag.local_index import LocalIndex


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="Download/load the public local model")
    args = parser.parse_args()
    if not args.execute:
        print("Dry run. Add --execute to download/load the public BGE model; no chat API or user TXT is used.")
        return
    entries = [
        "顾客收货后七天内可以申请退货退款，商品须未使用并保留完整包装。",
        "园区的玫瑰每周浇水两次，避免阳光强烈时给叶片喷水。",
        "数据库每日凌晨备份一次，备份保存三十天，异常时联系运维。",
    ]
    with tempfile.TemporaryDirectory(prefix="md-local-check-") as folder:
        path = Path(folder) / "index.npz"
        initial = LocalIndex(entries, path)
        results = initial.search("买的东西不满意，可以把钱退回来吗？", entries, 1)
        assert results and results[0][1] == entries[0], "Semantic retrieval did not rank the return policy first"
        cached = LocalIndex(entries, path)
        assert cached.stats["generated"] == 0 and cached.stats["reused"] == 3
        changed_entries = entries[:2] + ["客服工作时间为工作日早上九点至下午六点。"]
        changed = LocalIndex(changed_entries, path)
        assert changed.stats["generated"] == 1 and changed.stats["reused"] == 2
        reordered = LocalIndex(list(reversed(changed_entries)), path)
        assert reordered.stats["generated"] == 0
        print(json.dumps({"semantic_top1": True, "dimensions": initial.matrix.shape[1],
                          "top1_score": round(results[0][0], 4), "initial": initial.stats,
                          "cached": cached.stats, "changed": changed.stats,
                          "reordered": reordered.stats, "chat_api_calls": 0}, ensure_ascii=True))


if __name__ == "__main__":
    main()
