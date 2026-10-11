"""«Tính năng đang có» (GET /api/tinh-nang) — dữ liệu cho khung giao diện quyết tab/mục nào hiện.

Khung giao diện KHÔNG biết tên module hay tên loại: mọi điều kiện suy từ `loai` + `cung_cap` của module đã
cài / đã liên kết (KE-HOACH-GIAO-DIEN-MODULE.md mục 3, 3b). Test dùng module GIẢ khai loại lạ (`mang`) và khả
năng lạ (`dinh_tuyen`) để chứng minh lõi không giữ danh sách loại/khả năng hợp lệ nào.

Kèm: kiểm mẫu các trường manifest mới (`loai_ten`, `loai_thu_tu`, `muc`), tóm tắt module trên git mang đủ
`loai`/`cung_cap`/dung lượng tải để trang Module xếp ô «Chưa cài» vào đúng khu, và lưới chặn «đặc thù nhà
cung cấp» trong lõi/khung (tên suy từ manifest của chính các module trong kho, không gõ tay).
"""

import ast
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import so_module
from app.kham_pha import kiem_manifest
from app.main import app
from gia import GitHubGia, MayChuThat, OpenAIGia, ghi_thu_muc, tep_echo, tep_kho

GOC = Path(__file__).resolve().parents[1]
GOC_GEMINI = GOC / "modules" / "gemini-api"
KHOA_QT = "khoa-quan-tri-tinh-nang-0123456789ab"
QT = {"Authorization": f"Bearer {KHOA_QT}"}
REPO = "chu-may/c2a"


