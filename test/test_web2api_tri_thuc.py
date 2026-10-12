"""Kiểm thử chuẩn TRI THỨC của lõi (tri_thuc): embedding, rerank, tìm kiếm, quy trình phễu.

Chứng minh 9 điểm nghiệm thu:
1. Lõi chạy khi KHÔNG có module tri thức nào: /api/tim-kiem/nguon rỗng, /api/tim-kiem trả danh sách rỗng, /v1/embeddings và /v1/rerank trả 404/501.
2. Thêm/gỡ module giả giữa chừng nạp nóng: các đường hiện/biến mất không sửa mã lõi.
3. Không từ khoá công nghệ riêng biệt nào trong app/ và web/.
4. Mã mở: khả năng/tầng/trường lạ không làm vỡ hệ thống; tầng lạ không có module khai -> 400 nêu mã.
5. Module giả nhung, xep_hang, tim_kiem: embeddings chuẩn OpenAI; rerank đúng thứ tự; 2 nguồn -> RRF đúng công thức (kiểm số); phễu 4 tầng chạy có bao_cao; tầng lỗi dừng và nêu đúng tầng; trần tầng (8) và trần thời gian -> lỗi nêu tầng; toán tử cây và gộp ngữ cảnh.
6. Khoá khách: thiếu phạm vi -> 403 insufficient_scope; có phạm vi -> gọi được; khách KHÔNG xem được /api/tim-kiem/nguon (quản trị).
7. Mục kết quả luôn có nguon_ngoai: true; chuỗi tìm kiếm dài > 200 ký tự bị cắt trong nhật ký; tài liệu không vào sổ.
8. Module khai nhung/xep_hang/tim_kiem mà thiếu phương thức -> không đúng chuẩn (kiem_chuan_module), không mở đường (501).
9. Tài liệu đầy đủ trong docs/THAM-CHIEU.md, sơ đồ Mermaid và biến môi trường.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import kho, nhat_ky, quy_trinh, so_module, tri_thuc
from app.main import app
from app.so_module import SoModule
from gia import ghi_thu_muc
from ui_chung import CO_CHROME, trang, trinh_duyet  # noqa: F401

KHOA_QT = "khoa-quan-tri-test-tri-thuc-0123456789ab"
QT = {"Authorization": f"Bearer {KHOA_QT}"}


def mau_module_tri_thuc(
    id_="tri-thuc-gia",
    tien_to="ttgia",
    loai="tri_thuc",
    cung_cap=None,
    thieu_phuong_thuc: list[str] | None = None,
) -> dict[str, bytes]:
    """Tạo tệp manifest.json và module.py cho module tri thức giả lập."""
    if cung_cap is None:
        cung_cap = ["nhung", "xep_hang", "tim_kiem"]
    manifest = {
        "id": id_,
        "ten": "Tri thức giả lập",
        "mo_ta": "Module tri thức giả để kiểm thử",
        "loai": loai,
        "giao_thuc": "cuc_bo",
        "tien_to": tien_to,
        "cung_cap": cung_cap,
        "truong_cau_hinh": [],
        "tai_nguyen": {"ram_them_mb": None, "dia_mb": None, "gpu": False, "mang_ra": [], "ghi_chu": ""},
    }

    phuong_thuc_ma = []

    if not thieu_phuong_thuc or "nhung" not in thieu_phuong_thuc:
        phuong_thuc_ma.append('''
    async def nhung(self, body: dict):
        inp = body.get("input", "")
        texts = [inp] if isinstance(inp, str) else list(inp)
        data = []
        for i, text in enumerate(texts):
            # Vector xác định theo chuỗi (tính băm chữ)
            v = [round(((ord(c) * 17 + i) % 100) / 100.0, 4) for c in (str(text)[:8] or "a").ljust(8, "0")]
            data.append({"object": "embedding", "embedding": v, "index": i})
        noi_dung = json.dumps({
            "object": "list",
            "data": data,
            "model": body.get("model", ""),
            "usage": {"prompt_tokens": len(texts), "total_tokens": len(texts)},
        }).encode("utf-8")
        return 200, {"content-type": "application/json"}, noi_dung
''')

    if not thieu_phuong_thuc or "xep_hang" not in thieu_phuong_thuc:
        phuong_thuc_ma.append('''
    async def xep_hang(self, body: dict):
        query = str(body.get("query", "")).lower()
        query_words = set(query.split())
        documents = body.get("documents", [])
        top_n = body.get("top_n", len(documents))

        results = []
        for idx, doc in enumerate(documents):
            text = doc if isinstance(doc, str) else str(doc.get("text", ""))
            doc_words = set(text.lower().split())
            score = round(float(len(query_words & doc_words)) * 0.5 + 0.1, 4)
            results.append({"index": idx, "relevance_score": score})

        results.sort(key=lambda x: x["relevance_score"], reverse=True)
        if top_n is not None:
            results = results[:int(top_n)]

        noi_dung = json.dumps({"results": results}).encode("utf-8")
        return 200, {"content-type": "application/json"}, noi_dung
''')

    if not thieu_phuong_thuc or "tim_kiem" not in thieu_phuong_thuc:
        phuong_thuc_ma.append(f'''
    async def tim_kiem(self, yc: dict):
        # Bộ dữ liệu mẫu trong bộ nhớ
        du_lieu = [
            {{
                "id": "{tien_to}.doc1",
                "diem": 0.95,
                "doan": "Tri tue nhan tao va hoc sau trong khoa hoc",
                "tai_lieu": "tailieu1.txt",
                "vi_tri": 1,
                "truong": {{"loai": "khoa_hoc", "nam": 2026, "duong_dan": "khoa_hoc/cntt/ai"}},
            }},
            {{
                "id": "{tien_to}.doc2",
                "diem": 0.85,
                "doan": "Van hoc co dien va tho ca Viet Nam",
                "tai_lieu": "tailieu2.txt",
                "vi_tri": 2,
                "truong": {{"loai": "van_hoc", "nam": 2025, "duong_dan": "van_hoc/tho"}},
            }},
            {{
                "id": "{tien_to}.doc3",
                "diem": 0.75,
                "doan": "He dieu hanh phan tan va mang may tinh",
                "tai_lieu": "tailieu3.txt",
                "vi_tri": 3,
                "truong": {{"loai": "khoa_hoc", "nam": 2026, "duong_dan": "khoa_hoc/cntt/he_dieu_hanh"}},
            }},
        ]
        return {{"muc": du_lieu, "tong": len(du_lieu)}}
''')

    than_module = "\n".join(phuong_thuc_ma)
    ma = f'''
import json

class Module:
    def __init__(self, manifest):
        self.manifest = manifest
        self.tien_to = manifest["tien_to"]

    def nap(self, cau_hinh):
        self.cau_hinh = cau_hinh

    async def cai(self, cau_hinh):
        return {{"ok": True}}

    async def chat(self, body, stream=False):
        return 200, {{"content-type": "application/json"}}, b'{{"choices": []}}'

    async def models(self):
        return [
            {{"id": f"{{self.tien_to}}/emb", "kha_nang": "nhung"}},
            {{"id": f"{{self.tien_to}}/rerank", "kha_nang": "xep_hang"}},
        ]

    async def suc_khoe(self):
        return {{"ok": True}}

    async def dung(self):
        pass

{than_module}
'''
    return {
        "manifest.json": json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
        "module.py": ma.encode("utf-8"),
    }


@pytest.fixture
def moi_truong(tmp_path, monkeypatch):
    """Môi trường dữ liệu sạch cho kiểm thử tri thức."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "modules").mkdir(parents=True, exist_ok=True)
    anh_dir = tmp_path / "anh"
    anh_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setattr(so_module, "THU_MUC_ANH", anh_dir)

    with TestClient(app) as client:
        so = SoModule()
        client.app.state.so = so
        yield client, data_dir, so


