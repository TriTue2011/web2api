"""Chat over the direct protocol: send via /chat/stream, poll /chat/history.

The /chat/stream call itself is fire-and-forget (its response stream only
carries the send echo). Genuine assistant replies are picked up by polling
chat.history for new message.assistant events — the same approach as
muse-cli's interactive chat.
"""

from __future__ import annotations

import time
from typing import Iterator


def event_text(ev: dict) -> str:
    p = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
    return p.get("display_text") or p.get("content") or ""


def is_genuine_reply(ev: dict, baseline_seq: int) -> bool:
    """True for a real assistant reply (not a proactive background push)."""
    if ev.get("event_name") != "message.assistant":
        return False
    if (ev.get("seq") or 0) <= baseline_seq:
        return False
    p = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
    # Background pushes (Telegram drafts, task updates) reference themselves.
    if ev.get("reply_to_message_id") or p.get("reply_to_message_id"):
        return False
    return bool(event_text(ev))


def build_prompt(messages: list[dict]) -> str:
    """OpenAI messages array -> one text prompt for the agent.

    Muse is an agent with its own memory; we pass the conversation as a
    labeled transcript so nothing is lost.
    """
    parts: list[str] = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if isinstance(content, list):  # multi-part content
            texts = [p.get("text", "") for p in content
                     if isinstance(p, dict) and p.get("type") == "text"]
            content = "\n".join(texts)
        content = str(content or "").strip()
        if not content:
            continue
        label = {"system": "System", "user": "User",
                 "assistant": "Assistant"}.get(role, role.capitalize())
        parts.append(f"{label}: {content}")
    return "\n\n".join(parts)


class ChatSession:
    """One prompt/response turn on a Gateway. Blocking — run in a thread."""

    def __init__(self, gw, node_id: str, poll_interval: float = 2.0,
                 wait_s: float = 300.0, quiet_s: float = 10.0):
        self.gw = gw
        self.node_id = node_id
        self.poll_interval = poll_interval
        self.wait_s = wait_s
        self.quiet_s = quiet_s

    def _last_seq(self) -> int:
        try:
            d = self.gw.call_json("chat.history", body={"limit": 1}, timeout=30)
        except Exception:
            return 0
        evs = d.get("chat_events", []) if isinstance(d, dict) else []
        return max([e.get("seq", 0) for e in evs] or [0])

    def _poll_known(self, baseline: int, known: dict) -> bool:
        """Refresh per-message texts. Returns True if anything changed.

        known: key -> (seq, text). Messages keep their seq while their text
        grows across polls, so we track by message key, not by seq cursor.
        """
        try:
            d = self.gw.call_json("chat.history", body={"limit": 15}, timeout=30)
        except Exception:
            return False
        evs = d.get("chat_events", []) if isinstance(d, dict) else []
        changed = False
        for e in evs:
            if not is_genuine_reply(e, baseline):
                continue
            p = e.get("payload") if isinstance(e.get("payload"), dict) else {}
            key = p.get("message_id") or e.get("message_id") or e.get("seq")
            item = (e.get("seq", 0), event_text(e))
            if known.get(key) != item:
                known[key] = item
                changed = True
        return changed

    def run(self, prompt: str) -> Iterator[str]:
        """Yield text deltas as the assistant reply arrives."""
        baseline = self._last_seq()
        body = {"items": [{"type": "text", "text": prompt}],
                "node_id": self.node_id, "capabilities": {}}
        self.gw._open("chat.stream", body=body)  # fire-and-forget
        known: dict = {}
        emitted, last_new = "", 0.0
        deadline = time.time() + self.wait_s
        got_any = False
        while time.time() < deadline:
            time.sleep(self.poll_interval)
            if self._poll_known(baseline, known):
                full = "".join(t for _, t in sorted(known.values()))
                # Emit only the unseen tail (messages grow across polls).
                delta = full[len(emitted):] if full.startswith(emitted) else full
                if delta:
                    emitted = full
                    got_any = True
                    last_new = time.time()
                    yield delta
            elif got_any and time.time() - last_new >= self.quiet_s:
                break
        if not got_any:
            raise TimeoutError(
                f"no assistant reply within {self.wait_s:.0f}s "
                "(check /history later — the agent may still be working)")

    def run_collect(self, prompt: str) -> str:
        return "".join(self.run(prompt))
