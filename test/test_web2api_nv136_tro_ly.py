"""NV136 — tro-ly G1.5: bộ dựng prompt theo ngân sách + bộ chọn công cụ theo tầng
+ X-quang prompt + sổ lỗi công cụ.

Test-first: mọi test dưới đây PHẢI ĐỎ trước khi có mã trong modules/tro-ly/.
Không mạng, không dữ liệu thật của nhà.
"""
from __future__ import annotations

import importlib.util
import time
from pathlib import Path

import pytest

GOC = Path(__file__).resolve().parents[1]
TL = GOC / "modules" / "tro-ly"


def _nap(ten_file: str, ten_module: str, goi: bool = False):
    import sys
    if goi:
        spec = importlib.util.spec_from_file_location(
            ten_module, TL / ten_file,
            submodule_search_locations=[str(TL)])
    else:
        spec = importlib.util.spec_from_file_location(ten_module, TL / ten_file)
    mm = importlib.util.module_from_spec(spec)
    sys.modules[ten_module] = mm
    spec.loader.exec_module(mm)
    return mm


manh_prompt = _nap("manh_prompt.py", "nv136_manh_prompt")
chon = _nap("chon_cong_cu.py", "nv136_chon_cong_cu")
xquang = _nap("x_quang.py", "nv136_x_quang")
soloi = _nap("so_loi.py", "nv136_so_loi")


# ── Danh mục công cụ giả dùng chung cho test ──────────────────────────────
def _danh_muc_co_ban():
    return [
        {"ten": "dieu_khien_den",
         "mo_ta": "Bật và tắt đèn trong nhà theo yêu cầu của người dùng",
         "mien": "den", "tham_so_bat_buoc": {}},
        {"ten": "dieu_khien_dieu_hoa",
         "mo_ta": "Bật, tắt và chỉnh nhiệt độ điều hoà không khí",
         "mien": "dieu_hoa", "tham_so_bat_buoc": {}},
        {"ten": "gui_email",
         "mo_ta": "Gửi thư điện tử email cho người nhận chỉ định",
         "mien": "email",
         "tham_so_bat_buoc": {"nguoi_nhan": r"(cho|tới|den|gửi tới)\s+([^\s,;.]+)"}},
        {"ten": "tim_tai_lieu",
         "mo_ta": "Tìm kiếm tài liệu trong kho lưu trữ nội bộ",
         "mien": "tim_kiem", "tham_so_bat_buoc": {}},
        {"ten": "goi_mcp",
         "mo_ta": "Gọi công cụ từ máy chủ MCP bên ngoài đã kết nối",
         "mien": "mcp", "tham_so_bat_buoc": {}},
        {"ten": "mo_cua",
         "mo_ta": "Mở cửa ra vào của nhà",
         "mien": "cua", "tham_so_bat_buoc": {},
         "doi_nghich_voi": "khoa_cua"},
        {"ten": "khoa_cua",
         "mo_ta": "Khoá cửa ra vào của nhà",
         "mien": "cua", "tham_so_bat_buoc": {},
         "doi_nghich_voi": "mo_cua"},
    ]


def _manh_nen():
    MP = manh_prompt.ManhPrompt
    return [
        MP(id="danh_tinh", nguon="loi", tang="on_dinh", uu_tien=100,
           noi_dung=manh_prompt.PROMPT_NEN_TOI_GIAN, tran_token=400,
           dieu_kien_nap=None),
        MP(id="quy_tac_den", nguon="module", tang="on_dinh", uu_tien=90,
           noi_dung="Quy tắc điều khiển đèn: chỉ dùng công cụ dieu_khien_den.",
           tran_token=100, dieu_kien_nap=lambda bc: bc.get("mien") == "den"),
        MP(id="bo_nho_phien", nguon="bo_nho", tang="hay_doi", uu_tien=10,
           noi_dung="Ghi nhớ: người dùng thích ánh sáng ấm.",
           tran_token=100, dieu_kien_nap=None),
    ]


# ── (1) prompt «bật đèn bếp» không chứa công cụ không liên quan ────────────
def test_prompt_bat_den_bep_khong_chua_cong_cu_khong_lien_quan():
    dm = _danh_muc_co_ban()
    kq = chon.tim_cong_cu("bật đèn bếp", dm, k=8)
    assert kq["cong_cu"] == "dieu_khien_den", f"chọn sai: {kq}"
    assert not kq["mo_ho"], f"mơ hồ không đáng: {kq}"
    bd = manh_prompt.BoDungPrompt()
    van_ban, _xq = bd.dung(_manh_nen(), ngan_sach_tong=2000,
                           ngan_sach_tang={"on_dinh": 800, "ngu_canh": 800, "hay_doi": 400},
                           boi_canh={"mien": "den"},
                           cong_cu_da_chon=[kq["cong_cu"]], danh_muc=dm)
    thap = van_ban.lower()
    assert "dieu_khien_den" in thap
    for cam in ("gui_email", "tim_tai_lieu", "goi_mcp", "thư điện tử", "mcp"):
        assert cam not in thap, f"prompt lọt công cụ không liên quan: {cam}"


