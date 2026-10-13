"""Kiểm thử dịch vụ trình duyệt (server.py) — không chạy trình duyệt thật.

Sử dụng bộ chạy tiến trình giả để kiểm chứng:
- Khởi động: thiếu hoặc khoá ngắn hơn 24 ký tự -> từ chối khởi động.
- Xác thực: Bearer token so hằng thời gian; /suc-khoe không cần khoá; sai/thiếu khoá -> 401.
- Mở hồ sơ: đúng chuỗi lệnh (Xvfb, browser, x11vnc, websockify); loại lạ -> 400; mở lại không trùng.
- Bảo mật đường dẫn: ID hợp lệ hex 16-64 ký tự; ID sai mẫu / path traversal / symlink thoát -> 400/403.
- Đóng hồ sơ: dừng tiến trình; giu_phien=false xoá đúng thư mục hồ sơ và không xoá ngoài.
- Giới hạn: trần số hồ sơ mở đồng thời -> 429; cấp phát và giải phóng cổng.
- Dọn idle: bảo vệ khi còn kết nối; tự đóng khi idle và không có kết nối.
- An toàn: không lọt khoá vào log; tiến trình con nhận mảng đối số (không shell=True) và môi trường tối thiểu.
- websockify đòi bí mật riêng từng hồ sơ (HMAC), bí mật đọc từ tệp 0600 chứ không qua dòng lệnh; x11vnc không chia sẻ.
- Xác thực chạy TRƯỚC phân tích thân; /docs, /redoc, /openapi.json tắt.
- Hồ sơ «ma»: tiến trình chết thì dựng lại / trả 502 và dọn sạch; dừng giết cả nhóm; cổng cấp vòng.
- Trần hồ sơ trên đĩa, hạn tuyệt đối; khoá yếu bị từ chối; access log tắt; khoá bị xoá khỏi /proc/<pid>/environ.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import http.client
import importlib
import json
import logging
import re
import os
import signal
import socket
import subprocess
import sys
import textwrap
import time
import types
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

# Đưa thư mục dịch vụ vào sys.path
GOC_DICH_VU = Path(__file__).resolve().parent
if str(GOC_DICH_VU) not in sys.path:
    sys.path.insert(0, str(GOC_DICH_VU))

import server
from server import (
    BoChayTienTrinh,
    BoKiemTraKetNoi,
    QuanLyHoSo,
    TienTrinhHandle,
    app,
    kiem_tra_khoa,
)

# Chuỗi khoá giả nằm trong allowlist của .gitleaks.toml (40 ký tự)
KHOA_TEST = "sk-lan-0000000000000000000000000000xyzw"
#: Khoá thứ hai suy từ khoá test (đảo ngược) — không thêm chuỗi giống bí mật mới vào kho.
KHOA_KHAC = KHOA_TEST[::-1]
HEADER_AUTH = {"Authorization": f"Bearer {KHOA_TEST}"}


def hmac_cu(ho_so_id: str, khoa: str = KHOA_TEST) -> str:
    """Bí mật kiểu CŨ (hằng số theo khoá, id) — bí mật mới phải KHÔNG bằng giá trị này."""
    return hmac.new(khoa.encode("utf-8"), ho_so_id.encode("utf-8"), hashlib.sha256).hexdigest()


def bi_mat_cua(dich: str) -> str:
    """Bí mật hồ sơ nằm ở phần mật khẩu của userinfo trong `dich`."""
    from urllib.parse import urlsplit

    u = urlsplit(dich)
    assert u.username == "w2a" and u.password
    return u.password


class TienTrinhGia(TienTrinhHandle):
    def __init__(self, lenh: list[str], env: dict[str, str], cwd: str | None = None, song: bool = True):
        self.lenh = lenh
        self.env = env
        self.cwd = cwd
        self.da_dung = False
        self.song = song

    def chet(self) -> None:
        """Giả lập tiến trình tự chết (crash) mà không ai gọi dung()."""
        self.song = False

    def dang_chay(self) -> bool:
        return self.song and not self.da_dung

    async def dung(self) -> None:
        self.da_dung = True


class BoChayGia(BoChayTienTrinh):
    def __init__(self, chet_ngay=None):
        self.lich_su_lenh: list[list[str]] = []
        self.lich_su_env: list[dict[str, str]] = []
        self.tien_trinh_tao: list[TienTrinhGia] = []
        self.bi_mat_ghi: list[str] = []
        #: Hàm (lenh) -> bool: True thì tiến trình «chết ngay sau khi khởi động».
        self.chet_ngay = chet_ngay

    async def chay(
        self,
        lenh: list[str],
        env: dict[str, str],
        cwd: str | None = None,
    ) -> TienTrinhHandle:
        self.lich_su_lenh.append(list(lenh))
        self.lich_su_env.append(dict(env))
        if "--auth-source" in lenh:
            # Chụp nội dung tệp bí mật lúc dựng (đường lỗi xoá tệp ngay sau đó)
            self.bi_mat_ghi.append(Path(lenh[lenh.index("--auth-source") + 1]).read_text(encoding="utf-8"))
        proc = TienTrinhGia(lenh, env, cwd, song=not (self.chet_ngay and self.chet_ngay(lenh)))
        self.tien_trinh_tao.append(proc)
        return proc


class BoKiemTraKetNoiGia(BoKiemTraKetNoi):
    def __init__(self):
        self.ket_noi: dict[int, int] = {}

    def dat_ket_noi(self, cong: int, so_luong: int) -> None:
        self.ket_noi[cong] = so_luong

    def dem_ket_noi(self, ho_so) -> int:
        # Khoá theo cổng của hồ sơ (bộ thật đếm kết nối ĐÃ XÁC THỰC của hồ sơ)
        return self.ket_noi.get(getattr(ho_so, "cong", ho_so), 0)


@pytest.fixture
def moi_truong_test(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    thu_muc_hoso = tmp_path / "hoso"
    thu_muc_hoso.mkdir()

    monkeypatch.setenv("TRINH_DUYET_KHOA", KHOA_TEST)
    monkeypatch.setenv("TRINH_DUYET_THU_MUC_GOC", str(thu_muc_hoso))
    # Thư mục tạm riêng của test: lifespan dọn `w2a-cookie-*` cũ ở thư mục tạm — không quét /tmp thật của máy.
    (tmp_path / "tam").mkdir()
    monkeypatch.setattr(__import__("tempfile"), "tempdir", str(tmp_path / "tam"))
    monkeypatch.setenv("TRINH_DUYET_CONG_TU", "6100")
    monkeypatch.setenv("TRINH_DUYET_CONG_DEN", "6105")
    monkeypatch.setenv("TRINH_DUYET_TOI_DA", "3")
    monkeypatch.setenv("TRINH_DUYET_IDLE_PHUT", "120")
    monkeypatch.setenv("TRINH_DUYET_HOST_NOI_BO", "trinh-duyet")
    monkeypatch.setenv("TRINH_DUYET_TOI_DA_HO_SO", "20")
    monkeypatch.setenv("TRINH_DUYET_TOI_DA_PHUT", "480")
    # Khoá đã nạp (như sau lifespan) để các test gọi thẳng QuanLyHoSo không cần TestClient.
    monkeypatch.setattr(server, "_khoa_dich_vu", KHOA_TEST, raising=False)

    bo_chay = BoChayGia()
    bo_kiem_tra = BoKiemTraKetNoiGia()
    cau_hinh = server.lay_cau_hinh()
    # Không chờ thật sau khi dựng tiến trình (bộ chạy giả đã biết ngay sống/chết).
    cau_hinh["cho_khoi_dong_giay"] = 0

    quan_ly = QuanLyHoSo(
        cau_hinh=cau_hinh,
        bo_chay=bo_chay,
        bo_kiem_tra=bo_kiem_tra,
    )
    monkeypatch.setattr(server, "quan_ly_toan_cuc", quan_ly)

    return {
        "thu_muc_hoso": thu_muc_hoso,
        "bo_chay": bo_chay,
        "bo_kiem_tra": bo_kiem_tra,
        "quan_ly": quan_ly,
    }


# ── Kiểm thử Khởi động & Khoá ─────────────────────────────────────────────────


def test_khoi_dong_thieu_khoa(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("TRINH_DUYET_KHOA", raising=False)
    with pytest.raises(RuntimeError, match="TRINH_DUYET_KHOA bắt buộc"):
        kiem_tra_khoa()


def test_khoi_dong_khoa_ngan(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TRINH_DUYET_KHOA", "khoa-qua-ngan-duoi-24")
    with pytest.raises(RuntimeError, match="tối thiểu 24 ký tự"):
        kiem_tra_khoa()


# ── Kiểm thử Xác thực & Sức khoẻ ──────────────────────────────────────────────


def test_suc_khoe(moi_truong_test):
    with TestClient(app) as client:
        res = client.get("/suc-khoe")
        assert res.status_code == 200
        assert res.json() == {"ok": True}


def test_xac_thuc_khong_khoa_hoac_khoa_sai(moi_truong_test):
    with TestClient(app) as client:
        # Không có Authorization header -> 401
        res = client.get("/ho-so")
        assert res.status_code == 401

        # Authorization header sai khoá -> 401
        res_sai = client.get(
            "/ho-so",
            headers={"Authorization": "Bearer sai-khoa-hoan-toan-00000000000000"},
        )
        assert res_sai.status_code == 401

        # Authorization header không phải Bearer -> 401
        res_basic = client.get(
            "/ho-so",
            headers={"Authorization": f"Basic {KHOA_TEST}"},
        )
        assert res_basic.status_code == 401


# ── Kiểm thử Mở Hồ sơ & Lệnh gọi ──────────────────────────────────────────────


def test_mo_ho_so_chromium_dung_chuoi_lenh(moi_truong_test):
    bo_chay: BoChayGia = moi_truong_test["bo_chay"]
    ho_so_id = "0123456789abcdef0123456789abcdef"

    with TestClient(app) as client:
        res = client.post(
            "/ho-so/mo",
            headers=HEADER_AUTH,
            json={"ho_so": ho_so_id, "loai": "chromium"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["ho_so"] == ho_so_id
        assert data["loai"] == "chromium"
        # `dich` mang userinfo w2a:<HMAC> để lõi tự gửi Basic auth tới websockify.
        assert re.fullmatch(r"http://w2a:[0-9a-f]{64}@trinh-duyet:6100", data["dich"])
        bi_mat = bi_mat_cua(data["dich"])
        # Tệp bí mật cho plugin websockify (kiểm khi hồ sơ còn mở; đóng thì tệp bị xoá)
        lenh_ws_luc_mo = bo_chay.lich_su_lenh[3]
        tep_bi_mat = Path(lenh_ws_luc_mo[lenh_ws_luc_mo.index("--auth-source") + 1])
        assert tep_bi_mat.read_text(encoding="utf-8") == f"w2a:{bi_mat}"
        assert (tep_bi_mat.stat().st_mode & 0o777) == 0o600

    # Kiểm tra 4 tiến trình đã được gọi: Xvfb, chromium, x11vnc, websockify
    assert len(bo_chay.lich_su_lenh) == 4

    lenh_xvfb = bo_chay.lich_su_lenh[0]
    assert lenh_xvfb[0] == "Xvfb"
    assert lenh_xvfb[1] == ":100"
    assert "-nolisten" in lenh_xvfb and "tcp" in lenh_xvfb

    lenh_chrome = bo_chay.lich_su_lenh[1]
    assert lenh_chrome[0] == "chromium"
    assert any(arg.startswith("--user-data-dir=") for arg in lenh_chrome)
    assert "--no-sandbox" in lenh_chrome

    lenh_vnc = bo_chay.lich_su_lenh[2]
    assert lenh_vnc[0] == "x11vnc"
    assert lenh_vnc[lenh_vnc.index("-display") + 1] == ":100"
    assert lenh_vnc[lenh_vnc.index("-listen") + 1] == "127.0.0.1"
    assert lenh_vnc[lenh_vnc.index("-rfbport") + 1] == "6000"
    assert "-nopw" in lenh_vnc
    # Không cho khách thứ hai nối song song: không chia sẻ, từ chối người đến sau thay vì đá người đang dùng.
    assert "-shared" not in lenh_vnc
    assert "-nevershared" in lenh_vnc and "-dontdisconnect" in lenh_vnc
    assert "-forever" in lenh_vnc

    lenh_ws = bo_chay.lich_su_lenh[3]
    assert lenh_ws[0] == "websockify"
    assert lenh_ws[lenh_ws.index("--web") + 1] == "/usr/share/novnc"
    # Xác thực bắt buộc cho cả WebSocket lẫn trang web tĩnh noVNC
    assert "--web-auth" in lenh_ws
    assert lenh_ws[lenh_ws.index("--auth-plugin") + 1] == "xac_thuc_ws.BasicTuTep"
    assert lenh_ws[lenh_ws.index("--auth-source") + 1] == str(tep_bi_mat)
    # Hai đối số vị trí cuối: cổng nghe và đích VNC cục bộ
    assert lenh_ws[-2:] == ["6100", "127.0.0.1:6000"]
    # websockify tìm được plugin qua PYTHONPATH của riêng nó
    env_ws = bo_chay.lich_su_env[3]
    assert (Path(env_ws["PYTHONPATH"]) / "xac_thuc_ws.py").is_file()
    # Bí mật KHÔNG nằm trên dòng lệnh của bất kỳ tiến trình nào (lộ qua /proc/<pid>/cmdline, `ps`, `docker top`)
    for lenh in bo_chay.lich_su_lenh:
        assert all(bi_mat not in arg for arg in lenh)


def test_mo_ho_so_firefox_dung_chuoi_lenh(moi_truong_test):
    bo_chay: BoChayGia = moi_truong_test["bo_chay"]
    ho_so_id = "fedcba9876543210fedcba9876543210"

    with TestClient(app) as client:
        res = client.post(
            "/ho-so/mo",
            headers=HEADER_AUTH,
            json={"ho_so": ho_so_id, "loai": "firefox"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["loai"] == "firefox"

    assert len(bo_chay.lich_su_lenh) == 4
    lenh_ff = bo_chay.lich_su_lenh[1]
    assert lenh_ff[0] == "firefox-esr"
    assert "--profile" in lenh_ff
    assert "--no-remote" in lenh_ff


def test_mo_lai_ho_so_dang_mo_tra_dich_cu_khong_trung(moi_truong_test):
    bo_chay: BoChayGia = moi_truong_test["bo_chay"]
    ho_so_id = "1111222233334444aaaabbbbccccdddd"

    with TestClient(app) as client:
        # Lần 1
        res1 = client.post(
            "/ho-so/mo",
            headers=HEADER_AUTH,
            json={"ho_so": ho_so_id, "loai": "chromium"},
        )
        assert res1.status_code == 200
        dich1 = res1.json()["dich"]

        # Lần 2 (mở lại cùng hồ sơ)
        res2 = client.post(
            "/ho-so/mo",
            headers=HEADER_AUTH,
            json={"ho_so": ho_so_id, "loai": "chromium"},
        )
        assert res2.status_code == 200
        dich2 = res2.json()["dich"]
        assert dich1 == dich2

    # Không chạy thêm tiến trình nào ở lần gọi thứ 2
    assert len(bo_chay.lich_su_lenh) == 4


def test_mo_ho_so_loai_la_tra_400(moi_truong_test):
    ho_so_id = "1234567890abcdef1234567890abcdef"
    with TestClient(app) as client:
        res = client.post(
            "/ho-so/mo",
            headers=HEADER_AUTH,
            json={"ho_so": ho_so_id, "loai": "safari"},
        )
        assert res.status_code == 400


# ── Kiểm thử Định danh & Chống thoát Thư mục ──────────────────────────────────


@pytest.mark.parametrize(
    "id_sai",
    [
        "ngan",  # Quá ngắn (< 16 ký tự)
        "quadaivatoi" * 10,  # Quá dài (> 64 ký tự)
        "1234567890ABCDEF1234567890ABCDEF",  # Chữ hoa không hợp lệ
        "user@example.com-12345678",  # Email suy đoán
        "../../etc/passwd0000000000",  # Path traversal
        "1234567890abcdef-12345678",  # Chứa ký tự gạch nối
    ],
)
def test_id_ho_so_sai_mau_tra_400(moi_truong_test, id_sai):
    with TestClient(app) as client:
        res = client.post(
            "/ho-so/mo",
            headers=HEADER_AUTH,
            json={"ho_so": id_sai, "loai": "chromium"},
        )
        assert res.status_code == 400


def test_chong_thoat_thu_muc_symlink(moi_truong_test, tmp_path: Path):
    thu_muc_ngoai = tmp_path / "ngoai"
    thu_muc_ngoai.mkdir()
    thu_muc_hoso: Path = moi_truong_test["thu_muc_hoso"]

    # Tạo một symlink hợp lệ theo định dạng ID nhưng trỏ ra ngoài
    id_symlink = "0123456789abcdef0123456789abcdef"
    link_path = thu_muc_hoso / id_symlink
    link_path.symlink_to(thu_muc_ngoai)

    with TestClient(app) as client:
        res = client.post(
            "/ho-so/mo",
            headers=HEADER_AUTH,
            json={"ho_so": id_symlink, "loai": "chromium"},
        )
        # Bị chặn 403 Forbidden do thoát khỏi thư mục gốc
        assert res.status_code == 403


# ── Kiểm thử Đóng Hồ sơ & Dọn dẹp Thư mục ──────────────────────────────────────


def test_dong_ho_so_giu_phien(moi_truong_test):
    bo_chay: BoChayGia = moi_truong_test["bo_chay"]
    thu_muc_hoso: Path = moi_truong_test["thu_muc_hoso"]
    ho_so_id = "0123456789abcdef0123456789abcdef"

    with TestClient(app) as client:
        client.post(
            "/ho-so/mo",
            headers=HEADER_AUTH,
            json={"ho_so": ho_so_id, "loai": "chromium"},
        )
        assert (thu_muc_hoso / ho_so_id).exists()

        res = client.post(
            f"/ho-so/{ho_so_id}/dong",
            headers=HEADER_AUTH,
            json={"giu_phien": True},
        )
        assert res.status_code == 200
        assert res.json() == {"ok": True, "session_preserved": True}
        # Không gửi thân → mặc định GIỮ phiên (như «Đóng · giữ phiên» của c2a)
        client.post("/ho-so/mo", headers=HEADER_AUTH, json={"ho_so": ho_so_id, "loai": "chromium"})
        assert client.post(f"/ho-so/{ho_so_id}/dong", headers=HEADER_AUTH).json() == {
            "ok": True, "session_preserved": True}

    # Các tiến trình đã được gọi dung()
    assert all(p.da_dung for p in bo_chay.tien_trinh_tao)
    # Thư mục hồ sơ vẫn còn
    assert (thu_muc_hoso / ho_so_id).exists()


def test_dong_ho_so_khong_giu_phien_xoa_dung_thu_muc(moi_truong_test, tmp_path: Path):
    thu_muc_hoso: Path = moi_truong_test["thu_muc_hoso"]
    ho_so_id = "0123456789abcdef0123456789abcdef"

    # Tạo tệp bên ngoài để chứng minh không bị xoá nhầm
    tep_ngoai = tmp_path / "tep_ngoai.txt"
    tep_ngoai.write_text("an toan", encoding="utf-8")

    with TestClient(app) as client:
        client.post(
            "/ho-so/mo",
            headers=HEADER_AUTH,
            json={"ho_so": ho_so_id, "loai": "chromium"},
        )
        assert (thu_muc_hoso / ho_so_id).exists()

        res = client.post(
            f"/ho-so/{ho_so_id}/dong",
            headers=HEADER_AUTH,
            json={"giu_phien": False},
        )
        assert res.status_code == 200

    # Thư mục hồ sơ đã bị xoá
    assert not (thu_muc_hoso / ho_so_id).exists()
    # Tệp bên ngoài không bị ảnh hưởng
    assert tep_ngoai.exists()


# ── Kiểm thử Trần số Hồ sơ & Cấp / Giải phóng Cổng ────────────────────────────


def test_tran_so_ho_so_va_giai_phong_cong(moi_truong_test):
    # Cấu hình tối đa 3 hồ sơ
    ids = [
        "11111111111111111111111111111111",
        "22222222222222222222222222222222",
        "33333333333333333333333333333333",
        "44444444444444444444444444444444",
    ]

    with TestClient(app) as client:
        # Mở 3 hồ sơ đầu tiên
        r1 = client.post("/ho-so/mo", headers=HEADER_AUTH, json={"ho_so": ids[0], "loai": "chromium"})
        assert r1.status_code == 200
        assert r1.json()["dich"].endswith(":6100")

        r2 = client.post("/ho-so/mo", headers=HEADER_AUTH, json={"ho_so": ids[1], "loai": "chromium"})
        assert r2.status_code == 200
        assert r2.json()["dich"].endswith(":6101")

        r3 = client.post("/ho-so/mo", headers=HEADER_AUTH, json={"ho_so": ids[2], "loai": "chromium"})
        assert r3.status_code == 200
        assert r3.json()["dich"].endswith(":6102")

        # Hồ sơ thứ 4 vượt trần (3) -> 429
        r4 = client.post("/ho-so/mo", headers=HEADER_AUTH, json={"ho_so": ids[3], "loai": "chromium"})
        assert r4.status_code == 429

        # Đóng hồ sơ 1
        rd = client.post(f"/ho-so/{ids[0]}/dong", headers=HEADER_AUTH, json={"giu_phien": True})
        assert rd.status_code == 200

        # Mở lại hồ sơ 4 -> thành công; cổng cấp VÒNG nên nhận 6103, KHÔNG tái dùng ngay 6100 vừa giải phóng
        r4_lai = client.post("/ho-so/mo", headers=HEADER_AUTH, json={"ho_so": ids[3], "loai": "chromium"})
        assert r4_lai.status_code == 200
        assert r4_lai.json()["dich"].endswith(":6103")


# ── Kiểm thử Danh sách Hồ sơ (GET /ho-so) ──────────────────────────────────────


def test_danh_sach_ho_so(moi_truong_test):
    ho_so_1 = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    ho_so_2 = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

    with TestClient(app) as client:
        client.post("/ho-so/mo", headers=HEADER_AUTH, json={"ho_so": ho_so_1, "loai": "chromium"})
        client.post("/ho-so/mo", headers=HEADER_AUTH, json={"ho_so": ho_so_2, "loai": "firefox"})
        # Đóng hồ sơ 1 nhưng giữ phiên
        client.post(f"/ho-so/{ho_so_1}/dong", headers=HEADER_AUTH, json={"giu_phien": True})

        res = client.get("/ho-so", headers=HEADER_AUTH)
        assert res.status_code == 200
        ds = res.json()
        assert len(ds) == 2

        muc_1 = next(m for m in ds if m["ho_so"] == ho_so_1)
        assert muc_1["trang_thai"] == "dong"
        assert muc_1["loai"] == "chromium"
        assert muc_1["luc_mo"] is None

        muc_2 = next(m for m in ds if m["ho_so"] == ho_so_2)
        assert muc_2["trang_thai"] == "dang_mo"
        assert muc_2["loai"] == "firefox"
        assert muc_2["luc_mo"] is not None


# ── Kiểm thử Dọn dẹp Idle & Bảo vệ Kết nối ───────────────────────────────────


def test_don_idle_va_bao_ve_khi_co_ket_noi(moi_truong_test):
    quan_ly: QuanLyHoSo = moi_truong_test["quan_ly"]
    bo_kiem_tra: BoKiemTraKetNoiGia = moi_truong_test["bo_kiem_tra"]

    ho_so_ban = "11111111111111111111111111111111"  # Có người kết nối
    ho_so_idle = "22222222222222222222222222222222"  # Không có ai kết nối

    async def _chay_test():
        await quan_ly.mo(ho_so_ban, "chromium")
        await quan_ly.mo(ho_so_idle, "chromium")

        cong_ban = quan_ly.danh_sach_mo[ho_so_ban].cong
        cong_idle = quan_ly.danh_sach_mo[ho_so_idle].cong

        bo_kiem_tra.dat_ket_noi(cong_ban, 1)  # Có 1 kết nối WebSocket noVNC
        bo_kiem_tra.dat_ket_noi(cong_idle, 0)  # 0 kết nối

        # Giả lập thời gian trôi qua 130 phút (lớn hơn ngưỡng 120 phút)
        quan_ly.danh_sach_mo[ho_so_ban].lan_hoat_dong_cuoi -= 130 * 60
        quan_ly.danh_sach_mo[ho_so_idle].lan_hoat_dong_cuoi -= 130 * 60

        can_dong = await quan_ly.don_idle()

        # Chỉ hồ sơ idle bị đóng, hồ sơ bận được bảo vệ
        assert ho_so_idle in can_dong
        assert ho_so_ban not in can_dong
        assert ho_so_idle not in quan_ly.danh_sach_mo
        assert ho_so_ban in quan_ly.danh_sach_mo

    asyncio.run(_chay_test())


# ── Kiểm thử Bảo mật: Không lộ Khoá trong Log & Môi trường Con ─────────────────


def test_khong_lo_khoa_trong_log(moi_truong_test, caplog: pytest.LogCaptureFixture):
    caplog.set_level(logging.DEBUG)

    with TestClient(app) as client:
        # Gửi request hợp lệ
        client.post(
            "/ho-so/mo",
            headers=HEADER_AUTH,
            json={"ho_so": "0123456789abcdef0123456789abcdef", "loai": "chromium"},
        )
        # Gửi request với khoá sai
        client.get("/ho-so", headers={"Authorization": "Bearer khoa-sai-khong-duoc-in"})

    # Kiểm tra toàn bộ nội dung log không bao giờ chứa chuỗi khoá
    for record in caplog.records:
        assert KHOA_TEST not in record.message
        assert "khoa-sai-khong-duoc-in" not in record.message


def test_tien_trinh_con_khong_ke_thua_khoa(moi_truong_test):
    bo_chay: BoChayGia = moi_truong_test["bo_chay"]
    ho_so_id = "0123456789abcdef0123456789abcdef"

    with TestClient(app) as client:
        client.post(
            "/ho-so/mo",
            headers=HEADER_AUTH,
            json={"ho_so": ho_so_id, "loai": "chromium"},
        )

    assert len(bo_chay.lich_su_env) == 4
    for env in bo_chay.lich_su_env:
        assert "TRINH_DUYET_KHOA" not in env
        # Đảm bảo không có bất kỳ biến môi trường nào chứa KHOA hay TOKEN
        for k, v in env.items():
            assert "KHOA" not in k.upper()
            assert "TOKEN" not in k.upper()
            assert "SECRET" not in k.upper()
            assert KHOA_TEST not in v


def test_quyen_thu_muc_ho_so_0700(moi_truong_test):
    thu_muc_hoso: Path = moi_truong_test["thu_muc_hoso"]
    ho_so_id = "0123456789abcdef0123456789abcdef"

    with TestClient(app) as client:
        client.post(
            "/ho-so/mo",
            headers=HEADER_AUTH,
            json={"ho_so": ho_so_id, "loai": "chromium"},
        )

    dd = thu_muc_hoso / ho_so_id
    assert dd.exists()
    assert (dd.stat().st_mode & 0o777) == 0o700


def test_danh_sach_ho_so_sau_khoi_dong_lai(moi_truong_test):
    thu_muc_hoso: Path = moi_truong_test["thu_muc_hoso"]
    ho_so_id = "0123456789abcdef0123456789abcdef"

    # Tạo trước thư mục hồ sơ và metadata trên đĩa như phiên trước để lại
    dd = thu_muc_hoso / ho_so_id
    dd.mkdir(parents=True, mode=0o700)
    (dd / ".thong_tin.json").write_text(json.dumps({"loai": "firefox"}), encoding="utf-8")

    # Tạo phiên bản QuanLyHoSo mới mô phỏng dịch vụ vừa khởi động lại
    cau_hinh = server.lay_cau_hinh()
    ql_moi = QuanLyHoSo(
        cau_hinh=cau_hinh,
        bo_chay=moi_truong_test["bo_chay"],
        bo_kiem_tra=moi_truong_test["bo_kiem_tra"],
    )

    async def _kiem_tra():
        # Danh sách mở ban đầu phải rỗng
        assert len(ql_moi.danh_sach_mo) == 0
        ds = await ql_moi.danh_sach()
        assert len(ds) == 1
        assert ds[0]["ho_so"] == ho_so_id
        assert ds[0]["trang_thai"] == "dong"
        assert ds[0]["loai"] == "firefox"
        assert ds[0]["luc_mo"] is None

    asyncio.run(_kiem_tra())


# ══ Các lỗi bảo mật do reviewer độc lập nêu (#1-#7) ═══════════════════════════


ID_A = "aaaa0000aaaa0000aaaa0000aaaa0000"
ID_B = "bbbb1111bbbb1111bbbb1111bbbb1111"


# ── #1 websockify có xác thực theo bí mật riêng từng hồ sơ ────────────────────


def test_bi_mat_ngau_nhien_moi_lan_mo(moi_truong_test):
    """Bí mật 256 bit NGẪU NHIÊN mỗi lần dựng: khác giữa hai hồ sơ, khác giữa hai lần mở, không là HMAC(khoá,id);
    mở lại hồ sơ đang mở trả đúng bí mật hiện tại; tệp bí mật luôn khớp bí mật hiện tại."""
    bo_chay: BoChayGia = moi_truong_test["bo_chay"]
    with TestClient(app) as client:
        mo = lambda hid: client.post("/ho-so/mo", headers=HEADER_AUTH, json={"ho_so": hid, "loai": "chromium"})
        a1, b1 = bi_mat_cua(mo(ID_A).json()["dich"]), bi_mat_cua(mo(ID_B).json()["dich"])
        assert re.fullmatch(r"[0-9a-f]{64}", a1) and a1 != b1
        assert a1 != hmac_cu(ID_A) and b1 != hmac_cu(ID_B)
        assert bi_mat_cua(mo(ID_A).json()["dich"]) == a1  # đang mở: giữ bí mật hiện tại
        client.post(f"/ho-so/{ID_A}/dong", headers=HEADER_AUTH, json={"giu_phien": True})
        a2 = bi_mat_cua(mo(ID_A).json()["dich"])
        assert a2 != a1  # mở lại sinh bí mật MỚI: `dich` cũ lỡ lộ không dùng lại được
        assert bo_chay.bi_mat_ghi == [f"w2a:{a1}", f"w2a:{b1}", f"w2a:{a2}"]


def test_khong_lo_bi_mat_qua_danh_sach_log_va_loi(moi_truong_test, caplog: pytest.LogCaptureFixture):
    """Bí mật hồ sơ chỉ xuất hiện trong phản hồi `mo`: không trong GET /ho-so, log hay thông báo lỗi."""
    caplog.set_level(logging.DEBUG)
    bo_chay: BoChayGia = moi_truong_test["bo_chay"]
    with TestClient(app) as client:
        ra = client.post("/ho-so/mo", headers=HEADER_AUTH, json={"ho_so": ID_A, "loai": "chromium"})
        assert ra.status_code == 200
        bi_mat_a = bi_mat_cua(ra.json()["dich"])
        # Mở lại (đường «đã mở») cũng không được log bí mật
        assert client.post("/ho-so/mo", headers=HEADER_AUTH, json={"ho_so": ID_A, "loai": "chromium"}).status_code == 200
        ds = client.get("/ho-so", headers=HEADER_AUTH)
        assert ds.status_code == 200
        for muc in ds.json():
            assert "dich" not in muc
        assert bi_mat_a not in ds.text
        # Đường lỗi: tiến trình chết ngay -> 502, thân lỗi không chứa bí mật/khoá
        bo_chay.chet_ngay = lambda lenh: lenh[0] == "chromium"
        loi = client.post("/ho-so/mo", headers=HEADER_AUTH, json={"ho_so": ID_B, "loai": "chromium"})
        assert loi.status_code == 502
        bi_mat_b = bo_chay.bi_mat_ghi[-1].split(":", 1)[1]
        assert bi_mat_b not in loi.text and KHOA_TEST not in loi.text
        client.post(f"/ho-so/{ID_A}/dong", headers=HEADER_AUTH, json={"giu_phien": True})

    for rec in caplog.records:
        msg = rec.getMessage()
        for bi_mat in (bi_mat_a, bi_mat_b, KHOA_TEST):
            assert bi_mat not in msg


def test_tep_bi_mat_bi_xoa_khi_dong(moi_truong_test):
    quan_ly: QuanLyHoSo = moi_truong_test["quan_ly"]
    bo_chay: BoChayGia = moi_truong_test["bo_chay"]

    async def _chay():
        await quan_ly.mo(ID_A, "chromium")
        lenh_ws = bo_chay.lich_su_lenh[3]
        tep = Path(lenh_ws[lenh_ws.index("--auth-source") + 1])
        assert tep.exists()
        # Tệp bí mật không nằm trong thư mục hồ sơ (HOME của trình duyệt) và không bị đếm là hồ sơ
        assert ID_A not in tep.parent.name
        await quan_ly.dong(ID_A, giu_phien=True)
        assert not tep.exists()
        assert [m["ho_so"] for m in await quan_ly.danh_sach()] == [ID_A]

    asyncio.run(_chay())


def test_moi_truong_con_khong_lot_bien_bi_mat_ten_bat_ky(moi_truong_test, monkeypatch: pytest.MonkeyPatch):
    """Biến mang bí mật có TÊN BẤT KỲ (kể cả tên trông như biến locale) không lọt sang tiến trình con."""
    gia_tri = "gia-tri-bi-mat-khong-duoc-lot-0000"
    monkeypatch.setenv("LC_MAT_KHAU", gia_tri)
    monkeypatch.setenv("CAU_HINH_BAT_KY", gia_tri)
    monkeypatch.setenv("DB_PASSWORD_URL", gia_tri)
    bo_chay: BoChayGia = moi_truong_test["bo_chay"]
    with TestClient(app) as client:
        bi_mat_a = bi_mat_cua(client.post("/ho-so/mo", headers=HEADER_AUTH, json={"ho_so": ID_A, "loai": "chromium"}).json()["dich"])
    assert len(bo_chay.lich_su_env) == 4
    for env in bo_chay.lich_su_env:
        for v in env.values():
            assert gia_tri not in v
            assert KHOA_TEST not in v
            assert bi_mat_a not in v


# ── #1 plugin websockify: đọc bí mật từ tệp, so hằng thời gian ────────────────


@pytest.fixture
def plugin_ws(monkeypatch: pytest.MonkeyPatch):
    """Nạp xac_thuc_ws với `websockify.auth_plugins` giả (venv không cài websockify)."""

    class AuthenticationError(Exception):
        def __init__(self, log_msg=None, response_code=403, response_headers={}, response_msg=None):
            self.code = response_code
            self.headers = response_headers
            super().__init__(f"{response_code} {log_msg or response_msg}")

    goi = types.ModuleType("websockify")
    con = types.ModuleType("websockify.auth_plugins")
    con.AuthenticationError = AuthenticationError
    goi.auth_plugins = con
    monkeypatch.setitem(sys.modules, "websockify", goi)
    monkeypatch.setitem(sys.modules, "websockify.auth_plugins", con)
    monkeypatch.delitem(sys.modules, "xac_thuc_ws", raising=False)
    mod = importlib.import_module("xac_thuc_ws")
    yield mod, AuthenticationError
    sys.modules.pop("xac_thuc_ws", None)


def _basic(u: str, p: str) -> str:
    return "Basic " + base64.b64encode(f"{u}:{p}".encode()).decode()


def test_plugin_ws_kiem_bi_mat_tu_tep(plugin_ws, tmp_path: Path):
    mod, Loi = plugin_ws
    tep = tmp_path / "bm"
    tep.write_text("w2a:" + "ab12" * 16, encoding="utf-8")
    p = mod.BasicTuTep(str(tep))
    p.authenticate({"Authorization": _basic("w2a", "ab12" * 16)}, "127.0.0.1", 6000)  # đúng: không ném
    for tieu_de in (
        None,
        _basic("w2a", "sai"),
        _basic("khac", "ab12" * 16),
        "Bearer " + "ab12" * 16,
        "Basic !!!khong-phai-base64",
    ):
        with pytest.raises(Loi) as e:
            p.authenticate({"Authorization": tieu_de} if tieu_de else {}, "127.0.0.1", 6000)
        assert e.value.code == 401
        assert "ab12" * 16 not in str(e.value)


def test_plugin_ws_tu_choi_khoi_dong_khi_tep_hong(plugin_ws, tmp_path: Path):
    mod, _ = plugin_ws
    with pytest.raises(Exception):
        mod.BasicTuTep(str(tmp_path / "khong-co"))
    rong = tmp_path / "rong"
    rong.write_text("", encoding="utf-8")
    with pytest.raises(ValueError):
        mod.BasicTuTep(str(rong))
    with pytest.raises(ValueError):
        mod.BasicTuTep(None)


def _cong_ranh() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.skipif(not Path("/usr/bin/websockify").exists(), reason="máy không cài websockify")
def test_websockify_that_doi_xac_thuc_voi_dung_co_cua_server(moi_truong_test, tmp_path: Path):
    """Chạy websockify THẬT (chỉ loopback) bằng đúng cờ/biến môi trường server dựng: thiếu/sai bí mật -> 401."""
    quan_ly: QuanLyHoSo = moi_truong_test["quan_ly"]
    bo_chay: BoChayGia = moi_truong_test["bo_chay"]
    async def _mo_hai():
        return (await quan_ly.mo(ID_A, "chromium"))["dich"], (await quan_ly.mo(ID_B, "chromium"))["dich"]

    dich_a, dich_b = asyncio.run(_mo_hai())
    lenh, env = list(bo_chay.lich_su_lenh[3]), dict(bo_chay.lich_su_env[3])
    web = tmp_path / "web"
    web.mkdir()
    (web / "vnc.html").write_text("noVNC-gia", encoding="utf-8")
    cong = _cong_ranh()
    lenh[0] = "/usr/bin/websockify"
    lenh[lenh.index("--web") + 1] = str(web)
    lenh[-2:] = [f"127.0.0.1:{cong}", f"127.0.0.1:{_cong_ranh()}"]
    tep_log = tmp_path / "ws.log"
    with open(tep_log, "wb") as f:
        p = subprocess.Popen(lenh, env=env, stdin=subprocess.DEVNULL, stdout=f, stderr=subprocess.STDOUT)
    try:
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", cong), timeout=0.2).close()
                break
            except OSError:
                assert p.poll() is None, tep_log.read_text(errors="replace")
                time.sleep(0.05)

        def get(auth: str | None) -> tuple[int, bytes]:
            c = http.client.HTTPConnection("127.0.0.1", cong, timeout=5)
            c.request("GET", "/vnc.html", headers={"Authorization": auth} if auth else {})
            r = c.getresponse()
            kq = (r.status, r.read())
            c.close()
            return kq

        bi_mat = bi_mat_cua(dich_a)
        assert get(None)[0] == 401
        assert get(_basic("w2a", bi_mat_cua(dich_b)))[0] == 401  # bí mật của hồ sơ khác (IDOR)
        assert get(_basic("w2a", bi_mat)) == (200, b"noVNC-gia")

        # Bắt tay WebSocket không có bí mật cũng bị chặn
        s = socket.create_connection(("127.0.0.1", cong), timeout=5)
        s.sendall(
            b"GET /websockify HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\n\r\n"
        )
        dong_dau = s.recv(64)
        s.close()
        assert dong_dau.startswith(b"HTTP/1.1 401") or dong_dau.startswith(b"HTTP/1.0 401"), dong_dau
    finally:
        p.terminate()
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()
    # websockify không tự ghi bí mật vào nhật ký của nó
    assert bi_mat_cua(dich_a) not in tep_log.read_text(errors="replace")


# ── #2 khoá dịch vụ bị xoá khỏi /proc/<pid>/environ ───────────────────────────


@pytest.mark.skipif(not Path("/proc/self/environ").exists(), reason="cần /proc của Linux")
def test_khoa_bi_xoa_khoi_proc_environ_sau_khi_nap(tmp_path: Path):
    """os.environ.pop KHÔNG đủ (/proc/<pid>/environ là vùng nhớ lúc exec) — phải xoá dấu trong vùng nhớ đó."""
    ma = textwrap.dedent(
        f"""
        import sys, time
        sys.path.insert(0, {str(GOC_DICH_VU)!r})
        import uvicorn
        import server
        def gia(*a, **k):
            print("san-sang", flush=True)
            time.sleep(30)
        uvicorn.run = gia
        server.main()
        """
    )
    env = {"PATH": os.environ.get("PATH", ""), "TRINH_DUYET_KHOA": KHOA_TEST, "PYTHONDONTWRITEBYTECODE": "1",
           "TRINH_DUYET_THU_MUC_GOC": str(tmp_path)}
    p = subprocess.Popen([sys.executable, "-c", ma], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        dong = p.stdout.readline()
        assert dong.strip() == b"san-sang", p.stderr.read()
        moi_truong = Path(f"/proc/{p.pid}/environ").read_bytes()
        assert KHOA_TEST.encode() not in moi_truong
    finally:
        p.kill()
        p.wait()


# ── #4 tài liệu tự sinh tắt; xác thực trước phân tích thân ────────────────────


@pytest.mark.parametrize("duong", ["/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"])
def test_tai_lieu_tu_sinh_bi_tat(moi_truong_test, duong):
    with TestClient(app) as client:
        assert client.get(duong, headers=HEADER_AUTH).status_code == 404
        # Không khoá: xác thực chạy trước định tuyến nên 401 — tuyệt đối không 200
        assert client.get(duong).status_code == 401


def test_than_json_hong_khong_khoa_tra_401_truoc(moi_truong_test):
    with TestClient(app) as client:
        res = client.post(
            "/ho-so/mo",
            content=b'{"ho_so": "chuoi-phan-chieu-XYZ", hong',
            headers={"Content-Type": "application/json"},
        )
        assert res.status_code == 401
        assert "chuoi-phan-chieu-XYZ" not in res.text
        # Có khoá thì mới tới bước phân tích thân -> 422
        res2 = client.post(
            "/ho-so/mo",
            content=b'{"ho_so": hong',
            headers={"Content-Type": "application/json", **HEADER_AUTH},
        )
        assert res2.status_code == 422


def test_suc_khoe_la_ngoai_le_duy_nhat(moi_truong_test):
    with TestClient(app) as client:
        assert client.get("/suc-khoe").status_code == 200
        assert client.get("/khong-ton-tai").status_code == 401
        assert client.post(f"/ho-so/{ID_A}/dong", json={"giu_phien": False}).status_code == 401


# ── #5 hồ sơ «ma», giết cả nhóm, cổng vòng, xoá thư mục ngoài event loop ──────


def test_mo_lai_ho_so_co_tien_trinh_chet_thi_dung_lai(moi_truong_test):
    quan_ly: QuanLyHoSo = moi_truong_test["quan_ly"]
    bo_chay: BoChayGia = moi_truong_test["bo_chay"]

    async def _chay():
        kq1 = await quan_ly.mo(ID_A, "chromium")
        cu = list(bo_chay.tien_trinh_tao)
        cu[1].chet()  # trình duyệt crash
        kq2 = await quan_ly.mo(ID_A, "chromium")
        assert len(bo_chay.lich_su_lenh) == 8  # dựng lại đủ 4 tiến trình
        assert all(p.da_dung for p in cu)  # bộ cũ đã được dọn
        assert kq2["dich"] != kq1["dich"]  # cổng mới (cấp vòng)
        assert all(p.dang_chay() for p in quan_ly.danh_sach_mo[ID_A].tien_trinh)

    asyncio.run(_chay())


def test_ho_so_chet_khong_giu_cho_tran_dong_thoi(moi_truong_test):
    quan_ly: QuanLyHoSo = moi_truong_test["quan_ly"]
    bo_chay: BoChayGia = moi_truong_test["bo_chay"]
    ids = [c * 32 for c in "1234"]

    async def _chay():
        for i in ids[:3]:
            await quan_ly.mo(i, "chromium")
        bo_chay.tien_trinh_tao[0].chet()  # Xvfb của hồ sơ đầu chết
        await quan_ly.mo(ids[3], "chromium")  # không 429 vì hồ sơ chết đã được dọn
        assert ids[0] not in quan_ly.danh_sach_mo

    asyncio.run(_chay())


def test_tien_trinh_chet_ngay_sau_khi_dung_tra_502_va_don_sach(moi_truong_test):
    bo_chay: BoChayGia = moi_truong_test["bo_chay"]
    quan_ly: QuanLyHoSo = moi_truong_test["quan_ly"]
    bo_chay.chet_ngay = lambda lenh: lenh[0] == "websockify"
    with TestClient(app) as client:
        res = client.post("/ho-so/mo", headers=HEADER_AUTH, json={"ho_so": ID_A, "loai": "chromium"})
        assert res.status_code == 502
        assert quan_ly.danh_sach_mo == {}
        assert all(p.da_dung for p in bo_chay.tien_trinh_tao)
        lenh_ws = bo_chay.lich_su_lenh[3]
        assert not Path(lenh_ws[lenh_ws.index("--auth-source") + 1]).exists()
        # Hết lỗi thì mở lại được, không bị kẹt ở trạng thái «đã mở»
        bo_chay.chet_ngay = None
        assert client.post("/ho-so/mo", headers=HEADER_AUTH, json={"ho_so": ID_A, "loai": "chromium"}).status_code == 200
        assert len(bo_chay.lich_su_lenh) == 8


def _da_chet(pid: int, cho: float) -> bool:
    han = time.monotonic() + cho
    while time.monotonic() < han:
        try:
            trang_thai = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
        except (FileNotFoundError, ProcessLookupError):
            return True
        if trang_thai == "Z":
            return True
        time.sleep(0.05)
    return False


@pytest.mark.skipif(not Path("/proc").is_dir(), reason="cần /proc của Linux")
def test_dung_giet_ca_nhom_tien_trinh(tmp_path: Path):
    """Tiến trình đầu chết vì SIGTERM nhưng con của nó bỏ qua SIGTERM: dung() phải SIGKILL cả nhóm."""
    tep_pid = tmp_path / "con.pid"
    ma = textwrap.dedent(
        f"""
        import os, signal, time
        if os.fork() == 0:
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            with open({str(tep_pid)!r} + ".tam", "w") as f:
                f.write(str(os.getpid()))
            os.rename({str(tep_pid)!r} + ".tam", {str(tep_pid)!r})
            time.sleep(60)
            os._exit(0)
        time.sleep(60)
        """
    )

    async def _chay() -> int:
        h = await server.BoChayThucTe().chay([sys.executable, "-c", ma], {"PATH": os.environ.get("PATH", "")})
        for _ in range(200):
            if tep_pid.exists():
                break
            await asyncio.sleep(0.05)
        pid_con = int(tep_pid.read_text())
        await h.dung()
        return pid_con

    pid_con = asyncio.run(_chay())
    try:
        assert _da_chet(pid_con, cho=3.0), "tiến trình con trong nhóm vẫn sống sau dung()"
    finally:
        try:
            os.kill(pid_con, signal.SIGKILL)
        except ProcessLookupError:
            pass


def test_cong_cap_vong_khong_tai_dung_ngay(moi_truong_test):
    quan_ly: QuanLyHoSo = moi_truong_test["quan_ly"]

    async def _chay():
        a = await quan_ly.mo(ID_A, "chromium")
        await quan_ly.dong(ID_A)
        b = await quan_ly.mo(ID_B, "chromium")
        assert a["dich"].endswith(":6100")
        assert b["dich"].endswith(":6101")  # vé/khách cũ của A (6100) không thể nối nhầm vào B
        # Mở/đóng liên tục: cổng tiến dần 6102..6105 rồi quay vòng, bỏ qua 6101 đang bận
        cong = []
        for i in range(5):
            hid = f"{i:02d}" * 16
            cong.append(int((await quan_ly.mo(hid, "chromium"))["dich"].rsplit(":", 1)[1]))
            await quan_ly.dong(hid)
        assert cong == [6102, 6103, 6104, 6105, 6100]

    asyncio.run(_chay())


def test_xoa_thu_muc_chay_ngoai_event_loop_sau_khi_tien_trinh_chet(moi_truong_test, monkeypatch: pytest.MonkeyPatch):
    quan_ly: QuanLyHoSo = moi_truong_test["quan_ly"]
    bo_chay: BoChayGia = moi_truong_test["bo_chay"]
    goi: list[dict[str, Any]] = []
    rmtree_that = server.shutil.rmtree

    def rmtree_ghi(path, *a, **k):
        try:
            asyncio.get_running_loop()
            trong_loop = True
        except RuntimeError:
            trong_loop = False
        goi.append({"trong_loop": trong_loop, "tat_ca_da_dung": all(p.da_dung for p in bo_chay.tien_trinh_tao)})
        return rmtree_that(path, *a, **k)

    monkeypatch.setattr(server.shutil, "rmtree", rmtree_ghi)

    async def _chay():
        await quan_ly.mo(ID_A, "chromium")
        await quan_ly.dong(ID_A, giu_phien=False)

    asyncio.run(_chay())
    assert goi == [{"trong_loop": False, "tat_ca_da_dung": True}]
    assert not (moi_truong_test["thu_muc_hoso"] / ID_A).exists()


# ── #6 trần hồ sơ trên đĩa, hạn tuyệt đối ─────────────────────────────────────


def test_tran_so_ho_so_tren_dia(moi_truong_test, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TRINH_DUYET_TOI_DA_HO_SO", "2")
    thu_muc: Path = moi_truong_test["thu_muc_hoso"]
    cau_hinh = server.lay_cau_hinh()
    cau_hinh["cho_khoi_dong_giay"] = 0
    quan_ly = QuanLyHoSo(cau_hinh=cau_hinh, bo_chay=BoChayGia(), bo_kiem_tra=BoKiemTraKetNoiGia())
    (thu_muc / ID_A).mkdir(mode=0o700)
    (thu_muc / ID_B).mkdir(mode=0o700)
    (thu_muc / "khong-phai-ho-so").mkdir()  # không được đếm

    async def _chay():
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as e:
            await quan_ly.mo("cccc2222cccc2222cccc2222cccc2222", "chromium")
        assert e.value.status_code == 429
        assert not (thu_muc / "cccc2222cccc2222cccc2222cccc2222").exists()
        # ID đã có trên đĩa vẫn mở lại được
        await quan_ly.mo(ID_A, "chromium")

    asyncio.run(_chay())


def test_han_tuyet_doi_dong_ho_so_du_con_ket_noi(moi_truong_test):
    quan_ly: QuanLyHoSo = moi_truong_test["quan_ly"]
    bo_kiem_tra: BoKiemTraKetNoiGia = moi_truong_test["bo_kiem_tra"]
    thu_muc: Path = moi_truong_test["thu_muc_hoso"]
    gio = [10_000.0]
    quan_ly.dong_ho = lambda: gio[0]

    async def _chay():
        await quan_ly.mo(ID_A, "chromium")
        bo_kiem_tra.dat_ket_noi(quan_ly.danh_sach_mo[ID_A].cong, 1)  # kết nối treo giữ mãi
        gio[0] += 479 * 60
        assert await quan_ly.don_idle() == []
        gio[0] += 2 * 60  # qua mốc 480 phút
        assert await quan_ly.don_idle() == [ID_A]
        assert ID_A not in quan_ly.danh_sach_mo
        assert (thu_muc / ID_A).exists()  # đóng kiểu giữ phiên

    asyncio.run(_chay())


# ── #7 khoá yếu, access log ───────────────────────────────────────────────────


@pytest.mark.parametrize("khoa_yeu", ["a" * 40, "ab" * 20, "0123456789"[:9] * 4])
def test_khoa_it_ky_tu_khac_nhau_bi_tu_choi(khoa_yeu):
    with pytest.raises(RuntimeError) as e:
        kiem_tra_khoa(khoa_yeu)
    assert khoa_yeu not in str(e.value)


def test_khoa_hop_le_duoc_nhan():
    assert kiem_tra_khoa(KHOA_TEST) == KHOA_TEST


def test_main_tat_access_log(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    import uvicorn

    goi: dict[str, Any] = {}
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: goi.update(k))
    monkeypatch.setenv("TRINH_DUYET_KHOA", KHOA_TEST)
    monkeypatch.setattr(server, "_khoa_dich_vu", None, raising=False)
    # Không xoá vùng nhớ môi trường của chính tiến trình pytest
    monkeypatch.setattr(server, "_xoa_dau_moi_truong", lambda ten: None, raising=False)
    server.main()
    assert goi.get("access_log") is False


# ── #3 #7 tệp triển khai ──────────────────────────────────────────────────────


def _compose() -> dict:
    import yaml  # PyYAML có trong .venv qua bandit (requirements-dev.txt)

    return yaml.safe_load((GOC_DICH_VU / "docker-compose.yml").read_text(encoding="utf-8"))


def test_compose_mang_rieng_khong_external():
    tep = _compose()
    dv = tep["services"]["trinh-duyet"]
    assert "ports" not in dv
    assert dv["networks"], "dịch vụ phải gắn vào mạng riêng của nó"
    for ten in dv["networks"]:
        mang = tep["networks"][ten] or {}
        assert not mang.get("external"), f"mạng {ten} phải là mạng riêng do compose tạo"
    # Không dịch vụ nào khác trong tệp gắn chung mạng đó
    assert list(tep["services"]) == ["trinh-duyet"]


def test_compose_gioi_han_quyen():
    dv = _compose()["services"]["trinh-duyet"]
    assert dv["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in dv["security_opt"]
    assert isinstance(dv["pids_limit"], int) and dv["pids_limit"] > 0
    # KHÔNG dùng init: tini sẽ là PID 1 và giữ khoá trong /proc/1/environ; server.py (PID 1) tự thu dọn xác sống
    assert dv.get("init") is not True
    assert dv["environment"].get("TRINH_DUYET_TOI_DA_HO_SO")
    assert dv["environment"].get("TRINH_DUYET_TOI_DA_PHUT")


def test_dockerfile_ghim_phien_ban_va_app_chi_doc():
    noi_dung = (GOC_DICH_VU / "Dockerfile").read_text(encoding="utf-8")
    yeu_cau = (GOC_DICH_VU.parents[1] / "requirements.txt").read_text(encoding="utf-8")
    import pydantic

    for goi in ("fastapi", "uvicorn"):
        ban = next(l.split("==")[1].strip() for l in yeu_cau.splitlines() if l.startswith(f"{goi}=="))
        assert f"{goi}=={ban}" in noi_dung
    assert f"pydantic=={pydantic.VERSION}" in noi_dung
    lenh_chown = [l for l in noi_dung.splitlines() if "chown" in l]
    assert lenh_chown and all("/app" not in l for l in lenh_chown)
    assert "xac_thuc_ws.py" in noi_dung


@pytest.mark.skipif(not Path("/proc").is_dir(), reason="cần /proc của Linux")
def test_thu_don_xac_song_mo_coi_nhung_bo_qua_pid_dang_theo_doi():
    """server.py là PID 1 trong container: phải waitpid con xác sống không ai theo dõi (sau killpg)."""
    pid_mo_coi = os.posix_spawn("/bin/true", ["/bin/true"], {})
    pid_theo_doi = os.posix_spawn("/bin/true", ["/bin/true"], {})
    try:
        for pid in (pid_mo_coi, pid_theo_doi):
            han = time.monotonic() + 3
            while Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z":
                assert time.monotonic() < han
                time.sleep(0.02)
        da_thu = server.thu_don_xac_song({pid_theo_doi})
        assert pid_mo_coi in da_thu and pid_theo_doi not in da_thu
        assert not Path(f"/proc/{pid_mo_coi}").exists()
        assert Path(f"/proc/{pid_theo_doi}").exists()  # vẫn để asyncio/chủ của nó đọc mã thoát
    finally:
        for pid in (pid_mo_coi, pid_theo_doi):
            try:
                os.waitpid(pid, 0)
            except ChildProcessError:
                pass


# ══ Lượt soát thứ hai ═════════════════════════════════════════════════════════


# ── (1) chỉ kết nối ĐÃ XÁC THỰC mới giữ hồ sơ sống ───────────────────────────


def _thoi_diem_bat_dau(pid: int) -> str:
    return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]


def _ho_so_gia(thu_muc: Path) -> Any:
    return types.SimpleNamespace(cong=6100, thu_muc_ket_noi=thu_muc)


@pytest.mark.skipif(not Path("/proc/self/stat").exists(), reason="cần /proc của Linux")
def test_bo_dem_ket_noi_da_xac_thuc_dua_tren_dau_cua_plugin(tmp_path: Path):
    thu_muc = tmp_path / "x.ket-noi"
    thu_muc.mkdir()
    bo = server.BoKiemTraKetNoiDaXacThuc()
    assert bo.dem_ket_noi(_ho_so_gia(thu_muc)) == 0
    # Dấu của tiến trình còn sống, đúng thời điểm bắt đầu -> 1
    (thu_muc / str(os.getpid())).write_text(_thoi_diem_bat_dau(os.getpid()))
    assert bo.dem_ket_noi(_ho_so_gia(thu_muc)) == 1
    # Dấu của tiến trình đã chết -> không tính, bị dọn
    p = subprocess.Popen(["/bin/true"])
    p.wait()
    (thu_muc / str(p.pid)).write_text("1")
    # pid bị tái dùng (thời điểm bắt đầu khác) -> không tính
    (thu_muc / str(os.getppid())).write_text("0")
    assert bo.dem_ket_noi(_ho_so_gia(thu_muc)) == 1
    assert not (thu_muc / str(p.pid)).exists()
    # Thư mục không tồn tại (hồ sơ chưa từng có khách xác thực) -> 0
    assert bo.dem_ket_noi(_ho_so_gia(tmp_path / "khong-co")) == 0


def test_plugin_ws_ghi_dau_chi_khi_xac_thuc_thanh_cong(plugin_ws, tmp_path: Path):
    mod, Loi = plugin_ws
    tep = tmp_path / "bm"
    tep.write_text("w2a:" + "cd34" * 16, encoding="utf-8")
    thu_muc_dau = tmp_path / "bm.ket-noi"
    thu_muc_dau.mkdir(mode=0o700)
    p = mod.BasicTuTep(str(tep))
    with pytest.raises(Loi):
        p.authenticate({"Authorization": _basic("w2a", "sai")}, "127.0.0.1", 6000)
    assert list(thu_muc_dau.iterdir()) == []  # kết nối chưa/không xác thực không để dấu
    p.authenticate({"Authorization": _basic("w2a", "cd34" * 16)}, "127.0.0.1", 6000)
    dau = thu_muc_dau / str(os.getpid())
    assert dau.read_text() == _thoi_diem_bat_dau(os.getpid())
    assert (dau.stat().st_mode & 0o777) == 0o600


@pytest.mark.skipif(not Path("/usr/bin/websockify").exists(), reason="máy không cài websockify")
def test_websockify_that_ket_noi_treo_chua_xac_thuc_khong_giu_ho_so(moi_truong_test, tmp_path: Path):
    """Gửi 1 byte rồi giữ socket (chưa qua plugin): hồ sơ vẫn bị dọn idle. Phiên WS đã xác thực thì giữ hồ sơ."""
    quan_ly: QuanLyHoSo = moi_truong_test["quan_ly"]
    bo_chay: BoChayGia = moi_truong_test["bo_chay"]
    quan_ly.bo_kiem_tra = server.BoKiemTraKetNoiDaXacThuc()
    gio = [50_000.0]
    quan_ly.dong_ho = lambda: gio[0]
    dich = asyncio.run(quan_ly.mo(ID_A, "chromium"))["dich"]
    lenh, env = list(bo_chay.lich_su_lenh[3]), dict(bo_chay.lich_su_env[3])

    vnc = socket.socket()  # đích VNC giả để websockify có chỗ chuyển tiếp
    vnc.bind(("127.0.0.1", 0))
    vnc.listen(8)
    cong = _cong_ranh()
    web = tmp_path / "web"
    web.mkdir()
    lenh[0] = "/usr/bin/websockify"
    lenh[lenh.index("--web") + 1] = str(web)
    lenh[-2:] = [f"127.0.0.1:{cong}", f"127.0.0.1:{vnc.getsockname()[1]}"]
    p = subprocess.Popen(lenh, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    treo: list[socket.socket] = []
    try:
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", cong), timeout=0.2).close()
                break
            except OSError:
                assert p.poll() is None
                time.sleep(0.05)
        for _ in range(5):
            s = socket.create_connection(("127.0.0.1", cong), timeout=5)
            s.sendall(b"G")
            treo.append(s)
        time.sleep(0.5)
        ho_so = quan_ly.danh_sach_mo[ID_A]
        assert quan_ly.bo_kiem_tra.dem_ket_noi(ho_so) == 0

        # Phiên WebSocket ĐÃ xác thực, giữ mở
        ws = socket.create_connection(("127.0.0.1", cong), timeout=5)
        ws.sendall(
            b"GET /websockify HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\n"
            + f"Authorization: {_basic('w2a', bi_mat_cua(dich))}\r\n\r\n".encode()
        )
        assert ws.recv(64).startswith(b"HTTP/1.1 101")
        han = time.monotonic() + 3
        while quan_ly.bo_kiem_tra.dem_ket_noi(ho_so) != 1:
            assert time.monotonic() < han, "phiên đã xác thực không được đếm"
            time.sleep(0.05)
        gio[0] += 130 * 60
        assert asyncio.run(quan_ly.don_idle()) == []  # đang có người dùng thật
        ws.close()
        han = time.monotonic() + 5
        while quan_ly.bo_kiem_tra.dem_ket_noi(ho_so) != 0:
            assert time.monotonic() < han, "phiên đã đóng vẫn được đếm"
            time.sleep(0.05)
        gio[0] += 130 * 60
        # Chỉ còn 5 kết nối treo chưa xác thực: hồ sơ vẫn bị dọn
        assert asyncio.run(quan_ly.don_idle()) == [ID_A]
    finally:
        for s in treo:
            s.close()
        vnc.close()
        p.terminate()
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()


# ── (2) chặn file:// bằng chính sách trình duyệt ─────────────────────────────


def test_chinh_sach_trinh_duyet_chan_file_va_duoc_cai_trong_anh():
    chrome = json.loads((GOC_DICH_VU / "chinh-sach" / "chromium.json").read_text(encoding="utf-8"))
    assert "file://*" in chrome["URLBlocklist"]
    ff = json.loads((GOC_DICH_VU / "chinh-sach" / "firefox-policies.json").read_text(encoding="utf-8"))
    assert "file:///*" in ff["policies"]["WebsiteFilter"]["Block"]
    dockerfile = (GOC_DICH_VU / "Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"^COPY chinh-sach/chromium\.json /etc/chromium/policies/managed/\S+\.json$", dockerfile, re.M)
    assert re.search(
        r"^COPY chinh-sach/firefox-policies\.json /usr/lib/firefox-esr/distribution/policies\.json$", dockerfile, re.M
    )


# ── (4) mo thất bại không để thư mục mồ côi ──────────────────────────────────


def test_mo_that_bai_khong_de_thu_muc_mo_coi_chiem_tran(moi_truong_test, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TRINH_DUYET_TOI_DA_HO_SO", "3")
    thu_muc: Path = moi_truong_test["thu_muc_hoso"]
    cau_hinh = server.lay_cau_hinh()
    cau_hinh["cho_khoi_dong_giay"] = 0
    bo_chay = BoChayGia(chet_ngay=lambda lenh: lenh[0] == "chromium")
    quan_ly = QuanLyHoSo(cau_hinh=cau_hinh, bo_chay=bo_chay, bo_kiem_tra=BoKiemTraKetNoiGia())
    from fastapi import HTTPException

    # Thư mục đã có từ trước (phiên đăng nhập cũ) KHÔNG bị xoá khi dựng thất bại
    cu = thu_muc / ID_A
    cu.mkdir(mode=0o700)
    (cu / "Cookies").write_text("phien-cu", encoding="utf-8")

    async def _chay():
        with pytest.raises(HTTPException) as e:
            await quan_ly.mo(ID_A, "chromium")
        assert e.value.status_code == 502
        assert (cu / "Cookies").read_text(encoding="utf-8") == "phien-cu"
        for hid in ("1" * 32, "2" * 32, "3" * 32):
            with pytest.raises(HTTPException) as e:
                await quan_ly.mo(hid, "chromium")
            assert e.value.status_code == 502
            assert not (thu_muc / hid).exists()
        bo_chay.chet_ngay = None
        await quan_ly.mo("4" * 32, "chromium")  # trần 3: ID_A + ID mới, không 429

    asyncio.run(_chay())


# ── (5) plugin xác thực kiểm IN-PROCESS, không phụ thuộc websockify có cài ───


def _tieu_de_http(*dong: str) -> Any:
    """Tiêu đề kiểu http.client.HTTPMessage — đúng kiểu websockify truyền cho plugin."""
    return http.client.parse_headers(__import__("io").BytesIO(("".join(d + "\r\n" for d in dong) + "\r\n").encode("latin-1")))


def test_plugin_ws_moi_bien_the_tieu_de(plugin_ws, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    mod, Loi = plugin_ws
    bi_mat = "ef56" * 16
    tep = tmp_path / "bm"
    tep.write_text(f"w2a:{bi_mat}\n", encoding="utf-8")  # xuống dòng cuối tệp được bỏ qua
    p = mod.BasicTuTep(str(tep))
    goi_so_sanh: list[tuple[bytes, bytes]] = []
    so_that = mod.hmac.compare_digest
    monkeypatch.setattr(mod.hmac, "compare_digest", lambda a, b: goi_so_sanh.append((a, b)) or so_that(a, b))

    dung = _basic("w2a", bi_mat)
    p.authenticate(_tieu_de_http(f"Authorization: {dung}"), "127.0.0.1", 6000)
    assert goi_so_sanh, "phải so bằng hmac.compare_digest (hằng thời gian)"
    sai = [
        [],  # không có tiêu đề
        ["Authorization: "],
        ["Authorization: Basic"],
        ["Authorization: Basic "],
        [f"Authorization: basic {dung[6:]}"],  # sai hoa/thường của lược đồ
        [f"Authorization: Bearer {dung[6:]}"],
        ["Authorization: Basic %%%"],
        ["Authorization: Basic " + base64.b64encode(b"w2a" + bi_mat.encode()).decode()],  # thiếu ':'
        ["Authorization: Basic " + base64.b64encode(f"w2a:{bi_mat[:-1]}".encode()).decode()],  # thiếu 1 ký tự
        ["Authorization: Basic " + base64.b64encode(f"w2a:{bi_mat}x".encode()).decode()],  # thừa 1 ký tự
        ["Authorization: Basic " + base64.b64encode(f"W2A:{bi_mat}".encode()).decode()],  # sai tên
        ["Authorization: Basic " + base64.b64encode(f"w2a:{bi_mat.upper()}".encode()).decode()],
        ["Authorization: Basic " + base64.b64encode(f":{bi_mat}".encode()).decode()],
        ["Authorization: Basic " + base64.b64encode("w2a:ạ".encode()).decode()],
        # Lặp tiêu đề: tiêu đề ĐẦU sai thì từ chối dù tiêu đề sau đúng
        [f"Authorization: {_basic('w2a', 'sai')}", f"Authorization: {dung}"],
    ]
    for cac_dong in sai:
        with pytest.raises(Loi) as e:
            p.authenticate(_tieu_de_http(*cac_dong), "127.0.0.1", 6000)
        assert e.value.code == 401
        assert e.value.headers.get("WWW-Authenticate", "").startswith("Basic")
        assert bi_mat not in str(e.value)
    # Base64 có khoảng trắng thừa đầu/cuối vẫn đúng
    p.authenticate(_tieu_de_http(f"Authorization: Basic   {dung[6:]}  "), "127.0.0.1", 6000)


@pytest.mark.parametrize("noi_dung", [b"", b"\n", b"w2a", b"w2a:", b":bi-mat", b"   "])
def test_plugin_ws_tep_rong_hoac_hong_thi_khong_khoi_dong(plugin_ws, tmp_path: Path, noi_dung: bytes):
    mod, _ = plugin_ws
    tep = tmp_path / "bm"
    tep.write_bytes(noi_dung)
    with pytest.raises(ValueError):
        mod.BasicTuTep(str(tep))


# ── Đọc cookie hồ sơ Firefox: GET /ho-so/{id}/cookie?mien= ───────────────────
#
# cookies.sqlite GIẢ dựng bằng sqlite3 với đúng bảng/cột của Firefox (moz_cookies). Giá trị cookie là chuỗi GIẢ,
# entropy thấp, đặc trưng để quét rò rỉ trong log/thông báo lỗi.

import sqlite3  # noqa: E402
import tempfile  # noqa: E402

COT_MOZ_COOKIES = (
    "id INTEGER PRIMARY KEY, originAttributes TEXT NOT NULL DEFAULT '', name TEXT, value TEXT, host TEXT, "
    "path TEXT, expiry INTEGER, lastAccessed INTEGER, creationTime INTEGER, isSecure INTEGER, isHttpOnly INTEGER, "
    "inBrowserElement INTEGER DEFAULT 0, sameSite INTEGER DEFAULT 0, rawSameSite INTEGER DEFAULT 0, "
    "schemeMap INTEGER DEFAULT 0"
)
HS_FF = "f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0"
HS_FF_KHAC = "e1e1e1e1e1e1e1e1e1e1e1e1e1e1e1e1"
TUONG_LAI = int(time.time()) + 30 * 86400
QUA_KHU = int(time.time()) - 86400
DUONG_COOKIE = f"/ho-so/{HS_FF}/cookie"


def _tao_ho_so_ff(goc: Path, ho_so: str, cookies: list[dict], loai: str | None = "firefox") -> Path:
    """Thư mục hồ sơ + `.thong_tin.json` + cookies.sqlite giả. Mỗi cookie: dict cột → giá trị (mặc định hợp lệ)."""
    thu_muc = goc / ho_so
    thu_muc.mkdir(mode=0o700)
    if loai is not None:
        (thu_muc / ".thong_tin.json").write_text(json.dumps({"loai": loai}), encoding="utf-8")
    con = sqlite3.connect(thu_muc / "cookies.sqlite")
    con.execute(f"CREATE TABLE moz_cookies ({COT_MOZ_COOKIES})")
    for i, c in enumerate(cookies):
        hang = {"originAttributes": "", "path": "/", "expiry": TUONG_LAI, "lastAccessed": 0,
                "creationTime": 1000 + i, "isSecure": 1, "isHttpOnly": 1} | c
        con.execute(f"INSERT INTO moz_cookies ({', '.join(hang)}) VALUES ({', '.join('?' * len(hang))})",
                    list(hang.values()))
    con.commit()
    con.close()
    return thu_muc


def _goi_cookie(client, ho_so: str = HS_FF, mien: str = "muse.ai", headers=None):
    return client.get(f"/ho-so/{ho_so}/cookie", params={"mien": mien},
                      headers=HEADER_AUTH if headers is None else headers)


def test_cookie_loc_dung_ten_mien(moi_truong_test):
    """Nhận `muse.ai`, `.muse.ai`, `x.muse.ai`; KHÔNG nhận `evilmuse.ai`, `muse.ai.evil.example`, tên miền khác."""
    _tao_ho_so_ff(moi_truong_test["thu_muc_hoso"], HS_FF, [
        {"name": "a_goc", "value": "GIA-goc-0001", "host": "muse.ai"},
        {"name": "b_cham", "value": "GIA-cham-0002", "host": ".muse.ai"},
        {"name": "c_con", "value": "GIA-con-0003", "host": "x.muse.ai"},
        {"name": "d_ac", "value": "GIA-AC-evil-0004", "host": "evilmuse.ai"},
        {"name": "e_ac", "value": "GIA-AC-evil-0005", "host": ".evilmuse.ai"},
        {"name": "f_ac", "value": "GIA-AC-hau-0006", "host": "muse.ai.evil.example"},
        {"name": "g_khac", "value": "GIA-AC-khac-0007", "host": "khac.example"},
        {"name": "h_ai", "value": "GIA-AC-tld-0008", "host": ".ai"},
    ])
    with TestClient(app) as client:
        r = _goi_cookie(client)
    assert r.status_code == 200, r.text
    assert r.json() == {"cookie": "a_goc=GIA-goc-0001; b_cham=GIA-cham-0002; c_con=GIA-con-0003", "so_cookie": 3}


def test_cookie_loai_het_han_giay_va_mili_giay(moi_truong_test):
    """`expiry` là giây (Firefox cũ) hay mili-giây (Firefox mới) — nhận biết theo độ lớn từng dòng."""
    _tao_ho_so_ff(moi_truong_test["thu_muc_hoso"], HS_FF, [
        {"name": "s_con", "value": "GIA-s-con", "host": ".muse.ai", "expiry": TUONG_LAI},
        {"name": "s_het", "value": "GIA-AC-s-het", "host": ".muse.ai", "expiry": QUA_KHU},
        {"name": "ms_con", "value": "GIA-ms-con", "host": ".muse.ai", "expiry": TUONG_LAI * 1000},
        {"name": "ms_het", "value": "GIA-AC-ms-het", "host": ".muse.ai", "expiry": QUA_KHU * 1000},
        {"name": "khong", "value": "GIA-AC-khong", "host": ".muse.ai", "expiry": 0},
        {"name": "rong", "value": "GIA-AC-null", "host": ".muse.ai", "expiry": None},
    ])
    with TestClient(app) as client:
        r = _goi_cookie(client)
    assert r.status_code == 200, r.text
    assert r.json() == {"cookie": "s_con=GIA-s-con; ms_con=GIA-ms-con", "so_cookie": 2}


def test_cookie_loai_ky_tu_cam_va_ngu_canh_khac(moi_truong_test):
    """Tên ngoài bảng token RFC 6265, giá trị có ký tự điều khiển/xuống dòng/«;»/ngoài ASCII in được → loại.
    Cookie của ngăn chứa/phân vùng (originAttributes khác rỗng) → loại. Trùng tên → giữ một (đường dài nhất)."""
    _tao_ho_so_ff(moi_truong_test["thu_muc_hoso"], HS_FF, [
        {"name": "tot", "value": "GIA-tot", "host": ".muse.ai"},
        {"name": "ten;ac", "value": "GIA-AC-1", "host": ".muse.ai"},
        {"name": "ten=ac", "value": "GIA-AC-2", "host": ".muse.ai"},
        {"name": "ten ac", "value": "GIA-AC-3", "host": ".muse.ai"},
        {"name": "", "value": "GIA-AC-4", "host": ".muse.ai"},
        {"name": "xuong_dong", "value": "GIA-AC-5\r\nX-Chen: 1", "host": ".muse.ai"},
        {"name": "cham_phay", "value": "GIA-AC-6; chen=1", "host": ".muse.ai"},
        {"name": "nul", "value": "GIA-AC-7\x00", "host": ".muse.ai"},
        {"name": "tab", "value": "GIA-AC-8\t", "host": ".muse.ai"},
        {"name": "unicode", "value": "GIA-AC-9-ạ", "host": ".muse.ai"},
        {"name": "ngan_chua", "value": "GIA-AC-10", "host": ".muse.ai", "originAttributes": "^userContextId=1"},
        # Trùng tên: giữ cookie trình duyệt gửi TRƯỚC (đường dài nhất, rồi tạo sớm nhất). Cookie đúng nằm GIỮA theo id
        # nên thứ tự chèn (id tăng/giảm) hay chỉ một khoá sắp xếp đều chọn sai.
        {"name": "trung", "value": "GIA-trung-ngan", "host": ".muse.ai", "path": "/", "creationTime": 1},
        {"name": "trung", "value": "GIA-trung-dai", "host": ".muse.ai", "path": "/api", "creationTime": 2},
        {"name": "trung", "value": "GIA-trung-dai-moi", "host": ".muse.ai", "path": "/api", "creationTime": 3},
        {"name": "nhi_phan", "value": b"GIA-AC-blob", "host": ".muse.ai"},  # BLOB, không phải chữ
    ])
    with TestClient(app) as client:
        r = _goi_cookie(client)
    assert r.status_code == 200, r.text
    assert r.json() == {"cookie": "trung=GIA-trung-dai; tot=GIA-tot", "so_cookie": 2}


def test_cookie_tran_so_luong_va_do_dai(moi_truong_test):
    ds = [{"name": "qua_dai", "value": "G" * (server.TRAN_DO_DAI_COOKIE + 1), "host": ".muse.ai"}]
    ds += [{"name": f"c{i:04d}", "value": f"GIA-{i:04d}", "host": ".muse.ai"} for i in range(server.TRAN_SO_COOKIE + 50)]
    _tao_ho_so_ff(moi_truong_test["thu_muc_hoso"], HS_FF, ds)
    with TestClient(app) as client:
        kq = _goi_cookie(client).json()
    assert kq["so_cookie"] == server.TRAN_SO_COOKIE
    assert "qua_dai" not in kq["cookie"]
    assert len(kq["cookie"]) <= server.TRAN_CHUOI_COOKIE
    assert kq["cookie"].count("; ") == server.TRAN_SO_COOKIE - 1


def test_cookie_tran_tong_do_dai_chuoi(moi_truong_test, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(server, "TRAN_CHUOI_COOKIE", 40)
    _tao_ho_so_ff(moi_truong_test["thu_muc_hoso"], HS_FF, [
        {"name": f"c{i}", "value": "GIA-0123456789", "host": ".muse.ai"} for i in range(5)])
    with TestClient(app) as client:
        kq = _goi_cookie(client).json()
    assert len(kq["cookie"]) <= 40 and kq["so_cookie"] == 2


def test_cookie_doc_ca_dong_chi_nam_trong_wal(moi_truong_test):
    """Firefox đang mở giữ dữ liệu mới trong `cookies.sqlite-wal`: dòng CHỈ có trong WAL vẫn phải đọc được."""
    thu_muc = _tao_ho_so_ff(moi_truong_test["thu_muc_hoso"], HS_FF, [
        {"name": "cu", "value": "GIA-trong-db", "host": ".muse.ai"}])
    nguoi_ghi = sqlite3.connect(thu_muc / "cookies.sqlite")
    try:
        assert nguoi_ghi.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        nguoi_ghi.execute("PRAGMA wal_autocheckpoint=0")
        nguoi_ghi.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        nguoi_ghi.execute("INSERT INTO moz_cookies (name, value, host, path, expiry, creationTime) "
                          "VALUES ('moi', 'GIA-chi-trong-wal', '.muse.ai', '/', ?, 5000)", (TUONG_LAI,))
        nguoi_ghi.commit()
        assert (thu_muc / "cookies.sqlite-wal").stat().st_size > 0
        # Bản chính KHÔNG có dòng mới (chỉ nằm trong WAL) — nếu có thì phép thử vô nghĩa.
        tach = Path(tempfile.mkdtemp())
        try:
            shutil_copy = __import__("shutil").copyfile
            shutil_copy(thu_muc / "cookies.sqlite", tach / "x.sqlite")
            c = sqlite3.connect(tach / "x.sqlite")
            assert c.execute("SELECT count(*) FROM moz_cookies WHERE name='moi'").fetchone()[0] == 0
            c.close()
        finally:
            for t in tach.iterdir():
                t.unlink()
            tach.rmdir()
        with TestClient(app) as client:
            kq = _goi_cookie(client).json()
    finally:
        nguoi_ghi.close()
    assert kq == {"cookie": "cu=GIA-trong-db; moi=GIA-chi-trong-wal", "so_cookie": 2}


@pytest.mark.parametrize("loai", ["chromium", None])
def test_cookie_ho_so_chromium_hoac_khong_ro_loai_tra_501(moi_truong_test, loai):
    """Chromium mã hoá cookie và cần cổng điều khiển trình duyệt — cố ý KHÔNG hỗ trợ. Thiếu `.thong_tin.json` thì
    coi như chromium (giống GET /ho-so)."""
    _tao_ho_so_ff(moi_truong_test["thu_muc_hoso"], HS_FF, [
        {"name": "a", "value": "GIA-AC-chromium", "host": ".muse.ai"}], loai=loai)
    with TestClient(app) as client:
        r = _goi_cookie(client)
    assert r.status_code == 501
    assert "GIA-AC-chromium" not in r.text
    assert "Firefox" in r.json()["detail"]


def test_cookie_ho_so_dang_mo_409(moi_truong_test):
    """Chép `cookies.sqlite` rồi `-wal` KHÔNG nguyên tử khi Firefox đang ghi: chỉ đọc hồ sơ ĐÃ ĐÓNG — đang mở → 409,
    đóng (giữ phiên) xong thì đọc được."""
    _tao_ho_so_ff(moi_truong_test["thu_muc_hoso"], HS_FF, [{"name": "a", "value": "GIA-a", "host": ".muse.ai"}])
    with TestClient(app) as client:
        assert client.post("/ho-so/mo", headers=HEADER_AUTH, json={"ho_so": HS_FF, "loai": "firefox"}).status_code == 200
        r = _goi_cookie(client)
        assert r.status_code == 409 and "GIA-a" not in r.text
        client.post(f"/ho-so/{HS_FF}/dong", headers=HEADER_AUTH)
        assert _goi_cookie(client).json() == {"cookie": "a=GIA-a", "so_cookie": 1}


@pytest.mark.parametrize("mien", [
    "", "MUSE.AI", ".muse.ai", "muse.ai.", "-muse.ai", "muse-.ai", "muse..ai", "muse_ai.com", "muse.ai/x",
    "muse.ai%0a", "muse.ai\n", "ai", "localhost", "a" * 64 + ".com", ".".join(["a" * 60] * 5), "mu se.ai",
    "<script>.ai", "muse.ai:443", "*.muse.ai",
])
def test_cookie_ten_mien_sai_400_khong_phan_chieu(moi_truong_test, mien):
    _tao_ho_so_ff(moi_truong_test["thu_muc_hoso"], HS_FF, [{"name": "a", "value": "GIA-AC-mien", "host": ".muse.ai"}])
    with TestClient(app) as client:
        r = _goi_cookie(client, mien=mien)
    assert r.status_code == 400
    assert "GIA-AC-mien" not in r.text
    if len(mien) >= 4:  # chuỗi ngắn («ai») trùng chữ thường trong câu báo — không phải phản chiếu
        assert mien not in r.json()["detail"]


def test_cookie_ten_mien_hop_le_nhieu_dang(moi_truong_test):
    _tao_ho_so_ff(moi_truong_test["thu_muc_hoso"], HS_FF, [
        {"name": "a", "value": "GIA-a", "host": ".sub-1.vi-du.example"}])
    with TestClient(app) as client:
        assert _goi_cookie(client, mien="sub-1.vi-du.example").json()["so_cookie"] == 1
        assert _goi_cookie(client, mien="vi-du.example").json()["so_cookie"] == 1
        assert _goi_cookie(client, mien="khac.example").json() == {"cookie": "", "so_cookie": 0}
        assert client.get(DUONG_COOKIE, headers=HEADER_AUTH).status_code == 400  # thiếu `mien`


@pytest.mark.parametrize("id_sai", ["../etc", "F0F0F0F0F0F0F0F0", "f0f0", "f0f0f0f0f0f0f0f0g", "..%2f..%2fetc"])
def test_cookie_id_sai_400(moi_truong_test, id_sai):
    with TestClient(app) as client:
        r = client.get(f"/ho-so/{id_sai}/cookie", params={"mien": "muse.ai"}, headers=HEADER_AUTH)
    if "/" in id_sai or "%2f" in id_sai:
        assert r.status_code in (400, 403, 404)  # đường có «/» không khớp tuyến — không bao giờ 200
    else:
        assert r.status_code == 400


def test_cookie_khong_khoa_hoac_sai_khoa_401(moi_truong_test):
    _tao_ho_so_ff(moi_truong_test["thu_muc_hoso"], HS_FF, [{"name": "a", "value": "GIA-AC-401", "host": ".muse.ai"}])
    with TestClient(app) as client:
        for h in ({}, {"Authorization": f"Bearer {KHOA_KHAC}"}, {"Authorization": f"Basic {KHOA_TEST}"},
                  {"Authorization": KHOA_TEST}):
            r = _goi_cookie(client, headers=h)
            assert r.status_code == 401, h
            assert "GIA-AC-401" not in r.text


def test_cookie_ho_so_chua_co_hoac_khong_co_kho_cookie_404_chung(moi_truong_test):
    goc: Path = moi_truong_test["thu_muc_hoso"]
    with TestClient(app) as client:
        r_chua = _goi_cookie(client)  # thư mục hồ sơ chưa từng tạo
        thu_muc = _tao_ho_so_ff(goc, HS_FF, [])
        (thu_muc / "cookies.sqlite").unlink()
        r_thieu = _goi_cookie(client)
    assert r_chua.status_code == r_thieu.status_code == 404
    assert r_chua.json() == r_thieu.json()
    assert r_chua.json()["detail"] != "Not Found", "404 phải do tuyến cookie trả, không phải «không có tuyến»"


def test_cookie_symlink_sang_ho_so_khac_bi_chan(moi_truong_test, tmp_path: Path):
    """IDOR qua symlink: trình duyệt (cùng uid) cài `cookies.sqlite` của hồ sơ A trỏ sang hồ sơ B, hoặc biến cả
    thư mục hồ sơ A thành symlink tới B / ra ngoài gốc → không đọc được cookie của B."""
    goc: Path = moi_truong_test["thu_muc_hoso"]
    b = _tao_ho_so_ff(goc, HS_FF_KHAC, [{"name": "b", "value": "GIA-AC-cua-B", "host": ".muse.ai"}])
    a = _tao_ho_so_ff(goc, HS_FF, [])
    (a / "cookies.sqlite").unlink()
    (a / "cookies.sqlite").symlink_to(b / "cookies.sqlite")
    with TestClient(app) as client:
        r1 = _goi_cookie(client)
        assert r1.status_code == 404 and "GIA-AC-cua-B" not in r1.text
        # Thư mục hồ sơ là symlink tới hồ sơ khác TRONG gốc
        (a / "cookies.sqlite").unlink()
        (a / ".thong_tin.json").unlink()
        a.rmdir()
        a.symlink_to(b)
        r2 = _goi_cookie(client)
        assert r2.status_code in (403, 404) and "GIA-AC-cua-B" not in r2.text
        # Thư mục hồ sơ là symlink ra NGOÀI gốc
        ngoai = _tao_ho_so_ff(tmp_path, "ngoai", [{"name": "n", "value": "GIA-AC-ngoai", "host": ".muse.ai"}])
        a.unlink()
        a.symlink_to(ngoai)
        r3 = _goi_cookie(client)
        assert r3.status_code in (403, 404) and "GIA-AC-ngoai" not in r3.text
        # WAL là symlink: không theo
        a.unlink()
        a2 = _tao_ho_so_ff(goc, HS_FF, [{"name": "a", "value": "GIA-a", "host": ".muse.ai"}])
        (a2 / "cookies.sqlite-wal").symlink_to(b / "cookies.sqlite")
        r4 = _goi_cookie(client)
        assert r4.status_code == 404 and "GIA-AC-cua-B" not in r4.text


def test_cookie_tep_khong_phai_tep_thuong_hoac_qua_lon(moi_truong_test, monkeypatch: pytest.MonkeyPatch):
    goc: Path = moi_truong_test["thu_muc_hoso"]
    thu_muc = _tao_ho_so_ff(goc, HS_FF, [{"name": "a", "value": "GIA-AC-lon", "host": ".muse.ai"}])
    with TestClient(app) as client:
        monkeypatch.setattr(server, "TRAN_TEP_COOKIE_BYTE", 100)
        r = _goi_cookie(client)
        assert r.status_code == 413 and "GIA-AC-lon" not in r.text
        monkeypatch.setattr(server, "TRAN_TEP_COOKIE_BYTE", 64 * 1024 * 1024)
        (thu_muc / "cookies.sqlite").unlink()
        (thu_muc / "cookies.sqlite").mkdir()
        assert _goi_cookie(client).status_code == 404


def test_cookie_kho_hong_tra_500_chung(moi_truong_test):
    thu_muc = _tao_ho_so_ff(moi_truong_test["thu_muc_hoso"], HS_FF, [])
    (thu_muc / "cookies.sqlite").write_bytes(b"day khong phai sqlite GIA-AC-hong " * 20)
    with TestClient(app) as client:
        r = _goi_cookie(client)
    assert r.status_code == 500
    assert "GIA-AC-hong" not in r.text and "sqlite" not in r.json()["detail"].lower()


@pytest.fixture
def thu_muc_tam_rieng(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Đặt thư mục tạm của tiến trình vào một chỗ riêng để kiểm nó RỖNG sau mỗi lần đọc cookie."""
    tam = tmp_path / "tam-rieng"
    tam.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(tam))
    return tam


