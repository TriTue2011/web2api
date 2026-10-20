"""Sổ dữ liệu chi tiêu — SQLite ở ``<WEB2API_DATA>/du_lieu/chi-tieu/chi_tieu.db`` (0600).

Port từ ``~/workspace/c2a-nv146/services/chi_tieu/kho.py`` @ d879b03 (bản gốc
``/opt/claude-c2a/chatgpt2api/services/chi_tieu/kho.py`` — đường /opt/claude-c2a không còn trong
môi trường này, nguồn đọc tại bản checkout d879b03 còn sạch). Chỉ đổi: đường lưu trữ theo
khuôn module web2api (``_thu_muc_du_lieu`` / ``WEB2API_DATA``), tệp 0600, thư mục 0700.

KHÔNG port: bảng ``hop_thu``/``thu_da_doc`` (thuộc email_chi.py — kéo kênh email, spec NV160
yêu cầu không port; phụ thuộc module `email` chưa gộp, điểm nối ghi ở README.md); các hàm
``lien_ket``/``ma_lien_ket`` (nối kênh chat Zalo/Telegram của c2a — web2api cách ly sổ theo
danh tính đăng nhập, không có cơ chế mã liên kết).
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from . import thoi_gian as _tg

_KHOA = threading.RLock()
_DUONG: Path | None = None

#: Trần số bản ghi khi đọc (chống trả quá nhiều dữ liệu một lần).
TRAN_BAN_GHI = 10_000

_LUOC_DO = """
CREATE TABLE IF NOT EXISTS so (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ten TEXT NOT NULL,
    chu TEXT,                         -- id danh tính web sở hữu; NULL = sổ dùng chung của nhà
    luong INTEGER NOT NULL DEFAULT 10000000,
    ngay_bat_dau INTEGER NOT NULL DEFAULT 1,
    nguong TEXT NOT NULL DEFAULT '[0.65, 0.8, 1.0]',
    tao_luc TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS so_chu ON so (chu) WHERE chu IS NOT NULL;

CREATE TABLE IF NOT EXISTS hu (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    so_id INTEGER NOT NULL REFERENCES so(id) ON DELETE CASCADE,
    ten TEXT NOT NULL,
    ty_le REAL NOT NULL DEFAULT 0,
    thu_tu INTEGER NOT NULL DEFAULT 0,       -- thứ tự hiển thị
    thu_tu_bu INTEGER NOT NULL DEFAULT 0,    -- hũ nhỏ hơn bị rút bù TRƯỚC khi hũ khác vượt
    thu_tu_dac_biet INTEGER NOT NULL DEFAULT 0,  -- >0: hứng chi phí đặc biệt, nhỏ hơn hứng trước
    an INTEGER NOT NULL DEFAULT 0            -- 1 = đã xoá mềm (còn khoản chi cũ trỏ tới)
);

CREATE TABLE IF NOT EXISTS chi_tieu (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    so_id INTEGER NOT NULL,
    hu_id INTEGER NOT NULL,
    so_tien INTEGER NOT NULL,
    ghi_chu TEXT NOT NULL DEFAULT '',
    nguon TEXT NOT NULL DEFAULT 'chat',
    thoi_gian TEXT NOT NULL,
    thang TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS chi_tieu_so_thang ON chi_tieu (so_id, thang);

CREATE TABLE IF NOT EXISTS thu_nhap_them (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    so_id INTEGER NOT NULL,
    mo_ta TEXT NOT NULL DEFAULT '',
    so_tien INTEGER NOT NULL,
    thoi_gian TEXT NOT NULL,
    thang TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chi_phi_dac_biet (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    so_id INTEGER NOT NULL,
    mo_ta TEXT NOT NULL,
    so_tien INTEGER NOT NULL,
    thoi_gian TEXT NOT NULL,
    thang TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cong_ty (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    so_id INTEGER NOT NULL,
    loai TEXT NOT NULL CHECK (loai IN ('tam_ung', 'chi')),
    so_tien INTEGER NOT NULL,
    mo_ta TEXT NOT NULL,
    thoi_gian TEXT NOT NULL,
    giai_chi_id INTEGER                      -- NULL = kỳ đang mở
);

CREATE TABLE IF NOT EXISTS giai_chi (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    so_id INTEGER NOT NULL,
    thoi_gian TEXT NOT NULL,
    tong_tam_ung INTEGER NOT NULL,
    tong_chi INTEGER NOT NULL,
    so_du INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS canh_bao_da_gui (
    so_id INTEGER NOT NULL,
    thang TEXT NOT NULL,
    khoa TEXT NOT NULL,                      -- 'hu:<id>' | '__tong__' | '__tong_vuot__'
    nguong REAL NOT NULL,
    thoi_gian TEXT NOT NULL,
    PRIMARY KEY (so_id, thang, khoa, nguong)
);
"""

#: 6 hũ của bản gốc (55/5/10/10/10/10) — chỉ là MẪU khi tạo sổ mới; người dùng sửa / thêm / xoá tự do.
#: thu_tu_bu theo bản gốc (THU_TU_BU): Hưởng thụ → Dự phòng → Học tập → Gia đình → Thiết yếu → Tự do tài chính.
#: Chi phí đặc biệt: Dự phòng rồi Hưởng thụ.
HU_MAU = [
    {"ten": "Thiết Yếu", "ty_le": 55, "thu_tu_bu": 5, "thu_tu_dac_biet": 0},
    {"ten": "Gia Đình & Cho Đi", "ty_le": 5, "thu_tu_bu": 4, "thu_tu_dac_biet": 0},
    {"ten": "Học Tập", "ty_le": 10, "thu_tu_bu": 3, "thu_tu_dac_biet": 0},
    {"ten": "Dự Phòng & Tiết Kiệm", "ty_le": 10, "thu_tu_bu": 2, "thu_tu_dac_biet": 1},
    {"ten": "Hưởng Thụ", "ty_le": 10, "thu_tu_bu": 1, "thu_tu_dac_biet": 2},
    {"ten": "Tự Do Tài Chính", "ty_le": 10, "thu_tu_bu": 6, "thu_tu_dac_biet": 0},
]


def _duong() -> Path:
    if _DUONG is not None:
        return _DUONG
    goc = os.environ.get("WEB2API_DATA", "/data")
    return Path(goc) / "du_lieu" / "chi-tieu" / "chi_tieu.db"


def dat_duong(p: Path) -> None:
    """Module gọi lúc nap() để trỏ về thư mục dữ liệu của mình."""
    global _DUONG
    _DUONG = Path(p)


_da_tao: set[str] = set()


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    with _KHOA:
        p = _duong()
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(p.parent, 0o700)
        except OSError:
            pass
        moi = not p.exists()
        conn = sqlite3.connect(p, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            if str(p) not in _da_tao:
                conn.executescript(_LUOC_DO)
                _da_tao.add(str(p))
            if moi:
                try:
                    os.chmod(p, 0o600)
                except OSError:
                    pass
            yield conn
            conn.commit()
        finally:
            conn.close()


def bay_gio() -> str:
    return _tg.bay_gio()


def _dict(r: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(r) if r is not None else None


# ── Sổ ──────────────────────────────────────────────────────────────────────
def tao_so(ten: str, chu: str | None) -> dict[str, Any]:
    with db() as c:
        cur = c.execute("INSERT INTO so (ten, chu, tao_luc) VALUES (?, ?, ?)", (ten, chu, bay_gio()))
        so_id = int(cur.lastrowid)
        for i, h in enumerate(HU_MAU):
            c.execute("INSERT INTO hu (so_id, ten, ty_le, thu_tu, thu_tu_bu, thu_tu_dac_biet) VALUES (?,?,?,?,?,?)",
                      (so_id, h["ten"], h["ty_le"], i + 1, h["thu_tu_bu"], h["thu_tu_dac_biet"]))
    return so(so_id)  # type: ignore[return-value]


def so(so_id: int) -> dict[str, Any] | None:
    with db() as c:
        r = _dict(c.execute("SELECT * FROM so WHERE id=?", (so_id,)).fetchone())
    if r:
        r["nguong"] = json.loads(r["nguong"] or "[]")
    return r


def so_cua_chu(chu: str) -> dict[str, Any] | None:
    with db() as c:
        r = c.execute("SELECT id FROM so WHERE chu=?", (chu,)).fetchone()
    return so(int(r["id"])) if r else None


def moi_so() -> list[dict[str, Any]]:
    """Mọi sổ — CHỈ tên + chủ + thời điểm tạo, không bao giờ kèm số tiền."""
    with db() as c:
        return [dict(r) for r in c.execute("SELECT id, ten, chu, tao_luc FROM so ORDER BY id LIMIT ?",
                                           (TRAN_BAN_GHI,))]


def sua_so(so_id: int, **truong: Any) -> None:
    cot = {k: v for k, v in truong.items() if k in ("ten", "luong", "ngay_bat_dau", "nguong")}
    if "nguong" in cot:
        cot["nguong"] = json.dumps(cot["nguong"])
    if not cot:
        return
    with db() as c:
        c.execute(f"UPDATE so SET {', '.join(f'{k}=?' for k in cot)} WHERE id=?", (*cot.values(), so_id))


def xoa_so(so_id: int) -> None:
    with db() as c:
        for bang in ("chi_tieu", "thu_nhap_them", "chi_phi_dac_biet", "cong_ty", "giai_chi", "canh_bao_da_gui",
                     "hu"):
            c.execute(f"DELETE FROM {bang} WHERE so_id=?", (so_id,))
        c.execute("DELETE FROM so WHERE id=?", (so_id,))


# ── Hũ ──────────────────────────────────────────────────────────────────────
def ds_hu(so_id: int, ca_an: bool = False) -> list[dict[str, Any]]:
    with db() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM hu WHERE so_id=?" + ("" if ca_an else " AND an=0") + " ORDER BY thu_tu, id LIMIT ?",
            (so_id, TRAN_BAN_GHI))]


def them_hu(so_id: int, ten: str, ty_le: float, thu_tu_bu: int, thu_tu_dac_biet: int) -> int:
    with db() as c:
        tt = int(c.execute("SELECT COALESCE(MAX(thu_tu), 0) FROM hu WHERE so_id=?", (so_id,)).fetchone()[0]) + 1
        cur = c.execute("INSERT INTO hu (so_id, ten, ty_le, thu_tu, thu_tu_bu, thu_tu_dac_biet) VALUES (?,?,?,?,?,?)",
                        (so_id, ten, ty_le, tt, thu_tu_bu, thu_tu_dac_biet))
        return int(cur.lastrowid)


def sua_hu(so_id: int, hu_id: int, **truong: Any) -> None:
    cot = {k: v for k, v in truong.items() if k in ("ten", "ty_le", "thu_tu", "thu_tu_bu", "thu_tu_dac_biet")}
    if cot:
        with db() as c:
            c.execute(f"UPDATE hu SET {', '.join(f'{k}=?' for k in cot)} WHERE id=? AND so_id=?",
                      (*cot.values(), hu_id, so_id))


def xoa_hu(so_id: int, hu_id: int) -> None:
    """Hũ còn khoản chi thì xoá MỀM (lịch sử cũ vẫn đọc được tên), không còn thì xoá hẳn."""
    with db() as c:
        co = c.execute("SELECT 1 FROM chi_tieu WHERE so_id=? AND hu_id=? LIMIT 1", (so_id, hu_id)).fetchone()
        if co:
            c.execute("UPDATE hu SET an=1, ty_le=0 WHERE id=? AND so_id=?", (hu_id, so_id))
        else:
            c.execute("DELETE FROM hu WHERE id=? AND so_id=?", (hu_id, so_id))


# ── Khoản chi ───────────────────────────────────────────────────────────────
def ghi_chi(so_id: int, hu_id: int, so_tien: int, ghi_chu: str, nguon: str, thang: str,
            thoi_gian: str | None = None) -> int:
    with db() as c:
        cur = c.execute("INSERT INTO chi_tieu (so_id, hu_id, so_tien, ghi_chu, nguon, thoi_gian, thang)"
                        " VALUES (?,?,?,?,?,?,?)", (so_id, hu_id, so_tien, ghi_chu, nguon, thoi_gian or bay_gio(), thang))
        return int(cur.lastrowid)


def chi_theo_id(so_id: int, id_: int) -> dict[str, Any] | None:
    with db() as c:
        return _dict(c.execute("SELECT * FROM chi_tieu WHERE id=? AND so_id=?", (id_, so_id)).fetchone())


def ds_chi(so_id: int, thang: str) -> list[dict[str, Any]]:
    with db() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM chi_tieu WHERE so_id=? AND thang=? ORDER BY thoi_gian DESC, id DESC LIMIT ?",
            (so_id, thang, TRAN_BAN_GHI))]


def tong_chi_theo_hu(so_id: int, thang: str) -> dict[int, int]:
    with db() as c:
        return {int(r[0]): int(r[1]) for r in c.execute(
            "SELECT hu_id, SUM(so_tien) FROM chi_tieu WHERE so_id=? AND thang=? GROUP BY hu_id", (so_id, thang))}


def trung_binh_chi_3_thang(so_id: int, thang: str) -> dict[int, float]:
    """Trung bình chi từng hũ của 3 kỳ TRƯỚC ``thang`` (kỳ không có khoản chi nào vẫn tính là 0)."""
    nam, th = int(thang[:4]), int(thang[5:7])
    cac: list[str] = []
    for _ in range(3):
        th -= 1
        if th == 0:
            nam, th = nam - 1, 12
        cac.append(f"{nam:04d}-{th:02d}")
    with db() as c:
        dong = c.execute(f"SELECT hu_id, SUM(so_tien) FROM chi_tieu WHERE so_id=? AND thang IN ({','.join('?' * 3)})"
                         " GROUP BY hu_id", (so_id, *cac)).fetchall()
    return {int(r[0]): int(r[1]) / 3 for r in dong}


def sua_chi(so_id: int, id_: int, **truong: Any) -> bool:
    cot = {k: v for k, v in truong.items() if k in ("hu_id", "so_tien", "ghi_chu", "thoi_gian", "thang")}
    if not cot:
        return False
    with db() as c:
        cur = c.execute(f"UPDATE chi_tieu SET {', '.join(f'{k}=?' for k in cot)} WHERE id=? AND so_id=?",
                        (*cot.values(), id_, so_id))
        return cur.rowcount > 0


def xoa_chi(so_id: int, id_: int) -> bool:
    with db() as c:
        return c.execute("DELETE FROM chi_tieu WHERE id=? AND so_id=?", (id_, so_id)).rowcount > 0


def tach_chi(so_id: int, id_: int, muc: list[dict[str, Any]]) -> list[int]:
    """Xoá dòng gốc, ghi các dòng mới GIỮ thời gian + kỳ của dòng gốc — một giao dịch."""
    with db() as c:
        goc = c.execute("SELECT * FROM chi_tieu WHERE id=? AND so_id=?", (id_, so_id)).fetchone()
        c.execute("DELETE FROM chi_tieu WHERE id=? AND so_id=?", (id_, so_id))
        ids = []
        for m in muc:
            cur = c.execute("INSERT INTO chi_tieu (so_id, hu_id, so_tien, ghi_chu, nguon, thoi_gian, thang)"
                            " VALUES (?,?,?,?,?,?,?)", (so_id, m["hu_id"], m["so_tien"], m.get("ghi_chu", ""),
                                                        goc["nguon"], goc["thoi_gian"], goc["thang"]))
            ids.append(int(cur.lastrowid))
        return ids


# ── Thu nhập thêm / chi phí đặc biệt (cùng khuôn: mô tả + số tiền + kỳ) ──────
_BANG_KY = ("thu_nhap_them", "chi_phi_dac_biet")


def ghi_ky(bang: str, so_id: int, mo_ta: str, so_tien: int, thang: str) -> int:
    assert bang in _BANG_KY
    with db() as c:
        cur = c.execute(f"INSERT INTO {bang} (so_id, mo_ta, so_tien, thoi_gian, thang) VALUES (?,?,?,?,?)",
                        (so_id, mo_ta, so_tien, bay_gio(), thang))
        return int(cur.lastrowid)


def ds_ky(bang: str, so_id: int, thang: str) -> list[dict[str, Any]]:
    assert bang in _BANG_KY
    with db() as c:
        return [dict(r) for r in c.execute(f"SELECT * FROM {bang} WHERE so_id=? AND thang=? ORDER BY id LIMIT ?",
                                           (so_id, thang, TRAN_BAN_GHI))]


def tong_ky(bang: str, so_id: int, thang: str) -> int:
    assert bang in _BANG_KY
    with db() as c:
        return int(c.execute(f"SELECT COALESCE(SUM(so_tien), 0) FROM {bang} WHERE so_id=? AND thang=?",
                             (so_id, thang)).fetchone()[0])


def sua_ky(bang: str, so_id: int, id_: int, **truong: Any) -> bool:
    assert bang in _BANG_KY
    cot = {k: v for k, v in truong.items() if k in ("mo_ta", "so_tien", "thang")}
    if not cot:
        return False
    with db() as c:
        return c.execute(f"UPDATE {bang} SET {', '.join(f'{k}=?' for k in cot)} WHERE id=? AND so_id=?",
                         (*cot.values(), id_, so_id)).rowcount > 0


def xoa_ky(bang: str, so_id: int, id_: int) -> bool:
    assert bang in _BANG_KY
    with db() as c:
        return c.execute(f"DELETE FROM {bang} WHERE id=? AND so_id=?", (id_, so_id)).rowcount > 0


# ── Tạm ứng công ty ─────────────────────────────────────────────────────────
def ghi_cong_ty(so_id: int, loai: str, so_tien: int, mo_ta: str) -> int:
    with db() as c:
        cur = c.execute("INSERT INTO cong_ty (so_id, loai, so_tien, mo_ta, thoi_gian) VALUES (?,?,?,?,?)",
                        (so_id, loai, so_tien, mo_ta, bay_gio()))
        return int(cur.lastrowid)


def ds_cong_ty(so_id: int, giai_chi_id: int | None = None) -> list[dict[str, Any]]:
    with db() as c:
        if giai_chi_id is None:
            q = c.execute("SELECT * FROM cong_ty WHERE so_id=? AND giai_chi_id IS NULL ORDER BY id LIMIT ?",
                          (so_id, TRAN_BAN_GHI))
        else:
            q = c.execute("SELECT * FROM cong_ty WHERE so_id=? AND giai_chi_id=? ORDER BY id LIMIT ?",
                          (so_id, giai_chi_id, TRAN_BAN_GHI))
        return [dict(r) for r in q]


def sua_cong_ty(so_id: int, id_: int, **truong: Any) -> bool:
    cot = {k: v for k, v in truong.items() if k in ("loai", "so_tien", "mo_ta")}
    if not cot:
        return False
    with db() as c:
        return c.execute(f"UPDATE cong_ty SET {', '.join(f'{k}=?' for k in cot)} WHERE id=? AND so_id=?"
                         " AND giai_chi_id IS NULL", (*cot.values(), id_, so_id)).rowcount > 0


def xoa_cong_ty(so_id: int, id_: int) -> bool:
    with db() as c:
        return c.execute("DELETE FROM cong_ty WHERE id=? AND so_id=? AND giai_chi_id IS NULL",
                         (id_, so_id)).rowcount > 0


def giai_chi_ky(so_id: int) -> dict[str, Any] | None:
    with db() as c:
        dong = c.execute("SELECT loai, so_tien FROM cong_ty WHERE so_id=? AND giai_chi_id IS NULL", (so_id,)).fetchall()
        if not dong:
            return None
        tu = sum(r["so_tien"] for r in dong if r["loai"] == "tam_ung")
        chi = sum(r["so_tien"] for r in dong if r["loai"] == "chi")
        cur = c.execute("INSERT INTO giai_chi (so_id, thoi_gian, tong_tam_ung, tong_chi, so_du) VALUES (?,?,?,?,?)",
                        (so_id, bay_gio(), tu, chi, tu - chi))
        gid = int(cur.lastrowid)
        c.execute("UPDATE cong_ty SET giai_chi_id=? WHERE so_id=? AND giai_chi_id IS NULL", (gid, so_id))
    return {"id": gid, "tong_tam_ung": tu, "tong_chi": chi, "so_du": tu - chi}


def ds_giai_chi(so_id: int) -> list[dict[str, Any]]:
    with db() as c:
        return [dict(r) for r in c.execute("SELECT * FROM giai_chi WHERE so_id=? ORDER BY id DESC LIMIT ?",
                                           (so_id, TRAN_BAN_GHI))]


def giai_chi(so_id: int, gid: int) -> dict[str, Any] | None:
    with db() as c:
        return _dict(c.execute("SELECT * FROM giai_chi WHERE id=? AND so_id=?", (gid, so_id)).fetchone())


# ── Cảnh báo đã gửi ─────────────────────────────────────────────────────────
def da_gui(so_id: int, thang: str, khoa: str, nguong: float) -> bool:
    with db() as c:
        return c.execute("SELECT 1 FROM canh_bao_da_gui WHERE so_id=? AND thang=? AND khoa=? AND nguong=?",
                         (so_id, thang, khoa, nguong)).fetchone() is not None


def danh_dau_da_gui(so_id: int, thang: str, khoa: str, nguong: float) -> None:
    with db() as c:
        c.execute("INSERT OR IGNORE INTO canh_bao_da_gui VALUES (?,?,?,?,?)", (so_id, thang, khoa, nguong, bay_gio()))


def _reset_for_tests(duong: Path) -> None:
    global _DUONG
    _DUONG = duong
    _da_tao.discard(str(duong))