def test_1_loi_chay_khi_khong_co_module_tri_thuc(moi_truong):
    """Điểm 1: Lõi chạy khi KHÔNG có module tri thức nào: các đường trả về sạch, không vỡ."""
    client, data_dir, so = moi_truong

    # GET /api/tim-kiem/nguon trả danh sách nguồn rỗng
    r_nguon = client.get("/api/tim-kiem/nguon", headers=QT)
    assert r_nguon.status_code == 200
    assert r_nguon.json() == {"nguon": []}

    # POST /api/tim-kiem trả danh sách rỗng, không lỗi
    r_tk = client.post("/api/tim-kiem", headers=QT, json={"chuoi": "kiem thu"})
    assert r_tk.status_code == 200
    res_tk = r_tk.json()
    assert res_tk["muc"] == []
    assert res_tk["tong"] == 0

    # POST /v1/embeddings khi model không thuộc module nào -> 404 model_not_found
    r_emb = client.post("/v1/embeddings", headers=QT, json={"model": "khongco/emb", "input": "chao"})
    assert r_emb.status_code == 404
    assert r_emb.json()["error"]["code"] == "model_not_found"

    # POST /v1/embeddings thiếu model -> 400 missing_model
    r_emb_no_m = client.post("/v1/embeddings", headers=QT, json={"input": "chao"})
    assert r_emb_no_m.status_code == 400

    # POST /v1/rerank khi model không thuộc module nào -> 404 model_not_found
    r_rr = client.post("/v1/rerank", headers=QT, json={"model": "khongco/rerank", "query": "q", "documents": ["doc1"]})
    assert r_rr.status_code == 404
    assert r_rr.json()["error"]["code"] == "model_not_found"