def test_cookie_thu_muc_tam_0700_va_bi_xoa_moi_duong(moi_truong_test, thu_muc_tam_rieng: Path,
                                                     monkeypatch: pytest.MonkeyPatch):
    goc: Path = moi_truong_test["thu_muc_hoso"]
    thu_muc = _tao_ho_so_ff(goc, HS_FF, [{"name": "a", "value": "GIA-a", "host": ".muse.ai"}])
    quyen: list[int] = []
    doc_that = server._doc_cookie_tu_ban_sao

    def doc_va_ghi_quyen(tep: Path, *a):
        quyen.append(tep.parent.stat().st_mode & 0o777)
        assert tep.parent.parent == thu_muc_tam_rieng
        return doc_that(tep, *a)

    monkeypatch.setattr(server, "_doc_cookie_tu_ban_sao", doc_va_ghi_quyen)
    with TestClient(app) as client:
        assert _goi_cookie(client).status_code == 200
        assert quyen == [0o700]
        assert list(thu_muc_tam_rieng.iterdir()) == []
        # Đường lỗi: kho hỏng (500), tệp quá lớn (413), Chromium (501) — thư mục tạm vẫn bị xoá
        (thu_muc / "cookies.sqlite").write_bytes(b"hong" * 100)
        assert _goi_cookie(client).status_code == 500
        assert list(thu_muc_tam_rieng.iterdir()) == []
        monkeypatch.setattr(server, "TRAN_TEP_COOKIE_BYTE", 10)
        assert _goi_cookie(client).status_code == 413
        assert list(thu_muc_tam_rieng.iterdir()) == []


