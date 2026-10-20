"""NV162-SUA — hoa-don: E2 (DOCTYPE/ENTITY qua mặt bằng XML mã hoá UTF-16/32) + E3 (đối chiếu O(n·m)).

Theo soát độc lập B mục 6:
- E2: `_xml_an_toan` dò `b"<!doctype"` trên BYTE THÔ → tài liệu UTF-16 (BOM hoặc không BOM,
  cả cửa tham số `xml` dạng chuỗi xen NUL lẫn sheet/sharedStrings trong .xlsx) lọt qua và
  thực thể nội bộ bị mở rộng. Vá: giải mã đúng bảng mã TRƯỚC rồi kiểm trên văn bản đã giải mã;
  khai báo `encoding=` trong prolog khác byte thật → từ chối.
- E3: `_ghep` O(n·m) (gọi `_gap` + SequenceMatcher trong vòng lồng) chạy ĐỒNG BỘ trong
  `thuc_hien` → chặn vòng lặp sự kiện. Vá: tiền tính khoá một lần + chỉ mục băm theo mã/tên,
  trần số dòng, chạy đối chiếu ngoài vòng lặp sự kiện (to_thread).

E1 (nối lõi) thuộc NV181 — KHÔNG nằm trong tệp này.
"""

from __future__ import annotations

import asyncio
import importlib.util
import io
import json
import time
import zipfile
from pathlib import Path

import pytest

GOC = Path(__file__).resolve().parents[1]
THU_MUC = GOC / "modules" / "hoa-don"
ID_CONG_CU = "hoa_don.cong_cu"


def _module_py():
    p = THU_MUC / "module.py"
    spec = importlib.util.spec_from_file_location("hoa_don_nv162_sua", p)
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


def doc(mod, **ts):
    return chay(mod.thuc_hien(ID_CONG_CU, "doc_hoa_don", ts))


# ── Dữ liệu tấn công / hợp lệ ────────────────────────────────────────────────
XML_TAN_CONG = (
    '<?xml version="1.0"?>\n'
    '<!DOCTYPE HDon [<!ENTITY a "ENTITY-DA-MO-RONG-OK">]>\n'
    "<HDon><DLHDon><NDHDon><DSHHDVu>"
    "<HHDVu><THHDVu>&a;</THHDVu></HHDVu>"
    "</DSHHDVu></NDHDon></DLHDon></HDon>"
)

XML_HOP_LE = """<?xml version="1.0" encoding="utf-8"?>
<HDon><DLHDon>
<TTChung><KHMSHDon>1</KHMSHDon><KHHDon>AA/23E</KHHDon><SHDon>123</SHDon><NLap>2026-10-01</NLap></TTChung>
<NDHDon>
<NBan><Ten>Cong ty TNHH Vi Du</Ten><MST>0101234567</MST></NBan>
<NMua><Ten>Cong ty Mua Hang</Ten><MST>0307654321</MST></NMua>
<DSHHDVu>
<HHDVu><STT>1</STT><TChat>1</TChat><THHDVu>Giay in A4</THHDVu><MHHDVu>GIA4</MHHDVu><DVTinh>Ram</DVTinh><SLuong>10</SLuong><DGia>80000</DGia><STCKhau>0</STCKhau><ThTien>800000</ThTien><TSuat>8%</TSuat></HHDVu>
</DSHHDVu>
<TToan><TgTCThue>800000</TgTCThue><TgTThue>64000</TgTThue><TTCKTMai>0</TTCKTMai><TgTTTBSo>864000</TgTTTBSo></TToan>
</NDHDon></DLHDon></HDon>"""


