"""Test NV156 — hợp nhất van-phong (NV138) + vanphong (NV132) thành MỘT module.

- Chỉ còn MỘT ô «Văn phòng»: id `van-phong`, KHÔNG còn thư mục `vanphong`.
- Ba thực thể từ cùng một module: office_doc, office_ghi, ocr_va_soan_thao.
- doc_guard (chép services/doc_guard.py @ c2a d879b03): zip bomb, zip-slip,
  XXE, trần kích thước qua công cụ `kiem_tai_lieu`.
- `cai()` cho phép lưu cấu hình từng nửa (chỉ Office hoặc chỉ OCR).
"""
from __future__ import annotations

import base64
import io
import json
import logging
import sys
import zipfile
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.kham_pha import doc_manifest, nap_ma, van_tay  # noqa: E402

GOC = Path(__file__).resolve().parents[1]
THU_MUC = GOC / "modules" / "van-phong"
ID_TT = "van_phong.ocr_va_soan_thao"


def _chay(coro):
    import asyncio
    return asyncio.run(coro)


def _khong_gian():
    m, loi = doc_manifest(THU_MUC)
    assert loi is None, loi
    lop, ten = nap_ma(THU_MUC, m, van_tay(THU_MUC))
    return sys.modules[ten], lop, m


def _module():
    _, lop, m = _khong_gian()
    mod = lop(m)
    mod.nap({})
    return mod


# ───────────── đường gộp: một module duy nhất ─────────────

def test_khong_con_module_vanphong():
    assert not (GOC / "modules" / "vanphong").exists()
    m, loi = doc_manifest(THU_MUC)
    assert loi is None
    assert m["id"] == "van-phong"


def test_ba_thuc_the_trong_mot_module():
    mod = _module()
    # NV181: thuc_the là async def (hợp đồng lõi) — giữ nguyên tập id kỳ vọng.
    ids = {e["id"] for e in _chay(mod.thuc_the(None))}
    assert ids == {"van_phong.office_doc", "van_phong.office_ghi",
                   "van_phong.ocr_va_soan_thao"}


def test_tai_lieu_chi_mot_dong_van_phong():
    readme = (GOC / "README.md").read_text(encoding="utf-8")
    dong = [d for d in readme.splitlines() if d.startswith("| `van-phong`")]
    assert len(dong) == 1, dong
    assert "`vanphong`" not in readme


def test_khoa_dich_vu_giu_nguyen_khai_bao_nv138():
    """Điều phối đã viết lại mô tả khoá ở nhánh khác — ở đây KHÔNG đổi khai báo."""
    m, loi = doc_manifest(THU_MUC)
    assert loi is None
    truong = {t["khoa"]: t for t in m["truong_cau_hinh"]}
    kd = truong["khoa_dich_vu"]
    assert kd["bat_buoc"] is True
    assert kd["kieu"] == "bi_mat"
    # Mô tả đã được viết lại có chủ ý (điều phối 20/10): nói rõ khoá TỰ ĐẶT, cách sinh, đúng tên biến của dịch vụ.
    # Kiểm cấu trúc ý nghĩa thay vì nguyên văn: vẫn đúng tên biến, vẫn không hứa «API trả lại khoá».
    assert "OFFICE_KHOA" in kd["mo_ta"] and "TỰ ĐẶT" in kd["mo_ta"] and "không vào log" in kd["mo_ta"]
    # Trường OCR MỚI: không bắt buộc ở manifest (chỉ bắt buộc khi dùng OCR) và
    # mo_ta nói rõ lấy ở đâu.
    assert truong["diem_chat_url"]["bat_buoc"] is False
    assert truong["khoa_khach"]["bat_buoc"] is False
    assert "điểm chat" in truong["khoa_khach"]["mo_ta"]


# ───────────── doc_guard qua công cụ kiem_tai_lieu ─────────────