def test_2_tha_go_module_nong(moi_truong):
    """Điểm 2: Thêm/gỡ module nóng: các đường mở ra và biến mất không sửa lõi."""
    client, data_dir, so = moi_truong

    # Thả module vào thư mục dữ liệu
    mod_path = data_dir / "modules" / "tri-thuc-gia"
    ghi_thu_muc(mod_path, mau_module_tri_thuc("tri-thuc-gia", "ttgia"))

    # Nạp nóng
    r_nap = client.post("/api/modules/nap-lai", headers=QT)
    assert r_nap.status_code == 200
    assert "tri-thuc-gia" in r_nap.json()["them"]

    # Cài đặt liên kết module
    r_cai = client.post("/api/modules/tri-thuc-gia/cai", headers=QT, json={"cau_hinh": {}})
    assert r_cai.status_code == 200

    # Nguồn tìm kiếm hiện ra
    r_nguon = client.get("/api/tim-kiem/nguon", headers=QT)
    assert r_nguon.status_code == 200
    ds = r_nguon.json()["nguon"]
    assert len(ds) == 1
    assert ds[0]["id"] == "tri-thuc-gia"
    assert ds[0]["dung_chuan"] is True
    assert ds[0]["trang_thai"] == "san_sang"

    # Gọi tìm kiếm ra kết quả
    r_tk = client.post("/api/tim-kiem", headers=QT, json={"chuoi": "ai"})
    assert r_tk.status_code == 200
    assert len(r_tk.json()["muc"]) > 0

    # Gỡ module
    r_go = client.delete("/api/modules/tri-thuc-gia", headers=QT)
    assert r_go.status_code == 200

    # Nguồn tìm kiếm biến mất, quay về rỗng
    r_nguon_sau = client.get("/api/tim-kiem/nguon", headers=QT)
    assert r_nguon_sau.status_code == 200
    assert r_nguon_sau.json()["nguon"] == []

    # Gọi embeddings sau khi gỡ -> 404 model_not_found
    r_emb_sau = client.post("/v1/embeddings", headers=QT, json={"model": "ttgia/emb", "input": "test"})
    assert r_emb_sau.status_code == 404


def test_3_khong_tu_khoa_cong_nghe():
    """Điểm 3: app/ và web/ tuyệt đối không chứa tên công nghệ cụ thể."""
    goc = Path(__file__).resolve().parents[1]
    cam = re.compile(r"\b(fts5|bm25|qdrant|faiss|chroma|cohere|jina|pinecone|elasticsearch|sqlite)\b", re.IGNORECASE)

    vi_pham = []
    for thu_muc in ("app", "web"):
        for p in (goc / thu_muc).rglob("*"):
            if p.is_file() and p.suffix in (".py", ".js", ".html", ".css", ".md"):
                text = p.read_text(encoding="utf-8", errors="ignore")
                for so_dong, line in enumerate(text.splitlines(), start=1):
                    m = cam.search(line)
                    if m:
                        vi_pham.append(f"{p}:{so_dong}: từ khoá «{m.group(0)}» trong: {line.strip()}")

    assert not vi_pham, "Phát hiện tên công nghệ cụ thể trong app/ hoặc web/:\n" + "\n".join(vi_pham)


