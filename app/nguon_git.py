"""Nguồn module là GIT của dự án: đẩy module lên git → web2api tự thấy, báo «có module mới», cài khi được lệnh.

Không cần dựng lại ảnh, không khởi động lại: mã tải về <WEB2API_DATA>/modules/<id>/ rồi nạp nóng.

Đường đi (chỉ API GitHub, host CỐ ĐỊNH — người dùng không đưa URL tuỳ ý vào được, nên không có SSRF):
  1. Mỗi `chu_ky_giay`: GET /repos/{repo}/commits/{nhanh} kèm If-None-Match (ETag). Không đổi → 304:
     một vòng thăm dò bình thường tốn ĐÚNG 1 yêu cầu. Tài liệu GitHub nói 304 có điều kiện không trừ
     hạn mức — CHƯA kiểm điều đó cho truy cập ẩn danh; dù có bị trừ, chu kỳ 300 s chỉ tốn 12 yêu cầu/giờ.
  2. Commit đổi → GET /git/trees/{cây}?recursive=1, mỗi thư mục con của `thu_muc` có manifest.json là
     một module; manifest đọc qua /git/blobs (chỉ cho module có sha thư mục ĐỔI so với lần trước).
  3. Cài: chốt theo ĐÚNG cây của commit đã thấy (không lấy «mới nhất» ngầm), tải từng blob, KIỂM
     BĂM git (sha1 của "blob <len>\\0"+nội dung) khớp sha trong cây — lệch một tệp là từ chối cả module.

Kho đang PUBLIC (chủ máy mở 10/10/2026) → đọc ẨN DANH, không gửi Authorization khi không có token.
Ẩn danh chỉ 60 yêu cầu/giờ/IP: đọc x-ratelimit-remaining/x-ratelimit-reset mỗi phản hồi; còn <10 thì
giãn nhịp tới giờ reset (giữ nguyên ETag), hết hạn mức (403/429) thì chờ tới reset — không đốt vòng lặp.

Rủi ro (nói rõ trong README): mã module chạy TRONG tiến trình lõi — ai đẩy được lên nhánh đó là chạy
được mã trong lõi. Tự cài mặc định TẮT; bật `yeu_cau_chu_ky` để chỉ tự cài commit đã xác minh chữ ký.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import logging
import os
import posixpath
import re
import secrets
import shutil
import tarfile
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urljoin, urlsplit

import httpx

from app import kho, nhat_ky
from app.chuyen_tiep import TransportGhimIp, iso
from app.kham_pha import ID_HOP_LE, doc_manifest, kiem_manifest
from app.so_module import LoiCanDuyetQuyen, LoiSo, SoModule
from app.tu_xa import _dia_chi_bi_chan

log = logging.getLogger("web2api.nguon_git")

API_MAC_DINH = "https://api.github.com"
REPO_HOP_LE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
DUONG_HOP_LE = re.compile(r"^[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*\Z")
SHA_HOP_LE = re.compile(r"^[0-9a-f]{40}\Z")
#: Thư mục chứa module trong kho git của dự án (TriTue2011/web2api: modules/<id>/ ở gốc kho).
THU_MUC_MAC_DINH = "modules"
TRAN_TEP = 200
TRAN_TONG = 50 * 1024 * 1024
TRAN_BLOB = 5 * 1024 * 1024
HAN_MUC_THAP = 10
TEN_BI_MAT = "_nguon_git"
TEP_CAU_HINH = "nguon_git.json"
TEP_TRANG_THAI = "nguon_git_trang_thai.json"
#: NV59: nguồn git phụ (id -> cấu hình), tách khỏi nguon_git.json để định dạng cũ một-repo vẫn đọc được.
TEP_NGUON_PHU = "nguon_git_nguon.json"
#: NV59: trạng thái đồng bộ của từng nguồn phụ (id -> trạng thái).
TEP_TT_NGUON = "nguon_git_trang_thai_nguon.json"
#: NV59: id nguồn phụ — chữ thường, ngắn, an toàn làm tên tệp/khoá bí mật.
ID_NGUON_HOP_LE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}\Z")
#: NV59: id dành riêng cho nguồn cấu hình cũ một-repo.
ID_NGUON_CHINH = "chinh"


def api_github() -> str:
    return os.environ.get("WEB2API_GITHUB_API", API_MAC_DINH).rstrip("/")


def bam_blob(du_lieu: bytes) -> str:
    """Mã đối tượng git của một tệp. SHA-1 vì đó là ĐỊNH DẠNG của git/GitHub (sha trong cây là SHA-1),
    không phải ta chọn: phải băm đúng thuật toán đó mới so được với sha trong cây."""
    return hashlib.sha1(b"blob %d\0" % len(du_lieu) + du_lieu).hexdigest()  # nosec B324 — định dạng git


#: Kỳ hạn quyền duyệt hạng B sau khi duyệt (ngày) — NV50 CG3. Hết hạn → bản mới
#: lại phải qua cổng duyệt (mọi cập nhật A→B trên main đều đã phải duyệt).
KY_HAN_DUYET_HANG_B_NGAY = 30


class LoiGit(Exception):
    def __init__(self, thong_diep: str, ma: int = 502, cho_den: float | None = None):
        super().__init__(thong_diep)
        self.ma = ma
        self.cho_den = cho_den  # thời điểm (epoch) nên thử lại — khi GitHub báo hết hạn mức


def _duong_an_toan(ten: str, ten_tham_so: str) -> str:
    if not isinstance(ten, str) or len(ten) > 200 or not DUONG_HOP_LE.match(ten) or any(
            p in (".", "..") for p in ten.split("/")):
        raise ValueError(f"{ten_tham_so} chỉ gồm chữ, số, . _ - và «/» giữa các phần (không «..»)")
    return ten


def kiem_repo(repo) -> str:
    # «..» làm chủ/kho thì /repos/../.. trỏ sang đường KHÁC của API GitHub — và mang theo token.
    if not isinstance(repo, str) or not REPO_HOP_LE.match(repo) or any(p in (".", "..") for p in repo.split("/")):
        raise ValueError("repo phải dạng chủ/kho, ví dụ TriTue2011/web2api")
    return repo


def kiem_id_nguon(id_: object) -> str:
    """NV59: kiểm id nguồn git phụ. Sai → ValueError (không chép giá trị khoá)."""
    if not isinstance(id_, str) or not ID_NGUON_HOP_LE.match(id_):
        raise ValueError("id nguồn chỉ gồm chữ thường, số, «-», «_», dài 1..32, bắt đầu bằng chữ/số")
    if id_ == ID_NGUON_CHINH:
        raise ValueError(f"id {ID_NGUON_CHINH!r} dành riêng cho nguồn cấu hình cũ một-repo")
    return id_


def kiem_nguon(than: dict) -> dict:
    """NV59: kiểm THUẦN (không ghi) cấu hình MỘT nguồn phụ — id + cùng bộ kiểm repo/SSRF của nguồn cũ.
    `tu_dong` mặc định TẮT cho nguồn mới (hợp đồng 4). Sai → ValueError."""
    if not isinstance(than, dict):
        raise ValueError("cấu hình nguồn git phải là đối tượng JSON")
    id_ = kiem_id_nguon(than.get("id"))
    cfg = kiem_cau_hinh({k: v for k, v in than.items() if k != "id"},
                        {"tu_dong": False, "yeu_cau_chu_ky": False})
    return {"id": id_, **cfg}


def kiem_cau_hinh(than: dict, cu: dict) -> dict:
    """Kiểm THUẦN (không ghi) cấu hình nguồn git — MỘT bộ kiểm cho PUT /api/nguon-git, cho lượt đọc nguon_git.json và
    cho khôi phục. Trường vắng trong `than` lấy từ `cu`, rồi mặc định. Sai → ValueError (không chép giá trị khoá)."""
    if not isinstance(than, dict):
        raise ValueError("cấu hình nguồn git phải là đối tượng JSON")
    repo = kiem_repo(than.get("repo", cu.get("repo")))
    nhanh = _duong_an_toan(than.get("nhanh", cu.get("nhanh", "main")), "nhanh")
    thu_muc = _duong_an_toan(than.get("thu_muc", cu.get("thu_muc", THU_MUC_MAC_DINH)), "thu_muc")
    chu_ky = than.get("chu_ky_giay", cu.get("chu_ky_giay", 300))
    if not isinstance(chu_ky, int) or isinstance(chu_ky, bool) or not 1 <= chu_ky <= 86400:
        raise ValueError("chu_ky_giay phải là số nguyên 1..86400")
    co = {}
    for k in ("tu_dong", "yeu_cau_chu_ky"):
        v = than.get(k, cu.get(k, False))
        if not isinstance(v, bool):
            raise ValueError(f"{k} phải là true/false")
        co[k] = v
    return {"repo": repo, "nhanh": nhanh, "thu_muc": thu_muc, "chu_ky_giay": chu_ky, **co}


def _doc_nguon_phu() -> list[dict]:
    """NV59: đọc danh sách nguồn git phụ từ nguon_git_nguon.json (qua CÙNG bộ kiểm `kiem_nguon`).
    Tệp hỏng/sai → coi như rỗng (không chặn nguồn chính)."""
    p = kho.thu_muc_du_lieu() / TEP_NGUON_PHU
    try:
        du = kho.doc_json(p)
    except kho.KhoHong as e:
        log.warning("%s — coi như chưa có nguồn phụ", e)
        return []
    ds = (du or {}).get("nguon") if isinstance(du, dict) else None
    if not isinstance(ds, list):
        return []
    ra = []
    thay = {n.get("id"): n for n in ds if isinstance(n, dict)}
    for id_, n in thay.items():
        try:
            ra.append(kiem_nguon(n))
        except ValueError as e:
            log.warning("Nguồn git phụ %r sai (%s) — bỏ qua", id_, e)
    return ra


def _ghi_nguon_phu(ds: list[dict]) -> None:
    """NV59: ghi danh sách nguồn phụ (đã kiểm)."""
    kho.ghi_json_cong_khai(TEP_NGUON_PHU, {"nguon": ds})


def doc_danh_sach_nguon() -> list[dict] | None:
    """NV59: toàn bộ nguồn git đã cấu hình — nguồn «chinh» (cấu hình cũ một-repo, nếu có) rồi các nguồn phụ.
    None = chưa cấu hình gì."""
    ds = []
    cfg = doc_cau_hinh()
    if cfg is not None:
        ds.append({"id": ID_NGUON_CHINH, **cfg})
    ds.extend(_doc_nguon_phu())
    return ds or None


def doc_cau_hinh() -> dict | None:
    """Cấu hình nguồn git đang hiệu lực: nguon_git.json (qua CÙNG bộ kiểm `kiem_cau_hinh` — tệp sửa tay sai hay
    «repo» «../..» không được dùng) hoặc biến môi trường. None = chưa cấu hình."""
    p = kho.thu_muc_du_lieu() / TEP_CAU_HINH
    try:
        cfg = kho.doc_json(p)
    except kho.KhoHong as e:
        log.warning("%s — coi như chưa cấu hình nguồn git", e)
        return None
    if isinstance(cfg, dict) and cfg.get("repo"):
        try:
            return kiem_cau_hinh(cfg, {})
        except ValueError as e:
            log.warning("%s sai (%s) — coi như chưa cấu hình nguồn git", p, e)
            return None
    repo = os.environ.get("WEB2API_GIT_REPO", "").strip()
    if not repo:
        return None
    try:
        return {"repo": kiem_repo(repo),
                "nhanh": _duong_an_toan(os.environ.get("WEB2API_GIT_NHANH") or "main", "WEB2API_GIT_NHANH"),
                "thu_muc": _duong_an_toan(os.environ.get("WEB2API_GIT_THU_MUC") or THU_MUC_MAC_DINH,
                                          "WEB2API_GIT_THU_MUC"),
                "chu_ky_giay": 300, "tu_dong": False, "yeu_cau_chu_ky": False, "nguon": "bien_moi_truong"}
    except ValueError as e:
        log.warning("Biến môi trường nguồn git sai, bỏ qua: %s", e)
        return None


class NguonGit:
    #: Nhân thời gian chờ của vòng nền — test đặt nhỏ để không phải ngủ thật.
    HE_SO_CHO = 1.0
    TRAN_LUI_GIAY = 1800.0

    def __init__(self, so: SoModule):
        self.so = so
        p_trang_thai = kho.thu_muc_du_lieu() / TEP_TRANG_THAI
        try:
            tt = kho.doc_json(p_trang_thai)
        except kho.KhoHong as e:
            # Tệp hỏng: chỉ giữ lại (.hong) khi nội dung thực sự hỏng cú pháp (ValueError / JSONDecodeError).
            # Nếu là lỗi đọc đĩa tạm thời (OSError: phân quyền, hết file descriptor...), không dời tệp lành.
            if isinstance(getattr(e, "__cause__", None), ValueError):
                hong = p_trang_thai.with_name(p_trang_thai.name + ".hong")
                dem = 0
                while hong.exists():
                    dem += 1
                    hong = p_trang_thai.with_name(f"{p_trang_thai.name}.hong.{dem}")
                try:
                    p_trang_thai.rename(hong)
                    log.warning("%s — đã giữ lại ở %s, bắt đầu lại trạng thái đồng bộ", e, hong.name)
                except OSError:
                    log.warning("%s — không đổi tên được tệp hỏng, bắt đầu lại trạng thái đồng bộ", e)
            else:
                log.warning("%s — lỗi đọc đĩa tạm thời, bắt đầu lại trạng thái đồng bộ mà không dời tệp", e)
            tt = None
        self.trang_thai: dict = tt if isinstance(tt, dict) else {}
        self._danh_thuc = asyncio.Event()
        self._khoa = asyncio.Lock()  # một lần đồng bộ/cài từ git tại một thời điểm
        self._khach: httpx.AsyncClient | None = None
        self._tac_vu: asyncio.Task | None = None
        self._lan_loi = 0
        self._lan_dau = False  # lần đồng bộ vừa xong là lần ĐẦU của nguồn này → _tu_dong cập nhật module đang có
        self._cay_muc: list[dict] = []
        self._cay_blob: dict[str, str] = {}
        # NV59: trạng thái đồng bộ từng nguồn phụ (id -> trạng thái), cờ lần đầu, cây theo nguồn.
        self._tt_nguon: dict[str, dict] = {}
        self._lan_dau_nguon: dict[str, bool] = {}
        self._cay_nguon: dict[str, dict] = {}
        self._ghi_chu: dict[tuple[str, str], tuple[str, str]] = {}  # NV59: khoá (nguon_id, module_id)
        self._tu_dong_hong: dict[tuple[str, str], str] = {}  # NV59: khoá (nguon_id, module_id)
        p_tt_nguon = kho.thu_muc_du_lieu() / TEP_TT_NGUON
        try:
            tt_n = kho.doc_json(p_tt_nguon)
        except kho.KhoHong as e:
            log.warning("%s — coi như chưa có trạng thái nguồn phụ", e)
            tt_n = None
        if isinstance(tt_n, dict):
            for nid, st in (tt_n.get("nguon") or {}).items():
                if isinstance(nid, str) and isinstance(st, dict):
                    self._tt_nguon[nid] = st
        if api_github() != API_MAC_DINH:
            log.warning("WEB2API_GITHUB_API=%s khác mặc định %s — chỉ dùng cho test", api_github(), API_MAC_DINH)

    # ───────────── cấu hình ─────────────

    def cau_hinh(self) -> dict | None:
        return doc_cau_hinh()

    def token_va_nguon(self) -> tuple[str | None, str | None]:
        env_tk = (os.environ.get("WEB2API_GITHUB_TOKEN", "").strip()
                  or os.environ.get("WEB2API_GIT_TOKEN", "").strip())
        if env_tk:
            return env_tk, "bien_moi_truong"
        try:
            tk = kho.doc_bi_mat(TEN_BI_MAT).get("token")
        except kho.KhoHong:
            tk = None
        if tk:
            return tk, "da_luu"
        return None, None

    def token(self) -> str | None:
        tk, _ = self.token_va_nguon()
        return tk

    def luu_cau_hinh(self, than: dict) -> dict:
        cfg = kiem_cau_hinh(than, self.cau_hinh() or {})
        token = than.get("token", None)
        if token is not None and not isinstance(token, str):
            raise ValueError("token phải là chuỗi")
        kho.ghi_json_cong_khai(TEP_CAU_HINH, cfg)
        if "token" in than:
            if token:
                kho.ghi_bi_mat(TEN_BI_MAT, {"token": token.strip()})
            else:
                kho.xoa_bi_mat(TEN_BI_MAT)
        self._lan_loi = 0
        self.danh_thuc()
        return self.cong_khai()

    # ───────────── NV59: quản lý nhiều nguồn ─────────────

    def _danh_sach_nguon(self) -> list[dict]:
        """Danh sách nguồn đã cấu hình (đọc đĩa mỗi lần — PUT/xoá có hiệu lực ngay)."""
        return doc_danh_sach_nguon() or []

    def _lay_nguon(self, id_: str) -> dict:
        """Nguồn theo id; không có → LoiGit 404."""
        for n in self._danh_sach_nguon():
            if n["id"] == id_:
                return n
        raise LoiGit(f"không có nguồn git {id_!r}", 404)

    def _nguon_chinh(self) -> tuple[dict | None, dict]:
        """(cấu hình, trạng thái) của nguồn «chinh»; chưa cấu hình → (None, trang_thai)."""
        for n in self._danh_sach_nguon():
            if n["id"] == ID_NGUON_CHINH:
                return n, self.trang_thai
        return None, self.trang_thai

    def _tt(self, nid: str) -> dict:
        """Trạng thái đồng bộ của nguồn: «chinh» dùng self.trang_thai, nguồn phụ dùng _tt_nguon."""
        if nid == ID_NGUON_CHINH:
            return self.trang_thai
        return self._tt_nguon.setdefault(nid, {})

    def _luu_tt_nguon(self) -> None:
        """Lưu trạng thái đồng bộ các nguồn phụ."""
        kho.ghi_json_cong_khai(TEP_TT_NGUON, {"nguon": self._tt_nguon})

    def _token_cua(self, nid: str) -> tuple[str | None, str | None]:
        """Token cho một nguồn: «chinh» dùng luồng cũ (env/kho _nguon_git), nguồn phụ dùng
        bí mật riêng _nguon_git_<id> (không dùng chung — thu hồi độc lập)."""
        if nid == ID_NGUON_CHINH:
            tk, nguon = self.token_va_nguon()
            if tk is None:
                # Tương thích: test có thể mock trực tiếp token()
                tk = self.token()
            return tk, nguon
        env_tk = (os.environ.get(f"WEB2API_GITHUB_TOKEN_{nid.upper()}", "").strip()
                  or os.environ.get(f"WEB2API_GIT_TOKEN_{nid.upper()}", "").strip())
        if env_tk:
            return env_tk, "bien_moi_truong"
        try:
            bm = kho.doc_bi_mat(f"{TEN_BI_MAT}_{nid}")
        except kho.KhoHong:
            bm = None
        if isinstance(bm, dict) and bm.get("token"):
            return str(bm["token"]), "da_luu"
        return self.token_va_nguon()

    async def them_nguon(self, than: dict) -> dict:
        """Thêm nguồn git phụ (đã kiểm qua kiem_nguon; trùng id → 409). Token riêng theo nguồn."""
        cfg = kiem_nguon(than)
        token = than.get("token")
        if token is not None and not isinstance(token, str):
            raise ValueError("token phải là chuỗi")
        async with self._khoa:
            ds = _doc_nguon_phu()
            if any(n["id"] == cfg["id"] for n in ds):
                raise LoiGit(f"đã có nguồn git {cfg['id']!r}", 409)
            ds.append(cfg)
            _ghi_nguon_phu(ds)
            nid = cfg["id"]
            if token:
                kho.ghi_bi_mat(f"{TEN_BI_MAT}_{nid}", {"token": token.strip()})
            else:
                kho.xoa_bi_mat(f"{TEN_BI_MAT}_{nid}")
            self._lan_dau_nguon[nid] = True
            self.danh_thuc()
            return self._cong_khai_nguon(cfg)

    async def sua_nguon(self, id_: str, than: dict) -> dict:
        """Sửa nguồn phụ (trừ id). Không có → 404."""
        if not isinstance(than, dict):
            raise ValueError("cấu hình nguồn git phải là đối tượng JSON")
        token = than.get("token")
        if token is not None and not isinstance(token, str):
            raise ValueError("token phải là chuỗi")
        async with self._khoa:
            ds = _doc_nguon_phu()
            cu = next((n for n in ds if n["id"] == id_), None)
            if cu is None:
                raise LoiGit(f"không có nguồn git {id_!r}", 404)
            cfg = kiem_cau_hinh(than, cu)
            ds = [{"id": n["id"], **cfg} if n["id"] == id_ else n for n in ds]
            _ghi_nguon_phu(ds)
            if token is not None:
                if token:
                    kho.ghi_bi_mat(f"{TEN_BI_MAT}_{id_}", {"token": token.strip()})
                else:
                    kho.xoa_bi_mat(f"{TEN_BI_MAT}_{id_}")
            self._lan_dau_nguon[id_] = True
            self.danh_thuc()
            return self._cong_khai_nguon({"id": id_, **cfg})

    async def xoa_nguon(self, id_: str) -> None:
        """Xoá nguồn phụ: gỡ cấu hình + trạng thái + bí mật + bộ nhớ đệm, GIỮ NGUYÊN module đã cài
        (hợp đồng 5). Không có → 404."""
        async with self._khoa:
            ds = _doc_nguon_phu()
            if not any(n["id"] == id_ for n in ds):
                raise LoiGit(f"không có nguồn git {id_!r}", 404)
            _ghi_nguon_phu([n for n in ds if n["id"] != id_])
            self._tt_nguon.pop(id_, None)
            self._lan_dau_nguon.pop(id_, None)
            self._cay_nguon.pop(id_, None)
            kho.xoa_bi_mat(f"{TEN_BI_MAT}_{id_}")
            self._luu_tt_nguon()
            self.danh_thuc()

    async def dong_bo_nguon(self, id_: str) -> dict:
        """Đồng bộ NGAY một nguồn (qua _dong_bo dùng chung với vòng nền)."""
        async with self._khoa:
            nguon = self._lay_nguon(id_)
            tt = self._tt(id_)
            await self._dong_bo(nguon, tt)
            if id_ == ID_NGUON_CHINH:
                lan_dau, self._lan_dau = self._lan_dau, False
            else:
                lan_dau = self._lan_dau_nguon.pop(id_, False)
            await self._tu_dong_mot(nguon, tt, lan_dau)
            return self._cong_khai_nguon(nguon)

    def danh_sach_nguon(self) -> list[dict]:
        """Danh sách nguồn công khai (không lộ token): cấu hình + trạng thái tóm tắt."""
        return [self._cong_khai_nguon(n) for n in self._danh_sach_nguon()]

    def _cong_khai_nguon(self, nguon: dict) -> dict:
        nid = nguon["id"]
        tt = self._tt(nid)
        commit = tt.get("commit") or {}
        tk, tk_nguon = self._token_cua(nid)
        return {
            "id": nid,
            "repo": nguon["repo"], "nhanh": nguon["nhanh"], "thu_muc": nguon["thu_muc"],
            "chu_ky_giay": nguon["chu_ky_giay"], "tu_dong": nguon.get("tu_dong", False),
            "yeu_cau_chu_ky": nguon.get("yeu_cau_chu_ky", False),
            "token": kho.che(tk) if tk else None, "token_nguon": tk_nguon,
            "so_module": len(tt.get("modules") or []),
            "commit": {k: commit.get(k) for k in ("sha", "tac_gia", "thong_diep", "thoi_gian", "da_xac_minh")},
            "loi": tt.get("loi"),
        }

    async def cai_tu_nguon(self, nid: str, id_: str) -> dict:
        """Cài module từ ĐÚNG một nguồn (kiểm 409 trùng nguồn khác trong _cai_tu)."""
        async with self._khoa:
            nguon = self._lay_nguon(nid)
            return await self._cai_tu(nguon, self._tt(nid), id_)

    def cong_khai(self) -> dict:
        cfg = self.cau_hinh()
        tk, tk_nguon = self.token_va_nguon()
        tt = self.trang_thai
        canh_bao = tt.get("canh_bao")
        if not tk and cfg and isinstance(cfg.get("chu_ky_giay"), (int, float)) and cfg["chu_ky_giay"] < 600:
            cb_chu_ky = "chu kỳ dưới 10 phút khi chưa có token dễ hết hạn mức (60 yêu cầu/giờ)"
            canh_bao = f"{canh_bao}; {cb_chu_ky}" if canh_bao else cb_chu_ky
        cau_hinh_res = None
        if cfg is not None:
            cau_hinh_res = {**cfg, "token": kho.che(tk) if tk else None}
            if tk_nguon == "bien_moi_truong":
                cau_hinh_res["nguon"] = "bien_moi_truong"
                cau_hinh_res["token_nguon"] = "bien_moi_truong"
                cau_hinh_res["nguon_token"] = "bien_moi_truong"
            elif tk_nguon == "da_luu":
                cau_hinh_res["token_nguon"] = "da_luu"
                cau_hinh_res["nguon_token"] = "da_luu"
        return {
            "cau_hinh": cau_hinh_res,
            "trang_thai": {
                "kiem_luc": tt.get("kiem_luc"),
                "loi": tt.get("loi"),
                "canh_bao": canh_bao,
                "han_muc_con": tt.get("han_muc_con"),
                "reset_luc": tt.get("reset_luc"),
                "thu_lai_luc": tt.get("thu_lai_luc"),
                "commit": tt.get("commit"),
                "so_module": len(tt.get("modules") or []),
            },
        }

    # ───────────── HTTP tới GitHub ─────────────

    def _client(self) -> httpx.AsyncClient:
        if self._khach is None:
            # follow_redirects=False: chuyển hướng (repo đổi tên…) KHÔNG được mang token sang chỗ khác.
            # NV16: TransportGhimIp + _dia_chi_bi_chan — chặn SSRF/DNS-rebinding cho cả URL gốc
            # và redirect thủ công (tarball 302 sang codeload.github.com), dùng bộ kiểm chung.
            self._khach = httpx.AsyncClient(follow_redirects=False,
                                            transport=TransportGhimIp(_dia_chi_bi_chan))
        return self._khach

    def _che(self, van_ban: str, nid: str = ID_NGUON_CHINH) -> str:
        tk, _ = self._token_cua(nid)
        return van_ban.replace(tk, kho.che(tk)) if tk else van_ban

    def _doc_han_muc(self, r: httpx.Response, tt: dict | None = None) -> None:
        tt = self.trang_thai if tt is None else tt
        con = r.headers.get("x-ratelimit-remaining")
        reset = r.headers.get("x-ratelimit-reset")
        if con is not None and con.isdigit():
            tt["han_muc_con"] = int(con)
        if reset is not None and reset.isdigit():
            tt["reset_luc"] = iso(int(reset))
            tt["_reset_epoch"] = int(reset)

    async def _goi(self, duong: str, etag: str | None = None,
                   nid: str = ID_NGUON_CHINH, tt: dict | None = None) -> httpx.Response:
        hd = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "web2api"}
        tk, _ = self._token_cua(nid)
        if tk:
            hd["Authorization"] = f"Bearer {tk}"
        if etag:
            hd["If-None-Match"] = etag
        try:
            r = await self._client().get(api_github() + duong, headers=hd,
                                         timeout=httpx.Timeout(30.0, connect=10.0))
        except httpx.HTTPError as e:
            raise LoiGit(f"lỗi mạng tới GitHub: {type(e).__name__}") from None
        self._doc_han_muc(r, tt)
        if r.status_code == 429 or (r.status_code == 403 and r.headers.get("x-ratelimit-remaining") == "0"):
            cho_den = (self.trang_thai if tt is None else tt).get("_reset_epoch")
            ra = r.headers.get("retry-after")
            if ra and ra.isdigit():
                cho_den = max(cho_den or 0, time.time() + int(ra))
            raise LoiGit(f"hết hạn mức API GitHub ({r.status_code}"
                         + (", ẩn danh 60 yêu cầu/giờ/IP — thêm token để nâng" if not tk else "")
                         + f"); thử lại lúc {(self.trang_thai if tt is None else tt).get('reset_luc') or 'sau'}",
                         429, cho_den)
        return r

    def _loi_http(self, r: httpx.Response, viec: str, nid: str = ID_NGUON_CHINH) -> LoiGit:
        try:
            tin = str(r.json().get("message") or "")
        except (ValueError, AttributeError):
            tin = ""
        goi_y = ""
        tk, _ = self._token_cua(nid)
        if r.status_code == 404:
            if not tk:
                goi_y = " — không thấy repo/nhánh (kho riêng tư cần token có quyền đọc (Contents: Read))"
            else:
                goi_y = " — không thấy repo/nhánh (kiểm tra lại tên repo/nhánh hoặc token có quyền đọc (Contents: Read))"
        elif r.status_code == 401:
            goi_y = " — token sai hoặc hết hạn"
        elif r.status_code == 403:
            goi_y = " — bị từ chối truy cập (kho riêng tư cần token có quyền đọc (Contents: Read))"
        elif r.status_code in (301, 302, 307, 308):
            goi_y = " — repo đã đổi tên/chuyển chỗ; sửa «repo» trong cấu hình"
        return LoiGit(self._che(f"GitHub trả HTTP {r.status_code} khi {viec}: {tin[:200]}{goi_y}", nid), 502)

    async def _tai_tarball(self, repo: str, sha: str, nid: str = ID_NGUON_CHINH,
                           tt: dict | None = None) -> bytes:
        if not isinstance(sha, str) or not SHA_HOP_LE.match(sha):
            raise LoiGit(f"sha commit không hợp lệ: {sha!r}")
        hd = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "web2api"}
        tk, _ = self._token_cua(nid)
        if tk:
            hd["Authorization"] = f"Bearer {tk}"
        url_goc = api_github() + f"/repos/{repo}/tarball/{sha}"
        try:
            r = await self._client().get(url_goc, headers=hd,
                                         timeout=httpx.Timeout(60.0, connect=10.0))
        except httpx.HTTPError as e:
            raise LoiGit(f"lỗi mạng tới GitHub: {type(e).__name__}") from None
        self._doc_han_muc(r, tt)
        if r.status_code == 429 or (r.status_code == 403 and r.headers.get("x-ratelimit-remaining") == "0"):
            cho_den = (self.trang_thai if tt is None else tt).get("_reset_epoch")
            ra = r.headers.get("retry-after")
            if ra and ra.isdigit():
                cho_den = max(cho_den or 0, time.time() + int(ra))
            raise LoiGit(f"hết hạn mức API GitHub ({r.status_code}"
                         + (", ẩn danh 60 yêu cầu/giờ/IP — thêm token để nâng" if not tk else "")
                         + f"); thử lại lúc {(self.trang_thai if tt is None else tt).get('reset_luc') or 'sau'}",
                         429, cho_den)

        # Xử lý chuyển hướng một bước (GitHub 302 sang codeload.github.com)
        if r.status_code in (301, 302, 307, 308):
            loc = r.headers.get("location")
            if not loc:
                raise LoiGit(f"GitHub trả HTTP {r.status_code} khi tải tarball nhưng thiếu Location", 502)
            url_tiep = urljoin(url_goc, loc)
            hd_tiep = {"User-Agent": "web2api"}
            # An toàn: KHÔNG gửi Authorization sang máy chủ khác khi theo chuyển hướng
            # Chỉ gửi token khi scheme VÀ netloc trùng với api_github() đã cấu hình
            sp_api = urlsplit(api_github())
            sp_dich = urlsplit(url_tiep)
            if tk and sp_api.scheme == sp_dich.scheme and sp_api.netloc == sp_dich.netloc:
                hd_tiep["Authorization"] = f"Bearer {tk}"
            try:
                r = await self._client().get(url_tiep, headers=hd_tiep,
                                             timeout=httpx.Timeout(60.0, connect=10.0))
            except httpx.HTTPError as e:
                raise LoiGit(f"lỗi mạng khi theo chuyển hướng tarball: {type(e).__name__}") from None

        if r.status_code != 200:
            raise self._loi_http(r, f"tải tarball commit {sha[:12]}", nid)
        du_lieu = r.content
        if len(du_lieu) > TRAN_TONG:
            raise LoiGit(f"tarball commit {sha[:12]} lớn {len(du_lieu)} byte, vượt trần {TRAN_TONG}", 422)
        return du_lieu

    async def _doc_cay(self, repo: str, cay: str, nid: str = ID_NGUON_CHINH,
                       tt: dict | None = None) -> list[dict]:
        if not SHA_HOP_LE.match(cay):
            raise LoiGit(f"sha cây không hợp lệ: {cay!r}")
        r = await self._goi(f"/repos/{repo}/git/trees/{cay}?recursive=1", nid=nid, tt=tt)
        if r.status_code != 200:
            raise self._loi_http(r, "đọc cây thư mục", nid)
        j = r.json()
        if j.get("truncated"):
            raise LoiGit("cây thư mục quá lớn, GitHub cắt bớt (truncated) — không đọc được đầy đủ")
        return j.get("tree") or []

    async def _doc_blob(self, repo: str, sha: str, nid: str = ID_NGUON_CHINH,
                        tt: dict | None = None) -> bytes:
        if not isinstance(sha, str) or not SHA_HOP_LE.match(sha):
            raise LoiGit(f"sha tệp không hợp lệ: {sha!r}")
        r = await self._goi(f"/repos/{repo}/git/blobs/{sha}", nid=nid, tt=tt)
        if r.status_code != 200:
            raise self._loi_http(r, f"tải tệp {sha[:12]}", nid)
        j = r.json()
        if j.get("encoding") != "base64" or not isinstance(j.get("content"), str):
            raise LoiGit(f"tệp {sha[:12]} không ở dạng base64")
        du_lieu = base64.b64decode(j["content"])
        if len(du_lieu) > TRAN_BLOB:
            raise LoiGit(f"tệp {sha[:12]} lớn {len(du_lieu)} byte, vượt trần {TRAN_BLOB}")
        if bam_blob(du_lieu) != sha:
            raise LoiGit(f"tệp {sha[:12]} SAI BĂM — nội dung nhận được không khớp sha trong cây git; "
                         "từ chối cả module", 422)
        return du_lieu

    # ───────────── thăm dò ─────────────

    def _luu_trang_thai(self) -> None:
        tt = dict(self.trang_thai)
        tt["danh_sach"] = self.danh_sach()
        kho.ghi_json_cong_khai(TEP_TRANG_THAI, tt)

    async def mot_lan(self) -> None:
        """Một vòng thăm dò nguồn «chinh». LoiGit nếu GitHub lỗi (người gọi lùi nhịp)."""
        nguon, tt = self._nguon_chinh()
        if nguon is None:
            return
        await self._dong_bo(nguon, tt)

    async def _dong_bo(self, nguon: dict, tt: dict) -> None:
        """Một vòng thăm dò của ĐÚNG một nguồn. LoiGit nếu GitHub lỗi (người gọi lùi nhịp)."""
        nid = nguon["id"]
        repo, nhanh, thu_muc = nguon["repo"], nguon["nhanh"], nguon["thu_muc"]
        if tt.get("dinh_danh") != [repo, nhanh, thu_muc]:
            tt.clear()
            tt["dinh_danh"] = [repo, nhanh, thu_muc]
        # «Lần đầu» = trạng thái chưa từng có commit của ĐÚNG nguồn này (đổi repo/nhánh/thư mục thì tt vừa bị
        # xoá ở trên). Dấu chính là commit đã lưu trong trạng thái: máy đã đồng bộ từ bản cũ
        # thì có sẵn commit → nâng cấp lõi KHÔNG tự cập nhật module lúc không ai bấm; khởi động lại cũng vậy.
        lan_dau = not tt.get("commit")
        r = await self._goi(f"/repos/{repo}/commits/{quote(nhanh, safe='/')}", etag=tt.get("etag"),
                            nid=nid, tt=tt)
        tt["kiem_luc"] = kho.bay_gio_iso()
        if r.status_code == 304:
            tt["loi"] = None
            return
        if r.status_code == 409:  # kho rỗng: không phải lỗi, chỉ là chưa có module nào
            tt.update(etag=None, commit=None, modules=[], loi=None)
            return
        if r.status_code != 200:
            raise self._loi_http(r, "đọc commit mới nhất", nid)
        j = r.json()
        c = j.get("commit") or {}
        commit = {"sha": j.get("sha"), "tac_gia": (c.get("author") or {}).get("name"),
                  "thong_diep": str(c.get("message") or "")[:300],
                  "da_xac_minh": bool((c.get("verification") or {}).get("verified")),
                  "cay": (c.get("tree") or {}).get("sha")}
        cay = await self._doc_cay(repo, str(commit["cay"]), nid, tt)
        self._cay_nguon[nid] = {"muc": cay, "blob": {e["path"]: e["sha"] for e in cay if e.get("type") == "blob"}}
        if nid == ID_NGUON_CHINH:
            # tương thích: mã cũ đọc self._cay_muc/_cay_blob
            self._cay_muc = self._cay_nguon[nid]["muc"]
            self._cay_blob = self._cay_nguon[nid]["blob"]
        modules = await self._doc_modules(repo, cay, thu_muc, tt.get("modules") or [], nid, tt)
        # ETag chỉ lưu khi đã đọc xong: hỏng giữa chừng thì lần sau không bị 304 che mất commit này.
        tt.update(etag=r.headers.get("etag"), commit=commit, modules=modules, loi=None)
        if nid == ID_NGUON_CHINH:
            self._lan_dau = lan_dau
        else:
            self._lan_dau_nguon[nid] = lan_dau

    async def _doc_modules(self, repo: str, cay: list[dict], thu_muc: str, cu: list[dict],
                           nid: str = ID_NGUON_CHINH, tt: dict | None = None) -> list[dict]:
        tien = thu_muc + "/"
        thu_muc_con = {e["path"][len(tien):]: e["sha"] for e in cay
                       if e.get("type") == "tree" and e["path"].startswith(tien) and "/" not in e["path"][len(tien):]}
        manifest = {e["path"][len(tien):-len("/manifest.json")]: e["sha"] for e in cay
                    if e.get("type") == "blob" and e["path"].startswith(tien) and e["path"].endswith("/manifest.json")}
        cu_theo_id = {m["id"]: m for m in cu if isinstance(m, dict)}
        # Dung lượng tải = cộng `size` mọi blob trong thư mục module của CÂY đã đọc (số thật của git, không tải gì thêm).
        dung_luong: dict[str, int] = {}
        for e in cay:
            if e.get("type") == "blob" and e["path"].startswith(tien) and "/" in e["path"][len(tien):]:
                ten_tm = e["path"][len(tien):].split("/", 1)[0]
                dung_luong[ten_tm] = dung_luong.get(ten_tm, 0) + int(e.get("size") or 0)
        ra = []
        for ten, sha_tm in sorted(thu_muc_con.items()):
            if ten not in manifest:
                continue  # thư mục con không có manifest.json ở gốc thì không phải module
            truoc = cu_theo_id.get(ten)
            # Thư mục không đổi → khỏi tải lại manifest (tiết kiệm hạn mức). Bản tóm tắt do lõi CŨ ghi (chưa có
            # `loai`) thì đọc lại một lần: thiếu `loai` thì trang Module không xếp được ô «Chưa cài» vào khu nào.
            if truoc and truoc.get("tree_sha") == sha_tm and (truoc.get("loi") or "loai" in truoc):
                ra.append({**truoc, "dung_luong": dung_luong.get(ten, 0)})
                continue
            muc = {"id": ten, "tree_sha": sha_tm, "loi": None, "dung_luong": dung_luong.get(ten, 0)}
            try:
                m = json.loads(await self._doc_blob(repo, manifest[ten], nid, tt))
                ly_do = kiem_manifest(m)
                if ly_do is None and m["id"] != ten:
                    ly_do = f"id trong manifest ({m['id']!r}) khác tên thư mục ({ten!r})"
            except LoiGit:
                raise
            except ValueError as e:
                ly_do, m = f"manifest.json không phải JSON: {e}", {}
            if ly_do:
                muc["loi"] = ly_do
            else:
                muc.update(ten=m["ten"], mo_ta=m["mo_ta"], phien_ban=m.get("phien_ban"),
                           yeu_cau_goi=m.get("yeu_cau_goi", []), mang_ra=m["tai_nguyen"].get("mang_ra", []),
                           tai_nguyen=m["tai_nguyen"], loai=m["loai"], loai_ten=m.get("loai_ten"),
                           loai_thu_tu=m.get("loai_thu_tu"), nhom=m.get("nhom"), cung_cap=m.get("cung_cap", []))
            ra.append(muc)
        return ra

    # ───────────── trạng thái & thông báo ─────────────

    def _nguon_da_cai(self, id_: str) -> dict | None:
        km = self.so.catalog.get(id_)
        if km is None or km.nguon != "du_lieu":
            return None
        try:
            nj = json.loads((km.thu_muc / ".nguon.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if isinstance(nj, dict):
            nj.setdefault("nguon_id", ID_NGUON_CHINH)  # NV59: bản cài cũ (trước NV59) coi như từ «chinh»
        return nj

    def _danh_sach_mot(self, nguon: dict | None, tt: dict) -> list[dict]:
        """Trạng thái từng module của MỘT nguồn so với bản đã cài — tính LÚC ĐỌC (người dùng cài/gỡ
        không cần đợi vòng thăm dò sau mới thấy đúng). Bản đã cài khớp khi sha THƯ MỤC module
        trong git bằng nhau: so nội dung thật, chặt hơn so số phiên bản (quên tăng phiên bản vẫn thấy)."""
        if nguon is None:
            return []
        nid = nguon["id"]
        commit = tt.get("commit") or {}
        ra = []
        for m in tt.get("modules") or []:
            km = self.so.catalog.get(m["id"])
            nguon_da_cai = self._nguon_da_cai(m["id"])
            ly_do = m.get("loi")
            if ly_do:
                tt_trang_thai = "loi_manifest"
            elif km is not None and km.nguon == "anh":
                tt_trang_thai, ly_do = "loi_manifest", "trùng id với module đi kèm ảnh"
            elif km is None:
                tt_trang_thai = "moi"
            elif nguon_da_cai and nguon_da_cai.get("tree_sha") == m["tree_sha"]:
                tt_trang_thai = "da_cai_dung"
            else:
                tt_trang_thai = "co_ban_moi"
            ghi_chu = self._ghi_chu.get((nid, m["id"]))
            ra.append({
                "id": m["id"], "ten": m.get("ten"), "trang_thai": tt_trang_thai,
                "phien_ban_cu": km.manifest.get("phien_ban") if km else None,
                "phien_ban_moi": m.get("phien_ban"),
                # Tóm tắt manifest trên git: trang Module xếp ô «Chưa cài» vào khu theo `loai` và hiện «Cần gì để chạy».
                **{k: m.get(k) for k in ("mo_ta", "loai", "loai_ten", "loai_thu_tu", "nhom", "cung_cap", "tai_nguyen",
                                         "yeu_cau_goi", "dung_luong")},
                "commit": {k: commit.get(k) for k in ("sha", "tac_gia", "thong_diep")},
                "ly_do": ly_do,
                "ghi_chu": ghi_chu[1] if ghi_chu and ghi_chu[0] == m["tree_sha"] else None,
            })
        return ra

    def danh_sach(self) -> list[dict]:
        """Trạng thái từng module trên git so với bản đã cài (nguồn chính), cộng các module đã cài
        từ nguồn đã bị xoá (NV59: `nguon_trang_thai` = «nguồn đã gỡ», module KHÔNG bị gỡ theo)."""
        nguon, tt = self._nguon_chinh()
        ra = self._danh_sach_mot(nguon, tt)
        co = {x["id"] for x in ra}
        ids_hien = {n["id"] for n in self._danh_sach_nguon()}
        for id_mod, km in self.so.catalog.items():
            if id_mod in co or km.nguon != "du_lieu":
                continue
            nj = self._nguon_da_cai(id_mod)
            nid = (nj or {}).get("nguon_id")
            if not nid or nid in ids_hien:
                continue
            ra.append({
                "id": id_mod, "ten": km.manifest.get("ten"), "trang_thai": "nguon_da_go",
                "nguon_trang_thai": "nguồn đã gỡ", "nguon_id": nid,
                "phien_ban_cu": km.manifest.get("phien_ban"), "phien_ban_moi": None,
                "mo_ta": km.manifest.get("mo_ta"),
                "commit": {"sha": (nj or {}).get("sha_commit")},
                "ly_do": None, "ghi_chu": None,
            })
        return ra

    def thong_bao(self) -> dict:
        tat_ca = self.danh_sach()
        ds = [x for x in tat_ca if x["trang_thai"] != "da_cai_dung"]
        tt = self.trang_thai
        # `moi_nhat`: module đã cài ĐÚNG bản trên git — giao diện ghi «Đã là bản mới nhất» (không suy từ chỗ vắng mặt:
        # vắng mặt còn có thể là module không có trên git).
        return {"so_moi": sum(x["trang_thai"] in ("moi", "co_ban_moi") for x in ds), "danh_sach": ds,
                "moi_nhat": [x["id"] for x in tat_ca if x["trang_thai"] == "da_cai_dung"],
                "dong_bo": {k: tt.get(k) for k in ("kiem_luc", "loi", "canh_bao", "han_muc_con", "reset_luc")}}

    def so_thong_bao(self) -> int:
        return sum(x["trang_thai"] in ("moi", "co_ban_moi") for x in self.danh_sach())

    # ───────────── cài từ git ─────────────

    def viec(self, id_: str) -> str:
        """Quy tắc từ ngữ, suy từ ĐĨA chứ không từ danh sách: module chưa có mã trên máy thì là «cài»;
        đã cài rồi (có trong catalog, kể cả chưa liên kết hay đặt tay) thì mọi thay đổi bản là «cập nhật»."""
        return "cập nhật" if id_ in self.so.catalog else "cài"

    async def cai(self, id_: str, chap_nhan_quyen_them: list | None = None,
                sha_commit: str | None = None,
                nguoi_thuc_hien: str | None = None) -> dict:
        """Cài module từ git (nguồn chính). Có `chap_nhan_quyen_them` + `sha_commit` → duyệt bản đang chờ
        (phải khớp chính xác diff đã xem); không có → cài thường (bản mới xin thêm quyền
        thì 409 chờ duyệt thay vì kích hoạt).

        NV50: kiểm tra xác nhận lần 2 đặt ở route HTTP (nơi người bấm), không ở đây.
        """
        async with self._khoa:
            if chap_nhan_quyen_them is not None or sha_commit is not None:
                return await self._cai_duyet(id_, chap_nhan_quyen_them, sha_commit,
                                            nguoi_thuc_hien=nguoi_thuc_hien)
            nguon, tt = self._nguon_chinh()
            if nguon is None:
                raise LoiGit("chưa cấu hình nguồn git (PUT /api/nguon-git)", 400)
            return await self._cai_tu(nguon, tt, id_)

    async def _cai_duyet(self, id_: str, chap_nhan_quyen_them: list | None, sha_commit: str | None,
                        nguoi_thuc_hien: str | None = None) -> dict:
        """Duyệt bản đang chờ: không tải lại, kích hoạt đúng thư mục đã chốt khi xem diff.

        NV50 CG2: mỗi lần duyệt ghi nhật ký quản trị (người thực hiện + module).
        NV50 CG3: quyền duyệt được cấp có kỳ hạn (het_han trong tương lai).
        (CG1 — xác nhận lần 2 — kiểm ở route HTTP, nơi người bấm.)
        """
        if not isinstance(chap_nhan_quyen_them, list) or not sha_commit:
            raise LoiGit("duyệt cần thân {\"chap_nhan_quyen_them\": [...], \"sha_commit\": \"...\"}", 400)
        pending = kho.doc_cho_duyet_quyen(id_)
        if pending is None:
            raise LoiGit(f"module {id_!r} không có yêu cầu chờ duyệt quyền", 409)
        if sha_commit != pending.get("sha_commit"):
            raise LoiGit("SHA commit không khớp yêu cầu chờ duyệt — cài lại để xem diff mới", 409)
        thu_muc = SoModule.thu_muc_cho_duyet(id_)  # suy từ id, không dùng đường dẫn lưu trong JSON
        if not thu_muc.is_dir():
            kho.xoa_cho_duyet_quyen(id_)
            raise LoiGit(f"bản chờ duyệt của {id_!r} không còn trên đĩa — cài lại để tạo yêu cầu mới", 409)
        if self.so.catalog.get(id_) is None:
            kho.xoa_cho_duyet_quyen(id_)
            raise LoiGit(f"module {id_!r} hiện không cài — cài mới thay vì duyệt", 409)
        kq = await self.so.cai_thu_muc(id_, thu_muc, chap_nhan_quyen_them=chap_nhan_quyen_them)
        het_han = (datetime.now(timezone.utc) + timedelta(days=KY_HAN_DUYET_HANG_B_NGAY)).isoformat()
        nhat_ky.ghi("quan_tri", "thong_tin",
                    f"Đã duyệt và kích hoạt bản mới của module {id_} (commit {str(sha_commit)[:12]})",
                    module=id_, nguoi_thuc_hien=nguoi_thuc_hien or "?",
                    het_han_quyen_duyet=het_han)
        return {"ok": True, "id": id_, "duyet_quyen": True,
                "duyet_hang_b": {"nguoi_duyet": nguoi_thuc_hien or "?", "het_han": het_han},
                **kq}

    async def _cai_tu(self, nguon: dict, tt: dict, id_: str, tu_dong: bool = False) -> dict:
        """Cài module từ ĐÚNG một nguồn. NV59: trùng id với module đã cài từ nguồn KHÁC → 409
        (không tự đè); đúng nguồn cũ → cập nhật bình thường.
        `tu_dong=True` chỉ từ vòng tự cài: bản mới của module ĐÃ cài thì giữ chờ duyệt (cai_thu_muc, NV49 R-1)."""
        nid = nguon["id"]
        if not isinstance(id_, str) or not ID_HOP_LE.match(id_):
            raise LoiGit(f"id {id_!r} không hợp lệ", 400)
        commit = tt.get("commit") or {}
        m = next((x for x in tt.get("modules") or [] if x["id"] == id_), None)
        if m is None or not commit.get("sha"):
            raise LoiGit(f"module {id_!r} không có trong lần đồng bộ gần nhất của nguồn {nid!r}"
                         " — chờ vòng thăm dò sau", 404)
        if m.get("loi"):
            raise LoiGit(f"manifest của {id_!r} lỗi: {m['loi']}", 422)
        # NV59 hợp đồng 3: hai nguồn trùng id module → KHÔNG tự đè nhau.
        da_cai = self._nguon_da_cai(id_)
        if da_cai is not None:
            nid_cu = da_cai.get("nguon_id") or "chinh"
            if nid_cu != nid:
                raise LoiGit(f"module {id_!r} đã cài từ nguồn {nid_cu!r} — không tự đè bằng nguồn {nid!r} "
                             "(trùng id module từ hai nguồn)", 409)
        repo, thu_muc = nguon["repo"], nguon["thu_muc"]
        tien = f"{thu_muc}/{id_}/"

        # Nếu chưa có cây trong bộ nhớ (ví dụ sau khởi động lại), đọc cây một lần
        cay_blob = self._cay_nguon.get(nid)
        if not cay_blob and commit.get("cay"):
            cay = await self._doc_cay(repo, str(commit["cay"]), nid, tt)
            cay_blob = {"muc": cay, "blob": {e["path"]: e["sha"] for e in cay if e.get("type") == "blob"}}
            self._cay_nguon[nid] = cay_blob
            if nid == ID_NGUON_CHINH:
                self._cay_muc, self._cay_blob = cay_blob["muc"], cay_blob["blob"]
        cay_muc = (cay_blob or {}).get("muc") or []
        blob_theo_duong = (cay_blob or {}).get("blob") or {}

        # Kiểm tra submodule từ cây nếu có
        for e in cay_muc:
            if e.get("path", "").startswith(tien):
                rel = e["path"][len(tien):]
                if e.get("mode") == "160000" or e.get("type") == "commit":
                    raise LoiGit(f"module chứa submodule {rel!r} — từ chối", 422)

        # Cài bằng MỘT yêu cầu tarball chốt theo commit đã thấy
        du_lieu_tar = await self._tai_tarball(repo, str(commit["sha"]), nid, tt)
        tam = kho.thu_muc_modules() / f".tam-{id_}-{secrets.token_hex(4)}"
        try:
            try:
                tf = tarfile.open(fileobj=io.BytesIO(du_lieu_tar), mode="r:*")
            except (tarfile.TarError, EOFError, OSError) as e:
                raise LoiGit(f"tarball commit {str(commit['sha'])[:12]} hỏng hoặc không đúng định dạng: {e}", 422)

            with tf:
                tep_trong_module, tong_dung_luong = self._giai_nen_kiem_tra(tf, tien, blob_theo_duong)
                tam.mkdir(parents=True)
                for rel, noi_dung in tep_trong_module:
                    p = tam / rel
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_bytes(noi_dung)
                    os.chmod(p, 0o644)

            manifest, ly_do = doc_manifest(tam)
            if ly_do or manifest["id"] != id_:
                raise LoiGit(f"manifest tải về không hợp lệ: {ly_do or 'id khác tên thư mục'}", 422)
            nguon = {"nguon_id": nid, "repo": repo, "nhanh": nguon["nhanh"], "sha_commit": commit["sha"],
                     "tree_sha": m["tree_sha"],
                     "phien_ban": manifest.get("phien_ban"), "cai_luc": kho.bay_gio_iso()}
            (tam / ".nguon.json").write_text(json.dumps(nguon, ensure_ascii=False, indent=2), encoding="utf-8")
            viec = self.viec(id_)  # đọc TRƯỚC khi cài: cài xong thì module đã nằm trong catalog
            kq = await self.so.cai_thu_muc(id_, tam, tu_dong=tu_dong)
        except BaseException:
            if tam.exists():
                shutil.rmtree(tam, ignore_errors=True)
            raise
        self._ghi_chu.pop((nid, id_), None)
        self._tu_dong_hong.pop((nid, id_), None)
        (tt.get("da_go") or {}).pop(id_, None)
        log.warning("Đã %s module %s phiên bản %s từ git (commit %s)", viec, id_, manifest.get("phien_ban"),
                    str(commit["sha"])[:12])
        return {"ok": True, "id": id_, "phien_ban": manifest.get("phien_ban"), "sha_commit": commit["sha"], **kq}

    def _giai_nen_kiem_tra(self, tf: tarfile.TarFile, tien: str,
                           blob_theo_duong: dict[str, str]) -> tuple[list[tuple[str, bytes]], int]:
        tep_ra: list[tuple[str, bytes]] = []
        da_thay: set[str] = set()
        tong = 0
        for member in tf.getmembers():
            ten_goc = member.name
            if "\0" in ten_goc or "\\" in ten_goc:
                raise LoiGit(f"đường dẫn không an toàn trong tarball: {ten_goc!r}", 422)
            parts = [p for p in ten_goc.split("/") if p]
            if any(p in ("..", ".") for p in ten_goc.split("/")):
                raise LoiGit(f"đường dẫn không an toàn trong tarball: {ten_goc!r}", 422)
            if posixpath.isabs(ten_goc) or ten_goc.startswith("/"):
                raise LoiGit(f"đường dẫn không an toàn trong tarball: {ten_goc!r}", 422)

            # Xác định đường dẫn tương đối trong kho sau khi bỏ tiền tố gốc nếu có (<owner>-<repo>-<sha>/)
            if ten_goc.startswith(tien):
                rel_repo = ten_goc
            elif len(parts) > 1 and "/".join(parts[1:]).startswith(tien):
                rel_repo = "/".join(parts[1:])
            else:
                rel_repo = None

            if rel_repo is None:
                # Mục ngoài module: bỏ qua (chỉ giải nén module này)
                continue

            # Mục thuộc module: kiểm tra an toàn chặt chẽ
            if member.issym() or member.islnk():
                raise LoiGit(f"module chứa symlink {member.name!r} — từ chối", 422)
            if member.isdev() or member.ischr() or member.isblk() or member.isfifo():
                raise LoiGit(f"loại mục lạ trong module: {member.name!r}", 422)
            if member.isdir():
                continue
            if not member.isreg():
                raise LoiGit(f"loại mục lạ trong module: {member.name!r}", 422)

            rel = rel_repo[len(tien):]
            if (not rel or rel.startswith("/") or "\\" in rel or "\0" in rel
                    or any(p in ("", ".", "..") for p in rel.split("/")) or posixpath.normpath(rel) != rel):
                raise LoiGit(f"đường dẫn không an toàn trong module: {rel!r}", 422)
            khoa = unicodedata.normalize("NFC", rel).casefold()
            if khoa in da_thay:
                raise LoiGit(f"tên tệp trùng nhau sau chuẩn hoá: {rel!r}", 422)
            da_thay.add(khoa)

            if member.size > TRAN_BLOB:
                raise LoiGit(f"tệp {rel!r} lớn {member.size} byte, vượt trần {TRAN_BLOB}", 422)
            tong += member.size
            if tong > TRAN_TONG:
                raise LoiGit(f"tổng dung lượng {tong} byte vượt trần {TRAN_TONG}", 422)
            if len(tep_ra) + 1 > TRAN_TEP:
                raise LoiGit(f"module có nhiều hơn {TRAN_TEP} tệp, vượt trần {TRAN_TEP}", 422)

            f = tf.extractfile(member)
            if f is None:
                raise LoiGit(f"không đọc được nội dung tệp {rel!r} từ tarball", 422)
            du_lieu = f.read()
            if len(du_lieu) != member.size:
                raise LoiGit(f"dung lượng tệp {rel!r} đọc được không khớp header tar", 422)

            expected_sha = blob_theo_duong.get(rel_repo)
            if expected_sha and bam_blob(du_lieu) != expected_sha:
                raise LoiGit(f"tệp {rel!r} SAI BĂM — nội dung nhận được không khớp sha trong cây git; "
                             "từ chối cả module", 422)

            tep_ra.append((rel, du_lieu))

        ten_tep = {r for r, _ in tep_ra}
        if not tep_ra:
            raise LoiGit(f"không tìm thấy thư mục {tien} trong tarball", 404)
        if "manifest.json" not in ten_tep or "module.py" not in ten_tep:
            raise LoiGit("module phải có manifest.json và module.py ở gốc thư mục", 422)
        return tep_ra, tong

    def da_go(self, id_: str) -> None:
        """Quản trị vừa gỡ module: nhớ (lưu đĩa) sha thư mục của bản trên git để vòng tự cài KHÔNG cài
        lại đúng bản đó ở lần thăm dò sau — không thì gỡ xong một chu kỳ là module quay về.
        NV59: đánh dấu ở mọi nguồn đang liệt kê module này (không biết module từ nguồn nào vì đã gỡ)."""
        # Luôn đánh dấu ở self.trang_thai: vừa là trạng thái nguồn «chinh», vừa tương thích mã/test
        # gán trực tiếp trang_thai (không qua cấu hình nguồn).
        m = next((x for x in self.trang_thai.get("modules") or [] if x["id"] == id_), None)
        if m is not None:
            self.trang_thai.setdefault("da_go", {})[id_] = m["tree_sha"]
        for nguon in self._danh_sach_nguon():
            if nguon["id"] == ID_NGUON_CHINH:
                continue  # đã xử lý ở trên
            tt = self._tt(nguon["id"])
            m = next((x for x in tt.get("modules") or [] if x["id"] == id_), None)
            if m is None:
                continue
            tt.setdefault("da_go", {})[id_] = m["tree_sha"]
        try:
            self._luu_trang_thai()
        except OSError as e:
            log.warning("Không ghi được %s: %s", TEP_TRANG_THAI, e)
        try:
            self._luu_tt_nguon()
        except OSError as e:
            log.warning("Không ghi được %s: %s", TEP_TT_NGUON, e)

    # ───────────── tự cài ─────────────

    async def _tu_dong(self, bo_qua: set[str] | None = None) -> None:
        """Tự cài cho TẤT CẢ nguồn đã cấu hình — mỗi nguồn theo `tu_dong`/`yeu_cau_chu_ky` của mình
        (NV59 hợp đồng 4). `bo_qua`: id các nguồn vừa đồng bộ hỏng (vòng nền) thì bỏ qua lượt này."""
        bo_qua = bo_qua or set()
        for nguon in self._danh_sach_nguon():
            nid = nguon["id"]
            if nid in bo_qua:
                continue
            tt = self._tt(nid)
            if nid == ID_NGUON_CHINH:
                lan_dau, self._lan_dau = self._lan_dau, False
            else:
                lan_dau = self._lan_dau_nguon.pop(nid, False)
            await self._tu_dong_mot(nguon, tt, lan_dau)

    async def _tu_dong_mot(self, nguon: dict, tt: dict, lan_dau: bool) -> None:
        """Tự cài sau một vòng thăm dò của MỘT nguồn, hai trường hợp, cùng mọi chốt bên dưới (chữ ký, bản vừa gỡ,
        bản đã tự cài hỏng):
          - `tu_dong` bật: module MỚI thì cài thẳng; bản mới của module đã cài thì tải về giữ chờ duyệt;
          - lần đồng bộ ĐẦU của nguồn (`lan_dau`, KE-HOACH-CAI-MODULE.md 3.4): MỘT lần, module đã có mã mà
            git có bản khác (`co_ban_moi`, kể cả module đặt tay không có .nguon.json) được tải về giữ chờ duyệt
            dù `tu_dong` tắt — không cài module mới, việc đó chủ máy bấm.
        NV49 R-1: mọi bản mới của module ĐÃ cài (đổi mã dù quyền giữ nguyên, hay xin thêm quyền) đều do cổng
        trong `cai_thu_muc` giữ lại chờ duyệt — mã mới chỉ chạy khi quản trị bấm «Cập nhật» hoặc duyệt."""
        nid = nguon["id"]
        duoc = {"moi", "co_ban_moi"} if nguon.get("tu_dong") else {"co_ban_moi"} if lan_dau else set()
        if not duoc:
            return
        commit = tt.get("commit") or {}
        snap = {m["id"]: m for m in tt.get("modules") or []}
        for x in self._danh_sach_mot(nguon, tt):
            if x["trang_thai"] not in duoc:
                continue
            m = snap[x["id"]]
            viec = self.viec(x["id"])
            khoa = (nid, x["id"])
            if self._tu_dong_hong.get(khoa) == m["tree_sha"]:
                continue  # bản này đã tự cài hỏng một lần — chờ bản mới hoặc quản trị
            if (tt.get("da_go") or {}).get(x["id"]) == m["tree_sha"]:
                self._ghi_chu[khoa] = (m["tree_sha"], f"quản trị đã gỡ bản này — không tự {viec} lại; "
                                                     f"bản mới trên git hoặc {viec} tay thì {viec}")
                continue
            ly_do = None
            if nguon.get("yeu_cau_chu_ky") and not commit.get("da_xac_minh"):
                ly_do = f"commit chưa được GitHub xác minh chữ ký (yeu_cau_chu_ky bật) — không tự {viec}, cần quản trị {viec} tay"
            if ly_do:
                self._ghi_chu[khoa] = (m["tree_sha"], ly_do)
                continue
            try:
                await self._cai_tu(nguon, tt, x["id"], tu_dong=True)
            except LoiCanDuyetQuyen as e:
                # Bản mới xin thêm quyền hoặc đổi mã module đã cài: cổng trong cai_thu_muc đã lưu yêu cầu chờ
                # duyệt — giữ lại, đánh dấu để không tải lại mỗi vòng (quản trị duyệt tay khi sẵn sàng).
                self._tu_dong_hong[khoa] = m["tree_sha"]
                if e.diff["them"]:
                    self._ghi_chu[khoa] = (m["tree_sha"],
                        f"bản mới XIN THÊM QUYỀN ({len(e.diff['them'])} mục) — không tự {viec}, "
                        f"chờ quản trị duyệt (POST /api/nguon-git/cai/{x['id']})")
                    log.warning("Tự %s module %s: bản mới xin thêm quyền, chờ quản trị duyệt", viec, x["id"])
                else:
                    self._ghi_chu[khoa] = (m["tree_sha"],
                        f"bản mới ĐỔI MÃ module đã cài (không xin thêm quyền) — "
                        f"không tự {viec}, chờ quản trị duyệt: bấm «Cập nhật» hoặc POST /api/nguon-git/cai/{x['id']} "
                        f"với {{\"chap_nhan_quyen_them\": [], \"sha_commit\": \"{e.sha_commit}\"}}")
                    log.warning("Tự %s module %s: bản mới đổi mã, chờ quản trị duyệt", viec, x["id"])
            except (LoiGit, LoiSo) as e:
                self._tu_dong_hong[khoa] = m["tree_sha"]
                self._ghi_chu[khoa] = (m["tree_sha"], f"tự {viec} hỏng: {e}")
                log.warning("Tự %s module %s hỏng: %s", viec, x["id"], e)

    # ───────────── vòng nền ─────────────

    def nhip_ke_tiep(self, chu_ky: float, bay_gio: float | None = None) -> float:
        """Giây tới lần thăm dò sau. Hạn mức còn < 10 → giãn tới giờ reset (vẫn giữ ETag)."""
        bay_gio = time.time() if bay_gio is None else bay_gio
        con = self.trang_thai.get("han_muc_con")
        reset = self.trang_thai.get("_reset_epoch")
        if con is not None and con < HAN_MUC_THAP and reset and reset > bay_gio:
            self.trang_thai["canh_bao"] = (f"hạn mức API GitHub còn {con} yêu cầu — giãn nhịp thăm dò tới "
                                           f"{self.trang_thai.get('reset_luc')}")
            return max(chu_ky, reset - bay_gio + 1)
        self.trang_thai["canh_bao"] = None
        return chu_ky

    def danh_thuc(self) -> None:
        self._danh_thuc.set()

    async def _cho(self, giay: float | None) -> None:
        try:
            if giay is None:
                await self._danh_thuc.wait()
            else:
                await asyncio.wait_for(self._danh_thuc.wait(), timeout=giay * self.HE_SO_CHO)
        except TimeoutError:
            pass
        self._danh_thuc.clear()

    def _ghi_loi_nguon(self, nid: str, e: Exception) -> None:
        """Ghi lỗi đồng bộ của MỘT nguồn (NV59: lỗi nguồn này không chặn nguồn kia)."""
        tt = self._tt(nid)
        if isinstance(e, LoiGit):
            tin = str(e)
        else:
            log.exception("Lỗi nội bộ khi đồng bộ nguồn git %s", nid)
            tin = f"lỗi nội bộ: {type(e).__name__}"
        tin = self._che(tin, nid)
        tt["loi"] = tin
        log.warning("Đồng bộ nguồn git %s hỏng: %s", nid, tin)

    async def chay_nen(self) -> None:
        while True:
            ds = self._danh_sach_nguon()
            if not ds:
                await self._cho(None)  # chưa cấu hình: ngủ tới khi PUT /api/nguon-git đánh thức
                continue
            chu_ky = min(float(n["chu_ky_giay"]) for n in ds)
            # Lời đánh thức tới TRƯỚC vòng này đã được chính vòng này phục vụ — xoá để lần chờ sau
            # không bật lại ngay (đo được: PUT cấu hình rồi khởi động → 2 yêu cầu liền nhau lúc hết hạn mức).
            self._danh_thuc.clear()
            try:
                async with self._khoa:
                    that_bai = set()
                    loi_dau = None
                    for nguon in ds:
                        # NV59 hợp đồng 2: mỗi nguồn thăm dò riêng; lỗi nguồn này không chặn nguồn kia.
                        try:
                            await self._dong_bo(nguon, self._tt(nguon["id"]))
                        except asyncio.CancelledError:
                            raise
                        except Exception as e:
                            that_bai.add(nguon["id"])
                            self._ghi_loi_nguon(nguon["id"], e)
                            if loi_dau is None:
                                loi_dau = e
                    if loi_dau is not None and len(that_bai) == len(ds):
                        # Tất cả nguồn đều hỏng → lùi nhịp toàn cục như cũ (đặt thu_lai_luc).
                        raise loi_dau
                    await self._tu_dong(bo_qua=that_bai)
                    try:
                        self._luu_tt_nguon()
                    except OSError as e:
                        log.warning("Không ghi được %s: %s", TEP_TT_NGUON, e)
                self._lan_loi = 0
                cho = self.nhip_ke_tiep(chu_ky)
                self.trang_thai["thu_lai_luc"] = None
            except asyncio.CancelledError:
                raise
            except Exception as e:  # vòng nền KHÔNG được chết vì một lần đồng bộ hỏng
                self._lan_loi += 1
                cho = min(chu_ky * 2 ** self._lan_loi, self.TRAN_LUI_GIAY)
                if isinstance(e, LoiGit):
                    tin = str(e)
                    if e.cho_den:
                        cho = max(cho, e.cho_den - time.time() + 1)
                else:
                    log.exception("Lỗi nội bộ khi đồng bộ nguồn git")
                    tin = f"lỗi nội bộ: {type(e).__name__}"
                self.trang_thai["loi"] = self._che(tin)
                self.trang_thai["thu_lai_luc"] = iso(time.time() + cho)
                log.warning("Đồng bộ nguồn git hỏng (lần %d liên tiếp), thử lại sau %.0f s: %s",
                            self._lan_loi, cho, self._che(tin))
            try:
                self._luu_trang_thai()
            except OSError as e:
                log.warning("Không ghi được %s: %s", TEP_TRANG_THAI, e)
            await self._cho(cho)

    def bat_dau(self) -> None:
        self._tac_vu = asyncio.create_task(self.chay_nen(), name="web2api-nguon-git")

    async def dung(self) -> None:
        if self._tac_vu is not None:
            self._tac_vu.cancel()
            try:
                await self._tac_vu
            except asyncio.CancelledError:
                pass
            self._tac_vu = None
        if self._khach is not None:
            await self._khach.aclose()
            self._khach = None
