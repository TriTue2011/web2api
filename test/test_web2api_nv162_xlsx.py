"""Module hoa-don (NV162) — đọc .xlsx bằng zipfile với trần giải nén, chống zip bomb,
công thức không thực thi; điểm nối văn bản PDF đã trích (van-phong NV156 làm phần trích).
"""

from __future__ import annotations

import base64
import importlib.util
import io
import zipfile
from pathlib import Path

import pytest

GOC = Path(__file__).resolve().parents[1]
THU_MUC = GOC / "modules" / "hoa-don"

ID_CONG_CU = "hoa_don.cong_cu"


def _md():
    p = THU_MUC / "module.py"
    spec = importlib.util.spec_from_file_location("hoa_don_nv162_xlsx", p)
    md = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(md)
    return md


@pytest.fixture()
def md():
    return _md()


@pytest.fixture()
def mod(md):
    import json
    manifest = json.loads((THU_MUC / "manifest.json").read_text(encoding="utf-8"))
    m = md.Module(manifest)
    m.nap({})
    return m


def chay(coro):
    import asyncio
    return asyncio.run(coro)


def doi_chieu(mod, **ts):
    return chay(mod.thuc_hien(ID_CONG_CU, "doi_chieu_hoa_don", ts))


def xlsx_bytes(rows, *, cong_thuc_v=None):
    """Dựng .xlsx tối thiểu: shared strings + một sheet."""
    chuoi = []
    idx = {}
    def si(s):
        s = str(s)
        if s not in idx:
            idx[s] = len(chuoi)
            chuoi.append(s)
        return idx[s]
    o = ["<worksheet xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'><sheetData>"]
    for r, hang in enumerate(rows, start=1):
        o.append(f"<row r='{r}'>")
        for c, v in enumerate(hang):
            ten_o = f"{chr(65 + c)}{r}"
            if cong_thuc_v is not None and (r, c) in cong_thuc_v:
                o.append(f"<c r='{ten_o}'><f>{cong_thuc_v[(r, c)]}</f><v>{si(cong_thuc_v['v'])}</v></c>")
            elif isinstance(v, (int, float)):
                o.append(f"<c r='{ten_o}'><v>{v}</v></c>")
            else:
                o.append(f"<c r='{ten_o}' t='s'><v>{si(v)}</v></c>")
        o.append("</row>")
    o.append("</sheetData></worksheet>")
    ss = "".join(f"<si><t>{x}</t></si>" for x in chuoi)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("xl/sharedStrings.xml",
                   f"<sst xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'>{ss}</sst>")
        z.writestr("xl/worksheets/sheet1.xml", "".join(o))
    return buf.getvalue()


def xlsx_cong_thuc_khong_gia_tri():
    """Ô D2 là công thức CHƯA có giá trị lưu (tệp chưa được Excel tính lại)."""
    ss = "".join(f"<si><t>{x}</t></si>" for x in
                 ["Ten hang hoa", "So luong", "Don gia", "Thanh tien", "Giay in A4", "80.000"])
    sh = ("<worksheet xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'>"
          "<sheetData>"
          "<row r='1'><c r='A1' t='s'><v>0</v></c><c r='B1' t='s'><v>1</v></c>"
          "<c r='C1' t='s'><v>2</v></c><c r='D1' t='s'><v>3</v></c></row>"
          "<row r='2'><c r='A2' t='s'><v>4</v></c><c r='B2'><v>10</v></c>"
          "<c r='C2' t='s'><v>5</v></c>"
          "<c r='D2'><f>SUM(A1:A999999)</f></c></row>"
          "</sheetData></worksheet>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/sharedStrings.xml",
                   f"<sst xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'>{ss}</sst>")
        z.writestr("xl/worksheets/sheet1.xml", sh)
    return buf.getvalue()


DANH_MUC_DONG = [
    ["STT", "Ma hang", "Ten hang hoa", "Don vi tinh", "So luong", "Don gia", "Thanh tien"],
    [1, "GIA4", "Giay in A4", "Ram", 10, "80.000", "800.000"],
    [2, "BBX", "But bi xanh", "Hop", 5, "50.000", "250.000"],
]

XML_HOP_LE = (Path(__file__).parent / "test_web2api_nv162_hoa_don.py").read_text().split(
    'XML_HOP_LE = """')[1].split('"""')[0]


def test_doi_chieu_xlsx_base64_khop(mod):
    b64 = base64.b64encode(xlsx_bytes(DANH_MUC_DONG)).decode()
    kq = doi_chieu(mod, xml=XML_HOP_LE, xlsx_base64=b64)
    assert kq["ok"] is True
    assert "khớp danh mục" in kq["bao_cao"]