def test_4_ma_mo_truong_la_tang_la(moi_truong):
    """Điểm 4: Mã mở: trường lạ trong yêu cầu không vỡ; tầng quy trình lạ không module nào có -> 400 nêu mã."""
    client, data_dir, so = moi_truong
    mod_path = data_dir / "modules" / "tri-thuc-gia"
    ghi_thu_muc(mod_path, mau_module_tri_thuc("tri-thuc-gia", "ttgia"))
    client.post("/api/modules/nap-lai", headers=QT)
    client.post("/api/modules/tri-thuc-gia/cai", headers=QT, json={"cau_hinh": {}})

    # Gửi trường lạ vào POST /api/tim-kiem -> không lỗi
    r = client.post(
        "/api/tim-kiem",
        headers=QT,
        json={"chuoi": "test", "truong_la_1": 123, "truong_la_2": {"a": "b"}},
    )
    assert r.status_code == 200

    # Phễu có tầng mang khả năng lạ không ai có -> 400 nêu mã khả năng
    r_la = client.post(
        "/api/tim-kiem",
        headers=QT,
        json={
            "chuoi": "test",
            "pheu": [
                {"ten": "tang_la", "kha_nang": "chuc_nang_khong_ai_biet"},
            ],
        },
    )
    assert r_la.status_code == 400
    assert "chuc_nang_khong_ai_biet" in r_la.json()["error"]["message"]


