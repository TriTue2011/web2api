"""NV59 — test trước (đỏ) cho NHIỀU NGUỒN GIT trên một server.

Nền: mỗi server chỉ khai được MỘT repo git. Trong app/nguon_git.py:
`kiem_cau_hinh` (dòng ~97–115), `doc_cau_hinh` và lớp `NguonGit` đều làm việc với
đúng MỘT cấu hình {repo, nhanh, thu_muc, chu_ky_giay, tu_dong, yeu_cau_chu_ky}.

Mục tiêu: người dùng giữ nguồn của chủ kho VÀ thêm nguồn riêng (repo GitHub của họ)
cùng lúc.

Hợp đồng đề xuất cho người viết mã (test dùng các đường/hàm này):
1. Cấu hình có DANH SÁCH nguồn; mỗi nguồn: id ngắn, repo, nhánh, thư mục, chu kỳ,
   tu_dong, yeu_cau_chu_ky. Cấu hình cũ một-repo vẫn đọc được → tự thành danh sách
   một phần tử (không đòi người dùng cấu hình lại).
2. Mỗi nguồn thăm dò riêng; lỗi hoặc giới hạn tần suất của nguồn này không chặn nguồn kia.
3. Module cài từ git ghi nhớ nó đến từ nguồn nào (trong .nguon.json, trường "nguon_id").
   Hai nguồn có module trùng id → KHÔNG tự đè nhau: cài mới từ nguồn thứ hai bị từ
   chối 409 với lý do rõ, trừ khi đúng nguồn cũ.
4. tu_dong và yeu_cau_chu_ky tính theo từng nguồn; mặc định tu_dong = tắt cho nguồn mới.
5. Xoá một nguồn: module đã cài từ nguồn đó KHÔNG bị gỡ, nhưng không còn cập nhật từ
   nguồn ấy; trạng thái hiển thị «nguồn đã gỡ».
6. Nguồn mới đi qua cùng bộ kiểm đường dẫn/SSRF/repo như nguồn hiện có
   (kiem_repo, _duong_an_toan).
7. Các đường quản lý nguồn (thêm, sửa, xoá, liệt kê) là đường quản trị.

Đường/hàm đề xuất:
- POST   /api/nguon-git/nguon        {id, repo, nhanh, thu_muc, chu_ky_giay, tu_dong, yeu_cau_chu_ky[, token]}
- GET    /api/nguon-git/nguon        → {"nguon": [ {...}, ... ]}
- PUT    /api/nguon-git/nguon/{id}   {repo?, nhanh?, ...}
- DELETE /api/nguon-git/nguon/{id}
- nguon_git.doc_danh_sach_nguon() -> list[dict] | None   (None khi chưa cấu hình gì)
- NguonGit.dong_bo_nguon(id) — một vòng thăm dò của ĐÚNG nguồn id

Quy ước: hành vi CHƯA có trên main → @pytest.mark.xfail(strict=True);
hành vi ĐÃ đúng trên main → test thường (phòng thủ).
Chuỗi giả rõ ràng: token GitHub giả sk-test-nv59-…, khoá quản trị test riêng.
"""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app import nguon_git, so_module
from app.main import app
from app.nguon_git import LoiGit, NguonGit, _duong_an_toan, kiem_cau_hinh, kiem_repo
from app.so_module import SoModule
from gia import GitHubGia, MayChuThat, tep_echo, tep_kho

KHOA_QT = "khoa-quan-tri-test-nv59-0123456789abcdef"
QT = {"Authorization": f"Bearer {KHOA_QT}"}
KHOA_SAI = {"Authorization": "Bearer sai-test-nv59-0000000000000000"}
REPO_A = "chu-may/c2a"       # nguồn của chủ kho
REPO_B = "toi/kho-rieng"     # nguồn riêng của người dùng
TOKEN_GIA = "sk-test-nv59-token-0000000000000000"
NGUON_A = {"id": "chinh", "repo": REPO_A, "nhanh": "main", "thu_muc": "modules", "chu_ky_giay": 60}
NGUON_B = {"id": "rieng", "repo": REPO_B, "nhanh": "main", "thu_muc": "modules", "chu_ky_giay": 60}


