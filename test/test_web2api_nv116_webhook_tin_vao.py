"""NV116 (lõi) — module kênh qua cửa webhook đẩy TIN VÀO cổng kenh_vao.

Cửa webhook chỉ đưa THÂN JSON cho `module.webhook(yc)` (không có `app`), nên module không tự gọi được `kenh_vao.nhan_tin`. Hợp đồng:
`webhook()` trả dict có khoá `tin_vao` (danh sách tin chuẩn hoá) → lõi chuyển từng tin vào `kenh_vao.nhan_tin(app, module, tin)` rồi BỎ
khoá đó khỏi phản hồi (không phản chiếu nội dung tin). Chỉ chạy SAU khi cửa đã xác thực (bí mật + chữ ký + chống phát lại + hạn dòng).
"""
import asyncio

import pytest

from app import kenh_vao
from test_web2api_nv31_webhook import BI_MAT_SAI, MODULE, _post, client  # noqa: F401


def _tin(i):
    return {"id_tin": f"t{i}", "nguoi_gui": "u1", "noi_gui": "chat-1", "la_nhom": False, "ten_tu_khai": None, "van_ban": f"xin chao {i}", "luc": None}


@pytest.fixture
def ghi(monkeypatch):
    goi = []

    async def gia(app, module_id, tin):
        goi.append((module_id, tin))
        return {"ok": True}
    monkeypatch.setattr(kenh_vao, "nhan_tin", gia)
    return goi


def _mod_tra(mod, ket_qua):
    async def webhook(yc):
        mod.da_nhan.append(yc)
        return ket_qua
    mod.webhook = webhook


def test_tin_vao_duoc_chuyen_va_khong_lo_ra_phan_hoi(client, ghi):
    c, mod, so = client
    _mod_tra(mod, {"ok": True, "tin_vao": [_tin(1), _tin(2)]})
    r = _post(c, json={"x": 1})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True}, "khoá tin_vao phải bị bỏ khỏi phản hồi"
    assert [(m, t["id_tin"]) for m, t in ghi] == [(MODULE, "t1"), (MODULE, "t2")]


def test_toi_da_20_tin_va_bo_phan_tu_khong_phai_dict(client, ghi):
    c, mod, so = client
    _mod_tra(mod, {"ok": True, "tin_vao": [_tin(i) for i in range(30)] + ["rac", 5, None]})
    assert _post(c, json={"x": 1}).status_code == 200
    assert len(ghi) == 20


def test_tin_vao_sai_kieu_bi_bo_khong_vo_phan_hoi(client, ghi):
    c, mod, so = client
    _mod_tra(mod, {"ok": True, "tin_vao": "khong-phai-danh-sach"})
    r = _post(c, json={"x": 1})
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert ghi == []


def test_loi_trong_nhan_tin_khong_lam_hong_webhook(client, monkeypatch):
    c, mod, so = client

    async def hong(app, module_id, tin):
        raise RuntimeError("hong")
    monkeypatch.setattr(kenh_vao, "nhan_tin", hong)
    _mod_tra(mod, {"ok": True, "tin_vao": [_tin(1)]})
    r = _post(c, json={"x": 1})
    assert r.status_code == 200 and r.json() == {"ok": True}


def test_sai_bi_mat_thi_khong_chuyen_tin(client, ghi):
    c, mod, so = client
    _mod_tra(mod, {"ok": True, "tin_vao": [_tin(1)]})
    assert _post(c, ma=BI_MAT_SAI, json={"x": 1}).status_code == 404
    assert ghi == []
    assert mod.da_nhan == [], "module không được gọi khi sai bí mật"


def test_khong_co_khoa_tin_vao_thi_hanh_vi_cu(client, ghi):
    c, mod, so = client
    r = _post(c, json={"tin": "chao", "id_su_kien": "sk1"})
    assert r.status_code == 200 and r.json() == {"ok": True, "lan": 1}
    assert ghi == []