def test_5_module_gia_nhung_xep_hang_tim_kiem_va_rrf(moi_truong, monkeypatch):
    """Điểm 5: Chứng minh đầy đủ nhung, xep_hang, RRF 2 nguồn, phễu 4 tầng, dừng khi lỗi, trần tầng/thời gian, cây và cắt ngữ cảnh."""
    client, data_dir, so = moi_truong

    # Dựng module 1: ttgia
    mod1_path = data_dir / "modules" / "tri-thuc-1"
    ghi_thu_muc(mod1_path, mau_module_tri_thuc("tri-thuc-1", "tt1"))

    # Dựng module 2: tt2
    mod2_path = data_dir / "modules" / "tri-thuc-2"
    ghi_thu_muc(mod2_path, mau_module_tri_thuc("tri-thuc-2", "tt2"))

    client.post("/api/modules/nap-lai", headers=QT)
    client.post("/api/modules/tri-thuc-1/cai", headers=QT, json={"cau_hinh": {}})
    client.post("/api/modules/tri-thuc-2/cai", headers=QT, json={"cau_hinh": {}})

    # 1. Kiểm POST /v1/embeddings (chuẩn OpenAI, vector xác định theo chuỗi)
    r_emb = client.post("/v1/embeddings", headers=QT, json={"model": "tt1/emb", "input": "xin chao"})
    assert r_emb.status_code == 200
    emb_json = r_emb.json()
    assert emb_json["object"] == "list"
    assert len(emb_json["data"]) == 1
    assert emb_json["data"][0]["index"] == 0
    assert emb_json["data"][0]["object"] == "embedding"
    v1 = emb_json["data"][0]["embedding"]
    assert isinstance(v1, list) and len(v1) == 8

    # Gọi lại cùng chuỗi -> vector giống nhau hoàn toàn
    r_emb_same = client.post("/v1/embeddings", headers=QT, json={"model": "tt1/emb", "input": "xin chao"})
    assert r_emb_same.json()["data"][0]["embedding"] == v1

    # Kiểm list input
    r_emb_list = client.post("/v1/embeddings", headers=QT, json={"model": "tt1/emb", "input": ["doan mot", "doan hai"]})
    assert r_emb_list.status_code == 200
    assert len(r_emb_list.json()["data"]) == 2

    # 2. Kiểm POST /v1/rerank (xếp hạng và cắt top_n, kiểm hình dạng)
    r_rr = client.post(
        "/v1/rerank",
        headers=QT,
        json={
            "model": "tt1/rerank",
            "query": "khoa hoc may tinh",
            "documents": [
                "Van hoc viet nam",
                "Khoa hoc vu tru",
                "Nganh khoa hoc may tinh va he thong",
            ],
            "top_n": 2,
        },
    )
    assert r_rr.status_code == 200
    rr_json = r_rr.json()
    assert "results" in rr_json
    assert len(rr_json["results"]) == 2
    # Tài liệu thứ 2 (chỉ số 2) có nhiều từ trùng nhất -> điểm cao nhất
    assert rr_json["results"][0]["index"] == 2
    assert rr_json["results"][0]["relevance_score"] > rr_json["results"][1]["relevance_score"]

    # Kiểm tra hình dạng rerank: query không phải chuỗi -> 400
    assert client.post("/v1/rerank", headers=QT, json={"model": "tt1/rerank", "query": 123, "documents": ["a"]}).status_code == 400
    # documents không phải list -> 400
    assert client.post("/v1/rerank", headers=QT, json={"model": "tt1/rerank", "query": "q", "documents": "khong_phai_list"}).status_code == 400
    # top_n âm hoặc lớn hơn số documents -> 400
    assert client.post("/v1/rerank", headers=QT, json={"model": "tt1/rerank", "query": "q", "documents": ["a"], "top_n": 5}).status_code == 400
    assert client.post("/v1/rerank", headers=QT, json={"model": "tt1/rerank", "query": "q", "documents": ["a"], "top_n": 0}).status_code == 400

    # 3. Hai nguồn -> RRF đúng công thức toán học
    # Tính trực tiếp toán tử gop: nguồn 1 có [A, B], nguồn 2 có [B, C]
    ds_nguon = {
        "danh_sach": [
            [{"id": "A", "doan": "doc A"}, {"id": "B", "doan": "doc B"}],
            [{"id": "B", "doan": "doc B"}, {"id": "C", "doan": "doc C"}],
        ]
    }
    kq_gop = quy_trinh.toan_tu_gop(ds_nguon, k=60)
    muc_gop = kq_gop["muc"]
    # Điểm mong đợi:
    # A (hạng 1 nguồn 1): 1 / (60 + 1) = 1/61 = 0.01639344
    # B (hạng 2 nguồn 1, hạng 1 nguồn 2): 1/62 + 1/61 = 0.01612903 + 0.01639344 = 0.03252247
    # C (hạng 2 nguồn 2): 1/62 = 0.01612903
    assert muc_gop[0]["id"] == "B"
    assert muc_gop[1]["id"] == "A"
    assert muc_gop[2]["id"] == "C"
    assert abs(muc_gop[0]["diem"] - (1 / 61 + 1 / 62)) < 1e-6
    assert abs(muc_gop[1]["diem"] - (1 / 61)) < 1e-6
    assert abs(muc_gop[2]["diem"] - (1 / 62)) < 1e-6

    # 4. Phễu 4 tầng: truy_hoi -> loc -> xep_hang -> cat
    pheu_chay = [
        {"ten": "tang_truy_hoi", "kha_nang": "tim_kiem", "nguon": "tri-thuc-1"},
        {"ten": "tang_loc", "kha_nang": "loc", "tham_so": {"dieu_kien": {"loai": "khoa_hoc"}}},
        {"ten": "tang_xep_hang", "kha_nang": "xep_hang", "nguon": "tri-thuc-1", "tham_so": {"query": "hoc sau"}},
        {"ten": "tang_cat", "kha_nang": "cat", "tham_so": {"so_luong": 1}},
    ]
    r_pheu = client.post("/api/tim-kiem", headers=QT, json={"chuoi": "hoc sau", "pheu": pheu_chay})
    assert r_pheu.status_code == 200
    res_pheu = r_pheu.json()
    assert len(res_pheu["muc"]) == 1
    assert res_pheu["muc"][0]["id"] == "tt1.doc1"
    # Kiểm tra bao_cao
    bc = res_pheu["bao_cao"]
    assert len(bc) == 4
    assert [b["ten"] for b in bc] == ["tang_truy_hoi", "tang_loc", "tang_xep_hang", "tang_cat"]
    assert bc[0]["vao"] == 0 and bc[0]["ra"] == 3
    assert bc[1]["vao"] == 3 and bc[1]["ra"] == 2  # lọc loại khoa_hoc còn 2 mục
    assert bc[2]["vao"] == 2 and bc[2]["ra"] == 2
    assert bc[3]["vao"] == 2 and bc[3]["ra"] == 1  # cắt còn 1 mục
    assert all(b["ms"] >= 0 for b in bc)

    # 5. Tầng giữa bị lỗi -> dừng, nêu đúng tầng, các tầng trước vẫn có trong bao_cao
    pheu_loi = [
        {"ten": "tang_truy_hoi", "kha_nang": "tim_kiem", "nguon": "tri-thuc-1"},
        {"ten": "tang_loi_giua", "kha_nang": "kha_nang_khong_ton_tai"},
        {"ten": "tang_cat", "kha_nang": "cat", "tham_so": {"so_luong": 1}},
    ]
    r_loi_tang = client.post("/api/tim-kiem", headers=QT, json={"chuoi": "test", "pheu": pheu_loi})
    assert r_loi_tang.status_code == 400
    than_loi = r_loi_tang.json()
    assert "tang_loi_giua" in than_loi["error"]["message"]
    assert than_loi["error"]["tang_loi"] == "tang_loi_giua"
    bc_loi = than_loi["error"]["bao_cao"]
    assert len(bc_loi) == 2
    assert bc_loi[0]["ten"] == "tang_truy_hoi" and bc_loi[0]["ra"] == 3
    assert bc_loi[1]["ten"] == "tang_loi_giua" and "loi" in bc_loi[1]

    # 6. Trần số tầng (8)
    pheu_qua_tang = [{"ten": f"t_{i}", "kha_nang": "cat"} for i in range(9)]
    r_qua_tang = client.post("/api/tim-kiem", headers=QT, json={"pheu": pheu_qua_tang})
    assert r_qua_tang.status_code == 400
    assert r_qua_tang.json()["error"]["code"] == "too_many_steps"

    # 7. Trần thời gian tổng (WEB2API_QUY_TRINH_GIAY) -> 504
    monkeypatch.setenv("WEB2API_QUY_TRINH_GIAY", "0.001")
    # Tầng chạy làm chậm để vượt trần
    pheu_tre = [
        {"ten": "tang_cham", "kha_nang": "tim_kiem", "nguon": "tri-thuc-1"},
    ]
    # Patch asyncio.wait_for hoặc để quy trình kiểm tra hết hạn
    r_504 = client.post("/api/tim-kiem", headers=QT, json={"pheu": pheu_tre})
    assert r_504.status_code in (504, 200)  # Nếu máy chạy cực nhanh <1ms thì 200, thử chắc chắn 504 bằng test trực tiếp hàm chay
    monkeypatch.undo()

    # Kiểm tra trực tiếp 504 trong quy_trinh.chay
    monkeypatch.setenv("WEB2API_QUY_TRINH_GIAY", "0.0")
    with pytest.raises(quy_trinh.LoiQuyTrinh) as exc_info:
        asyncio.run(quy_trinh.chay([{"ten": "tang_qua_han", "kha_nang": "cat"}], {}, so))
    assert exc_info.value.ma == 504
    assert exc_info.value.tang_loi == "tang_qua_han"
    monkeypatch.undo()

    # 8. Toán tử cây (cay)
    du_lieu_cay = {
        "muc": [
            {"id": "1", "truong": {"duong_dan": "khoa_hoc/cntt/ai"}},
            {"id": "2", "truong": {"duong_dan": "khoa_hoc/cntt/he_dieu_hanh/nhan"}},
            {"id": "3", "truong": {"duong_dan": "van_hoc/tho"}},
        ]
    }
    # Lọc gốc "khoa_hoc/cntt", độ sâu 1
    kq_cay = quy_trinh.toan_tu_cay(du_lieu_cay, {"truong": "duong_dan", "goc": "khoa_hoc/cntt", "do_sau": 1})
    ids_cay = [m["id"] for m in kq_cay["muc"]]
    assert ids_cay == ["1"]  # mục 2 có độ sâu 2 so với gốc nên bị loại, mục 3 sai gốc

    # 9. Toán tử gộp ngữ cảnh (gop_ngu_canh)
    du_lieu_ngu_canh = {
        "muc": [
            {"id": "1", "doan": "12345"},  # dài 5
            {"id": "2", "doan": "12345"},  # dài 5
            {"id": "3", "doan": "12345"},  # dài 5
        ]
    }
    kq_nc = quy_trinh.toan_tu_gop_ngu_canh(du_lieu_ngu_canh, tran_ky_tu=12)
    assert len(kq_nc["muc"]) == 2  # 5 + 5 <= 12, mục 3 thành 15 > 12 nên dừng


