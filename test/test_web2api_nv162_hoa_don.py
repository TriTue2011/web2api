"""Module hoa-don — đối chiếu hoá đơn điện tử (NĐ 123/2020, TT 78/2021) với bảng danh mục (NV162).

Port từ c2a services/hoa_don.py (d879b03) theo LUẬT PORT. Khác biệt có chủ đích:
không đọc tệp theo đường dẫn (nhận nội dung qua tham số), không pandas (đọc .xlsx
bằng zipfile + ElementTree, trần giải nén, công thức không thực thi), số tiền là
Decimal (c2a dùng float), PDF chỉ nhận văn bản đã trích (điểm nối van-phong NV156).

An toàn: XML là dữ liệu KHÔNG TIN — DOCTYPE/ENTITY bị từ chối (chống nổ thực thể),
trần kích thước/độ sâu/số phần tử; .xlsx chống zip bomb; mẫu hoá đơn lạ → báo
«không đọc được», không đoán.
"""

from __future__ import annotations

import asyncio
import base64
import importlib.util
import io
import json
import zipfile
from pathlib import Path

import pytest

GOC = Path(__file__).resolve().parents[1]
THU_MUC = GOC / "modules" / "hoa-don"


def _module_py():
    p = THU_MUC / "module.py"
    spec = importlib.util.spec_from_file_location("hoa_don_nv162", p)
    md = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(md)
    return md


@pytest.fixture()
def md():
    return _module_py()


@pytest.fixture()
def mod(md):
    manifest = json.loads((THU_MUC / "manifest.json").read_text(encoding="utf-8"))
    m = md.Module(manifest)
    m.nap({})
    return m


def chay(coro):
    return asyncio.run(coro)


# ── Dữ liệu giả: hoá đơn TT78 và danh mục khớp ─────────────────────────────
XML_HOP_LE = """<?xml version="1.0" encoding="utf-8"?>
<HDon><DLHDon>
<TTChung><KHMSHDon>1</KHMSHDon><KHHDon>AA/23E</KHHDon><SHDon>123</SHDon><NLap>2026-10-01</NLap></TTChung>
<NDHDon>
<NBan><Ten>Cong ty TNHH Vi Du</Ten><MST>0101234567</MST></NBan>
<NMua><Ten>Cong ty Mua Hang</Ten><MST>0307654321</MST></NMua>
<DSHHDVu>
<HHDVu><STT>1</STT><TChat>1</TChat><THHDVu>Giay in A4</THHDVu><MHHDVu>GIA4</MHHDVu><DVTinh>Ram</DVTinh><SLuong>10</SLuong><DGia>80000</DGia><STCKhau>0</STCKhau><ThTien>800000</ThTien><TSuat>8%</TSuat></HHDVu>
<HHDVu><STT>2</STT><TChat>1</TChat><THHDVu>But bi xanh</THHDVu><MHHDVu>BBX</MHHDVu><DVTinh>Hop</DVTinh><SLuong>5</SLuong><DGia>50000</DGia><STCKhau>0</STCKhau><ThTien>250000</ThTien><TSuat>8%</TSuat></HHDVu>
</DSHHDVu>
<TToan><TgTCThue>1050000</TgTCThue><TgTThue>84000</TgTThue><TTCKTMai>0</TTCKTMai><TgTTTBSo>1134000</TgTTTBSo></TToan>
</NDHDon></DLHDon></HDon>"""

DANH_MUC_KHOP = [
    ["STT", "Ma hang", "Ten hang hoa", "Don vi tinh", "So luong", "Don gia", "Thanh tien"],
    [1, "GIA4", "Giay in A4", "Ram", 10, "80.000", "800.000"],
    [2, "BBX", "But bi xanh", "Hop", 5, "50.000", "250.000"],
]

ID_CONG_CU = "hoa_don.cong_cu"


def doc(mod, **ts):
    return chay(mod.thuc_hien(ID_CONG_CU, "doc_hoa_don", ts))


def doi_chieu(mod, **ts):
    return chay(mod.thuc_hien(ID_CONG_CU, "doi_chieu_hoa_don", ts))


# ── Manifest / thực thể ─────────────────────────────────────────────────────
def test_manifest_hop_le(md):
    from app.kham_pha import kiem_manifest
    manifest = json.loads((THU_MUC / "manifest.json").read_text(encoding="utf-8"))
    assert kiem_manifest(manifest) is None
    assert manifest["id"] == "hoa-don"
    assert manifest["tien_to"] == "hoa_don"
    assert manifest["truong_cau_hinh"] == []      # thuần tính toán, không khoá, không mạng


def test_thuc_the_cong_cu_doc(mod):
    import asyncio
    # NV181: thuc_the là async def (hợp đồng lõi).
    ds = asyncio.run(mod.thuc_the())
    assert len(ds) == 1
    e = ds[0]
    assert e["id"] == ID_CONG_CU and e["loai"] == "cong_cu"
    assert e["rui_ro_mac_dinh"] == "doc"
    hd = {t["hanh_dong"]: t for t in e["tinh_nang"]}
    assert set(hd) == {"doc_hoa_don", "doi_chieu_hoa_don"}
    assert all(t["rui_ro"] == "doc" for t in hd.values())


