# -*- coding: utf-8 -*-
"""Module anh-ai — tạo/sửa ảnh AI qua 10 nhà cung cấp (port từ c2a, commit d879b03).

LUẬT PORT (chủ máy 19/10): c2a là kho của chính chủ máy → CHÉP mã đang chạy
thật, chỉ đổi lớp kết nối/khuôn module/xác thực; KHÔNG viết lại từ đầu.

Phần chép từ c2a (kho CHỈ ĐỌC /home/hatch/workspace/c2a-nv146):
- Điều phối gọi adapter (build_url/build_body/build_headers → POST → parse_response
  → normalize, xoay khoá khi 400/401/403/429) ←
  services/protocol/openai_v1_image_generations.py::_handle_adapter_image và
  services/protocol/openai_v1_image_edit.py::_handle_adapter_edit (gộp hai hàm
  gần-giống-nhau thành MỘT hàm _chay_anh; bỏ log_service/request_context/backend_router
  của c2a; prompt KHÔNG qua redact_text vì web2api không có privacy_gate — prompt đi
  thẳng tới nhà cung cấp như mọi module khác).
- Hàng rào ảnh cho đầu vào/ra ← services/image_guard.py (đã port riêng: kiem_anh.py).
- Chuẩn hoá/giới hạn ảnh ← services/image_utils.py (đã port riêng: tien_ich_anh.py).
- Hàng đợi tác vụ ← services/image_task_service.py (đã port riêng: hang_doi.py).
- Kho nhãn ảnh ← services/image_tags_service.py (đã port riêng: nhan_anh.py).
- 10 adapter nhà cung cấp ← services/image_providers/*.py (đã port riêng: bo_chuyen_doi/).

Phần KHÔNG port + lý do:
- services/gemini_watermark.py (strip_watermark_b64_items): gỡ/che dấu nước của nhà
  cung cấp là vùng điều khoản dịch vụ → KHÔNG port theo spec NV192; xem
  hoi-nv192-gemini-watermark.md (khuyến nghị «không làm»).
- services/image_service.py (thư viện ảnh/thumbnail/zip/xoá + đồng bộ cloud):
  ngoài phạm vi NV192 (chỉ tạo/sửa + hàng đợi + chặn ảnh xấu).
- api/ai.py phần ảnh (route FastAPI): lõi web2api đã có /v1/images/* → module.anh/anh_sua.
- veo_video.py: chỉ làm video, ngoài phạm vi.

Thiết kế bảo mật:
- Mọi khoá (api_key/api_keys) kiểu `bi_mat`: không vào log/phản hồi/suc_khoe/thông
  báo lỗi; _KHOA_DANG_DUNG + bộ lọc log chặn dòng chứa khoá trên logger HTTP.
- base_url do người dùng khai đi qua ĐÚNG app.tu_xa.kiem_url_lan (SSRF) lúc cai();
  không viết bộ kiểm thứ hai.
- Ảnh trả về từ nhà cung cấp là DỮ LIỆU KHÔNG TIN: qua kiem_anh (chống bom giải nén)
  trước khi lưu; tên tệp do module sinh (uuid), không lấy từ nhà cung cấp.
- Lõi app/ KHÔNG sửa: hàng đợi chạy NGAY TRONG module (hang_doi.py).
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import io
import json
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any

from app.kho import che as _che_khoa, thu_muc_modules
from app.loi_openai import than_loi
from app.tu_xa import kiem_url_lan

from .hang_doi import HangDoiTacVu, TaskQueueFull
from .kiem_anh import ImageRejected, kiem_anh, kiem_bo_anh

log = logging.getLogger(__name__)

# ── hằng số ──────────────────────────────────────────────────────────────

#: Trần prompt (khớp validate của hang_doi.py).
PROMPT_TOI_DA = 8000
#: Số ảnh mỗi lượt — trần của c2a (lõi cho tới 10, module kẹp về 4 như c2a).
N_TOI_DA = 4
#: Hết giờ gọi nhà cung cấp (giây) — như c2a.
HET_GIO = 300
#: Kích thước mặc định khi client không nêu.
SIZE_MAC_DINH = "1024x1024"
#: Tên model hợp lệ trong đường model anh_ai/<nhà>/<tên>.
TEN_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}")

#: Khoá đang dùng — bộ lọc log chặn mọi dòng của thư viện HTTP chứa một trong các chuỗi này.
_KHOA_DANG_DUNG: set[str] = set()
_LOGGER_HTTP = ("httpx", "httpcore", "httpcore.connection", "httpcore.http11",
                "httpcore.http2", "httpcore.proxy", "curl_cffi")


class _ChanKhoaAnhAi(logging.Filter):
    """Bỏ dòng log chứa khoá API ảnh. Cài MỘT lần lên logger HTTP — KHÔNG đổi
    mức log toàn cục. Chặn theo CẤU TRÚC (mọi dòng «Authorization … Bearer») và
    theo giá trị (mọi khoá đã nạp qua nap)."""

    _anh_ai_loc = True

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            noi_dung = record.getMessage()
        except Exception:
            return False
        thap = noi_dung.lower()
        if "authorization" in thap and "bearer" in thap:
            return False
        return not any(k in noi_dung for k in _KHOA_DANG_DUNG)


def _cai_bo_loc_log() -> None:
    for ten in _LOGGER_HTTP:
        lg = logging.getLogger(ten)
        for f in [f for f in lg.filters if getattr(f, "_anh_ai_loc", False)]:
            lg.removeFilter(f)  # nạp lại module: bỏ bộ lọc của bản cũ
        lg.addFilter(_ChanKhoaAnhAi())


_cai_bo_loc_log()


class LoiNhaCungCap(RuntimeError):
    """Nhà cung cấp trả lỗi HTTP. `.ma` = mã HTTP, `.chi_tiet` đã cắt ngắn."""

    def __init__(self, ma: int, chi_tiet: str = "") -> None:
        self.ma = ma
        self.chi_tiet = chi_tiet
        super().__init__(f"nha_cung_cap_tra_loi_{ma}")


class LoiCauHinh(RuntimeError):
    """Cấu hình module thiếu/sai (vd custom thiếu base_url)."""


# ── registry nhà cung cấp ────────────────────────────────────────────────

def _registry() -> dict[str, Any]:
    """Dict {key nhà cung cấp: adapter}. Đọc lười để module vẫn import được khi
    thư mục bo_chuyen_doi chưa port xong (test-first)."""
    try:
        from . import bo_chuyen_doi as _bcd
    except ImportError:
        return {}
    reg = getattr(_bcd, "IMAGE_ADAPTERS", None) or getattr(_bcd, "BO_CHUYEN_DOI", None) or {}
    return dict(reg)


def _lay_adapter(nha: str) -> Any | None:
    """Adapter MỚI cho mỗi lượt gọi (c2a: sdwebui/flow giữ trạng thái tạm theo
    request nên không được là singleton — tạo mới hết cho đồng nhất)."""
    try:
        from . import bo_chuyen_doi as _bcd
        lay_moi = getattr(_bcd, "lay_thi_tan_moi", None)
        if callable(lay_moi):
            return lay_moi(nha)
    except ImportError:
        pass
    reg = _registry()
    if nha.startswith("custom:"):
        mau = reg.get("custom") or reg.get("custom_openai_image")
    else:
        mau = reg.get(nha)
    if mau is None:
        return None
    pid = getattr(mau, "provider_id", None)
    try:
        return type(mau)(pid) if pid is not None else type(mau)()
    except Exception:
        try:
            return type(mau)()
        except Exception:
            return mau


def _danh_sach_nha() -> list[str]:
    return sorted(k for k in _registry() if k != "custom")


#: Model gợi ý theo nhà cung cấp (danh sách hiển thị cho /v1/models; adapter nhận
#: tên model tuỳ ý qua đường model nên danh sách này chỉ là gợi ý, không phải trần).
ANH_MODELS: dict[str, list[str]] = {
    "gemini": ["imagen-3.0-generate-001"],
    "fal_ai": ["fal-ai/flux-pro", "fal-ai/flux-dev", "fal-ai/flux-schnell"],
    "stability_ai": ["sd3.5-large", "stable-image-ultra", "stable-image-core"],
    "black_forest_labs": ["flux-pro-1.1", "flux-dev"],
    "cloudflare_ai": ["@cf/black-forest-labs/flux-1-schnell",
                     "@cf/stabilityai/stable-diffusion-xl-base-1.0"],
    "huggingface": ["black-forest-labs/FLUX.1-schnell",
                    "stabilityai/stable-diffusion-xl-base-1.0"],
    "nvidia_nim_image": ["black-forest-labs/flux.1-dev", "stabilityai/sdxl-turbo"],
    "sdwebui": ["sd_xl_base_1.0"],
    "flow": ["imagen-3.0-generate-001"],
}

#: Nhà nào hỗ trợ sửa/inpaint (supports_image_edit của adapter c2a).
_NHA_HO_TRO_SUA = {"gemini", "fal_ai", "stability_ai", "nvidia_nim_image",
                   "sdwebui", "flow", "custom"}


def _tach_model(model: str) -> tuple[str, str]:
    """«anh_ai/<nhà>/<tên>» → (nhà, tên). Sai dạng → ValueError."""
    if not isinstance(model, str) or not model.startswith("anh_ai/"):
        raise ValueError(f"model phải dạng anh_ai/<nhà>/<tên>, nhận {model!r}")
    phan = model[len("anh_ai/"):]
    nha, _, ten = phan.partition("/")
    if not nha or not ten or not TEN_MODEL.fullmatch(ten):
        raise ValueError(f"model phải dạng anh_ai/<nhà>/<tên>, nhận {model!r}")
    return nha, ten


def _http_post_mac_dinh(url: str, *, headers: dict, json_body: dict,
                        timeout: int) -> Any:
    """POST qua curl_cffi (mặc định). Test thay bằng hàm giả qua Module(..., _http_post=...)."""
    from curl_cffi import requests
    return requests.post(url, headers=headers, json=json_body, timeout=timeout)


# ── tiện ích ─────────────────────────────────────────────────────────────

def _so_nguyen(gia_tri: Any, mac_dinh: int, toi_thieu: int, toi_da: int) -> int:
    try:
        v = int(str(gia_tri).strip())
    except (TypeError, ValueError, AttributeError):
        return mac_dinh
    return max(toi_thieu, min(toi_da, v))


def _danh_sach_khoa(ch: dict) -> list[str]:
    """Mọi khoá từ cấu hình (api_key + api_keys, chấp nhận str/list/JSON)."""
    ra: list[str] = []
    mot = str(ch.get("api_key") or ch.get("apiKey") or "").strip()
    if mot:
        ra.append(mot)
    nhieu = ch.get("api_keys") or ch.get("apiKeys") or []
    if isinstance(nhieu, str):
        try:
            nhieu = json.loads(nhieu)
        except Exception:
            nhieu = [k.strip() for k in nhieu.split(",")]
    if isinstance(nhieu, (list, tuple)):
        ra.extend(str(k).strip() for k in nhieu if str(k).strip())
    # khử trùng giữ thứ tự
    thay: list[str] = []
    for k in ra:
        if k not in thay:
            thay.append(k)
    return thay


def _che_khoa(khoa: str) -> str:
    if not khoa:
        return "(chưa khai)"
    return f"…{khoa[-4:]}" if len(khoa) >= 4 else "…"


#: Mã HTTP mà c2a xoay sang khoá kế (400/401/403/429).
_MA_XOAY_KHOA = (400, 401, 403, 429)

#: Tên cấu hình module đọc trong cai()/nap().
_CAU_HINH_LUU_KEYS = ("nha_cung_cap", "api_key", "api_keys", "base_url", "account_id",
                      "solver_url", "flow_accounts", "dong_thoi_toi_da",
                      "dong_thoi_moi_nguoi", "giu_tac_vu_ngay")


# ── lớp Module ───────────────────────────────────────────────────────────

class Module:
    """Module anh-ai: tạo/sửa ảnh AI qua 10 nhà cung cấp (port từ c2a).

    Hợp đồng với lõi (xem app/tuyen.py, app/goi_kha_nang.py):
    - `nap(cau_hinh)` ĐỒNG BỘ (lõi await-nap sẽ RuntimeError).
    - `anh(body)` → (200, headers, bytes JSON {created, data:[{b64_json}]}).
    - `anh_sua(yc)` → như trên; yc là YeuCauDaPhan (truong + tep).
    - `thuc_the/trang_thai/thuc_hien`: công cụ hàng đợi tác vụ ảnh (chuẩn C2).
    - Khả năng «anh»/«anh_sua» đã khai trong manifest.cung_cap.
    """

    def __init__(self, manifest: dict, _http_post: Any = None,
                 _goc_kiem_thu: str | None = None) -> None:
        self.manifest = manifest or {}
        self.tien_to = str(self.manifest.get("tien_to") or "anh_ai")
        self._http_post = _http_post or _http_post_mac_dinh
        self._cau_hinh: dict[str, Any] = {}
        self._hang_doi: HangDoiTacVu | None = None
        self._thu_muc_anh: Path | None = None
        self._goc_kiem_thu = Path(_goc_kiem_thu) if _goc_kiem_thu else None
        self._dong_thoi_toi_da = 6
        self._dong_thoi_moi_nguoi = 2
        self._giu_tac_vu_ngay = 30

    # ── vòng đời ──────────────────────────────────────────────────────

    def _goc_du_lieu(self) -> Path:
        if self._goc_kiem_thu is not None:
            return self._goc_kiem_thu
        return thu_muc_modules()

    def nap(self, cau_hinh: dict) -> None:
        cau_hinh = dict(cau_hinh or {})
        self._cau_hinh = cau_hinh
        for khoa in _danh_sach_khoa(cau_hinh):
            _KHOA_DANG_DUNG.add(khoa)
        self._dong_thoi_toi_da = _so_nguyen(cau_hinh.get("dong_thoi_toi_da"), 6, 1, 20)
        self._dong_thoi_moi_nguoi = _so_nguyen(cau_hinh.get("dong_thoi_moi_nguoi"), 2, 1, 10)
        self._giu_tac_vu_ngay = _so_nguyen(cau_hinh.get("giu_tac_vu_ngay"), 30, 1, 365)
        goc = self._goc_du_lieu() / self.tien_to
        self._thu_muc_anh = goc / "anh"
        self._thu_muc_anh.mkdir(parents=True, exist_ok=True)
        self._hang_doi = HangDoiTacVu(
            goc / "hang_doi.json",
            xu_ly_tao=self._tao_dong_bo,
            xu_ly_sua=self._sua_dong_bo,
            dong_thoi_toi_da=self._dong_thoi_toi_da,
            dong_thoi_moi_chu=self._dong_thoi_moi_nguoi,
            giu_ngay_getter=lambda: self._giu_tac_vu_ngay,
        )

    async def cai(self, cau_hinh: dict) -> dict:
        """Kiểm cấu hình trước khi lưu. CHỈ kiểm URL qua app.tu_xa (SSRF) —
        KHÔNG gọi mạng tới nhà cung cấp ở đây (gọi là tốn hạn mức của họ)."""
        ch = dict(cau_hinh or {})
        nha = str(ch.get("nha_cung_cap") or "gemini").strip()
        adapter = _lay_adapter(nha)
        if adapter is None:
            return {"ok": False, "loi":
                    f"nha_cung_cap {nha!r} không hợp lệ. Chọn: {', '.join(_danh_sach_nha())} "
                    f"hoặc custom:<id-may-chu>."}
        # URL do người dùng khai → ĐÚNG app.tu_xa.kiem_url_lan, không bộ kiểm thứ hai.
        url_can_kiem: list[tuple[str, str]] = []
        base_url = str(ch.get("base_url") or "").strip()
        if nha.startswith("custom:"):
            if not base_url:
                return {"ok": False, "loi":
                        "custom:<id> BẮT BUỘC khai base_url (địa chỉ máy chủ OpenAI-compatible)."}
            url_can_kiem.append(("base_url", base_url))
        elif nha == "sdwebui" and base_url:
            url_can_kiem.append(("base_url", base_url))
        elif nha == "gemini" and base_url:
            url_can_kiem.append(("base_url", base_url))
        elif nha == "flow":
            solver = str(ch.get("solver_url") or "").strip()
            if not solver:
                return {"ok": False, "loi":
                        "flow BẮT BUỘC khai solver_url (địa chỉ REST của captcha-solver)."}
            url_can_kiem.append(("solver_url", solver))
            tai_khoan = ch.get("flow_accounts") or "[]"
            try:
                ds = json.loads(tai_khoan) if isinstance(tai_khoan, str) else tai_khoan
            except Exception:
                return {"ok": False, "loi": "flow_accounts phải là JSON danh sách tài khoản."}
            if not isinstance(ds, list) or not all(
                    isinstance(a, dict) and a.get("project_id") for a in ds):
                return {"ok": False, "loi":
                        "flow_accounts phải là JSON: [{\"profile\": \"...\", \"project_id\": \"...\"}]."}
        for ten, url in url_can_kiem:
            try:
                ch[ten] = await kiem_url_lan(url)
            except ValueError as e:
                return {"ok": False, "loi": f"{ten} không hợp lệ (SSRF): {e}"}
        khoas = _danh_sach_khoa(ch)
        if not khoas and nha not in ("sdwebui",):
            # sdwebui chạy local có thể không cần khoá; các nhà còn lại cần ít nhất 1.
            return {"ok": False, "loi":
                    f"nha_cung_cap={nha} cần khai api_key (hoặc api_keys để xoay vòng)."}
        # kiểm số nguyên các trần
        for ten, mac_dinh, toi_thieu, toi_da in (
                ("dong_thoi_toi_da", 6, 1, 20), ("dong_thoi_moi_nguoi", 2, 1, 10),
                ("giu_tac_vu_ngay", 30, 1, 365)):
            try:
                v = int(str(ch.get(ten) if ch.get(ten) not in (None, "") else mac_dinh))
                if not (toi_thieu <= v <= toi_da):
                    raise ValueError()
            except (TypeError, ValueError):
                return {"ok": False, "loi": f"{ten} phải là số nguyên {toi_thieu}–{toi_da}."}
        ket_qua_tung_khoa = [{"khoa": _che_khoa(k), "ok": True} for k in khoas]
        cau_hinh_luu = {k: ch.get(k) for k in _CAU_HINH_LUU_KEYS if ch.get(k) not in (None, "")}
        cau_hinh_luu["nha_cung_cap"] = nha
        return {"ok": True, "loi": None, "ket_qua_tung_khoa": ket_qua_tung_khoa,
                "cau_hinh_luu": cau_hinh_luu}

    async def models(self) -> list[dict]:
        ra: list[dict] = []
        for nha, ds in ANH_MODELS.items():
            for m in ds:
                ra.append({"id": f"{self.tien_to}/{nha}/{m}",
                           "ten": f"{m} ({nha})", "nha_cung_cap": nha})
        return ra

    async def suc_khoe(self) -> dict:
        ch = self._cau_hinh
        nha = str(ch.get("nha_cung_cap") or "gemini")
        khoas = _danh_sach_khoa(ch)
        hd: dict[str, Any] = {}
        if self._hang_doi is not None:
            try:
                with self._hang_doi._khoa:
                    from collections import Counter
                    dem = Counter(t.get("status") for t in self._hang_doi._tac_vu.values())
                hd = {"queued": dem.get("queued", 0), "running": dem.get("running", 0),
                      "done": dem.get("done", 0), "failed": dem.get("failed", 0)}
            except Exception:
                hd = {}
        return {"ok": True, "nha_cung_cap": nha,
                "khoa": [_che_khoa(k) for k in khoas],
                "hang_doi": hd,
                "thu_muc_anh": str(self._thu_muc_anh) if self._thu_muc_anh else None}

    async def dung(self) -> None:
        if self._hang_doi is not None:
            self._hang_doi.dong()

    # ── khả năng ảnh (lõi gọi) ─────────────────────────────────────────

    async def anh(self, body: dict) -> tuple[int, dict, bytes]:
        """POST /v1/images/generations → (200, headers, JSON bytes {created,data}).

        body: {model: "anh_ai/<nhà>/<tên>", prompt, n, size, response_format, ...}.
        """
        body = dict(body or {})
        prompt = body.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            return self._loi(400, "missing_prompt", "Thiếu trường «prompt».")
        if len(prompt) > PROMPT_TOI_DA:
            return self._loi(400, "invalid_value",
                              f"«prompt» quá dài ({len(prompt)} ký tự, trần {PROMPT_TOI_DA}).")
        try:
            nha, ten_model = _tach_model(body.get("model"))
        except ValueError as e:
            return self._loi(400, "invalid_value", str(e))
        adapter = _lay_adapter(nha)
        if adapter is None:
            return self._loi(400, "invalid_value", f"nha_cung_cap {nha!r} không hỗ trợ.")
        try:
            n = max(1, min(N_TOI_DA, int(body.get("n") or 1)))
        except (TypeError, ValueError):
            return self._loi(400, "invalid_value", "«n» phải là số nguyên.")
        ket_qua = await asyncio.to_thread(self._tao_dong_bo,
                                          {**body, "model": ten_model, "n": n})
        if ket_qua.get("loi"):
            return self._loi(*ket_qua["loi"])
        noi_dung = json.dumps({"created": ket_qua["created"], "data": ket_qua["data"]},
                              ensure_ascii=False).encode()
        return 200, {"content-type": "application/json"}, noi_dung

    async def anh_sua(self, yc: Any) -> tuple[int, dict, bytes]:
        """POST /v1/images/edits (multipart đã qua lõi) → như anh()."""
        truong = getattr(yc, "truong", None) or {}
        prompt = truong.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            return self._loi(400, "missing_prompt", "Thiếu trường «prompt» (mô tả cần sửa gì).")
        if len(prompt) > PROMPT_TOI_DA:
            return self._loi(400, "invalid_value",
                              f"«prompt» quá dài ({len(prompt)} ký tự, trần {PROMPT_TOI_DA}).")
        try:
            nha, ten_model = _tach_model(truong.get("model"))
        except ValueError as e:
            return self._loi(400, "invalid_value", str(e))
        adapter = _lay_adapter(nha)
        if adapter is None:
            return self._loi(400, "invalid_value", f"nha_cung_cap {nha!r} không hỗ trợ.")
        if not getattr(adapter, "supports_image_edit", False):
            return self._loi(400, "capability_not_supported",
                              f"nha_cung_cap {nha!r} không hỗ trợ sửa ảnh.")
        # Ảnh đầu vào là DỮ LIỆU KHÔNG TIN: kiểm hàng rào trước khi đưa cho adapter.
        tep_anh = [t for t in (getattr(yc, "tep_ten", lambda x: []))("image")]
        if not tep_anh:
            return self._loi(400, "missing_file", "Thiếu tệp «image».")
        try:
            kiem_bo_anh([(t.noi_dung, t.ten_tep or "image", t.content_type or "image/png")
                         for t in tep_anh])
        except ImageRejected as e:
            return self._loi(400, "invalid_image", str(e))
        images = [{"b64_json": base64.b64encode(t.noi_dung).decode("ascii"),
                   "mime": t.content_type or "image/png"} for t in tep_anh]
        mask_tep = (getattr(yc, "tep_ten", lambda x: []))("mask")
        mask_b64 = None
        if mask_tep:
            try:
                kiem_anh(mask_tep[0].noi_dung, ten="mask")
                mask_b64 = base64.b64encode(mask_tep[0].noi_dung).decode("ascii")
            except ImageRejected as e:
                return self._loi(400, "invalid_image", f"mask: {e}")
        try:
            n = max(1, min(N_TOI_DA, int(truong.get("n") or 1)))
        except (TypeError, ValueError):
            return self._loi(400, "invalid_value", "«n» phải là số nguyên.")
        body = {"model": ten_model, "prompt": prompt, "n": n,
                "size": truong.get("size"), "response_format": "b64_json"}
        ket_qua = await asyncio.to_thread(self._sua_dong_bo, body, images, mask_b64)
        if ket_qua.get("loi"):
            return self._loi(*ket_qua["loi"])
        noi_dung = json.dumps({"created": ket_qua["created"], "data": ket_qua["data"]},
                              ensure_ascii=False).encode()
        return 200, {"content-type": "application/json"}, noi_dung

    @staticmethod
    def _loi(ma: int, code: str, thong_bao: str) -> tuple[int, dict, bytes]:
        return ma, {"content-type": "application/json"}, json.dumps(
            {"loi": code, "thong_bao": thong_bao}, ensure_ascii=False).encode()

    # ── thực thể công cụ (chuẩn C2: hàng đợi tác vụ ảnh) ─────────────────

    async def thuc_the(self, loc=None) -> list[dict]:
        return [{
            "id": f"{self.tien_to}.hang_doi_anh",
            "ten": "Hàng đợi tác vụ tạo/sửa ảnh AI",
            "loai": "cong_cu",
            "thuoc_tinh": {"nha_cung_cap": str(self._cau_hinh.get("nha_cung_cap") or "gemini")},
            "tinh_nang": [
                {"hanh_dong": "gui_tao", "rui_ro": "ghi",
                 "mo_ta": "Xếp hàng một tác vụ tạo ảnh (prompt, model anh_ai/<nhà>/<tên>, size).",
                 "tham_so": {"type": "object", "properties": {
                     "ma": {"type": "string"}, "prompt": {"type": "string"},
                     "model": {"type": "string"}, "size": {"type": "string"}},
                     "required": ["ma", "prompt", "model"]}},
                {"hanh_dong": "gui_sua", "rui_ro": "ghi",
                 "mo_ta": "Xếp hàng một tác vụ sửa ảnh (ảnh vào dạng base64).",
                 "tham_so": {"type": "object", "properties": {
                     "ma": {"type": "string"}, "prompt": {"type": "string"},
                     "model": {"type": "string"}, "size": {"type": "string"},
                     "anh_b64": {"type": "array", "items": {"type": "string"}}},
                     "required": ["ma", "prompt", "model", "anh_b64"]}},
                {"hanh_dong": "lay", "rui_ro": "doc",
                 "mo_ta": "Lấy trạng thái/kết quả một tác vụ.",
                 "tham_so": {"type": "object", "properties": {"ma": {"type": "string"}},
                             "required": ["ma"]}},
                {"hanh_dong": "liet_ke", "rui_ro": "doc",
                 "mo_ta": "Liệt kê tác vụ của người gọi.",
                 "tham_so": {"type": "object", "properties": {
                     "gioi_han": {"type": "integer"}}}},
                {"hanh_dong": "huy", "rui_ro": "ghi",
                 "mo_ta": "Huỷ tác vụ còn đang xếp hàng.",
                 "tham_so": {"type": "object", "properties": {"ma": {"type": "string"}},
                             "required": ["ma"]}},
                {"hanh_dong": "don_dep", "rui_ro": "ghi",
                 "mo_ta": "Xoá tác vụ đã xong cũ hơn số ngày giữ.",
                 "tham_so": {"type": "object", "properties": {
                     "giu_ngay": {"type": "integer"}}}},
            ],
            "rui_ro_mac_dinh": "ghi",
        }]

    async def trang_thai(self, ids) -> dict:
        luc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        nha = str(self._cau_hinh.get("nha_cung_cap") or "gemini")
        ra = {}
        for id_ in ids or []:
            if id_ == f"{self.tien_to}.hang_doi_anh":
                ra[id_] = {"gia_tri": "san_sang" if _lay_adapter(nha) else "chua_cai",
                           "hop_le": True, "luc": luc}
        return ra

    async def thuc_hien(self, id: str, hanh_dong: str, tham_so: dict | None) -> dict:
        tham_so = dict(tham_so or {})
        if id != f"{self.tien_to}.hang_doi_anh":
            return {"ok": False, "loi": "khong_thay_thuc_the"}
        hd = self._hang_doi
        if hd is None:
            return {"ok": False, "loi": "chua_nap"}
        danh_tinh = tham_so.get("danh_tinh") or "khong_ro"
        try:
            if hanh_dong == "gui_tao":
                return {"ok": True, "tac_vu": hd.gui_tao(
                    danh_tinh, ma=str(tham_so.get("ma") or ""),
                    prompt=str(tham_so.get("prompt") or ""),
                    model=str(tham_so.get("model") or ""),
                    size=tham_so.get("size"))}
            if hanh_dong == "gui_sua":
                anh_b64 = tham_so.get("anh_b64") or []
                if not isinstance(anh_b64, list) or not anh_b64:
                    return {"ok": False, "loi": "thieu_anh_b64"}
                # Payload hàng đợi phải JSON được → ảnh vào dưới dạng b64 chuỗi.
                anh_vao = []
                for muc in anh_b64[:4]:
                    if not isinstance(muc, str):
                        continue
                    try:
                        raw = base64.b64decode(muc.split(",", 1)[-1])
                        kiem_anh(raw, ten="anh_vao")
                    except (binascii.Error, ImageRejected, ValueError) as e:
                        return {"ok": False, "loi": f"anh_vao_khong_hop_le: {e}"}
                    anh_vao.append({"b64_json": base64.b64encode(raw).decode("ascii"),
                                    "mime": "image/png"})
                return {"ok": True, "tac_vu": hd.gui_sua(
                    danh_tinh, ma=str(tham_so.get("ma") or ""),
                    prompt=str(tham_so.get("prompt") or ""),
                    model=str(tham_so.get("model") or ""),
                    size=tham_so.get("size"), anh=anh_vao)}
            if hanh_dong == "lay":
                tv = hd.lay(danh_tinh, str(tham_so.get("ma") or ""))
                return {"ok": tv is not None, "tac_vu": tv}
            if hanh_dong == "liet_ke":
                return {"ok": True, "tac_vu": hd.liet_ke(danh_tinh, tham_so.get("gioi_han", 50))}
            if hanh_dong == "huy":
                return hd.huy(danh_tinh, str(tham_so.get("ma") or ""))
            if hanh_dong == "don_dep":
                return hd.don_dep(tham_so.get("giu_ngay"))
            return {"ok": False, "loi": "hanh_dong_khong_ho_tro"}
        except (ValueError, RuntimeError) as e:
            return {"ok": False, "loi": str(e) or "loi_hang_doi"}

    # ── xử lý đồng bộ (chạy trong thread: lõi gọi qua to_thread, hàng đợi
    #     gọi trực tiếp trong thread worker của nó) ─────────────────────

    def _tao_dong_bo(self, body: dict) -> dict:
        return self._chay(body, images=None, mask_b64=None)

    def _sua_dong_bo(self, body: dict, images: list | None = None,
                     mask_b64: str | None = None) -> dict:
        # Đường hàng đợi (hang_doi._chay_task) gọi xu_ly_sua(payload) với MỘT
        # tham số: ảnh đầu vào nằm trong payload["anh"] (định dạng giống images:
        # list {"b64_json","mime"} do thuc_hien/gui_sua dựng). Không có fallback
        # này thì tác vụ sửa qua hàng đợi bị mất ảnh đầu vào (bug NV192).
        if images is None:
            images = (body or {}).get("anh") or None
        return self._chay(body, images=images, mask_b64=mask_b64)

    def _chay(self, body: dict, *, images: list | None,
              mask_b64: str | None) -> dict:
        """Gộp _handle_adapter_image/_handle_adapter_edit của c2a:
        build_url → build_body → build_headers → POST → parse_response →
        normalize, xoay khoá khi 400/401/403/429.

        body["model"] ở đây đã là TÊN MODEL THUẦN (không tiền tố anh_ai/<nhà>/);
        nhà cung cấp đã được chọn lúc tách model ở anh()/anh_sua(). Trả dict
        {"created", "data"} hoặc {"loi": (ma, code, thong_bao)}.
        """
        body = dict(body or {})
        ten_model = str(body.get("model") or "")
        prompt = str(body.get("prompt") or "")
        n = max(1, min(N_TOI_DA, body.get("n") or 1))
        # Model đi qua hàng đợi lưu DẠNG ĐẦY ĐỦ anh_ai/<nhà>/<tên>; đường anh()/
        # anh_sua() đã tách sẵn nên model ở đây có thể là tên thuần → tách lại
        # an toàn, rơi về nhà cung cấp đã cấu hình nếu chỉ là tên thuần.
        nha, ten_model = self._tach_model_an_toan(body.get("_model_day_du") or body.get("model"))
        adapter = _lay_adapter(nha)
        if adapter is None:
            return {"loi": (400, "invalid_value", f"nha_cung_cap {nha!r} không hỗ trợ.")}
        try:
            adapter.tai_tu_url = self._tai_tu_url_da_kiem
        except Exception:
            pass
        credentials = self._xay_dung_credentials(nha)
        # Ảnh đầu vào của module là list dict {"b64_json","mime"} (do anh_sua/
        # thuc_hien dựng); adapter c2a chỉ hiểu bytes/str-data/tuple
        # (bytes, filename, mime) → chuẩn hoá ở ĐÂY một lần cho mọi adapter,
        # không đụng từng tệp adapter.
        anh_vao: list = []
        for muc in images or []:
            if isinstance(muc, dict) and muc.get("b64_json"):
                try:
                    raw = base64.b64decode(str(muc["b64_json"]).split(",", 1)[-1])
                except (binascii.Error, ValueError):
                    continue
                anh_vao.append((raw, str(muc.get("mime") or "image/png")))
            else:
                anh_vao.append(muc)
        tat_ca: list[dict] = []
        for idx in range(n):
            loi_cuoi: tuple | None = None
            so_khoa = max(1, getattr(adapter, "get_key_count",
                                     lambda c: 1)(credentials) or 1)
            for key_try in range(so_khoa):
                credentials["_key_index"] = key_try
                try:
                    try:
                        url = adapter.build_url(ten_model, credentials, key_try)
                    except TypeError:
                        url = adapter.build_url(ten_model, credentials)
                    than_yeu_cau = adapter.build_body(ten_model, {**body, "model": ten_model,
                                                                 "images": anh_vao,
                                                                 "mask_b64": mask_b64})
                    headers = adapter.build_headers(credentials, than_yeu_cau,
                                                    ten_model, body)
                    log.info({"event": "anh_ai_goi", "nha": nha, "model": ten_model,
                              "url": str(url)[:120], "key_try": key_try})
                    resp = self._http_post(str(url), headers=headers,
                                           json_body=than_yeu_cau, timeout=HET_GIO)
                    try:
                        if resp.status_code >= 400:
                            van_ban = ""
                            try:
                                van_ban = resp.text[:500]
                            except Exception:
                                van_ban = ""
                            if hasattr(adapter, "on_key_failed"):
                                try:
                                    adapter.on_key_failed(credentials, resp.status_code, van_ban)
                                except Exception:
                                    pass
                            # 401/403 thường là khoá hết hạn/bị chặn theo khoá — xoay
                            # khoá kế (c2a); không xoay thì pool nhiều khoá mất
                            # khả năng failover đúng lúc cần.
                            if (resp.status_code in _MA_XOAY_KHOA
                                    and key_try < so_khoa - 1):
                                log.warning({"event": "anh_ai_xoay_khoa", "nha": nha,
                                             "status": resp.status_code, "key_try": key_try})
                                loi_cuoi = (502, "upstream_error",
                                            f"nhà cung cấp {nha} trả HTTP {resp.status_code}.")
                                continue
                            loi_cuoi = (502, "upstream_error",
                                        f"nhà cung cấp {nha} trả HTTP {resp.status_code}: "
                                        f"{van_ban[:200]}")
                            break
                        parsed = None
                        try:
                            parsed = adapter.parse_response(resp)
                        except Exception as exc:
                            # parse_response của adapter ném = tín hiệu xoay
                            # (vd Flow hết ngạch) — thử khoá/tài khoản kế.
                            if key_try < so_khoa - 1:
                                log.warning({"event": "anh_ai_xoay_khoa", "nha": nha,
                                             "ly_do": str(exc)[:120]})
                                loi_cuoi = (502, "upstream_error", str(exc)[:200])
                                continue
                            raise
                        if parsed is None:
                            try:
                                parsed = resp.json()
                            except Exception:
                                try:
                                    parsed = {"image_bytes": resp.content}
                                except Exception:
                                    parsed = {}
                        chuan = adapter.normalize(parsed, body)
                        muc = chuan.get("data") or []
                        if muc and hasattr(adapter, "on_key_success"):
                            try:
                                adapter.on_key_success(credentials)
                            except Exception:
                                pass
                        tat_ca.extend(muc)
                        loi_cuoi = None
                        break  # xong ảnh thứ idx
                    finally:
                        try:
                            resp.close()
                        except Exception:
                            pass
                except Exception as exc:
                    log.warning({"event": "anh_ai_loi", "nha": nha,
                                 "loai": type(exc).__name__})
                    if key_try < so_khoa - 1:
                        loi_cuoi = (502, "upstream_error", str(exc)[:200])
                        continue
                    loi_cuoi = (502, "upstream_error",
                                f"nhà cung cấp {nha} lỗi: {type(exc).__name__}")
                    break
            if loi_cuoi is not None:
                return {"loi": loi_cuoi}
        if not tat_ca:
            return {"loi": (502, "upstream_error", "nhà cung cấp không trả ảnh nào.")}
        # Ảnh trả về là DỮ LIỆU KHÔNG TIN: kiểm hàng rào + lưu tệp do module
        # sinh tên (uuid) — không lấy tên từ nhà cung cấp.
        ra: list[dict] = []
        for muc in tat_ca:
            if not isinstance(muc, dict):
                continue
            b64 = muc.get("b64_json") or ""
            if isinstance(b64, str) and b64.startswith("data:"):
                b64 = b64.split(",", 1)[-1]
            if not b64:
                continue
            try:
                raw = base64.b64decode(b64)
                mime = kiem_anh(raw)
            except (binascii.Error, ImageRejected, ValueError) as e:
                log.warning({"event": "anh_ai_anh_xau", "nha": nha, "loi": str(e)[:120]})
                continue
            ten_tep = f"{uuid.uuid4().hex}.{_duoi_theo_mime(mime)}"
            try:
                (self._thu_muc_anh or Path(".")).mkdir(parents=True, exist_ok=True)
                (self._thu_muc_anh or Path(".")).joinpath(ten_tep).write_bytes(raw)
            except OSError as e:
                log.warning({"event": "anh_ai_luu_loi", "loi": str(e)[:120]})
            muc_sach = {"b64_json": base64.b64encode(raw).decode("ascii"),
                        "tep": ten_tep}
            if muc.get("revised_prompt"):
                muc_sach["revised_prompt"] = str(muc["revised_prompt"])[:2000]
            ra.append(muc_sach)
        if not ra:
            return {"loi": (502, "upstream_error",
                             "mọi ảnh nhà cung cấp trả về đều không qua được hàng rào kiểm ảnh.")}
        return {"created": int(time.time()), "data": ra}

    def _tach_model_an_toan(self, model: Any) -> tuple[str, str]:
        """Tách «anh_ai/<nhà>/<tên>» (hàng đợi) hoặc tên thuần (đường anh())."""
        try:
            return _tach_model(model)
        except ValueError:
            nha = str(self._cau_hinh.get("nha_cung_cap") or "gemini").strip()
            return nha, str(model or "")

    def _xay_dung_credentials(self, nha: str) -> dict[str, Any]:
        ch = self._cau_hinh
        khoas = _danh_sach_khoa(ch)
        khoa_dau = khoas[0] if khoas else ""
        base_url = str(ch.get("base_url") or "").strip()
        if nha == "sdwebui" and not base_url:
            base_url = "http://127.0.0.1:7860"
        return {
            "apiKey": khoa_dau, "apiKeys": khoas, "accessToken": khoa_dau,
            "api_key": khoa_dau, "api_keys": khoas,
            "base_url": base_url,
            "account_id": str(ch.get("account_id") or ""),
            "_cau_hinh": dict(ch),
        }

    def _tai_tu_url_da_kiem(self, url: str, timeout: int = 30) -> bytes:
        """Tải URL ảnh cho adapter — URL đi qua ĐÚNG app.tu_xa (SSRF) trước.

        Được adapter gọi trong thread worker (không có event loop đang chạy)
        nên dùng asyncio.run cho bộ kiểm async. Trần 20MB+1: ảnh vượt trần bị
        cắt ở đây, hàng rào kiem_anh kiểm lại sau khi tải xong.
        """
        url_sach = asyncio.run(kiem_url_lan(url))
        from curl_cffi import requests
        tran = 20 * 1024 * 1024 + 1
        r = requests.get(url_sach, timeout=timeout, stream=True)
        try:
            if r.status_code >= 400:
                raise RuntimeError(f"tai_url_that_bai_http_{r.status_code}")
            buf = bytearray()
            for khoi in r.iter_content(65536):
                buf += khoi
                if len(buf) > tran:
                    raise RuntimeError("anh_tu_url_vuot_tran_20MB")
            if not buf:
                raise RuntimeError("anh_tu_url_rong")
            return bytes(buf)
        finally:
            try:
                r.close()
            except Exception:
                pass


def _duoi_theo_mime(mime: str) -> str:
    return {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp",
            "image/gif": "gif"}.get(mime, "png")