def _zip_b64(cac_tep: dict[str, bytes], nen=zipfile.ZIP_DEFLATED) -> str:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", nen) as z:
        for ten, nd in cac_tep.items():
            z.writestr(ten, nd)
    return base64.b64encode(buf.getvalue()).decode()


def _kiem(mod, tep_b64: str, ten: str = "tep.docx") -> dict:
    return _chay(mod.thuc_hien(ID_TT, "kiem_tai_lieu",
                               {"tep_b64": tep_b64, "ten": ten}))


def test_kiem_tai_lieu_docx_hop_le():
    mod = _module()
    kq = _kiem(mod, _zip_b64({"[Content_Types].xml": b"<Types/>",
                              "word/document.xml": b"<w:document/>"}))
    assert kq["ok"] is True, kq
    assert kq["loai"] == "zip"


def test_kiem_tai_lieu_chan_zip_bomb():
    mod = _module()
    # 2MB số 0 nén ZIP_DEFLATED → tỉ lệ nén ~1000:1, vượt trần 150:1 của doc_guard.
    bom = _zip_b64({"word/document.xml": b"\x00" * (2 * 1024 * 1024)})
    kq = _kiem(mod, bom)
    assert kq["ok"] is False
    assert "nén" in kq["loi"]


def test_kiem_tai_lieu_chan_zip_slip():
    mod = _module()
    kq = _kiem(mod, _zip_b64({"../evil.xml": b"x"}))
    assert kq["ok"] is False
    assert "đường dẫn" in kq["loi"]


def test_kiem_tai_lieu_chan_xxe():
    mod = _module()
    xxe = (b'<?xml version="1.0"?><!DOCTYPE w [<!ENTITY xxe SYSTEM '
           b'"file:///etc/passwd">]><w:document>&xxe;</w:document>')
    kq = _kiem(mod, _zip_b64({"word/document.xml": xxe}))
    assert kq["ok"] is False
    assert "ENTITY" in kq["loi"] or "thực thể" in kq["loi"]


def test_kiem_tai_lieu_tran_kich_thuoc():
    kg, _, _ = _khong_gian()
    # Trần byte qua tham số của đúng hàm port (doc_guard.kiem_tai_lieu).
    with pytest.raises(kg.DocRejected):
        kg.kiem_tai_lieu(b"x" * 2048, ten="lon.bin", max_bytes=1024)
    with pytest.raises(kg.DocRejected):
        kg.kiem_tai_lieu(b"", ten="rong.bin")


def test_kiem_tai_lieu_pdf_qua_nhieu_trang():
    kg, _, _ = _khong_gian()
    pdf = b"%PDF-1.4\n" + b"/Type /Page\n" * (kg.MAX_PDF_PAGES + 5)
    with pytest.raises(kg.DocRejected):
        kg.kiem_tai_lieu(pdf, ten="sach.pdf")


# ───────────── đối chiếu chống bịa (ocr_rules @ c2a) ─────────────

def test_doi_chieu_chong_bia():
    kg, _, _ = _khong_gian()
    doi_chieu = ("Hôm nay chúng ta học bài toán cộng trừ nhân chia phân số "
                 "và hình học phẳng trong chương trình lớp bốn. " * 4)
    bia = ("Con tàu vũ trụ bay lên mặt trăng rồi đáp xuống sao hoả trong "
           "một ngày nắng đẹp của mùa hè năm ngoái xa xôi. " * 4)
    assert kg.co_dau_hieu_bia(bia, doi_chieu) is True
    assert kg.co_dau_hieu_bia(doi_chieu, doi_chieu) is False
    # Bản đối chiếu quá ngắn → không kết luận (None), không vu oan.
    assert kg.ty_le_chu_la(bia, "ngắn") is None


# ───────────── cai(): lưu được từng nửa cấu hình ─────────────

