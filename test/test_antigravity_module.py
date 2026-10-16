"""Test module antigravity (modules/antigravity/) và bốn điều kiện nghiệm thu chung.

Kiểm tra:
1. Module antigravity:
   - cai() với thông tin sai → ok: False
   - cai() với cầu nối giả → ok: True, cau_hinh_luu đúng
   - Cài không cần khoá: da_cau_hinh: False, can_cau_hinh: True
   - models() có tiền tố "antigravity/"
   - chat() non-stream & stream chuyển tiếp đúng
   - suc_khoe() đọc thông tin đã che từ /tai-khoan
2. Bốn điều kiện nghiệm thu chung:
   - ĐK 1: Lõi chạy đúng khi không có module nào và khi chỉ có module loại lạ
   - ĐK 2: Thêm/gỡ module giữa chừng không phải sửa mã lõi
   - ĐK 3: Không tên nhà cung cấp/module trong app/ và web/ (dòng mới)
   - ĐK 4: Mọi bảng/danh sách trong lõi là mã MỞ (mã lạ hiện nguyên mã)
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from app import kham_pha, so_module
from app.kham_pha import kiem_manifest, doc_manifest, nap_ma, van_tay
from app.main import app
from app.so_module import SoModule
from gia import MayChuThat

GOC = Path(__file__).resolve().parents[1]
THU_MUC_MODULE = GOC / "modules" / "antigravity"
KHOA_ADMIN = "khoa-quan-tri-test-0123456789abcdef"
HEADER_ADMIN = {"Authorization": f"Bearer {KHOA_ADMIN}"}
KHOA_CAU_NOI = "sk-lan-0000000000000000000000000000xyzw"


class CauNoiGia:
    """Máy chủ cầu nối giả lập giao thức của dich-vu/antigravity-cau-noi/."""

    def __init__(self):
        self.da_goi_chat = False
        self.da_goi_models = False
        self.da_goi_tai_khoan = False
        routes = [
            Route("/health", self.health, methods=["GET"]),
            Route("/v1/models", self.models, methods=["GET"]),
            Route("/v1/chat/completions", self.chat, methods=["POST"]),
            Route("/tai-khoan", self.tai_khoan, methods=["GET"]),
        ]
        self.app = Starlette(routes=routes)

    async def health(self, request):
        return JSONResponse({"status": "ok", "accounts": 1})

    async def models(self, request):
        auth = request.headers.get("authorization", "")
        if auth != f"Bearer {KHOA_CAU_NOI}":
            return JSONResponse({"error": {"message": "Sai khoá"}}, status_code=401)
        self.da_goi_models = True
        return JSONResponse({
            "object": "list",
            "data": [
                {"id": "gemini-3.8-flash-high", "object": "model", "owned_by": "antigravity"},
                {"id": "claude-sonnet-4-6", "object": "model", "owned_by": "antigravity"},
            ],
        })

    async def chat(self, request):
        auth = request.headers.get("authorization", "")
        if auth != f"Bearer {KHOA_CAU_NOI}":
            return JSONResponse({"error": {"message": "Sai khoá"}}, status_code=401)
        self.da_goi_chat = True
        body = await request.json()
        model = body.get("model", "")
        stream = body.get("stream", False)
        if stream:
            async def _sse():
                chunk = {"id": "c1", "object": "chat.completion.chunk",
                         "choices": [{"index": 0, "delta": {"content": "Phản hồi stream"}, "finish_reason": None}]}
                yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode()
                yield b"data: [DONE]\n\n"
            return StreamingResponse(_sse(), media_type="text/event-stream")
        return JSONResponse({
            "id": "c1",
            "object": "chat.completion",
            "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "Phản hồi thường"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        })

    async def tai_khoan(self, request):
        auth = request.headers.get("authorization", "")
        if auth != f"Bearer {KHOA_CAU_NOI}":
            return JSONResponse({"error": {"message": "Sai khoá"}}, status_code=401)
        self.da_goi_tai_khoan = True
        return JSONResponse({
            "tai_khoan": [{
                "thu_tu": 0,
                "email": "u…r@example.com",
                "dang_ban": False,
                "han_muc": {"Gemini Models": [{"cua_so": "Weekly", "phan_tram": 50, "moc_reset": "2026-10-19T00:00:00Z"}]},
            }],
        })


@pytest.fixture
def du_lieu(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_ADMIN)
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")
    return tmp_path


# ───────────── 1. Test Module Antigravity ─────────────


def test_manifest_antigravity_hop_le():
    m, loi = doc_manifest(THU_MUC_MODULE)
    assert loi is None, f"manifest lỗi: {loi}"
    assert m["id"] == "antigravity"
    assert m["loai"] == "ai"
    assert m["tien_to"] == "antigravity"
    assert m["cung_cap"] == ["chat"]
    truong_khoa = [t["khoa"] for t in m["truong_cau_hinh"]]
    assert "dia_chi_cau_noi" in truong_khoa
    assert "khoa_cau_noi" in truong_khoa


def test_nap_ma_antigravity():
    m, _ = doc_manifest(THU_MUC_MODULE)
    vt = van_tay(THU_MUC_MODULE)
    lop, ten = nap_ma(THU_MUC_MODULE, m, vt)
    assert lop is not None
    instance = lop(m)
    assert instance.tien_to == "antigravity"


def test_cai_antigravity_that_bai_khi_sai_dia_chi_hoac_khoa():
    m, _ = doc_manifest(THU_MUC_MODULE)
    vt = van_tay(THU_MUC_MODULE)
    lop, _ = nap_ma(THU_MUC_MODULE, m, vt)
    instance = lop(m)

    # Không nhập khoá
    res = asyncio.run(instance.cai({"dia_chi_cau_noi": "http://127.0.0.1:8300"}))
    assert res["ok"] is False
    assert "khoa_cau_noi" in res["loi"]

    # Địa chỉ không nghe
    res = asyncio.run(instance.cai({"dia_chi_cau_noi": "http://127.0.0.1:9", "khoa_cau_noi": KHOA_CAU_NOI}))
    assert res["ok"] is False


def test_vong_doi_module_voi_cau_noi_gia():
    cn = CauNoiGia()
    with MayChuThat(cn.app) as server:
        async def _chay():
            m, _ = doc_manifest(THU_MUC_MODULE)
            vt = van_tay(THU_MUC_MODULE)
            lop, _ = nap_ma(THU_MUC_MODULE, m, vt)
            mod = lop(m)

            # 1. cai()
            kq = await mod.cai({"dia_chi_cau_noi": server.url, "khoa_cau_noi": KHOA_CAU_NOI})
            assert kq["ok"] is True
            assert kq["cau_hinh_luu"]["dia_chi_cau_noi"] == server.url
            assert kq["cau_hinh_luu"]["khoa_cau_noi"] == KHOA_CAU_NOI

            # 2. nap()
            mod.nap(kq["cau_hinh_luu"])

            # 3. models()
            ds = await mod.models()
            assert len(ds) == 2
            ids = [x["id"] for x in ds]
            assert "antigravity/gemini-3.8-flash-high" in ids
            assert "antigravity/claude-sonnet-4-6" in ids

            # 4. chat() non-stream
            status, headers, body = await mod.chat(
                {"model": "antigravity/gemini-3.8-flash-high", "messages": [{"role": "user", "content": "hi"}]},
                stream=False,
            )
            assert status == 200
            data = json.loads(body)
            assert data["choices"][0]["message"]["content"] == "Phản hồi thường"

            # 5. chat() stream
            status, headers, gen = await mod.chat(
                {"model": "antigravity/claude-sonnet-4-6", "stream": True, "messages": [{"role": "user", "content": "hi"}]},
                stream=True,
            )
            assert status == 200
            chunks = []
            async for c in gen:
                chunks.append(c)
            noi_dung = b"".join(chunks).decode("utf-8")
            assert "Phản hồi stream" in noi_dung

            # 6. suc_khoe()
            sk = await mod.suc_khoe()
            assert sk["ok"] is True
            assert "tai_khoan" in sk
            assert sk["tai_khoan"][0]["email"] == "u…r@example.com"

            # 7. dung()
            await mod.dung()

        asyncio.run(_chay())


def test_tich_hop_web2api_cai_khong_khoa_sau_do_lien_ket(du_lieu):
    # Sao chép module vào thư mục dữ liệu (giống như cài từ git)
    shutil.copytree(THU_MUC_MODULE, du_lieu / "modules" / "antigravity")

    with TestClient(app) as c:
        # Cài không cần khoá: có trong danh sách nhưng chưa phục vụ
        r = c.get("/api/modules", headers=HEADER_ADMIN)
        assert r.status_code == 200
        mods = r.json()["modules"]
        ag = next((m for m in mods if m["id"] == "antigravity"), None)
        assert ag is not None
        assert ag["da_cau_hinh"] is False
        assert ag["can_cau_hinh"] is True

        # Chưa vào /v1/models
        assert not any(m["id"].startswith("antigravity/") for m in c.get("/v1/models", headers=HEADER_ADMIN).json()["data"])

        # Liên kết với cầu nối giả
        cn = CauNoiGia()
        with MayChuThat(cn.app) as server:
            r = c.post(
                "/api/modules/antigravity/cai",
                headers=HEADER_ADMIN,
                json={"cau_hinh": {"dia_chi_cau_noi": server.url, "khoa_cau_noi": KHOA_CAU_NOI}},
            )
            assert r.status_code == 200
            assert r.json()["ok"] is True

            # Sau khi liên kết: da_cau_hinh = True
            r_mod = c.get("/api/modules", headers=HEADER_ADMIN).json()["modules"]
            ag_sau = next(m for m in r_mod if m["id"] == "antigravity")
            assert ag_sau["da_cau_hinh"] is True

            # Model xuất hiện trong /v1/models
            models_sau = c.get("/v1/models", headers=HEADER_ADMIN).json()["data"]
            assert any(m["id"] == "antigravity/gemini-3.8-flash-high" for m in models_sau)

            # Chat qua web2api
            r_chat = c.post(
                "/v1/chat/completions",
                headers=HEADER_ADMIN,
                json={"model": "antigravity/gemini-3.8-flash-high", "messages": [{"role": "user", "content": "alo"}]},
            )
            assert r_chat.status_code == 200
            assert r_chat.json()["choices"][0]["message"]["content"] == "Phản hồi thường"

            # Sức khoẻ: tài khoản của cầu nối (email đã che) đi qua; khoá cầu nối KHÔNG lộ ở bất kỳ API nào của lõi.
            r_sk = c.get("/api/modules/antigravity/suc-khoe", headers=HEADER_ADMIN)
            assert r_sk.status_code == 200 and r_sk.json()["tai_khoan"][0]["email"] == "u…r@example.com"
            for duong in ("/api/modules", "/api/modules/antigravity/suc-khoe", "/api/modules/antigravity/cau-hinh",
                          "/api/tinh-nang"):
                assert KHOA_CAU_NOI not in c.get(duong, headers=HEADER_ADMIN).text, duong


# ───────────── 2. BỐN ĐIỀU KIỆN NGHIỆM THU CHUNG ─────────────


def test_dieu_kien_1_loi_chay_dung_khi_khong_co_module_va_khi_co_module_la(du_lieu):
    """ĐK 1: Lõi chạy đúng khi KHÔNG có module nào (và khi chỉ có module loại lạ)."""
    # 1. Không có module nào
    with TestClient(app) as c:
        assert c.get("/health").status_code == 200
        assert c.get("/api/he-thong", headers=HEADER_ADMIN).status_code == 200
        assert c.get("/api/modules", headers=HEADER_ADMIN).status_code == 200
        assert c.get("/api/modules", headers=HEADER_ADMIN).json()["modules"] == []
        assert c.get("/v1/models", headers=HEADER_ADMIN).json()["data"] == []

    # 2. Chỉ có module loại lạ (ví dụ loai: "nha_thong_minh", cung_cap: ["den"])
    mod_la = du_lieu / "modules" / "thiet-bi-nha"
    mod_la.mkdir(parents=True)
    manifest_la = {
        "id": "thiet-bi-nha",
        "ten": "Thiết bị nhà",
        "mo_ta": "Module điều khiển nhà",
        "hang": "A",
        "loai": "nha_thong_minh",
        "loai_ten": "Nhà",
        "giao_thuc": "openai",
        "tien_to": "nha",
        "cung_cap": ["den"],
        "truong_cau_hinh": [],
        "tai_nguyen": {"mang_ra": []},
    }
    (mod_la / "manifest.json").write_text(json.dumps(manifest_la), encoding="utf-8")
    ma_la = """
