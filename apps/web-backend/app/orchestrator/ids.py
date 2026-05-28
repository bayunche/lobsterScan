"""message_id 生成 + 校验 + 任务级去重表

v2 群聊协议（详 docs/开发文档.md §3.5）每条事件必须带稳定 message_id。

格式：`msg_<8 位小写 hex>`（决策见 specs/001-v2-chat-protocol-state/research.md §2）。
"""

from __future__ import annotations

import re
import secrets

__all__ = ["new_message_id", "is_message_id", "MessageIdRegistry"]


_MESSAGE_ID_RE = re.compile(r"^msg_[a-f0-9]{8}$")


def new_message_id() -> str:
    """生成新的 message_id。`msg_<8 位 hex>`。"""
    return f"msg_{secrets.token_hex(4)}"


def is_message_id(s: str) -> bool:
    """字符串是否符合 message_id 格式。"""
    return isinstance(s, str) and bool(_MESSAGE_ID_RE.match(s))


class MessageIdRegistry:
    """任务级 message_id 去重表。

    用法：
        reg = MessageIdRegistry()
        if not reg.add_or_reject("msg_abcd1234"):
            # 重复 → 拒写（emit_v2 应降级为 agent.failed，详 FR-005 / FR-020）
            ...
    """

    def __init__(self) -> None:
        self._seen: set[str] = set()

    def add_or_reject(self, message_id: str) -> bool:
        """返回 True 表示首次注册成功；False 表示重复。"""
        if message_id in self._seen:
            return False
        self._seen.add(message_id)
        return True

    def __len__(self) -> int:
        return len(self._seen)

    def __contains__(self, message_id: object) -> bool:
        return message_id in self._seen
