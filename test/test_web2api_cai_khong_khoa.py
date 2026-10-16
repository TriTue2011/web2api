"""Cài module KHÔNG cần khoá, liên kết khoá SAU trên web (KE-HOACH-CAI-MODULE.md, 11/10/2026).

Ba trạng thái suy ra từ cấu trúc, không từ danh sách gõ tay:
  đã cài       — mã nằm trong <WEB2API_DATA>/modules/<id>/ (catalog) → có trong GET /api/modules, có tab;
  đã liên kết  — có cấu hình hợp lệ và đang phục vụ /v1 (`da_cau_hinh`);
  module có trường `bat_buoc` mà chưa có cấu hình thì KHÔNG được tạo thể hiện.
Và: lần đầu thêm nguồn git thì cập nhật một lần các module đã có mã mà git có bản khác.

GitHub và Google đều là máy chủ GIẢ cục bộ (gia.py); khoá là chuỗi giả đã có trong allowlist gitleaks.
"""

import asyncio
import json
import logging
import shutil
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import so_module
from app.main import app
from app.nguon_git import NguonGit
from app.so_module import SoModule
from gia import GitHubGia, GoogleGia, MayChuThat, ghi_thu_muc, tep_echo, tep_kho

GOC_GEMINI = Path(__file__).resolve().parents[1] / "modules" / "gemini-api"
KHOA_QT = "khoa-quan-tri-test-0123456789abcdef"
QT = {"Authorization": f"Bearer {KHOA_QT}"}
K1 = "AIzaSyTOT-mot-000000000000000000000001"
K2 = "AIzaSyTOT-hai-000000000000000000000002"
K3 = "AIzaSyTOT-ba--000000000000000000000003"
MOI_KHOA = (K1, K2, K3)
REPO = "chu-may/c2a"
CAU_HINH_GIT = {"repo": REPO, "nhanh": "main", "thu_muc": "modules", "chu_ky_giay": 1}


def tep_gemini() -> dict[str, bytes]:
    return {str(p.relative_to(GOC_GEMINI)): p.read_bytes() for p in GOC_GEMINI.rglob("*")
            if p.is_file() and "__pycache__" not in p.parts}


@pytest.fixture(scope="module")
def _gh():
    g = GitHubGia(REPO)
    with MayChuThat(g.app) as s:
        g.url = s.url
        yield g


@pytest.fixture
def gh(_gh, monkeypatch):
    for k in ("nhanh", "commits", "trees", "blobs", "blob_sai", "nhat_ky", "loi_commit"):
        getattr(_gh, k).clear()
    monkeypatch.setenv("WEB2API_GITHUB_API", _gh.url)
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")  # NV16: NguonGit ghim IP, máy chủ giả ở loopback
    return _gh


@pytest.fixture(scope="module")
def _google():
    g = GoogleGia()
    with MayChuThat(g.app) as s:
        g.url = s.url
        yield g


@pytest.fixture
def google(_google):
    _google.hanh_vi.clear()
    _google.nhat_ky.clear()
    _google.hanh_vi.update({k: {"kieu": "tot"} for k in MOI_KHOA})
    return _google