class Module:
    def __init__(self, m): self.m = m
    def nap(self, c): pass
    async def cai(self, c): return {"ok": True, "cau_hinh_luu": {}}
    async def chat(self, b, s): return 200, {}, b"{}"
    async def models(self): return []
    async def suc_khoe(self): return {"ok": True}
    async def dung(self): pass
"""
    (mod_la / "module.py").write_text(ma_la, encoding="utf-8")

    with TestClient(app) as c:
        assert c.get("/health").status_code == 200
        assert c.get("/api/tinh-nang", headers=HEADER_ADMIN).status_code == 200
        mods = c.get("/api/modules", headers=HEADER_ADMIN).json()["modules"]
        assert len(mods) == 1
        assert mods[0]["loai"] == "nha_thong_minh"


def test_dieu_kien_2_them_go_module_giua_chung_khong_sua_ma_loi(du_lieu):
    """ĐK 2: Thêm/gỡ module giữa chừng không phải sửa mã lõi."""
    with TestClient(app) as c:
        assert len(c.get("/api/modules", headers=HEADER_ADMIN).json()["modules"]) == 0

        # Thả module mới vào thư mục dữ liệu
        mod_moi = du_lieu / "modules" / "cam-bien-la"
        mod_moi.mkdir(parents=True)
        m = {
            "id": "cam-bien-la",
            "ten": "Cảm biến lạ",
            "mo_ta": "Mô tả",
            "hang": "A",
            "loai": "cam_bien",
            "giao_thuc": "openai",
            "tien_to": "sensor",
            "cung_cap": ["nhiet_do"],
            "truong_cau_hinh": [],
            "tai_nguyen": {"mang_ra": []},
        }
        (mod_moi / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
        ma = """