def test_cookie_khong_ro_ri_vao_log_moi_muc(moi_truong_test, caplog: pytest.LogCaptureFixture):
    """Quét TOÀN BỘ log mức DEBUG (mọi logger) qua đường thành công lẫn đường lỗi: không giá trị cookie nào lọt ra."""
    caplog.set_level(logging.DEBUG)
    goc: Path = moi_truong_test["thu_muc_hoso"]
    _tao_ho_so_ff(goc, HS_FF, [{"name": "phien", "value": "GIA-LOG-bimat-0001", "host": ".muse.ai"}])
    hong = _tao_ho_so_ff(goc, HS_FF_KHAC, [])
    (hong / "cookies.sqlite").write_bytes(b"GIA-LOG-bimat-0002 " * 50)
    with TestClient(app) as client:
        assert "GIA-LOG-bimat-0001" in _goi_cookie(client).text
        _goi_cookie(client, ho_so=HS_FF_KHAC)
        _goi_cookie(client, mien="KHONG.HOP.LE")
        client.get("/ho-so", headers=HEADER_AUTH)
    assert caplog.records, "phải bắt được log để phép quét có nghĩa"
    for rec in caplog.records:
        chu = rec.getMessage() + repr(rec.args) + str(rec.exc_info or "")
        for bi_mat in ("GIA-LOG-bimat-0001", "GIA-LOG-bimat-0002"):
            assert bi_mat not in chu, rec.getMessage()