class GitHubGiaDoi:
    """Hai kho GitHub giả sau MỘT URL (WEB2API_GITHUB_API chỉ trỏ một chỗ):
    định tuyến theo chủ/kho trong đường dẫn."""

    def __init__(self):
        self.a = GitHubGia(REPO_A)
        self.b = GitHubGia(REPO_B)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return
        phan = scope["path"].split("/")
        repo = "/".join(phan[2:4]) if len(phan) >= 4 else ""
        gia = self.a if repo == REPO_A else self.b if repo == REPO_B else None
        if gia is None:
            from starlette.responses import JSONResponse
            await JSONResponse({"message": "Not Found"}, status_code=404)(scope, receive, send)
            return
        await gia.app(scope, receive, send)


@pytest.fixture(scope="module")
def _gh_doi():
    g = GitHubGiaDoi()
    with MayChuThat(g) as s:
        g.url = s.url
        yield g


@pytest.fixture
def gh(_gh_doi, monkeypatch):
    for gia in (_gh_doi.a, _gh_doi.b):
        for k in ("nhanh", "commits", "trees", "blobs", "blob_sai", "nhat_ky", "loi_commit"):
            getattr(gia, k).clear()
        gia.token = None
        gia.han_muc = None
        gia.cat_cay = False
    monkeypatch.setenv("WEB2API_GITHUB_API", _gh_doi.url)
    # NV16: NguonGit dùng TransportGhimIp — cho phép loopback cho máy chủ giả trong test
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    return _gh_doi


