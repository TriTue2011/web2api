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
    r = c.get("/api/tinh-nang", headers=QT)
    assert r.status_code == 200, r.text
    return r.json()


# ───────────── các ca của kế hoạch mục 7 ─────────────

def test_ca_0_module(du_lieu):
    with TestClient(app) as c:
        j = tn(c)
    assert j == {"modules": [], "loai": [], "kha_nang_da_cai": {}, "kha_nang_lien_ket": {}, "tab": [], "muc": [],
                 "mui_gio": None, "phien": {"tong": 0, "can_dang_nhap": 0}, "co_combo": False}


# ───────────── múi giờ của nhà: biến TZ → `mui_gio` ─────────────

@pytest.mark.parametrize("tz, mong", [("Asia/Ho_Chi_Minh", "Asia/Ho_Chi_Minh"), (":Asia/Ho_Chi_Minh", "Asia/Ho_Chi_Minh"),
                                      (" America/New_York ", "America/New_York"), ("UTC", "UTC")])
def test_mui_gio_doc_tu_tz(du_lieu, monkeypatch, caplog, tz, mong):
    """Studio báo giờ theo múi giờ người vận hành khai bằng biến chuẩn TZ, không theo trình duyệt đang mở ở đâu."""
    monkeypatch.setenv("TZ", tz)
    with TestClient(app) as c:
        assert tn(c)["mui_gio"] == mong
    assert not [r for r in caplog.records if "TZ" in r.getMessage()]


@pytest.mark.parametrize("tz", ["Không/Có", "../etc", "Europe", "/etc/localtime", "ICT-7", "Asia/Ho_Chi_Minh\nGIA", "x" * 300])
def test_mui_gio_sai_thanh_null_loi_van_chay(du_lieu, monkeypatch, caplog, tz):
    """TZ sai (tên lạ, thoát thư mục, thư mục, đường tuyệt đối, dạng POSIX) → null + MỘT cảnh báo không chép lại giá
    trị; lõi vẫn khởi động và phục vụ bình thường."""
    monkeypatch.setenv("TZ", tz)
    with TestClient(app) as c:
        assert c.get("/health").json()["ok"] is True
        j = tn(c)
        tn(c)  # gọi lại: không cảnh báo theo từng yêu cầu
    assert j["mui_gio"] is None and j["modules"] == []
    canh_bao = [r.getMessage() for r in caplog.records if "TZ" in r.getMessage()]
    assert len(canh_bao) == 1 and tz.strip() not in canh_bao[0], canh_bao


@pytest.mark.parametrize("tz", [None, "", "   "])
def test_mui_gio_chua_dat_la_null_khong_canh_bao(du_lieu, monkeypatch, caplog, tz):
    if tz is not None:
        monkeypatch.setenv("TZ", tz)
    with TestClient(app) as c:
        assert tn(c)["mui_gio"] is None
    assert not [r for r in caplog.records if "TZ" in r.getMessage()]


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
                                 "tien_to": "gpu", "tu_xa": True, "da_lien_ket": True, "dang_nhap_tu_dong": False,
                                 "tai_dung_ho_so": False, "nhom": None}]
        assert j["kha_nang_lien_ket"] == {"ai": 1, "chat": 1}
        assert c.delete("/api/modules/may-gpu", headers=QT).status_code == 200
        assert tn(c)["modules"] == []


