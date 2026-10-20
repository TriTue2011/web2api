"""Module `camera` (NV167) — chụp ảnh, xem khung và phát tiếng qua loa camera.

Nguồn port (ghi theo LUẬT PORT của chủ máy):
- ``/opt/claude-c2a/chatgpt2api/services/camera_nha.py`` (bản sao
  ``~/workspace/c2a-nv146``, commit ``d879b034fa2154810c0b6a3e33e79a14e7ff3696``):
  ``che_bi_mat`` (hàm dưới, chép chạy thật), ``kich_thuoc_jpeg`` (hàm dưới, chép
  chạy thật), khớp tên theo nguyên cụm → điểm từ riêng (``_slug``/``_tim`` dưới).
- ``/opt/claude-c2a/chatgpt2api/services/loa_camera.py`` (cùng commit): toàn bộ
  giao thức kênh nói đặt ở dịch vụ phụ ``dich-vu/camera/`` (xem tệp đó).
- ``/opt/claude-c2a/chatgpt2api/api/camera.py``: ý tưởng hợp đồng snapshot.

KHÔNG port (lý do trong báo cáo «Phần KHÔNG port và vì sao»): vòng quét
chuyển động YOLO/Frigate của ``canh_camera_nha.py`` (YOLO là NV168), STT wakeword
của ``ve_tinh_camera.py``, ``hik_noi.py`` (SDK hãng), TTS ``noi()`` của c2a.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import unicodedata
import time
from datetime import datetime, timezone

import httpx

from app import tu_xa

log = logging.getLogger("web2api.camera")

#: Kiểu bản ghi camera hợp lệ — tương đương KIEU_HOP_LE của c2a.
_KIEU_HOP_LE = ("go2rtc", "rtsp")
#: Số cảnh báo chuyển động giữ gần nhất trong RAM (không lưu đĩa theo spec).
_TRAN_CANH_BAO = 20


# ── chép từ camera_nha.py của c2a (chạy thật, đã đo trên máy thật) ──────────

def che_bi_mat(url: str) -> str:
    """Bỏ mật khẩu khỏi URL trước khi cho ai đó nhìn thấy.

    ``rtsp://admin:MatKhau@10.0.0.5/stream`` → ``rtsp://admin:***@10.0.0.5/stream``
    """
    if not url:
        return ""
    try:
        p = urlsplit(url)
    except ValueError:
        return re.sub(r"//[^/@]*@", "//***@", url)
    if not p.password:
        return url
    chu = f"{p.username or ''}:***@{p.hostname or ''}"
    if p.port:
        chu += f":{p.port}"
    return urlunsplit((p.scheme, chu, p.path, p.query, p.fragment))


def kich_thuoc_jpeg(jpeg: bytes) -> tuple[int, int] | None:
    """Đọc rộng×cao từ header JPEG. Không nhận ra thì trả ``None``."""
    i, n = 2, len(jpeg)
    if jpeg[:2] != b"\xff\xd8":
        return None
    while i + 9 < n:
        if jpeg[i] != 0xFF:
            i += 1
            continue
        dau = jpeg[i + 1]
        # SOF0…SOF15, trừ DHT(C4), JPG(C8), DAC(CC) — chúng không mang kích thước.
        if 0xC0 <= dau <= 0xCF and dau not in (0xC4, 0xC8, 0xCC):
            cao = int.from_bytes(jpeg[i + 5:i + 7], "big")
            rong = int.from_bytes(jpeg[i + 7:i + 9], "big")
            return (rong, cao) if rong and cao else None
        if dau in (0xD8, 0x01) or 0xD0 <= dau <= 0xD7:
            i += 2
            continue
        i += 2 + int.from_bytes(jpeg[i + 2:i + 4], "big")
    return None


def _slug(ten: str) -> str:
    """Chuẩn hoá tên camera thành mảnh id (bỏ dấu, chỉ [a-z0-9-])."""
    ten = unicodedata.normalize("NFD", str(ten or "")).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ten.lower()).strip("-")
    return slug or "camera"


def _diem_tu_rieng(ten: str, ten_that: str) -> float:
    """Điểm khớp từ riêng (camera_nha._khop của c2a): từ đầy đủ = 1, từ con = 0.5."""
    tu = set(re.findall(r"[a-z0-9]+", _slug(ten)))
    tu_that = set(re.findall(r"[a-z0-9]+", _slug(ten_that)))
    diem = 0.0
    for t in tu:
        if t in tu_that:
            diem += 1.0
        elif any(t in x for x in tu_that):
            diem += 0.5
    return diem / max(len(tu), 1)


def _tim(ten: str, danh_sach: list[dict]) -> tuple[dict, bool]:
    """Khớp tên theo chiến lược của c2a: nguyên cụm trước, điểm từ riêng sau.

    Trả (bản ghi, mập_mờ). Một camera thì nới điều kiện (nhà một camera gọi
    «camera» là đủ). Mập mờ thì ném LoiCamera yêu cầu nói rõ hơn.
    """
    danh_sach = danh_sach or []
    ten_goc = str(ten or "").strip()
    if not ten_goc:
        if len(danh_sach) == 1:
            return danh_sach[0], False
        raise LoiCamera("chưa nói rõ camera nào")
    go = _slug(ten_goc)
    ung = [c for c in danh_sach if _slug(c.get("name") or "") == go]
    if len(ung) == 1:
        return ung[0], False
    tinh = [(_diem_tu_rieng(ten_goc, c.get("name") or ""), c) for c in danh_sach]
    tinh = [(d, c) for d, c in tinh if d >= 1.0]
    if len(danh_sach) == 1 and not tinh:
        return danh_sach[0], False
    if len(tinh) == 1:
        return tinh[0][1], False
    if len(tinh) > 1:
        ten_s = ", ".join(c.get("name") or "?" for _, c in tinh)
        raise LoiCamera(f"nhiều camera khớp với «{ten_goc}»: {ten_s} — nói rõ hơn nhé")
    raise LoiCamera(f"không thấy camera «{ten_goc}»")


class LoiCamera(RuntimeError):
    """Lỗi đã sẵn sàng đọc cho người dùng (thông điệp có thể chứa tên camera nhưng
    KHÔNG bao giờ chứa URL/mật khẩu — dùng che_bi_mat trước khi dựng câu)."""


from urllib.parse import urlsplit, urlunsplit  # noqa: E402 — để che_bi_mat chép nguyên bản không cần đổi thứ tự


class CameraModule:
    """Module camera: mỗi camera một thực thể đọc; thực thể ghi chỉ khi bật loa."""

    tien_to = "cam."

    def __init__(self, manifest: dict) -> None:
        self.manifest = manifest
        self._danh_sach: list[dict] = []
        self._canh_bao: list[dict] = []
        self._kich_hoat = False
        self._loi: str = ""

    # ── vòng đời ──

    def nap(self, cau_hinh: dict) -> None:
        cau_hinh = cau_hinh or {}
        self._cau_hinh = dict(cau_hinh)
        try:
            danh_sach = json.loads(str(cau_hinh.get("danh_sach_camera") or "[]"))
        except ValueError as exc:
            self._loi = f"danh_sach_camera không phải JSON hợp lệ ({exc})"
            self._danh_sach = []
            self._kich_hoat = False
            return
        ds = [d for d in (danh_sach or []) if isinstance(d, dict)]
        ten_id: set[str] = set()
        for d in ds:
            d.setdefault("name", "")
            idm = _slug(d["name"]) or "camera"
            if idm in ten_id:
                self._loi = f"trùng tên camera «{d['name']}» (id «{idm}») — sửa cho khác nhau"
                self._danh_sach = []
                self._kich_hoat = False
                return
            ten_id.add(idm)
            d["_id"] = idm
        self._danh_sach = ds
        self._kich_hoat = True
        self._loi = ""

    def go(self, cau_hinh: dict) -> None:
        self.nap(cau_hinh)

    async def cai(self, cau_hinh: dict) -> dict:
        """Kiểm tra cấu hình khi lưu: JSON hợp lệ, bản ghi hợp lệ, URL qua tu_xa,
        khoá dịch vụ đủ dài nếu khai, gọi thử dịch vụ phụ nếu khai."""
        ket = {"ok": True, "loi": [], "canh_bao": []}
        cau_hinh = cau_hinh or {}
        try:
            ds = json.loads(str(cau_hinh.get("danh_sach_camera") or "[]"))
        except ValueError:
            return {"ok": False, "loi": ["danh_sach_camera không phải JSON hợp lệ."]}
        if not isinstance(ds, list):
            return {"ok": False, "loi": ["danh_sach_camera phải là một mảng JSON."]}
        ten_id: set[str] = set()
        for i, d in enumerate(ds):
            if not isinstance(d, dict):
                ket["loi"].append(f"danh_sach_camera[{i}] phải là một đối tượng.")
                continue
            ten = str(d.get("name") or "").strip()
            if not ten:
                ket["loi"].append(f"danh_sach_camera[{i}] thiếu name.")
                continue
            idm = _slug(ten)
            if idm in ten_id:
                ket["loi"].append(f"trùng tên camera «{ten}» (id «{idm}»).")
                continue
            ten_id.add(idm)
            kieu = str(d.get("kind") or "go2rtc")
            if kieu not in _KIEU_HOP_LE:
                ket["loi"].append(f"camera «{ten}»: kind phải là go2rtc hoặc rtsp.")
                continue
            if kieu == "go2rtc":
                base = str(d.get("base") or "")
                if not base:
                    ket["loi"].append(f"camera «{ten}»: thiếu base (địa chỉ go2rtc).")
                else:
                    try:
                        await tu_xa.kiem_url_lan(base)
                    except Exception as exc:
                        ket["loi"].append(f"camera «{ten}»: base không hợp lệ ({exc}).")
                if not str(d.get("src") or "").strip():
                    ket["loi"].append(f"camera «{ten}»: thiếu src (tên luồng).")
                for khoa in ("src_ai",):
                    val = str(d.get(khoa) or "").strip()
                    if val.lower().startswith(("http://", "https://")):
                        ket["canh_bao"].append(f"camera «{ten}»: {khoa} là URL http — luồng phụ nên là tên go2rtc hoặc rtsp://.")
            else:
                for khoa in ("url", "url_ai"):
                    val = str(d.get(khoa) or "").strip()
                    if khoa == "url" and not val:
                        ket["loi"].append(f"camera «{ten}»: thiếu url (URL RTSP).")
                    elif val:
                        err = await self._kiem_rtsp(val)
                        if err:
                            ket["loi"].append(f"camera «{ten}»: {khoa} {err}")
        dich_vu_url = str(cau_hinh.get("dich_vu_url") or "").strip()
        dich_vu_khoa = str(cau_hinh.get("dich_vu_khoa") or "")
        if dich_vu_url:
            try:
                await tu_xa.kiem_url_lan(dich_vu_url)
            except Exception as exc:
                ket["loi"].append(f"dich_vu_url không hợp lệ ({exc}).")
            if not dich_vu_khoa or len(dich_vu_khoa) < 24:
                ket["loi"].append("dich_vu_khoa phải ≥ 24 ký tự khi khai dich_vu_url.")
            elif ket["ok"] is not False:
                try:
                    r = await self._http_get(dich_vu_url.rstrip("/") + "/suc-khoe", timeout=5.0)
                    if r.get("ma") != 200:
                        ket["canh_bao"].append(f"dịch vụ phụ không trả /suc-khoe (mã {r.get('ma')}).")
                except Exception as exc:
                    ket["canh_bao"].append(f"không nối được dịch vụ phụ khi lưu ({type(exc).__name__}).")
        if str(cau_hinh.get("loa_bat") or "").lower() not in ("", "true", "false"):
            ket["loi"].append("loa_bat chỉ nhận 'true' hoặc 'false'.")
        if str(cau_hinh.get("cho_ai_xem") or "").lower() not in ("", "true", "false"):
            ket["loi"].append("cho_ai_xem chỉ nhận 'true' hoặc 'false'.")
        try:
            if cau_hinh.get("phat_toi_da_giay") not in (None, "") and float(cau_hinh.get("phat_toi_da_giay")) <= 0:
                ket["loi"].append("phat_toi_da_giay phải là số dương.")
        except (TypeError, ValueError):
            ket["loi"].append("phat_toi_da_giay phải là số.")
        if ket["loi"]:
            ket["ok"] = False
        return ket

    async def _kiem_rtsp(self, url: str) -> str | None:
        """Kiểm URL RTSP bằng tu_xa: scheme rtsp + host qua _dia_chi_bi_chan."""
        try:
            p = urlsplit(url)
        except ValueError as exc:
            return f"không phân tích được ({exc})."
        if p.scheme.lower() != "rtsp":
            return "phải bắt đầu bằng rtsp://."
        host = (p.hostname or "").strip("[]")
        if not host:
            return "thiếu địa chỉ máy chủ."
        try:
            tu_xa._dia_chi_bi_chan(host)
        except Exception as exc:
            return f"địa chỉ bị chặn ({exc})."
        return None

    # ── HTTP ra (giả được trong test) ──

    async def _http_get(self, url: str, headers: dict | None = None, timeout: float = 20.0) -> dict:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(url, headers=headers or {})
            return {"ma": r.status_code, "than": r.content}

    async def _http_post(self, url: str, than: dict, headers: dict | None = None, timeout: float = 30.0) -> dict:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(url, json=than, headers=headers or {})
            try:
                noi_dung = r.json()
            except ValueError:
                noi_dung = None
            return {"ma": r.status_code, "json": noi_dung}

    # ── thực thể ──

    def _loa_bat(self) -> bool:
        return str(self._cau_hinh_get("loa_bat") or "false").lower() == "true"

    def _cau_hinh_get(self, khoa: str):
        return (self._cau_hinh or {}).get(khoa)

    def thuc_the(self, loc: dict | None = None) -> list[dict]:
        if not self._kich_hoat:
            return []
        ds = list(self._danh_sach)
        ten = str((loc or {}).get("ten") or "").strip()
        if ten:
            try:
                cam, _ = _tim(ten, self._danh_sach)
                ds = [cam]
            except LoiCamera:
                return []
        kq: list[dict] = []
        for cam in ds:
            ten_cam = cam.get("name") or "camera"
            kq.append({
                "id": f"cam.{cam['_id']}",
                "ten": f"Camera {ten_cam}",
                "loai": "camera",
                "rui_ro_mac_dinh": "doc",
                "hanh_dong": [
                    {"ten": "chup", "mo_ta": "Chụp một khung ảnh của camera này"},
                    {"ten": "chup_cho_ai", "mo_ta": "Ảnh cho mô hình thị giác (luồng phụ, thu nhỏ mạnh)"},
                    {"ten": "canh_bao_moi_nhat", "mo_ta": "Danh sách cảnh báo chuyển động gần nhất"},
                ],
            })
            if self._loa_bat():
                kq.append({
                    "id": f"cam.phat_{cam['_id']}",
                    "ten": f"Loa camera {ten_cam}",
                    "loai": "camera_phat",
                    "rui_ro_mac_dinh": "ghi",
                    "hanh_dong": [
                        {"ten": "phat", "mo_ta": "Phát âm thanh (base64 WAV/MP3) ra loa — hành động GHI"},
                        {"ten": "bo_dam_mo", "mo_ta": "Mở kênh bộ đàm hai chiều — hành động GHI"},
                        {"ten": "bo_dam_them", "mo_ta": "Gửi tiếng alaw (base64) vào kênh bộ đàm"},
                        {"ten": "bo_dam_dong", "mo_ta": "Đóng kênh bộ đàm"},
                    ],
                })
        return kq

    def trang_thai(self, ids: list[str] | None = None) -> dict[str, dict]:
        tt: dict[str, dict] = {}
        for e in self.thuc_the():
            if ids is not None and e["id"] not in ids:
                continue
            tt[e["id"]] = {"ten": e["ten"], "trang_thai": "san_sang",
                           "co_canh_bao": len(self._canh_bao) > 0}
        return tt

    async def thuc_hien(self, id_thuc_the: str, hanh_dong: str, tham_so: dict | None = None) -> dict:
        tham_so = tham_so or {}
        if not self._kich_hoat:
            return {"ok": False, "ma": "chua_nap", "loi": self._loi or "module chưa nạp cấu hình"}
        cam = self._giai_id(id_thuc_the)
        if cam is None:
            return {"ok": False, "ma": "khong_tim_thay", "loi": "không tìm thấy camera"}
        ten_cam = cam.get("name") or "camera"
        if hanh_dong == "chup":
            return await self._lam_chup(ten_cam, cam, cho_ai=False)
        if hanh_dong == "chup_cho_ai":
            if str(self._cau_hinh_get("cho_ai_xem") or "false").lower() != "true":
                return {"ok": False, "ma": "bi_cam",
                        "loi": "chưa bật «cho_ai_xem» trong cấu hình — ảnh camera là dữ liệu nhạy cảm"}
            return await self._lam_chup(ten_cam, cam, cho_ai=True)
        if hanh_dong == "canh_bao_moi_nhat":
            return {"ok": True, "canh_bao": list(self._canh_bao)}
        if hanh_dong in ("phat", "bo_dam_mo", "bo_dam_them", "bo_dam_dong"):
            if not self._loa_bat():
                return {"ok": False, "ma": "bi_cam",
                        "loi": "loa camera đang tắt (mặc định tắt) — bật «loa_bat» trong cấu hình mới dùng được"}
            if id_thuc_the == f"cam.phat_{cam['_id']}":
                return await self._lam_ghi(ten_cam, cam, hanh_dong, tham_so)
            return {"ok": False, "ma": "sai_thuc_the", "loi": "hành động ghi phải gọi trên thực thể cam.phat_<tên>"}
        return {"ok": False, "ma": "khong_co_hanh_dong", "loi": f"không có hành động {hanh_dong}"}

    def _giai_id(self, id_thuc_the: str) -> dict | None:
        if not str(id_thuc_the).startswith("cam."):
            return None
        phan = str(id_thuc_the)[len("cam."):]
        if phan.startswith("phat_"):
            phan = phan[len("phat_"):]
        for cam in self._danh_sach:
            if cam.get("_id") == phan:
                return cam
        return None

    def _ban_ghi_gui(self, ten_cam: str, cam: dict) -> dict:
        """Bản ghi gửi cho dịch vụ phụ: chỉ trường cần thiết, thêm name."""
        giu = {"name": ten_cam, "kind": cam.get("kind")}
        for k in ("base", "src", "src_ai", "url", "url_ai", "username", "password", "cho_loa"):
            if cam.get(k) is not None:
                giu[k] = cam[k]
        return giu

    def _dich_vu(self) -> tuple[str, str] | tuple[None, None]:
        url = str(self._cau_hinh_get("dich_vu_url") or "").strip()
        khoa = str(self._cau_hinh_get("dich_vu_khoa") or "")
        if not url or not khoa:
            return None, None
        return url.rstrip("/"), khoa

    async def _lam_chup(self, ten_cam: str, cam: dict, cho_ai: bool) -> dict:
        url, khoa = self._dich_vu()
        try:
            if url:
                kq = await self._http_post(f"{url}/chup",
                                           {"cam": self._ban_ghi_gui(ten_cam, cam), "cho_ai": cho_ai},
                                           headers={"Authorization": f"Bearer {khoa}"}, timeout=30.0)
                if kq.get("ma") != 200 or not isinstance(kq.get("json"), dict):
                    raise LoiCamera(f"dịch vụ phụ trả mã {kq.get('ma')}")
                ket = kq["json"]
                if not ket.get("ok"):
                    raise LoiCamera(str(ket.get("loi") or "dịch vụ phụ chụp không được"))
                jpeg = base64.b64decode(str(ket.get("anh_b64") or ""), validate=True)
            else:
                # Đường dự phòng không dịch vụ phụ: go2rtc qua HTTP trực tiếp
                # (module chính không có ffmpeg nên RTSP khai thẳng cần dịch vụ phụ).
                if str(cam.get("kind")) != "go2rtc":
                    raise LoiCamera("chưa cấu hình dịch vụ phụ — camera RTSP khai thẳng cần dich-vu/camera")
                jpeg = await self._chup_go2rtc_truc_tiep(ten_cam, cam)
        except LoiCamera as exc:
            return {"ok": False, "ma": "chup_loi", "loi": str(exc)}
        except Exception as exc:  # noqa: BLE001 — không lộ chi tiết nội bộ
            log.warning("camera chup loi (%s)", type(exc).__name__)
            return {"ok": False, "ma": "chup_loi", "loi": "chụp ảnh không được"}
        kt = kich_thuoc_jpeg(jpeg)
        return {"ok": True, "trang_thai_moi": {
            "anh_base64": base64.b64encode(jpeg).decode("ascii"),
            "dinh_dang": "image/jpeg",
            "kich_thuoc": list(kt) if kt else None,
        }}

    async def _chup_go2rtc_truc_tiep(self, ten_cam: str, cam: dict) -> bytes:
        base = str(cam.get("base") or "").rstrip("/")
        src = str(cam.get("src") or "")
        headers: dict[str, str] = {}
        if cam.get("username"):
            mkhau = f"{cam['username']}:{cam.get('password') or ''}"
            headers["Authorization"] = "Basic " + base64.b64encode(mkhau.encode()).decode()
        kq = await self._http_get(f"{base}/api/frame.jpeg?src={src}", headers=headers, timeout=20.0)
        if kq.get("ma") == 404:
            raise LoiCamera(f"go2rtc không có luồng tên '{src}'")
        if kq.get("ma") in (401, 403):
            raise LoiCamera("go2rtc từ chối — sai tài khoản hoặc mật khẩu")
        if kq.get("ma") != 200 or not kq.get("than"):
            raise LoiCamera(f"go2rtc trả về mã {kq.get('ma')}")
        return kq["than"]

    async def _lam_ghi(self, ten_cam: str, cam: dict, hanh_dong: str, tham_so: dict) -> dict:
        url, khoa = self._dich_vu()
        if not url:
            return {"ok": False, "ma": "chua_cau_hinh",
                    "loi": "chưa cấu hình dịch vụ phụ (dich_vu_url) — phát/bộ đàm chỉ chạy qua dịch vụ phụ"}
        headers = {"Authorization": f"Bearer {khoa}"}
        than = {"cam": self._ban_ghi_gui(ten_cam, cam)}
        if hanh_dong == "phat":
            am = tham_so.get("am_thanh_b64") or tham_so.get("am_thanh_base64")
            if not isinstance(am, str) or not am:
                return {"ok": False, "ma": "thieu_tham_so", "loi": "thiếu am_thanh_b64 (âm thanh WAV/MP3 mã base64)"}
            toi_da = tham_so.get("toi_da_giay")
            try:
                toi_da_m = min(float(self._cau_hinh_get("phat_toi_da_giay") or 10),
                               float(toi_da) if toi_da is not None else 10.0)
            except (TypeError, ValueError):
                return {"ok": False, "ma": "thieu_tham_so", "loi": "toi_da_giay phải là số"}
            than.update({"am_thanh_b64": am, "toi_da_giay": toi_da_m})
            duong = "/phat"
        elif hanh_dong == "bo_dam_mo":
            duong = "/bo_dam/mo"
        elif hanh_dong == "bo_dam_them":
            phien = tham_so.get("phien")
            alaw = tham_so.get("alaw_b64")
            if not phien or not isinstance(alaw, str):
                return {"ok": False, "ma": "thieu_tham_so", "loi": "thiếu phien hoặc alaw_b64"}
            than.update({"phien": phien, "alaw_b64": alaw})
            duong = "/bo_dam/them"
        else:
            phien = tham_so.get("phien")
            if not phien:
                return {"ok": False, "ma": "thieu_tham_so", "loi": "thiếu phien"}
            than.update({"phien": phien})
            duong = "/bo_dam/dong"
        try:
            kq = await self._http_post(f"{url}{duong}", than, headers=headers, timeout=40.0)
        except Exception as exc:  # noqa: BLE001
            log.warning("camera ghi loi (%s)", type(exc).__name__)
            return {"ok": False, "ma": "loi_dich_vu", "loi": "không nối được dịch vụ phụ"}
        noi_dung = kq.get("json") if isinstance(kq.get("json"), dict) else {}
        if kq.get("ma") != 200 or not noi_dung.get("ok"):
            loi = str(noi_dung.get("loi") or "dịch vụ phụ thực hiện không được")
            return {"ok": False, "ma": "loi_dich_vu", "loi": loi[:200]}
        noi_dung["ok"] = True
        return noi_dung

    # ── sức khoẻ / webhook ──

    def suc_khoe(self) -> dict:
        url, khoa = self._dich_vu()
        return {
            "ok": self._kich_hoat,
            "loi": self._loi or None,
            "so_camera": len(self._danh_sach),
            "dich_vu_phu": "da_khai" if url else "chua_khai",
            "loa_bat": self._loa_bat(),
            "so_canh_bao_giu": len(self._canh_bao),
        }

    def su_kien_id(self, yc: dict) -> str:
        """Trích id sự kiện Frigate để lõi chống phát lại (payload đã xác thực)."""
        try:
            than = yc.get("than") if isinstance(yc, dict) else None
            after = (than or {}).get("after") if isinstance(than, dict) else None
            mid = (after or {}).get("id") if isinstance(after, dict) else None
            return f"frigate-{mid}" if isinstance(mid, str) and mid.strip() else ""
        except Exception as exc:  # noqa: BLE001 — đã kiểm toán
            log.warning("camera: trích id sự kiện webhook hỏng (%s)", type(exc).__name__)
            return ""

    async def webhook(self, yc: dict) -> dict:
        """Nhận cảnh báo chuyển động từ Frigate qua cửa webhook chung của lõi.

        Frigate gọi /api/webhook/camera/<ma_webhook> với JSON có ``after.id``.
        Chỉ giữ bản tóm tắt (id, loại, tên camera, thời điểm) — KHÔNG giữ ảnh
        hay URL; tối đa _TRAN_CANH_BAO sự kiện gần nhất trong RAM.
        """
        try:
            than = yc.get("than") if isinstance(yc, dict) else None
            if not isinstance(than, dict):
                return {"ok": True}
            after = than.get("after") if isinstance(than.get("after"), dict) else {}
            label = str(after.get("label") or than.get("type") or "chuyen_dong")
            ten_cam = str(after.get("camera") or "")
            self._canh_bao.append({
                "id": str(after.get("id") or ""),
                "loai": label,
                "camera": ten_cam,
                "luc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            })
            del self._canh_bao[:-_TRAN_CANH_BAO]
        except Exception as exc:  # noqa: BLE001 — đã kiểm toán: webhook không được sập
            log.warning("camera: webhook loi_khong_ngu (%s)", type(exc).__name__)
        return {"ok": True}
