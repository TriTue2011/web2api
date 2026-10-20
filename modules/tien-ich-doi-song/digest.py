"""Bản tin tóm tắt (digest) — máy trạng thái gom mục → gửi theo giờ.

Port từ kho CỦA CHÍNH CHỦ MÁY (LUẬT PORT 19/10, chỉ chép không viết lại):
  nguồn: /opt/claude-c2a/chatgpt2api/services/digest.py
  commit: d879b03 (cây CHỈ ĐỌC, không sửa c2a)

Chỉnh so với c2a (chỉ đổi lớp kết nối/khuôn module, KHÔNG đổi logic):
  - `from services.config import DATA_DIR` → `dat_du_lieu()`/`dat_duong_du_lieu()`:
    module web2api truyền thư mục dữ liệu của mình (WEB2API_DATA/du_lieu/<id>,
    0700; tệp trạng thái 0600 vì chứa nội dung tin cá nhân).
  - KHÔNG port: `send_target/send_targets/send_file_target` (gửi tới kênh
    Zalo/Telegram của c2a), `_zalop_thread_type`, `parse_target`,
    `_luu_tep_phuc_vu`, `_all_sources` (tự đọc tài khoản email/lịch của c2a),
    `start/stop/_loop` (vòng lặp nền của c2a) — lớp GỬI ở web2api do tác nhân
    (tro-ly) hoặc người gọi công cụ đảm nhận; `notify()/flush()/tick()` trả về
    DỮ LIỆU (văn bản + danh sách đích) thay vì tự gửi.
  - `summarize(text, what, max_chars, goi_model=None)`: c2a gọi model 'burst'
    qua services.agent (c2a-nội bộ). Port giữ nguyên prompt và đường lùi cắt
    gọn; `goi_model` do người gọi truyền vào (Callable[[model, messages], str]).
    Không truyền → dùng đường lùi cắt gọn xác định (không bịa tóm tắt).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger("web2api.tien_ich_doi_song.digest")

try:
    from zoneinfo import ZoneInfo
    _TZ = ZoneInfo("Asia/Ho_Chi_Minh")
except Exception:  # pragma: no cover - môi trường thiếu tzdata
    _TZ = timezone(timedelta(hours=7))

_DUONG_DU_LIEU: Path | None = None
_PATH: Path | None = None
_lock = threading.RLock()
_state: dict[str, dict[str, Any]] = {}
_loaded = False

_SEEN_CAP = 400      # UID giữ lại mỗi nguồn (đủ chống trùng, không phình file)
_PENDING_CAP = 60    # mục chờ tối đa mỗi nguồn
_MAX_MSG = 3500      # giữ như c2a (giới hạn kênh nhắn tin)
_TRAN_MUC = 2000     # trần ký tự một mục trong bộ đệm


def dat_duong_du_lieu(duong: str | Path) -> Path:
    """Module gọi một lần lúc nạp: thư mục chứa tệp trạng thái (sẽ mkdir 0700)."""
    global _DUONG_DU_LIEU, _PATH, _loaded, _state
    _DUONG_DU_LIEU = Path(duong)
    _DUONG_DU_LIEU.mkdir(parents=True, exist_ok=True)
    try:
        _DUONG_DU_LIEU.chmod(0o700)
    except OSError:
        pass
    _PATH = _DUONG_DU_LIEU / "digest_state.json"
    with _lock:
        _state = {}
        _loaded = False
    return _DUONG_DU_LIEU


def _duong_tep() -> Path:
    if _PATH is None:
        raise RuntimeError("digest: chưa đặt thư mục dữ liệu (dat_duong_du_lieu)")
    return _PATH


# ── State ────────────────────────────────────────────────────────────────────
def _ensure() -> None:
    global _loaded, _state
    if _loaded:
        return
    with _lock:
        if _loaded:
            return
        try:
            p = _duong_tep()
            if p.is_file():
                raw = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    _state = {str(k): v for k, v in raw.items() if isinstance(v, dict)}
        except Exception:
            _state = {}
        _loaded = True


def _save() -> None:
    try:
        p = _duong_tep()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(_state, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.chmod(0o600)
        tmp.replace(p)
    except Exception as exc:
        log.warning("digest: save state lỗi: %s", str(exc)[:160])


def _rec(source: str) -> dict[str, Any]:
    _ensure()
    r = _state.get(source)
    if not isinstance(r, dict):
        r = {"seen": [], "pending": [], "fired": {}}
        _state[source] = r
    r.setdefault("seen", [])
    r.setdefault("pending", [])
    r.setdefault("fired", {})
    return r


def seen(source: str, uid: str) -> bool:
    """UID này đã xử lý chưa (chống gửi trùng khi quét lại)."""
    uid = str(uid or "").strip()
    if not uid:
        return False
    with _lock:
        return uid in (_rec(source).get("seen") or [])


def mark_seen(source: str, uid: str) -> None:
    uid = str(uid or "").strip()
    if not uid:
        return
    with _lock:
        r = _rec(source)
        lst = [x for x in (r.get("seen") or []) if x != uid]
        lst.append(uid)
        r["seen"] = lst[-_SEEN_CAP:]
        _save()


def queue(source: str, text: str) -> None:
    """Gom một mục vào bộ đệm chờ tới giờ định kỳ."""
    text = str(text or "").strip()
    if not text:
        return
    with _lock:
        r = _rec(source)
        pend = list(r.get("pending") or [])
        pend.append({"ts": time.time(), "text": text[:_TRAN_MUC]})
        r["pending"] = pend[-_PENDING_CAP:]
        _save()


def pending_count(source: str) -> int:
    with _lock:
        return len(_rec(source).get("pending") or [])


def _take_pending(source: str) -> list[dict[str, Any]]:
    with _lock:
        r = _rec(source)
        pend = list(r.get("pending") or [])
        r["pending"] = []
        _save()
        return pend


def forget(source: str) -> None:
    """Xoá state của một nguồn (khi người dùng xoá nguồn)."""
    with _lock:
        _ensure()
        if source in _state:
            _state.pop(source, None)
            _save()


# ── Tóm tắt ──────────────────────────────────────────────────────────────────
def summarize(text: str, *, what: str = "email", max_chars: int = 900,
              goi_model: Callable[[str, list[dict[str, str]]], str] | None = None) -> str:
    """Tóm tắt văn bản. `goi_model(model, messages) -> str` do người gọi truyền;
    không có thì dùng bản cắt gọn xác định (không bịa tóm tắt, không trả rỗng).
    Prompt giữ nguyên của c2a (d879b03)."""
    raw = str(text or "").strip()
    if not raw:
        return ""
    if len(raw) <= 240:
        return raw
    if goi_model is not None:
        try:
            out = goi_model(
                "burst",
                [
                    {"role": "system", "content":
                        "Bạn tóm tắt tiếng Việt, ngắn gọn, đúng sự thật. Không thêm "
                        "thông tin không có trong nội dung. Trả tối đa 6 gạch đầu dòng."},
                    {"role": "user", "content":
                        f"Tóm tắt {what} sau (nêu ai gửi/việc gì/mốc thời gian/việc cần làm):"
                        f"\n\n{raw[:12000]}"},
                ],
            )
            s = str(out or "").strip()
            if s:
                return s[:max_chars]
        except Exception as exc:
            log.warning("digest: tóm tắt lỗi (dùng bản cắt gọn): %s", str(exc)[:160])
    return raw[:max_chars] + ("…" if len(raw) > max_chars else "")


# ── Thông báo (trả dữ liệu; người gọi tự gửi) ────────────────────────────────
def notify(source: str, cfg: dict[str, Any], text: str) -> dict[str, Any]:
    """Xử lý một mục mới theo cấu hình thông báo của nguồn.

    - ``notify_on_new`` → văn bản GỬI NGAY trả về ở `gui_ngay` (người gọi tự gửi
      tới các `dich` vì module không giữ kênh gửi).
    - ``notify_times``  → xếp vào bộ đệm, `kiem_tra_moc_gio()` gửi khi tới giờ.
    Trả {gui_ngay: [van_ban], dich: [...], da_xep_cho: bool}."""
    dich = [str(t) for t in (cfg.get("notify_targets") or []) if str(t).strip()]
    on_new = bool(cfg.get("notify_on_new"))
    times = [t for t in (cfg.get("notify_times") or []) if str(t).strip()]
    out: dict[str, Any] = {"gui_ngay": [], "dich": dich, "da_xep_cho": False}
    body = str(text or "").strip()
    if not body:
        return out
    if on_new and dich:
        out["gui_ngay"] = [body]
    if times:
        queue(source, body)
        out["da_xep_cho"] = True
    return out


def _hm_now() -> str:
    return datetime.now(_TZ).strftime("%H:%M")


def _today() -> str:
    return datetime.now(_TZ).strftime("%Y-%m-%d")


def _norm_hm(v: Any) -> str:
    """'7:5' / '07:05' / '0705' → '07:05'; không hợp lệ → ''."""
    s = str(v or "").strip()
    if not s:
        return ""
    if ":" not in s and s.isdigit() and len(s) in (3, 4):
        s = s[:-2].rjust(2, "0") + ":" + s[-2:]
    parts = s.split(":")
    if len(parts) != 2:
        return ""
    try:
        h, m = int(parts[0]), int(parts[1])
    except ValueError:
        return ""
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return ""
    return f"{h:02d}:{m:02d}"


def flush(source: str, cfg: dict[str, Any] | None = None, *, title: str = "") -> dict[str, Any]:
    """Gộp bộ đệm của nguồn thành MỘT bản tổng hợp, trả dữ liệu cho người gọi gửi.
    Bộ đệm rỗng → {so_muc: 0, van_ban: ""} (không spam 'không có gì mới')."""
    pend = _take_pending(source)
    if not pend:
        return {"nguon": source, "so_muc": 0, "van_ban": "", "muc": []}
    head = (title or f"📬 Tổng hợp ({len(pend)} mục)").strip()
    muc = []
    for item in pend:
        when = datetime.fromtimestamp(float(item.get("ts") or 0), _TZ).strftime("%H:%M")
        muc.append({"luc": when, "noi_dung": str(item.get("text") or "").strip()})
    dong = [f"{head} — {len(pend)} mục", ""]
    for i, m_ in enumerate(muc, 1):
        dong.append(f"{i}. [{m_['luc']}] {m_['noi_dung']}")
    return {"nguon": source, "so_muc": len(pend), "van_ban": "\n".join(dong), "muc": muc}


def tick(sources: list[tuple[str, dict[str, Any], str]]) -> list[dict[str, Any]]:
    """Kiểm tra mốc giờ định kỳ của các nguồn, trả các bản tổng hợp đã tới giờ.

    `sources` = [(source_key, cfg, title)] — người gọi liệt kê rõ nguồn
    (c2a tự đọc email/lịch; ở web2api lớp đọc nguồn nằm ngoài module).
    Mỗi mốc giờ chỉ bắn 1 lần/ngày (state 'fired'). Trả [{nguon, tieu_de,
    so_muc, van_ban, dich}]."""
    now_hm = _hm_now()
    today = _today()
    da_gui: list[dict[str, Any]] = []
    for source, cfg, title in sources:
        times = {_norm_hm(t) for t in (cfg.get("notify_times") or [])}
        times.discard("")
        if now_hm not in times:
            continue
        with _lock:
            r = _rec(source)
            if str((r.get("fired") or {}).get(now_hm) or "") == today:
                continue  # mốc này hôm nay đã bắn
            r.setdefault("fired", {})[now_hm] = today
            _save()
        try:
            ban = flush(source, title=title)
            if ban["so_muc"]:
                ban["tieu_de"] = title
                ban["dich"] = [str(t) for t in (cfg.get("notify_targets") or []) if str(t).strip()]
                da_gui.append(ban)
        except Exception as exc:
            log.warning("digest: flush %s lỗi: %s", source, str(exc)[:160])
    return da_gui


def _reset_for_tests(path: Path | None = None) -> None:
    global _PATH, _state, _loaded
    with _lock:
        if path is not None:
            _PATH = path
        _state = {}
        _loaded = False