def test_suc_khoe(mod):
    assert chay(mod.suc_khoe())["ok"] is True


def test_hanh_dong_la_bi_tu_choi(mod):
    kq = chay(mod.thuc_hien(ID_CONG_CU, "xoa_hoa_don", {"xml": XML_HOP_LE}))
    assert kq["ok"] is False and kq["loi"] == "hanh_dong_khong_ho_tro"
    kq = chay(mod.thuc_hien("hoa_don.la", "doc_hoa_don", {"xml": XML_HOP_LE}))
    assert kq["ok"] is False and kq["loi"] == "khong_thay_thuc_the"


# ── Đọc XML ─────────────────────────────────────────────────────────────────
def test_doc_xml_hop_le(mod):
    kq = doc(mod, xml=XML_HOP_LE)
    assert kq["ok"] is True and kq["nguon_ngoai"] is True
    d = kq["du_lieu"]
    assert d["so"] == "123" and d["ky_hieu"] == "AA/23E"
    assert d["ban"]["mst"] == "0101234567"            # MST giữ nguyên chuỗi, số 0 đầu không mất
    assert len(d["dong"]) == 2
    assert d["dong"][0]["ten"] == "Giay in A4" and d["dong"][0]["ma"] == "GIA4"
    assert d["tong"]["thanh_toan"] == "1134000"        # Decimal → chuỗi, không qua float
    assert d["dong"][0]["tt"] == "800000"


def test_doc_xml_tien_decimal_chinh_xac(mod):
    # 3 × 100000.55 = 300001.65 — qua float sẽ thành 300001.65000000004 và phép tính «sai» giả.
    xml = XML_HOP_LE.replace("<DGia>80000</DGia>", "<DGia>100000.55</DGia>") \
                    .replace("<SLuong>10</SLuong>", "<SLuong>3</SLuong>") \
                    .replace("<ThTien>800000</ThTien>", "<ThTien>300001.65</ThTien>")
    kq = doc(mod, xml=xml)
    assert kq["ok"] is True
    assert kq["du_lieu"]["dong"][0]["tt"] == "300001.65"
    md_kq = doi_chieu(mod, xml=xml, danh_muc=[
        ["Ten hang hoa", "So luong", "Don gia", "Thanh tien"],
        ["Giay in A4", 3, "100.000,55", "300.001,65"],
        ["But bi xanh", 5, "50.000", "250.000"],
    ])
    assert md_kq["ok"] is True
    assert "Phép tính trên hóa đơn" in md_kq["bao_cao"]


def test_doc_xml_khong_phai_hoa_don(mod):
    kq = doc(mod, xml="<GiayTo><Ten>abc</Ten></GiayTo>")
    assert kq["ok"] is False and "loi" in kq


def test_doc_xml_hong(mod):
    kq = doc(mod, xml="<HDon><DLHDon><TTChung>")
    assert kq["ok"] is False


def test_doc_xml_tham_so_sai_kieu(mod):
    assert doc(mod, xml=123)["ok"] is False
    assert doc(mod)["ok"] is False
    assert doi_chieu(mod, xml=XML_HOP_LE, danh_muc="khong-phai-danh-sach")["ok"] is False


def test_xml_doctype_entity_bi_tu_choi(mod):
    bom = """<?xml version="1.0"?>
<!DOCTYPE HDon [<!ENTITY a "xxxxxxxxxx"><!ENTITY b "&a;&a;&a;&a;&a;">]>
<HDon><DLHDon><DSHHDVu><HHDVu><THHDVu>&b;</THHDVu></HHDVu></DSHHDVu></DLHDon></HDon>"""
    kq = doc(mod, xml=bom)
    assert kq["ok"] is False


def test_xml_qua_tran_kich_thuoc(mod, md):
    lon = XML_HOP_LE + "<!--" + "x" * (md.TRAN_XML + 10) + "-->"
    assert doc(mod, xml=lon)["ok"] is False


def test_xml_long_sau_qua_tran(mod, md):
    sau = "<a>" * (md.TRAN_DO_SAU + 5) + "</a>" * (md.TRAN_DO_SAU + 5)
    kq = doc(mod, xml=f"<HDon>{sau}</HDon>")
    assert kq["ok"] is False and "sâu" in kq["loi"]    # phải chết vì trần độ sâu, không phải vì thiếu HHDVu


# ── Đối chiếu ───────────────────────────────────────────────────────────────
def test_doi_chieu_khop(mod):
    kq = doi_chieu(mod, xml=XML_HOP_LE, danh_muc=DANH_MUC_KHOP)
    assert kq["ok"] is True
    assert "khớp danh mục" in kq["bao_cao"]
    assert "ghép được 2 cặp" in kq["bao_cao"]


