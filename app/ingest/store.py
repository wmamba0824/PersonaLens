"""Atomic per-UID snapshots and exclusive operation locks in ignored .runtime."""

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile

from app.ingest.identity import parse_creator


DEFAULT_ROOT = Path(__file__).resolve().parents[2] / ".runtime" / "creators"


class BusyError(RuntimeError):
    pass


def digest(records):
    return hashlib.sha256(json.dumps(records, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class CreatorStore:
    max_profile_chars = 6000
    def __init__(self, identity, root=None):
        self.identity = parse_creator(identity.uid)
        base = Path(root) if root is not None else DEFAULT_ROOT
        self.path = base / self.identity.key
        self.path.resolve().relative_to(base.resolve())
        if self.path.is_symlink():
            raise ValueError("人物目录不能是符号链接。")

    def read(self):
        file = self.path / "snapshot.json"
        if not file.exists():
            return {"schema": 1, "uid": self.identity.uid, "name": "UP主 " + self.identity.uid,
                    "records": [], "profile": {}, "corpus_digest": digest([]), "job": {}}
        data = json.loads(file.read_text(encoding="utf-8"))
        if data.get("schema") != 1 or data.get("uid") != self.identity.uid:
            raise ValueError("人物快照身份不匹配。")
        return data

    def write(self, data):
        if data.get("uid") != self.identity.uid:
            raise ValueError("不能写入其他人物的资料。")
        self.path.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path,
                                             suffix=".tmp", delete=False) as handle:
                temporary = Path(handle.name)
                json.dump(data, handle, ensure_ascii=False, indent=2)
            os.replace(str(temporary), str(self.path / "snapshot.json"))
        finally:
            if temporary and temporary.exists():
                temporary.unlink()

    @contextmanager
    def operation(self, stage):
        self.path.mkdir(parents=True, exist_ok=True)
        lock = self.path / "operation.lock"
        try:
            descriptor = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise BusyError("该人物有任务正在运行或上次进程中断。勿重复提交；确认旧进程停止后再检查人物目录的 operation.lock。") from None
        os.close(descriptor)
        try:
            data = self.read()
            data["job"] = {"stage": stage, "status": "running", "started_at": datetime.now(timezone.utc).isoformat()}
            self.write(data)
            yield
        except Exception:
            data = self.read()
            data["job"].update(status="failed", error="本阶段未完成；已保存的资料和画像保留。")
            self.write(data)
            raise
        else:
            data = self.read()
            data["job"].update(status="done")
            self.write(data)
        finally:
            lock.unlink()

    def save_collection(self, result):
        data = self.read()
        existing = {record["id"]: record for record in data["records"]}
        for record in result["records"]:
            if record.get("author_uid") != self.identity.uid:
                raise ValueError("采集包含其他作者的内容，已拒绝写入。")
            existing[record["id"]] = record
        records = sorted(existing.values(), key=lambda record: (record["published_at"], record["id"]))
        if len(records) > 1000:
            raise ValueError("第一版每个人物最多保留1000条，请缩小范围。")
        data.update(name=result["name"], records=records, corpus_digest=digest(records),
                    collection={key: result[key] for key in ("complete", "warning", "skipped")})
        if "coverage" in result:
            data["collection"]["coverage"] = result["coverage"]
        self.write(data)
        return data

    def profile_fresh(self, data=None):
        data = data or self.read()
        return bool(data.get("profile", {}).get("text")) and data["profile"].get("corpus_digest") == data["corpus_digest"]

    def approve(self, text, expected_digest=None, expected_revision=None):
        text = text.strip()
        with self.operation("approve"):
            data = self.read()
            if ((expected_digest is not None and expected_digest != data["corpus_digest"])
                    or (expected_revision is not None and expected_revision != data["profile"].get("revision"))):
                raise ValueError("画像或资料已被其他任务更新，请刷新后重新审核。")
            if not self.profile_fresh(data) or not 1 <= len(text) <= self.max_profile_chars:
                raise ValueError("画像已过期、为空或超长，请重新生成后审核。")
            data["profile"].update(text=text, approved=True, revision=digest([text]))
            self.write(data)