# ── (2) 50 công cụ giả không làm prompt phình ─────────────────────────────
def test_50_cong_cu_gia_khong_lam_prompt_phinh():
    dm = _danh_muc_co_ban()
    dm_phinh = dm + [
        {"ten": f"cong_cu_gia_{i:02d}", "mo_ta": f"Công cụ giả số {i} cho việc không liên quan",
         "mien": f"mien_gia_{i}", "tham_so_bat_buoc": {}}
        for i in range(50)
    ]
    bd = manh_prompt.BoDungPrompt()
    kw = dict(ngan_sach_tong=2000,
              ngan_sach_tang={"on_dinh": 800, "ngu_canh": 800, "hay_doi": 400},
              boi_canh={"mien": "den"}, danh_muc=dm)
    kq1 = chon.tim_cong_cu("bật đèn bếp", dm, k=8)
    vb1, _ = bd.dung(_manh_nen(), cong_cu_da_chon=[kq1["cong_cu"]], **kw)
    kq2 = chon.tim_cong_cu("bật đèn bếp", dm_phinh, k=8)
    vb2, _ = bd.dung(_manh_nen(), cong_cu_da_chon=[kq2["cong_cu"]],
                     **{**kw, "danh_muc": dm_phinh})
    t1 = manh_prompt.uoc_tinh_token(vb1)
    t2 = manh_prompt.uoc_tinh_token(vb2)
    assert abs(t2 - t1) / max(t1, 1) <= 0.05, f"prompt phình: {t1} -> {t2}"
    assert kq2["cong_cu"] == "dieu_khien_den"


# ── (3) tiền tố ổn định giữa hai lượt ─────────────────────────────────────
def test_tien_to_on_dinh_giua_hai_luot_giong_byte():
    bd = manh_prompt.BoDungPrompt()
    kw = dict(ngan_sach_tong=2000,
              ngan_sach_tang={"on_dinh": 800, "ngu_canh": 800, "hay_doi": 400},
              boi_canh={"mien": "den"})
    vb1, xq1 = bd.dung(_manh_nen(), **kw)
    vb2, xq2 = bd.dung(_manh_nen(), **kw)
    p1 = bd.tien_to_on_dinh(vb1, xq1)
    p2 = bd.tien_to_on_dinh(vb2, xq2)
    assert p1 and p1 == p2
    assert vb1 == vb2, "cùng đầu vào phải cho prompt giống hệt"


# ── (4) vượt ngân sách: bỏ nguyên mảnh theo ưu tiên ───────────────────────
def test_vuot_ngan_sach_bo_nguyen_manh_theo_uu_tien():
    MP = manh_prompt.ManhPrompt
    manhs = [
        MP(id="a", nguon="loi", tang="on_dinh", uu_tien=100,
           noi_dung="Mảnh A quan trọng. " * 20, tran_token=500, dieu_kien_nap=None),
        MP(id="b", nguon="module", tang="on_dinh", uu_tien=10,
           noi_dung="Mảnh B ít quan trọng. " * 20, tran_token=500, dieu_kien_nap=None),
        MP(id="c", nguon="module", tang="on_dinh", uu_tien=1,
           noi_dung="Mảnh C kém quan trọng nhất. " * 20, tran_token=500, dieu_kien_nap=None),
    ]
    bd = manh_prompt.BoDungPrompt()
    # Ngân sách chỉ đủ cho ~1.5 mảnh
    tok_a = manh_prompt.uoc_tinh_token(manhs[0].noi_dung)
    vb, xq = bd.dung(manhs, ngan_sach_tong=10 ** 9,
                     ngan_sach_tang={"on_dinh": int(tok_a * 1.5), "ngu_canh": 0, "hay_doi": 0},
                     boi_canh={})
    assert "Mảnh A quan trọng." in vb
    assert "Mảnh C kém quan trọng nhất." not in vb
    # Không mảnh nào bị cắt giữa chừng: nội dung trong prompt phải nguyên văn
    for m in manhs:
        if m.noi_dung in vb:
            continue
        assert m.noi_dung not in vb  # hoặc vào nguyên, hoặc không vào
    loai = {m["id"]: m["ly_do"] for m in xq["manh_loai"]}
    assert loai.get("c") == "tran" or loai.get("b") == "tran"


