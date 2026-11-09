"""Q-B (xem màn hình đăng nhập chỉ-đọc) và Q-D (nhật ký chẩn đoán) của cổng MCP — đề nghị xin quyền 04/11.

Hai công cụ mặc định TẮT (không hiện trong tools/list); bật bằng WEB2API_MCP_QUAN_SAT_MODULE (id module), riêng ảnh màn hình còn
cần WEB2API_MCP_MAN_HINH_HET_HAN (mốc ISO, tối đa 24 giờ tính từ lúc gọi) và chỉ chụp khi có lượt đăng nhập do chính cổng MCP bắt đầu.
"""
from __future__ import annotations

import base64
import datetime as dt

import pytest

from app import cong_mcp_cong_cu as cmcu
from test_mcp_cong import moi_truong_mcp, rpc, tao_khoa_khach_mcp  # noqa: F401

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64 + b"\xff\xd9"


class ModuleGia:
    def __init__(self, anh=JPEG, buoc=None):
        self.anh, self.buoc, self.so_lan_chup = anh, buoc or [], 0

    async def man_hinh(self):
        self.so_lan_chup += 1
        return self.anh

    async def chan_doan(self, so_buoc=50):
        return {"ok": True, "buoc": self.buoc[:so_buoc]}


def _han(gio: float) -> str:
    return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=gio)).isoformat()


def _ten_cong_cu(client, khoa):
    r = rpc(client, khoa, "tools/list")
    return {t["name"] for t in r.json()["result"]["tools"]}


def _bat(monkeypatch, so, mod, het_han=None):
    monkeypatch.setenv("WEB2API_MCP_QUAN_SAT_MODULE", "may-dang-nhap")
    if het_han is not None:
        monkeypatch.setenv("WEB2API_MCP_MAN_HINH_HET_HAN", het_han)
    so.dang_chay["may-dang-nhap"] = mod


def _co_phien(trang_thai="dang_chay"):
    cmcu._PHIEN_DANG_NHAP = {"phien_id": "p1", "tai_khoan_id": "tk-x", "trang_thai": "dang_chay", "ma_loi": "",
                              "huong_dan": "", "bat_dau_luc": "x", "ket_thuc_luc": None}
    if trang_thai != "dang_chay":
        cmcu._ket_thuc_phien(cmcu._PHIEN_DANG_NHAP, trang_thai)


def test_mac_dinh_tat_khong_hien_khong_goi_duoc(moi_truong_mcp):
    client, _ = moi_truong_mcp
    khoa = tao_khoa_khach_mcp(client)
    ten = _ten_cong_cu(client, khoa)
    assert not ({"man_hinh_dang_nhap", "chan_doan_dang_nhap"} & ten)
    for t in ("man_hinh_dang_nhap", "chan_doan_dang_nhap"):
        r = rpc(client, khoa, "tools/call", {"name": t, "arguments": {}})
        assert r.json()["error"]["code"] == -32602


def test_leo_thang_quyen(moi_truong_mcp, monkeypatch):
    client, so = moi_truong_mcp
    khoa = tao_khoa_khach_mcp(client)
    khoa_khac = tao_khoa_khach_mcp(client, pham_vi=["chat"], nhan="khac")
    _bat(monkeypatch, so, ModuleGia(), _han(1))
    _co_phien()
    goi = {"name": "man_hinh_dang_nhap", "arguments": {}}
    assert rpc(client, None, "tools/call", goi).status_code == 401
    assert rpc(client, "w2a_khong-co-khoa-nay-0000000000", "tools/call", goi).status_code in (401, 403)
    assert rpc(client, khoa_khac, "tools/call", goi).status_code == 403
    from test_mcp_cong import _KHOA_QT_GIA
    assert rpc(client, _KHOA_QT_GIA, "tools/call", goi).status_code == 403


def test_chan_doan_tra_buoc_va_che_email(moi_truong_mcp, monkeypatch):
    client, so = moi_truong_mcp
    khoa = tao_khoa_khach_mcp(client)
    buoc = [{"loai": "bam", "selector": 'a[href*="chu.nha@example.com"]', "ket_qua": "khong_thay"},
            {"loai": "dieu_huong", "url": "accounts.google.com/signin/v2/identifier", "ket_qua": "ok"}]
    _bat(monkeypatch, so, ModuleGia(buoc=buoc))
    assert "chan_doan_dang_nhap" in _ten_cong_cu(client, khoa)
    r = rpc(client, khoa, "tools/call", {"name": "chan_doan_dang_nhap", "arguments": {"so_buoc": 10}})
    assert "chu.nha@example.com" not in r.text
    sc = r.json()["result"]["structuredContent"]
    assert len(sc["buoc"]) == 2 and sc["buoc"][1]["loai"] == "dieu_huong"