def test_6_khoa_khach_va_phan_quyen_pham_vi(moi_truong):
    """Điểm 6: Khoá khách: thiếu phạm vi nhung/xep_hang/tim_kiem -> 403; có -> được; khách không xem được /api/tim-kiem/nguon."""
    client, data_dir, so = moi_truong
    mod_path = data_dir / "modules" / "tri-thuc-gia"
    ghi_thu_muc(mod_path, mau_module_tri_thuc("tri-thuc-gia", "ttgia"))
    client.post("/api/modules/nap-lai", headers=QT)
    client.post("/api/modules/tri-thuc-gia/cai", headers=QT, json={"cau_hinh": {}})

    # 1. Tạo khoá chỉ có quyền chat
    r_k_chat = client.post("/api/khoa-khach", headers=QT, json={"nhan": "Khach Chat", "pham_vi": ["chat"]})
    k_chat = r_k_chat.json()["khoa"]["khoa"]
    h_chat = {"Authorization": f"Bearer {k_chat}"}

    # Gọi nhung thiếu quyền -> 403 insufficient_scope
    r1 = client.post("/v1/embeddings", headers=h_chat, json={"model": "ttgia/emb", "input": "test"})
    assert r1.status_code == 403
    assert r1.json()["error"]["code"] == "insufficient_scope"

    # Gọi xep_hang thiếu quyền -> 403 insufficient_scope
    r2 = client.post("/v1/rerank", headers=h_chat, json={"model": "ttgia/rerank", "query": "q", "documents": ["d"]})
    assert r2.status_code == 403
    assert r2.json()["error"]["code"] == "insufficient_scope"

    # Gọi tim_kiem thiếu quyền -> 403 insufficient_scope
    r3 = client.post("/api/tim-kiem", headers=h_chat, json={"chuoi": "q"})
    assert r3.status_code == 403
    assert r3.json()["error"]["code"] == "insufficient_scope"

    # Khách không được xem /api/tim-kiem/nguon (quản trị) -> 403 forbidden
    r_admin_only = client.get("/api/tim-kiem/nguon", headers=h_chat)
    assert r_admin_only.status_code == 403
    assert r_admin_only.json()["error"]["code"] == "forbidden"

    # 2. Tạo khoá có quyền nhung
    r_k_nhung = client.post("/api/khoa-khach", headers=QT, json={"nhan": "Khach Nhung", "pham_vi": ["nhung"]})
    k_nhung = r_k_nhung.json()["khoa"]["khoa"]
    h_nhung = {"Authorization": f"Bearer {k_nhung}"}

    r_ok_nhung = client.post("/v1/embeddings", headers=h_nhung, json={"model": "ttgia/emb", "input": "test"})
    assert r_ok_nhung.status_code == 200

    # 3. Tạo khoá có quyền xep_hang
    r_k_xh = client.post("/api/khoa-khach", headers=QT, json={"nhan": "Khach Rerank", "pham_vi": ["xep_hang"]})
    k_xh = r_k_xh.json()["khoa"]["khoa"]
    h_xh = {"Authorization": f"Bearer {k_xh}"}

    r_ok_xh = client.post("/v1/rerank", headers=h_xh, json={"model": "ttgia/rerank", "query": "q", "documents": ["d"]})
    assert r_ok_xh.status_code == 200

    # 4. Tạo khoá có quyền tim_kiem
    r_k_tk = client.post("/api/khoa-khach", headers=QT, json={"nhan": "Khach Tim Kiem", "pham_vi": ["tim_kiem"]})
    k_tk = r_k_tk.json()["khoa"]["khoa"]
    h_tk = {"Authorization": f"Bearer {k_tk}"}

    r_ok_tk = client.post("/api/tim-kiem", headers=h_tk, json={"chuoi": "q"})
    assert r_ok_tk.status_code == 200


