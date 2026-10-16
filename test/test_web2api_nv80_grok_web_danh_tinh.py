"""Test NV80 — grok-web gắn danh tính chung (theo khuôn NV77).

Hợp đồng:
- Tài khoản cũ (cookie, không danh_tinh_id) nạp y như trước khi chưa gắn.
- Gắn danh_tinh_id: hiển thị tên theo danh tính (che email), đăng ký tham chiếu
  với kho danh tính của lõi (danh_tinh.gan) để xoá danh tính bị chặn 409 khi còn
  tài khoản trỏ tới; gỡ gắn/dung() thì gỡ tham chiếu.
- Gắn danh tính không tồn tại → lỗi rõ ràng ngay ở nap().
- Manifest khai can_module ["vnc-xa"].
- Mapping gắn bền qua khởi động nhờ kho riêng của module (hash cookie → danh_tinh_id).
"""

from __future__ import annotations

import asyncio
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
THU_MUC = GOC / "modules" / "grok-web"
TEN_GOI = "grok_web_nv80_test"


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
    return sys.modules[f"{TEN_GOI}.module"]


mod_module = _tai_goi()
Module = mod_module.Module
MANIFEST = json.loads((THU_MUC / "manifest.json").read_text(encoding="utf-8"))

EMAIL = "ban@gmail.com"


def chay(coro):
    return asyncio.run(coro)


def ck(sso):
    return f"sso={sso}; x-userid=uid-{sso}; phien=abc123"


@pytest.fixture
def moi_truong(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("WEB2API_DATA", str(data))
    return data


def _tao_danh_tinh(email=EMAIL, nhan="Ban"):
    return danh_tinh.tao(email, nhan)


def _mod(moi_truong):
    mod = Module(MANIFEST)
    mod.kho = kho.kho_rieng("grok-web")
    return mod


# ---------------------------------------------------------------------------
# Tương thích ngược.
# ---------------------------------------------------------------------------

def test_tai_khoan_cu_chay_khong_can_danh_tinh(moi_truong):
    """Cookie cũ (không danh_tinh_id): nap ok, không chạm kho danh tính."""
    mod = _mod(moi_truong)
    chay(mod.nap({"tai_khoan": [ck("sso-cu")]}))
    assert len(mod._tai_khoan) == 1
    assert mod._dt_id == [None]
    assert mod._dt_ten == [None]
    assert danh_tinh.danh_sach() == []


# ---------------------------------------------------------------------------
# Gắn danh tính.
# ---------------------------------------------------------------------------

def test_gan_danh_tinh_hien_thi_theo_danh_tinh(moi_truong):
    dt = _tao_danh_tinh(nhan="")
    mod = _mod(moi_truong)
    chay(mod.nap({"tai_khoan": [ck("sso-a")], "danh_tinh": [dt["id"]]}))
    assert mod._dt_id == [dt["id"]]
    assert mod._dt_ten == ["b***@gmail.com"]  # che email, không lộ nguyên
    assert EMAIL not in mod._dt_ten[0]


def test_gan_danh_tinh_dung_nhan_neu_co(moi_truong):
    dt = _tao_danh_tinh(nhan="Gmail ca nhan")
    mod = _mod(moi_truong)
    chay(mod.nap({"tai_khoan": [ck("sso-a")], "danh_tinh": [dt["id"]]}))
    assert mod._dt_ten == ["Gmail ca nhan"]


def test_gan_danh_tinh_khong_ton_tai_loi_ro_rang(moi_truong):
    mod = _mod(moi_truong)
    with pytest.raises(ValueError, match="dt-khong-co.*không tồn tại"):
        chay(mod.nap({"tai_khoan": [ck("sso-a")], "danh_tinh": ["dt-khong-co"]}))
    assert mod._tai_khoan == []


def test_danh_tinh_sai_do_dai_loi_ro_rang(moi_truong):
    mod = _mod(moi_truong)
    with pytest.raises(ValueError, match="danh_tinh"):
        chay(mod.nap({"tai_khoan": [ck("sso-a"), ck("sso-b")],
                      "danh_tinh": ["dt-x"]}))
    assert mod._tai_khoan == []


def test_xoa_danh_tinh_bi_chan_khi_module_dang_gan(moi_truong):
    dt = _tao_danh_tinh()
    mod = _mod(moi_truong)
    chay(mod.nap({"tai_khoan": [ck("sso-a")], "danh_tinh": [dt["id"]]}))
    with pytest.raises(LoiOpenAI) as exc:
        danh_tinh.xoa(dt["id"])
    assert exc.value.code == "con_tai_khoan"
    assert exc.value.status_code == 409


def test_go_gan_roi_xoa_duoc(moi_truong):
    dt = _tao_danh_tinh()
    mod = _mod(moi_truong)
    chay(mod.nap({"tai_khoan": [ck("sso-a")], "danh_tinh": [dt["id"]]}))
    chay(mod.nap({"tai_khoan": [ck("sso-a")], "danh_tinh": [None]}))
    assert mod._dt_id == [None]
    assert danh_tinh.xoa(dt["id"]) is True


def test_dung_go_tham_chieu(moi_truong):
    dt = _tao_danh_tinh()
    mod = _mod(moi_truong)
    chay(mod.nap({"tai_khoan": [ck("sso-a")], "danh_tinh": [dt["id"]]}))
    chay(mod.dung())
    assert danh_tinh.xoa(dt["id"]) is True


def test_dung_khong_go_cua_the_hien_moi(moi_truong):
    dt = _tao_danh_tinh()
    cu = _mod(moi_truong)
    chay(cu.nap({"tai_khoan": [ck("sso-a")], "danh_tinh": [dt["id"]]}))
    moi = _mod(moi_truong)
    chay(moi.nap({"tai_khoan": [ck("sso-a")], "danh_tinh": [dt["id"]]}))
    chay(cu.dung())  # thể hiện cũ dung sau: không được gỡ tham chiếu của mới
    with pytest.raises(LoiOpenAI) as exc:
        danh_tinh.xoa(dt["id"])
    assert exc.value.code == "con_tai_khoan"


def test_gan_ben_qua_nap_lai(moi_truong):
    """Mapping gắn bền qua lần nap sau: đọc từ kho riêng theo hash cookie."""
    dt = _tao_danh_tinh(nhan="")
    mod = _mod(moi_truong)
    chay(mod.nap({"tai_khoan": [ck("sso-a")], "danh_tinh": [dt["id"]]}))
    mod2 = _mod(moi_truong)
    chay(mod2.nap({"tai_khoan": [ck("sso-a")]}))  # cấu hình đã lưu không có danh_tinh
    assert mod2._dt_id == [dt["id"]]
    assert mod2._dt_ten == ["b***@gmail.com"]


def test_hon_hop_gan_va_khong_gan(moi_truong):
    dt = _tao_danh_tinh(nhan="")
    mod = _mod(moi_truong)
    chay(mod.nap({"tai_khoan": [ck("sso-a"), ck("sso-b")],
                  "danh_tinh": [dt["id"], None]}))
    assert mod._dt_id == [dt["id"], None]
    assert mod._dt_ten[0] == "b***@gmail.com"
    assert mod._dt_ten[1] is None


# ---------------------------------------------------------------------------
# Manifest.
# ---------------------------------------------------------------------------

def test_manifest_khai_can_module_vnc_xa():
    assert MANIFEST["can_module"] == ["vnc-xa"]
    assert MANIFEST["hang"] == "A"
    assert kiem_manifest(MANIFEST) is None