def test_khong_lo_bi_mat(du_lieu, monkeypatch):
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    o = OpenAIGia(khoa="sk-khoa-tu-xa-RIENG-7777")
    with MayChuThat(o.app) as s, TestClient(app) as c:
        c.post("/api/modules/tu-xa", headers=QT, json={"id": "may-gpu", "ten": "Máy GPU", "tien_to": "gpu",
                                                       "base_url": s.url + "/v1", "api_key": "sk-khoa-tu-xa-RIENG-7777"})
        van_ban = c.get("/api/tinh-nang", headers=QT).text
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
    khi đã liên kết. Provider cũng hiện từ lúc cài (tab rỗng chỉ chỗ thêm khoá — chủ máy: «cài AI rồi phải thấy có
    tài khoản, có provider»); Combo không bao giờ (chưa có backend)."""
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
                     "tai_khoan": True, "provider": True, "combo": False, "nav": ["studio"],
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
        monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")  # NV16: NguonGit ghim IP, máy chủ giả ở loopback
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
        monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")  # NV16: NguonGit ghim IP, máy chủ giả ở loopback
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
    trong `mang_ra` (bỏ «apis» cuối: generativelanguage.googleapis.com → generativelanguage, google).

    Ngoại lệ: các mảnh từ thông dụng (như «assistant», «phong») dùng chung qua MANH_TU_THONG_DUNG từ conftest.
    Tên đầy đủ của module và tiền tố vẫn bị quét đầy đủ.
    """
    from conftest import MANH_TU_THONG_DUNG
    ten = set()
    for p in (GOC / "modules").glob("*/manifest.json"):
        m = json.loads(p.read_text(encoding="utf-8"))
        # Tiền tố trùng nguyên từ thông dụng (NV163: module «dich», tien_to «dich»)
        # cũng loại như các mảnh của id — nếu không, mã lõi có sẵn từ trước
        # (LoiDichBiChan, gui_den_dich...) bị suy thành đặc thù nhà cung cấp.
        if m["tien_to"].lower() not in MANH_TU_THONG_DUNG:
            ten.add(m["tien_to"].lower())
        ten |= {x for x in m["id"].lower().split("-") if len(x) >= 4 and x not in {"api", "apis"} and x not in MANH_TU_THONG_DUNG}
        for may in m["tai_nguyen"].get("mang_ra", []):
            for nhan in may.lower().split(".")[:-1]:
                if len(nhan) >= 5:
                    # Cùng bộ miễn trừ mảnh thông dụng như id/tiền tố: «openapi.tuyaus.com» bị cắt «api» còn «open»,
                    # là tiền tố của chuẩn mở OpenAI (LoiOpenAI…) chứ không phải nhà cung cấp.
                    manh = re.sub(r"apis?$", "", nhan)
                    if manh not in MANH_TU_THONG_DUNG:
                        ten.add(manh)
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
    # NV121: mã khả năng chuẩn (HOP_DONG_KHA_NANG) là tên CHUNG của lõi
    # (như kenh_ra, thiet_bi, trinh_duyet) — lõi được phép nêu trong bảng hợp
    # đồng; không phải đặc thù nhà cung cấp. tien_to "ma_xac_minh" của module
    # mới trùng mã chuẩn nên loại khỏi quét; tên NCC thật (gmail, telegram,
    # zalo, mikrotik...) vẫn bị quét như cũ.
    from app.kham_pha import HOP_DONG_KHA_NANG
    ten = ten - set(HOP_DONG_KHA_NANG)
    # NV169: «open» là từ CHUNG (mảnh của «OpenAPI» — chuẩn mở, không phải tên
    # nhà cung cấp): mang_ra «openapi.tuyaus.com» của module khoa-cua bị cắt
    # hậu tố api → «open», rồi khớp LoiOpenAI/OpenAI đã có khắp lõi từ ngày
    # đầu. Loại riêng như «gmail» ở test khung dưới; tên NCC thật (tuya...)
    # vẫn bị quét như cũ.
    ten = ten - {"open"}
    pham = []
    for p in sorted((GOC / "app").rglob("*.py")):
        for chu in _chu_trong_ma_python(p):
            for t in ten:
                if t in chu.lower():
                    pham.append(f"{p.relative_to(GOC)}: «{t}» trong {chu[:80]!r}")
    assert not pham, "lõi chứa đặc thù nhà cung cấp (phải nằm trong module):\n" + "\n".join(pham)


def test_khung_giao_dien_khong_mang_ten_module():
    ten = _ten_nha_cung_cap()
    # NV121: «gmail» xuất hiện trong placeholder MẪU CHUNG của ô dán tài khoản
    # hàng loạt (ten@gmail.com|mat-khau — có từ NV78, trước NV121): là miền ví
    # dụ chung, không phải tên nhà cung cấp cứng trong khung giao diện.
    # mang_ra "imap.gmail.com" của module mới đưa "gmail" vào danh sách quét
    # nên loại riêng ở đây; các tên NCC khác vẫn bị quét như cũ.
    ten = ten - {"gmail"}
    # NV169: «open» — xem chú thích ở test_loi_khong_mang_dac_thu_nha_cung_cap
    # (mảnh chung của «OpenAPI», khớp thuộc tính open/js thông thường).
    ten = ten - {"open"}
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