def test_doi_chieu_lech_don_gia(mod):
    dm = [list(r) for r in DANH_MUC_KHOP]
    dm[2][5] = "55.000"
    dm[2][6] = "275.000"
    kq = doi_chieu(mod, xml=XML_HOP_LE, danh_muc=dm)
    assert kq["ok"] is True
    assert "đơn giá" in kq["bao_cao"] and "But bi xanh" in kq["bao_cao"]


def test_doi_chieu_thua_thieu_dong(mod):
    dm = [list(r) for r in DANH_MUC_KHOP]
    dm.append([3, "KEP", "Kep giay", "Hop", 2, "15.000", "30.000"])
    kq = doi_chieu(mod, xml=XML_HOP_LE, danh_muc=dm)
    assert "Chỉ có trong danh mục" in kq["bao_cao"] and "Kep giay" in kq["bao_cao"]
    kq2 = doi_chieu(mod, xml=XML_HOP_LE, danh_muc=DANH_MUC_KHOP[:2])
    assert "Chỉ có trên hóa đơn" in kq2["bao_cao"] and "But bi xanh" in kq2["bao_cao"]


def test_doi_chieu_phep_tinh_sai_tren_hoa_don(mod):
    xml = XML_HOP_LE.replace("<ThTien>250000</ThTien>", "<ThTien>240000</ThTien>")
    kq = doi_chieu(mod, xml=xml, danh_muc=DANH_MUC_KHOP)
    assert "❌" in kq["bao_cao"]


def test_doi_chieu_gia_danh_muc_da_gom_thue(mod):
    # Cả ba dòng danh mục đều = hoá đơn × 1,08 → «giá đã gồm thuế 8%» (cần ≥ 3 dòng, khớp c2a).
    xml = XML_HOP_LE.replace(
        "</DSHHDVu>",
        "<HHDVu><STT>3</STT><TChat>1</TChat><THHDVu>Keo dan</THHDVu><MHHDVu>KEO</MHHDVu>"
        "<DVTinh>Tuyp</DVTinh><SLuong>4</SLuong><DGia>25000</DGia><STCKhau>0</STCKhau>"
        "<ThTien>100000</ThTien><TSuat>8%</TSuat></HHDVu></DSHHDVu>")
    dm = [["Ten hang hoa", "So luong", "Don gia", "Thanh tien"],
          ["Giay in A4", 10, "86.400", "864.000"],
          ["But bi xanh", 5, "54.000", "270.000"],
          ["Keo dan", 4, "27.000", "108.000"]]
    kq = doi_chieu(mod, xml=xml, danh_muc=dm)
    assert "ĐÃ GỒM" in kq["bao_cao"] and "8%" in kq["bao_cao"]


def test_ghep_ten_gan_dung(mod):
    # Mã KHÁC nhau và tên lệch một ký tự → chỉ ghép được nhờ bước tên gần đúng ≥ 0,85.
    dm = [list(r) for r in DANH_MUC_KHOP]
    dm[1][1] = "GIA4X"
    dm[1][2] = "Giay in A44"
    kq = doi_chieu(mod, xml=XML_HOP_LE, danh_muc=dm)
    assert "ghép được 2 cặp" in kq["bao_cao"]
    assert "ghép gần đúng" in kq["bao_cao"]


def test_dong_tong_cong_khong_phai_hang(mod):
    # Dòng «Tổng cộng» trong danh mục là dòng TỔNG, không phải mặt hàng — không được báo thừa.
    dm = [list(r) for r in DANH_MUC_KHOP]
    dm.append(["", "", "Tong cong", "", "", "", "1.050.000"])
    kq = doi_chieu(mod, xml=XML_HOP_LE, danh_muc=dm)
    assert kq["ok"] is True
    assert "Tong cong" not in kq["bao_cao"]


def test_doc_xml_so_thap_phan_dai_chinh_xac(mod):
    # Số thập phân dài phải quay về ĐÚNG chuỗi gốc — qua float là mất chữ số (Decimal không được thay).
    xml = XML_HOP_LE.replace("<DGia>80000</DGia>", "<DGia>123456.7890123456789</DGia>")
    kq = doc(mod, xml=xml)
    assert kq["ok"] is True
    assert kq["du_lieu"]["dong"][0]["dg"] == "123456.7890123456789"


def test_doi_chieu_danh_muc_khong_co_tieu_de(mod):
    kq = doi_chieu(mod, xml=XML_HOP_LE, danh_muc=[["a", "b"], ["c", "d"]])
    assert kq["ok"] is False and "loi" in kq


def test_bao_cao_bi_cat_o_tran(mod, md):
    dong_dm = [["Ten hang hoa", "So luong", "Don gia", "Thanh tien"]]
    for i in range(600):
        dong_dm.append([f"Mat hang so {i:04d} khong co tren hoa don", 1, "1.000", "1.000"])
    kq = doi_chieu(mod, xml=XML_HOP_LE, danh_muc=dong_dm)
    assert kq["ok"] is True
    assert len(kq["bao_cao"]) <= md.TRAN_DAU_RA + 20