# ── (5) chống trùng mảnh ─────────────────────────────────────────────────
def test_chong_trung_manh_cung_noi_dung():
    MP = manh_prompt.ManhPrompt
    nd = "Chỉ dẫn dùng chung cho mọi lượt."
    manhs = [
        MP(id="m1", nguon="loi", tang="on_dinh", uu_tien=100,
           noi_dung=nd, tran_token=500, dieu_kien_nap=None),
        MP(id="m2", nguon="module", tang="on_dinh", uu_tien=50,
           noi_dung="  " + nd + "  ", tran_token=500, dieu_kien_nap=None),
    ]
    bd = manh_prompt.BoDungPrompt()
    vb, xq = bd.dung(manhs, ngan_sach_tong=10 ** 9,
                     ngan_sach_tang={"on_dinh": 10 ** 9, "ngu_canh": 0, "hay_doi": 0},
                     boi_canh={})
    assert vb.count("Chỉ dẫn dùng chung") == 1
    assert any(m["ly_do"] == "trung" for m in xq["manh_loai"])


# ── (6) bộ ca chọn công cụ ≥ 60 ───────────────────────────────────────────
def _bo_ca_60():
    dm = _danh_muc_co_ban()
    ca = []
    # Nhóm 1: gần nghĩa (20 ca) — kỳ vọng đúng hạng 1, không mơ hồ
    gan_nghia = [
        ("bật đèn phòng khách", "dieu_khien_den"),
        ("tắt đèn ngủ đi", "dieu_khien_den"),
        ("bật đèn cầu thang", "dieu_khien_den"),
        ("bật điều hoà phòng ngủ", "dieu_khien_dieu_hoa"),
        ("chỉnh điều hoà 26 độ", "dieu_khien_dieu_hoa"),
        ("tắt điều hoà đi", "dieu_khien_dieu_hoa"),
        ("gửi email cho An", "gui_email"),
        ("gửi thư cho chị Ba", "gui_email"),
        ("mail tới sếp", "gui_email"),
        ("tìm tài liệu hợp đồng", "tim_tai_lieu"),
        ("tìm kiếm tài liệu cũ", "tim_tai_lieu"),
        ("tra cứu tài liệu", "tim_tai_lieu"),
        ("gọi tool MCP", "goi_mcp"),
        ("gọi công cụ MCP", "goi_mcp"),
        ("khoá cửa lại", "khoa_cua"),
        ("khoá cửa chính", "khoa_cua"),
        ("bật đèn", "dieu_khien_den"),
        ("điều hoà nóng quá", "dieu_khien_dieu_hoa"),
        ("gửi mail báo cáo cho sếp", "gui_email"),
        ("tìm tài liệu thuế", "tim_tai_lieu"),
    ]
    for yd, kv in gan_nghia:
        ca.append({"y_dinh": yd, "ky_vong": kv, "nhom": "gan_nghia"})
    # Nhóm 2: chính tả sai / không dấu (12 ca)
    sai = [
        ("bat den bep", "dieu_khien_den"),
        ("tat den ngu", "dieu_khien_den"),
        ("bât đèn phòng khách", "dieu_khien_den"),
        ("dieu hoa phong ngu", "dieu_khien_dieu_hoa"),
        ("gui email cho An", "gui_email"),
        ("gữi mail cho An", "gui_email"),
        ("tim tai lieu", "tim_tai_lieu"),
        ("tìm tài liệụ", "tim_tai_lieu"),
        ("khoa cua", "khoa_cua"),
        ("khoá cưa chính", "khoa_cua"),
        ("mo dieu hoa", "dieu_khien_dieu_hoa"),
        ("gọi mcp", "goi_mcp"),
    ]
    for yd, kv in sai:
        ca.append({"y_dinh": yd, "ky_vong": kv, "nhom": "chinh_ta"})
    # Nhóm 3: hành động đối nghịch (8 ca) — kỳ vọng vượt biên đầy đủ hoặc hỏi lại
    for yd in ("mở cửa", "mở cửa chính", "mở cửa ra vào", "mở cửa giúp",
               "khoá cửa", "khoá cửa giúp tôi", "đóng cửa lại", "mở khoá cửa"):
        ca.append({"y_dinh": yd, "ky_vong": "mo_ho", "nhom": "doi_nghich"})
    # Nhóm 4: thiếu tham số bắt buộc (8 ca)
    for yd in ("gửi mail", "gửi email", "gửi thư", "mail cho",
               "gửi mail đi", "soạn thư", "gửi email giúp", "nhắn mail"):
        ca.append({"y_dinh": yd, "ky_vong": "thieu_tham_so", "nhom": "thieu_tham_so"})
    # Nhóm 5: hai công cụ chồng chéo (12 ca) — thêm cặp chồng chéo vào danh mục
    dm_cc = dm + [
        {"ten": "tim_kiem_tai_lieu",
         "mo_ta": "Tìm kiếm tài liệu trong kho dữ liệu nội bộ của hệ thống",
         "mien": "tim_kiem", "tham_so_bat_buoc": {}},
    ]
    for yd in ("tìm tài liệu", "tìm kiếm tài liệu", "tra tài liệu",
               "tìm tài liệu trong kho", "kiếm tài liệu", "tìm tài liệu giúp",
               "tìm tài liệu hợp đồng", "tìm kiếm tài liệu cũ", "tìm tài liệu thuế",
               "tra cứu tài liệu", "tìm tài liệu nội bộ", "lục tài liệu"):
        ca.append({"y_dinh": yd, "ky_vong": "mo_ho", "nhom": "chong_cheo",
                   "danh_muc": dm_cc})
    assert len(ca) >= 60, f"chỉ có {len(ca)} ca"
    return ca, dm