# ───────────── NV65: tab_web nằm trong thanh Hệ thống, không ở thanh chính ─────────────

@pytest.mark.skipif(not shutil.which("node"), reason="cần node")
def test_tab_web_thanh_he_thong_khong_o_thanh_chinh_va_dung_thu_tu():
    """Hợp đồng mới (NV65): tab_web của module đã liên kết nằm trong thanh Hệ thống,
    không đứng ở thanh trên cùng (navChinh) hay thanh dưới (navDuoi).
    (a) thanh trên cùng và thanh dưới không chứa mục `m-…`;
    (b) thanh Hệ thống chứa mục `m-<module>` đúng thứ tự (sau Module, trước Bảo mật & người dùng);
    (d) vai thường (user) và 403 không thấy tab module."""
    tn_co_tab = {
        "modules": [{"id": "mod-demo", "ten": "Demo", "da_lien_ket": True}],
        "kha_nang_da_cai": {"chat": 1},
        "kha_nang_lien_ket": {"chat": 1},
        "tab": [{"module": "mod-demo", "nhan": "Bàn điều khiển", "duong": "m/mod-demo/index.html"}],
    }
    # (a) Kiểm tra thanh trên và thanh dưới không chứa mục m-...
    kq_chinh = _khung(tn_co_tab, "K.navChinh(tn, '').map((x) => x.id)")
    assert not any(x.startswith("m-") for x in kq_chinh), f"navChinh chứa m-*: {kq_chinh}"
    assert "m-mod-demo" not in kq_chinh

    kq_duoi = _khung(tn_co_tab, "K.navDuoi(tn, '').map((x) => x.id)")
    assert not any(x.startswith("m-") for x in kq_duoi), f"navDuoi chứa m-*: {kq_duoi}"
    assert "m-mod-demo" not in kq_duoi

    # (b) Kiểm tra thanh Hệ thống chứa m-mod-demo đúng thứ tự: sau 'module', trước 'bao_mat'
    kq_ht = _khung(tn_co_tab, "K.navHeThong(tn, '', 0)")
    ids_ht = [x["id"] for x in kq_ht]
    assert "m-mod-demo" in ids_ht, f"navHeThong thiếu m-mod-demo: {ids_ht}"
    idx_mod = ids_ht.index("module")
    idx_tab = ids_ht.index("m-mod-demo")
    idx_bm = ids_ht.index("bao_mat")
    assert idx_mod < idx_tab < idx_bm, f"Thứ tự navHeThong sai: {ids_ht}"

    # Kiểm tra chi tiết mục m-mod-demo
    muc_tab = next(x for x in kq_ht if x["id"] == "m-mod-demo")
    assert muc_tab["nhan"] == "Bàn điều khiển"
    assert muc_tab["href"] == "He-thong-Module.dc.html?m=mod-demo"
    assert muc_tab["cham"] is False
    assert muc_tab["con"] == []
    assert muc_tab["ac"] == "false"

    # Aria-current chỉ là 'page' khi hienTai khớp
    kq_ht_active = _khung(tn_co_tab, "K.navHeThong(tn, 'm-mod-demo', 0)")
    muc_active = next(x for x in kq_ht_active if x["id"] == "m-mod-demo")
    assert muc_active["ac"] == "page"
    muc_mod_active = next(x for x in kq_ht_active if x["id"] == "module")
    assert muc_mod_active["ac"] == "false"

    # (d) Vai thường (user) và 403 không thấy tab module
    # TH1: vai user
    ma_user = (
        "const fs = require('fs'), vm = require('vm');"
        "const fn = async () => ({ ok: true, status: 200, json: async () => ({ id: 'u1', vai: 'user' }) });"
        "const sb = {fetch: fn, window: {}, location: {href: 'http://may/', replace() {}}, URL, console};"
        "vm.createContext(sb);"
        f"vm.runInContext(fs.readFileSync({json.dumps(str(GOC / 'web' / 'khung.js'))}, 'utf8'), sb);"
        f"const K = sb.window.Khung, tn = {json.dumps(tn_co_tab)};"
        "K.napToi().then(() => {"
        "  const ht = K.navHeThong(tn, '', 0);"
        "  process.stdout.write(JSON.stringify(ht));"
        "});"
    )
    r_u = subprocess.run(["node", "-e", ma_user], capture_output=True, text=True, timeout=15)
    assert r_u.returncode == 0, r_u.stderr
    ht_user = json.loads(r_u.stdout)
    assert [x["id"] for x in ht_user] == ["bao_mat"], f"Vai user phải chỉ thấy bao_mat: {ht_user}"

    # TH2: tn bị 403
    tn_403 = dict(tn_co_tab, ma=403, error={"message": "Từ chối"})
    kq_403 = _khung(tn_403, "K.navHeThong(tn, '', 0)")
    assert [x["id"] for x in kq_403] == ["bao_mat"], f"Khi 403 phải chỉ thấy bao_mat: {kq_403}"


