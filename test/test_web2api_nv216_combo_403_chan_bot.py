"""NV216 — ĐO combo khi bậc đầu trả 403 «chặn chống bot» (bước a: ĐO, chưa sửa).

Qua LÕI THẬT (TestClient + app thật + BoDieuPhoiCombo/BoCauChi thật), bậc 1 giả trả 403
với thân lỗi JSON như module chatgpt-web sau NV215 (trả tuple (403, headers, body),
KHÔNG raise — đúng cách module thật báo ra combo), bậc 2 giả trả 200.

Ba câu hỏi của spec:
1. có hạ xuống bậc kế ngay, trong trần thời gian không?
2. mạch ngắt có mở vĩnh viễn / quá lâu vì 403 không? (403 = lỗi tạm: phân loại theo
   mã HTTP + cấu trúc, CẤM danh sách từ khoá)
3. tiêu đề x-web2api-duong có ghi đúng bậc bị bỏ qua không?

Harness (module giả điều khiển bằng kich_ban.json, TestClient) tái dùng từ
test_web2api_combo.py — tiền lệ test_401_lui_ngay_va_nghi_ca_nhom_viec.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from app.combo import bo_cau_chi, phan_loai
from app.kham_pha import phan_loai_loi_mac_dinh
from app.main import app
from app.so_module import SoModule
from gia import ghi_thu_muc
from test_web2api_combo import (
    KHOA_QT,
    QT,
    chat,
    dat_kich_ban,
    dat_lai_trang_thai,
    doc_goi,
    mau_module_gia,
    put,
)

#: Thân lỗi 403 như chatgpt-web trả ra sau NV215 (LoiChanBot → tuple, giữ khoá, dừng xoay).
THAN_403_CHATGPT_WEB = {
    "error": {
        "message": "ChatGPT Web không chat được qua web (chatgpt.com chặn chống bot) — "
                   "dùng module openaicodex (đăng nhập OAuth) hoặc khoá API.",
        "type": "api_error",
        "code": "bot_chan",
    }
}

#: Thân 403 khác hẳn (không nhắc «chặn chống bot») — phân loại không được phụ thuộc chữ.
THAN_403_KHAC = {"error": {"message": "Forbidden", "type": "api_error", "code": "forbidden"}}


@pytest.fixture
def moi_truong_403(tmp_path, monkeypatch):
    """Hai module giả: n216a (bậc 1, sẽ trả 403) và n216b (bậc 2, trả 200)."""
    data_dir = tmp_path / "data"
    (data_dir / "modules").mkdir(parents=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setenv("WEB2API_COMBO_NGUONG", "3")
    monkeypatch.setenv("WEB2API_COMBO_NGHI_GIAY", "1")  # nghỉ ngắn để đo hồi phục nhanh
    monkeypatch.setenv("WEB2API_COMBO_DINH_GIAY", "0")
    monkeypatch.delenv("WEB2API_COMBO_TOI_DA_GIAY", raising=False)
    dat_lai_trang_thai()
    ghi_thu_muc(data_dir / "modules" / "mod-n216a", mau_module_gia("mod-n216a", "n216a"))
    ghi_thu_muc(data_dir / "modules" / "mod-n216b", mau_module_gia("mod-n216b", "n216b"))
    with TestClient(app) as client:
        so = SoModule()
        client.app.state.so = so
        asyncio.run(so.khoi_dong())
        assert client.post("/api/modules/mod-n216a/cai", json={"cau_hinh": {}}, headers=QT).status_code == 200
        assert client.post("/api/modules/mod-n216b/cai", json={"cau_hinh": {}}, headers=QT).status_code == 200
        assert put(client, "cb403", [{"model": "n216a/m1"}, {"model": "n216b/m2"}]).status_code == 200
        yield client, data_dir
    dat_lai_trang_thai()


def test_403_ha_xuong_bac_ke_ngay_trong_tran(moi_truong_403):
    """Q1: bậc 1 trả 403 → combo hạ xuống bậc 2 NGAY (mỗi bậc gọi đúng 1 lần,
    không thử lại, không chờ), trả 200 trong trần thời gian tổng."""
    client, data_dir = moi_truong_403
    dat_kich_ban(data_dir, "n216a:chat", 403, noi_dung=THAN_403_CHATGPT_WEB)
    t0 = time.monotonic()
    r = chat(client, "combo/cb403", noi="q1")
    elapsed = time.monotonic() - t0
    assert r.status_code == 200, r.text
    assert r.headers["x-web2api-route"] == "n216b/m2"
    assert "tu-n216b" in r.text
    assert doc_goi(data_dir) == ["n216a:chat", "n216b:chat"], \
        "mỗi bậc gọi đúng 1 lần — không thử lại bậc 403, không bỏ qua bậc 2"
    assert elapsed < 5.0, f"phải hạ bậc NGAY trong trần thời gian (mất {elapsed:.2f}s)"


def test_403_duong_ghi_dung_bac_bi_bo_qua(moi_truong_403):
    """Q3: x-web2api-duong ghi từng bước đã thử — bậc 1 với mã 403 + phân loại,
    bậc 2 với 200 — chỉ mã cố định, không lộ thân lỗi."""
    client, data_dir = moi_truong_403
    dat_kich_ban(data_dir, "n216a:chat", 403, noi_dung=THAN_403_CHATGPT_WEB)
    r = chat(client, "combo/cb403", noi="q3")
    assert r.status_code == 200
    duong = r.headers.get("x-web2api-duong")
    assert duong, "thiếu tiêu đề x-web2api-duong"
    chang = duong.split(" > ")
    assert len(chang) == 2, duong
    assert chang[0].startswith("n216a/m1:403:khoa_hong"), duong
    assert chang[1].startswith("n216b/m2:200:ok"), duong
    assert "chặn chống bot" not in duong and "bot_chan" not in duong


def test_403_phan_loai_theo_ma_khong_phu_thuoc_than_loi(moi_truong_403):
    """Q2 (phân loại): 403 → khoa_hong theo MÃ HTTP thuần — hai thân lỗi khác nhau
    cho cùng một phân loại và cùng một đường đi; không dò chuỗi (CẤM danh sách từ khoá)."""
    assert phan_loai(403) == "khoa_hong"
    assert phan_loai_loi_mac_dinh(403) == "khoa_hong"
    client, data_dir = moi_truong_403
    dat_kich_ban(data_dir, "n216a:chat", 403, noi_dung=THAN_403_KHAC)
    r = chat(client, "combo/cb403", noi="q2b")
    assert r.status_code == 200
    duong = r.headers["x-web2api-duong"]
    assert duong.split(" > ")[0].startswith("n216a/m1:403:khoa_hong"), duong


def test_403_cau_chi_mo_nhung_khong_vinh_vien(moi_truong_403):
    """Q2 (mạch ngắt): 403 → cầu chì (module, chat) mở NGAY (nghỉ theo
    WEB2API_COMBO_NGHI_GIAY, không đợi đủ ngưỡng) NHƯNG có hạn — hết thời gian
    nghỉ là được thăm dò lại, không mở vĩnh viễn."""
    client, data_dir = moi_truong_403
    dat_kich_ban(data_dir, "n216a:chat", 403, noi_dung=THAN_403_CHATGPT_WEB)
    r = chat(client, "combo/cb403", noi="q2a")
    assert r.status_code == 200
    stats = bo_cau_chi.get_stats("mod-n216a", "chat")
    assert stats["trang_thai"] == "mo", "401/403: nghỉ ngay, không đợi đủ ngưỡng"
    assert stats["ma_loi_cuoi"] == 403
    assert stats["so_lan_mo"] == 1
    assert 0 <= stats["mo_con_lai_giay"] <= 1.0, \
        "thời gian mở bị chặn bởi WEB2API_COMBO_NGHI_GIAY (ở đây đặt 1s để đo nhanh)"
    time.sleep(1.3)  # qua thời gian nghỉ
    assert bo_cau_chi.trang_thai("mod-n216a", "chat") == "cho_tham_do", \
        "hết nghỉ phải được thăm dò lại — không mở vĩnh viễn"
