"""Kiểm thử chuẩn NV107: Sổ thiết bị mạng và báo MÁY LẠ qua chuẩn kenh_ra.

Tuân thủ nghiêm ngặt:
- Module nguồn và kênh đều GIẢ
- Không mạng Internet, không phụ thuộc bên ngoài
- Khoá giả rõ ràng
- Kiểm tra toàn bộ hợp đồng A -> E
- Rà soát bảo mật: 401 không khoá, 403 khoá khách, 404 MAC sai khuôn / Path Traversal
- MAC che trong nhật ký, không lộ nội dung tin hay IP trong log
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import gui_ra, khoa_khach, nhat_ky, so_thiet_bi
from app.ham_khoa import KhoHong
from app.main import app

KHOA_QT = "test-auth"

MOD_NGUON_1 = "nguon-mot"
MOD_NGUON_2 = "nguon-hai"
MOD_KENH_A = "kenh-a"
MOD_KENH_B = "kenh-b"

DS_KENH_A = [{"id": "bot-a1", "ten": "Bot A1"}]
DS_KENH_B = [{"id": "bot-b1", "ten": "Bot B1"}, {"id": "bot-b2", "ten": "Bot B2"}]


class ModuleNguonGia:
    """Module giả có chuẩn thiet_bi (cung cấp danh sách thực thể mạng)."""

    def __init__(self, tien_to: str, thuc_the_list: list[dict] | None = None):
        self.tien_to = tien_to
        self.thuc_the_list = thuc_the_list if thuc_the_list is not None else []
        self.loi: Exception | None = None

    async def thuc_the(self, loc: dict | None = None) -> list[dict]:
        if self.loi is not None:
            raise self.loi
        return self.thuc_the_list

    async def trang_thai(self, ids: list[str]) -> dict:
        return {i: {"gia_tri": "on", "hop_le": True} for i in ids}

    async def thuc_hien(self, id: str, hanh_dong: str, tham_so: dict) -> dict:
        return {"ok": True}

    async def dung(self) -> None:
        pass


class ModuleKenhGia:
    """Module giả có chuẩn kenh_ra (gửi tin ra ngoài)."""

    def __init__(self, ds_kenh: list[dict]):
        self._ds_kenh = ds_kenh
        self.da_gui: list[tuple[dict, dict]] = []
        self.ket_qua: dict = {"ok": True}
        self.loi: Exception | None = None

    def kenh_ra(self) -> list[dict]:
        return self._ds_kenh

    async def gui_tin(self, dich: dict, noi_dung: dict) -> dict:
        if self.loi is not None:
            raise self.loi
        self.da_gui.append((dich, noi_dung))
        return self.ket_qua

    async def dung(self) -> None:
        pass


def _manifest(mod_id: str, tien_to: str, cung_cap: list[str]) -> dict:
    return {
        "id": mod_id,
        "ten": f"Module {mod_id}",
        "mo_ta": f"Mô tả {mod_id}",
        "loai": "tien_ich",
        "giao_thuc": "cuc_bo",
        "tien_to": tien_to,
        "hang": "A",
        "cung_cap": cung_cap,
    }


@pytest.fixture
def moi_truong(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setenv("WEB2API_RL_LOI_XAC_THUC", "0")
    monkeypatch.setenv("WEB2API_CHU_KY_SO_THIET_BI_GIAY", "60")
    gui_ra._dat_lai_trang_thai()

    _, khoa_khach_id = khoa_khach.tao_khoa(nhan="khach", pham_vi=["thiet_bi:doc"])

    mod_n1 = ModuleNguonGia(MOD_NGUON_1)
    mod_n2 = ModuleNguonGia(MOD_NGUON_2)
    mod_ka = ModuleKenhGia(DS_KENH_A)
    mod_kb = ModuleKenhGia(DS_KENH_B)

    with TestClient(app) as c:
        so = c.app.state.so
        so.dang_chay[MOD_NGUON_1] = mod_n1
        so.dang_chay[MOD_NGUON_2] = mod_n2
        so.dang_chay[MOD_KENH_A] = mod_ka
        so.dang_chay[MOD_KENH_B] = mod_kb

        so.catalog[MOD_NGUON_1] = SimpleNamespace(manifest=_manifest(MOD_NGUON_1, MOD_NGUON_1, ["thiet_bi"]), tien_to=MOD_NGUON_1)
        so.catalog[MOD_NGUON_2] = SimpleNamespace(manifest=_manifest(MOD_NGUON_2, MOD_NGUON_2, ["thiet_bi"]), tien_to=MOD_NGUON_2)
        so.catalog[MOD_KENH_A] = SimpleNamespace(manifest=_manifest(MOD_KENH_A, MOD_KENH_A, ["kenh_ra"]), tien_to=MOD_KENH_A)
        so.catalog[MOD_KENH_B] = SimpleNamespace(manifest=_manifest(MOD_KENH_B, MOD_KENH_B, ["kenh_ra"]), tien_to=MOD_KENH_B)

        yield c, mod_n1, mod_n2, mod_ka, mod_kb, {"qt": KHOA_QT, "khach": khoa_khach_id}

    for m in (MOD_NGUON_1, MOD_NGUON_2, MOD_KENH_A, MOD_KENH_B):
        so.dang_chay.pop(m, None)
        so.catalog.pop(m, None)
    gui_ra._dat_lai_trang_thai()


# ==============================================================================
# 1. ĐƠN VỊ: Chuẩn hoá MAC & làm sạch dữ liệu không tin
# ==============================================================================

def test_chuan_hoa_mac():
    # Nhận các định dạng hợp lệ
    cac_mau = [
        "AA:BB:CC:DD:EE:FF",
        "aa-bb-cc-dd-ee-ff",
        "aabb.ccdd.eeff",
        "aabbccddeeff",
        "  Aa:bB:cC:Dd:eE:fF  ",
    ]
    for mau in cac_mau:
        mac, _ = so_thiet_bi.chuan_hoa_mac(mau)
        assert mac == "aa:bb:cc:dd:ee:ff", f"Thất bại với mẫu {mau}"

    # Bỏ qua: sai khuôn, toàn 0, broadcast, multicast
    assert so_thiet_bi.chuan_hoa_mac("00:00:00:00:00:00") == (None, False)
    assert so_thiet_bi.chuan_hoa_mac("ff:ff:ff:ff:ff:ff") == (None, False)
    # Multicast: bit thấp octet đầu = 1 (01, 33, ...)
    assert so_thiet_bi.chuan_hoa_mac("01:00:5e:00:00:01") == (None, False)
    assert so_thiet_bi.chuan_hoa_mac("33:33:00:00:00:01") == (None, False)
    # Sai khuôn
    assert so_thiet_bi.chuan_hoa_mac("not-a-mac") == (None, False)
    assert so_thiet_bi.chuan_hoa_mac("aabbccddeef") == (None, False)
    assert so_thiet_bi.chuan_hoa_mac("aabbccddeeff11") == (None, False)
    assert so_thiet_bi.chuan_hoa_mac(12345) == (None, False)

    # Cờ ngẫu nhiên: bit 0x02 của octet đầu
    # 02:00:00:00:00:01 -> octet đầu 0x02, bit 0x02 bật, bit 0x01 tắt -> unicast, ngẫu nhiên
    mac, ngau_nhien = so_thiet_bi.chuan_hoa_mac("02:00:00:00:00:01")
    assert mac == "02:00:00:00:00:01"
    assert ngau_nhien is True

    # 00:11:22:33:44:55 -> octet đầu 0x00, bit 0x02 tắt -> không ngẫu nhiên
    mac, ngau_nhien = so_thiet_bi.chuan_hoa_mac("00:11:22:33:44:55")
    assert mac == "00:11:22:33:44:55"
    assert ngau_nhien is False


def test_lam_sach_ten_tu_khai():
    # Tên máy tự khai là dữ liệu KHÔNG TIN: loại bỏ ký tự điều khiển, ngắt dòng, cắt 40 ký tự
    raw = "  My\r\nPhone\x00\x08-Extra-Long-Name-That-Exceeds-Forty-Characters-Limit-123456  "
    clean = so_thiet_bi.lam_sach_ten_tu_khai(raw)
    assert "\r" not in clean
    assert "\n" not in clean
    assert "\x00" not in clean
    assert len(clean) <= 40
    assert clean.startswith("MyPhone")


def test_che_mac():
    mac = "aa:bb:cc:dd:ee:ff"
    che = so_thiet_bi.che_mac(mac)
    assert che == "aa:bb:cc:**:**:ff"


# ==============================================================================
# 2. HỢP ĐỒNG A: Hàm gửi dùng chung gui_den_dich
# ==============================================================================

def test_gui_den_dich_hai_dich_thieu_kenh_id_khong_gui(moi_truong):
    c, _, _, _, mod_kb, _ = moi_truong
    # mod-kenh-b có 2 đích: bot-b1, bot-b2. Thiếu kenh_id -> 400 và không gửi
    async def _chay():
        with pytest.raises(gui_ra.LoiOpenAI) as exc:
            await gui_ra.gui_den_dich(c.app, MOD_KENH_B, None, {"van_ban": "test"})
        assert exc.value.ma == 400
        assert exc.value.code == "kenh_required"
        assert len(mod_kb.da_gui) == 0

    asyncio.run(_chay())


def test_gui_den_dich_ok_khong_phai_bool_that_bai(moi_truong):
    c, _, _, mod_ka, _, _ = moi_truong
    # Module trả ok là chuỗi "true" (không phải bool thật) -> 502
    async def _chay():
        mod_ka.ket_qua = {"ok": "true"}
        with pytest.raises(gui_ra.LoiOpenAI) as exc:
            await gui_ra.gui_den_dich(c.app, MOD_KENH_A, "bot-a1", {"van_ban": "test"})
        assert exc.value.ma == 502
        assert exc.value.code == "gui_that_bai"

    asyncio.run(_chay())


# ==============================================================================
# 3. HỢP ĐỒNG B: Kho dữ liệu so_thiet_bi.json & KhoHong
# ==============================================================================

def test_kho_hong_dung_ghi_va_503(moi_truong, tmp_path):
    c, _, _, _, _, khoa = moi_truong
    p = tmp_path / "so_thiet_bi.json"
    p.write_bytes(b"INVALID_JSON_CORRUPTED{{{")

    # GET trạng thái hiện loi_kho: true
    r = c.get("/api/so-thiet-bi/trang-thai", headers={"Authorization": f"Bearer {khoa['qt']}"})
    assert r.status_code == 200
    assert r.json().get("loi_kho") is True

    # Các API khác trả 503
    r2 = c.get("/api/so-thiet-bi", headers={"Authorization": f"Bearer {khoa['qt']}"})
    assert r2.status_code == 503

    r3 = c.post("/api/so-thiet-bi/chot", headers={"Authorization": f"Bearer {khoa['qt']}"})
    assert r3.status_code == 503

    # Tệp hỏng được giữ nguyên văn byte, không bị ghi đè!
    assert p.read_bytes() == b"INVALID_JSON_CORRUPTED{{{"


# ==============================================================================
# 4. HỢP ĐỒNG C & D: Vòng quét, chốt lần đầu, chống bão, MAC ngẫu nhiên
# ==============================================================================

def test_lan_dau_chua_chot_khong_bao_chot_xong_may_moi_bao_mot_lan(moi_truong):
    c, mod_n1, _, mod_ka, _, khoa = moi_truong
    vong = so_thiet_bi.VongQuetSoThietBi(lambda: c.app)

    async def _chay():
        # Cấu hình báo trước: bật gửi tới mod-kenh-a
        r_bao = c.put(
            "/api/so-thiet-bi/bao",
            headers={"Authorization": f"Bearer {khoa['qt']}"},
            json={"bat": True, "dich": [{"module": MOD_KENH_A, "kenh_id": "bot-a1"}], "bao_mac_ngau_nhien": "co"},
        )
        assert r_bao.status_code == 200

        # Nguồn 1 có máy M1
        mod_n1.thuc_the_list = [
            {
                "id": f"{MOD_NGUON_1}.dev1",
                "ten": "Camera Nhà",
                "loai": "camera",
                "thuoc_tinh": {"mac": "00:11:22:33:44:55", "ip": "192.168.1.10", "ten_may_tu_khai": "cam-dhcp"},
            }
        ]

        # Chu kỳ 1: chưa chốt -> máy ghi vào sổ nhưng KHÔNG báo ra kênh
        await vong.chay_mot_chu_ky()
        assert len(mod_ka.da_gui) == 0

        r_tt = c.get("/api/so-thiet-bi/trang-thai", headers={"Authorization": f"Bearer {khoa['qt']}"})
        assert r_tt.json()["tong"] == 1
        assert r_tt.json()["da_chot"] is False
        assert r_tt.json()["chua_bao"] == 1

        # Quản trị chốt sổ
        r_chot = c.post("/api/so-thiet-bi/chot", headers={"Authorization": f"Bearer {khoa['qt']}"})
        assert r_chot.status_code == 200
        assert r_chot.json()["da_chot"] is True

        # Chu kỳ 2: chạy lại, máy cũ đã chốt thành quen nên KHÔNG báo
        await vong.chay_mot_chu_ky()
        assert len(mod_ka.da_gui) == 0

        # Bây giờ xuất hiện máy M2 (máy lạ mới)
        mod_n1.thuc_the_list.append({
            "id": f"{MOD_NGUON_1}.dev2",
            "ten": "Máy Lạ",
            "loai": "dien_thoai",
            "thuoc_tinh": {"mac": "00:11:22:33:44:66", "ip": "192.168.1.20", "ten_may_tu_khai": "iphone-guest"},
        })

        # Chu kỳ 3: máy mới M2 được báo ĐÚNG 1 LẦN
        await vong.chay_mot_chu_ky()
        assert len(mod_ka.da_gui) == 1
        dich_da_gui, tin_da_gui = mod_ka.da_gui[-1]
        assert dich_da_gui["kenh_id"] == "bot-a1"
        assert "00:11:22:33:44:66" in tin_da_gui["van_ban"]
        assert "(tự khai)" in tin_da_gui["van_ban"]

        # Chu kỳ 4: không báo lại máy M2
        await vong.chay_mot_chu_ky()
        assert len(mod_ka.da_gui) == 1

    asyncio.run(_chay())


def test_nguon_loi_hoac_tra_rong_khong_lam_may_cu_thanh_moi(moi_truong):
    c, mod_n1, _, mod_ka, _, khoa = moi_truong
    vong = so_thiet_bi.VongQuetSoThietBi(lambda: c.app)

    async def _chay():
        # Chốt sổ với M1
        mod_n1.thuc_the_list = [{
            "id": f"{MOD_NGUON_1}.dev1",
            "ten": "May1",
            "loai": "laptop",
            "thuoc_tinh": {"mac": "00:aa:bb:cc:dd:ee", "ip": "192.168.1.5"},
        }]
        await vong.chay_mot_chu_ky()
        c.post("/api/so-thiet-bi/chot", headers={"Authorization": f"Bearer {khoa['qt']}"})

        # Bật báo
        c.put(
            "/api/so-thiet-bi/bao",
            headers={"Authorization": f"Bearer {khoa['qt']}"},
            json={"bat": True, "dich": [{"module": MOD_KENH_A, "kenh_id": "bot-a1"}]},
        )

        # Chu kỳ sau: nguồn 1 bị lỗi / quá hạn
        mod_n1.loi = TimeoutError("router treo")
        await vong.chay_mot_chu_ky()

        # Kiểm tra trạng thái nguồn ghi nhận lỗi, máy không bị xoá
        r_tt = c.get("/api/so-thiet-bi/trang-thai", headers={"Authorization": f"Bearer {khoa['qt']}"})
        assert r_tt.json()["tong"] == 1
        assert r_tt.json()["trang_thai_nguon"][MOD_NGUON_1]["trang_thai"] == "loi"

        # Chu kỳ kế: nguồn 1 trở lại bình thường
        mod_n1.loi = None
        await vong.chay_mot_chu_ky()

        # Máy M1 KHÔNG bị coi là máy mới, KHÔNG gửi báo
        assert len(mod_ka.da_gui) == 0

    asyncio.run(_chay())


def test_hai_nguon_cung_mot_mac_gop_mot_dong(moi_truong):
    c, mod_n1, mod_n2, _, _, khoa = moi_truong
    vong = so_thiet_bi.VongQuetSoThietBi(lambda: c.app)

    async def _chay():
        mac_chung = "00:11:22:aa:bb:cc"
        mod_n1.thuc_the_list = [{
            "id": f"{MOD_NGUON_1}.d1",
            "ten": "May A",
            "loai": "pc",
            "thuoc_tinh": {"mac": mac_chung, "ip": "192.168.1.15"},
        }]
        mod_n2.thuc_the_list = [{
            "id": f"{MOD_NGUON_2}.d2",
            "ten": "May B",
            "loai": "pc",
            "thuoc_tinh": {"mac": mac_chung, "ip": "192.168.1.15", "ten_may_tu_khai": "desktop-pc"},
        }]

        await vong.chay_mot_chu_ky()
        r = c.get("/api/so-thiet-bi", headers={"Authorization": f"Bearer {khoa['qt']}"})
        assert r.status_code == 200
        ds = r.json()
        assert len(ds) == 1
        assert ds[0]["mac"] == mac_chung
        assert ds[0]["ten_tu_khai"] == "desktop-pc"

    asyncio.run(_chay())


def test_chong_bao_may_moi_tam_dung(moi_truong):
    c, mod_n1, _, mod_ka, _, khoa = moi_truong
    vong = so_thiet_bi.VongQuetSoThietBi(lambda: c.app)

    async def _chay():
        # Đã chốt và bật báo
        c.post("/api/so-thiet-bi/chot", headers={"Authorization": f"Bearer {khoa['qt']}"})
        c.put(
            "/api/so-thiet-bi/bao",
            headers={"Authorization": f"Bearer {khoa['qt']}"},
            json={"bat": True, "dich": [{"module": MOD_KENH_A, "kenh_id": "bot-a1"}]},
        )

        # Đột ngột xuất hiện 7 máy mới (> max(5, 7/2))
        mod_n1.thuc_the_list = [
            {
                "id": f"{MOD_NGUON_1}.d{i}",
                "ten": f"May {i}",
                "loai": "iot",
                "thuoc_tinh": {"mac": f"00:11:22:33:44:{i:02x}"},
            }
            for i in range(1, 8)
        ]

        await vong.chay_mot_chu_ky()

        # Chỉ gửi MỘT tin tóm tắt cảnh báo bão, không gửi 7 tin!
        assert len(mod_ka.da_gui) == 1
        assert "bão" in mod_ka.da_gui[0][1]["van_ban"].lower() or "tạm dừng" in mod_ka.da_gui[0][1]["van_ban"].lower()

        # Trạng thái phải là tam_dung: true
        r_tt = c.get("/api/so-thiet-bi/trang-thai", headers={"Authorization": f"Bearer {khoa['qt']}"})
        assert r_tt.json()["tam_dung"] is True

        # Quản trị gọi tiếp tục
        r_ttuc = c.post("/api/so-thiet-bi/tiep-tuc", headers={"Authorization": f"Bearer {khoa['qt']}"})
        assert r_ttuc.status_code == 200
        assert r_ttuc.json()["tam_dung"] is False

    asyncio.run(_chay())


def test_bao_mac_ngau_nhien_ba_che_do(moi_truong):
    c, mod_n1, _, mod_ka, _, khoa = moi_truong
    vong = so_thiet_bi.VongQuetSoThietBi(lambda: c.app)

    async def _chay():
        # MAC ngẫu nhiên: 02:xx:xx:xx:xx:xx
        mac_nn_1 = "02:11:22:33:44:01"
        mac_nn_2 = "02:11:22:33:44:02"

        c.post("/api/so-thiet-bi/chot", headers={"Authorization": f"Bearer {khoa['qt']}"})

        # Chế độ "khong": không báo
        c.put(
            "/api/so-thiet-bi/bao",
            headers={"Authorization": f"Bearer {khoa['qt']}"},
            json={"bat": True, "dich": [{"module": MOD_KENH_A, "kenh_id": "bot-a1"}], "bao_mac_ngau_nhien": "khong"},
        )
        mod_n1.thuc_the_list = [{"id": f"{MOD_NGUON_1}.nn1", "thuoc_tinh": {"mac": mac_nn_1}}]
        await vong.chay_mot_chu_ky()
        assert len(mod_ka.da_gui) == 0  # Không báo

        # Chế độ "gop": gộp các máy MAC ngẫu nhiên vào 1 tin tóm tắt
        c.put(
            "/api/so-thiet-bi/bao",
            headers={"Authorization": f"Bearer {khoa['qt']}"},
            json={"bat": True, "dich": [{"module": MOD_KENH_A, "kenh_id": "bot-a1"}], "bao_mac_ngau_nhien": "gop"},
        )
        mod_n1.thuc_the_list.append({"id": f"{MOD_NGUON_1}.nn2", "thuoc_tinh": {"mac": mac_nn_2}})
        await vong.chay_mot_chu_ky()
        assert len(mod_ka.da_gui) == 1
        assert "ngẫu nhiên" in mod_ka.da_gui[0][1]["van_ban"].lower()

    asyncio.run(_chay())


def test_gui_loi_o_dich_a_thanh_cong_o_dich_b(moi_truong):
    c, mod_n1, _, mod_ka, mod_kb, khoa = moi_truong
    vong = so_thiet_bi.VongQuetSoThietBi(lambda: c.app)

    async def _chay():
        c.post("/api/so-thiet-bi/chot", headers={"Authorization": f"Bearer {khoa['qt']}"})
        c.put(
            "/api/so-thiet-bi/bao",
            headers={"Authorization": f"Bearer {khoa['qt']}"},
            json={
                "bat": True,
                "dich": [
                    {"module": MOD_KENH_A, "kenh_id": "bot-a1"},
                    {"module": MOD_KENH_B, "kenh_id": "bot-b1"},
                ],
                "bao_mac_ngau_nhien": "co",
            },
        )

        # Đích A lỗi, đích B bình thường
        mod_ka.loi = RuntimeError("Lỗi mạng đích A")

        mod_n1.thuc_the_list = [{"id": f"{MOD_NGUON_1}.d1", "thuoc_tinh": {"mac": "00:11:22:33:44:88"}}]
        await vong.chay_mot_chu_ky()

        # Đích B đã nhận tin
        assert len(mod_kb.da_gui) == 1
        # Đích A lỗi nên chưa có tin thành công
        assert len(mod_ka.da_gui) == 0

        # Chu kỳ 2: Đích A sửa xong lỗi
        mod_ka.loi = None
        await vong.chay_mot_chu_ky()

        # Đích A được thử lại và thành công
        assert len(mod_ka.da_gui) == 1
        # Đích B KHÔNG bị gửi lại! (vẫn chỉ có 1 tin)
        assert len(mod_kb.da_gui) == 1

    asyncio.run(_chay())


def test_dich_moi_them_sau_nhan_may_cho(moi_truong):
    c, mod_n1, _, mod_ka, mod_kb, khoa = moi_truong
    vong = so_thiet_bi.VongQuetSoThietBi(lambda: c.app)

    async def _chay():
        c.post("/api/so-thiet-bi/chot", headers={"Authorization": f"Bearer {khoa['qt']}"})
        # Ban đầu chỉ cấu hình đích A
        c.put(
            "/api/so-thiet-bi/bao",
            headers={"Authorization": f"Bearer {khoa['qt']}"},
            json={"bat": True, "dich": [{"module": MOD_KENH_A, "kenh_id": "bot-a1"}]},
        )

        mod_n1.thuc_the_list = [{"id": f"{MOD_NGUON_1}.d1", "thuoc_tinh": {"mac": "00:11:22:33:44:99"}}]
        await vong.chay_mot_chu_ky()
        assert len(mod_ka.da_gui) == 1

        # Bây giờ cấu hình thêm đích B
        c.put(
            "/api/so-thiet-bi/bao",
            headers={"Authorization": f"Bearer {khoa['qt']}"},
            json={
                "bat": True,
                "dich": [
                    {"module": MOD_KENH_A, "kenh_id": "bot-a1"},
                    {"module": MOD_KENH_B, "kenh_id": "bot-b1"},
                ],
            },
        )

        # Chu kỳ kế: đích B nhận máy đang chờ, đích A không nhận lại
        await vong.chay_mot_chu_ky()
        assert len(mod_kb.da_gui) == 1
        assert len(mod_ka.da_gui) == 1

    asyncio.run(_chay())


def test_module_kenh_bi_go_dich_hop_le_false_va_may_cho_van_con(moi_truong):
    c, mod_n1, _, _, _, khoa = moi_truong
    vong = so_thiet_bi.VongQuetSoThietBi(lambda: c.app)

    async def _chay():
        c.post("/api/so-thiet-bi/chot", headers={"Authorization": f"Bearer {khoa['qt']}"})
        c.put(
            "/api/so-thiet-bi/bao",
            headers={"Authorization": f"Bearer {khoa['qt']}"},
            json={"bat": True, "dich": [{"module": MOD_KENH_A, "kenh_id": "bot-a1"}]},
        )

        # Gỡ module kênh A
        c.app.state.so.dang_chay.pop(MOD_KENH_A, None)

        mod_n1.thuc_the_list = [{"id": f"{MOD_NGUON_1}.d1", "thuoc_tinh": {"mac": "00:11:22:33:44:aa"}}]
        await vong.chay_mot_chu_ky()

        # Trạng thái hiện đích không hợp lệ
        r_tt = c.get("/api/so-thiet-bi/trang-thai", headers={"Authorization": f"Bearer {khoa['qt']}"})
        assert r_tt.json()["dich_hop_le"] is False
        # Máy lạ vẫn chờ (chua_bao == 1)
        assert r_tt.json()["chua_bao"] == 1

    asyncio.run(_chay())


# ==============================================================================
# 5. HỢP ĐỒNG E: BẢO MẬT & API QUẢN TRỊ
# ==============================================================================

def test_bao_mat_leo_thang_quyen_401_403(moi_truong):
    c, _, _, _, _, khoa = moi_truong

    # Không khoá -> 401
    assert c.get("/api/so-thiet-bi").status_code == 401
    assert c.get("/api/so-thiet-bi/trang-thai").status_code == 401
    assert c.post("/api/so-thiet-bi/chot").status_code == 401
    assert c.post("/api/so-thiet-bi/tiep-tuc").status_code == 401
    assert c.put("/api/so-thiet-bi/bao", json={"bat": True}).status_code == 401
    assert c.get("/api/so-thiet-bi/bao").status_code == 401
    assert c.post("/api/so-thiet-bi/bao/thu").status_code == 401
    assert c.patch("/api/so-thiet-bi/00:11:22:33:44:55", json={"ten": "X"}).status_code == 401
    assert c.delete("/api/so-thiet-bi/00:11:22:33:44:55").status_code == 401

    # Khoá khách (vai thấp) gọi đường quản trị -> 403
    hdr_khach = {"Authorization": f"Bearer {khoa['khach']}"}
    assert c.get("/api/so-thiet-bi", headers=hdr_khach).status_code == 403
    assert c.get("/api/so-thiet-bi/trang-thai", headers=hdr_khach).status_code == 403
    assert c.post("/api/so-thiet-bi/chot", headers=hdr_khach).status_code == 403
    assert c.post("/api/so-thiet-bi/tiep-tuc", headers=hdr_khach).status_code == 403
    assert c.put("/api/so-thiet-bi/bao", headers=hdr_khach, json={"bat": True}).status_code == 403
    assert c.get("/api/so-thiet-bi/bao", headers=hdr_khach).status_code == 403
    assert c.post("/api/so-thiet-bi/bao/thu", headers=hdr_khach).status_code == 403
    assert c.patch("/api/so-thiet-bi/00:11:22:33:44:55", headers=hdr_khach, json={"ten": "X"}).status_code == 403
    assert c.delete("/api/so-thiet-bi/00:11:22:33:44:55", headers=hdr_khach).status_code == 403


def test_mac_sai_khuon_path_traversal_404(moi_truong):
    c, _, _, _, _, khoa = moi_truong
    hdr_qt = {"Authorization": f"Bearer {khoa['qt']}"}

    # MAC sai khuôn hoặc cố tình Path Traversal -> 404
    cac_mac_sai = [
        "invalid-mac",
        "00:11:22",
        "00:11:22:33:44:55:66",
        "%2e%2e%2f%2e%2e%2fetc%2fpasswd",
        "%2e%2e%2fetc%2fpasswd",
        "00:11:22:33:44:ZZ",
    ]
    for m in cac_mac_sai:
        r_patch = c.patch(f"/api/so-thiet-bi/{m}", headers=hdr_qt, json={"ten": "ABC"})
        assert r_patch.status_code == 404, f"PATCH {m} không trả 404 mà là {r_patch.status_code}"
        r_del = c.delete(f"/api/so-thiet-bi/{m}", headers=hdr_qt)
        assert r_del.status_code == 404, f"DELETE {m} không trả 404 mà là {r_del.status_code}"


def test_api_patch_va_delete(moi_truong):
    c, mod_n1, _, _, _, khoa = moi_truong
    vong = so_thiet_bi.VongQuetSoThietBi(lambda: c.app)
    hdr_qt = {"Authorization": f"Bearer {khoa['qt']}"}

    mac = "00:11:22:33:44:cc"
    mod_n1.thuc_the_list = [{"id": f"{MOD_NGUON_1}.d1", "thuoc_tinh": {"mac": mac}}]
    asyncio.run(vong.chay_mot_chu_ky())

    # PATCH đặt tên và trạng thái
    r_patch = c.patch(f"/api/so-thiet-bi/{mac}", headers=hdr_qt, json={"ten": "Tivi Phòng Khách", "trang_thai": "quen"})
    assert r_patch.status_code == 200
    assert r_patch.json()["ten"] == "Tivi Phòng Khách"
    assert r_patch.json()["trang_thai"] == "quen"

    # PATCH trạng thái không hợp lệ -> 400
    r_sai = c.patch(f"/api/so-thiet-bi/{mac}", headers=hdr_qt, json={"trang_thai": "khong_hop_le"})
    assert r_sai.status_code == 400

    # Lọc danh sách theo trạng thái và tìm kiếm
    r_get = c.get("/api/so-thiet-bi?q=Tivi", headers=hdr_qt)
    assert r_get.status_code == 200
    assert len(r_get.json()) == 1

    r_get_none = c.get("/api/so-thiet-bi?q=KhongCoDau", headers=hdr_qt)
    assert len(r_get_none.json()) == 0

    # DELETE xoá thiết bị
    r_del = c.delete(f"/api/so-thiet-bi/{mac}", headers=hdr_qt)
    assert r_del.status_code == 200
    assert r_del.json()["ok"] is True

    # GET lại không còn
    r_get_sau = c.get(f"/api/so-thiet-bi?q={mac}", headers=hdr_qt)
    assert len(r_get_sau.json()) == 0


def test_bao_thu_khong_lo_loi_tho(moi_truong):
    c, _, _, mod_ka, _, khoa = moi_truong
    hdr_qt = {"Authorization": f"Bearer {khoa['qt']}"}

    c.put(
        "/api/so-thiet-bi/bao",
        headers=hdr_qt,
        json={"bat": True, "dich": [{"module": MOD_KENH_A, "kenh_id": "bot-a1"}]},
    )

    # Thử thành công
    r1 = c.post("/api/so-thiet-bi/bao/thu", headers=hdr_qt)
    assert r1.status_code == 200
    assert r1.json()["ok"] is True

    # Khi lỗi: không lộ lỗi thô
    mod_ka.loi = Exception("SECRET_INTERNAL_DB_TOKEN_ERROR_CRASH")
    r2 = c.post("/api/so-thiet-bi/bao/thu", headers=hdr_qt)
    assert r2.status_code == 200
    assert r2.json()["ok"] is False
    assert "SECRET_INTERNAL_DB_TOKEN" not in json.dumps(r2.json())


def test_khong_co_mac_tho_trong_nhat_ky(moi_truong, monkeypatch):
    c, mod_n1, _, _, _, khoa = moi_truong
    vong = so_thiet_bi.VongQuetSoThietBi(lambda: c.app)

    async def _chay():
        ghi_nhat_ky_calls = []
        goc_ghi = nhat_ky.ghi

        def mock_ghi(kenh, muc, tom_tat, **kwargs):
            ghi_nhat_ky_calls.append((kenh, muc, tom_tat, kwargs))
            return goc_ghi(kenh, muc, tom_tat, **kwargs)

        monkeypatch.setattr(nhat_ky, "ghi", mock_ghi)

        mac_tho = "00:11:22:33:44:dd"
        mod_n1.thuc_the_list = [{
            "id": f"{MOD_NGUON_1}.d1",
            "ten": "May Bi Mat",
            "thuoc_tinh": {"mac": mac_tho, "ip": "10.0.0.99"},
        }]

        await vong.chay_mot_chu_ky()

        # Rà soát mọi dòng nhật ký kênh so_thiet_bi
        stb_logs = [call for call in ghi_nhat_ky_calls if call[0] == "so_thiet_bi"]
        assert len(stb_logs) > 0
        for kenh, muc, tom_tat, kw in stb_logs:
            # Tuyệt đối không có MAC thô
            assert mac_tho not in tom_tat, f"MAC thô xuất hiện trong tóm tắt log: {tom_tat}"
            assert mac_tho not in str(kw), f"MAC thô xuất hiện trong kwargs log: {kw}"
            # Tuyệt đối không có IP
            assert "10.0.0.99" not in tom_tat
            assert "10.0.0.99" not in str(kw)
            # Tuyệt đối không có tên máy
            assert "May Bi Mat" not in tom_tat
            assert "May Bi Mat" not in str(kw)
            # MAC đã che dạng aa:bb:cc:**:**:ff
            assert "00:11:22:**:**:dd" in tom_tat or "00:11:22:**:**:dd" in str(kw)

    asyncio.run(_chay())


def test_loi_chay_khi_khong_co_module_nao(moi_truong):
    c, *_ = moi_truong
    # Xoá toàn bộ module
    c.app.state.so.dang_chay.clear()
    c.app.state.so.catalog.clear()

    vong = so_thiet_bi.VongQuetSoThietBi(lambda: c.app)
    # Vòng quét chạy êm ái, không ném exception
    asyncio.run(vong.chay_mot_chu_ky())

    hdr_qt = {"Authorization": f"Bearer {KHOA_QT}"}
    r = c.get("/api/so-thiet-bi/trang-thai", headers=hdr_qt)
    assert r.status_code == 200
    assert r.json()["tong"] == 0


def test_lan_dau_nhieu_may_khong_tu_dat_tam_dung_va_chot_xong_van_bao_duoc(moi_truong):
    """Lỗi do người soát tìm ra (Claude, 18/10): chống bão chạy TRƯỚC kiểm tra «đã chốt» nên lần quét đầu
    (sổ rỗng, mọi máy đều «mới», ≥ 6 máy) tự đặt tam_dung=true; chốt xong máy lạ vẫn im lặng tới khi ai đó nhớ gọi
    «tiep-tuc». Yêu cầu: chưa chốt → KHÔNG kích hoạt chống bão; chốt xong → máy lạ đầu tiên được báo đúng một lần."""
    c, mod_n1, _, mod_ka, _, khoa = moi_truong
    vong = so_thiet_bi.VongQuetSoThietBi(lambda: c.app)
    qt = {"Authorization": f"Bearer {khoa['qt']}"}

    def may(i):
        return {"id": f"{MOD_NGUON_1}.d{i}", "ten": f"May {i}", "loai": "iot", "thuoc_tinh": {"mac": f"00:11:22:33:44:{i:02x}"}}

    async def _chay():
        c.put("/api/so-thiet-bi/bao", headers=qt, json={"bat": True, "dich": [{"module": MOD_KENH_A, "kenh_id": "bot-a1"}]})
        mod_n1.thuc_the_list = [may(i) for i in range(1, 9)]          # 8 máy ngay lần quét đầu, CHƯA chốt
        await vong.chay_mot_chu_ky()
        tt = c.get("/api/so-thiet-bi/trang-thai", headers=qt).json()
        assert tt["tam_dung"] is False, "chưa chốt sổ thì không được tự tạm dừng vì «bão»"
        assert mod_ka.da_gui == []
        assert c.post("/api/so-thiet-bi/chot", headers=qt).status_code == 200
        assert c.get("/api/so-thiet-bi/trang-thai", headers=qt).json()["tam_dung"] is False
        mod_n1.thuc_the_list = [may(i) for i in range(1, 10)]         # máy thứ 9: lạ thật
        await vong.chay_mot_chu_ky()
        assert len(mod_ka.da_gui) == 1, "sau khi chốt, máy lạ đầu tiên phải được báo đúng một lần"
        await vong.chay_mot_chu_ky()
        assert len(mod_ka.da_gui) == 1, "không báo lặp"

    asyncio.run(_chay())