def test_cai_chi_office_khong_can_khoa_chat():
    _, lop, m = _khong_gian()
    mod = lop(m)
    kq = _chay(mod.cai({"office_bat": "0",
                        "dich_vu_url": "http://127.0.0.1:8701",
                        "khoa_dich_vu": "khoa-gia-0123456789"}))
    assert kq["ok"] is True, kq
    assert kq["cau_hinh_luu"]["khoa_dich_vu"] == "khoa-gia-0123456789"


def test_cai_thieu_nua_cap_chat_bi_tu_choi():
    _, lop, m = _khong_gian()
    mod = lop(m)
    kq = _chay(mod.cai({"diem_chat_url": "https://chat.example.invalid/v1"}))
    assert kq["ok"] is False
    assert "khoa_khach" in kq["loi"]


# ───────────── bộ lọc log + mốc trang giả ─────────────

def test_bo_loc_log_chan_ca_hai_khoa(caplog):
    kg, _, _ = _khong_gian()
    ghi = logging.getLogger("httpx")
    loc = None
    for f in ghi.filters:
        if getattr(f, "_van_phong_loc", False):
            loc = f
            break
    assert loc is not None
    with caplog.at_level(logging.DEBUG, logger="httpx"):
        ghi.debug('Authorization: Bearer KHOA-BI-MAT-ABCDEF gọi %s', "x")
        ghi.debug("dòng sạch bình thường")
    # Mẫu «Authorization … Bearer» bị chặn theo CẤU TRÚC, dù khoá chưa nạp.
    assert "KHOA-BI-MAT-ABCDEF" not in caplog.text
    assert "dòng sạch bình thường" in caplog.text
    caplog.clear()
    # Đăng ký khoá thật vào tập chặn rồi kiểm lại.
    kg._KHOA_DANG_DUNG.add("KHOA-BI-MAT-ABCDEF")
    with caplog.at_level(logging.DEBUG, logger="httpx"):
        ghi.debug("dòng chứa KHOA-BI-MAT-ABCDEF ở giữa")
    assert "KHOA-BI-MAT-ABCDEF" not in caplog.text
    kg._KHOA_DANG_DUNG.discard("KHOA-BI-MAT-ABCDEF")


def test_moc_trang_gia_do_model_chen_bi_go():
    mod = _module()
    kg, _, _ = _khong_gian()
    van = "<<<TRANG 99>>>\nnội dung trang một"
    ket = []

    def xu_ly(yeu_cau: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "choices": [{"message": {"role": "assistant", "content": van}}]})

    _, lop, m = _khong_gian()
    mod = lop(m, _transport=httpx.MockTransport(xu_ly))
    mod.nap({"diem_chat_url": "http://127.0.0.1:9/v1", "khoa_khach": "k"})
    kq = _chay(mod.thuc_hien(ID_TT, "pdf_thanh_anh", {
        "anh_b64_list": [base64.b64encode(
            base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
        ).decode()],
        "model": "m",
    }))
    assert kq["ok"] is True, kq
    assert "<<<TRANG 99>>>" not in kq["van_ban_gop"]
    assert kg.pages_seen(kq["van_ban_gop"]) == {1}


def test_tep_anh_ngoai_thu_muc_module_bi_chan(tmp_path, monkeypatch):
    """Ảnh THẬT nằm ở thư mục anh em — resolve() ra ngoài thư mục module → chặn."""
    kg, lop, m = _khong_gian()
    goc = tmp_path / "van-phong"
    goc.mkdir()
    ngoai = tmp_path / "van-phong-bac"
    ngoai.mkdir()
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
    (ngoai / "anh.png").write_bytes(png)
    monkeypatch.setattr(kg, "thu_muc_modules", lambda: tmp_path)
    mod = lop(m, _transport=None)
    mod.nap({"diem_chat_url": "http://127.0.0.1:9/v1", "khoa_khach": "k"})
    kq = _chay(mod.thuc_hien(ID_TT, "ocr_anh",
                             {"tep": "../van-phong-bac/anh.png", "model": "m"}))
    assert kq["ok"] is False
    assert "ngoài thư mục" in kq["loi"]