def test_danh_sach_ho_so_khong_tra_cookie(moi_truong_test):
    _tao_ho_so_ff(moi_truong_test["thu_muc_hoso"], HS_FF, [{"name": "a", "value": "GIA-AC-ds", "host": ".muse.ai"}])
    with TestClient(app) as client:
        r = client.get("/ho-so", headers=HEADER_AUTH)
    assert r.status_code == 200 and "GIA-AC-ds" not in r.text
    assert any(m["ho_so"] == HS_FF and m["loai"] == "firefox" for m in r.json())


# ── Workspace (mô hình của c2a: tên hiển thị + thư mục hồ sơ ID ngẫu nhiên) ──────────


def test_tao_workspace_id_ngau_nhien_ten_trong_tep_0600(moi_truong_test):
    goc: Path = moi_truong_test["thu_muc_hoso"]
    with TestClient(app) as client:
        r1 = client.post("/ho-so", headers=HEADER_AUTH, json={"ten": "  Muse cá nhân  "})
        r2 = client.post("/ho-so", headers=HEADER_AUTH, json={"ten": "Muse cá nhân"})
        assert r1.status_code == r2.status_code == 200, r1.text
        a, b = r1.json(), r2.json()
        assert a["ten"] == b["ten"] == "Muse cá nhân"
        assert re.fullmatch(r"[a-f0-9]{24}", a["ho_so"]) and a["ho_so"] != b["ho_so"], "tên trùng vẫn hai hồ sơ riêng"
        thu_muc = goc / a["ho_so"]
        assert thu_muc.stat().st_mode & 0o777 == 0o700
        tep = thu_muc / ".workspace.json"
        assert tep.stat().st_mode & 0o777 == 0o600
        assert json.loads(tep.read_text(encoding="utf-8")) == {"name": "Muse cá nhân"}
        assert json.loads((thu_muc / ".thong_tin.json").read_text(encoding="utf-8")) == {"loai": "firefox"}
        assert client.post("/ho-so", headers=HEADER_AUTH, json={"ten": "x", "loai": "chromium"}).status_code == 200
        assert client.post("/ho-so", headers=HEADER_AUTH, json={"ten": "x", "loai": "opera"}).status_code == 400
        assert client.post("/ho-so", json={"ten": "khong khoa"}).status_code == 401
    assert len(moi_truong_test["bo_chay"].lich_su_lenh) == 0, "tạo workspace KHÔNG mở trình duyệt"


