"""Atomic per-UID snapshots and exclusive operation locks in ignored .runtime."""

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import uuid

from app.ingest.identity import parse_identity
from app.rag.chunking import CHUNKER_VERSION


DEFAULT_ROOT = Path(__file__).resolve().parents[2] / ".runtime" / "creators"


class BusyError(RuntimeError):
    pass


def digest(records):
    return hashlib.sha256(json.dumps(records, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class CreatorStore:
    max_profile_chars = 6000
    def __init__(self, identity, root=None):
        self.identity = parse_identity(identity.uid)
        base = Path(root) if root is not None else DEFAULT_ROOT
        self.path = base / self.identity.key
        self.path.resolve().relative_to(base.resolve())
        if self.path.is_symlink():
            raise ValueError("人物目录不能是符号链接。")

    def read(self):
        file = self.path / "snapshot.json"
        if file.is_symlink():
            raise ValueError("人物快照不能是符号链接。")
        if not file.exists():
            return {"schema": 1, "uid": self.identity.uid, "name": "UP主 " + self.identity.uid,
                    "records": [], "profile": {}, "corpus_digest": digest([]), "job": {}}
        if file.stat().st_size > 32 * 1024 * 1024:
            raise ValueError("人物快照过大，请缩小资料范围。")
        data = json.loads(file.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("schema") != 1 or data.get("uid") != self.identity.uid:
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
        if sum(len(record["text"]) for record in records) > 5000000:
            raise ValueError("每个人物最多保留500万字，请缩小资料范围。")
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

    def load_messages(self, data):
        try:
            path = self.path / "conversation.json"
            if path.is_symlink() or path.stat().st_size > 2 * 1024 * 1024:
                return []
            saved = json.loads(path.read_text(encoding="utf-8"))
            if saved.get("signature") != history_signature(data):
                return []
            messages = saved["messages"]
            if not isinstance(messages, list) or len(messages) > 20:
                return []
            if any(not isinstance(item, dict) or item.get("role") not in {"user", "assistant"}
                   or not isinstance(item.get("content"), str) or len(item["content"]) > 12000 for item in messages):
                return []
            for item in messages:
                sources = item.get("sources", [])
                if (not isinstance(sources, list) or len(sources) > 8
                        or any(not isinstance(source, dict) or not isinstance(source.get("number"), int)
                               or not isinstance(source.get("text"), str) or not isinstance(source.get("date"), str)
                               for source in sources)):
                    return []
            return messages
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return []

    def save_messages(self, data, messages):
        """Use the same exclusive lock, without mutating the portrait snapshot."""
        self.path.mkdir(parents=True, exist_ok=True)
        lock = self.path / "operation.lock"
        try:
            descriptor = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise BusyError("人物正在更新，未覆盖本地对话；请稍后重试。") from None
        os.close(descriptor)
        temporary = None
        try:
            if history_signature(self.read()) != history_signature(data):
                raise ValueError("资料或画像已变化，未保存旧版本对话。")
            target = self.path / "conversation.json"
            if target.is_symlink():
                raise ValueError("对话文件不能是符号链接。")
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path,
                                             suffix=".tmp", delete=False) as handle:
                temporary = Path(handle.name)
                json.dump({"signature": history_signature(data), "messages": messages[-20:]}, handle, ensure_ascii=False)
            os.replace(str(temporary), str(target))
        finally:
            if temporary and temporary.exists():
                temporary.unlink()
            lock.unlink()


def history_signature(data):
    return [data["uid"], data["corpus_digest"], data.get("profile", {}).get("revision"),
            data.get("profile", {}).get("approved"), CHUNKER_VERSION]


def create_local(name, root=None):
    name = name.strip()
    if not name or len(name) > 80 or any(ord(char) < 32 for char in name):
        raise ValueError("请填写1–80字的人物名称，不含换行。")
    store = CreatorStore(parse_identity("local_" + uuid.uuid4().hex), root)
    data = store.read()
    data["name"] = name
    store.write(data)
    return store


def saved_creators(root=None):
    base = Path(root) if root is not None else DEFAULT_ROOT
    result = []
    for path in sorted(base.glob("*")):
        if not path.is_dir() or path.is_symlink():
            continue
        try:
            uid = path.name[len("bilibili_"):] if path.name.startswith("bilibili_") else path.name
            identity = parse_identity(uid)
            if identity.key != path.name:
                continue
            store = CreatorStore(identity, base)
            if not (path / "snapshot.json").is_file():
                continue
            data = store.read()
            result.append({"uid": identity.uid, "name": data["name"], "records": len(data["records"]),
                           "ready": store.profile_fresh(data) and data.get("profile", {}).get("approved") is True})
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return result