class Module:
    def __init__(self, m): pass
    def nap(self, c): pass
    async def cai(self, c): return {"ok": True, "cau_hinh_luu": {}}
    async def chat(self, b, s): return 200, {}, b"{}"
    async def models(self): return []
    async def suc_khoe(self): return {"ok": True}
    async def dung(self): pass
"""
        (mod_moi / "module.py").write_text(ma, encoding="utf-8")

        # Nạp lại
        assert c.post("/api/modules/nap-lai", headers=HEADER_ADMIN).status_code == 200
        mods = c.get("/api/modules", headers=HEADER_ADMIN).json()["modules"]
        assert any(x["id"] == "cam-bien-la" for x in mods)

        # Gỡ module
        shutil.rmtree(mod_moi)
        assert c.post("/api/modules/nap-lai", headers=HEADER_ADMIN).status_code == 200
        mods_sau = c.get("/api/modules", headers=HEADER_ADMIN).json()["modules"]
        assert not any(x["id"] == "cam-bien-la" for x in mods_sau)


def test_dieu_kien_2_tha_va_go_chinh_module_antigravity_giua_chung(du_lieu):
    """ĐK 2 với CHÍNH module antigravity: lõi đang chạy không module → thả thư mục vào, nạp nóng → có (chờ cấu
    hình), liên kết → model hiện; gỡ thư mục, nạp nóng → biến mất khỏi /api/modules và /v1/models. Không sửa lõi."""
    dich = du_lieu / "modules" / "antigravity"
    with TestClient(app) as c:
        assert c.get("/api/modules", headers=HEADER_ADMIN).json()["modules"] == []
        shutil.copytree(THU_MUC_MODULE, dich, ignore=shutil.ignore_patterns("__pycache__"))
        assert c.post("/api/modules/nap-lai", headers=HEADER_ADMIN).status_code == 200
        ag = next(m for m in c.get("/api/modules", headers=HEADER_ADMIN).json()["modules"] if m["id"] == "antigravity")
        assert ag["can_cau_hinh"] is True and ag["da_cau_hinh"] is False
        assert not [m for m in c.get("/v1/models", headers=HEADER_ADMIN).json()["data"] if m["id"].startswith("antigravity/")]

        cn = CauNoiGia()
        with MayChuThat(cn.app) as server:
            r = c.post("/api/modules/antigravity/cai", headers=HEADER_ADMIN,
                       json={"cau_hinh": {"dia_chi_cau_noi": server.url, "khoa_cau_noi": KHOA_CAU_NOI}})
            assert r.status_code == 200 and r.json()["ok"] is True
            assert any(m["id"] == "antigravity/claude-sonnet-4-6"
                       for m in c.get("/v1/models", headers=HEADER_ADMIN).json()["data"])

        # Gỡ TAY (xoá thư mục mã) rồi nạp nóng: hết phục vụ; mục cấu hình còn lại hiện riêng, không loại/không chạy
        # (lõi cố ý không giấu cấu hình mất mã — app/tuyen.py ds_modules).
        shutil.rmtree(dich)
        assert c.post("/api/modules/nap-lai", headers=HEADER_ADMIN).status_code == 200
        con = [m for m in c.get("/api/modules", headers=HEADER_ADMIN).json()["modules"] if m.get("id") == "antigravity"]
        assert all("loai" not in m and not m.get("da_cau_hinh") for m in con), con
        assert not [m for m in c.get("/v1/models", headers=HEADER_ADMIN).json()["data"] if m["id"].startswith("antigravity/")]
        r = c.post("/v1/chat/completions", headers=HEADER_ADMIN,
                   json={"model": "antigravity/claude-sonnet-4-6", "messages": [{"role": "user", "content": "x"}]})
        assert r.status_code == 404 and "error" in r.json()
        # Thả lại rồi gỡ hẳn bằng API (DELETE chỉ nhận module còn mã trên đĩa): mục cấu hình và bí mật cũng đi.
        shutil.copytree(THU_MUC_MODULE, dich, ignore=shutil.ignore_patterns("__pycache__"))
        assert c.post("/api/modules/nap-lai", headers=HEADER_ADMIN).status_code == 200
        assert c.delete("/api/modules/antigravity", headers=HEADER_ADMIN).status_code == 200
        assert not any(m.get("id") == "antigravity" for m in c.get("/api/modules", headers=HEADER_ADMIN).json()["modules"])
        assert not (du_lieu / "bi_mat" / "antigravity.json").exists()


def test_dieu_kien_3_khong_ten_nha_cung_cap_trong_loi():
    """ĐK 3: Không tên nhà cung cấp/module trong app/ và web/ (dòng mới không được thêm)."""
    # Cùng phép đo với `grep -rniE "antigravity|agy" app web` (agy tính theo TỪ để không bắt nhầm chuỗi con).
    mau = re.compile(r"antigravity|\bagy\b", re.IGNORECASE)
    loi_tep: list[str] = []
    for goc_thu_muc in (GOC / "app", GOC / "web"):
        for p in goc_thu_muc.rglob("*"):
            if not p.is_file() or p.suffix in (".pyc", ".png", ".jpg", ".ico"):
                continue
            txt = p.read_text(encoding="utf-8", errors="ignore")
            for m in mau.finditer(txt):
                loi_tep.append(f"{p.relative_to(GOC)}: chứa {m.group(0)}")
    assert not loi_tep, f"Lõi (app/, web/) bị dính tên module/nhà cung cấp cụ thể: {loi_tep}"


def test_dieu_kien_4_ma_mo_khong_loi_khi_gap_ma_la():
    """ĐK 4: Mọi bảng/danh sách trong lõi là mã MỞ: mã lạ hiện nguyên mã, không lỗi."""
    manifest_la = {
        "id": "module-sieu-la",
        "ten": "Siêu lạ",
        "mo_ta": "Mô tả",
        "hang": "B",
        "loai": "loai_hoan_toan_moi",
        "loai_ten": "Loại mới",
        "giao_thuc": "openai",
        "tien_to": "sieu_la",
        "cung_cap": ["kha_nang_chua_tung_co"],
        "truong_cau_hinh": [],
        "tai_nguyen": {"mang_ra": []},
    }
    # kiem_manifest phải chấp nhận mã mới mà không báo lỗi
    loi = kiem_manifest(manifest_la)
    assert loi is None, f"Lõi từ chối mã lạ: {loi}"