@pytest.fixture
def du_lieu(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")
    return tmp_path


def chay(ham):
    """Chạy `ham(so, ng)` trong MỘT vòng sự kiện với sổ module + nguồn git mới."""
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


def khoa_khach(c):
    r = c.post("/api/khoa-khach", headers=QT, json={"nhan": "Khach NV59", "pham_vi": ["chat"]})
    assert r.status_code == 200
    return {"Authorization": "Bearer " + r.json()["khoa"]["khoa"]}


# ───────────── phòng thủ: hành vi ĐÃ đúng trên main ─────────────

def test_cau_hinh_cu_mot_repo_van_doc_duoc(du_lieu):
    """Hợp đồng 1 (tương thích): cấu hình cũ một-repo vẫn đọc được.
    Đường PUT/GET /api/nguon-git chỉ gọi luu_cau_hinh/cong_khai nên kiểm ở tầng đơn vị —
    né vòng nền TestClient (ghi nhận NV59: tắt TestClient khi vòng nền vừa bị đánh thức
    có thể treo vì tác vụ chay_nen không chết sau cancel)."""
    async def ham(so, ng):
        return ng.luu_cau_hinh({"repo": REPO_A, "nhanh": "main"})["cau_hinh"]

    cfg = chay(ham)
    assert cfg["repo"] == REPO_A and cfg["nhanh"] == "main"
    assert cfg["thu_muc"] == "modules" and cfg["chu_ky_giay"] == 300
    assert cfg["tu_dong"] is False and cfg["yeu_cau_chu_ky"] is False


def test_put_danh_sach_vao_duong_cu_bi_tu_choi(du_lieu):
    """Đường cũ chỉ nhận MỘT cấu hình: gửi danh sách vào đó phải 400, không im lặng nhận sai."""
    with TestClient(app) as c:
        r = c.put("/api/nguon-git", json=[NGUON_A, NGUON_B], headers=QT)
    assert r.status_code == 400
    assert not (du_lieu / "nguon_git.json").exists()


def test_kiem_repo_va_duong_an_toan_cho_nguon_moi():
    """Hợp đồng 6: nguồn mới phải qua cùng bộ kiểm. Bộ kiểm hiện có đã chặn đúng."""
    assert kiem_repo("chu-may/c2a") == "chu-may/c2a"
    assert _duong_an_toan("nhanh-rieng", "nhanh") == "nhanh-rieng"
    assert _duong_an_toan("modules/phu", "thu_muc") == "modules/phu"
    for repo_xau in ("../x", "a/..", "a/b/c", "..", "a/b ", "a//b", 123, None):
        with pytest.raises(ValueError):
            kiem_repo(repo_xau)
    for duong_xau in ("/abs", "a//b", "../x", "a/./b", "x" * 201, 123, None):
        with pytest.raises(ValueError):
            _duong_an_toan(duong_xau, "thu_muc")
    with pytest.raises(ValueError):
        kiem_cau_hinh({"repo": REPO_A, "chu_ky_giay": 0}, {})
    with pytest.raises(ValueError):
        kiem_cau_hinh({"repo": REPO_A, "tu_dong": "co"}, {})
    ok = kiem_cau_hinh({"repo": REPO_A, "nhanh": "main", "thu_muc": "modules",
                        "chu_ky_giay": 60, "tu_dong": True, "yeu_cau_chu_ky": False}, {})
    assert ok == {"repo": REPO_A, "nhanh": "main", "thu_muc": "modules",
                  "chu_ky_giay": 60, "tu_dong": True, "yeu_cau_chu_ky": False}


def test_duong_quan_ly_khong_khoa_401(du_lieu):
    """Luật bắt buộc: đường quản trị không khoá → 401 (đường hiện có, phòng thủ)."""
    with TestClient(app) as c:
        assert c.get("/api/nguon-git").status_code == 401
        assert c.put("/api/nguon-git", json={"repo": REPO_A}).status_code == 401
        assert c.get("/api/nguon-git", headers=KHOA_SAI).status_code == 401


def test_khoa_khach_goi_duong_quan_tri_403(du_lieu):
    """Luật bắt buộc: khoá khách gọi đường quản trị → 403 (đường hiện có, phòng thủ)."""
    with TestClient(app) as c:
        kk = khoa_khach(c)
        assert c.get("/api/nguon-git", headers=kk).status_code == 403
        assert c.put("/api/nguon-git", headers=kk, json={"repo": REPO_A}).status_code == 403


def test_module_cai_ghi_nho_nguon_trong_nguon_json(gh, du_lieu):
    """Nền cho hợp đồng 3: module cài từ git hiện đã ghi nhớ repo/nhánh/sha vào .nguon.json."""
    gh.a.day(tep_kho({"echo": tep_echo("echo v1", "1.0.0")}))

    async def ham(so, ng):
        ng.luu_cau_hinh({"repo": REPO_A, "nhanh": "main", "thu_muc": "modules", "chu_ky_giay": 1})
        await ng.mot_lan()
        kq = await ng.cai("echo")
        assert kq["ok"]
        return json.loads((du_lieu / "modules" / "echo" / ".nguon.json").read_text(encoding="utf-8"))

    nguon = chay(ham)
    assert nguon["repo"] == REPO_A and nguon["nhanh"] == "main"
    assert nguon["sha_commit"] and nguon["tree_sha"] and nguon["phien_ban"] == "1.0.0"


# ───────────── đỏ: hành vi CHƯA có trên main ─────────────

@pytest.mark.xfail(strict=True, reason="NV59 NV59-01: chưa có danh sách nguồn; cấu hình cũ chưa tự thành danh sách một phần tử")
def test_cau_hinh_cu_thanh_danh_sach_mot_phan_tu(gh, du_lieu):
    """Hợp đồng 1: cấu hình cũ một-repo tự thành danh sách một phần tử (đọc được, không bắt cấu hình lại).
    Không PUT cấu hình trước — đường mới chưa tồn tại nên 404 bất kể có cấu hình hay không;
    PUT sẽ đánh thức vòng nền và có thể treo khi tắt TestClient (ghi nhận NV59)."""
    with TestClient(app) as c:
        r = c.get("/api/nguon-git/nguon", headers=QT)
        assert r.status_code == 200, r.text
        ds = r.json()["nguon"]
    assert len(ds) == 1
    assert ds[0]["repo"] == REPO_A and ds[0]["nhanh"] == "main"
    assert ds[0]["tu_dong"] is False  # mặc định an toàn khi chuyển đổi


@pytest.mark.xfail(strict=True, reason="NV59 NV59-02: chưa có đường thêm nguồn thứ hai")
def test_them_nguon_thu_hai(gh, du_lieu):
    """Mục tiêu NV59: giữ nguồn của chủ kho VÀ thêm nguồn riêng cùng lúc.
    Không PUT cấu hình trước — đường mới chưa tồn tại nên 405 bất kể có cấu hình hay không;
    PUT sẽ đánh thức vòng nền và có thể treo khi tắt TestClient (ghi nhận NV59)."""
    with TestClient(app) as c:
        r = c.post("/api/nguon-git/nguon", json=NGUON_B, headers=QT)
        assert r.status_code == 200, r.text
        them = r.json()
        assert them["id"] == "rieng" and them["repo"] == REPO_B
        ds = c.get("/api/nguon-git/nguon", headers=QT).json()["nguon"]
    assert {n["id"] for n in ds} == {"chinh", "rieng"}


@pytest.mark.xfail(strict=True, reason="NV59 NV59-03: chưa thăm dò riêng từng nguồn; lỗi nguồn này chặn nguồn kia")
def test_tham_do_rieng_loi_nguon_nay_khong_chan_nguon_kia(gh, du_lieu):
    """Hợp đồng 2: nguồn A lỗi 500 liên tiếp, nguồn B vẫn thăm dò và thấy module mới."""
    gh.a.day(tep_kho({"echo": tep_echo("echo v1", "1.0.0")}))
    gh.b.day(tep_kho({"phu": tep_echo("phu v1", "1.0.0")}))

    async def ham(so, ng):
        ng.luu_cau_hinh({"repo": REPO_A, "nhanh": "main", "thu_muc": "modules", "chu_ky_giay": 1})
        # Giả lập: thêm nguồn thứ hai (đường mới) rồi làm nguồn A lỗi
        await ng.them_nguon(NGUON_B)
        gh.a.loi_commit += [(500, {}), (500, {})]
        await ng.dong_bo_nguon("chinh")   # nguồn A lỗi → LoiGit
        await ng.dong_bo_nguon("rieng")   # nguồn B vẫn phải thăm dò được
        return ng.danh_sach_nguon()

    try:
        ds = chay(ham)
    except LoiGit:
        pass  # nguồn A lỗi là đúng kỳ vọng; quan trọng là B không bị chặn
    assert any(n["id"] == "rieng" and n["so_module"] == 1 for n in ds)


@pytest.mark.xfail(strict=True, reason="NV59 NV59-04: .nguon.json chưa ghi nguồn đã cài (nguon_id)")
def test_nguon_json_ghi_nguon_id(gh, du_lieu):
    """Hợp đồng 3: module cài từ git ghi nhớ nó đến từ nguồn nào."""
    gh.b.day(tep_kho({"phu": tep_echo("phu v1", "2.0.0")}))

    async def ham(so, ng):
        ng.luu_cau_hinh({"repo": REPO_A, "nhanh": "main", "thu_muc": "modules", "chu_ky_giay": 1})
        await ng.them_nguon(NGUON_B)
        await ng.dong_bo_nguon("rieng")
        await ng.cai_tu_nguon("rieng", "phu")
        return json.loads((du_lieu / "modules" / "phu" / ".nguon.json").read_text(encoding="utf-8"))

    nguon = chay(ham)
    assert nguon["nguon_id"] == "rieng" and nguon["repo"] == REPO_B


@pytest.mark.xfail(strict=True, reason="NV59 NV59-05: hai nguồn trùng id module chưa bị chặn 409")
def test_trung_id_hai_nguon_khong_tu_de_nhau(gh, du_lieu):
    """Hợp đồng 3: module «echo» đã cài từ nguồn chính; nguồn riêng cũng có «echo» →
    cài từ nguồn riêng phải 409 với lý do rõ, KHÔNG tự đè."""
    gh.a.day(tep_kho({"echo": tep_echo("echo chinh", "1.0.0")}))
    gh.b.day(tep_kho({"echo": tep_echo("echo la", "9.9.9")}))

    async def ham(so, ng):
        ng.luu_cau_hinh({"repo": REPO_A, "nhanh": "main", "thu_muc": "modules", "chu_ky_giay": 1})
        await ng.them_nguon(NGUON_B)
        await ng.dong_bo_nguon("chinh")
        await ng.cai("echo")
        await ng.dong_bo_nguon("rieng")
        with pytest.raises(LoiGit) as ei:
            await ng.cai_tu_nguon("rieng", "echo")
        assert ei.value.ma == 409
        assert "nguồn" in str(ei.value).lower() or "trùng" in str(ei.value).lower()

    chay(ham)


@pytest.mark.xfail(strict=True, reason="NV59 NV59-06: cập nhật từ đúng nguồn cũ chưa được cho phép riêng")
def test_cap_nhat_tu_dung_nguon_cu_van_duoc(gh, du_lieu):
    """Hợp đồng 3 (ngoại lệ): trùng id nhưng đúng nguồn cũ → cập nhật bình thường."""
    gh.a.day(tep_kho({"echo": tep_echo("echo v1", "1.0.0")}))

    async def ham(so, ng):
        ng.luu_cau_hinh({"repo": REPO_A, "nhanh": "main", "thu_muc": "modules", "chu_ky_giay": 1})
        await ng.them_nguon(NGUON_B)
        await ng.dong_bo_nguon("chinh")
        await ng.cai("echo")
        gh.a.day(tep_kho({"echo": tep_echo("echo v2", "2.0.0")}))
        await ng.dong_bo_nguon("chinh")
        kq = await ng.cai_tu_nguon("chinh", "echo")
        assert kq["ok"] and kq["phien_ban"] == "2.0.0"

    chay(ham)


@pytest.mark.xfail(strict=True, reason="NV59 NV59-07: chưa xoá được nguồn; module đã cài chưa giữ lại đúng cách")
def test_xoa_nguon_khong_go_module_da_cai(gh, du_lieu):
    """Hợp đồng 5: xoá nguồn «rieng» → module «phu» vẫn chạy, không còn cập nhật từ nguồn ấy,
    trạng thái hiển thị «nguồn đã gỡ»."""
    gh.b.day(tep_kho({"phu": tep_echo("phu v1", "1.0.0")}))

    async def ham(so, ng):
        ng.luu_cau_hinh({"repo": REPO_A, "nhanh": "main", "thu_muc": "modules", "chu_ky_giay": 1})
        await ng.them_nguon(NGUON_B)
        await ng.dong_bo_nguon("rieng")
        await ng.cai_tu_nguon("rieng", "phu")
        assert "phu" in so.catalog
        await ng.xoa_nguon("rieng")
        assert "phu" in so.catalog  # KHÔNG bị gỡ
        ds = ng.danh_sach()
        assert any(x["id"] == "phu" and x.get("nguon_trang_thai") == "nguồn đã gỡ" for x in ds)

    chay(ham)


@pytest.mark.xfail(strict=True, reason="NV59 NV59-08: tu_dong chưa tính theo từng nguồn; nguồn mới chưa mặc định tắt")
def test_tu_dong_mac_dinh_tat_cho_nguon_moi(gh, du_lieu):
    """Hợp đồng 4: nguồn mới không khai tu_dong → tu_dong = False; module mới không tự cài."""
    gh.b.day(tep_kho({"phu": tep_echo("phu v1", "1.0.0")}))

    async def ham(so, ng):
        ng.luu_cau_hinh({"repo": REPO_A, "nhanh": "main", "thu_muc": "modules",
                         "chu_ky_giay": 1, "tu_dong": True})
        nguon = await ng.them_nguon(NGUON_B)  # không khai tu_dong
        assert nguon["tu_dong"] is False
        await ng.dong_bo_nguon("rieng")
        await ng._tu_dong()
        assert "phu" not in so.catalog  # nguồn mới mặc định tắt → không tự cài

    chay(ham)


@pytest.mark.xfail(strict=True, reason="NV59 NV59-09: yeu_cau_chu_ky chưa tính theo từng nguồn")
def test_yeu_cau_chu_ky_theo_tung_nguon(gh, du_lieu):
    """Hợp đồng 4: nguồn B bật yeu_cau_chu_ky, commit chưa xác minh → không tự cài từ B;
    nguồn A tắt → vẫn tự cài."""
    gh.a.day(tep_kho({"echo": tep_echo("echo v1", "1.0.0")}))
    gh.b.day(tep_kho({"phu": tep_echo("phu v1", "1.0.0")}), xac_minh=False)

    async def ham(so, ng):
        ng.luu_cau_hinh({"repo": REPO_A, "nhanh": "main", "thu_muc": "modules",
                         "chu_ky_giay": 1, "tu_dong": True})
        await ng.them_nguon({**NGUON_B, "tu_dong": True, "yeu_cau_chu_ky": True})
        await ng.dong_bo_nguon("chinh")
        await ng.dong_bo_nguon("rieng")
        await ng._tu_dong()
        assert "echo" in so.catalog       # nguồn A: không yêu cầu chữ ký → tự cài
        assert "phu" not in so.catalog    # nguồn B: commit chưa xác minh → không tự cài

    chay(ham)


@pytest.mark.xfail(strict=True, reason="NV59 NV59-10: đường quản lý nguồn chưa tồn tại nên chưa có cổng quản trị")
def test_duong_quan_ly_nguon_la_duong_quan_tri(gh, du_lieu):
    """Hợp đồng 7: thêm/sửa/xoá/liệt kê nguồn là đường quản trị — không khoá 401, khoá khách 403."""
    with TestClient(app) as c:
        kk = khoa_khach(c)
        # Chưa có đường → hiện 404; đúng phải là 401/403 theo luật bắt buộc.
        assert c.post("/api/nguon-git/nguon", json=NGUON_B).status_code == 401
        assert c.post("/api/nguon-git/nguon", json=NGUON_B, headers=kk).status_code == 403
        assert c.get("/api/nguon-git/nguon", headers=kk).status_code == 403
        assert c.delete("/api/nguon-git/nguon/rieng", headers=kk).status_code == 403