@pytest.fixture
def du_lieu(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")
    return tmp_path


def tep_gia(id_: str, *, loai: str = "ai", cung_cap=None, tien_to: str | None = None, tab: bool = False,
            them: dict | None = None) -> dict[str, bytes]:
    """Module giả không cần cấu hình (dựa trên echo), manifest tuỳ biến."""
    tep = tep_echo(id_=id_, tien_to=tien_to or id_.replace("-", ""))
    m = json.loads(tep["manifest.json"])
    m["loai"] = loai
    if cung_cap is not None:
        m["cung_cap"] = cung_cap
    if not tab:
        m.pop("tab_web")
    m.update(them or {})
    tep["manifest.json"] = json.dumps(m, ensure_ascii=False).encode()
    return tep


def dat(du_lieu, id_: str, tep: dict) -> None:
    ghi_thu_muc(du_lieu / "modules" / id_, tep)


def tep_gemini() -> dict[str, bytes]:
    return {str(p.relative_to(GOC_GEMINI)): p.read_bytes() for p in GOC_GEMINI.rglob("*")
            if p.is_file() and "__pycache__" not in p.parts}


def tn(c) -> dict:
    r = c.get("/api/tinh-nang")  # MỞ: không cần khoá quản trị
    assert r.status_code == 200, r.text
    return r.json()


# ───────────── các ca của kế hoạch mục 7 ─────────────

def test_ca_0_module(du_lieu):
    with TestClient(app) as c:
        j = tn(c)
    assert j == {"modules": [], "loai": [], "kha_nang_da_cai": {}, "kha_nang_lien_ket": {}, "tab": [], "muc": []}


def test_ai_da_cai_chua_khoa_roi_lien_ket_roi_go(du_lieu):
    dat(du_lieu, "gemini-api", tep_gemini())       # có trường bắt buộc → đã cài, CHƯA liên kết
    dat(du_lieu, "chat-gia", tep_gia("chat-gia", cung_cap=["chat"]))
    with TestClient(app) as c:
        j = tn(c)
        loai = {x["ma"]: x for x in j["loai"]}
        assert loai["ai"]["so_da_cai"] == 2 and loai["ai"]["so_da_lien_ket"] == 0
        assert loai["ai"]["ten"] == "AI"           # từ `loai_ten` trong manifest của gemini-api, lõi không đặt
        assert j["kha_nang_da_cai"] == {"ai": 2, "chat": 2, "anh": 1, "video": 1}
        assert j["kha_nang_lien_ket"] == {}
        assert {m["id"]: m["da_lien_ket"] for m in j["modules"]} == {"chat-gia": False, "gemini-api": False}

        # Liên kết module giả (không cần khoá) → khả năng của nó vào tập đã liên kết; gemini vẫn chưa.
        assert c.post("/api/modules/chat-gia/cai", json={"cau_hinh": {}}, headers=QT).json()["ok"]
        j = tn(c)
        assert j["kha_nang_lien_ket"] == {"ai": 1, "chat": 1}
        assert {x["ma"]: x["so_da_lien_ket"] for x in j["loai"]} == {"ai": 1}

        # Gỡ → trở lại đúng như trước khi có nó.
        assert c.delete("/api/modules/chat-gia", headers=QT).status_code == 200
        j = tn(c)
        assert j["kha_nang_lien_ket"] == {} and j["kha_nang_da_cai"] == {"ai": 1, "chat": 1, "anh": 1, "video": 1}
        assert [m["id"] for m in j["modules"]] == ["gemini-api"]


def test_loai_la_kha_nang_la_va_muc_khong_sua_loi(du_lieu):
    muc = [{"id": "bang", "nhan": "Bảng định tuyến", "can": []},
           {"id": "tu-dong", "nhan": "Tự động điều chỉnh", "can": ["chat"]}]
    dat(du_lieu, "mang-gia", tep_gia("mang-gia", loai="mang", cung_cap=["dinh_tuyen"], tab=True,
                                     them={"loai_ten": "Mạng", "loai_thu_tu": 30, "muc": muc}))
    with TestClient(app) as c:
        j = tn(c)
        assert j["loai"] == [{"ma": "mang", "ten": "Mạng", "thu_tu": 30, "so_da_cai": 1, "so_da_lien_ket": 0}]
        assert j["tab"] == []                              # chưa liên kết → chưa có tab
        assert j["muc"] == [{"module": "mang-gia", "id": "bang", "nhan": "Bảng định tuyến", "can": []},
                            {"module": "mang-gia", "id": "tu-dong", "nhan": "Tự động điều chỉnh", "can": ["chat"]}]
        assert c.post("/api/modules/mang-gia/cai", json={"cau_hinh": {}}, headers=QT).json()["ok"]
        j = tn(c)
        assert j["kha_nang_lien_ket"] == {"mang": 1, "dinh_tuyen": 1}
        # Đường TƯƠNG ĐỐI (chạy được sau proxy ở thư mục con), không bắt đầu bằng «/».
        assert j["tab"] == [{"module": "mang-gia", "nhan": "Echo", "duong": "m/mang-gia/echo.dc.html"}]
        assert c.get("/" + j["tab"][0]["duong"]).status_code == 200


def test_thu_tu_loai_va_ten_loai(du_lieu):
    dat(du_lieu, "b-app", tep_gia("b-app", loai="app", them={"loai_ten": "App", "loai_thu_tu": 20}))
    dat(du_lieu, "a-ai", tep_gia("a-ai", loai="ai", them={"loai_ten": "AI", "loai_thu_tu": 10}))
    dat(du_lieu, "c-nha", tep_gia("c-nha", loai="nha"))     # không khai tên/thứ tự → đứng sau, tên = null
    with TestClient(app) as c:
        j = tn(c)
    assert [(x["ma"], x["ten"], x["thu_tu"]) for x in j["loai"]] == [("ai", "AI", 10), ("app", "App", 20),
                                                                      ("nha", None, None)]


def test_module_tu_xa_tinh_la_ai_co_chat(du_lieu, monkeypatch):
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    o = OpenAIGia()
    with MayChuThat(o.app) as s, TestClient(app) as c:
        r = c.post("/api/modules/tu-xa", headers=QT, json={"id": "may-gpu", "ten": "Máy GPU", "tien_to": "gpu",
                                                           "base_url": s.url + "/v1"})
        assert r.status_code == 200, r.text
        j = tn(c)
        assert j["modules"] == [{"id": "may-gpu", "ten": "Máy GPU", "loai": "ai", "cung_cap": ["chat"],
                                 "tien_to": "gpu", "tu_xa": True, "da_lien_ket": True}]
        assert j["kha_nang_lien_ket"] == {"ai": 1, "chat": 1}
        assert c.delete("/api/modules/may-gpu", headers=QT).status_code == 200
        assert tn(c)["modules"] == []


def test_khong_lo_bi_mat(du_lieu, monkeypatch):
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    o = OpenAIGia(khoa="sk-khoa-tu-xa-RIENG-7777")
    with MayChuThat(o.app) as s, TestClient(app) as c:
        c.post("/api/modules/tu-xa", headers=QT, json={"id": "may-gpu", "ten": "Máy GPU", "tien_to": "gpu",
                                                       "base_url": s.url + "/v1", "api_key": "sk-khoa-tu-xa-RIENG-7777"})
        van_ban = c.get("/api/tinh-nang").text
    assert "sk-khoa" not in van_ban and "7777" not in van_ban and "base_url" not in van_ban


# ───────────── bảng điều kiện của KHUNG (web/khung.js) trên dữ liệu thật ─────────────

def _khung(tn_: dict, bieu_thuc: str):
    """Chạy web/khung.js THẬT bằng node trên dữ liệu `/api/tinh-nang` thật; trả giá trị của `bieu_thuc` (JS, có K, tn)."""
    ma = (
        "const fs = require('fs'), vm = require('vm');"
        "const sb = {window: {}, location: {href: 'http://may/', replace() {}}, URL, console};"
        "vm.createContext(sb);"
        f"vm.runInContext(fs.readFileSync({json.dumps(str(GOC / 'web' / 'khung.js'))}, 'utf8'), sb);"
        f"const K = sb.window.Khung, tn = {json.dumps(tn_)};"
        f"process.stdout.write(JSON.stringify({bieu_thuc}));"
    )
    r = subprocess.run(["node", "-e", ma], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


TOM_TAT_KHUNG = """({
  studio: K.du('studio', tn), che_do: ['studio_chat', 'studio_anh', 'studio_video'].filter((m) => K.du(m, tn)),
  dung_duoc: ['studio_chat', 'studio_anh', 'studio_video'].filter((m) => K.dungDuoc(m, tn)),
  ai: K.du('ai', tn), tai_khoan: K.du('tai_khoan', tn), provider: K.du('provider', tn), combo: K.du('combo', tn),
  nav: K.navChinh(tn, '').map((x) => x.id), trang_dau: K.trangDau(tn)
})"""


@pytest.mark.skipif(not shutil.which("node"), reason="cần node")
def test_bang_dieu_kien_khung_tab_chinh_theo_da_cai(du_lieu):
    """Chủ máy 11/10: Studio (tab «chính» theo khả năng) hiện khi module ĐÃ CÀI, không đợi khoá; chỉ GỌI /v1 được
    khi đã liên kết. Provider vẫn đợi liên kết; Combo không bao giờ (chưa có backend)."""
    dat(du_lieu, "chat-gia", tep_gia("chat-gia", cung_cap=["chat", "anh"]))          # cài, CHƯA liên kết
    with TestClient(app) as c:
        rong = _khung(tn(c) | {"modules": [], "kha_nang_da_cai": {}, "kha_nang_lien_ket": {}},
                      "({studio: K.du('studio', tn), nav: K.navChinh(tn, '').map((x) => x.id), trang_dau: K.trangDau(tn)})")
        assert rong["studio"] is False and rong["nav"] == [] and rong["trang_dau"] == "He-thong.dc.html"

        j = tn(c)
        assert j["kha_nang_lien_ket"] == {}
        k = _khung(j, "K.du('studio', tn)")
        assert k is True, "module khai chat đã CÀI (chưa khoá) phải có Studio"
        k = _khung(j, TOM_TAT_KHUNG)
        assert k == {"studio": True, "che_do": ["studio_chat", "studio_anh"], "dung_duoc": [], "ai": True,
                     "tai_khoan": True, "provider": False, "combo": False, "nav": ["studio"],
                     "trang_dau": "Studio.dc.html"}, k
        # Câu «cần gì» của Studio nói về CÀI, không về liên kết.
        assert "cài một module có «chat»" in _khung(j | {"modules": [], "kha_nang_da_cai": {}}, "K.loiThieu('studio', tn)")
        # Module chưa có khoá cho một chế độ → đi đâu để thêm khoá: module có mã «ai» → Hệ thống → AI → Tài khoản.
        assert _khung(j, "K.chuaKhoaCho('studio_chat', tn).map((m) => m.id)") == ["chat-gia"]
        assert _khung(j, "K.noiThemKhoa(tn.modules[0], tn)") == {
            "tab": "tai_khoan", "href": "He-thong-AI.dc.html#tk-chat-gia", "nhan": "Hệ thống → AI → Tài khoản"}
        assert _khung(j, "K.trangChinhCua('chat-gia', tn)")["href"] == "Studio.dc.html"

        assert c.post("/api/modules/chat-gia/cai", json={"cau_hinh": {}}, headers=QT).json()["ok"]
        k = _khung(tn(c), TOM_TAT_KHUNG)
        assert k["dung_duoc"] == ["studio_chat", "studio_anh"] and k["provider"] is True and k["combo"] is False, k
        assert _khung(tn(c), "K.chuaKhoaCho('studio_chat', tn)") == []

        # Hai nguồn AI đã liên kết: đủ «≥ 2» nhưng Combo chưa có backend → vẫn không.
        dat(du_lieu, "chat-hai", tep_gia("chat-hai", cung_cap=["chat"]))
        assert c.post("/api/modules/nap-lai", headers=QT).status_code == 200
        assert c.post("/api/modules/chat-hai/cai", json={"cau_hinh": {}}, headers=QT).json()["ok"]
        assert _khung(tn(c), "K.du('combo', tn)") is False

        # Gỡ hết → trở lại như ca 0.
        for id_ in ("chat-gia", "chat-hai"):
            assert c.delete(f"/api/modules/{id_}", headers=QT).status_code == 200
        k = _khung(tn(c), TOM_TAT_KHUNG)
        assert k["studio"] is False and k["ai"] is False and k["nav"] == [], k


@pytest.mark.skipif(not shutil.which("node"), reason="cần node")
def test_khung_module_loai_la_khong_co_trang_chinh_va_them_khoa_o_trang_module(du_lieu):
    """Module loại LẠ (`mang`, khả năng `dinh_tuyen`): không ứng tab chính nào → cài xong ở lại trang Module; module
    không mang mã của tab Tài khoản thì thêm khoá ở bảng chi tiết của chính nó. Không bảng tên nào trong khung."""
    dat(du_lieu, "mang-gia", tep_gia("mang-gia", loai="mang", cung_cap=["dinh_tuyen"]))
    dat(du_lieu, "app-chat", tep_gia("app-chat", loai="app", cung_cap=["chat"]))
    with TestClient(app) as c:
        j = tn(c)
        assert _khung(j, "K.trangChinhCua('mang-gia', tn)") is None
        # Module loại «app» nhưng khai chat: vẫn ứng Studio (theo khả năng, không theo loại) …
        assert _khung(j, "K.trangChinhCua('app-chat', tn)")["href"] == "Studio.dc.html"
        # … còn khoá thì thêm ở bảng chi tiết (nó không mang mã của tab Tài khoản).
        assert _khung(j, "K.noiThemKhoa(tn.modules.find((m) => m.id === 'app-chat'), tn)") == {
            "tab": "module", "href": "He-thong.dc.html#module-app-chat", "nhan": "Hệ thống → Module"}


# ───────────── kiểm mẫu manifest ─────────────

def _manifest(**them):
    m = json.loads(tep_gia("x-gia")["manifest.json"])
    m.update(them)
    return m


@pytest.mark.parametrize("them,chu", [
    ({"loai": "AI"}, "loai"),                       # cùng mẫu với mã khả năng: `can` trộn hai loại mã
    ({"loai": "nha thong minh"}, "loai"),
    ({"loai_ten": ""}, "loai_ten"),
    ({"loai_ten": 5}, "loai_ten"),
    ({"loai_thu_tu": "1"}, "loai_thu_tu"),
    ({"loai_thu_tu": True}, "loai_thu_tu"),
    ({"muc": {"id": "a"}}, "muc"),
    ({"muc": [{"id": "a", "nhan": "A", "can": []}, {"id": "a", "nhan": "B", "can": []}]}, "muc"),
    ({"muc": [{"id": "a", "nhan": "A", "can": ["Chat"]}]}, "muc"),
    ({"muc": [{"id": "a", "nhan": "A"}]}, "muc"),
    ({"muc": [{"id": "../a", "nhan": "A", "can": []}]}, "muc"),
])
def test_manifest_sai_bi_tu_choi(them, chu):
    ly_do = kiem_manifest(_manifest(**them))
    assert ly_do and chu in ly_do, ly_do


def test_manifest_moi_hop_le():
    assert kiem_manifest(_manifest(loai="mang", loai_ten="Mạng", loai_thu_tu=-3,
                                   muc=[{"id": "a-1", "nhan": "A", "can": ["ai", "chat"]}])) is None
    assert kiem_manifest(json.loads((GOC_GEMINI / "manifest.json").read_text(encoding="utf-8"))) is None


# ───────────── tóm tắt module trên git ─────────────

def _cho_danh_sach(c, dieu_kien=bool, giay=10):
    import time
    han = time.monotonic() + giay
    while True:
        ds = c.get("/api/modules/thong-bao", headers=QT).json()["danh_sach"]
        if dieu_kien(ds) or time.monotonic() > han:
            return ds
        time.sleep(0.05)


CFG_GIT = {"repo": REPO, "nhanh": "main", "thu_muc": "modules", "chu_ky_giay": 1}


def test_tom_tat_git_co_loai_cung_cap_va_dung_luong(du_lieu, monkeypatch):
    gh = GitHubGia(REPO)
    tep = tep_gia("mang-gia", loai="mang", cung_cap=["dinh_tuyen"], them={"loai_ten": "Mạng", "loai_thu_tu": 30})
    gh.day(tep_kho({"mang-gia": tep}))
    co = sum(len(v) for v in tep.values())
    with MayChuThat(gh.app) as s:
        monkeypatch.setenv("WEB2API_GITHUB_API", s.url)
        with TestClient(app) as c:
            assert c.put("/api/nguon-git", headers=QT, json=CFG_GIT).status_code == 200
            ds = _cho_danh_sach(c)
    assert len(ds) == 1, ds
    x = ds[0]
    assert (x["trang_thai"], x["loai"], x["loai_ten"], x["loai_thu_tu"], x["cung_cap"]) == \
        ("moi", "mang", "Mạng", 30, ["dinh_tuyen"])
    assert x["dung_luong"] == co                     # cộng `size` của mọi blob trong thư mục module trên cây git
    assert x["mo_ta"] and isinstance(x["tai_nguyen"], dict)


def test_trang_thai_git_cu_thieu_truong_moi_duoc_doc_lai(du_lieu, monkeypatch):
    """Máy nâng cấp lõi: tệp trạng thái đồng bộ cũ (chưa có `loai`) cùng tree_sha — phải đọc lại manifest MỘT lần,
    không để ô «Chưa cài» nằm ngoài khu nào mãi tới khi module đổi."""
    gh = GitHubGia(REPO)
    gh.day(tep_kho({"mang-gia": tep_gia("mang-gia", loai="mang")}))
    with MayChuThat(gh.app) as s:
        monkeypatch.setenv("WEB2API_GITHUB_API", s.url)
        with TestClient(app) as c:
            c.put("/api/nguon-git", headers=QT, json=CFG_GIT)
            assert _cho_danh_sach(c)
        tep_tt = du_lieu / "nguon_git_trang_thai.json"
        tt = json.loads(tep_tt.read_text())
        for m in tt["modules"]:
            for k in ("loai", "loai_ten", "loai_thu_tu", "cung_cap", "dung_luong"):
                m.pop(k, None)
        tt["etag"] = None  # vòng sau đọc lại commit (cùng cây) thay vì 304
        tep_tt.write_text(json.dumps(tt))
        with TestClient(app) as c:
            ds = _cho_danh_sach(c, lambda d: d and d[0].get("loai") == "mang")
    assert ds and ds[0].get("loai") == "mang" and ds[0].get("dung_luong"), ds


# ───────────── lõi và khung không mang đặc thù nhà cung cấp ─────────────

def _ten_nha_cung_cap() -> set[str]:
    """Tên suy từ manifest của CHÍNH các module trong kho (không gõ tay): tiền tố, các đoạn của id, nhãn tên máy
    trong `mang_ra` (bỏ «apis» cuối: generativelanguage.googleapis.com → generativelanguage, google)."""
    ten = set()
    for p in (GOC / "modules").glob("*/manifest.json"):
        m = json.loads(p.read_text(encoding="utf-8"))
        ten.add(m["tien_to"].lower())
        ten |= {x for x in m["id"].lower().split("-") if len(x) >= 4 and x not in {"api", "apis"}}
        for may in m["tai_nguyen"].get("mang_ra", []):
            for nhan in may.lower().split(".")[:-1]:
                if len(nhan) >= 5:
                    ten.add(re.sub(r"apis?$", "", nhan))
    assert ten, "phải suy được ít nhất một tên từ modules/"
    return ten


def _chu_trong_ma_python(p: Path) -> list[str]:
    """Chuỗi hằng (trừ docstring) và định danh trong mã — chú thích và docstring không tính."""
    cay = ast.parse(p.read_text(encoding="utf-8"))
    docstring = set()
    for nut in ast.walk(cay):
        if isinstance(nut, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and nut.body:
            dau = nut.body[0]
            if isinstance(dau, ast.Expr) and isinstance(dau.value, ast.Constant) and isinstance(dau.value.value, str):
                docstring.add(id(dau.value))
    ra = []
    for nut in ast.walk(cay):
        if isinstance(nut, ast.Constant) and isinstance(nut.value, str) and id(nut) not in docstring:
            ra.append(nut.value)
        elif isinstance(nut, ast.Name):
            ra.append(nut.id)
        elif isinstance(nut, ast.Attribute):
            ra.append(nut.attr)
        elif isinstance(nut, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            ra.append(nut.name)
        elif isinstance(nut, ast.arg):
            ra.append(nut.arg)
        elif isinstance(nut, ast.keyword) and nut.arg:
            ra.append(nut.arg)
    return ra


def test_loi_khong_mang_dac_thu_nha_cung_cap():
    ten = _ten_nha_cung_cap()
    pham = []
    for p in sorted((GOC / "app").rglob("*.py")):
        for chu in _chu_trong_ma_python(p):
            for t in ten:
                if t in chu.lower():
                    pham.append(f"{p.relative_to(GOC)}: «{t}» trong {chu[:80]!r}")
    assert not pham, "lõi chứa đặc thù nhà cung cấp (phải nằm trong module):\n" + "\n".join(pham)


def test_khung_giao_dien_khong_mang_ten_module():
    ten = _ten_nha_cung_cap()
    # Phông chữ tải từ CDN fonts.googleapis.com/fonts.gstatic.com — CDN phông, không phải nhà cung cấp AI.
    cdn_phong = re.compile(r"https://fonts\.(googleapis|gstatic)\.com/[^\"'\s>]*")
    pham = []
    for p in sorted((GOC / "web").iterdir()):
        if not p.is_file():
            continue
        noi_dung = cdn_phong.sub("", p.read_text(encoding="utf-8")).lower()
        for t in ten:
            if t in noi_dung:
                pham.append(f"{p.name}: «{t}»")
    assert not pham, "khung giao diện chứa tên module/nhà cung cấp:\n" + "\n".join(pham)
