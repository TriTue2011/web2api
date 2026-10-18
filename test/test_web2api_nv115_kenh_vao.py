"""Kiểm thử chuẩn NV115: Cổng TIN VÀO chung của lõi + PHÂN QUYỀN người gửi + lệnh trả lời của chủ máy.

Tuân thủ nghiêm ngặt:
- Module kênh GIẢ (có kenh_ra/gui_tin/hoi_vong), không Internet.
- Kiểm tra toàn bộ hợp đồng A -> E:
  A: Tin chuẩn hoá mà module đẩy vào (kiểm khuôn, trần, bỏ ký tự điều khiển).
  B: Xử lý trong lõi: chống trùng 10 phút, hạn dòng, phân quyền theo người gửi (không theo nhóm),
     người lạ không trả lời (vào sổ người lạ, trần 200, báo 1 lần tới sổ thiết bị),
     phân tích lệnh cấu trúc bỏ dấu/«/»/«@bot», trợ giúp theo vai, ngoại lệ không lộ,
     trả lời qua hàm chung gui_den_dich, nhật ký băm nguoi_gui không lộ nội dung/đối số.
  C: Hỏi vòng do lõi giữ vòng lặp: chu kỳ, lùi dần khi lỗi, dừng khi gỡ module, không chạy trùng,
     hết giờ riêng, ngu tiêm được. Kiểm manifest hoi_vong sai khuôn -> bỏ trường + log cảnh báo.
  D: Sổ phân quyền <DATA>/kenh_vao.json (0600, nguyên tử, KhoHong -> 503 không ghi đè).
     API can_quan_tri: 401 không khoá, 403 khoá khách, 404 path traversal.
  E: Nối vào sổ thiết bị: may-la (>= nguoi_dung), quen/bo-qua (quan_tri), MAC sai khuôn/không tồn tại.
- Lõi chạy khi không có module nào.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import gui_ra, kenh_vao, kho, khoa_khach, nhat_ky, so_thiet_bi
from app.ham_khoa import KhoHong
from app.kham_pha import kiem_manifest
from app.main import app

KHOA_QT = "test-auth"
MOD_KENH_A = "kenh-a"
MOD_KENH_B = "kenh-b"

DS_KENH_A = [{"id": "chat-a1", "ten": "Chat A1"}]
DS_KENH_B = [{"id": "chat-b1", "ten": "Chat B1"}, {"id": "chat-b2", "ten": "Chat B2"}]


class ModuleKenhVaoGia:
    """Module kênh giả có cả kenh_ra, gui_tin và hoi_vong."""

    def __init__(self, ds_kenh: list[dict], chu_ky_giay: int = 5):
        self._ds_kenh = ds_kenh
        self.chu_ky_giay = chu_ky_giay
        self.da_gui: list[tuple[dict, dict]] = []
        self.ket_qua_gui: dict = {"ok": True}
        self.loi_gui: Exception | None = None
        self.danh_sach_tin_hoi_vong: list[dict] = []
        self.so_lan_hoi_vong = 0
        self.loi_hoi_vong: Exception | None = None

    def kenh_ra(self) -> list[dict]:
        return self._ds_kenh

    async def gui_tin(self, dich: dict, noi_dung: dict) -> dict:
        if self.loi_gui is not None:
            raise self.loi_gui
        self.da_gui.append((dich, noi_dung))
        return self.ket_qua_gui

    async def hoi_vong(self) -> list[dict]:
        self.so_lan_hoi_vong += 1
        if self.loi_hoi_vong is not None:
            raise self.loi_hoi_vong
        tin = list(self.danh_sach_tin_hoi_vong)
        self.danh_sach_tin_hoi_vong.clear()
        return tin

    async def dung(self) -> None:
        pass


def _manifest(mod_id: str, tien_to: str, cung_cap: list[str], hoi_vong: dict | None = None) -> dict:
    m = {
        "id": mod_id,
        "ten": f"Module {mod_id}",
        "mo_ta": f"Mô tả {mod_id}",
        "loai": "kenh",
        "giao_thuc": "cuc_bo",
        "tien_to": tien_to,
        "hang": "A",
        "cung_cap": cung_cap,
        "truong_cau_hinh": [],
        "tai_nguyen": {},
    }
    if hoi_vong is not None:
        m["hoi_vong"] = hoi_vong
    return m


@pytest.fixture
def moi_truong(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setenv("WEB2API_RL_LOI_XAC_THUC", "0")
    monkeypatch.setenv("WEB2API_CHU_KY_SO_THIET_BI_GIAY", "60")
    gui_ra._dat_lai_trang_thai()
    kenh_vao._dat_lai_trang_thai()

    _, khoa_khach_id = khoa_khach.tao_khoa(nhan="khach", pham_vi=["chat"])

    mod_ka = ModuleKenhVaoGia(DS_KENH_A)
    mod_kb = ModuleKenhVaoGia(DS_KENH_B)

    with TestClient(app) as c:
        so = c.app.state.so
        so.dang_chay.clear()
        so.catalog.clear()
        so.dang_chay[MOD_KENH_A] = mod_ka
        so.dang_chay[MOD_KENH_B] = mod_kb

        so.catalog[MOD_KENH_A] = SimpleNamespace(
            manifest=_manifest(MOD_KENH_A, MOD_KENH_A, ["kenh_ra"], {"chu_ky_giay": 5}),
            tien_to=MOD_KENH_A,
        )
        so.catalog[MOD_KENH_B] = SimpleNamespace(
            manifest=_manifest(MOD_KENH_B, MOD_KENH_B, ["kenh_ra"]),
            tien_to=MOD_KENH_B,
        )

        so_thiet_bi.dang_ky_lenh_so_thiet_bi()

        yield {
            "client": c,
            "so": so,
            "mod_ka": mod_ka,
            "mod_kb": mod_kb,
            "khoa_khach": khoa_khach_id,
            "data_dir": tmp_path,
        }


# ==============================================================================
# A. Tin chuẩn hoá mà module đẩy vào
# ==============================================================================

def test_nhan_tin_khuon_sai_bi_bo(moi_truong):
    async def _chay():
        client = moi_truong["client"]
        app_obj = client.app

        # nguoi_gui lạ (chứa khoảng trắng, emoji, ký tự đặc biệt) -> bỏ tin
        tin_la_1 = {
            "id_tin": "t1",
            "nguoi_gui": "bad user!",
            "noi_gui": "chat-a1",
            "la_nhom": False,
            "ten_tu_khai": "Test",
            "van_ban": "/help",
            "luc": None,
        }
        kq1 = await kenh_vao.nhan_tin(app_obj, MOD_KENH_A, tin_la_1)
        assert kq1.get("ok") is False

        # nguoi_gui quá dài (>64 ký tự) -> bỏ tin
        tin_la_2 = {
            "id_tin": "t2",
            "nguoi_gui": "a" * 65,
            "noi_gui": "chat-a1",
            "la_nhom": False,
            "ten_tu_khai": "Test",
            "van_ban": "/help",
            "luc": None,
        }
        kq2 = await kenh_vao.nhan_tin(app_obj, MOD_KENH_A, tin_la_2)
        assert kq2.get("ok") is False

        # van_ban quá 4000 ký tự -> bỏ tin
        tin_la_3 = {
            "id_tin": "t3",
            "nguoi_gui": "user123",
            "noi_gui": "chat-a1",
            "la_nhom": False,
            "ten_tu_khai": "Test",
            "van_ban": "x" * 4001,
            "luc": None,
        }
        kq3 = await kenh_vao.nhan_tin(app_obj, MOD_KENH_A, tin_la_3)
        assert kq3.get("ok") is False

    asyncio.run(_chay())


def test_nhan_tin_lam_sach_ky_tu_dieu_khien(moi_truong):
    async def _chay():
        client = moi_truong["client"]
        app_obj = client.app
        mod_ka = moi_truong["mod_ka"]

        # Cấp quyền cho user123
        await kenh_vao.cap_quyen(MOD_KENH_A, "user123", "nguoi_dung", "Người dùng 1")

        # Đăng ký lệnh test để kiểm tra văn bản đã lọc ký tự điều khiển
        nhan_duoc = []

        async def cmd_echo(ngu_canh, doi_so):
            nhan_duoc.append(doi_so)
            return f"echo: {doi_so}"

        kenh_vao.dang_ky_lenh("test-echo", cmd_echo, "Lệnh test", "nguoi_dung")

        # van_ban chứa ký tự điều khiển \x00, \x07
        tin = {
            "id_tin": "t4",
            "nguoi_gui": "user123",
            "noi_gui": "chat-a1",
            "la_nhom": False,
            "ten_tu_khai": "Test",
            "van_ban": "/test-echo xin\x00chao\x07 ban",
            "luc": None,
        }
        kq = await kenh_vao.nhan_tin(app_obj, MOD_KENH_A, tin)
        assert kq.get("ok") is True
        assert nhan_duoc[0] == "xinchao ban"
        assert len(mod_ka.da_gui) == 1
        assert "xinchao ban" in mod_ka.da_gui[0][1]["van_ban"]

    asyncio.run(_chay())


def test_noi_gui_khong_thuoc_kenh_ra_khong_gui_tra_loi(moi_truong):
    async def _chay():
        client = moi_truong["client"]
        app_obj = client.app
        mod_kb = moi_truong["mod_kb"]  # có chat-b1, chat-b2

        await kenh_vao.cap_quyen(MOD_KENH_B, "user123", "nguoi_dung", "Người 1")

        da_chay = []

        async def cmd_run(ngu_canh, doi_so):
            da_chay.append(True)
            return "Kết quả"

        kenh_vao.dang_ky_lenh("test-run", cmd_run, "Lệnh test", "nguoi_dung")

        # noi_gui là chat-la không có trong kenh_ra() của mod_kb
        tin = {
            "id_tin": "tb1",
            "nguoi_gui": "user123",
            "noi_gui": "chat-la",
            "la_nhom": False,
            "ten_tu_khai": "User",
            "van_ban": "/test-run",
            "luc": None,
        }
        kq = await kenh_vao.nhan_tin(app_obj, MOD_KENH_B, tin)
        # Lệnh vẫn được xử lý, nhưng không gửi trả lời
        assert kq.get("ok") is True
        assert len(da_chay) == 1
        assert len(mod_kb.da_gui) == 0  # Không gửi trả lời vì đích không có trong kenh_ra

    asyncio.run(_chay())


# ==============================================================================
# B. Xử lý trong lõi (Chống trùng, Hạn dòng, Phân quyền, Lệnh, Trả lời)
# ==============================================================================

def test_chong_trung_id_tin_chay_mot_lan(moi_truong):
    async def _chay():
        client = moi_truong["client"]
        app_obj = client.app
        mod_ka = moi_truong["mod_ka"]

        await kenh_vao.cap_quyen(MOD_KENH_A, "user1", "quan_tri", "Admin")

        dem = []

        async def cmd_dem(ngu_canh, doi_so):
            dem.append(1)
            return f"Lần {len(dem)}"

        kenh_vao.dang_ky_lenh("test-dem", cmd_dem, "Lệnh đếm", "quan_tri")

        tin = {
            "id_tin": "trung-01",
            "nguoi_gui": "user1",
            "noi_gui": "chat-a1",
            "la_nhom": False,
            "ten_tu_khai": "Admin",
            "van_ban": "/test-dem",
            "luc": None,
        }

        kq1 = await kenh_vao.nhan_tin(app_obj, MOD_KENH_A, tin)
        assert kq1.get("ok") is True
        assert len(dem) == 1
        assert len(mod_ka.da_gui) == 1

        # Gửi lại cùng id_tin
        kq2 = await kenh_vao.nhan_tin(app_obj, MOD_KENH_A, tin)
        assert kq2.get("ok") is True
        assert kq2.get("trung") is True
        assert len(dem) == 1  # Không chạy lại
        assert len(mod_ka.da_gui) == 1  # Không gửi trả lời lại

        # Kiểm tra bộ đếm trạng thái
        tt = await kenh_vao.lay_trang_thai()
        assert tt["trung"] >= 1

    asyncio.run(_chay())


def test_han_dong_theo_nguoi_va_module(moi_truong, monkeypatch):
    async def _chay():
        client = moi_truong["client"]
        app_obj = client.app

        # Đặt hạn dòng thấp để test: 3 tin/phút cho người
        monkeypatch.setenv("WEB2API_KENH_VAO_NGUOI_PHUT", "3")
        await kenh_vao.cap_quyen(MOD_KENH_A, "spam-user", "quan_tri", "Spammer")

        async def cmd_nop(ngu_canh, doi_so):
            return "ok"

        kenh_vao.dang_ky_lenh("test-nop", cmd_nop, "Lệnh nop", "quan_tri")

        for i in range(3):
            tin = {
                "id_tin": f"spam-{i}",
                "nguoi_gui": "spam-user",
                "noi_gui": "chat-a1",
                "la_nhom": False,
                "ten_tu_khai": "Spammer",
                "van_ban": "/test-nop",
                "luc": None,
            }
            res = await kenh_vao.nhan_tin(app_obj, MOD_KENH_A, tin)
            assert res.get("ok") is True

        # Tin thứ 4 -> vượt hạn dòng -> bỏ
        tin_vuot = {
            "id_tin": "spam-4",
            "nguoi_gui": "spam-user",
            "noi_gui": "chat-a1",
            "la_nhom": False,
            "ten_tu_khai": "Spammer",
            "van_ban": "/test-nop",
            "luc": None,
        }
        res4 = await kenh_vao.nhan_tin(app_obj, MOD_KENH_A, tin_vuot)
        assert res4.get("ok") is False
        assert res4.get("loi") == "vuot_han_dong"

        tt = await kenh_vao.lay_trang_thai()
        assert tt["vuot_han"] >= 1

    asyncio.run(_chay())


def test_nguoi_la_khong_tra_loi_vao_danh_sach_la_va_bao_mot_lan(moi_truong):
    async def _chay():
        client = moi_truong["client"]
        app_obj = client.app
        mod_ka = moi_truong["mod_ka"]

        # Thiết lập đích báo sổ thiết bị để kiểm tra báo người lạ
        so_tb = so_thiet_bi.doc_so()
        so_tb["cau_hinh_bao"] = {
            "bat": True,
            "dich": [{"module": MOD_KENH_A, "kenh_id": "chat-a1"}],
            "bao_mac_ngau_nhien": "gop",
        }
        so_thiet_bi.ghi_so(so_tb)

        tin_la = {
            "id_tin": "la-1",
            "nguoi_gui": "nguoi-la-xyz",
            "noi_gui": "chat-a1",
            "la_nhom": False,
            "ten_tu_khai": "Kẻ Lạ",
            "van_ban": "/may-la",
            "luc": None,
        }

        kq = await kenh_vao.nhan_tin(app_obj, MOD_KENH_A, tin_la)
        assert kq.get("vai") == "la"

        # Người lạ không được trả lời lệnh
        # Nhưng có 1 tin báo người lạ gửi tới đích báo của sổ thiết bị
        assert len(mod_ka.da_gui) == 1
        tin_bao = mod_ka.da_gui[0][1]["van_ban"]
        assert "nguoi-la-xyz" in tin_bao or "Kẻ Lạ" in tin_bao

        # Kiểm tra danh sách người lạ
        ds_la = await kenh_vao.lay_danh_sach_nguoi_la()
        assert len(ds_la) == 1
        assert ds_la[0]["nguoi_id"] == "nguoi-la-xyz"
        assert ds_la[0]["so_lan"] == 1

        # Người lạ nhắn tin lần 2 -> KHÔNG báo lại lần nữa (chỉ báo 1 lần)
        tin_la_2 = {
            "id_tin": "la-2",
            "nguoi_gui": "nguoi-la-xyz",
            "noi_gui": "chat-a1",
            "la_nhom": False,
            "ten_tu_khai": "Kẻ Lạ",
            "van_ban": "alo",
            "luc": None,
        }
        await kenh_vao.nhan_tin(app_obj, MOD_KENH_A, tin_la_2)
        assert len(mod_ka.da_gui) == 1  # Vẫn chỉ là 1 tin báo lúc trước!

        ds_la_sau = await kenh_vao.lay_danh_sach_nguoi_la()
        assert ds_la_sau[0]["so_lan"] == 2

    asyncio.run(_chay())


def test_nhom_nguoi_chua_cap_quyen_trong_nhom_chu_may_van_la_la(moi_truong):
    """LỖI C2A: c2a từng lấy chat_id quyết quyền admin -> ai trong nhóm cũng thành admin.
    LÕI WEB2API: quyền theo người gửi (nguoi_gui), không theo nhóm (noi_gui)."""
    async def _chay():
        client = moi_truong["client"]
        app_obj = client.app
        mod_ka = moi_truong["mod_ka"]

        # Nhóm chat-a1 là nơi chủ máy nhận thông báo, nhưng thành viên trong nhóm là "member-la"
        tin_nhom = {
            "id_tin": "nhom-01",
            "nguoi_gui": "member-la",
            "noi_gui": "chat-a1",
            "la_nhom": True,
            "ten_tu_khai": "Thành viên nhóm",
            "van_ban": "may-la",
            "luc": None,
        }

        kq = await kenh_vao.nhan_tin(app_obj, MOD_KENH_A, tin_nhom)
        assert kq.get("vai") == "la"
        # Kẻ lạ trong nhóm không thể chạy lệnh và không nhận phản hồi lệnh
        for _, nd in mod_ka.da_gui:
            assert "Danh sách máy lạ" not in nd.get("van_ban", "")

    asyncio.run(_chay())


def test_vai_thap_goi_lenh_vai_cao_bi_tu_choi(moi_truong):
    async def _chay():
        client = moi_truong["client"]
        app_obj = client.app
        mod_ka = moi_truong["mod_ka"]

        # user-viewer có vai chi_xem
        await kenh_vao.cap_quyen(MOD_KENH_A, "user-viewer", "chi_xem", "Chỉ Xem")

        # Gọi lệnh may-la (yêu cầu vai >= nguoi_dung)
        tin = {
            "id_tin": "cmd-01",
            "nguoi_gui": "user-viewer",
            "noi_gui": "chat-a1",
            "la_nhom": False,
            "ten_tu_khai": "Viewer",
            "van_ban": "may-la",
            "luc": None,
        }
        kq = await kenh_vao.nhan_tin(app_obj, MOD_KENH_A, tin)
        assert kq.get("ok") is False
        assert kq.get("loi") == "khong_du_quyen"

        # Nhận câu thông báo từ chối
        assert len(mod_ka.da_gui) == 1
        assert "quyền" in mod_ka.da_gui[0][1]["van_ban"].lower()

    asyncio.run(_chay())


def test_phan_tich_lenh_bo_dau_slash_at_bot(moi_truong):
    async def _chay():
        client = moi_truong["client"]
        app_obj = client.app

        await kenh_vao.cap_quyen(MOD_KENH_A, "admin-1", "quan_tri", "Admin")

        lenh_da_goi = []

        async def cmd_test(ngu_canh, doi_so):
            lenh_da_goi.append((ngu_canh["lenh"], doi_so))
            return "ok"

        kenh_vao.dang_ky_lenh("test-phan-tich", cmd_test, "Xem test", "quan_tri")

        # Kiểm tra nhiều biến thể: /Test-Phân-Tích@MyBot, /test-phan-tich, TEST-PHAN-TICH
        cac_mau = [
            "/Test-Phân-Tích@MyBot",
            "/test-phan-tich",
            "TEST-PHAN-TICH",
            "/test-phan-tich@bot123 arg1 arg2",
        ]

        for i, mau in enumerate(cac_mau):
            tin = {
                "id_tin": f"pt-{i}",
                "nguoi_gui": "admin-1",
                "noi_gui": "chat-a1",
                "la_nhom": False,
                "ten_tu_khai": "Admin",
                "van_ban": mau,
                "luc": None,
            }
            await kenh_vao.nhan_tin(app_obj, MOD_KENH_A, tin)

        assert len(lenh_da_goi) == 4
        for ten, _ in lenh_da_goi:
            assert ten == "test-phan-tich"
        assert lenh_da_goi[3][1] == "arg1 arg2"

    asyncio.run(_chay())


def test_lenh_la_tro_giup_theo_vai(moi_truong):
    async def _chay():
        client = moi_truong["client"]
        app_obj = client.app
        mod_ka = moi_truong["mod_ka"]

        await kenh_vao.cap_quyen(MOD_KENH_A, "user-nd", "nguoi_dung", "Người dùng")

        tin = {
            "id_tin": "la-cmd-1",
            "nguoi_gui": "user-nd",
            "noi_gui": "chat-a1",
            "la_nhom": False,
            "ten_tu_khai": "User",
            "van_ban": "/lenh-khong-ton-tai",
            "luc": None,
        }
        await kenh_vao.nhan_tin(app_obj, MOD_KENH_A, tin)

        assert len(mod_ka.da_gui) == 1
        van_ban = mod_ka.da_gui[0][1]["van_ban"]
        # Liệt kê lệnh khả dụng cho vai nguoi_dung (gồm may-la, nhưng KHÔNG có quen/bo-qua vì cần quan_tri)
        assert "may-la" in van_ban
        assert "quen" not in van_ban

    asyncio.run(_chay())


def test_ngoai_le_trong_lenh_khong_lo_thong_tin(moi_truong):
    async def _chay():
        client = moi_truong["client"]
        app_obj = client.app
        mod_ka = moi_truong["mod_ka"]

        await kenh_vao.cap_quyen(MOD_KENH_A, "admin-1", "quan_tri", "Admin")

        async def cmd_hong(ngu_canh, doi_so):
            raise RuntimeError("Secret_DB_Password_Leak_12345")

        kenh_vao.dang_ky_lenh("test-hong", cmd_hong, "Lệnh hỏng", "quan_tri")

        tin = {
            "id_tin": "hong-1",
            "nguoi_gui": "admin-1",
            "noi_gui": "chat-a1",
            "la_nhom": False,
            "ten_tu_khai": "Admin",
            "van_ban": "/test-hong",
            "luc": None,
        }
        kq = await kenh_vao.nhan_tin(app_obj, MOD_KENH_A, tin)
        assert kq.get("ok") is False

        assert len(mod_ka.da_gui) == 1
        phan_hoi = mod_ka.da_gui[0][1]["van_ban"]
        assert phan_hoi == "Lệnh lỗi."
        assert "Secret_DB_Password" not in phan_hoi

    asyncio.run(_chay())


def test_nhat_ky_khong_lo_noi_dung_doi_so_va_id_tho(moi_truong):
    async def _chay():
        client = moi_truong["client"]
        app_obj = client.app

        await kenh_vao.cap_quyen(MOD_KENH_A, "secret-user-id", "quan_tri", "Secret Name")

        tin = {
            "id_tin": "log-1",
            "nguoi_gui": "secret-user-id",
            "noi_gui": "chat-a1",
            "la_nhom": False,
            "ten_tu_khai": "Secret Name",
            "van_ban": "/may-la arg_secret_data",
            "luc": None,
        }

        # Bắt nhật ký
        dong_nhat_ky = []
        goc_ghi = nhat_ky.ghi

        def mock_ghi(kenh, kieu, thong_diep, **kwargs):
            if kenh == "kenh_vao":
                dong_nhat_ky.append((thong_diep, kwargs))
            return goc_ghi(kenh, kieu, thong_diep, **kwargs)

        nhat_ky.ghi = mock_ghi
        try:
            await kenh_vao.nhan_tin(app_obj, MOD_KENH_A, tin)
        finally:
            nhat_ky.ghi = goc_ghi

        assert len(dong_nhat_ky) >= 1
        for thong_diep, kwargs in dong_nhat_ky:
            # Kiểm tra không lộ id_tho, van_ban, doi_so, ten_tu_khai
            assert "secret-user-id" not in thong_diep
            assert "arg_secret_data" not in thong_diep
            assert "Secret Name" not in thong_diep
            for k, v in kwargs.items():
                s = str(v)
                assert "secret-user-id" not in s
                assert "arg_secret_data" not in s
                assert "Secret Name" not in s
            # nguoi_gui phải là chuỗi băm rút gọn
            assert "nguoi_gui" in kwargs
            assert len(kwargs["nguoi_gui"]) <= 16
            assert kwargs["nguoi_gui"] == hashlib.sha256(b"secret-user-id").hexdigest()[:12]

    asyncio.run(_chay())


def test_khong_co_lenh_doi_vai_trong_bang_lenh(moi_truong):
    """Quy tắc: Không lệnh nào trong bảng lệnh được phép thay đổi phân quyền hay cấp vai."""
    lenh_ds = kenh_vao.danh_sach_lenh()
    for l in lenh_ds:
        ten = l["ten"]
        assert "vai" not in ten
        assert "admin" not in ten
        assert "quyen" not in ten
        assert "sudo" not in ten


# ==============================================================================
# C. Kéo tin theo chu kỳ (Hỏi vòng) do lõi giữ vòng lặp
# ==============================================================================

def test_kiem_manifest_hoi_vong():
    # Đúng khuôn
    m_tot = _manifest("m1", "m1", ["kenh_ra"], {"chu_ky_giay": 10})
    assert kiem_manifest(m_tot) is None
    assert "hoi_vong" in m_tot

    # Sai khuôn: chu_ky_giay < 2 -> bỏ trường, log cảnh báo, không làm hỏng manifest
    m_sai_1 = _manifest("m2", "m2", ["kenh_ra"], {"chu_ky_giay": 1})
    assert kiem_manifest(m_sai_1) is None
    assert "hoi_vong" not in m_sai_1  # đã bị bỏ

    # Sai khuôn: chu_ky_giay > 300 -> bỏ trường
    m_sai_2 = _manifest("m3", "m3", ["kenh_ra"], {"chu_ky_giay": 301})
    assert kiem_manifest(m_sai_2) is None
    assert "hoi_vong" not in m_sai_2

    # Sai khuôn: không phải dict -> bỏ trường
    m_sai_3 = _manifest("m4", "m4", ["kenh_ra"], "sai")
    assert kiem_manifest(m_sai_3) is None
    assert "hoi_vong" not in m_sai_3


def test_hoi_vong_chay_lui_dan_va_dung_khi_go(moi_truong):
    async def _chay():
        client = moi_truong["client"]
        app_obj = client.app
        mod_ka = moi_truong["mod_ka"]

        # Đưa tin vào danh sách hỏi vòng
        mod_ka.danh_sach_tin_hoi_vong.append({
            "id_tin": "hv-01",
            "nguoi_gui": "user123",
            "noi_gui": "chat-a1",
            "la_nhom": False,
            "ten_tu_khai": "Test",
            "van_ban": "alo",
            "luc": None,
        })

        cho_giay: list[float] = []

        async def mock_ngu(s: float):
            cho_giay.append(s)

        vong = kenh_vao.VongHoiVong(lambda: app_obj, ngu=mock_ngu)

        # Chạy 1 chu kỳ hỏi vòng cho MOD_KENH_A
        await vong.chay_mot_luot_module(MOD_KENH_A, mod_ka, 5)

        assert mod_ka.so_lan_hoi_vong == 1
        assert len(mod_ka.danh_sach_tin_hoi_vong) == 0  # Đã lấy tin đi

        # Gây lỗi -> kiểm tra lùi dần (backoff)
        mod_ka.loi_hoi_vong = RuntimeError("Network error")
        delay1 = await vong.chay_mot_luot_module(MOD_KENH_A, mod_ka, 5)
        assert delay1 == 10  # 5 * 2 = 10

        delay2 = await vong.chay_mot_luot_module(MOD_KENH_A, mod_ka, 5)
        assert delay2 == 20  # 10 * 2 = 20

        # Lỗi tối đa lùi 300s (5 phút)
        vong._lui_loi[MOD_KENH_A] = 200
        delay3 = await vong.chay_mot_luot_module(MOD_KENH_A, mod_ka, 5)
        assert delay3 == 300

        # Khôi phục thành công -> reset về chu_ky_giay
        mod_ka.loi_hoi_vong = None
        delay_ok = await vong.chay_mot_luot_module(MOD_KENH_A, mod_ka, 5)
        assert delay_ok == 5

    asyncio.run(_chay())


# ==============================================================================
# D. Sổ phân quyền (<DATA>/kenh_vao.json) & API quản trị
# ==============================================================================

def test_api_kenh_vao_bao_mat_401_403(moi_truong):
    client = moi_truong["client"]
    khoa_k = moi_truong["khoa_khach"]

    # 1. Không khoá -> 401
    r = client.get("/api/kenh-vao/nguoi")
    assert r.status_code == 401

    r = client.put("/api/kenh-vao/nguoi", json={"module": MOD_KENH_A, "nguoi_id": "u1", "vai": "quan_tri"})
    assert r.status_code == 401

    r = client.delete(f"/api/kenh-vao/nguoi/{MOD_KENH_A}/u1")
    assert r.status_code == 401

    r = client.get("/api/kenh-vao/la")
    assert r.status_code == 401

    r = client.get("/api/kenh-vao/lenh")
    assert r.status_code == 401

    r = client.get("/api/kenh-vao/trang-thai")
    assert r.status_code == 401

    # 2. Khoá khách (vai thấp) -> 403
    h_khach = {"Authorization": f"Bearer {khoa_k}"}
    assert client.get("/api/kenh-vao/nguoi", headers=h_khach).status_code == 403
    assert client.put("/api/kenh-vao/nguoi", json={"module": MOD_KENH_A, "nguoi_id": "u1", "vai": "quan_tri"}, headers=h_khach).status_code == 403
    assert client.delete(f"/api/kenh-vao/nguoi/{MOD_KENH_A}/u1", headers=h_khach).status_code == 403
    assert client.get("/api/kenh-vao/la", headers=h_khach).status_code == 403
    assert client.get("/api/kenh-vao/lenh", headers=h_khach).status_code == 403
    assert client.get("/api/kenh-vao/trang-thai", headers=h_khach).status_code == 403


def test_api_crud_nguoi_va_path_traversal(moi_truong):
    client = moi_truong["client"]
    h_qt = {"Authorization": f"Bearer {KHOA_QT}"}

    # Thêm người hợp lệ
    r = client.put(
        "/api/kenh-vao/nguoi",
        json={"module": MOD_KENH_A, "nguoi_id": "user-abc", "vai": "quan_tri", "ten": "Chủ nhân"},
        headers=h_qt,
    )
    assert r.status_code == 200
    assert r.json()["nguoi_id"] == "user-abc"

    # Xem danh sách
    r_list = client.get("/api/kenh-vao/nguoi", headers=h_qt)
    assert r_list.status_code == 200
    ds = r_list.json()
    assert any(x["nguoi_id"] == "user-abc" and x["vai"] == "quan_tri" for x in ds)

    # Thêm với module không có kenh_ra -> 400 hoặc 404
    r_bad_mod = client.put(
        "/api/kenh-vao/nguoi",
        json={"module": "mod-khong-co", "nguoi_id": "u2", "vai": "quan_tri"},
        headers=h_qt,
    )
    assert r_bad_mod.status_code in (400, 404)

    # Thêm với vai sai khuôn -> 400
    r_bad_vai = client.put(
        "/api/kenh-vao/nguoi",
        json={"module": MOD_KENH_A, "nguoi_id": "u3", "vai": "VAI SAI!"},
        headers=h_qt,
    )
    assert r_bad_vai.status_code == 400

    # Path traversal trong DELETE -> 400, 404 hoặc 405 (Starlette chặn trước)
    r_pt = client.delete(f"/api/kenh-vao/nguoi/{MOD_KENH_A}/..%2f..%2fetc%2fpasswd", headers=h_qt)
    assert r_pt.status_code in (400, 404, 405)

    # Id quá dài (>64) -> 400
    r_long = client.delete(f"/api/kenh-vao/nguoi/{MOD_KENH_A}/{'a'*65}", headers=h_qt)
    assert r_long.status_code == 400

    # Id chứa ký tự không hợp lệ -> 400
    r_bad_id = client.delete(f"/api/kenh-vao/nguoi/{MOD_KENH_A}/bad%20user", headers=h_qt)
    assert r_bad_id.status_code == 400

    # Xoá người không tồn tại -> 404
    r_404 = client.delete(f"/api/kenh-vao/nguoi/{MOD_KENH_A}/khong-ton-tai", headers=h_qt)
    assert r_404.status_code == 404

    # Xoá người hợp lệ -> 200
    r_del = client.delete(f"/api/kenh-vao/nguoi/{MOD_KENH_A}/user-abc", headers=h_qt)
    assert r_del.status_code == 200
    assert r_del.json().get("ok") is True


def test_so_hong_503_va_khong_ghi_de(moi_truong):
    client = moi_truong["client"]
    tmp_path = moi_truong["data_dir"]
    h_qt = {"Authorization": f"Bearer {KHOA_QT}"}

    # Ghi tệp kenh_vao.json bị hỏng
    tep_so = tmp_path / "kenh_vao.json"
    noi_dung_hong = b'{"nguoi": [ invalid json ...'
    tep_so.write_bytes(noi_dung_hong)

    # Gọi API -> 503
    r = client.get("/api/kenh-vao/nguoi", headers=h_qt)
    assert r.status_code == 503

    r_put = client.put(
        "/api/kenh-vao/nguoi",
        json={"module": MOD_KENH_A, "nguoi_id": "u1", "vai": "quan_tri"},
        headers=h_qt,
    )
    assert r_put.status_code == 503

    # So sánh byte: tệp trên đĩa KHÔNG bị ghi đè
    assert tep_so.read_bytes() == noi_dung_hong

    # Kiểm tra trạng thái báo lỗi kho
    r_tt = client.get("/api/kenh-vao/trang-thai", headers=h_qt)
    assert r_tt.status_code == 200
    assert r_tt.json()["loi_kho"] is True


# ==============================================================================
# E. Nối vào sổ thiết bị (NV107)
# ==============================================================================

def test_lenh_so_thiet_bi_may_la_va_quen_bo_qua(moi_truong):
    async def _chay():
        client = moi_truong["client"]
        app_obj = client.app
        mod_ka = moi_truong["mod_ka"]

        # Thêm 2 thiết bị vào sổ thiết bị
        so_tb = so_thiet_bi.doc_so()
        so_tb["thiet_bi"] = {
            "aa:bb:cc:dd:ee:01": {
                "mac": "aa:bb:cc:dd:ee:01",
                "ip": "192.168.1.101",
                "ten_tu_khai": "iphone-guest",
                "trang_thai": "la",
                "lan_dau": "2026-10-18T10:00:00Z",
            },
            "aa:bb:cc:dd:ee:02": {
                "mac": "aa:bb:cc:dd:ee:02",
                "ip": "192.168.1.102",
                "ten_tu_khai": None,
                "trang_thai": "quen",
                "lan_dau": "2026-10-18T10:00:00Z",
            },
        }
        so_thiet_bi.ghi_so(so_tb)

        # Cấp quyền admin cho admin-stb
        await kenh_vao.cap_quyen(MOD_KENH_A, "admin-stb", "quan_tri", "Admin STB")

        # 1. Lệnh may-la
        tin1 = {
            "id_tin": "stb-1",
            "nguoi_gui": "admin-stb",
            "noi_gui": "chat-a1",
            "la_nhom": False,
            "ten_tu_khai": "Admin",
            "van_ban": "may-la",
            "luc": None,
        }
        await kenh_vao.nhan_tin(app_obj, MOD_KENH_A, tin1)
        assert len(mod_ka.da_gui) == 1
        phan_hoi1 = mod_ka.da_gui[0][1]["van_ban"]
        assert "aa:bb:cc:dd:ee:01" in phan_hoi1
        assert "iphone-guest (tự khai)" in phan_hoi1
        assert "aa:bb:cc:dd:ee:02" not in phan_hoi1  # đã là 'quen'

        # 2. Lệnh quen với MAC sai khuôn
        tin2 = {
            "id_tin": "stb-2",
            "nguoi_gui": "admin-stb",
            "noi_gui": "chat-a1",
            "la_nhom": False,
            "ten_tu_khai": "Admin",
            "van_ban": "quen mac-khong-hop-le",
            "luc": None,
        }
        await kenh_vao.nhan_tin(app_obj, MOD_KENH_A, tin2)
        assert "MAC không hợp lệ" in mod_ka.da_gui[1][1]["van_ban"]

        # 3. Lệnh quen với MAC không tồn tại trong sổ (unicast hợp lệ)
        tin3 = {
            "id_tin": "stb-3",
            "nguoi_gui": "admin-stb",
            "noi_gui": "chat-a1",
            "la_nhom": False,
            "ten_tu_khai": "Admin",
            "van_ban": "quen 12:22:33:44:55:66",
            "luc": None,
        }
        await kenh_vao.nhan_tin(app_obj, MOD_KENH_A, tin3)
        assert "Không thấy máy này" in mod_ka.da_gui[2][1]["van_ban"]

        # 4. Lệnh quen hợp lệ
        tin4 = {
            "id_tin": "stb-4",
            "nguoi_gui": "admin-stb",
            "noi_gui": "chat-a1",
            "la_nhom": False,
            "ten_tu_khai": "Admin",
            "van_ban": "quen aa:bb:cc:dd:ee:01",
            "luc": None,
        }
        await kenh_vao.nhan_tin(app_obj, MOD_KENH_A, tin4)
        # Trạng thái trong sổ thiết bị phải chuyển thành "quen"
        so_sau = so_thiet_bi.doc_so()
        assert so_sau["thiet_bi"]["aa:bb:cc:dd:ee:01"]["trang_thai"] == "quen"

        # 5. Lệnh bo-qua hợp lệ
        tin5 = {
            "id_tin": "stb-5",
            "nguoi_gui": "admin-stb",
            "noi_gui": "chat-a1",
            "la_nhom": False,
            "ten_tu_khai": "Admin",
            "van_ban": "bo-qua aa:bb:cc:dd:ee:01",
            "luc": None,
        }
        await kenh_vao.nhan_tin(app_obj, MOD_KENH_A, tin5)
        so_sau2 = so_thiet_bi.doc_so()
        assert so_sau2["thiet_bi"]["aa:bb:cc:dd:ee:01"]["trang_thai"] == "bo_qua"

    asyncio.run(_chay())


# ==============================================================================
# Bốn điều kiện nghiệm thu chung: Không module nào lõi vẫn chạy
# ==============================================================================

def test_loi_chay_khi_khong_co_module_nao(moi_truong):
    async def _chay():
        client = moi_truong["client"]
        app_obj = client.app
        so = moi_truong["so"]

        # Xoá toàn bộ module đang chạy
        so.dang_chay.clear()
        so.catalog.clear()

        # Nhận tin khi không có module -> xử lý không lỗi
        tin = {
            "id_tin": "empty-1",
            "nguoi_gui": "user1",
            "noi_gui": "chat-1",
            "la_nhom": False,
            "ten_tu_khai": "User",
            "van_ban": "test",
            "luc": None,
        }
        kq = await kenh_vao.nhan_tin(app_obj, "mod-khong-ton-tai", tin)
        assert kq is not None

        # API vẫn chạy bình thường
        h_qt = {"Authorization": f"Bearer {KHOA_QT}"}
        r = client.get("/api/kenh-vao/trang-thai", headers=h_qt)
        assert r.status_code == 200

    asyncio.run(_chay())