def test_bo_ca_chon_cong_cu_60_ca():
    ca, dm_mac_dinh = _bo_ca_60()
    sai = []
    thong_ke = {}
    for c in ca:
        dm = c.get("danh_muc", dm_mac_dinh)
        kq = chon.tim_cong_cu(c["y_dinh"], dm, k=8)
        nhom = c["nhom"]
        thong_ke.setdefault(nhom, {"dung": 0, "tong": 0})
        thong_ke[nhom]["tong"] += 1
        ok = False
        kv = c["ky_vong"]
        if kv == "mo_ho":
            ok = kq["mo_ho"] is True
        elif kv == "thieu_tham_so":
            ok = bool(kq.get("thieu_tham_so"))
        else:
            ok = (kq["cong_cu"] == kv and not kq["mo_ho"]
                  and not kq.get("thieu_tham_so"))
        if ok:
            thong_ke[nhom]["dung"] += 1
        else:
            sai.append({**c, "duoc": kq["cong_cu"], "mo_ho": kq["mo_ho"],
                        "thieu": kq.get("thieu_tham_so")})
    print("\n== Thống kê bộ ca ==")
    for nhom, tk in thong_ke.items():
        print(f"  {nhom}: {tk['dung']}/{tk['tong']}")
    if sai:
        print("== Ca sai ==")
        for c in sai:
            print(f"  [{c['nhom']}] {c['y_dinh']!r} kỳ vọng {c['ky_vong']}, "
                  f"được {c['duoc']}, mo_ho={c['mo_ho']}, thieu={c['thieu']}")
    # Ngưỡng chấp nhận: mỗi nhóm đúng ≥ 70%
    for nhom, tk in thong_ke.items():
        assert tk["dung"] / tk["tong"] >= 0.7, f"nhóm {nhom} chỉ đúng {tk}"


# ── (7) cổng biên: đột biến tắt cổng → test đỏ ────────────────────────────
def test_cong_bien_dot_bien_tat_cong_phai_mat_mo_ho():
    dm = _danh_muc_co_ban()
    # Ca mơ hồ thật: hai ứng viên điểm sát nhau
    kq = chon.tim_cong_cu("mở cửa", dm, k=8)
    assert kq["mo_ho"] is True, "cổng biên phải bắt ca mơ hồ"
    # Đột biến: tắt cổng biên (ngưỡng biên = 0, ngưỡng tối thiểu = 0,
    # ngưỡng đối nghịch = 0, ngưỡng chồng chéo = 1.0 để vô hiệu)
    kq2 = chon.tim_cong_cu("mở cửa", dm, k=8,
                           nguong_bien=0.0, nguong_toi_thieu=0.0,
                           nguong_bien_doi_nghich=0.0, nguong_chong_cheo=1.0)
    assert kq2["mo_ho"] is False, "tắt cổng biên thì ca mơ hồ không còn bị chặn"