def test_man_hinh_can_han_hop_le(moi_truong_mcp, monkeypatch):
    client, so = moi_truong_mcp
    khoa = tao_khoa_khach_mcp(client)
    _co_phien()
    for het_han in (None, _han(-1), _han(48), "khong-phai-ngay"):
        monkeypatch.delenv("WEB2API_MCP_MAN_HINH_HET_HAN", raising=False)
        _bat(monkeypatch, so, ModuleGia(), het_han)
        assert "man_hinh_dang_nhap" not in _ten_cong_cu(client, khoa), het_han
    _bat(monkeypatch, so, ModuleGia(), _han(1))
    assert "man_hinh_dang_nhap" in _ten_cong_cu(client, khoa)


def test_man_hinh_chi_khi_co_phien_mcp(moi_truong_mcp, monkeypatch):
    client, so = moi_truong_mcp
    khoa = tao_khoa_khach_mcp(client)
    mod = ModuleGia()
    _bat(monkeypatch, so, mod, _han(1))
    cmcu._PHIEN_DANG_NHAP = None
    r = rpc(client, khoa, "tools/call", {"name": "man_hinh_dang_nhap", "arguments": {}})
    assert r.json()["result"]["structuredContent"]["loi"] == "khong_co_phien"
    assert mod.so_lan_chup == 0  # không chụp (chụp cũng làm trình duyệt khỏi tự đóng)
    _co_phien("thanh_cong")
    cmcu._PHIEN_DANG_NHAP["ket_thuc_mono"] = cmcu._bay_gio() - 3600  # phiên đã xong từ lâu
    r = rpc(client, khoa, "tools/call", {"name": "man_hinh_dang_nhap", "arguments": {}})
    assert r.json()["result"]["structuredContent"]["loi"] == "khong_co_phien"
    assert mod.so_lan_chup == 0


def test_man_hinh_tra_khoi_anh_va_khong_lo_vao_van_ban(moi_truong_mcp, monkeypatch):
    client, so = moi_truong_mcp
    khoa = tao_khoa_khach_mcp(client)
    _bat(monkeypatch, so, ModuleGia(), _han(1))
    _co_phien()
    r = rpc(client, khoa, "tools/call", {"name": "man_hinh_dang_nhap", "arguments": {}})
    kq = r.json()["result"]
    anh = [c for c in kq["content"] if c["type"] == "image"]
    assert len(anh) == 1 and anh[0]["mimeType"] == "image/jpeg"
    assert base64.b64decode(anh[0]["data"]) == JPEG
    van_ban = "".join(c["text"] for c in kq["content"] if c["type"] == "text")
    assert anh[0]["data"] not in van_ban and anh[0]["data"] not in str(kq["structuredContent"])
    assert kq["isError"] is False


def test_man_hinh_tu_choi_anh_khong_phai_jpeg(moi_truong_mcp, monkeypatch):
    client, so = moi_truong_mcp
    khoa = tao_khoa_khach_mcp(client)
    _bat(monkeypatch, so, ModuleGia(anh=b"<html>trang rieng</html>"), _han(1))
    _co_phien()
    r = rpc(client, khoa, "tools/call", {"name": "man_hinh_dang_nhap", "arguments": {}})
    kq = r.json()["result"]
    assert kq["isError"] is True and not [c for c in kq["content"] if c["type"] == "image"]


def test_man_hinh_giai_cach_giua_hai_lan(moi_truong_mcp, monkeypatch):
    client, so = moi_truong_mcp
    khoa = tao_khoa_khach_mcp(client)
    mod = ModuleGia()
    _bat(monkeypatch, so, mod, _han(1))
    _co_phien()
    for _ in range(3):
        rpc(client, khoa, "tools/call", {"name": "man_hinh_dang_nhap", "arguments": {}})
    assert mod.so_lan_chup == 1  # hai lần dồn sau bị từ chối, không đụng tới trình duyệt
