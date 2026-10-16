"""Kiểm thử giao diện Chrome cho tính năng quản lý model (VIỆC B2):
- Bật/tắt model từng dòng
- Đặt mặc định model theo nhóm (kể cả nhóm lạ)
- Chọn tất cả / bỏ chọn tất cả theo nhóm
- Nút «Lưu thay đổi» sáng khi có sửa, xám khi chưa sửa
- Hoạt động đúng ở cả kích thước máy tính (1280) và di động (390)
- Tự động bỏ qua khi môi trường không có Chrome (hộp cát).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
import pytest

from app import so_module
from app.main import app
from gia import MayChuThat, ghi_thu_muc
from ui_chung import CO_CHROME, dang_nhap_quan_tri, trang, trinh_duyet  # noqa: F401 — fixture

pytestmark = pytest.mark.skipif(not CO_CHROME, reason="cần Chrome")

KHOA_QT = "khoa-quan-tri-giao-dien-0123456789ab"
QT = {"Authorization": f"Bearer {KHOA_QT}"}
K_TOT = "AIzaSy-KHOA-MOT-tot-1111"


def _module_co_nhom_la(id_: str = "ai-nhom-la", tien_to: str = "anl") -> dict[str, bytes]:
    """Module giả có 3 nhóm: chat, anh, và nhóm LẠ «suy_luan_dac_biet»."""
    manifest = {
        "id": id_,
        "ten": "AI Đa Nhóm",
        "mo_ta": "Module test quản lý model với nhóm lạ",
        "phien_ban": "1.0.0",
        "loai": "ai",
        "loai_ten": "AI",
        "loai_thu_tu": 10,
        "hang": "A",
        "giao_thuc": "openai",
        "tien_to": tien_to,
        "cung_cap": ["chat", "anh", "suy_luan_dac_biet"],
        "truong_cau_hinh": [
            {
                "khoa": "api_keys",
                "nhan": "Khoá API",
                "kieu": "danh_sach_bi_mat",
                "bat_buoc": True,
                "mac_dinh": None,
            }
        ],
        "tai_nguyen": {"mang_ra": []},
    }
    ma = f"""
class Module:
    def __init__(self, manifest):
        self.manifest = manifest
        self.tien_to = manifest["tien_to"]
        self.khoa = []
    def nap(self, cau_hinh):
        self.khoa = list(cau_hinh.get("api_keys") or [])
    async def cai(self, cau_hinh):
        return {{"ok": True, "ket_qua_tung_khoa": [{{"khoa": "…1111", "ok": True, "do_tre_ms": 6, "so_model": 4, "loi": None}}], "cau_hinh_luu": cau_hinh}}
    async def chat(self, body, stream):
        return 200, {{"content-type": "application/json"}}, b'{{"choices": [{{"message": {{"content": "ok"}}}}]}}'
    async def models(self):
        t = self.tien_to
        return [
            {{"id": f"{{t}}/chat-1", "object": "model"}},
            {{"id": f"{{t}}/chat-2", "object": "model"}},
            {{"id": f"{{t}}/ve-1", "object": "model", "kha_nang": ["anh"]}},
            {{"id": f"{{t}}/suy-luan-1", "object": "model", "kha_nang": ["suy_luan_dac_biet"]}},
        ]
    async def suc_khoe(self):
        return {{"ok": True, "khoa": [{{"khoa": "…1111", "ok": True, "do_tre_ms": 6, "so_model": 4, "loi": None}}]}}
    async def dung(self):
        pass