def _xlsx_voi(sheet_bytes: bytes, ss_bytes: bytes | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("xl/worksheets/sheet1.xml", sheet_bytes)
        if ss_bytes is not None:
            z.writestr("xl/sharedStrings.xml", ss_bytes)
    return buf.getvalue()


# ── E2: DOCTYPE/ENTITY không được qua mặt bằng bảng mã ─────────────────────
@pytest.mark.parametrize("ma_hoa,ten", [
    ("bom-le", "utf-16-le-bom"), ("bom-be", "utf-16-be-bom"),
    ("le", "utf-16-le-khong-bom"), ("be", "utf-16-be-khong-bom"),
    ("32le", "utf-32-le-bom"), ("32be", "utf-32-be-bom"),
])
def test_e2_doctype_utf16_32_bi_tu_choi(md, ma_hoa, ten):
    if ma_hoa == "bom-le":
        data = b"\xff\xfe" + XML_TAN_CONG.encode("utf-16-le")
    elif ma_hoa == "bom-be":
        data = b"\xfe\xff" + XML_TAN_CONG.encode("utf-16-be")
    elif ma_hoa == "le":
        data = XML_TAN_CONG.encode("utf-16-le")
    elif ma_hoa == "be":
        data = XML_TAN_CONG.encode("utf-16-be")
    elif ma_hoa == "32le":
        data = b"\xff\xfe\x00\x00" + XML_TAN_CONG.encode("utf-32-le")
    else:
        data = b"\x00\x00\xfe\xff" + XML_TAN_CONG.encode("utf-32-be")
    kq = md.doc_xml(data)
    assert "loi" in kq, f"{ten}: DOCTYPE trong XML {ten} phải bị từ chối, nhận: {str(kq)[:120]}"
    assert "ENTITY-DA-MO-RONG-OK" not in json.dumps(kq, ensure_ascii=False)


def test_e2_doctype_chuoi_xen_nul_bi_tu_choi(mod):
    # Cửa (a) của soát: tham số `xml` là CHUỖI có ký tự NUL xen kẽ — UTF-8 của nó
    # giống hệt UTF-16LE không BOM, expat tự nhận bảng mã đó.
    xen_nul = "".join(c + "\x00" for c in XML_TAN_CONG)
    kq = doc(mod, xml=xen_nul)
    assert kq["ok"] is False
    assert "ENTITY-DA-MO-RONG-OK" not in json.dumps(kq, ensure_ascii=False)


def test_e2_xlsx_sheet_utf16_doctype_bi_tu_choi(md):
    sheet = (
        '<?xml version="1.0" encoding="UTF-16"?>\n'
        '<!DOCTYPE worksheet [<!ENTITY a "ENTITY-DA-MO-RONG-OK">]>\n'
        "<worksheet xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'>"
        "<sheetData><row r='1'><c r='A1' t='inlineStr'><is><t>&a;</t></is></c></row>"
        "</sheetData></worksheet>"
    )
    data = _xlsx_voi(b"\xff\xfe" + sheet.encode("utf-16-le"))
    kq = md.doc_xlsx_hang(data)
    assert isinstance(kq, dict) and "loi" in kq, f"sheet UTF-16 có DOCTYPE phải bị từ chối: {str(kq)[:120]}"


def test_e2_xlsx_sharedstrings_utf16_doctype_bi_tu_choi(md):
    sheet = (
        "<worksheet xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'>"
        "<sheetData><row r='1'><c r='A1' t='s'><v>0</v></c></row></sheetData></worksheet>"
    )
    ss = (
        '<?xml version="1.0" encoding="UTF-16"?>\n'
        '<!DOCTYPE sst [<!ENTITY a "ENTITY-DA-MO-RONG-OK">]>\n'
        "<sst xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'>"
        "<si><t>&a;</t></si></sst>"
    )
    data = _xlsx_voi(sheet.encode("utf-8"), b"\xff\xfe" + ss.encode("utf-16-le"))
    kq = md.doc_xlsx_hang(data)
    assert isinstance(kq, dict) and "loi" in kq, f"sharedStrings UTF-16 có DOCTYPE phải bị từ chối: {str(kq)[:120]}"


def test_e2_khai_bao_encoding_khac_byte_that_bi_tu_choi(md):
    # Prolog nói utf-8 nhưng byte thật là UTF-16LE (có BOM) → không khớp → từ chối.
    data = b"\xff\xfe" + XML_HOP_LE.encode("utf-16-le")
    kq = md.doc_xml(data)
    assert "loi" in kq
    # Ngược lại: prolog nói UTF-16 nhưng byte thật là UTF-8 → từ chối.
    lech = XML_HOP_LE.replace('encoding="utf-8"', 'encoding="UTF-16"').encode("utf-8")
    kq2 = md.doc_xml(lech)
    assert "loi" in kq2


def test_e2_utf16_hop_le_khong_doctype_van_doc_duoc(md):
    # Vá KHÔNG được cấm blanket UTF-16: hoá đơn UTF-16 khai đúng, không DOCTYPE → đọc bình thường.
    van_ban = XML_HOP_LE.replace('encoding="utf-8"', 'encoding="UTF-16"')
    data = van_ban.encode("utf-16")          # có BOM, khớp khai báo
    kq = md.doc_xml(data)
    assert "loi" not in kq, f"UTF-16 hợp lệ phải đọc được: {str(kq)[:160]}"
    assert len(kq["dong"]) == 1 and kq["dong"][0]["ten"] == "Giay in A4"


def test_e2_doctype_utf8_thuong_van_bi_tu_choi(mod):
    # Đường cũ vẫn phải chặn (hồi quy).
    kq = doc(mod, xml=XML_TAN_CONG)
    assert kq["ok"] is False


# ── E3: đối chiếu không còn O(n·m), không chặn vòng lặp sự kiện ────────────
def _dong(ten, ma=""):
    return {"stt": "", "ten": ten, "ma": ma, "dvt": "", "sl": None, "dg": None,
            "tt": None, "thue_suat": ""}


def test_e3_ghep_theo_ma_thang_ten_khop_dung(md):
    # Mã khớp phải thắng: dòng danh mục có TÊN khớp đúng nhưng mã khác không được cướp cặp.
    hd = [_dong("Kep giay dac biet", ma="KEP")]
    dm = [_dong("Kep giay dac biet", ma="KHAC"), _dong("Thuoc kep", ma="KEP")]
    cap, chua, thua = md._ghep(hd, dm)
    assert cap == [(0, 1)] and chua == [] and thua == [0]


def test_e3_ghep_ten_trung_chi_dung_mot_lan(md):
    hd = [_dong("Giay in A4"), _dong("Giay in A4")]
    dm = [_dong("Giay in A4")]
    cap, chua, thua = md._ghep(hd, dm)
    assert cap == [(0, 0)] and chua == [1] and thua == []


def test_e3_ghep_bang_lon_nhanh(md):
    # Tên lệch đúng một ký tự cuối → KHÔNG khớp đúng tên, buộc qua đường gần giống cho mọi dòng.
    n = 300
    hd = [_dong(f"Mat hang {i:04d} phien ban A") for i in range(n)]
    dm = [_dong(f"Mat hang {i:04d} phien ban B") for i in range(n)]
    t0 = time.perf_counter()
    cap, chua, thua = md._ghep(hd, dm)
    dt = time.perf_counter() - t0
    assert len(cap) == n
    assert dt < 2.0, f"_ghep {n}x{n} mất {dt:.2f}s — vẫn O(n·m) (bản cũ ~16s ở cỡ này)"


def test_e3_tran_so_dong_hoa_don(md):
    hd = [_dong(f"Hang {i}") for i in range(md.TRAN_DONG + 1)]
    with pytest.raises(md.LoiDuLieu):
        md._ghep(hd, [])


def test_e3_doi_chieu_khong_chan_vong_lap(mod):
    n = 300
    dong_xml = "".join(
        f"<HHDVu><STT>{i}</STT><TChat>1</TChat><THHDVu>Mat hang {i:04d} phien ban A</THHDVu>"
        f"<DVTinh>Cai</DVTinh><SLuong>1</SLuong><DGia>1000</DGia><STCKhau>0</STCKhau>"
        f"<ThTien>1000</ThTien><TSuat>8%</TSuat></HHDVu>" for i in range(n))
    xml = (f'<?xml version="1.0" encoding="utf-8"?><HDon><DLHDon><NDHDon><DSHHDVu>{dong_xml}'
           "</DSHHDVu></NDHDon></DLHDon></HDon>")
    dm = [["Ten hang hoa", "So luong", "Don gia", "Thanh tien"]] + [
        [f"Mat hang {i:04d} phien ban B", 1, "1.000", "1.000"] for i in range(n)]

    async def chinh():
        dem = 0
        xong = False

        async def nhip():
            nonlocal dem
            while not xong:
                dem += 1
                await asyncio.sleep(0.001)

        tac_vu = asyncio.create_task(nhip())
        kq = await mod.thuc_hien(ID_CONG_CU, "doi_chieu_hoa_don", {"xml": xml, "danh_muc": dm})
        xong = True
        await tac_vu
        return kq, dem

    kq, dem = asyncio.run(chinh())
    assert kq["ok"] is True
    assert dem >= 5, f"vòng lặp sự kiện bị chặn: nhịp nền chỉ chạy {dem} lần trong lúc đối chiếu"
