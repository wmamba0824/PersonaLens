"""Accept exact Bilibili homepages or UIDs, not arbitrary fetch URLs."""

from dataclasses import dataclass
import re
from urllib.parse import urlsplit


@dataclass(frozen=True)
class CreatorIdentity:
    uid: str

    @property
    def key(self):
        return "bilibili_" + self.uid

    @property
    def url(self):
        return "https://space.bilibili.com/" + self.uid


def parse_creator(value):
    value = str(value).strip()
    if re.fullmatch(r"[1-9][0-9]{0,18}", value):
        return CreatorIdentity(value)
    if value.startswith("space.bilibili.com/"):
        value = "https://" + value
    parsed = urlsplit(value)
    if (parsed.scheme not in ("http", "https") or parsed.netloc != "space.bilibili.com"
            or parsed.username or parsed.password):
        raise ValueError("请输入 B站主页 https://space.bilibili.com/数字UID 或数字UID，不支持短链接。")
    match = re.fullmatch(r"/([1-9][0-9]{0,18})(?:/(?:dynamic|video))?/?", parsed.path)
    if not match:
        raise ValueError("链接必须是B站个人主页，不是单个视频、专栏或其他网站。")
    return CreatorIdentity(match[1])