def test_module_tab_web_la_tu_hien_va_go_bien_mat(du_lieu):
    """(c) Thả module mẫu mang tab_web LẠ vào → tab tự hiện trong Hệ thống;
    gỡ module → tab biến mất. Lõi không sửa dòng nào."""
    mod_id = "mod-la-nv65"
    mod_dir = du_lieu / "modules" / mod_id
    mod_dir.mkdir(parents=True)
    manifest = {
        "id": mod_id,
        "hang": "A",
        "ten": "Module Lạ NV65",
        "mo_ta": "Mô tả module lạ NV65",
        "phien_ban": "1.0.0",
        "loai": "tien_ich",
        "giao_thuc": "openai",
        "tien_to": "la65",
        "truong_cau_hinh": [],
        "tai_nguyen": {"mang_ra": []},
        "tab_web": {"nhan": "Tab Lạ 65", "trang": "web/trang.html"},
    }
    (mod_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    ma_code = """class Module:
    def __init__(self, m): self.manifest = m; self.tien_to = m["tien_to"]
    def nap(self, cfg): pass
    async def cai(self, cfg): return {"ok": True, "ket_qua_tung_khoa": [], "loi": None, "cau_hinh_luu": {}}
    async def suc_khoe(self): return {"ok": True}
    async def dung(self): pass
"""
    (mod_dir / "module.py").write_text(ma_code, encoding="utf-8")

    with TestClient(app) as c:
        assert c.post("/api/modules/nap-lai", headers=QT).status_code == 200

        # Khi chưa liên kết: tab chưa hiện trong tn.tab
        tn_chua_lk = c.get("/api/tinh-nang", headers=QT).json()
        assert not any(t["module"] == mod_id for t in tn_chua_lk.get("tab", []))

        # Liên kết module
        r_cai = c.post(f"/api/modules/{mod_id}/cai", json={"cau_hinh": {}}, headers=QT)
        assert r_cai.status_code == 200 and r_cai.json()["ok"] is True

        tn_da_lk = c.get("/api/tinh-nang", headers=QT).json()
        assert any(t["module"] == mod_id and t["nhan"] == "Tab Lạ 65" for t in tn_da_lk.get("tab", []))

        if shutil.which("node"):
            # Thanh trên cùng không có m-mod-la-nv65
            kq_chinh = _khung(tn_da_lk, "K.navChinh(tn, '').map((x) => x.id)")
            assert f"m-{mod_id}" not in kq_chinh

            # Thanh Hệ thống tự hiện m-mod-la-nv65
            kq_ht = _khung(tn_da_lk, "K.navHeThong(tn, '', 0)")
            ids_ht = [x["id"] for x in kq_ht]
            assert f"m-{mod_id}" in ids_ht
            assert ids_ht.index("module") < ids_ht.index(f"m-{mod_id}") < ids_ht.index("bao_mat")
            tab_obj = next(x for x in kq_ht if x["id"] == f"m-{mod_id}")
            assert tab_obj["nhan"] == "Tab Lạ 65"

        # Gỡ module -> tab biến mất hoàn toàn
        r_del = c.delete(f"/api/modules/{mod_id}", headers=QT)
        assert r_del.status_code == 200

        tn_sau_go = c.get("/api/tinh-nang", headers=QT).json()
        assert not any(t["module"] == mod_id for t in tn_sau_go.get("tab", []))

        if shutil.which("node"):
            kq_ht_sau = _khung(tn_sau_go, "K.navHeThong(tn, '', 0)")
            ids_ht_sau = [x["id"] for x in kq_ht_sau]
            assert f"m-{mod_id}" not in ids_ht_sau