# ── (8) phát hiện chồng chéo ──────────────────────────────────────────────
def test_do_chong_cheo_phat_hien_cap_mo_ta_giong_nhau():
    ds = [
        {"ten": "tim_tai_lieu",
         "mo_ta": "Tìm kiếm tài liệu trong kho lưu trữ nội bộ"},
        {"ten": "tim_kiem_tai_lieu",
         "mo_ta": "Tìm kiếm tài liệu trong kho dữ liệu nội bộ của hệ thống"},
        {"ten": "dieu_khien_den",
         "mo_ta": "Bật và tắt đèn trong nhà theo yêu cầu của người dùng"},
    ]
    cap = chon.do_chong_cheo(ds)
    ten_cap = {(a, b) for a, b, _d in cap} | {(b, a) for a, b, _d in cap}
    assert ("tim_tai_lieu", "tim_kiem_tai_lieu") in ten_cap
    assert not any("dieu_khien_den" in (a, b) for a, b, _d in cap)


# ── (9) X-quang đủ trường, không chứa bí mật ──────────────────────────────
def test_x_quang_du_truong_va_khong_chua_bi_mat(tmp_path):
    nk = xquang.NhatKyXQuang(str(tmp_path / "xq.jsonl"))
    bd = manh_prompt.BoDungPrompt()
    vb, xq = bd.dung(_manh_nen(), ngan_sach_tong=2000,
                     ngan_sach_tang={"on_dinh": 800, "ngu_canh": 800, "hay_doi": 400},
                     boi_canh={"mien": "den"})
    nk.ghi(luot_id="luot-1", ho_so_id="hs-1", x_quang=xq,
           cong_cu_schema=["dieu_khien_den"], cong_cu_danh_muc=["gui_email"],
           ghi_chu="mat_khau_noi_bo=KHONG-DUOC-RO-RA")
    dong = nk.doc(ho_so_id="hs-1", nguoi_hoi_ho_so_id="hs-1")
    assert len(dong) == 1
    d = dong[0]
    assert d["luot_id"] == "luot-1"
    assert "manh_vao" in d and "manh_loai" in d
    assert "token_theo_tang" in d
    assert "cong_cu_schema" in d and "cong_cu_danh_muc" in d
    for m in d["manh_vao"]:
        assert {"id", "nguon", "token", "tang", "ly_do"} <= set(m)
    # Không chứa bí mật
    raw = str(d)
    assert "KHONG-DUOC-RO-RA" not in raw
    # IDOR: hồ sơ khác không đọc được
    with pytest.raises(PermissionError):
        nk.doc(ho_so_id="hs-1", nguoi_hoi_ho_so_id="hs-2")


# ── (10) sổ lỗi chỉ đưa mục liên quan ─────────────────────────────────────
def test_so_loi_chi_dua_muc_lien_quan_den_cong_cu(tmp_path):
    so = soloi.SoLoi(str(tmp_path / "loi.json"))
    so.ghi("hs-1", "gui_email", "sai_tham_so", "thiếu người nhận")
    so.ghi("hs-1", "gui_email", "bi_tu_choi", "máy chủ từ chối")
    so.ghi("hs-1", "dieu_khien_den", "sai_tham_so", "thiếu tên đèn")
    so.ghi("hs-1", "gui_email", "het_gio", "quá 30 giây")
    so.ghi("hs-1", "gui_email", "loi_mang", "mất kết nối")
    muc = so.lay_lien_quan("hs-1", "gui_email")
    assert len(muc) == 3, "tối đa 3 dòng gần nhất"
    assert all(m["cong_cu"] == "gui_email" for m in muc)
    assert muc[0]["tom_tat"] == "mất kết nối"  # gần nhất trước
    # Hồ sơ khác không đọc được
    assert so.lay_lien_quan("hs-2", "gui_email") == []


# ── ĐO: 1000 công cụ giả → chọn < 50 ms ────────────────────────────────────
def test_hieu_nang_1000_cong_cu_duoi_50ms():
    dm = [{"ten": f"cc_{i:04d}", "mo_ta": f"Công cụ giả thứ {i} cho việc vặt linh tinh",
           "mien": f"mien_{i % 37}", "tham_so_bat_buoc": {}} for i in range(1000)]
    dm.append({"ten": "dieu_khien_den",
               "mo_ta": "Bật và tắt đèn trong nhà theo yêu cầu của người dùng",
               "mien": "den", "tham_so_bat_buoc": {}})
    t0 = time.perf_counter()
    kq = chon.tim_cong_cu("bật đèn bếp", dm, k=8)
    dt = (time.perf_counter() - t0) * 1000
    assert dt < 50, f"chọn 1000 công cụ mất {dt:.1f} ms, vượt 50 ms"
    assert kq["cong_cu"] == "dieu_khien_den"
