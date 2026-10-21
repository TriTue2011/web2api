"""NV221 — Có ≥ 2 provider thì PHẢI hiện combo (bước a: ĐO, rồi làm bước b).

ĐO (đã xác nhận bằng grep trước khi viết test):
- web/khung.js: DIEU_KIEN.combo có `chua_co: 'Combo chưa có backend ở bản này'`
  → hàm `thieu` luôn trả lý do → combo KHÔNG BAO GIỜ hiện trong menu, dù backend
  /api/combo có thật (app/tuyen.py, app/combo.py), NV196a/b có combo thật,
  NV216 đã đo combo chạy thật.
- web/He-thong-AI.dc.html: không có khối combo nào trong DOM.

LÀM (b): bỏ cờ `chua_co`; combo hiện khi ≥ 2 module AI đã LIÊN KẾT (đếm theo dữ
liệu thật qua DIEU_KIEN có sẵn: can=[ai], toi_thieu=2, pham_vi mặc định
'lien_ket' — không cờ cứng); tab Combo trong Hệ thống → AI: liệt kê combo,
tạo/xoá combo, thứ tự bậc, ô «Đang dùng #1», nút «Gợi ý combo mặc định»
(GET /api/combo/goi-y: Codex trước → web sau, cùng nhóm).
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

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

KHUNG_JS = Path(__file__).resolve().parent.parent / "web" / "khung.js"

NODE_KIEM_DIEU_KIEN = r"""
const fs = require('fs');
const src = fs.readFileSync(process.argv[1], 'utf8');
const window = {};
eval(src);
const K = window.Khung;
const ket_qua = {
  co_chua_co: 'chua_co' in K.DIEU_KIEN.combo,
  du_2: K.du('combo', { kha_nang_lien_ket: { ai: 2 }, kha_nang_da_cai: { ai: 2 } }),
  du_1: K.du('combo', { kha_nang_lien_ket: { ai: 1 }, kha_nang_da_cai: { ai: 1 } }),
};
console.log(JSON.stringify(ket_qua));
"""


def test_khung_js_combo_bo_co_chua_co():
    """ĐỎ trước sửa: DIEU_KIEN.combo còn `chua_co` → menu không bao giờ hiện combo."""
    r = subprocess.run(
        ["node", "-e", NODE_KIEM_DIEU_KIEN, str(KHUNG_JS)],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0, r.stderr
    kq = json.loads(r.stdout)
    assert kq["co_chua_co"] is False, "combo còn cờ chua_co — backend đã có thật"
    assert kq["du_2"] is True, "2 provider đã liên kết phải đủ điều kiện hiện combo"
    assert kq["du_1"] is False, "1 provider thì chưa đủ (toi_thieu=2)"


def mau_module_gia_co_nhom(id_: str, tien_to: str, nhom_id: str, nhom_ten: str):
    """Module giả như mau_module_gia nhưng manifest có thêm `nhom`."""
    tep = mau_module_gia(id_, tien_to)
    manifest = json.loads(tep["manifest.json"].decode("utf-8"))
    manifest["nhom"] = {"id": nhom_id, "ten": nhom_ten}
    tep["manifest.json"] = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
    return tep


@pytest.fixture
def moi_truong_221(tmp_path, monkeypatch):
    """Ba module giả đã liên kết: codex + web cùng nhóm chatgpt, một module nhóm grok."""
    data_dir = tmp_path / "data"
    (data_dir / "modules").mkdir(parents=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.delenv("WEB2API_COMBO_TOI_DA_GIAY", raising=False)
    dat_lai_trang_thai()
    ghi_thu_muc(data_dir / "modules" / "mx-codex",
                mau_module_gia_co_nhom("mx-codex", "mxcodex", "chatgpt", "ChatGPT"))
    ghi_thu_muc(data_dir / "modules" / "mx-web",
                mau_module_gia_co_nhom("mx-web", "mxweb", "chatgpt", "ChatGPT"))
    ghi_thu_muc(data_dir / "modules" / "mx-grok",
                mau_module_gia_co_nhom("mx-grok", "mxgrok", "grok", "Grok"))
    with TestClient(app) as client:
        so = SoModule()
        client.app.state.so = so
        asyncio.run(so.khoi_dong())
        for mid in ("mx-codex", "mx-web", "mx-grok"):
            assert client.post(f"/api/modules/{mid}/cai", json={"cau_hinh": {}},
                              headers=QT).status_code == 200, mid
        yield client, data_dir
    dat_lai_trang_thai()


def test_goi_y_combo_codex_truoc_web_sau_cung_nhom(moi_truong_221):
    """GET /api/combo/goi-y: Codex trước → web sau (cùng nhóm), nhóm theo nhom.id."""
    client, _ = moi_truong_221
    r = client.get("/api/combo/goi-y", headers=QT)
    assert r.status_code == 200, r.text
    goi_y = r.json()["goi_y"]
    models = [g["model"] for g in goi_y]
    assert models == ["mxcodex/m1", "mxweb/m1", "mxgrok/m1"], models
    assert goi_y[0]["la_codex"] is True
    assert goi_y[1]["la_codex"] is False


def test_tao_liet_ke_xoa_combo(moi_truong_221):
    """PUT /api/combo/{ten} → GET liệt kê thấy → DELETE xoá mất."""
    client, _ = moi_truong_221
    buoc = [{"model": "mxcodex/m1"}, {"model": "mxweb/m1"}]
    assert put(client, "cb221", buoc).status_code == 200
    ds = client.get("/api/combo", headers=QT).json()["combos"]
    ten_ds = [c["ten"] for c in ds]
    assert "cb221" in ten_ds
    cb = next(c for c in ds if c["ten"] == "cb221")
    assert [b["model"] for b in cb["buoc"]] == ["mxcodex/m1", "mxweb/m1"]
    assert client.delete("/api/combo/cb221", headers=QT).status_code == 200
    ds2 = client.get("/api/combo", headers=QT).json()["combos"]
    assert "cb221" not in [c["ten"] for c in ds2]


def test_chat_qua_model_combo_dung_bac_dau(moi_truong_221):
    """ĐO 'thử chat combo': POST /v1/chat/completions với model combo/<ten>
    đi qua lõi thật, gọi đúng bậc #1 trước."""
    client, data_dir = moi_truong_221
    assert put(client, "cb221", [{"model": "mxcodex/m1"}, {"model": "mxweb/m1"}]).status_code == 200
    r = chat(client, "combo/cb221", noi="thu-chat-combo")
    assert r.status_code == 200, r.text
    assert "tu-mxcodex" in r.text
    assert doc_goi(data_dir) == ["mxcodex:chat"]