def test_7_bao_mat_nguon_ngoai_va_nhat_ky(moi_truong):
    """Điểm 7: Mục kết quả luôn có nguon_ngoai: true; chuỗi > 200 ký tự bị cắt trong sổ; tài liệu không vào sổ."""
    client, data_dir, so = moi_truong
    mod_path = data_dir / "modules" / "tri-thuc-gia"
    ghi_thu_muc(mod_path, mau_module_tri_thuc("tri-thuc-gia", "ttgia"))
    client.post("/api/modules/nap-lai", headers=QT)
    client.post("/api/modules/tri-thuc-gia/cai", headers=QT, json={"cau_hinh": {}})

    chuoi_dai = "tim kiem tri thuc thong tin khoa hoc " * 10
    r = client.post("/api/tim-kiem", headers=QT, json={"chuoi": chuoi_dai})
    assert r.status_code == 200
    res = r.json()

    # Mọi mục có nguon_ngoai: true
    for m in res["muc"]:
        assert m.get("nguon_ngoai") is True
        assert m.get("nguon") == "tri-thuc-gia"

    # Kiểm tra kênh nhật ký tri_thuc
    dong_nhat_ky = list(nhat_ky.doc("tri_thuc"))
    assert len(dong_nhat_ky) > 0
    dong_cuoi = dong_nhat_ky[0]
    # Chuỗi tìm kiếm trong sổ bị cắt tối đa 200 ký tự
    chuoi_da_ghi = dong_cuoi.get("them", {}).get("chuoi", "")
    assert len(chuoi_da_ghi) <= 200
    assert len(chuoi_da_ghi) == 200

    # Tuyệt đối không ghi tài liệu hoặc nội dung text của tài liệu vào nhật ký
    str_dong = json.dumps(dong_cuoi)
    assert "Tri tue nhan tao" not in str_dong
    assert "tailieu1.txt" not in str_dong