@pytest.fixture
def du_lieu(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")
    return tmp_path


def cho(dieu_kien, giay=10, mo_ta=""):
    han = time.monotonic() + giay
    while time.monotonic() < han:
        v = dieu_kien()
        if v:
            return v
        time.sleep(0.05)
    raise AssertionError(f"hết {giay} s chờ: {mo_ta}")


def muc(c, id_):
    return next((m for m in c.get("/api/modules", headers=QT).json()["modules"] if m.get("id") == id_), None)


def tin(model="gemini/gemini-2.5-flash"):
    return {"model": model, "messages": [{"role": "user", "content": "xin chào"}]}


def khong_ro_khoa(*van_ban):
    for vb in van_ban:
        for k in MOI_KHOA:
            assert k not in vb, f"khoá …{k[-6:]} bị lộ"


def cai_gemini_tu_git(c, gh):
    gh.day(tep_kho({"gemini-api": tep_gemini()}), thong_diep="thêm gemini-api")
    assert c.put("/api/nguon-git", json=CAU_HINH_GIT, headers=QT).status_code == 200
    cho(lambda: any(x["id"] == "gemini-api" for x in c.get("/api/modules/thong-bao", headers=QT).json()["danh_sach"]),
        mo_ta="thông báo gemini-api")
    r = c.post("/api/nguon-git/cai/gemini-api", headers=QT)
    assert r.status_code == 200, r.text
    return r.json()


# ───────────── ca 1: cài không khoá ─────────────

def test_cai_tu_git_khong_khoa_hien_trong_danh_sach_nhung_chua_phuc_vu(du_lieu, gh):
    with TestClient(app) as c:
        kq = cai_gemini_tu_git(c, gh)
        assert kq["cai"] is None  # có trường bắt buộc → lõi không tự cài cấu hình rỗng
        m = muc(c, "gemini-api")
        assert m is not None, "module đã cài (mã trên đĩa) phải có trong GET /api/modules"
        assert m["da_cau_hinh"] is False and m["can_cau_hinh"] is True and m["nguon"] == "du_lieu"
        # Gemini không khai tab riêng → tab trỏ tới thẻ cấu hình của nó trên trang Module (đường tương đối).
        assert m["tab_web"] == {"nhan": m["ten"], "duong": "He-thong.dc.html#module-gemini-api"}
        mo = c.get("/api/modules", headers=QT).text + c.get("/api/modules/catalog", headers=QT).text
        assert REPO not in mo
        cat = next(x for x in c.get("/api/modules/catalog", headers=QT).json()["modules"] if x["id"] == "gemini-api")
        assert cat["da_cau_hinh"] is False and "da_cai" not in cat
        # Không vào /v1/models, chat nói rõ chưa có khoá và chỉ chỗ thêm.
        assert c.get("/v1/models", headers=QT).json()["data"] == []
        r = c.post("/v1/chat/completions", json=tin(), headers=QT)
        assert r.status_code == 404
        loi = r.json()["error"]
        assert "chưa có khoá" in loi["message"] and "gemini-api" in loi["message"] and "tab" in loi["message"]
        assert "chưa cài" not in loi["message"]
        assert loi["code"] == "module_not_configured"
        # Sức khoẻ cũng nói đúng trạng thái, không nói «chưa cài».
        r = c.get("/api/modules/gemini-api/suc-khoe", headers=QT)
        assert r.status_code == 404 and "chưa có khoá" in r.json()["error"]["message"]
        # Cấu hình rỗng bị module từ chối và KHÔNG tạo thể hiện.
        r = c.post("/api/modules/gemini-api/cai", json={"cau_hinh": {}}, headers=QT)
        assert r.status_code == 400 and r.json()["ok"] is False
        assert "gemini-api" not in c.app.state.so.dang_chay


def test_khong_tao_the_hien_khi_thieu_truong_bat_buoc(du_lieu, google):
    # modules.json có mục cho gemini-api nhưng tệp bí mật đã mất: khởi động lại KHÔNG được dựng thể hiện
    # với danh sách khoá rỗng (ds_khoa(None) = [] — mã module không tự vỡ, lõi phải chặn).
    shutil.copytree(GOC_GEMINI, du_lieu / "modules" / "gemini-api")
    with TestClient(app) as c:
        r = c.post("/api/modules/gemini-api/cai", headers=QT,
                   json={"cau_hinh": {"api_keys": [K1], "base_url": google.url}})
        assert r.status_code == 200, r.text
    (du_lieu / "bi_mat" / "gemini-api.json").unlink()
    with TestClient(app) as c:
        assert "gemini-api" not in c.app.state.so.dang_chay
        assert muc(c, "gemini-api")["da_cau_hinh"] is False
        assert "chưa có khoá" in c.post("/v1/chat/completions", json=tin(), headers=QT).json()["error"]["message"]


# ───────────── ca 2: thêm / bớt khoá trên web ─────────────

def test_them_bot_khoa_tung_cai_khong_lo_khoa(du_lieu, google, caplog):
    shutil.copytree(GOC_GEMINI, du_lieu / "modules" / "gemini-api")
    with caplog.at_level(logging.DEBUG), TestClient(app) as c:
        phan_hoi = []

        def goi(phuong_thuc, duong, **kw):
            r = c.request(phuong_thuc, duong, headers=QT, **kw)
            phan_hoi.append(r.text)
            return r

        r = goi("POST", "/api/modules/gemini-api/cai", json={"cau_hinh": {"api_keys": [K1], "base_url": google.url}})
        assert r.status_code == 200 and r.json()["ok"], r.text
        assert [x["id"] for x in goi("GET", "/v1/models").json()["data"]] == [
            "gemini/gemini-2.5-flash", "gemini/gemini-3-flash-preview"]
        assert goi("POST", "/v1/chat/completions", json=tin()).status_code == 200
        assert muc(c, "gemini-api")["da_cau_hinh"] is True

        # Hợp đồng form: trường của manifest + giá trị đã lưu, bí mật CHỈ ở dạng che, theo thứ tự (chỉ số).
        ch = goi("GET", "/api/modules/gemini-api/cau-hinh").json()
        assert [t["khoa"] for t in ch["truong_cau_hinh"]] == ["api_keys", "base_url", "proxy_token"]
        assert ch["gia_tri"] == {"api_keys": ["…" + K1[-4:]], "base_url": google.url + "/v1beta/openai"}
        assert ch["da_cau_hinh"] is True

        # Thêm khoá thứ hai: GIỮ khoá cũ theo chỉ số, không phải dán lại; base_url vắng mặt = giữ giá trị đã lưu
        # (nếu bị đặt lại mặc định, module gọi Google THẬT và hỏng vì mạng đã bị chặn).
        r = goi("POST", "/api/modules/gemini-api/cai", json={"cau_hinh": {"api_keys": {"giu": [0], "them": [K2]}}})
        assert r.status_code == 200 and r.json()["ok"], r.text
        assert json.loads((du_lieu / "bi_mat" / "gemini-api.json").read_text())["api_keys"] == [K1, K2]
        assert goi("GET", "/api/modules/gemini-api/cau-hinh").json()["gia_tri"]["api_keys"] == [
            "…" + K1[-4:], "…" + K2[-4:]]

        # Gửi cấu hình rỗng: mọi trường vắng mặt → giữ nguyên, kiểm lại rồi lưu y như cũ.
        r = goi("POST", "/api/modules/gemini-api/cai", json={"cau_hinh": {}})
        assert r.status_code == 200 and len(r.json()["ket_qua_tung_khoa"]) == 2

        # Bớt khoá thứ nhất: chỉ giữ chỉ số 1.
        r = goi("POST", "/api/modules/gemini-api/cai", json={"cau_hinh": {"api_keys": {"giu": [1]}}})
        assert r.status_code == 200 and r.json()["ok"], r.text
        assert json.loads((du_lieu / "bi_mat" / "gemini-api.json").read_text())["api_keys"] == [K2]
        goi("POST", "/v1/chat/completions", json=tin())
        assert [x for x in google.nhat_ky if x["duong"] == "chat"][-1]["khoa"] == K2

        # Danh sách thường (không phải dạng giu/them) vẫn là THAY cả danh sách.
        r = goi("POST", "/api/modules/gemini-api/cai", json={"cau_hinh": {"api_keys": [K3]}})
        assert r.status_code == 200
        assert json.loads((du_lieu / "bi_mat" / "gemini-api.json").read_text())["api_keys"] == [K3]

        # Đầu vào sai ở biên → 400 rõ ràng, không lưu gì.
        for sai in ({"giu": ["0"]}, {"giu": [7]}, {"giu": [0], "la": 1}, {"them": "chuoi"}):
            r = goi("POST", "/api/modules/gemini-api/cai", json={"cau_hinh": {"api_keys": sai}})
            assert r.status_code == 400, (sai, r.text)
        assert json.loads((du_lieu / "bi_mat" / "gemini-api.json").read_text())["api_keys"] == [K3]

        phan_hoi += [c.get("/api/modules", headers=QT).text, c.get("/api/modules/catalog", headers=QT).text,
                     (du_lieu / "modules.json").read_text()]
    khong_ro_khoa(*phan_hoi, caplog.text)


def test_cau_hinh_module_chua_lien_ket_va_khong_co(du_lieu):
    shutil.copytree(GOC_GEMINI, du_lieu / "modules" / "gemini-api")
    with TestClient(app) as c:
        ch = c.get("/api/modules/gemini-api/cau-hinh", headers=QT).json()
        assert ch["da_cau_hinh"] is False and ch["gia_tri"] == {}
        assert c.get("/api/modules/khong-co/cau-hinh", headers=QT).status_code == 404


# ───────────── ca 3: gỡ, khởi động lại ─────────────

def test_go_va_khoi_dong_lai_giu_dung_trang_thai(du_lieu, google):
    shutil.copytree(GOC_GEMINI, du_lieu / "modules" / "gemini-api")
    ghi_thu_muc(du_lieu / "modules" / "echo", tep_echo())
    with TestClient(app) as c:
        assert c.post("/api/modules/echo/cai", json={"cau_hinh": {}}, headers=QT).status_code == 200
    with TestClient(app) as c:
        assert (muc(c, "gemini-api")["da_cau_hinh"], muc(c, "echo")["da_cau_hinh"]) == (False, True)
        assert muc(c, "echo")["can_cau_hinh"] is False
        # Module tự khai tab riêng thì vẫn dùng tab đó; mở được cả khi chưa liên kết thì cũng mở khi đã cài.
        assert muc(c, "echo")["tab_web"] == {"nhan": "Echo", "duong": "/m/echo/echo.dc.html"}
        r = c.post("/api/modules/gemini-api/cai", headers=QT, json={"cau_hinh": {"api_keys": [K1], "base_url": google.url}})
        assert r.status_code == 200
    with TestClient(app) as c:
        assert muc(c, "gemini-api")["da_cau_hinh"] is True
        # Gỡ module đã liên kết: xoá cả bí mật lẫn mã.
        assert c.delete("/api/modules/gemini-api", headers=QT).status_code == 200
        assert not (du_lieu / "bi_mat" / "gemini-api.json").exists()
        assert not (du_lieu / "modules" / "gemini-api").exists() and muc(c, "gemini-api") is None
    # Gỡ module CHƯA liên kết (mã có, chưa khoá).
    shutil.copytree(GOC_GEMINI, du_lieu / "modules" / "gemini-api")
    with TestClient(app) as c:
        assert muc(c, "gemini-api")["da_cau_hinh"] is False
        r = c.delete("/api/modules/gemini-api", headers=QT)
        assert r.status_code == 200, r.text
        assert not (du_lieu / "modules" / "gemini-api").exists() and muc(c, "gemini-api") is None


def test_tab_rieng_mo_duoc_khi_da_cai_chua_lien_ket(du_lieu):
    ghi_thu_muc(du_lieu / "modules" / "echo", tep_echo())
    with TestClient(app) as c:
        assert "echo" not in c.app.state.so.dang_chay
        r = c.get("/m/echo/echo.dc.html")
        assert r.status_code == 200 and "echo v1" in r.text


def test_ban_moi_them_truong_bat_buoc_thi_thanh_chua_lien_ket(du_lieu):
    # Bản mới trên đĩa thêm trường bắt buộc mà cấu hình đã lưu không có: không để mã CŨ phục vụ tiếp dưới
    # phiên bản mới — module thành «đã cài, chưa liên kết».
    ghi_thu_muc(du_lieu / "modules" / "echo", tep_echo("echo v1"))
    with TestClient(app) as c:
        assert c.post("/api/modules/echo/cai", json={"cau_hinh": {}}, headers=QT).status_code == 200
        cu = c.app.state.so.dang_chay["echo"]
        tep = tep_echo("echo v2", "2.0.0")
        m = json.loads(tep["manifest.json"])
        m["truong_cau_hinh"] = [{"khoa": "ma", "nhan": "Mã", "kieu": "bi_mat", "bat_buoc": True}]
        tep["manifest.json"] = json.dumps(m).encode()
        ghi_thu_muc(du_lieu / "modules" / "echo", tep)
        assert c.post("/api/modules/nap-lai", headers=QT).json()["doi"] == ["echo"]
        assert "echo" not in c.app.state.so.dang_chay and cu.da_dung is True
        assert muc(c, "echo")["da_cau_hinh"] is False and muc(c, "echo")["phien_ban"] == "2.0.0"
        assert "chưa có khoá" in c.post("/v1/chat/completions", json=tin("echo/x"), headers=QT).json()["error"]["message"]


# ───────────── ca 4: lần đầu thêm nguồn git thì cập nhật module đang có ─────────────

def chay(ham):
    async def _():
        so = SoModule()
        await so.khoi_dong()
        ng = NguonGit(so)
        try:
            return await ham(so, ng)
        finally:
            await ng.dung()
            await so.dung_het()
    return asyncio.run(_())


def test_lan_dau_them_git_cap_nhat_module_dat_tay_qua_vong_nen(du_lieu, gh):
    ghi_thu_muc(du_lieu / "modules" / "echo", tep_echo("echo tay", "0.9.0"))
    gh.day(tep_kho({"echo": tep_echo("echo git", "1.0.0"), "dich": tep_echo("dich", id_="dich", tien_to="dich")}))
    with TestClient(app) as c:
        assert c.post("/api/modules/echo/cai", json={"cau_hinh": {}}, headers=QT).status_code == 200
        assert c.put("/api/nguon-git", json=CAU_HINH_GIT, headers=QT).status_code == 200
        cho(lambda: (du_lieu / "modules" / "echo" / ".nguon.json").exists(), mo_ta="echo được cập nhật từ git")
        r = c.post("/v1/chat/completions", json=tin("echo/x"), headers=QT)
        assert r.json()["choices"][0]["message"]["content"] == "echo git: xin chào"
        assert c.get("/api/nguon-git", headers=QT).json()["cau_hinh"]["tu_dong"] is False
        assert "dich" not in c.app.state.so.catalog  # module MỚI trên git thì chờ chủ máy bấm Cài
        # Lần đồng bộ sau (bản mới trên git) KHÔNG tự cập nhật lặp lại.
        gh.day(tep_kho({"echo": tep_echo("echo git 2", "2.0.0"), "dich": tep_echo("dich", id_="dich", tien_to="dich")}))
        c.put("/api/nguon-git", json={}, headers=QT)  # đánh thức thăm dò ngay
        cho(lambda: any(x["id"] == "echo" and x["trang_thai"] == "co_ban_moi"
                        for x in c.get("/api/modules/thong-bao", headers=QT).json()["danh_sach"]), mo_ta="thấy bản 2")
        time.sleep(1.5)
        r = c.post("/v1/chat/completions", json=tin("echo/x"), headers=QT)
        assert r.json()["choices"][0]["message"]["content"] == "echo git: xin chào"


def test_lan_dau_khong_cap_nhat_ban_xin_them_quyen(du_lieu, gh):
    ghi_thu_muc(du_lieu / "modules" / "echo", tep_echo("echo tay"))
    gh.day(tep_kho({"echo": tep_echo("echo git", yeu_cau_goi=["json"], mang_ra=["vi-du.com"])}))

    async def ham(so, ng):
        await so.nap_lai()
        ng.luu_cau_hinh(CAU_HINH_GIT)
        await ng.mot_lan()
        await ng._tu_dong()
        assert not (du_lieu / "modules" / "echo" / ".nguon.json").exists()
        x = ng.thong_bao()["danh_sach"][0]
        assert x["trang_thai"] == "co_ban_moi" and "THÊM QUYỀN" in x["ghi_chu"]
        assert "không tự cập nhật" in x["ghi_chu"]  # đã có mã trên máy → «cập nhật», không phải «cài»
    chay(ham)


def test_quy_tac_tu_ngu_cai_va_cap_nhat(du_lieu, gh):
    # Chưa có mã trên máy → «cài»; đã cài (kể cả đặt tay, chưa liên kết) → «cập nhật». Suy từ đĩa, không từ danh sách.
    ghi_thu_muc(du_lieu / "modules" / "echo", tep_echo("echo tay"))
    gh.day(tep_kho({"echo": tep_echo("echo git"), "dich": tep_echo("dich", id_="dich", tien_to="dich")}), xac_minh=False)

    async def ham(so, ng):
        assert (ng.viec("echo"), ng.viec("dich")) == ("cập nhật", "cài")
        ng.luu_cau_hinh({**CAU_HINH_GIT, "tu_dong": True, "yeu_cau_chu_ky": True})
        await ng.mot_lan()
        await ng._tu_dong()
        ghi_chu = {x["id"]: x["ghi_chu"] for x in ng.thong_bao()["danh_sach"]}
        assert "không tự cập nhật" in ghi_chu["echo"] and "không tự cài" in ghi_chu["dich"]
    chay(ham)


def test_lan_dau_ton_trong_da_go_va_yeu_cau_chu_ky(du_lieu, gh):
    ghi_thu_muc(du_lieu / "modules" / "echo", tep_echo("echo tay"))
    gh.day(tep_kho({"echo": tep_echo("echo git")}))

    async def ham(so, ng):
        await so.nap_lai()
        ng.luu_cau_hinh(CAU_HINH_GIT)
        await ng.mot_lan()
        ng.da_go("echo")  # quản trị đã gỡ đúng bản này trước đó
        await ng._tu_dong()
        assert not (du_lieu / "modules" / "echo" / ".nguon.json").exists()
        assert "đã gỡ" in ng.thong_bao()["danh_sach"][0]["ghi_chu"]
    chay(ham)

    gh.day(tep_kho({"echo": tep_echo("echo git ký")}), xac_minh=False)
    (du_lieu / "nguon_git_trang_thai.json").unlink(missing_ok=True)

    async def ham2(so, ng):
        ng.luu_cau_hinh({**CAU_HINH_GIT, "yeu_cau_chu_ky": True})
        await ng.mot_lan()
        await ng._tu_dong()
        assert not (du_lieu / "modules" / "echo" / ".nguon.json").exists()
        assert "chữ ký" in ng.thong_bao()["danh_sach"][0]["ghi_chu"]
    chay(ham2)


def test_trang_thai_da_dong_bo_tu_truoc_khong_tinh_la_lan_dau(du_lieu, gh):
    # Máy đang chạy bản cũ đã đồng bộ repo này rồi (trạng thái lưu trên đĩa có commit): nâng cấp lõi
    # KHÔNG được tự cập nhật module lúc không ai bấm.
    gh.day(tep_kho({"echo": tep_echo("echo git")}))

    async def ham(so, ng):
        ng.luu_cau_hinh(CAU_HINH_GIT)
        await ng.mot_lan()
        await ng._tu_dong()
        ng._luu_trang_thai()
    chay(ham)
    ghi_thu_muc(du_lieu / "modules" / "echo", tep_echo("echo tay"))
    gh.day(tep_kho({"echo": tep_echo("echo git 2")}))

    async def ham2(so, ng):
        await ng.mot_lan()
        await ng._tu_dong()
        assert not (du_lieu / "modules" / "echo" / ".nguon.json").exists()
        assert ng.thong_bao()["danh_sach"][0]["trang_thai"] == "co_ban_moi"
    chay(ham2)


def test_nguon_tu_bien_moi_truong_cung_la_lan_dau(du_lieu, gh, monkeypatch):
    # Nguồn từ biến môi trường cũng là «lần đầu» ở lần đồng bộ đầu tiên của repo đó.
    ghi_thu_muc(du_lieu / "modules" / "echo", tep_echo("echo tay"))
    gh.day(tep_kho({"echo": tep_echo("echo git")}))
    monkeypatch.setenv("WEB2API_GIT_REPO", REPO)

    async def ham(so, ng):
        await ng.mot_lan()
        await ng._tu_dong()
        assert (du_lieu / "modules" / "echo" / ".nguon.json").exists()
        assert ng.cau_hinh()["tu_dong"] is False
    chay(ham)
