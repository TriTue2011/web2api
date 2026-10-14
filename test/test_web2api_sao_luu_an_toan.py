"""Khôi phục cấu hình KHÔNG được thành đường thực thi mã / đổi hướng khoá / làm chết khoá khách.

Mỗi test tái hiện một kịch bản người soát độc lập đã chứng minh bằng mã chạy thật:
- PoC 1: `nguon_git.json` độc (repo «../../evil/x», tự cài bật, tắt chữ ký) — chế độ `kiem` báo hợp lệ, `ap_dung` ghi.
- Cờ an toàn của nguồn git (`tu_dong`, `yeu_cau_chu_ky`) không được NỚI qua khôi phục; đổi repo bị GIỮ.
- `tu_xa.json` đổi `base_url` trong khi khoá cũ `bi_mat/<id>.json` vẫn trên máy → khoá cũ bị gửi sang máy lạ.
- `modules.json` đổi cấu hình công khai (vd base_url) khi khoá cũ còn trên máy — cùng lớp với tu_xa.
- PoC A: `khoa_khach.json` sai hình dạng làm chết im lặng mọi khoá khách cũ mà API vẫn trả ok.
- PoC B: lỗi ghi giữa chừng để trạng thái nửa vời; trạng thái sau lỗi phải BẰNG trạng thái trước (so từng tệp).
- Bom nén / tham số scrypt do người tải lên điều khiển: từ chối TRƯỚC khi giải nén / băm.
Lõi thật (lifespan) với thư mục ảnh rỗng: sổ module và nguồn git là bản thật, không giả.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import struct
import tarfile
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import khoa_khach, sao_luu, so_module
from app.main import app

KHOA_QT = "khoa-quan-tri-test-0123456789abcdef"
QT = {"Authorization": f"Bearer {KHOA_QT}"}
MAT_KHAU = "MatKhauSaoLuu123456!"
KHOA_API_GIA = "AIzaSyUI-TOT-0000000000000000000000tot1"
KHOA_KHACH_THO = "w2a_khoa-khach-gia-0000000000000000000000000"


def bam(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def ban_ghi_khoa(id_: str, khoa_tho: str, pham_vi=None) -> dict:
    return {"id": id_, "nhan": f"Khoá {id_}", "bat": True, "pham_vi": pham_vi or ["*"],
            "tao_luc": "2026-10-13T00:00:00+00:00", "dung_cuoi": None, "so_luot": 0, "bam": bam(khoa_tho)}


def tao_ban(tep: dict, phien_ban: int = 1, che_do_tar: str = "w") -> bytes:
    """Bản sao lưu KHÔNG mã hoá dựng tay (đúng định dạng ứng dụng): giá trị là bytes hoặc đối tượng JSON."""
    noi_dung = {k: (v if isinstance(v, bytes) else json.dumps(v).encode("utf-8")) for k, v in tep.items()}
    man = {"phien_ban": phien_ban, "tao_luc": "2026-10-13T00:00:00+00:00",
           "tep": [{"duong": k, "sha256": hashlib.sha256(v).hexdigest(), "kich_thuoc": len(v)}
                   for k, v in noi_dung.items()],
           "modules_git": [], "co_bi_mat": False, "co_phien": False}
    bio = io.BytesIO()
    with tarfile.open(fileobj=bio, mode=che_do_tar) as tf:
        for k, v in [("manifest.json", json.dumps(man).encode("utf-8"))] + list(noi_dung.items()):
            ti = tarfile.TarInfo(k)
            ti.size = len(v)
            tf.addfile(ti, io.BytesIO(v))
    return sao_luu.MAGIC + bytes([1, sao_luu.KIEU_THO]) + bio.getvalue()


def trang_thai_dia(goc: Path) -> dict[str, str]:
    """sha256 từng tệp cấu hình/bí mật (bỏ nhật ký, admin_key và chính các bản .truoc-khoi-phuc-*)."""
    ra = {}
    for p in sorted(goc.rglob("*")):
        rel = p.relative_to(goc)
        if rel.parts[0].startswith(".truoc-khoi-phuc-") or rel.parts[0] in ("nhat_ky", "admin_key"):
            continue
        if p.is_file():
            ra[rel.as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return ra


def kiem(client, ban: bytes, mat_khau: str | None = None):
    data = {"che_do": "kiem"}
    if mat_khau:
        data["mat_khau"] = mat_khau
    return client.post("/api/khoi-phuc", headers=QT, data=data,
                       files={"tep": ("b.w2a", ban, "application/octet-stream")})


def ap_dung(client, ban: bytes, mat_khau: str | None = None, sha: str | None = "dung"):
    data = {"che_do": "ap_dung"}
    if mat_khau:
        data["mat_khau"] = mat_khau
    if sha == "dung":
        data["sha256"] = hashlib.sha256(ban).hexdigest()
    elif sha is not None:
        data["sha256"] = sha
    return client.post("/api/khoi-phuc", headers=QT, data=data,
                       files={"tep": ("b.w2a", ban, "application/octet-stream")})


def ghi(p: Path, du_lieu, quyen: int = 0o644) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(du_lieu), encoding="utf-8")
    os.chmod(p, quyen)


@pytest.fixture
def goc(tmp_path, monkeypatch):
    d = tmp_path / "data"
    d.mkdir()
    os.chmod(d, 0o700)
    monkeypatch.setenv("WEB2API_DATA", str(d))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")
    khoa_khach.lam_moi_cache()
    yield d
    khoa_khach.lam_moi_cache()


NGUON_HOP_LE = {"repo": "TriTue2011/web2api", "nhanh": "main", "thu_muc": "modules", "chu_ky_giay": 300,
                "tu_dong": False, "yeu_cau_chu_ky": True}


# ───────────── điểm 1: mỗi tệp đi qua bộ kiểm của CHÍNH module sở hữu ─────────────

def test_poc1_nguon_git_doc_bi_tu_choi_ca_hai_che_do(goc):
    """PoC 1: repo «../../evil/x», chu_ky_giay 0 — `kiem` không được báo hợp lệ, `ap_dung` không được ghi."""
    doc = {"repo": "../../evil/x", "nhanh": "main", "thu_muc": "modules", "chu_ky_giay": 0,
           "tu_dong": True, "yeu_cau_chu_ky": False}
    with TestClient(app) as c:
        ban = tao_ban({"nguon_git.json": doc})
        truoc = trang_thai_dia(goc)
        r = kiem(c, ban)
        assert r.status_code == 400, r.text
        assert r.json()["error"]["code"] == "sao_luu_khong_hop_le"
        assert "nguon_git.json" in r.json()["error"]["message"]
        r = ap_dung(c, ban)
        assert r.status_code == 400, r.text
        assert trang_thai_dia(goc) == truoc
        assert not (goc / "nguon_git.json").exists()


def test_nguon_git_khoi_phuc_khong_noi_co_an_toan(goc):
    """Cùng nguồn: tu_dong KHÔNG được bật lên, yeu_cau_chu_ky KHÔNG được tắt đi; trường khác vẫn khôi phục."""
    ghi(goc / "nguon_git.json", NGUON_HOP_LE)
    ban = tao_ban({"nguon_git.json": {**NGUON_HOP_LE, "tu_dong": True, "yeu_cau_chu_ky": False,
                                      "chu_ky_giay": 900}})
    with TestClient(app) as c:
        r = kiem(c, ban)
        assert r.status_code == 200, r.text
        kb = r.json()["khac_biet"]["nguon_git"]
        assert kb["hien_tai"]["repo"] == "TriTue2011/web2api"
        assert kb["ban_sao_luu"]["tu_dong"] is True
        assert kb["se_ghi"]["tu_dong"] is False and kb["se_ghi"]["yeu_cau_chu_ky"] is True
        giu = {(g["tep"], g["truong"]) for g in r.json()["giu_lai"]}
        assert {("nguon_git.json", "tu_dong"), ("nguon_git.json", "yeu_cau_chu_ky")} <= giu
        r = ap_dung(c, ban)
        assert r.status_code == 200, r.text
        cfg = json.loads((goc / "nguon_git.json").read_text())
        assert cfg["tu_dong"] is False
        assert cfg["yeu_cau_chu_ky"] is True
        assert cfg["chu_ky_giay"] == 900
        assert c.get("/api/nguon-git", headers=QT).json()["cau_hinh"]["tu_dong"] is False


def test_nguon_git_chua_co_thi_co_khong_bat_len(goc):
    """Máy chưa cấu hình nguồn git: bản sao lưu mang tu_dong=true thì vẫn ghi tu_dong=false (mặc định an toàn)."""
    ban = tao_ban({"nguon_git.json": {**NGUON_HOP_LE, "tu_dong": True, "yeu_cau_chu_ky": False}})
    with TestClient(app) as c:
        r = ap_dung(c, ban)
        assert r.status_code == 200, r.text
    cfg = json.loads((goc / "nguon_git.json").read_text())
    assert cfg["tu_dong"] is False
    assert cfg["repo"] == "TriTue2011/web2api"


def test_nguon_git_doi_repo_bi_giu_nguyen(goc):
    """Đổi repo = lần đồng bộ ĐẦU của nguồn mới tự cập nhật module đã cài (không cần tu_dong) → khôi phục GIỮ nguồn cũ."""
    ghi(goc / "nguon_git.json", NGUON_HOP_LE)
    ban = tao_ban({"nguon_git.json": {**NGUON_HOP_LE, "repo": "ke-xau/x"},
                   "dia_chi.json": {"url": "https://w2a.example.com"}})
    with TestClient(app) as c:
        r = kiem(c, ban)
        assert r.status_code == 200, r.text
        j = r.json()
        assert "nguon_git.json" in j["se_bo"] and "nguon_git.json" not in j["se_ap_dung"]
        assert any(g["tep"] == "nguon_git.json" and g["truong"] == "repo" for g in j["giu_lai"])
        r = ap_dung(c, ban)
        assert r.status_code == 200, r.text
        assert "nguon_git.json" in r.json()["da_bo"]
    assert json.loads((goc / "nguon_git.json").read_text())["repo"] == "TriTue2011/web2api"
    assert json.loads((goc / "dia_chi.json").read_text())["url"] == "https://w2a.example.com"


def test_nguon_git_dat_moi_khi_da_co_module_tu_git_bi_giu(goc):
    """Chưa có nguồn nhưng <DATA>/modules/ đã có module: đặt nguồn từ bản sao lưu cũng là «lần đầu» tự cập nhật → giữ."""
    (goc / "modules" / "mod-git").mkdir(parents=True)
    ban = tao_ban({"nguon_git.json": {**NGUON_HOP_LE, "repo": "ke-xau/x"}})
    with TestClient(app) as c:
        r = ap_dung(c, ban)
        assert r.status_code == 200, r.text
        assert "nguon_git.json" in r.json()["da_bo"]
    assert not (goc / "nguon_git.json").exists()


TU_XA_CU = {"id": "xa1", "ten": "Máy GPU", "tien_to": "xa1", "base_url": "http://192.168.1.220:8000/v1",
            "cai_luc": "2026-10-13T00:00:00+00:00"}


def test_tu_xa_doi_base_url_khi_con_khoa_cu_bi_tu_choi(goc):
    """Bản KHÔNG mật khẩu đổi base_url của module từ xa đang có khoá: khoá cũ sẽ bị gửi sang máy lạ → 409, đĩa không đổi."""
    ghi(goc / "tu_xa.json", {"modules": [TU_XA_CU]})
    ghi(goc / "bi_mat" / "xa1.json", {"api_key": KHOA_API_GIA}, 0o600)
    os.chmod(goc / "bi_mat", 0o700)
    ban = tao_ban({"tu_xa.json": {"modules": [{**TU_XA_CU, "base_url": "http://192.168.1.99:8000/v1"}]}})
    with TestClient(app) as c:
        r = kiem(c, ban)
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["co_the_ap_dung"] is False
        assert any(t["tep"] == "tu_xa.json" and t["id"] == "xa1" for t in j["tu_choi"])
        muc = next(m for m in j["khac_biet"]["tu_xa"] if m["id"] == "xa1")
        assert muc["base_url"] == "http://192.168.1.99:8000/v1"
        assert muc["base_url_hien_tai"] == "http://192.168.1.220:8000/v1"
        truoc = trang_thai_dia(goc)
        r = ap_dung(c, ban)
        assert r.status_code == 409, r.text
        assert trang_thai_dia(goc) == truoc
        assert KHOA_API_GIA not in r.text


def test_tu_xa_base_url_ssrf_bi_tu_choi(goc):
    """base_url khôi phục phải qua kiem_url_lan như đường đăng ký thường (metadata đám mây bị chặn)."""
    ban = tao_ban({"tu_xa.json": {"modules": [{**TU_XA_CU, "base_url": "http://169.254.169.254/latest"}]}})
    with TestClient(app) as c:
        assert kiem(c, ban).status_code == 400
        assert ap_dung(c, ban).status_code == 400
    assert not (goc / "tu_xa.json").exists()


def test_tu_xa_khoi_phuc_chay_ngay_khong_can_khoi_dong_lai(goc):
    """6b: module từ xa vừa khôi phục phải phục vụ ngay (sổ nạp lại tu_xa), không chỉ ghi đĩa rồi báo thành công."""
    ban = tao_ban({"tu_xa.json": {"modules": [TU_XA_CU]}})
    with TestClient(app) as c:
        r = ap_dung(c, ban)
        assert r.status_code == 200, r.text
        so = c.app.state.so
        assert "xa1" in so.tu_xa and "xa1" in so.dang_chay
        assert so.tim_theo_tien_to("xa1") == "xa1"


def test_tu_xa_trung_tien_to_trong_tep_bi_tu_choi(goc):
    ban = tao_ban({"tu_xa.json": {"modules": [TU_XA_CU, {**TU_XA_CU, "id": "xa2"}]}})
    with TestClient(app) as c:
        assert kiem(c, ban).status_code == 400


def test_modules_doi_cau_hinh_cong_khai_khi_con_khoa_cu_bi_tu_choi(goc):
    """Cùng lớp với tu_xa: modules.json đổi giá trị công khai (base_url proxy) mà khoá cũ của module vẫn ở bi_mat/."""
    cu = {"id": "mod-a", "cai_luc": "2026-10-13T00:00:00+00:00",
          "cau_hinh_cong_khai": {"base_url": "https://proxy-cua-toi.example/v1", "khoa": "…tot1"}}
    ghi(goc / "modules.json", {"modules": [cu]})
    ghi(goc / "bi_mat" / "mod-a.json", {"khoa": KHOA_API_GIA}, 0o600)
    moi = {**cu, "cau_hinh_cong_khai": {"base_url": "https://ke-xau.example/v1", "khoa": "…tot1"}}
    ban = tao_ban({"modules.json": {"modules": [moi]}})
    with TestClient(app) as c:
        r = kiem(c, ban)
        assert r.status_code == 200, r.text
        assert r.json()["co_the_ap_dung"] is False
        muc = next(m for m in r.json()["khac_biet"]["modules"] if m["id"] == "mod-a")
        assert "base_url" in muc["truong_doi"]
        truoc = trang_thai_dia(goc)
        assert ap_dung(c, ban).status_code == 409
        assert trang_thai_dia(goc) == truoc


@pytest.mark.parametrize("tep,du_lieu", [
    ("modules.json", "x"),
    ("modules.json", {"modules": [{"id": "../x", "cai_luc": "x", "cau_hinh_cong_khai": {}}]}),
    ("modules.json", {"modules": [{"id": "mod-a", "cai_luc": "x", "cau_hinh_cong_khai": "khong-phai-dict"}]}),
    ("tu_xa.json", {"modules": "x"}),
    ("combo.json", {"bad name!": {"buoc": [{"model": "a/b"}]}}),
    ("combo.json", {"cb-1": {"buoc": []}}),
    ("dia_chi.json", {"url": "javascript:alert(1)"}),
    ("dia_chi.json", "x"),
    ("model.json", {"mod-a": {"tat": "khong-phai-list", "mac_dinh": {}}}),
    ("model.json", []),
    ("khoa_khach.json", {"khoa": "x"}),
    ("khoa_khach.json", {"khoa": [{"id": "kk_1", "bam": "khong-phai-hex"}]}),
    ("khoa_khach.json", {"khoa": [{**ban_ghi_khoa("kk_1", "a"), "pham_vi": "*"}]}),
    ("khoa_khach.json", {"khoa": [{k: v for k, v in ban_ghi_khoa("kk_1", "a").items() if k != "pham_vi"}]}),
    ("khoa_khach.json", {"khoa": [{**ban_ghi_khoa("kk_1", "a"), "bat": "true"}]}),
    ("khoa_khach.json", {"khoa": [ban_ghi_khoa("kk_1", "a"), ban_ghi_khoa("kk_1", "b")]}),
])
def test_sai_hinh_dang_bi_tu_choi_truoc_khi_ghi(goc, tep, du_lieu):
    """JSON hợp lệ + sha256 đúng nhưng SAI HÌNH DẠNG theo module sở hữu → 400 ở cả kiem lẫn ap_dung, không ghi gì."""
    ghi(goc / "khoa_khach.json", {"khoa": [ban_ghi_khoa("kk_cu", KHOA_KHACH_THO)]}, 0o600)
    with TestClient(app) as c:
        ban = tao_ban({tep: du_lieu})
        truoc = trang_thai_dia(goc)
        r = kiem(c, ban)
        assert r.status_code == 400, r.text
        assert tep in r.json()["error"]["message"]
        assert ap_dung(c, ban).status_code == 400
        assert trang_thai_dia(goc) == truoc
        assert khoa_khach.tim_theo_khoa_tho(KHOA_KHACH_THO) is not None


def test_poca_khoa_khach_sai_hinh_dang_khong_lam_chet_khoa_cu(goc):
    """PoC A: {"khoa": "x"} từng làm khoá cũ chết im lặng mà API trả ok:true."""
    ghi(goc / "khoa_khach.json", {"khoa": [ban_ghi_khoa("kk_cu", KHOA_KHACH_THO)]}, 0o600)
    with TestClient(app) as c:
        assert c.get("/v1/models", headers={"Authorization": f"Bearer {KHOA_KHACH_THO}"}).status_code == 200
        r = ap_dung(c, tao_ban({"khoa_khach.json": {"khoa": "x"}}))
        assert r.status_code == 400
        assert c.get("/v1/models", headers={"Authorization": f"Bearer {KHOA_KHACH_THO}"}).status_code == 200


def test_kiem_hien_khoa_khach_them_bot_va_doi_bam(goc):
    """Khoá thêm với `bam` tự chọn chính là cửa hậu /v1 → kiem phải liệt kê thêm / đổi băm / sẽ mất, kèm phạm vi."""
    ghi(goc / "khoa_khach.json", {"khoa": [ban_ghi_khoa("kk_cu", KHOA_KHACH_THO),
                                           ban_ghi_khoa("kk_mat", "khoa-se-mat")]}, 0o600)
    ban = tao_ban({"khoa_khach.json": {"khoa": [
        ban_ghi_khoa("kk_cu", KHOA_KHACH_THO, ["chat"]),
        ban_ghi_khoa("kk_moi", "khoa-ke-xau-chon", ["*"]),
    ]}})
    with TestClient(app) as c:
        r = kiem(c, ban)
        assert r.status_code == 200, r.text
        kk = r.json()["khac_biet"]["khoa_khach"]
        assert kk["so_khoa"] == 2
        theo_id = {k["id"]: k for k in kk["khoa"]}
        assert theo_id["kk_moi"]["trang_thai"] == "moi" and theo_id["kk_moi"]["pham_vi"] == ["*"]
        assert theo_id["kk_cu"]["trang_thai"] == "doi" and theo_id["kk_cu"]["pham_vi"] == ["chat"]
        assert kk["se_mat"] == ["kk_mat"]
        assert "bam" not in json.dumps(kk)


def test_ap_dung_bat_buoc_sha256_cua_ban_da_kiem(goc):
    """Áp dụng chỉ nhận đúng tệp đã «Kiểm tra»: thiếu hoặc lệch sha256 → 409, không ghi."""
    ban = tao_ban({"dia_chi.json": {"url": "https://w2a.example.com"}})
    with TestClient(app) as c:
        r = kiem(c, ban)
        assert r.json()["sha256"] == hashlib.sha256(ban).hexdigest()
        assert ap_dung(c, ban, sha=None).status_code == 409
        assert ap_dung(c, ban, sha="0" * 64).status_code == 409
        assert not (goc / "dia_chi.json").exists()
        assert ap_dung(c, ban).status_code == 200
    assert (goc / "dia_chi.json").exists()


# ───────────── điểm 2: nguyên tử, hoàn tác, kiểm chứng sau ghi ─────────────

def test_pocb_loi_giua_chung_hoan_tac_ve_dung_trang_thai_truoc(goc, monkeypatch):
    """PoC B: os.replace hỏng ở tệp thứ hai → mọi tệp đã ghi được phục hồi; trạng thái sau = trước, từng tệp."""
    ghi(goc / "combo.json", {"cu": {"ten": "cu", "mo_ta": "", "kha_nang": ["chat"],
                                    "buoc": [{"model": "a/b", "bat": True}]}}, 0o600)
    ghi(goc / "model.json", {"mod-a": {"tat": ["x"], "mac_dinh": {}}})
    ghi(goc / "khoa_khach.json", {"khoa": [ban_ghi_khoa("kk_cu", KHOA_KHACH_THO)]}, 0o600)
    ban = tao_ban({
        "combo.json": {"moi": {"ten": "moi", "mo_ta": "", "kha_nang": ["chat"], "buoc": [{"model": "c/d", "bat": True}]}},
        "dia_chi.json": {"url": "https://w2a.example.com"},
        "model.json": {"mod-b": {"tat": [], "mac_dinh": {}}},
    })
    with TestClient(app) as c:
        truoc = trang_thai_dia(goc)
        goc_replace = os.replace
        dem = {"n": 0}

        def replace_hong_mot_lan(src, dst, *a, **k):
            if str(dst).startswith(str(goc)) and ".truoc-khoi-phuc-" not in str(dst):
                dem["n"] += 1
                if dem["n"] == 2:
                    raise OSError("đĩa đầy giả")
            return goc_replace(src, dst, *a, **k)

        monkeypatch.setattr(os, "replace", replace_hong_mot_lan)
        r = ap_dung(c, ban)
        monkeypatch.setattr(os, "replace", goc_replace)
        assert r.status_code == 500, r.text
        assert r.json()["error"]["code"] == "khoi_phuc_da_hoan_tac"
        assert dem["n"] >= 2
        assert trang_thai_dia(goc) == truoc
        assert not (goc / "dia_chi.json").exists()
        assert not list(goc.rglob("*.tam"))
        assert khoa_khach.tim_theo_khoa_tho(KHOA_KHACH_THO) is not None


def test_kiem_chung_khoa_khach_sau_ghi_hong_thi_hoan_tac(goc, monkeypatch):
    """Sau ghi phải tra lại từng khoá khách của bản sao lưu bằng hàm tra thật; tra không thấy → hoàn tác + 500."""
    ghi(goc / "khoa_khach.json", {"khoa": [ban_ghi_khoa("kk_cu", KHOA_KHACH_THO)]}, 0o600)
    ban = tao_ban({"khoa_khach.json": {"khoa": [ban_ghi_khoa("kk_cu", KHOA_KHACH_THO, ["chat"])]},
                   "dia_chi.json": {"url": "https://w2a.example.com"}})
    with TestClient(app) as c:
        truoc = trang_thai_dia(goc)
        monkeypatch.setattr(khoa_khach, "tim_theo_bam", lambda b: None, raising=False)
        r = ap_dung(c, ban)
        monkeypatch.undo()
        assert r.status_code == 500, r.text
        assert r.json()["error"]["code"] == "khoi_phuc_da_hoan_tac"
        assert trang_thai_dia(goc) == truoc


def test_phan_hoi_neu_vi_tri_ban_truoc_khoi_phuc(goc):
    """6f: phản hồi nêu thư mục .truoc-khoi-phuc-* vừa tạo và số bản đang giữ (không lộ nội dung)."""
    ghi(goc / "bi_mat" / "mod-a.json", {"khoa": KHOA_API_GIA}, 0o600)
    with TestClient(app) as c:
        r = ap_dung(c, tao_ban({"dia_chi.json": {"url": "https://w2a.example.com"}}))
        assert r.status_code == 200, r.text
        bt = r.json()["ban_truoc"]
        assert bt["thu_muc"].startswith(".truoc-khoi-phuc-")
        assert (goc / bt["thu_muc"]).is_dir()
        assert bt["so_ban_giu"] == 1
        assert KHOA_API_GIA not in r.text


# ───────────── điểm 3: bom nén, trần giải nén, tham số scrypt ─────────────

def test_tar_nen_gzip_bi_tu_choi(goc):
    """Ứng dụng chỉ tạo tar KHÔNG nén → chỉ nhận tar không nén (gzip/bz2/xz là đường bom nén)."""
    ban = tao_ban({"dia_chi.json": {"url": "https://w2a.example.com"}}, che_do_tar="w:gz")
    with TestClient(app) as c:
        r = kiem(c, ban)
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "sao_luu_khong_hop_le"


def test_thanh_vien_vuot_tran_tung_tep_bi_tu_choi(goc):
    """Một tệp cấu hình > trần mỗi tệp (8 MiB) → từ chối theo m.size, trước khi đọc nội dung."""
    lon = b"{}" + b" " * (sao_luu.TRAN_MOI_TEP + 1)
    with TestClient(app) as c:
        r = kiem(c, tao_ban({"combo.json": lon}))
        assert r.status_code == 400


def test_khai_kich_thuoc_lon_khong_doc_noi_dung(goc, monkeypatch):
    """Thành viên KHAI 1,5 GiB: bị từ chối ngay khi thấy header, không gọi extractfile."""
    bio = io.BytesIO()
    ti = tarfile.TarInfo("combo.json")
    ti.size = 1536 * 1024 * 1024
    bio.write(ti.tobuf(format=tarfile.GNU_FORMAT))
    bio.write(b"\0" * 4096)
    ban = sao_luu.MAGIC + bytes([1, sao_luu.KIEU_THO]) + bio.getvalue()
    goi = []
    goc_extract = tarfile.TarFile.extractfile
    monkeypatch.setattr(tarfile.TarFile, "extractfile", lambda self, m: goi.append(m) or goc_extract(self, m))
    with TestClient(app) as c:
        assert kiem(c, ban).status_code == 400
    assert goi == []


def test_tham_so_scrypt_la_bi_tu_choi_truoc_khi_bam(goc, monkeypatch):
    """Header giả n=2^20 (1 GiB RAM) → từ chối TRƯỚC khi chạy scrypt; tham số đúng của ứng dụng thì mới băm."""
    goi = []

    class ScryptGian:
        def __init__(self, **kw):
            goi.append((kw["n"], kw["r"], kw["p"]))

        def derive(self, mk):
            return b"\0" * 32

    monkeypatch.setattr(sao_luu, "Scrypt", ScryptGian)
    for n, r_, p in [(1 << 20, 8, 1), (32768, 32, 1), (32768, 8, 32), (1024, 8, 1)]:
        hdr = sao_luu.MAGIC + bytes([1, 1]) + struct.pack(">III", n, r_, p) + os.urandom(16) + os.urandom(12)
        with TestClient(app) as c:
            assert kiem(c, hdr + os.urandom(64), MAT_KHAU).status_code == 400
    assert goi == []
    hdr = sao_luu.MAGIC + bytes([1, 1]) + struct.pack(">III", 32768, 8, 1) + os.urandom(16) + os.urandom(12)
    with TestClient(app) as c:
        assert kiem(c, hdr + os.urandom(64), MAT_KHAU).status_code == 400
    assert goi == [(32768, 8, 1)]


def test_hai_luot_khoi_phuc_khong_chay_cung_luc(goc):
    """Một lượt khôi phục đang chạy (đang giữ khoá) → lượt thứ hai bị từ chối 429, không xếp hàng chiếm RAM."""
    ban = tao_ban({"dia_chi.json": {"url": "https://w2a.example.com"}})
    with TestClient(app) as c:
        assert sao_luu._KHOA_KHOI_PHUC.acquire(blocking=False)
        try:
            r = kiem(c, ban)
        finally:
            sao_luu._KHOA_KHOI_PHUC.release()
        assert r.status_code == 429
        assert kiem(c, ban).status_code == 200
    assert isinstance(sao_luu._KHOA_KHOI_PHUC, type(threading.Lock()))


def test_tao_sao_luu_tu_choi_vuot_tran_khoi_phuc(goc):
    """Ứng dụng không được tạo bản sao lưu mà chính nó sẽ từ chối khi khôi phục."""
    (goc / "combo.json").write_bytes(b"{}" + b" " * (sao_luu.TRAN_MOI_TEP + 1))
    with TestClient(app) as c:
        r = c.post("/api/sao-luu", headers=QT, json={"mat_khau": None})
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "sao_luu_qua_lon"


# ───────────── điểm 5, 6 ─────────────

def test_khong_doi_quyen_thu_muc_goc_va_tao_bi_mat_0700(goc):
    """5: khôi phục không chmod thư mục gốc dữ liệu (0700 phải giữ 0700); bi_mat/ do khôi phục tạo có quyền 0700."""
    with TestClient(app) as c:
        ghi(goc / "bi_mat" / "mod-a.json", {"khoa": KHOA_API_GIA}, 0o600)
        ghi(goc / "dia_chi.json", {"url": "https://w2a.example.com"})
        r = c.post("/api/sao-luu", headers=QT, json={"mat_khau": MAT_KHAU})
        ban_co_mk = r.content
        for p in (goc / "bi_mat").iterdir():
            p.unlink()
        (goc / "bi_mat").rmdir()
        os.chmod(goc, 0o700)
        r = ap_dung(c, ban_co_mk, MAT_KHAU)
        assert r.status_code == 200, r.text
    assert stat.S_IMODE(os.stat(goc).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(goc / "bi_mat").st_mode) == 0o700
    assert stat.S_IMODE(os.stat(goc / "bi_mat" / "mod-a.json").st_mode) == 0o600


@pytest.mark.parametrize("gia_tri", ["false", 1, "true", None])
def test_kem_phien_chi_nhan_bool_that(goc, gia_tri):
    """6a: chuỗi "false" từng thành True. Sai kiểu → 400."""
    with TestClient(app) as c:
        r = c.post("/api/sao-luu", headers=QT, json={"mat_khau": MAT_KHAU, "kem_phien": gia_tri})
        assert r.status_code == 400, r.text


def test_manifest_phien_ban_la_bi_tu_choi(goc):
    """6d: phien_ban trong manifest khác bản ứng dụng hiểu → từ chối."""
    with TestClient(app) as c:
        assert kiem(c, tao_ban({"dia_chi.json": {"url": "https://w2a.example.com"}}, phien_ban=2)).status_code == 400


def test_ten_thanh_vien_khong_phan_chieu_ky_tu_dieu_khien(goc):
    """6c: tên thành viên do người tải lên đặt → thông báo lỗi không chứa ký tự điều khiển, có trần độ dài."""
    bio = io.BytesIO()
    with tarfile.open(fileobj=bio, mode="w") as tf:
        ti = tarfile.TarInfo("x\x1b[31m" + "A" * 400 + ".sh")
        ti.size = 0
        tf.addfile(ti, io.BytesIO(b""))
    ban = sao_luu.MAGIC + bytes([1, sao_luu.KIEU_THO]) + bio.getvalue()
    with TestClient(app) as c:
        r = kiem(c, ban)
        assert r.status_code == 400
        tin = r.json()["error"]["message"]
        assert "\x1b" not in tin and "A" * 200 not in tin
