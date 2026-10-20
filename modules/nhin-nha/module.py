"""Module nhin-nha — thị giác nhà: YOLO + nhận diện khuôn mặt qua dịch vụ ML phụ.

Kiến trúc: phần ML nặng (YOLO, dò mặt SCRFD, vector ArcFace) chạy ở dịch vụ phụ
``dich-vu/nhin-nha/`` (hoặc nút GPU xa trong nhà); module này chỉ là client +
sổ khuôn mặt (thuần Python, không numpy). Dữ liệu sinh trắc (embedding, tên
gắn mặt) chỉ nằm trong kho riêng của module — mã hoá khi nghỉ nếu lõi có khoá
chính, không thì 0600 — KHÔNG bao giờ ra log/phản hồi/suc_khoe, KHÔNG ra
khỏi lõi/máy nhà (địa chỉ dịch vụ chỉ nhận loopback + mạng nhà riêng).

Tận dụng từ c2a (commit d879b03, bản sao ~/workspace/c2a-nv146):
- `so_mat.py`: port logic sổ khuôn mặt từ `services/so_mat_nha.py` (cách nhận
  giống IRIS, ngưỡng tự chỉnh theo dữ liệu nhà, phép chấm thuần Python thay
  numpy) và hằng `NET_TOI_THIEU` từ `services/khuon_mat_nha.py`.
- Dịch vụ phụ `dich-vu/nhin-nha/`: port gần nguyên văn `services/yolo_nha.py`
  (MODELS + sha256, TEN_VIET, letterbox, giai_ma, vi_tri) và phần dò+vector
  của `services/khuon_mat_nha.py` (SCRFD/ArcFace của insightface 1.0.1) +
  mô hình «chạy xa/lùi CPU» của `services/onnx_xa.py`.
- KHÔNG port: luồng canh camera liên tục, gom cụm «mặt lạ» tự động theo lượt
  (don_mat_la/bao_tri), hỏi tên vòng lặp, lưu ảnh cắt mặt để tính lại khi đổi
  bộ model — chỉ nhận dạng theo yêu cầu; đổi bộ model thì sổ phải dạy lại.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import ipaddress
import json
import logging
import socket
import threading
import time
import uuid
from urllib.parse import urlsplit

import httpx

from . import so_mat

log = logging.getLogger("web2api.module.nhin_nha")

#: Khoá Bearer <redacted> vụ đang dùng (chỉ thêm) — bộ lọc log bỏ mọi dòng của thư viện
#: HTTP có chứa khoá (phòng khi khoá lọt vào URL ở đường kiểm thử).
_KHOA_DANG_DUNG: set[str] = set()
_LOGGER_HTTP = ("httpx", "httpcore", "httpcore.connection",
                "httpcore.http11", "httpcore.http2", "httpcore.proxy")


class _ChanRoKhoa(logging.Filter):
    """Bỏ dòng log chứa khoá dịch vụ. Cài MỘT lần lên logger của thư viện HTTP."""
    _nhin_nha_loc = True

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            noi_dung = record.getMessage()
        except Exception:
            return False
        return not any(k in noi_dung for k in _KHOA_DANG_DUNG)


def _cai_bo_loc_log() -> None:
    for ten in _LOGGER_HTTP:
        lg = logging.getLogger(ten)
        for f in [f for f in lg.filters if getattr(f, "_nhin_nha_loc", False)]:
            lg.removeFilter(f)
        lg.addFilter(_ChanRoKhoa())


_cai_bo_loc_log()

#: Trần ảnh gửi dịch vụ (base64) — 14 MB ≈ 10,5 MB ảnh thật.
TRAN_ANH_B64 = 14_000_000
_HET_GIO = 60.0


# ── Kiểm địa chỉ dịch vụ: chỉ loopback + mạng nhà riêng (cấm IP công khai) ───
# Học khuôn của modules/van-phong (đã tham khảo app/tu_xa.py) nhưng CHẶT HƠN:
# van-phong cho phép IP công khai (office ở máy khác); dữ liệu sinh trắc KHÔNG
# được rời máy nhà nên nhin-nha chỉ nhận loopback + RFC1918.

def _dia_chi_bi_chan(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str | None:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if ip.is_link_local:
        return "link-local / metadata đám mây (169.254.0.0/16)"
    if ip.is_unspecified or ip.is_multicast or ip.is_reserved:
        return "địa chỉ không định tuyến"
    if ip.is_loopback or ip.is_private:
        return None
    return "địa chỉ công khai — dữ liệu sinh trắc không được rời máy nhà"


async def _kiem_url_dich_vu(url: str) -> str:
    s = str(url or "").strip().rstrip("/")
    phan = urlsplit(s)
    if phan.scheme not in ("http", "https") or not phan.hostname:
        raise ValueError("dich_vu_url phải dạng http(s)://<máy>[:cổng]")
    if phan.username or phan.password:
        raise ValueError("dich_vu_url không được chứa tài khoản/mật khẩu")
    try:
        ds = [ipaddress.ip_address(phan.hostname)]
    except ValueError:
        try:
            thong_tin = await asyncio.get_running_loop().getaddrinfo(
                phan.hostname, phan.port or 0, type=socket.SOCK_STREAM)
        except OSError as e:
            raise ValueError(f"không phân giải được tên máy {phan.hostname!r}: {e}") from None
        ds = list({ipaddress.ip_address(x[4][0]) for x in thong_tin})
    for ip in ds:
        ly_do = _dia_chi_bi_chan(ip)
        if ly_do:
            raise ValueError(f"dich_vu_url trỏ vào {ip} — bị chặn: {ly_do} (chống SSRF)")
    return s


def _bat(gia_tri) -> bool:
    return str(gia_tri or "").strip().lower() in ("1", "bat", "true", "yes", "on")


def _ma() -> str:
    return uuid.uuid4().hex[:12]


class LoiDichVu(Exception):
    pass


class Module:
    """Module thiet_bi C2: mắt nhà + sổ khuôn mặt."""

    def __init__(self, manifest: dict, _goc_kiem_thu: str | None = None):
        self.manifest = manifest
        self.tien_to = manifest.get("tien_to", "nhin_nha")
        self._goc_kiem_thu = _goc_kiem_thu  # test nội bộ, không đọc từ cấu hình người dùng
        self.kho = None  # lõi gắn tay cầm kho riêng (app/so_module.py); test tự gắn
        self._nap_mac_dinh()

    def _nap_mac_dinh(self) -> None:
        self._nhin_nha_bat = False
        self._nhan_mat_bat = False
        self._dich_vu_url = "http://127.0.0.1:8703"
        self._khoa_dich_vu = ""
        self._nguong_co_the = 40.0
        self._nguong_chac = 55.0
        self._lock = threading.RLock()

    # ── kho ────────────────────────────────────────────────────────────────

    def _kho(self):
        k = self.kho
        if k is not None:
            return k
        from app.ham_khoa import KhoRieng
        return KhoRieng(self.manifest.get("id", "nhin-nha"))

    def _doc_so(self) -> dict:
        du = self._kho().doc("so") or {}
        return {"nguoi": du.get("nguoi", []), "mat": du.get("mat", []),
                "mat_la": du.get("mat_la", []), "su_kien": du.get("su_kien", [])}

    def _luu_so(self, so: dict) -> None:
        so["su_kien"] = (so.get("su_kien") or [])[-so_mat.TRAN_SU_KIEN:]
        self._kho().luu("so", {"nguoi": so.get("nguoi", []), "mat": so.get("mat", []),
                               "mat_la": so.get("mat_la", []), "su_kien": so["su_kien"]})

    # ── cấu hình ───────────────────────────────────────────────────────────

    def nap(self, cau_hinh: dict) -> None:
        cau_hinh = cau_hinh or {}
        self._nhin_nha_bat = _bat(cau_hinh.get("nhin_nha_bat"))
        self._nhan_mat_bat = _bat(cau_hinh.get("nhan_mat_bat"))
        self._dich_vu_url = str(cau_hinh.get("dich_vu_url") or "http://127.0.0.1:8703"
                                ).strip().rstrip("/") or "http://127.0.0.1:8703"
        self._khoa_dich_vu = str(cau_hinh.get("khoa_dich_vu") or "")
        try:
            self._nguong_co_the = max(10.0, min(95.0, float(cau_hinh.get("nguong_co_the", 40))))
            self._nguong_chac = max(10.0, min(99.0, float(cau_hinh.get("nguong_chac", 55))))
        except (TypeError, ValueError):
            self._nguong_co_the, self._nguong_chac = 40.0, 55.0
        if self._khoa_dich_vu:
            _KHOA_DANG_DUNG.add(self._khoa_dich_vu)

    async def cai(self, cau_hinh: dict) -> dict:
        cau_hinh = dict(cau_hinh or {})
        url = cau_hinh.get("dich_vu_url")
        if self._goc_kiem_thu and not url:
            url = self._goc_kiem_thu
        try:
            url_sach = await _kiem_url_dich_vu(url or "")
        except ValueError as e:
            return {"ok": False, "ket_qua_tung_khoa": [], "loi": str(e), "cau_hinh_luu": {}}
        khoa = str(cau_hinh.get("khoa_dich_vu") or "").strip()
        bat = _bat(cau_hinh.get("nhin_nha_bat"))
        if bat and len(khoa) < 24:
            return {"ok": False, "ket_qua_tung_khoa": [],
                    "loi": "khoa_dich_vu phải ≥ 24 ký tự (xem mo_ta trường này).",
                    "cau_hinh_luu": {}}
        nhan_mat_moi = _bat(cau_hinh.get("nhan_mat_bat"))
        ket_qua = [{"khoa": f"…{khoa[-4:]}" if khoa else "…", "ok": True}] if bat else []
        if bat:
            try:
                async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as c:
                    r = await c.get(f"{url_sach}/suc-khoe",
                                    headers={"Authorization": f"Bearer {khoa}"})
            except Exception as e:
                return {"ok": False, "ket_qua_tung_khoa": [],
                        "loi": f"Không gọi được dịch vụ ({type(e).__name__}).",
                        "cau_hinh_luu": {}}
            if r.status_code == 401:
                return {"ok": False, "ket_qua_tung_khoa": [],
                        "loi": "Dịch vụ từ chối khoá (sai khoá).", "cau_hinh_luu": {}}
            if r.status_code != 200:
                return {"ok": False, "ket_qua_tung_khoa": [],
                        "loi": f"Dịch vụ trả mã {r.status_code}.", "cau_hinh_luu": {}}
            try:
                tt = r.json()
            except Exception:
                return {"ok": False, "ket_qua_tung_khoa": [],
                        "loi": "Dịch vụ trả thân không phải JSON.", "cau_hinh_luu": {}}
            if not (isinstance(tt, dict) and tt.get("ok")):
                return {"ok": False, "ket_qua_tung_khoa": [],
                        "loi": "Dịch vụ chưa sẵn sàng.", "cau_hinh_luu": {}}
        if khoa:
            _KHOA_DANG_DUNG.add(khoa)
        # Nhận diện khuôn mặt là dữ liệu sinh trắc: bật phải có nhật ký rõ ràng.
        # (Đường /api/modules/nhin-nha/cai vốn chỉ quản trị gọi được.)
        if nhan_mat_moi and not self._nhan_mat_bat:
            from app import nhat_ky
            nhat_ky.ghi("nhin-nha", "canh_bao",
                        "Bật nhận diện khuôn mặt (nhan_mat_bat=1) qua cài đặt module")
        self.nap({**cau_hinh, "dich_vu_url": url_sach})
        return {"ok": True, "ket_qua_tung_khoa": ket_qua, "loi": None,
                "cau_hinh_luu": {"nhin_nha_bat": "1" if bat else "0",
                                 "nhan_mat_bat": "1" if nhan_mat_moi else "0",
                                 "dich_vu_url": url_sach,
                                 "khoa_dich_vu": khoa,
                                 "nguong_co_the": str(self._nguong_co_the),
                                 "nguong_chac": str(self._nguong_chac)}}

    async def suc_khoe(self) -> dict:
        """Không gọi mạng. Không chứa bí mật."""
        with self._lock:
            so = self._doc_so()
        return {"ok": True, "bat": self._nhin_nha_bat,
                "nhan_mat_bat": self._nhan_mat_bat,
                "so_nguoi": len(so["nguoi"]), "so_mat_la": len(so["mat_la"])}

    async def dung(self) -> None:
        pass

    # ── gọi dịch vụ ML ─────────────────────────────────────────────────────

    async def _goi_dich_vu(self, duong: str, than: dict) -> dict:
        if not self._nhin_nha_bat:
            raise LoiDichVu("chua_bat")
        if not self._khoa_dich_vu:
            raise LoiDichVu("chua_cau_hinh_khoa")
        try:
            async with httpx.AsyncClient(timeout=_HET_GIO, follow_redirects=False) as c:
                r = await c.post(f"{self._dich_vu_url}{duong}", json=than,
                                 headers={"Authorization": f"Bearer {self._khoa_dich_vu}"})
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            raise LoiDichVu(f"mang_loi:{type(e).__name__}") from None
        except Exception as e:
            raise LoiDichVu(f"loi_goi:{type(e).__name__}") from None
        if r.status_code == 401:
            raise LoiDichVu("sai_khoa")
        if r.status_code == 413:
            raise LoiDichVu("anh_qua_lon")
        if r.status_code != 200:
            raise LoiDichVu(f"ma_{r.status_code}")
        try:
            return r.json()
        except Exception as e:
            raise LoiDichVu("than_sai") from None

    async def _quet(self, anh_b64: str, nhan_mat: bool) -> dict:
        if len(str(anh_b64 or "")) > TRAN_ANH_B64:
            raise LoiDichVu("anh_qua_lon")
        try:
            base64.b64decode(str(anh_b64 or ""), validate=True)
        except (binascii.Error, ValueError):
            raise LoiDichVu("anh_sai")
        return await self._goi_dich_vu("/quet", {"anh_b64": str(anh_b64),
                                                "nhan_mat": bool(nhan_mat)})

    # ── chuẩn thiet_bi ─────────────────────────────────────────────────────

    def thuc_the(self, loc=None) -> list[dict]:
        t = self.tien_to
        return [
            {"id": f"{t}.mat", "ten": "Mắt nhà", "loai": "camera",
             "tinh_nang": ["quet_anh", "trang_thai_mo_hinh"], "rui_ro_mac_dinh": "doc"},
            {"id": f"{t}.so_mat", "ten": "Sổ khuôn mặt", "loai": "cam_bien",
             "tinh_nang": ["danh_sach_nguoi", "day_mat", "doi_ten", "xoa_nguoi",
                           "xoa_tat_ca", "danh_sach_mat_la", "dat_ten_mat_la",
                           "bo_qua_mat_la", "su_kien_gan"],
             "rui_ro_mac_dinh": "nguy_hiem"},
            {"id": f"{t}.nhan_dien", "ten": "Nhận diện khuôn mặt", "loai": "cam_bien",
             "tinh_nang": ["nhan_mat_trong_anh"], "rui_ro_mac_dinh": "nguy_hiem"},
        ]

    def trang_thai(self, ids: list[str]) -> dict:
        with self._lock:
            so = self._doc_so()
        ra = {}
        for i in ids or []:
            if i == f"{self.tien_to}.mat":
                ra[i] = {"bat": self._nhin_nha_bat,
                         "dich_vu": "da_noi" if self._khoa_dich_vu else "chua_cau_hinh",
                         "hop_le": True, "luc": None}
            elif i == f"{self.tien_to}.so_mat":
                ra[i] = {"so_nguoi": len(so["nguoi"]), "so_mat_la": len(so["mat_la"]),
                         "so_su_kien": len(so["su_kien"]), "hop_le": True, "luc": None}
            elif i == f"{self.tien_to}.nhan_dien":
                ra[i] = {"bat": self._nhan_mat_bat, "hop_le": True, "luc": None}
        return ra

    def _loi_that_bai(self, ma: str) -> dict:
        return {"ok": False, "loi": ma}

    async def thuc_hien(self, id: str, hanh_dong: str, tham_so: dict | None) -> dict:
        tham_so = tham_so or {}
        if not str(id or "").startswith(self.tien_to + "."):
            return self._loi_that_bai("404: không thấy thực thể")
        tt = {t["id"]: t for t in self.thuc_the()}
        if id not in tt:
            return self._loi_that_bai("404: không thấy thực thể")
        if hanh_dong not in tt[id]["tinh_nang"]:
            return self._loi_that_bai("400: hành động không hỗ trợ")
        if not self._nhin_nha_bat:
            return self._loi_that_bai("chua_bat")
        try:
            if id == f"{self.tien_to}.mat":
                return await self._hanh_dong_mat(hanh_dong, tham_so)
            if id == f"{self.tien_to}.so_mat":
                return await self._hanh_dong_so_mat(hanh_dong, tham_so)
            return await self._hanh_dong_nhan_dien(hanh_dong, tham_so)
        except LoiDichVu as e:
            return self._loi_that_bai(str(e))
        except Exception as e:
            log.warning("nhin-nha: lỗi không xác định (%s)", type(e).__name__)
            return self._loi_that_bai("loi_noi_bo")

    # ── thực thể mat ──────────────────────────────────────────────────────

    async def _hanh_dong_mat(self, hanh_dong: str, tham_so: dict) -> dict:
        if hanh_dong == "quet_anh":
            kq = await self._quet(tham_so.get("anh_b64", ""), nhan_mat=False)
            return {"ok": True, "trang_thai_moi": {
                "vat_the": kq.get("vat_the", []), "nguon_ngoai": True}}
        if hanh_dong == "trang_thai_mo_hinh":
            kq = await self._goi_dich_vu("/suc-khoe", {})
            return {"ok": True, "trang_thai_moi": {
                "yolo": kq.get("yolo", {}), "khuon_mat": kq.get("khuon_mat", {})}}
        return self._loi_that_bai("400: hành động không hỗ trợ")

    # ── sổ khuôn mặt ───────────────────────────────────────────────────────

    def _mau_tham_chieu(self, so: dict) -> list[dict]:
        nguoi_ten = {n["id"]: n["ten"] for n in so["nguoi"]}
        mau = []
        for m in so["mat"]:
            v = so_mat.vec_tu_bytes(base64.b64decode(m["vec_b64"]))
            if v is not None:
                mau.append({"nguoi_id": m["nguoi_id"],
                            "ten": nguoi_ten.get(m["nguoi_id"], "?"), "vec": v})
        return mau

    def _nguong(self, so: dict) -> tuple[float, float]:
        tran = so_mat.tinh_tran_khac(self._mau_tham_chieu(so))
        return so_mat.nguong_hieu_luc(self._nguong_co_the, self._nguong_chac, tran)

    def _ghi_su_kien(self, so: dict, loai: str, kq_khop: dict, hop,
                     vec_b64: str | None) -> None:
        so["su_kien"].append({
            "id": _ma(), "ts": time.time(), "camera": "", "loai": loai,
            "nguoi_id": kq_khop.get("nguoi_id"), "mat_la_id": None,
            "do_giong": kq_khop.get("do_giong", 0.0),
            "hop": [int(x) for x in hop] if hop else [],
            "vec_b64": vec_b64 or ""})

    def _gom_mat_la(self, so: dict, vec: tuple, vec_b64: str, hop) -> dict:
        """Xếp mặt lạ vào cụm giống nhất (≥ ngưỡng «có thể»), không có thì tạo."""
        co_the, _ = self._nguong(so)
        for c in so["mat_la"]:
            if c.get("bo_qua"):
                continue
            cv = so_mat.vec_tu_bytes(base64.b64decode(c["vec_b64"]))
            if cv is not None and so_mat.do_giong(vec, cv) >= co_the:
                c["so_lan"] = int(c.get("so_lan", 0)) + 1
                c["lan_cuoi"] = time.time()
                return c
        cum = {"id": _ma(), "vec_b64": vec_b64, "so_lan": 1,
               "lan_dau": time.time(), "lan_cuoi": time.time(),
               "da_hoi": 0, "bo_qua": False}
        so["mat_la"].append(cum)
        return cum

    def _nhan_dang_mat(self, so: dict, mat_dv: dict) -> dict:
        """Nhận một mặt từ dịch vụ: kiểm chất lượng → so sổ → ghi sự kiện."""
        hop = mat_dv.get("hop") or []
        nho = False
        try:
            nho = min(hop[2] - hop[0], hop[3] - hop[1]) < so_mat.MAT_NHO_NHAT
        except (TypeError, IndexError):
            pass
        vec = so_mat.vec_tu_bytes(base64.b64decode(mat_dv.get("vector_b64") or ""))
        if vec is None or not so_mat.chat_luong_ok(float(mat_dv.get("net", 0))):
            return {"hop": hop, "diem_do": mat_dv.get("diem_do"), "nho": nho,
                    "loai": "mo", "ten": None, "do_giong": 0.0}
        vec_b64 = mat_dv.get("vector_b64") or ""
        co_the, chac = self._nguong(so)
        kq = so_mat.khop(self._mau_tham_chieu(so), vec, co_the, chac)
        if kq["loai"] == "la":
            cum = self._gom_mat_la(so, vec, vec_b64, hop)
            self._ghi_su_kien(so, "la", kq, hop, vec_b64)
            kq = dict(kq, mat_la_id=cum["id"])
        else:
            self._ghi_su_kien(so, kq["loai"], kq, hop, vec_b64)
        return {"hop": hop, "diem_do": mat_dv.get("diem_do"), "nho": nho,
                "loai": kq["loai"], "ten": kq.get("ten"),
                "do_giong": kq.get("do_giong", 0.0)}

    async def _hanh_dong_so_mat(self, hanh_dong: str, tham_so: dict) -> dict:
        with self._lock:
            so = self._doc_so()

            if hanh_dong == "danh_sach_nguoi":
                return {"ok": True, "trang_thai_moi": {"nguoi": [
                    {"id": n["id"], "ten": n["ten"],
                     "so_mat": sum(1 for m in so["mat"] if m["nguoi_id"] == n["id"]),
                     "lan_cuoi": n.get("lan_cuoi")} for n in so["nguoi"]]}}

            if hanh_dong == "day_mat":
                ten = so_mat.kiem_ten(tham_so.get("ten"))
                if ten is None:
                    return self._loi_that_bai("ten_sai")
                try:
                    kq = await self._quet(tham_so.get("anh_b64", ""), nhan_mat=True)
                except LoiDichVu as e:
                    return self._loi_that_bai(str(e))
                mat_dv = kq.get("mat", [])
                if len(mat_dv) != 1:
                    return self._loi_that_bai("can_mot_mat")
                m = mat_dv[0]
                vec = so_mat.vec_tu_bytes(base64.b64decode(m.get("vector_b64") or ""))
                if vec is None or not so_mat.chat_luong_ok(float(m.get("net", 0))):
                    return self._loi_that_bai("mat_mo")
                co_the, chac = self._nguong(so)
                kq_khop = so_mat.khop(self._mau_tham_chieu(so), vec, co_the, chac)
                cu = next((n for n in so["nguoi"]
                           if so_mat.gon_ten(n["ten"]) == so_mat.gon_ten(ten)), None)
                if (kq_khop["loai"] == "quen" and kq_khop["nguoi_id"]
                        and (cu is None or kq_khop["nguoi_id"] != cu["id"])):
                    return self._loi_that_bai("giong_nguoi_khac")
                if cu is None:
                    cu = {"id": _ma(), "ten": ten, "tao_luc": time.time(),
                          "lan_cuoi": None}
                    so["nguoi"].append(cu)
                    moi = True
                else:
                    moi = False
                trung = False
                for em in so["mat"]:
                    if em["nguoi_id"] != cu["id"]:
                        continue
                    ev = so_mat.vec_tu_bytes(base64.b64decode(em["vec_b64"]))
                    if ev is not None and so_mat.do_giong(vec, ev) >= so_mat.NGUONG_TRUNG:
                        trung = True
                        break
                if not trung:
                    so["mat"].append({"id": _ma(), "nguoi_id": cu["id"],
                                     "vec_b64": m["vector_b64"], "diem_do": m.get("diem_do"),
                                     "tao_luc": time.time()})
                self._luu_so(so)
                return {"ok": True, "trang_thai_moi": {
                    "nguoi_id": cu["id"], "ten": cu["ten"], "nguoi_moi": moi,
                    "trung": trung,
                    "so_mat": sum(1 for x in so["mat"] if x["nguoi_id"] == cu["id"])}}

            if hanh_dong == "doi_ten":
                ten = so_mat.kiem_ten(tham_so.get("ten"))
                n = next((x for x in so["nguoi"] if x["id"] == tham_so.get("nguoi_id")), None)
                if ten is None or n is None:
                    return self._loi_that_bai("sai_tham_so")
                if any(so_mat.gon_ten(x["ten"]) == so_mat.gon_ten(ten) and x["id"] != n["id"]
                       for x in so["nguoi"]):
                    return self._loi_that_bai("ten_trung")
                n["ten"] = ten
                self._luu_so(so)
                return {"ok": True, "trang_thai_moi": {"nguoi_id": n["id"], "ten": ten}}

            if hanh_dong == "xoa_nguoi":
                nid = str(tham_so.get("nguoi_id") or "")
                truoc = len(so["nguoi"])
                so["nguoi"] = [n for n in so["nguoi"] if n["id"] != nid]
                so["mat"] = [m for m in so["mat"] if m["nguoi_id"] != nid]
                for e in so["su_kien"]:
                    if e.get("nguoi_id") == nid:
                        e["nguoi_id"] = None
                self._luu_so(so)
                return {"ok": True, "trang_thai_moi": {"da_xoa": truoc != len(so["nguoi"])}}

            if hanh_dong == "xoa_tat_ca":
                self._kho().xoa("so")
                return {"ok": True, "trang_thai_moi": {"da_xoa": True}}

            if hanh_dong == "danh_sach_mat_la":
                return {"ok": True, "trang_thai_moi": {"mat_la": [
                    {k: c.get(k) for k in ("id", "so_lan", "lan_dau", "lan_cuoi", "bo_qua")}
                    for c in so["mat_la"] if not c.get("bo_qua")]}}

            if hanh_dong == "dat_ten_mat_la":
                ten = so_mat.kiem_ten(tham_so.get("ten"))
                cum = next((c for c in so["mat_la"] if c["id"] == tham_so.get("cum_id")), None)
                if ten is None or cum is None:
                    return self._loi_that_bai("sai_tham_so")
                cu = next((n for n in so["nguoi"]
                           if so_mat.gon_ten(n["ten"]) == so_mat.gon_ten(ten)), None)
                if cu is None:
                    cu = {"id": _ma(), "ten": ten, "tao_luc": time.time(), "lan_cuoi": None}
                    so["nguoi"].append(cu)
                so["mat"].append({"id": _ma(), "nguoi_id": cu["id"],
                                 "vec_b64": cum["vec_b64"], "diem_do": 0.9,
                                 "tao_luc": time.time()})
                for e in so["su_kien"]:
                    if e.get("mat_la_id") == cum["id"]:
                        e["nguoi_id"] = cu["id"]
                        e["mat_la_id"] = None
                        e["loai"] = "quen"
                so["mat_la"] = [c for c in so["mat_la"] if c["id"] != cum["id"]]
                self._luu_so(so)
                return {"ok": True, "trang_thai_moi": {"nguoi_id": cu["id"], "ten": cu["ten"]}}

            if hanh_dong == "bo_qua_mat_la":
                cum = next((c for c in so["mat_la"] if c["id"] == tham_so.get("cum_id")), None)
                if cum is None:
                    return self._loi_that_bai("sai_tham_so")
                cum["bo_qua"] = True
                self._luu_so(so)
                return {"ok": True, "trang_thai_moi": {"cum_id": cum["id"]}}

            if hanh_dong == "su_kien_gan":
                sk = sorted(so["su_kien"], key=lambda e: -e["ts"])[:50]
                nguoi_ten = {n["id"]: n["ten"] for n in so["nguoi"]}
                return {"ok": True, "trang_thai_moi": {"su_kien": [
                    {k: e.get(k) for k in ("id", "ts", "camera", "loai", "nguoi_id",
                                          "mat_la_id", "do_giong", "hop")}
                    | ({"ten": nguoi_ten[e["nguoi_id"]]} if e.get("nguoi_id") in nguoi_ten else {})
                    for e in sk]}}

            return self._loi_that_bai("400: hành động không hỗ trợ")

    async def _hanh_dong_nhan_dien(self, hanh_dong: str, tham_so: dict) -> dict:
        if not self._nhan_mat_bat:
            # Nhận diện khuôn mặt mặc định TẮT — đóng khi lỗi.
            return self._loi_that_bai("chua_bat")
        if hanh_dong != "nhan_mat_trong_anh":
            return self._loi_that_bai("400: hành động không hỗ trợ")
        try:
            kq = await self._quet(tham_so.get("anh_b64", ""), nhan_mat=True)
        except LoiDichVu as e:
            return self._loi_that_bai(str(e))
        with self._lock:
            so = self._doc_so()
            ra = [self._nhan_dang_mat(so, m) for m in kq.get("mat", [])]
            for n in so["nguoi"]:
                if any(e.get("nguoi_id") == n["id"] for e in so["su_kien"]):
                    pass
            self._luu_so(so)
        # KHÔNG trả vector/embedding — chỉ tên + độ giống.
        return {"ok": True, "trang_thai_moi": {"mat": ra, "nguon_ngoai": True}}
