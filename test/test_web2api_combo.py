"""Kiểm thử định tuyến dự phòng nhiều nguồn / model ở lõi (Combo).

Module giả có `id` KHÁC `tien_to` (như module thật: id «mod-a», tiền tố «pa») — combo phải suy module từ tiền tố
của model bằng ĐÚNG cơ chế của đường /v1 thường. Kịch bản phản hồi đặt trong `<DATA>/kich_ban.json` theo khoá
`<tiền tố>:<việc>` hoặc `<tiền tố>`; mỗi lượt module được gọi ghi một dòng vào `<DATA>/goi.jsonl` (đếm bước nào
đã thật sự bị gọi, theo thứ tự).

Chứng minh:
- Định tuyến bước theo tiền tố (id ≠ tiền tố) cho chat / ảnh / stream khớp gọi thẳng; hết bước → 503 JSON chuẩn.
- Lọc hợp lệ TRƯỚC: model tắt, bước tắt, module chưa liên kết, module thiếu phương thức không bao giờ bị gọi.
- Kiểm kiểu PUT; `/v1/models` không sập khi combo.json hỏng; `bo_qua` nêu lý do cấu trúc, không phản chiếu đầu vào.
- 400 phân xử giữa các bước (400→200 hạ bậc bước đầu chỉ trong nhóm việc đó; 400→400 trả 400 đầu, không thử
  bước ba); 413 / 404 / 501 / 429 / 5xx / 401 lùi đúng luật, chat và ảnh cùng một bộ phân loại.
- Cầu chì theo (module, nhóm việc): «hỏi» tách khỏi «chiếm lượt thăm dò»; 401 nghỉ ngay; luôn thử lựa chọn cuối.
- Stream: lùi trước byte đầu; sau byte đầu phát sự kiện lỗi chuẩn, không nuốt; aclose thượng nguồn tất định.
- Trần thời gian tổng (504 JSON). Khoá dính băm, độ dài cố định. `co_combo` đúng nghĩa. Tên có «\\n» bị từ chối.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import kho, phien, so_module
from app.combo import BoCauChi, BoDieuPhoiCombo, BoDinhPhien, bo_cau_chi, bo_dinh_phien, bo_han_muc, kiem_ten
from app.kham_pha import ID_HOP_LE, MA_KHA_NANG, TIEN_TO_HOP_LE
from app.loi_openai import LoiOpenAI
from app.main import app
from app.so_module import SoModule
from gia import ghi_thu_muc

KHOA_QT = "khoa-quan-tri-test-combo-0123456789ab"
QT = {"Authorization": f"Bearer {KHOA_QT}"}
KHOA_GIA_1 = "AIzaSyTOT-mot-000000000000000000000001"
KHOA_GIA_2 = "sk-khoa-tu-xa-RIENG-7777"

#: (id, tiền tố) — id KHÁC tiền tố như module thật.
MODULE_GIA = [("mod-a", "pa"), ("mod-b", "pb"), ("mod-c", "pc")]


def mau_module_gia(id_: str, tien_to: str, co_anh: bool = True) -> dict[str, bytes]:
    """manifest.json + module.py của một module giả điều khiển bằng kich_ban.json, ghi mỗi lượt gọi vào goi.jsonl."""
    manifest = {
        "id": id_,
        "ten": f"Module giả {id_}",
        "mo_ta": "Module giả để kiểm thử combo",
        "loai": "ai",
        "giao_thuc": "cuc_bo",
        "tien_to": tien_to,
        "cung_cap": ["chat", "anh"] if co_anh else ["chat"],
        "truong_cau_hinh": [],
        "tai_nguyen": {"ram_them_mb": None, "dia_mb": None, "gpu": False, "mang_ra": [], "ghi_chu": ""},
    }
    anh_code = '''
    async def anh(self, body):
        return await self._tra("anh", body, False)
''' if co_anh else ""
    code = f'''import asyncio
import json
import os
from pathlib import Path


def _du_lieu():
    return Path(os.environ["WEB2API_DATA"])


class Module:
    def __init__(self, manifest):
        self.manifest = manifest
        self.tien_to = manifest["tien_to"]

    def nap(self, cau_hinh):
        self.cau_hinh = cau_hinh

    async def cai(self, cau_hinh):
        return {{"ok": True}}

    async def _tra(self, viec, body, stream):
        with open(_du_lieu() / "goi.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({{"tien_to": self.tien_to, "viec": viec, "model": body.get("model"),
                                 "stream": bool(stream)}}) + "\\n")
        p = _du_lieu() / "kich_ban.json"
        kb = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {{}}
        cfg = kb.get(self.tien_to + ":" + viec) or kb.get(self.tien_to) or {{}}
        if cfg.get("cho_giay"):
            await asyncio.sleep(float(cfg["cho_giay"]))
        ma = int(cfg.get("ma", 200))
        headers = dict(cfg.get("headers") or {{"content-type": "application/json"}})
        noi_dung = cfg.get("noi_dung") or {{"id": "x-" + self.tien_to, "model": body.get("model"),
                                            "choices": [{{"message": {{"content": "tu-" + self.tien_to}}}}]}}
        kieu = cfg.get("kieu_stream")
        if stream and ma == 200:
            tien_to = self.tien_to

            async def gen():
                try:
                    if kieu == "loi_truoc_byte_dau":
                        raise RuntimeError("đứt trước byte đầu")
                    if kieu == "cham_truoc_byte_dau":
                        await asyncio.sleep(5)
                    yield ("data: chunk-1-" + tien_to + "\\n\\n").encode()
                    if kieu == "loi_sau_byte_dau":
                        raise RuntimeError("đứt sau byte đầu")
                    yield b"data: [DONE]\\n\\n"
                finally:
                    with open(_du_lieu() / "dong.jsonl", "a", encoding="utf-8") as f:
                        f.write(tien_to + "\\n")
            return 200, {{"content-type": "text/event-stream"}}, gen()
        return ma, headers, json.dumps(noi_dung).encode("utf-8")

    async def chat(self, body, stream=False):
        return await self._tra("chat", body, stream)
{anh_code}
    async def models(self):
        return [
            {{"id": self.tien_to + "/m1", "kha_nang": ["chat"]}},
            {{"id": self.tien_to + "/m2", "kha_nang": ["chat", "anh"]}},
        ]

    async def suc_khoe(self):
        return {{"ok": True}}

    async def dung(self):
        pass
'''
    return {
        "manifest.json": json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
        "module.py": code.encode("utf-8"),
    }


def dat_kich_ban(data_dir: Path, khoa: str, ma: int = 200, headers: dict | None = None,
                 noi_dung: Any = None, kieu_stream: str | None = None, cho_giay: float | None = None) -> None:
    """Đặt phản hồi cho `khoa` = "<tiền tố>" hoặc "<tiền tố>:<việc>"."""
    p = data_dir / "kich_ban.json"
    kb = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    muc: dict[str, Any] = {"ma": ma}
    if headers is not None:
        muc["headers"] = headers
    if noi_dung is not None:
        muc["noi_dung"] = noi_dung
    if kieu_stream is not None:
        muc["kieu_stream"] = kieu_stream
    if cho_giay is not None:
        muc["cho_giay"] = cho_giay
    kb[khoa] = muc
    p.write_text(json.dumps(kb, ensure_ascii=False), encoding="utf-8")


def doc_goi(data_dir: Path) -> list[str]:
    """Danh sách "<tiền tố>:<việc>" theo thứ tự module bị gọi."""
    p = data_dir / "goi.jsonl"
    if not p.exists():
        return []
    return [f"{d['tien_to']}:{d['viec']}" for d in map(json.loads, p.read_text(encoding="utf-8").splitlines())]


def xoa_goi(data_dir: Path) -> None:
    (data_dir / "goi.jsonl").write_text("", encoding="utf-8")


def dat_lai_trang_thai() -> None:
    bo_cau_chi._states.clear()
    bo_han_muc._nghi.clear()
    bo_dinh_phien._map.clear()


def put(client, ten: str, buoc: list, **them) -> Any:
    return client.put(f"/api/combo/{ten}", json={"buoc": buoc, **them}, headers=QT)


def chat(client, model: str, noi: str = "hi", headers: dict | None = None, **them):
    return client.post("/v1/chat/completions", json={"model": model, "messages": [{"role": "user", "content": noi}],
                                                      **them}, headers=headers or QT)


def anh(client, model: str, headers: dict | None = None, **them):
    return client.post("/v1/images/generations", json={"model": model, "prompt": "mèo", **them},
                       headers=headers or QT)


@pytest.fixture
def moi_truong(tmp_path, monkeypatch):
    """Ba module chat+ảnh (pa, pb, pc), một module chỉ chat (pd), một module đã cài mà CHƯA liên kết (pe)."""
    data_dir = tmp_path / "data"
    (data_dir / "modules").mkdir(parents=True)
    anh_dir = tmp_path / "anh"
    anh_dir.mkdir()
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setattr(so_module, "THU_MUC_ANH", anh_dir)
    monkeypatch.setenv("WEB2API_COMBO_NGUONG", "3")
    monkeypatch.setenv("WEB2API_COMBO_NGHI_GIAY", "2")
    monkeypatch.setenv("WEB2API_COMBO_DINH_GIAY", "60")
    monkeypatch.delenv("WEB2API_COMBO_TOI_DA_GIAY", raising=False)
    dat_lai_trang_thai()

    for id_, tien_to in MODULE_GIA:
        ghi_thu_muc(data_dir / "modules" / id_, mau_module_gia(id_, tien_to))
    ghi_thu_muc(data_dir / "modules" / "mod-d", mau_module_gia("mod-d", "pd", co_anh=False))
    ghi_thu_muc(data_dir / "modules" / "mod-e", mau_module_gia("mod-e", "pe"))

    with TestClient(app) as client:
        so = SoModule()
        client.app.state.so = so
        asyncio.run(so.khoi_dong())
        for id_ in ("mod-a", "mod-b", "mod-c", "mod-d"):
            assert client.post(f"/api/modules/{id_}/cai", json={"cau_hinh": {}}, headers=QT).status_code == 200
        assert "mod-e" in so.catalog and "mod-e" not in so.dang_chay
        yield client, data_dir, so
    dat_lai_trang_thai()


# ───────────── lưu trữ + kiểm kiểu PUT ─────────────

@pytest.mark.parametrize("ten", ["Ten_Hoa", "co:gach", "combo", "trang-thai", "pa", "a", "a/b"])
def test_put_ten_khong_hop_le(moi_truong, ten):
    client, _, _ = moi_truong
    r = put(client, ten, [{"model": "pa/m1"}])
    assert r.status_code == 400, (ten, r.text)
    assert r.json()["error"]["type"] == "invalid_request_error"


@pytest.mark.parametrize("than", [
    {"buoc": []},
    {"buoc": "pa/m1"},
    {"buoc": [{"model": "pa/m1"}] * 21},
    {"buoc": ["pa/m1"]},
    {"buoc": [{"model": ""}]},
    {"buoc": [{"model": 5}]},
    {"buoc": [{"model": "khong-gach"}]},
    {"buoc": [{"model": "pa/m1\n"}]},
    {"buoc": [{"model": "pa/m1 x"}]},
    {"buoc": [{"model": "combo/khac"}]},
    {"buoc": [{"model": "pa/m1", "bat": "false"}]},
    {"buoc": [{"model": "pa/m1", "bat": 0}]},
    {"buoc": [{"model": "pa/m1"}], "kha_nang": [1, "a"]},
    {"buoc": [{"model": "pa/m1"}], "kha_nang": "chat"},
    {"buoc": [{"model": "pa/m1"}], "kha_nang": ["Chat"]},
    {"buoc": [{"model": "pa/m1"}], "mo_ta": 5},
])
def test_put_than_sai_kieu_tra_400_va_khong_luu(moi_truong, than):
    client, data_dir, _ = moi_truong
    r = client.put("/api/combo/cb", json=than, headers=QT)
    assert r.status_code == 400, (than, r.text)
    assert r.json()["error"]["type"] == "invalid_request_error"
    assert not (data_dir / "combo.json").exists()
    assert client.get("/v1/models", headers=QT).status_code == 200


def test_crud_combo_bo_qua_va_quyen_tep(moi_truong):
    client, data_dir, _ = moi_truong
    r = put(client, "cb_test", [{"model": "pa/m1"}, {"model": "zzz/none", "bat": True},
                                {"model": "pb/khong-co"}, {"model": "pe/m1"}], mo_ta="Combo thử")
    assert r.status_code == 200, r.text
    j = r.json()
    # Bước lạ vẫn được lưu (module có thể cài sau) nhưng nêu lý do CẤU TRÚC, không phản chiếu giá trị đầu vào.
    assert [b["model"] for b in j["combo"]["buoc"]] == ["pa/m1", "zzz/none", "pb/khong-co", "pe/m1"]
    assert j["bo_qua"] == [{"buoc": 2, "ly_do": "model_not_found"}, {"buoc": 3, "ly_do": "model_khong_co"},
                           {"buoc": 4, "ly_do": "module_not_configured"}]
    assert "zzz" not in json.dumps(j["bo_qua"]) and "khong-co" not in json.dumps(j["bo_qua"])
    assert (data_dir / "combo.json").stat().st_mode & 0o777 == 0o600

    assert [c["ten"] for c in client.get("/api/combo", headers=QT).json()["combos"]] == ["cb_test"]
    assert client.get("/api/combo/cb_test", headers=QT).json()["mo_ta"] == "Combo thử"
    tt = client.get("/api/combo/trang-thai", headers=QT).json()["trang_thai"]
    assert tt[0]["ten"] == "cb_test" and len(tt[0]["buoc"]) == 4
    # «\n» cuối tên: đường URL thì Starlette đã cắt trước (mẫu route `^…$`), nên kiểm ở hàm kiểm tên và qua `model`.
    with pytest.raises(LoiOpenAI):
        kiem_ten("cb_test\n")
    r = chat(client, "combo/cb_test\n")
    assert r.status_code == 404 and "x-web2api-combo" not in r.headers
    assert client.delete("/api/combo/cb_test", headers=QT).json() == {"ok": True}
    assert client.get("/api/combo/cb_test", headers=QT).status_code == 404


@pytest.mark.parametrize("noi_dung", [
    json.dumps({"hong1": {"kha_nang": [1, "a"], "buoc": [{"model": "pa/m1"}]},
                "hong2": 5,
                "hong3": {"buoc": "x"},
                "hong4": {"buoc": [{"model": "pa/m1", "bat": "false"}]},
                "Hoa": {"buoc": [{"model": "pa/m1"}]},
                "ab\n": {"buoc": [{"model": "pa/m1"}]},
                "tot": {"buoc": [{"model": "pa/m1", "bat": True}]}}),
    "[1, 2]",
    "{không phải json",
])
def test_combo_json_hong_khong_lam_sap(moi_truong, noi_dung):
    client, data_dir, _ = moi_truong
    (data_dir / "combo.json").write_text(noi_dung, encoding="utf-8")
    r = client.get("/v1/models", headers=QT)
    assert r.status_code == 200
    ids = {m["id"] for m in r.json()["data"]}
    assert not any(i.startswith("combo/") and i != "combo/tot" for i in ids)
    assert ("combo/tot" in ids) == ("tot" in noi_dung)
    assert client.get("/api/combo/trang-thai", headers=QT).status_code == 200
    assert client.get("/api/combo", headers=QT).status_code == 200
    assert client.get("/api/tinh-nang", headers=QT).json()["co_combo"] is ("tot" in noi_dung)
    assert chat(client, "combo/hong1").status_code == 404
    if "tot" in noi_dung:
        assert chat(client, "combo/tot").headers["x-web2api-route"] == "pa/m1"


# ───────────── định tuyến bước theo tiền tố (id ≠ tiền tố) ─────────────

def test_dinh_tuyen_id_khac_tien_to_khop_goi_thang(moi_truong):
    client, data_dir, _ = moi_truong
    assert put(client, "cb", [{"model": "pa/m2"}, {"model": "pb/m2"}]).status_code == 200

    thang = chat(client, "pa/m2")
    qua_combo = chat(client, "combo/cb", noi="khac")
    assert thang.status_code == qua_combo.status_code == 200
    assert qua_combo.json() == thang.json()
    assert qua_combo.headers["x-web2api-route"] == "pa/m2"
    assert qua_combo.headers["x-web2api-combo"] == "cb"
    assert qua_combo.headers["x-web2api-module"] == thang.headers["x-web2api-module"] == "pa"

    a_thang, a_combo = anh(client, "pa/m2"), anh(client, "combo/cb")
    assert a_thang.status_code == a_combo.status_code == 200
    assert a_combo.json() == a_thang.json()
    assert a_combo.headers["x-web2api-route"] == "pa/m2"

    s_thang = chat(client, "pa/m2", noi="s1", stream=True)
    s_combo = chat(client, "combo/cb", noi="s2", stream=True)
    assert s_thang.status_code == s_combo.status_code == 200
    assert s_combo.text == s_thang.text and "chunk-1-pa" in s_combo.text
    assert s_combo.headers["x-web2api-route"] == "pa/m2"
    assert doc_goi(data_dir) == ["pa:chat", "pa:chat", "pa:anh", "pa:anh", "pa:chat", "pa:chat"]


def test_het_buoc_tra_503_json_chuan(moi_truong):
    client, data_dir, _ = moi_truong
    assert put(client, "cb", [{"model": "zzz/a"}, {"model": "yyy/b"}]).status_code == 200
    for r in (chat(client, "combo/cb"), chat(client, "combo/cb", stream=True), anh(client, "combo/cb")):
        assert r.status_code == 503
        assert r.headers["content-type"].startswith("application/json")
        e = r.json()["error"]
        assert e["code"] == "no_available_routes" and e["type"] == "api_error" and e["message"]
        assert r.headers["x-web2api-combo"] == "cb"
    assert doc_goi(data_dir) == []


@pytest.mark.parametrize("nguyen_nhan", ["tat", "bat_false", "module_chua_lien_ket", "khong_phuong_thuc"])
def test_buoc_khong_hop_le_khong_bao_gio_duoc_goi(moi_truong, nguyen_nhan):
    """Luật «không chặn lựa chọn cuối» KHÔNG vượt qua bước không hợp lệ."""
    client, data_dir, _ = moi_truong
    goi = chat
    if nguyen_nhan == "tat":
        buoc = [{"model": "pa/m2"}, {"model": "pb/m2"}]
        kho.luu_cai_dat_model_module("mod-a", ["pa/m2"], {})
        kho.luu_cai_dat_model_module("mod-b", ["pb/m2"], {})
        assert chat(client, "pa/m2").status_code == 404  # gọi thẳng
    elif nguyen_nhan == "bat_false":
        buoc = [{"model": "pa/m2", "bat": False}, {"model": "pb/m2", "bat": False}]
    elif nguyen_nhan == "module_chua_lien_ket":
        buoc = [{"model": "pe/m1"}]
        assert chat(client, "pe/m1").json()["error"]["code"] == "module_not_configured"
    else:
        buoc = [{"model": "pd/m1"}]
        goi = anh
        assert anh(client, "pd/m1").status_code == 501  # gọi thẳng
    assert put(client, "cb", buoc).status_code == 200
    r = goi(client, "combo/cb")
    assert r.status_code == 503, r.text
    assert r.json()["error"]["code"] == "no_available_routes"
    assert doc_goi(data_dir) == []


def test_khach_chi_chat_khong_dung_duoc_buoc_anh(moi_truong):
    client, _, _ = moi_truong
    assert put(client, "cb", [{"model": "pa/m2"}]).status_code == 200
    h = {"Authorization": "Bearer " + client.post("/api/khoa-khach", json={"ten": "k-chat", "pham_vi": ["chat"]},
                                                  headers=QT).json()["khoa"]["khoa"]}
    h_anh = {"Authorization": "Bearer " + client.post("/api/khoa-khach", json={"ten": "k-anh", "pham_vi": ["anh"]},
                                                      headers=QT).json()["khoa"]["khoa"]}
    assert chat(client, "combo/cb", headers=h).status_code == 200
    assert anh(client, "combo/cb", headers=h).status_code == 403
    assert chat(client, "combo/cb", headers=h_anh).status_code == 403
    assert anh(client, "combo/cb", headers=h_anh).status_code == 200
    # Khách không vào được đường quản trị combo.
    assert client.get("/api/combo", headers=h).status_code == 403
    assert client.put("/api/combo/x", json={"buoc": [{"model": "pa/m1"}]}, headers=h).status_code == 403
    assert client.get("/api/combo").status_code == 401


def test_ten_tran_va_module_mang_tien_to_combo(moi_truong):
    """Tên trần trùng tên combo → combo; module mang tiền tố «combo» thắng `combo/<…>` (module được ưu tiên)."""
    client, data_dir, so = moi_truong
    assert put(client, "m1", [{"model": "pb/m1"}]).status_code == 200
    assert chat(client, "m1").headers["x-web2api-route"] == "pb/m1"
    assert chat(client, "combo/m1").headers["x-web2api-route"] == "pb/m1"

    ghi_thu_muc(data_dir / "modules" / "mod-cb", mau_module_gia("mod-cb", "combo"))
    assert client.post("/api/modules/nap-lai", headers=QT).status_code == 200
    assert client.post("/api/modules/mod-cb/cai", json={"cau_hinh": {}}, headers=QT).status_code == 200
    r = chat(client, "combo/m1")
    assert r.status_code == 200 and "x-web2api-route" not in r.headers
    assert r.headers["x-web2api-module"] == "combo"
    assert chat(client, "m1").headers["x-web2api-route"] == "pb/m1"
    # Không tạo được combo trùng tiền tố đang có.
    assert put(client, "pb", [{"model": "pa/m1"}]).status_code == 400


# ───────────── quy tắc lùi theo mã ─────────────

def test_400_phan_xu_giua_cac_buoc(moi_truong):
    client, data_dir, _ = moi_truong
    assert put(client, "cb", [{"model": "pa/m2"}, {"model": "pb/m2"}, {"model": "pc/m2"}]).status_code == 200
    loi_a = {"error": {"message": "loi-cua-pa", "type": "invalid_request_error", "code": "x"}}

    # 400 → 200: trả 200; bước đầu bị hạ bậc CHỈ trong nhóm việc chat.
    dat_kich_ban(data_dir, "pa", 400, noi_dung=loi_a)
    r = chat(client, "combo/cb", noi="q1")
    assert r.status_code == 200 and r.headers["x-web2api-route"] == "pb/m2"
    assert doc_goi(data_dir) == ["pa:chat", "pb:chat"]
    dat_kich_ban(data_dir, "pa", 200)
    xoa_goi(data_dir)
    assert chat(client, "combo/cb", noi="q2").headers["x-web2api-route"] == "pb/m2"
    assert doc_goi(data_dir) == ["pb:chat"]
    assert anh(client, "combo/cb").headers["x-web2api-route"] == "pa/m2"  # nhóm việc khác không bị phạt

    # 400 → 400: trả 400 NGUYÊN VĂN của bước đầu, KHÔNG thử bước thứ ba.
    dat_lai_trang_thai()
    xoa_goi(data_dir)
    dat_kich_ban(data_dir, "pa", 400, noi_dung=loi_a)
    dat_kich_ban(data_dir, "pb", 400, noi_dung={"error": {"message": "loi-cua-pb"}})
    r = chat(client, "combo/cb", noi="q3")
    assert r.status_code == 400 and r.json() == loi_a
    assert r.headers["x-web2api-route"] == "pa/m2" and r.headers["x-web2api-module"] == "pa"
    assert doc_goi(data_dir) == ["pa:chat", "pb:chat"]
    assert bo_han_muc.con_lai_giay("mod-a", "pa/m2", "chat") == 0  # lỗi của yêu cầu: không ai bị hạ

    # 400 → 5xx: phép so không kết luận, lùi bình thường; bước ba trả 200.
    dat_lai_trang_thai()
    dat_kich_ban(data_dir, "pb", 500)
    assert chat(client, "combo/cb", noi="q4").headers["x-web2api-route"] == "pc/m2"
    # 400 → 5xx → 5xx: hết bước → trả 400 đầu tiên (thông tin nhất cho khách).
    dat_lai_trang_thai()
    dat_kich_ban(data_dir, "pc", 503)
    r = chat(client, "combo/cb", noi="q5")
    assert r.status_code == 400 and r.json() == loi_a

    # Stream: 400 trước byte đầu cũng được phân xử.
    dat_lai_trang_thai()
    dat_kich_ban(data_dir, "pb", 200)
    r = chat(client, "combo/cb", noi="q6", stream=True)
    assert r.status_code == 200 and r.headers["x-web2api-route"] == "pb/m2" and "chunk-1-pb" in r.text

    # Ảnh: cùng luật.
    dat_lai_trang_thai()
    r = anh(client, "combo/cb")
    assert r.status_code == 200 and r.headers["x-web2api-route"] == "pb/m2"


def test_413_404_501_lui_khong_tinh_hong_ca_chat_va_anh(moi_truong):
    client, data_dir, _ = moi_truong
    assert put(client, "cb", [{"model": "pa/m2"}, {"model": "pb/m2"}]).status_code == 200
    for ma in (413, 404, 501):
        dat_lai_trang_thai()
        dat_kich_ban(data_dir, "pa", ma)
        assert chat(client, "combo/cb", noi=f"c{ma}").headers["x-web2api-route"] == "pb/m2"
        assert anh(client, "combo/cb").headers["x-web2api-route"] == "pb/m2"
        assert bo_cau_chi.get_stats("mod-a", "chat")["consecutive_failures"] == 0
        assert bo_cau_chi.get_stats("mod-a", "anh")["consecutive_failures"] == 0


def test_422_tra_ngay(moi_truong):
    client, data_dir, _ = moi_truong
    assert put(client, "cb", [{"model": "pa/m2"}, {"model": "pb/m2"}]).status_code == 200
    dat_kich_ban(data_dir, "pa", 422)
    for r in (chat(client, "combo/cb"), anh(client, "combo/cb")):
        assert r.status_code == 422 and r.headers["x-web2api-route"] == "pa/m2"
    assert doc_goi(data_dir) == ["pa:chat", "pa:anh"]


def test_429_ha_xuong_cuoi_roi_hoi_ve_cho_cu(moi_truong):
    client, data_dir, _ = moi_truong
    assert put(client, "cb", [{"model": "pa/m1"}, {"model": "pb/m1"}]).status_code == 200
    dat_kich_ban(data_dir, "pa", 429, headers={"retry-after": "1", "content-type": "application/json"})
    assert chat(client, "combo/cb", noi="r1").headers["x-web2api-route"] == "pb/m1"
    dat_kich_ban(data_dir, "pa", 200)
    xoa_goi(data_dir)
    assert chat(client, "combo/cb", noi="r2").headers["x-web2api-route"] == "pb/m1"
    assert doc_goi(data_dir) == ["pb:chat"]
    time.sleep(1.1)
    assert chat(client, "combo/cb", noi="r3").headers["x-web2api-route"] == "pa/m1"  # hồi về CHỖ CŨ


def test_401_lui_ngay_va_nghi_ca_nhom_viec(moi_truong):
    client, data_dir, _ = moi_truong
    assert put(client, "cb", [{"model": "pa/m1"}, {"model": "pa/m2"}, {"model": "pb/m2"}]).status_code == 200
    dat_kich_ban(data_dir, "pa:chat", 401, noi_dung={"error": {"message": "khoá hỏng"}})
    r = chat(client, "combo/cb", noi="k1")
    assert r.status_code == 200 and r.headers["x-web2api-route"] == "pb/m2"
    # 401 → (module, chat) nghỉ NGAY (không chờ 3 lỗi): bước pa/m2 cùng module cũng không bị gọi.
    assert doc_goi(data_dir) == ["pa:chat", "pb:chat"]
    assert bo_cau_chi.get_stats("mod-a", "chat")["trang_thai"] == "mo"
    xoa_goi(data_dir)
    assert chat(client, "combo/cb", noi="k2").headers["x-web2api-route"] == "pb/m2"
    assert doc_goi(data_dir) == ["pb:chat"]
    # Nhóm việc khác của cùng module không bị phạt (định tuyến như gọi thẳng: bước đầu pa/m1 có phương thức anh).
    assert anh(client, "combo/cb").headers["x-web2api-route"] == "pa/m1"
    tt = client.get("/api/combo/trang-thai", headers=QT).json()["trang_thai"][0]["buoc"][0]
    assert tt["cau_chi"]["chat"]["trang_thai"] == "mo" and tt["cau_chi"]["chat"]["ma_loi_cuoi"] == 401


def test_5xx_lui_va_moi_buoc_hong_tra_loi_cuoi_da_che(moi_truong):
    client, data_dir, _ = moi_truong
    assert put(client, "cb", [{"model": "pa/m2"}, {"model": "pb/m2"}]).status_code == 200
    dat_kich_ban(data_dir, "pa", 507)
    assert chat(client, "combo/cb", noi="a").headers["x-web2api-route"] == "pb/m2"
    assert anh(client, "combo/cb").headers["x-web2api-route"] == "pb/m2"
    dat_kich_ban(data_dir, "pa", 500, noi_dung={"error": {"message": f"khoá {KHOA_GIA_1}"}})
    dat_kich_ban(data_dir, "pb", 503, noi_dung={"error": {"message": f"khoá {KHOA_GIA_2}"}})
    r = chat(client, "combo/cb", noi="b")
    assert r.status_code == 503 and r.headers["x-web2api-route"] == "pb/m2"
    assert KHOA_GIA_2 not in r.text and "…" in r.text


# ───────────── cầu chì ─────────────

def test_cau_chi_hoi_khong_chiem_luot():
    c = BoCauChi()
    for _ in range(3):
        c.record_failure("m", "chat", 500, "x")
    assert c.trang_thai("m", "chat") == "mo"
    assert c.chiem("m", "chat") is None
    c._states[("m", "chat")]["opened_at"] -= 100
    # Hỏi bao nhiêu lần cũng không chiếm lượt thăm dò.
    assert c.trang_thai("m", "chat") == "cho_tham_do"
    assert c.trang_thai("m", "chat") == "cho_tham_do"
    assert c.chiem("m", "chat") == "tham_do"
    assert c.trang_thai("m", "chat") == "dang_tham_do"
    assert c.chiem("m", "chat") is None  # chỉ MỘT thăm dò
    c.tha("m", "chat")
    assert c.chiem("m", "chat") == "tham_do"
    c._states[("m", "chat")]["probe_started_at"] -= 1000  # bẫy B3: thăm dò treo không giữ cờ mãi
    assert c.trang_thai("m", "chat") == "cho_tham_do"
    assert c.chiem("m", "chat") == "tham_do"
    c.record_success("m", "chat")
    assert c.trang_thai("m", "chat") == "dong" and c.chiem("m", "chat") == "dong"
    c.mo_ngay("m", "chat", 401, "khoá")
    assert c.trang_thai("m", "chat") == "mo"
    assert c.trang_thai("m", "anh") == "dong"


def test_cau_chi_mo_sau_nguong_va_luon_thu_lua_chon_cuoi(moi_truong):
    client, data_dir, _ = moi_truong
    assert put(client, "cb", [{"model": "pa/m1"}, {"model": "pb/m1"}]).status_code == 200
    dat_kich_ban(data_dir, "pa", 500)
    for i in range(3):
        assert chat(client, "combo/cb", noi=f"f{i}").headers["x-web2api-route"] == "pb/m1"
    assert bo_cau_chi.get_stats("mod-a", "chat")["trang_thai"] == "mo"
    xoa_goi(data_dir)
    chat(client, "combo/cb", noi="f4")
    assert doc_goi(data_dir) == ["pb:chat"]

    # Cả hai cầu chì mở: vẫn thử theo thứ tự gốc, không chặn lựa chọn cuối.
    bo_cau_chi.mo_ngay("mod-b", "chat", 500, "x")
    xoa_goi(data_dir)
    r = chat(client, "combo/cb", noi="f5")
    assert r.status_code == 200 and r.headers["x-web2api-route"] == "pb/m1"
    assert doc_goi(data_dir) == ["pa:chat", "pb:chat"]

    # Hết nghỉ: một thăm dò thành công đóng cầu chì.
    bo_cau_chi._states[("mod-a", "chat")]["opened_at"] -= 100
    dat_kich_ban(data_dir, "pa", 200)
    assert chat(client, "combo/cb", noi="f6").headers["x-web2api-route"] == "pa/m1"
    assert bo_cau_chi.get_stats("mod-a", "chat")["trang_thai"] == "dong"


# ───────────── stream ─────────────

def test_stream_truoc_va_sau_byte_dau(moi_truong):
    client, data_dir, _ = moi_truong
    assert put(client, "cb", [{"model": "pa/m1"}, {"model": "pb/m1"}]).status_code == 200
    dat_kich_ban(data_dir, "pa", 200, kieu_stream="loi_truoc_byte_dau")
    r = chat(client, "combo/cb", noi="s1", stream=True)
    assert r.status_code == 200 and r.headers["x-web2api-route"] == "pb/m1" and "chunk-1-pb" in r.text

    dat_lai_trang_thai()
    xoa_goi(data_dir)
    dat_kich_ban(data_dir, "pa", 200, kieu_stream="loi_sau_byte_dau")
    r = chat(client, "combo/cb", noi="s2", stream=True)
    assert r.status_code == 200 and r.headers["x-web2api-route"] == "pa/m1"
    assert "chunk-1-pa" in r.text
    # Lỗi sau byte đầu KHÔNG bị nuốt: sự kiện lỗi SSE chuẩn, rồi kết thúc; không đổi bước.
    su_kien = [json.loads(d[len("data: "):]) for d in r.text.split("\n\n") if d.startswith("data: {")]
    assert su_kien and su_kien[-1]["error"]["code"] == "stream_interrupted"
    assert su_kien[-1]["error"]["type"] == "api_error"
    assert doc_goi(data_dir) == ["pa:chat"]
    assert bo_cau_chi.get_stats("mod-a", "chat")["consecutive_failures"] == 1


class _ItDem:
    """Iterator bất đồng bộ đếm số lần aclose; `loi_dau` = hỏng ngay ở khúc đầu."""

    def __init__(self, loi_dau: bool = False, cham: float = 0.0):
        self.dong = 0
        self.loi_dau = loi_dau
        self.cham = cham
        self.i = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.cham:
            await asyncio.sleep(self.cham)
        if self.loi_dau:
            raise RuntimeError("hỏng")
        self.i += 1
        if self.i > 3:
            raise StopAsyncIteration
        return f"data: k{self.i}\n\n".encode()

    async def aclose(self):
        self.dong += 1


class _ModGia:
    def __init__(self, ma: int, it: _ItDem):
        self.ma, self.it = ma, it

    async def chat(self, body, stream=False):
        return self.ma, {"content-type": "text/event-stream"}, self.it


def _runner(cac: dict[str, _ModGia]) -> BoDieuPhoiCombo:
    def phan_giai(model: str):
        tien_to = model.partition("/")[0]
        if tien_to not in cac:
            raise LoiOpenAI(404, "không có", code="model_not_found")
        return "id-" + tien_to, cac[tien_to]
    combo = {"ten": "cb", "buoc": [{"model": f"{t}/m", "bat": True} for t in cac]}
    return BoDieuPhoiCombo("cb", combo, None, None, phan_giai=phan_giai)


def test_aclose_thuong_nguon_tat_dinh(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    dat_lai_trang_thai()
    hong, loi_ma, tot = _ItDem(loi_dau=True), _ItDem(), _ItDem()
    runner = _runner({"qa": _ModGia(200, hong), "qb": _ModGia(500, loi_ma), "qc": _ModGia(200, tot)})

    async def chay():
        ma, headers, it = await runner.chat({"messages": [{"role": "user", "content": "x"}]}, True)
        assert ma == 200 and headers["x-web2api-route"] == "qc/m"
        # Bước bị bỏ (hỏng trước byte đầu / trả mã lỗi kèm luồng) được đóng NGAY, không chờ GC.
        assert hong.dong == 1 and loi_ma.dong == 1
        assert await it.__anext__() == b"data: k1\n\n"
        assert tot.dong == 0
        await it.aclose()  # khách ngắt giữa chừng
        assert tot.dong == 1
    asyncio.run(chay())
    dat_lai_trang_thai()


def test_khach_ngat_khi_cho_byte_dau_dong_thuong_nguon_ngay(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    dat_lai_trang_thai()
    cham = _ItDem(cham=10)
    runner = _runner({"qa": _ModGia(200, cham)})

    async def chay():
        viec = asyncio.ensure_future(runner.chat({"messages": [{"role": "user", "content": "x"}]}, True))
        await asyncio.sleep(0.05)
        viec.cancel()
        with pytest.raises(asyncio.CancelledError):
            await viec
        assert cham.dong == 1
        assert bo_cau_chi.trang_thai("id-qa", "chat") == "dong"
    asyncio.run(chay())
    dat_lai_trang_thai()


def test_tran_thoi_gian_tong(moi_truong, monkeypatch):
    client, data_dir, _ = moi_truong
    monkeypatch.setenv("WEB2API_COMBO_TOI_DA_GIAY", "0.5")
    assert put(client, "cb", [{"model": "pa/m1"}, {"model": "pb/m1"}]).status_code == 200
    dat_kich_ban(data_dir, "pa", 200, cho_giay=5)
    bat_dau = time.monotonic()
    r = chat(client, "combo/cb", noi="t1")
    assert time.monotonic() - bat_dau < 3
    assert r.status_code == 504 and r.json()["error"]["code"] == "combo_timeout"
    assert r.json()["error"]["type"] == "api_error"
    assert doc_goi(data_dir) == ["pa:chat"]  # luôn thử ít nhất một bước

    dat_lai_trang_thai()
    dat_kich_ban(data_dir, "pa", 200, kieu_stream="cham_truoc_byte_dau")
    bat_dau = time.monotonic()
    r = chat(client, "combo/cb", noi="t2", stream=True)
    assert time.monotonic() - bat_dau < 3
    assert r.status_code == 504 and r.json()["error"]["code"] == "combo_timeout"


# ───────────── dính tài khoản ─────────────

def test_dinh_tai_khoan(moi_truong):
    client, data_dir, _ = moi_truong
    assert put(client, "cb", [{"model": "pa/m1"}, {"model": "pb/m1"}]).status_code == 200
    assert chat(client, "combo/cb", noi="h1", user="alice").headers["x-web2api-route"] == "pa/m1"
    assert put(client, "cb", [{"model": "pb/m1"}, {"model": "pa/m1"}]).status_code == 200
    assert chat(client, "combo/cb", noi="h2", user="alice").headers["x-web2api-route"] == "pa/m1"
    assert chat(client, "combo/cb", noi="h3", user="bob").headers["x-web2api-route"] == "pb/m1"
    dat_kich_ban(data_dir, "pa", 500)
    assert chat(client, "combo/cb", noi="h4", user="alice").headers["x-web2api-route"] == "pb/m1"
    # Ảnh cũng dính.
    assert put(client, "ca", [{"model": "pc/m2"}, {"model": "pb/m2"}]).status_code == 200
    assert anh(client, "combo/ca", user="carol").headers["x-web2api-route"] == "pc/m2"
    assert put(client, "ca", [{"model": "pb/m2"}, {"model": "pc/m2"}]).status_code == 200
    assert anh(client, "combo/ca", user="carol").headers["x-web2api-route"] == "pc/m2"


def test_khoa_dinh_bam_va_co_tran():
    rat_dai = "x" * 5_000_000
    k = BoDinhPhien.lay_khoa_phien({"user": rat_dai})
    assert k is not None and len(k) <= 70 and "xxxx" not in k
    assert len(BoDinhPhien.lay_khoa_phien({}, rat_dai)) <= 70
    assert BoDinhPhien.lay_khoa_phien({"user": "alice"}) == BoDinhPhien.lay_khoa_phien({"user": "alice"})
    assert BoDinhPhien.lay_khoa_phien({"user": "alice"}) != BoDinhPhien.lay_khoa_phien({"user": "bob"})
    tin = BoDinhPhien.lay_khoa_phien({"messages": [{"role": "user", "content": rat_dai}]})
    assert len(tin) <= 70
    b = BoDinhPhien()
    for i in range(2100):
        b.bind("cb", "chat", BoDinhPhien.lay_khoa_phien({"user": f"u{i}" * 1000}), "pa/m1")
    assert len(b._map) <= 2000
    assert max(len(x) for x in b._map) < 120


# ───────────── tính năng, nhật ký, mẫu tên ─────────────

def test_co_combo_dung_nghia(moi_truong):
    client, _, _ = moi_truong
    assert client.get("/api/tinh-nang", headers=QT).json()["co_combo"] is False
    assert put(client, "cb", [{"model": "pa/m1"}]).status_code == 200
    assert client.get("/api/tinh-nang", headers=QT).json()["co_combo"] is True
    assert client.delete("/api/combo/cb", headers=QT).status_code == 200
    assert client.get("/api/tinh-nang", headers=QT).json()["co_combo"] is False


def test_combo_trong_v1_models(moi_truong):
    client, _, _ = moi_truong
    assert put(client, "cb", [{"model": "pa/m2"}, {"model": "pb/m2"}, {"model": "zzz/x"}]).status_code == 200
    assert put(client, "cc", [{"model": "pa/m1"}, {"model": "pb/m2"}]).status_code == 200
    data = {m["id"]: m for m in client.get("/v1/models", headers=QT).json()["data"]}
    assert data["combo/cb"]["owned_by"] == "combo" and data["combo/cb"]["kha_nang"] == ["anh", "chat"]
    assert data["combo/cc"]["kha_nang"] == ["chat"]


def test_nhat_ky_kenh_combo(moi_truong):
    client, _, _ = moi_truong
    assert put(client, "cb_log", [{"model": "pa/m1"}]).status_code == 200
    dong = client.get("/api/nhat-ky?kenh=combo", headers=QT).json()
    assert any("cb_log" in str(d.get("tom_tat", "")) for d in dong)


@pytest.mark.parametrize("mau", [ID_HOP_LE, TIEN_TO_HOP_LE, MA_KHA_NANG, phien.ID_HOP_LE])
def test_mau_ten_khong_nhan_xuong_dong_cuoi(mau):
    assert mau.match("ab") or mau.match("ab0")
    assert mau.match("ab\n") is None and mau.match("ab0\n") is None