@pytest.mark.parametrize("ten", ["", "   ", "x" * 101, "dong\nmoi", "tab\tx", "nul\x00", 5, None, ["a"]])
def test_tao_workspace_ten_sai_400_khong_phan_chieu(moi_truong_test, ten):
    with TestClient(app) as client:
        r = client.post("/ho-so", headers=HEADER_AUTH, json={"ten": ten})
    assert r.status_code in (400, 422)
    if isinstance(ten, str) and len(ten.strip()) >= 4:
        assert ten not in r.text
    assert list(moi_truong_test["thu_muc_hoso"].iterdir()) == []


def test_tao_workspace_tran_tren_dia_429(moi_truong_test, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(moi_truong_test["quan_ly"], "toi_da_ho_so", 2)
    with TestClient(app) as client:
        assert client.post("/ho-so", headers=HEADER_AUTH, json={"ten": "a"}).status_code == 200
        assert client.post("/ho-so", headers=HEADER_AUTH, json={"ten": "b"}).status_code == 200
        assert client.post("/ho-so", headers=HEADER_AUTH, json={"ten": "c"}).status_code == 429


def test_danh_sach_co_ten_dang_mo_thu_cong(moi_truong_test):
    goc: Path = moi_truong_test["thu_muc_hoso"]
    with TestClient(app) as client:
        a = client.post("/ho-so", headers=HEADER_AUTH, json={"ten": "<img src=x onerror=alert(1)>"}).json()["ho_so"]
        b = client.post("/ho-so", headers=HEADER_AUTH, json={"ten": "Tài khoản B"}).json()["ho_so"]
        client.post("/ho-so/mo", headers=HEADER_AUTH, json={"ho_so": b, "loai": "firefox"})
        # Hồ sơ cũ (mở bằng /ho-so/mo, không có .workspace.json) → tên là chính ID, như c2a
        cu = "0123456789abcdef0123456789abcdef"
        client.post("/ho-so/mo", headers=HEADER_AUTH, json={"ho_so": cu, "loai": "chromium"})
        client.post(f"/ho-so/{cu}/dong", headers=HEADER_AUTH)
        # Tên trên đĩa quá dài (trình duyệt cùng uid sửa được tệp) → cắt 100; symlink → không theo
        (goc / cu / ".workspace.json").write_text(json.dumps({"name": "y" * 500}), encoding="utf-8")
        (goc / a / ".workspace.json").unlink()
        (goc / a / ".workspace.json").symlink_to(goc / b / ".workspace.json")
        ds = {m["ho_so"]: m for m in client.get("/ho-so", headers=HEADER_AUTH).json()}
    assert ds[b]["ten"] == "Tài khoản B" and ds[b]["dang_mo"] is True and ds[b]["thu_cong"] is True
    assert ds[b]["trang_thai"] == "dang_mo" and ds[b]["loai"] == "firefox"
    assert ds[cu]["ten"] == "y" * 100 and ds[cu]["dang_mo"] is False and ds[cu]["thu_cong"] is False
    assert ds[a]["ten"] == a, "symlink .workspace.json không được theo"


@pytest.mark.parametrize("loai, vi_tri", [("firefox", -1), ("chromium", -1)])
def test_mo_voi_url_bat_dau_la_doi_so_cuoi(moi_truong_test, loai, vi_tri):
    bo_chay: BoChayGia = moi_truong_test["bo_chay"]
    with TestClient(app) as client:
        r = client.post("/ho-so/mo", headers=HEADER_AUTH,
                        json={"ho_so": HS_FF, "loai": loai, "url": "https://muse.ai/dang-nhap?x=1"})
    assert r.status_code == 200, r.text
    lenh = bo_chay.lich_su_lenh[1]
    assert lenh[vi_tri] == "https://muse.ai/dang-nhap?x=1"
    assert "about:blank" not in lenh


def test_mo_khong_url_la_trang_trong(moi_truong_test):
    bo_chay: BoChayGia = moi_truong_test["bo_chay"]
    with TestClient(app) as client:
        client.post("/ho-so/mo", headers=HEADER_AUTH, json={"ho_so": HS_FF, "loai": "firefox", "url": ""})
    assert bo_chay.lich_su_lenh[1][-1] == "about:blank"


@pytest.mark.parametrize("url", [
    "javascript:alert(1)", "file:///hoso/.bi-mat-ws/x", "ftp://vi-du.example/", "about:config", "data:text/html,x",
    "http://nguoi:matkhau@vi-du.example/", "http://nguoi@vi-du.example/", "https://", "https:///duong",
    "https://vi-du.example/" + "a" * 2048, "https://vi-du.example/a b", "https://vi-du.example/\n--profile=/tmp",
    "--profile=/hoso/khac", " https://vi-du.example/", "chrome://settings", 5,
])
def test_mo_url_sai_400_khong_chay_gi(moi_truong_test, url):
    bo_chay: BoChayGia = moi_truong_test["bo_chay"]
    with TestClient(app) as client:
        r = client.post("/ho-so/mo", headers=HEADER_AUTH, json={"ho_so": HS_FF, "loai": "firefox", "url": url})
    assert r.status_code in (400, 422)
    assert bo_chay.lich_su_lenh == []
    if isinstance(url, str) and len(url) > 8:
        assert url not in r.text


def test_mo_khong_ghi_xuyen_symlink_sang_ho_so_khac(moi_truong_test):
    """Trình duyệt (cùng uid, HOME = thư mục hồ sơ) đặt `.thong_tin.json` của hồ sơ A thành symlink tới kho cookie của
    hồ sơ B: lần «Mở» A sau đó KHÔNG được ghi đè tệp của B."""
    goc: Path = moi_truong_test["thu_muc_hoso"]
    b = _tao_ho_so_ff(goc, HS_FF_KHAC, [{"name": "b", "value": "GIA-b", "host": ".muse.ai"}])
    truoc = (b / "cookies.sqlite").read_bytes()
    a = goc / HS_FF
    a.mkdir(mode=0o700)
    (a / ".thong_tin.json").symlink_to(b / "cookies.sqlite")
    with TestClient(app) as client:
        r = client.post("/ho-so/mo", headers=HEADER_AUTH, json={"ho_so": HS_FF, "loai": "firefox"})
    assert r.status_code in (200, 502)
    assert (b / "cookies.sqlite").read_bytes() == truoc, "ghi xuyên symlink sang hồ sơ khác"


# ── FIFO trong hồ sơ (trình duyệt cùng uid tạo được) không được treo dịch vụ ─────────────

import threading  # noqa: E402


def _co_han(ham, han: float = 5.0):
    """Chạy `ham` trong luồng nền; quá `han` giây là TREO → test đỏ (không chờ mãi)."""
    kq: dict = {}
    t = threading.Thread(target=lambda: kq.setdefault("r", ham()), daemon=True)
    t.start()
    t.join(han)
    assert not t.is_alive(), "treo: thao tác không trả về trong hạn"
    return kq["r"]


@pytest.mark.parametrize("ten_tep", ["cookies.sqlite", "cookies.sqlite-wal"])
def test_fifo_kho_cookie_khong_treo(moi_truong_test, ten_tep):
    goc: Path = moi_truong_test["thu_muc_hoso"]
    thu_muc = _tao_ho_so_ff(goc, HS_FF, [{"name": "a", "value": "GIA-a", "host": ".muse.ai"}])
    if ten_tep == "cookies.sqlite":
        (thu_muc / ten_tep).unlink()
    os.mkfifo(thu_muc / ten_tep)
    with TestClient(app) as client:
        r = _co_han(lambda: _goi_cookie(client))
        assert r.status_code == 404
        assert _co_han(lambda: client.get("/ho-so", headers=HEADER_AUTH)).status_code == 200, "khoá không bị kẹt"


@pytest.mark.parametrize("ten_tep", [".workspace.json", ".thong_tin.json"])
def test_fifo_tep_thong_tin_khong_treo(moi_truong_test, ten_tep):
    goc: Path = moi_truong_test["thu_muc_hoso"]
    thu_muc = _tao_ho_so_ff(goc, HS_FF, [{"name": "a", "value": "GIA-a", "host": ".muse.ai"}])
    (thu_muc / ten_tep).unlink(missing_ok=True)
    os.mkfifo(thu_muc / ten_tep)
    with TestClient(app) as client:
        r = _co_han(lambda: client.get("/ho-so", headers=HEADER_AUTH))
        assert r.status_code == 200
        muc = next(m for m in r.json() if m["ho_so"] == HS_FF)
        assert muc["ten"] == HS_FF if ten_tep == ".workspace.json" else muc["loai"] == "chromium"
        assert _co_han(lambda: _goi_cookie(client)).status_code == (200 if ten_tep == ".workspace.json" else 501)
        # «Mở» ghi .thong_tin.json: FIFO không được làm treo lúc mở để ghi
        r_mo = _co_han(lambda: client.post("/ho-so/mo", headers=HEADER_AUTH, json={"ho_so": HS_FF, "loai": "firefox"}))
        assert r_mo.status_code in (200, 502)
        assert _co_han(lambda: client.get("/ho-so", headers=HEADER_AUTH)).status_code == 200


def test_fifo_dau_ket_noi_khong_treo(tmp_path: Path):
    """Dấu kết nối (đếm trong vòng dọn idle, chạy trên event loop): FIFO không được làm treo."""
    thu_muc = tmp_path / "dau"
    thu_muc.mkdir()
    os.mkfifo(thu_muc / str(os.getpid()))
    ho_so = types.SimpleNamespace(thu_muc_ket_noi=thu_muc)
    assert _co_han(lambda: server.BoKiemTraKetNoiDaXacThuc().dem_ket_noi(ho_so)) == 0


def test_khoi_dong_don_ban_sao_cookie_sot(moi_truong_test, thu_muc_tam_rieng: Path, tmp_path: Path):
    """Bị SIGKILL/OOM giữa lúc đọc cookie thì `finally` không chạy: lúc KHỞI ĐỘNG dọn `w2a-cookie-*` cũ — chỉ thư mục
    thật (không symlink), 0700, cùng uid, tên đúng khuôn mkdtemp, và chỉ trong thư mục tạm của chính dịch vụ."""
    sot = thu_muc_tam_rieng / "w2a-cookie-ab12_CD"
    sot.mkdir(mode=0o700)
    (sot / "cookies.sqlite").write_bytes(b"GIA-sot")
    (sot / "cookies.sqlite-wal").write_bytes(b"GIA-sot")
    ngoai = tmp_path / "ngoai-GIA"
    ngoai.mkdir()
    (ngoai / "giu.txt").write_text("giu", encoding="utf-8")
    lien_ket = thu_muc_tam_rieng / "w2a-cookie-lienket"
    lien_ket.symlink_to(ngoai)
    mo_rong = thu_muc_tam_rieng / "w2a-cookie-quyen755"
    mo_rong.mkdir(mode=0o755)
    os.chmod(mo_rong, 0o755)
    khac = thu_muc_tam_rieng / "khac-w2a-cookie-x"
    khac.mkdir(mode=0o700)
    with TestClient(app):
        pass
    assert not sot.exists()
    assert lien_ket.is_symlink() and (ngoai / "giu.txt").exists(), "không theo symlink"
    assert mo_rong.exists() and khac.exists()


def test_compose_thu_muc_tam_cookie_la_tmpfs_rieng():
    """Bản sao cookie (tạm) nằm trên tmpfs riêng (RAM), không trên đĩa của container."""
    dv = _compose()["services"]["trinh-duyet"]
    tmpdir = dv["environment"].get("TMPDIR")
    assert tmpdir and tmpdir != "/tmp"
    assert any(str(x).split(":", 1)[0] == tmpdir for x in dv.get("tmpfs", [])), dv.get("tmpfs")