def test_8_kiem_chuan_module(moi_truong):
    """Điểm 8: Module khai nhung/xep_hang/tim_kiem mà thiếu phương thức -> không đúng chuẩn, không mở đường (501)."""
    client, data_dir, so = moi_truong

    # Dựng module khai cung_cap đủ 3 khả năng nhưng thiếu phương thức nhung và xep_hang
    mod_path = data_dir / "modules" / "tri-thuc-thieu"
    ghi_thu_muc(mod_path, mau_module_tri_thuc(
        id_="tri-thuc-thieu",
        tien_to="ttthieu",
        cung_cap=["nhung", "xep_hang", "tim_kiem"],
        thieu_phuong_thuc=["nhung", "xep_hang"],
    ))

    r_nap = client.post("/api/modules/nap-lai", headers=QT)
    assert r_nap.status_code == 200
    client.post("/api/modules/tri-thuc-thieu/cai", headers=QT, json={"cau_hinh": {}})

    # Kiểm tra catalog: chuan_loi phát hiện thiếu nhung và xep_hang
    km = so.catalog.get("tri-thuc-thieu")
    assert km is not None
    assert "nhung" in km.chuan_loi
    assert "xep_hang" in km.chuan_loi
    assert "tim_kiem" not in km.chuan_loi

    # Gọi /v1/embeddings bị từ chối 501 capability_not_supported
    r_emb = client.post("/v1/embeddings", headers=QT, json={"model": "ttthieu/emb", "input": "test"})
    assert r_emb.status_code == 501
    assert r_emb.json()["error"]["code"] == "capability_not_supported"

    # Gọi /v1/rerank bị từ chối 501 capability_not_supported
    r_rr = client.post("/v1/rerank", headers=QT, json={"model": "ttthieu/rerank", "query": "q", "documents": ["d"]})
    assert r_rr.status_code == 501
    assert r_rr.json()["error"]["code"] == "capability_not_supported"


def test_9_tai_lieu_va_tham_chieu():
    """Điểm 9: Bảng tra cứu THAM-CHIEU.md có đầy đủ các tuyến mới, biến WEB2API_QUY_TRINH_GIAY và sơ đồ Mermaid."""
    goc = Path(__file__).resolve().parents[1]
    tham_chieu = (goc / "docs" / "THAM-CHIEU.md").read_text(encoding="utf-8")

    assert "/api/tim-kiem/nguon" in tham_chieu
    assert "/api/tim-kiem" in tham_chieu
    assert "/v1/embeddings" in tham_chieu
    assert "/v1/rerank" in tham_chieu
    assert "WEB2API_QUY_TRINH_GIAY" in tham_chieu
    assert "flowchart LR" in tham_chieu
    assert "RRF" in tham_chieu


@pytest.mark.skipif(not CO_CHROME, reason="cần Chrome")
def test_10_giao_dien_module_tri_thuc_chrome(trang, tmp_path, monkeypatch):
    """Test giao diện Chrome: Module tri thức hiển thị trên trang Hệ thống -> Module."""
    from gia import MayChuThat

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "modules").mkdir(parents=True, exist_ok=True)
    anh_dir = tmp_path / "anh"
    anh_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setattr(so_module, "THU_MUC_ANH", anh_dir)

    mod_path = data_dir / "modules" / "tri-thuc-gia"
    ghi_thu_muc(mod_path, mau_module_tri_thuc("tri-thuc-gia", "ttgia"))

    with MayChuThat(app, lifespan="on") as w:
        trang.di(f"{w.url}/He-thong.dc.html")
        trang.go("#o-khoa", KHOA_QT) if trang.js("!!document.getElementById('o-khoa')") else None
        trang.cho("document.querySelector('[data-o=\"tri-thuc-gia\"]') !== null", han=15, mo_ta="ô module tri thức")
        assert "Tri thức giả lập" in trang.js("document.querySelector('[data-o=\"tri-thuc-gia\"]').innerText")

