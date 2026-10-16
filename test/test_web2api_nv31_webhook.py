"""Cửa webhook chung của lõi (NV31): POST /api/webhook/{module}/{ma}.

Mỗi test kèm đột biến (bảng trong nv31-bao-cao.md): đột biến tương ứng phải làm
test ĐỎ, mã thật XANH.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import gioi_han as gioi_han_mod  # noqa: E402
from app import kho, nhat_ky  # noqa: E402
from app import webhook as webhook_mod  # noqa: E402
from app.main import app  # noqa: E402

MODULE = "kenh-test"
KHOA_BI_MAT = "wh_secret"
BI_MAT_DUNG = "bi-mat-webhook-gia-0123456789abcdef"
BI_MAT_SAI = "bi-mat-sai-0123456789abcdef"

MANIFEST = {
    "id": MODULE,
    "tien_to": "kenh-test",
    "webhook": {"bi_mat": KHOA_BI_MAT, "tran_byte": 65536},
}


class ModGia:
    """Module giả có webhook."""

    def __init__(self):
        self.da_nhan: list[dict] = []
        self.ngu = 0.0

    async def webhook(self, yc: dict) -> dict:
        if self.ngu:
            await asyncio.sleep(self.ngu)
        self.da_nhan.append(yc)
        return {"ok": True, "lan": len(self.da_nhan)}

    def su_kien_id(self, yc: dict) -> str:
        return yc.get("id_su_kien") or ""


def _mod_moi():
    return ModGia()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", "khoa-qt-gia-nv31")
    for v in ("WEB2API_WEBHOOK_THAN_TOI_DA_MB", "WEB2API_WEBHOOK_TIN_PHUT",
              "WEB2API_WEBHOOK_DONG_THOI", "WEB2API_WEBHOOK_THOI_GIAN_GIAY",
              "WEB2API_RL_LOI_XAC_THUC", "WEB2API_TRUST_PROXY"):
        monkeypatch.delenv(v, raising=False)
    gioi_han_mod.khoi_dong()
    webhook_mod._dat_lai_trang_thai()
    with TestClient(app) as c:
        so = c.app.state.so
        mod = _mod_moi()
        so.dang_chay[MODULE] = mod
        so.catalog[MODULE] = SimpleNamespace(manifest=dict(MANIFEST))
        kho.ghi_bi_mat(MODULE, {KHOA_BI_MAT: BI_MAT_DUNG})
        yield c, mod, so
    so.dang_chay.pop(MODULE, None)
    so.catalog.pop(MODULE, None)
    webhook_mod._dat_lai_trang_thai()


def _post(c, module=MODULE, ma=BI_MAT_DUNG, json=None, data=None, headers=None):
    kw = {"headers": headers or {}}
    if json is not None:
        kw["json"] = json
    elif data is not None:
        kw["data"] = data
    return c.post(f"/api/webhook/{module}/{ma}", **kw)


def test_bi_mat_dung_200_va_module_nhan_dung(client):
    c, mod, so = client
    r = _post(c, json={"tin": "chao", "id_su_kien": "sk1"})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "lan": 1}
    assert len(mod.da_nhan) == 1
    yc = mod.da_nhan[0]
    assert yc["tin"] == "chao"
    assert yc["nguon_ngoai"] is True  # lõi đóng dấu, không phải chỉ thị


def test_sai_bi_mat_sai_ma_module_la_cung_mot_404(client):
    c, mod, so = client
    cac_r = [
        _post(c, ma=BI_MAT_SAI, json={"a": 1}),          # bí mật sai
        _post(c, module="module-khong-co", json={"a": 1}),  # module lạ
        _post(c, module="kenh-test", ma="x", json={"a": 1}),  # mã ngắn sai
    ]
    for r in cac_r:
        assert r.status_code == 404, r.text
        assert r.content == b""  # 404 trống, giống nhau từng byte
    assert cac_r[0].content == cac_r[1].content == cac_r[2].content
    assert len(mod.da_nhan) == 0  # module không bị gọi lần nào
    assert BI_MAT_DUNG.encode() not in cac_r[0].content


def test_thieu_cau_hinh_503_dong_khi_loi(client, tmp_path):
    c, mod, so = client
    # Xoá bí mật đã cấu hình → module khai webhook nhưng chưa có bí mật.
    (tmp_path / "bi_mat" / f"{MODULE}.json").write_text("{}")
    r = _post(c, json={"a": 1})
    assert r.status_code == 503, r.text
    assert BI_MAT_DUNG not in r.text
    assert len(mod.da_nhan) == 0


def test_than_vuot_tran_413_va_module_khong_bi_goi(client, monkeypatch):
    c, mod, so = client
    monkeypatch.setenv("WEB2API_WEBHOOK_THAN_TOI_DA_MB", "0.001")  # ~1KB
    r = _post(c, data="x" * 5000, headers={"Content-Type": "application/json"})
    assert r.status_code == 413, r.text
    assert len(mod.da_nhan) == 0


def test_phat_lai_cung_id_chi_goi_module_mot_lan(client):
    c, mod, so = client
    r1 = _post(c, json={"id_su_kien": "trung-nhau", "n": 1})
    r2 = _post(c, json={"id_su_kien": "trung-nhau", "n": 2})
    assert r1.status_code == 200 and r2.status_code == 200
    assert len(mod.da_nhan) == 1  # lần 2 không gọi module
    assert mod.da_nhan[0]["n"] == 1


def test_vuot_han_dong_429(client, monkeypatch):
    c, mod, so = client
    monkeypatch.setenv("WEB2API_WEBHOOK_TIN_PHUT", "2")
    for i in range(2):
        r = _post(c, json={"id_su_kien": f"hl{i}"})
        assert r.status_code == 200, r.text
    r = _post(c, json={"id_su_kien": "hl-vuot"})
    assert r.status_code == 429, r.text
    assert r.headers.get("retry-after")


def test_31_bi_mat_sai_khoa_ip(client, monkeypatch):
    c, mod, so = client
    monkeypatch.setattr(gioi_han_mod, "_lay_ip", lambda request: "192.0.2.99")
    for _ in range(31):
        r = _post(c, ma=BI_MAT_SAI, json={"a": 1})
        assert r.status_code == 404
    # Kể cả gửi đúng bí mật sau đó vẫn 429 vì IP đã bị khoá.
    r = _post(c, ma=BI_MAT_DUNG, json={"a": 1})
    assert r.status_code == 429, r.text
    assert r.headers.get("retry-after")
    assert BI_MAT_DUNG not in r.text


def test_chu_ky_hmac_dung_sai(client):
    c, mod, so = client
    so.catalog[MODULE].manifest["webhook"] = {
        "bi_mat": KHOA_BI_MAT, "chu_ky": "header:X-Chu-Ky", "thuat_toan": "hmac-sha256",
    }
    than = b'{"tin": "ky"}'
    ky_dung = hmac.new(BI_MAT_DUNG.encode(), than, hashlib.sha256).hexdigest()
    r = _post(c, data=than, headers={"Content-Type": "application/json",
                                     "X-Chu-Ky": ky_dung})
    assert r.status_code == 200, r.text
    r = _post(c, data=than, headers={"Content-Type": "application/json",
                                     "X-Chu-Ky": "0" * 64})
    assert r.status_code == 404 and r.content == b""
    r = _post(c, data=than, headers={"Content-Type": "application/json"})
    assert r.status_code == 404 and r.content == b""
    assert len(mod.da_nhan) == 1  # chỉ lần ký đúng được gọi


def test_khoa_khach_khong_thay_duoc_bi_mat(client):
    c, mod, so = client
    # Đường mở: có hay không có Bearer, thiếu bí mật đúng vẫn 404.
    r = _post(c, ma=BI_MAT_SAI, json={"a": 1},
              headers={"Authorization": "Bearer khoa-khach-gia"})
    assert r.status_code == 404 and r.content == b""
    assert len(mod.da_nhan) == 0


def test_nguon_ngoai_khong_bi_khach_ghi_de(client):
    c, mod, so = client
    r = _post(c, json={"nguon_ngoai": False, "id_su_kien": "nd1"})
    assert r.status_code == 200
    assert mod.da_nhan[0]["nguon_ngoai"] is True  # dấu của lõi thắng


def test_dong_thoi_qua_tran_429():
    # Kiểm đơn vị bộ đếm đồng thời (không cần HTTP đa luồng).
    with webhook_mod._GiuDongThoi("m-dt"):
        with webhook_mod._GiuDongThoi("m-dt"):
            pass
    import os
    os.environ["WEB2API_WEBHOOK_DONG_THOI"] = "1"
    try:
        with webhook_mod._GiuDongThoi("m-dt2"):
            with pytest.raises(Exception) as e:
                with webhook_mod._GiuDongThoi("m-dt2"):
                    pass
            assert getattr(e.value, "ma", None) == 429
    finally:
        del os.environ["WEB2API_WEBHOOK_DONG_THOI"]
        webhook_mod._dat_lai_trang_thai()


def test_module_het_gio_504(client, monkeypatch):
    c, mod, so = client
    mod.ngu = 5.0
    monkeypatch.setenv("WEB2API_WEBHOOK_THOI_GIAN_GIAY", "0.2")
    t0 = time.monotonic()
    r = _post(c, json={"id_su_kien": "hg1"})
    assert r.status_code == 504, r.text
    assert time.monotonic() - t0 < 4


def test_nhat_ky_khong_ghi_bi_mat_hay_noi_dung(client):
    c, mod, so = client
    _post(c, json={"tin": "bi-mat-noi-dung-gia", "id_su_kien": "nk1"})
    _post(c, ma=BI_MAT_SAI, json={"a": 1})
    dong = nhat_ky.doc(kenh="webhook", gioi_han=50)
    tat_ca = " ".join(str(d) for d in dong)
    assert BI_MAT_DUNG not in tat_ca
    assert "bi-mat-noi-dung-gia" not in tat_ca