"""
    return {
        "manifest.json": json.dumps(manifest, ensure_ascii=False).encode(),
        "module.py": ma.encode(),
    }


def _khoi_tao_loi(tmp_path, monkeypatch, modules: dict[str, dict[str, bytes]]):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")
    for id_, tep in modules.items():
        ghi_thu_muc(tmp_path / "modules" / id_, tep)


def test_ui_quan_ly_model_desktop_1280(trang, tmp_path, monkeypatch):
    """Màn hình máy tính 1280px: bật/tắt, đặt mặc định, chọn/bỏ chọn tất cả, lưu thay đổi."""
    _khoi_tao_loi(tmp_path, monkeypatch, {"ai-nhom-la": _module_co_nhom_la()})
    with MayChuThat(app, lifespan="on") as w:
        # Liên kết module trước
        r_cai = httpx.post(f"{w.url}/api/modules/ai-nhom-la/cai", json={"cau_hinh": {"api_keys": [K_TOT]}}, headers=QT)
        assert r_cai.status_code == 200

        # Kích thước màn hình 1280
        trang.khung(1280, 800)
        dang_nhap_quan_tri(trang, w.url)

        # Mở trang Hệ thống → AI, tab Provider
        trang.di(w.url + "/He-thong-AI.dc.html#provider")
        trang.cho("document.getElementById('bang-ai')?.dataset.muc === 'provider'", mo_ta="vào tab Provider")

        # Chuyển sang tab con Model
        trang.bam("document.querySelector('[data-pv-tab=\"model\"]')")
        trang.cho("document.querySelectorAll('[data-mo-nhom]').length >= 3", han=15, mo_ta="3 nhóm model")

        # Nút Lưu thay đổi ban đầu bị vô hiệu hoá (xám)
        assert trang.js("document.getElementById('nut-luu-md')?.disabled === true")

        # Mở tất cả các nhóm (chat, anh, suy_luan_dac_biet)
        for nhom in ("chat", "anh", "suy_luan_dac_biet"):
            trang.bam(f"document.querySelector('[data-mo-nhom=\"{nhom}\"]')")

        trang.cho("document.querySelectorAll('[data-model]').length === 4", mo_ta="hiện đủ 4 model")

        # Nhóm chat ban đầu có 2 model đang bật
        dem_chat = trang.chu("[data-nhom-dem='chat']")
        assert "2/2 đang bật" in dem_chat or "2" in dem_chat

        # 1. Bật/tắt một model: bấm công tắc tắt anl/chat-1
        sw_chat1 = "document.querySelector('[data-cong-tac=\"anl/chat-1\"]')"
        assert trang.js(f"{sw_chat1}.getAttribute('aria-checked') === 'true'")
        trang.bam(sw_chat1)
        # Công tắc đổi sang false
        assert trang.js(f"{sw_chat1}.getAttribute('aria-checked') === 'false'")
        # Nút Lưu thay đổi sáng lên (không còn disabled)
        assert trang.js("document.getElementById('nut-luu-md')?.disabled === false")

        # 2. Đặt mặc định: bấm «Đặt mặc định» cho anl/chat-2
        nut_dat_md = "document.querySelector('[data-dat-mac-dinh=\"anl/chat-2\"]')"
        assert trang.js(f"!!{nut_dat_md}")
        trang.bam(nut_dat_md)
        # Chuyển thành nhãn Mặc định
        trang.cho("!!document.querySelector('[data-mac-dinh=\"anl/chat-2\"]')", mo_ta="nhãn Mặc định cho chat-2")

        # 3. Thao tác trên nhóm LẠ «suy_luan_dac_biet»
        # Đặt mặc định cho model nhóm lạ
        nut_md_la = "document.querySelector('[data-dat-mac-dinh=\"anl/suy-luan-1\"]')"
        trang.bam(nut_md_la)
        trang.cho("!!document.querySelector('[data-mac-dinh=\"anl/suy-luan-1\"]')", mo_ta="mặc định nhóm lạ")

        # Bỏ chọn tất cả nhóm lạ
        nut_bo_chon = "document.querySelector('[data-bo-chon-tat-ca=\"suy_luan_dac_biet\"]')"
        trang.bam(nut_bo_chon)
        # Model nhóm lạ bị tắt, và tự mất nhãn mặc định vì model bị tắt
        assert trang.js("document.querySelector('[data-cong-tac=\"anl/suy-luan-1\"]').getAttribute('aria-checked') === 'false'")
        assert trang.js("!document.querySelector('[data-mac-dinh=\"anl/suy-luan-1\"]')")

        # Chọn lại tất cả nhóm lạ
        nut_chon = "document.querySelector('[data-chon-tat-ca=\"suy_luan_dac_biet\"]')"
        trang.bam(nut_chon)
        assert trang.js("document.querySelector('[data-cong-tac=\"anl/suy-luan-1\"]').getAttribute('aria-checked') === 'true'")

        # 4. Bấm Lưu thay đổi
        trang.bam("document.getElementById('nut-luu-md')")
        # Nút chuyển về trạng thái vô hiệu hoá sau khi lưu thành công
        trang.cho("document.getElementById('nut-luu-md')?.disabled === true", han=10, mo_ta="lưu xong")

        # 5. Xác minh qua API backend
        r_api = httpx.get(f"{w.url}/api/modules/ai-nhom-la/model", headers=QT)
        assert r_api.status_code == 200
        d_api = r_api.json()
        assert "anl/chat-1" in d_api["tat"]
        assert d_api["mac_dinh"].get("chat") == "anl/chat-2"


def test_ui_quan_ly_model_mobile_390(trang, tmp_path, monkeypatch):
    """Màn hình di động 390px: kiểm tra giao diện không lỗi, điều khiển bật/tắt và lưu hoạt động."""
    _khoi_tao_loi(tmp_path, monkeypatch, {"ai-nhom-la": _module_co_nhom_la()})
    with MayChuThat(app, lifespan="on") as w:
        httpx.post(f"{w.url}/api/modules/ai-nhom-la/cai", json={"cau_hinh": {"api_keys": [K_TOT]}}, headers=QT)

        # Kích thước màn hình di động 390
        trang.khung(390, 844, dien_thoai=True)
        dang_nhap_quan_tri(trang, w.url)

        trang.di(w.url + "/He-thong-AI.dc.html#provider")
        trang.cho("document.getElementById('bang-ai')?.dataset.muc === 'provider'", mo_ta="vào tab Provider di động")

        trang.bam("document.querySelector('[data-pv-tab=\"model\"]')")
        trang.cho("document.querySelectorAll('[data-mo-nhom]').length >= 3", han=15, mo_ta="3 nhóm model di động")

        # Mở nhóm chat
        trang.bam("document.querySelector('[data-mo-nhom=\"chat\"]')")
        trang.cho("document.querySelectorAll('[data-model]').length >= 2", mo_ta="mở model chat")

        # Bấm Bỏ chọn tất cả nhóm chat trên di động
        trang.bam("document.querySelector('[data-bo-chon-tat-ca=\"chat\"]')")
        assert trang.js("document.getElementById('nut-luu-md')?.disabled === false")

        # Bấm Lưu thay đổi
        trang.bam("document.getElementById('nut-luu-md')")
        trang.cho("document.getElementById('nut-luu-md')?.disabled === true", han=10, mo_ta="lưu xong di động")

        # Kiểm tra backend
        r_api = httpx.get(f"{w.url}/api/modules/ai-nhom-la/model", headers=QT)
        assert r_api.status_code == 200
        d_api = r_api.json()
        assert "anl/chat-1" in d_api["tat"]
        assert "anl/chat-2" in d_api["tat"]
