"""Test NV77 — gemini-web gắn danh tính chung (NV67 nhóm 3, việc 8 thí điểm).

Hợp đồng:
- Tài khoản cũ (cookie chuỗi, không danh_tinh_id) chạy y như trước khi chưa gắn.
- Gắn danh_tinh_id: hiển thị tên theo danh tính (che email), đăng ký tham chiếu
  với kho danh tính của lõi (danh_tinh.gan) để xoá danh tính bị chặn 409 khi còn
  tài khoản trỏ tới; gỡ gắn/dung() thì gỡ tham chiếu.
- Gắn danh tính không tồn tại → lỗi rõ ràng ngay ở nap().
- Manifest khai can_module ["vnc-xa"].
- Mapping gắn bền qua khởi động nhờ kho riêng của module (hash cookie → danh_tinh_id),
  không chạm lõi, không thêm trường manifest.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

from app import danh_tinh, kho
from app.kham_pha import kiem_manifest
from app.loi_openai import LoiOpenAI

GOC = Path(__file__).resolve().parents[1]
THU_MUC = GOC / "modules" / "gemini-web"
TEN_GOI = "gemini_web_nv77_test"


def _tai_goi():
    for ten in [t for t in list(sys.modules) if t == TEN_GOI or t.startswith(TEN_GOI + ".")]:
        del sys.modules[ten]
    goi = types.ModuleType(TEN_GOI)
    goi.__path__ = [str(THU_MUC)]
    sys.modules[TEN_GOI] = goi
    for ten_tep in ("client", "converter", "module"):
        spec = importlib.util.spec_from_file_location(
            f"{TEN_GOI}.{ten_tep}", THU_MUC / f"{ten_tep}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"{TEN_GOI}.{ten_tep}"] = mod
        spec.loader.exec_module(mod)
    return (sys.modules[f"{TEN_GOI}.module"],
            sys.modules[f"{TEN_GOI}.client"])


mod_module, mod_client = _tai_goi()
Module = mod_module.Module
MANIFEST = json.loads((THU_MUC / "manifest.json").read_text(encoding="utf-8"))

EMAIL = "ban@gmail.com"


def chay(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Máy chủ giả tối thiểu: init và batchexecute luôn 200.
# ---------------------------------------------------------------------------

def _lam_gia():
    import httpx

    def xu_ly(req):
        duong = req.url.path
        if duong == "/":
            return httpx.Response(200, text='<html>"SNlM0e":"snl-gia"</html>')
        if duong == "/_/BardChatUi/data/batchexecute":
            inner = json.dumps([None, None, None, None, [["cid-1", "xin chao"]]])
            than = json.dumps(["wrb.fr", "hNvQH", inner, None, None, None, "generic"])
            return httpx.Response(200, text=")]}'\n" + than)
        return httpx.Response(404, text="khong co")

    return httpx.MockTransport(xu_ly)


def ck(psid):
    return f"__Secure-1PSID={psid}; __Secure-1PSIDTS=ts-{psid}"


@pytest.fixture
def moi_truong(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("WEB2API_DATA", str(data))
    transport = _lam_gia()

    def tao_client(self, psid, psidts):
        return mod_client.GeminiWebClient(psid, psidts, transport=transport)

    monkeypatch.setattr(Module, "_tao_client", tao_client)
    return data


def _tao_danh_tinh(email=EMAIL, nhan="Ban"):
    return danh_tinh.tao(email, nhan)


def _mod(moi_truong):
    mod = Module(MANIFEST)
    mod.kho = kho.kho_rieng("gemini-web")
    return mod


# ---------------------------------------------------------------------------
# Tương thích ngược: tài khoản cũ không gắn vẫn chạy y như trước.
# ---------------------------------------------------------------------------

def test_tai_khoan_cu_chay_khong_can_danh_tinh(moi_truong):
    """Cookie cũ (chuỗi, không danh_tinh_id): nap + chat 200, không chạm kho danh tính."""
    mod = _mod(moi_truong)
    mod.nap({"cookies": [ck("psid-cu")]})
    assert len(mod._tai_khoan) == 1
    tk = mod._tai_khoan[0]
    assert tk.danh_tinh_id is None
    ma, _, _ = chay(mod.chat({"model": "gemini-web/3.5-flash",
                              "messages": [{"role": "user", "content": "hi"}]}, False))
    assert ma == 200
    assert danh_tinh.danh_sach() == []
    # Hiển thị vẫn dạng che bí mật như cũ.
    assert tk.ten_che().startswith("…")


def test_cai_cu_luu_dung_chuoi_goc(moi_truong):
    """cai() với cookies cũ: ok, cau_hinh_luu giữ đúng chuỗi gốc từng tài khoản."""
    mod = _mod(moi_truong)
    kq = chay(mod.cai({"cookies": [ck("psid-1"), ck("psid-2")]}))
    assert kq["ok"] is True, kq
    luu = kq["cau_hinh_luu"]["cookies"]
    assert luu == [ck("psid-1"), ck("psid-2")]
    # Không lộ cookie thô trong kết quả từng khoá.
    for muc in kq["ket_qua_tung_khoa"]:
        assert "psid-1" not in muc["khoa"] and "psid-2" not in muc["khoa"]


# ---------------------------------------------------------------------------
# Gắn danh tính.
# ---------------------------------------------------------------------------

def test_gan_danh_tinh_hien_thi_theo_danh_tinh(moi_truong):
    """Gắn rồi: tên hiển thị theo danh tính (che email), chat vẫn chạy."""
    dt = _tao_danh_tinh(nhan="")
    mod = _mod(moi_truong)
    mod.nap({"cookies": [ck("psid-a")], "danh_tinh": [dt["id"]]})
    tk = mod._tai_khoan[0]
    assert tk.danh_tinh_id == dt["id"]
    assert tk.ten_che() == "b***@gmail.com"  # che email, không lộ nguyên
    assert EMAIL not in tk.ten_che()
    ma, _, _ = chay(mod.chat({"model": "gemini-web/3.5-flash",
                              "messages": [{"role": "user", "content": "hi"}]}, False))
    assert ma == 200


def test_gan_danh_tinh_dung_nhan_neu_co(moi_truong):
    dt = _tao_danh_tinh(nhan="Gmail ca nhan")
    mod = _mod(moi_truong)
    mod.nap({"cookies": [ck("psid-a")], "danh_tinh": [dt["id"]]})
    assert mod._tai_khoan[0].ten_che() == "Gmail ca nhan"


def test_gan_danh_tinh_khong_ton_tai_loi_ro_rang(moi_truong):
    """danh_tinh_id lạ → ValueError nêu rõ id và chữ «không tồn tại»."""
    mod = _mod(moi_truong)
    with pytest.raises(ValueError, match="dt-khong-co.*không tồn tại"):
        mod.nap({"cookies": [ck("psid-a")], "danh_tinh": ["dt-khong-co"]})
    assert mod._tai_khoan == []


def test_danh_tinh_sai_do_dai_loi_ro_rang(moi_truong):
    mod = _mod(moi_truong)
    with pytest.raises(ValueError, match="danh_tinh"):
        mod.nap({"cookies": [ck("psid-a"), ck("psid-b")], "danh_tinh": ["dt-x"]})


def test_xoa_danh_tinh_bi_chan_khi_module_dang_gan(moi_truong):
    """Đã gắn qua module → danh_tinh.xoa phải 409 con_tai_khoan (lõi chặn)."""
    dt = _tao_danh_tinh()
    mod = _mod(moi_truong)
    mod.nap({"cookies": [ck("psid-a")], "danh_tinh": [dt["id"]]})
    with pytest.raises(LoiOpenAI) as exc:
        danh_tinh.xoa(dt["id"])
    assert exc.value.code == "con_tai_khoan"
    assert exc.value.status_code == 409


def test_go_gan_roi_xoa_duoc(moi_truong):
    """Gỡ gắn trong cấu hình (danh_tinh None) → tham chiếu được gỡ → xoá được."""
    dt = _tao_danh_tinh()
    mod = _mod(moi_truong)
    mod.nap({"cookies": [ck("psid-a")], "danh_tinh": [dt["id"]]})
    mod.nap({"cookies": [ck("psid-a")], "danh_tinh": [None]})
    assert mod._tai_khoan[0].danh_tinh_id is None
    assert danh_tinh.xoa(dt["id"]) is True


def test_dung_go_tham_chieu(moi_truong):
    """dung() (gỡ module) gỡ tham chiếu danh tính của chính nó → xoá được."""
    dt = _tao_danh_tinh()
    mod = _mod(moi_truong)
    mod.nap({"cookies": [ck("psid-a")], "danh_tinh": [dt["id"]]})
    chay(mod.dung())
    assert danh_tinh.xoa(dt["id"]) is True


def test_dung_khong_go_cua_the_hien_moi(moi_truong):
    """Thể hiện cũ dung() sau khi thể hiện mới đã nap: không được gỡ tham chiếu của mới."""
    dt = _tao_danh_tinh()
    cu = _mod(moi_truong)
    cu.nap({"cookies": [ck("psid-a")], "danh_tinh": [dt["id"]]})
    moi = _mod(moi_truong)
    moi.nap({"cookies": [ck("psid-a")], "danh_tinh": [dt["id"]]})
    chay(cu.dung())  # mô phỏng _cai: the mới nap trước, cu dung sau
    with pytest.raises(LoiOpenAI) as exc:
        danh_tinh.xoa(dt["id"])
    assert exc.value.code == "con_tai_khoan"


def test_gan_ben_qua_nap_lai(moi_truong):
    """Mapping gắn bền qua lần nap sau (khởi động lại): đọc từ kho riêng theo hash cookie."""
    dt = _tao_danh_tinh(nhan="")
    mod = _mod(moi_truong)
    mod.nap({"cookies": [ck("psid-a")], "danh_tinh": [dt["id"]]})
    mod2 = _mod(moi_truong)
    mod2.nap({"cookies": [ck("psid-a")]})  # cấu hình đã lưu không có khoá danh_tinh
    assert mod2._tai_khoan[0].danh_tinh_id == dt["id"]
    assert mod2._tai_khoan[0].ten_che() == "b***@gmail.com"


def test_hon_hop_gan_va_khong_gan(moi_truong):
    """Một tài khoản gắn, một không: cả hai chạy, chỉ tài khoản gắn hiện tên danh tính."""
    dt = _tao_danh_tinh(nhan="")
    mod = _mod(moi_truong)
    mod.nap({"cookies": [ck("psid-a"), ck("psid-b")],
             "danh_tinh": [dt["id"], None]})
    assert mod._tai_khoan[0].ten_che() == "b***@gmail.com"
    assert mod._tai_khoan[1].ten_che().startswith("…")
    for i in (0, 1):
        ma, _, _ = chay(mod.chat({"model": "gemini-web/3.5-flash",
                                  "messages": [{"role": "user", "content": "hi"}]}, False))
        assert ma == 200


# ---------------------------------------------------------------------------
# Manifest.
# ---------------------------------------------------------------------------

def test_manifest_khai_can_module_vnc_xa():
    assert MANIFEST["can_module"] == ["vnc-xa"]
    assert MANIFEST["hang"] == "A"
    assert kiem_manifest(MANIFEST) is None