def test_xlsx_cong_thuc_khong_thuc_thi_dung_gia_tri_dau(md):
    # Công thức kiểu «=SUM(…)» của trang ngoài không bao giờ được chạy — ô không có
    # giá trị lưu thì bỏ qua, dòng vẫn đọc được từ các ô còn lại.
    dong = md.doc_xlsx_dong(xlsx_cong_thuc_khong_gia_tri())
    assert "dong" in dong and len(dong["dong"]) == 1
    assert dong["dong"][0]["ten"] == "Giay in A4"
    assert dong["dong"][0]["tt"] is None      # không dùng index rác, không chạy công thức
    assert dong["dong"][0]["dg"] == "80000"


def test_xlsx_zip_bomb_bi_tu_choi(mod):
    # Bom ZIP thật: 60 MB dữ liệu lặp nén xuống ~60 KB (tỉ lệ ~1000×), bung ra vượt trần.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("xl/worksheets/sheet1.xml", "0" * 60_000_000)
    b64 = base64.b64encode(buf.getvalue()).decode()
    kq = doi_chieu(mod, xml=XML_HOP_LE, xlsx_base64=b64)
    assert kq["ok"] is False and "loi" in kq


def test_xlsx_zip_bomb_ty_le_cao(mod):
    # Bom tỉ lệ: một tệp con 2 MB lặp (dưới mọi trần khác) nén xuống ~2 KB (tỉ lệ ~1000×)
    # — chỉ trần TỈ LỆ NÉN mới bắt được; tệp vẫn là .xlsx hợp lệ ở mọi mặt còn lại.
    buf = io.BytesIO(xlsx_bytes(DANH_MUC_DONG))
    with zipfile.ZipFile(buf, "a", zipfile.ZIP_DEFLATED) as z:
        z.writestr("xl/worksheets/sheet9.xml", "0" * 2_000_000)
    b64 = base64.b64encode(buf.getvalue()).decode()
    kq = doi_chieu(mod, xml=XML_HOP_LE, xlsx_base64=b64)
    assert kq["ok"] is False and "loi" in kq


def test_xlsx_khong_phai_zip(mod):
    b64 = base64.b64encode(b"khong phai zip").decode()
    assert doi_chieu(mod, xml=XML_HOP_LE, xlsx_base64=b64)["ok"] is False


def test_xlsx_khong_co_tieu_de(mod):
    b64 = base64.b64encode(xlsx_bytes([["a", "b"], ["c", "d"]])).decode()
    kq = doi_chieu(mod, xml=XML_HOP_LE, xlsx_base64=b64)
    assert kq["ok"] is False


def test_doc_van_ban_pdf_da_trich(mod):
    # Điểm nối: PDF do van-phong (NV156) trích thành Markdown — module chỉ phân tích văn bản.
    md = """| STT | Ten hang hoa | Don vi | So luong | Don gia | Thanh tien |
| --- | --- | --- | --- | --- | --- |
| 1 | Giay in A4 | Ram | 10 | 80.000 | 800.000 |
| 2 | But bi xanh | Hop | 5 | 50.000 | 250.000 |
"""
    kq = doi_chieu(mod, van_ban_pdf=md, danh_muc=DANH_MUC_DONG)
    assert kq["ok"] is True
    assert "khớp danh mục" in kq["bao_cao"]
    assert "PDF" in kq["bao_cao"]    # báo cáo NÓI RÕ nguồn là PDF (bản in), kém chắc hơn XML


def test_thieu_ca_hai_nguon_danh_muc(mod):
    kq = doi_chieu(mod, xml=XML_HOP_LE)
    assert kq["ok"] is False and "loi" in kq


def test_xlsx_dong_gioi_han_du_lieu(md):
    # Ô inline string (t="inlineStr") và ô kiểu str (t="str", kết quả công thức dạng chữ) phải đọc được.
    ss_xml = "<sst/>"
    sh = ("<worksheet xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'>"
          "<sheetData>"
          "<row r='1'><c r='A1' t='inlineStr'><is><t>Ten hang hoa</t></is></c>"
          "<c r='B1' t='inlineStr'><is><t>So luong</t></is></c>"
          "<c r='C1' t='inlineStr'><is><t>Don gia</t></is></c>"
          "<c r='D1' t='inlineStr'><is><t>Thanh tien</t></is></c></row>"
          "<row r='2'><c r='A2' t='inlineStr'><is><t>Giay in A4</t></is></c>"
          "<c r='B2'><v>10</v></c>"
          "<c r='C2' t='str'><f>CONCAT(A1)</f><v>80.000</v></c>"
          "<c r='D2' t='str'><v>800.000</v></c></row>"
          "</sheetData></worksheet>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/worksheets/sheet1.xml", sh)
    dong = md.doc_xlsx_dong(buf.getvalue())
    assert "dong" in dong
    assert dong["dong"][0]["dg"] == "80000"
