"""NV131 — khớp thiết bị Home Assistant nhanh (không qua LLM) + cổng an toàn.

Test-first theo LUẬT CỨNG của kho:
- Chạy trước khi viết mã triển khai → PHẢI ĐỎ.
- ≥ 60 test cases kiểm chứng tất cả các góc cạnh:
  1. Chuẩn hoá NFKC, bỏ dấu, đ→d, bỏ dấu câu, thường hoá
  2. Tương đồng chuỗi LCS + token sort + overlap
  3. 3-gram Jaccard ký tự
  4. BM25 trên tập ứng viên (token hiếm nặng hơn token phổ biến)
  5. Tương hợp hành động & bật ≠ tắt, mở ≠ đóng
  6. Cổng căn từ: sai chính tả nhẹ nhận, khác thiết bị loại
  7. Nhập nhằng & biên mơ hồ (mo_ho: true)
  8. Hot words (từ khoá gọi): chuyen_llm=True, cắt từ khoá
  9. Kiểm khả thi trước khi chạy & phục hồi đúng 1 lần
  10. Nới biên khi khớp từ vựng chính xác vs không nới biên khi đối nghịch
  11. Chỉ mục ứng viên lưu bền theo hash (không lưu câu nói)
  12. Công cụ chẩn đoán giai_thich_khop
  13. Biên & ca đặc biệt: ứng viên rỗng, câu rỗng, ký tự lạ
  14. Hiệu năng: 1.000 ứng viên < 100 ms
  15. Tích hợp trong lớp Module của module home-assistant
"""
from __future__ import annotations

import asyncio
import functools
import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest

GOC = Path(__file__).resolve().parents[1]
DIR_HA = GOC / "modules" / "home-assistant"

# Nạp module.py trước
duong_mod = DIR_HA / "module.py"
if not duong_mod.is_file():
    raise ModuleNotFoundError(f"Chưa có {duong_mod}")

pkg = type(sys)("home_assistant_pkg")
pkg.__path__ = [str(DIR_HA)]
sys.modules["home_assistant_pkg"] = pkg
spec_mod = importlib.util.spec_from_file_location("home_assistant_pkg.module", duong_mod)
module_mod = importlib.util.module_from_spec(spec_mod)
module_mod.__package__ = "home_assistant_pkg"
sys.modules["home_assistant_pkg.module"] = module_mod
spec_mod.loader.exec_module(module_mod)
Module = module_mod.Module

# Nạp khop_nhanh.py
duong_kn = DIR_HA / "khop_nhanh.py"
if not duong_kn.is_file():
    raise ModuleNotFoundError(f"Chưa có {duong_kn}")

spec_kn = importlib.util.spec_from_file_location("home_assistant_pkg.khop_nhanh", duong_kn)
khop_nhanh_mod = importlib.util.module_from_spec(spec_kn)
khop_nhanh_mod.__package__ = "home_assistant_pkg"
sys.modules["home_assistant_pkg.khop_nhanh"] = khop_nhanh_mod
spec_kn.loader.exec_module(khop_nhanh_mod)

normalize = khop_nhanh_mod.normalize
_char_ngrams = khop_nhanh_mod._char_ngrams
_jaccard = khop_nhanh_mod._jaccard
_seq_ratio = khop_nhanh_mod._seq_ratio
_word_sim = khop_nhanh_mod._word_sim
_bm25_scores = khop_nhanh_mod._bm25_scores
_intent_affinity = khop_nhanh_mod._intent_affinity
_tokens_aligned = khop_nhanh_mod._tokens_aligned
ChiMucUngVien = khop_nhanh_mod.ChiMucUngVien
KetQuaKhop = khop_nhanh_mod.KetQuaKhop
khop_thiet_bi = functools.partial(
    khop_nhanh_mod.khop_thiet_bi,
    mien_cam=module_mod.MIEN_CAM_DIEU_KHIEN,
    kha_nang_mien=module_mod._KHA_NANG_MIEN,
    trang_thai_hong=module_mod.TRANG_THAI_HONG,
    tinh_rui_ro=module_mod._rui_ro_mien,
)
giai_thich_khop = functools.partial(
    khop_nhanh_mod.giai_thich_khop,
    mien_cam=module_mod.MIEN_CAM_DIEU_KHIEN,
    kha_nang_mien=module_mod._KHA_NANG_MIEN,
    trang_thai_hong=module_mod.TRANG_THAI_HONG,
    tinh_rui_ro=module_mod._rui_ro_mien,
)
W_WORD = khop_nhanh_mod.W_WORD
W_CHAR = khop_nhanh_mod.W_CHAR
W_BM25 = khop_nhanh_mod.W_BM25
W_INTENT = khop_nhanh_mod.W_INTENT


# Dữ liệu ứng viên giả lập để test
UNG_VIEN_MAU = [
    {
        "id": "home_assistant.light.den_tran_phong_khach",
        "ten": "Đèn trần Phòng khách",
        "loai": "light",
        "khu": "Phòng khách",
        "tinh_nang": [
            {"hanh_dong": "bat", "mo_ta": "Bật đèn"},
            {"hanh_dong": "tat", "mo_ta": "Tắt đèn"},
            {"hanh_dong": "chinh_sang", "mo_ta": "Chỉnh độ sáng"},
        ],
        "rui_ro_mac_dinh": "ghi",
    },
    {
        "id": "home_assistant.light.den_ngu_phong_ngu",
        "ten": "Đèn ngủ Phòng ngủ",
        "loai": "light",
        "khu": "Phòng ngủ",
        "tinh_nang": [
            {"hanh_dong": "bat", "mo_ta": "Bật đèn"},
            {"hanh_dong": "tat", "mo_ta": "Tắt đèn"},
        ],
        "rui_ro_mac_dinh": "ghi",
    },
    {
        "id": "home_assistant.switch.quat_tran_phong_khach",
        "ten": "Quạt trần Phòng khách",
        "loai": "switch",
        "khu": "Phòng khách",
        "tinh_nang": [
            {"hanh_dong": "bat", "mo_ta": "Bật quạt"},
            {"hanh_dong": "tat", "mo_ta": "Tắt quạt"},
        ],
        "rui_ro_mac_dinh": "ghi",
    },
    {
        "id": "home_assistant.cover.rem_cua_phong_khach",
        "ten": "Rèm cửa Phòng khách",
        "loai": "cover",
        "khu": "Phòng khách",
        "tinh_nang": [
            {"hanh_dong": "mo", "mo_ta": "Mở rèm"},
            {"hanh_dong": "dong", "mo_ta": "Đóng rèm"},
        ],
        "rui_ro_mac_dinh": "ghi",
    },
    {
        "id": "home_assistant.lock.khoa_cua_chinh",
        "ten": "Khoá cửa chính",
        "loai": "lock",
        "khu": "Cửa chính",
        "tinh_nang": [
            {"hanh_dong": "khoa", "mo_ta": "Khoá"},
            {"hanh_dong": "mo_khoa", "mo_ta": "Mở khoá"},
        ],
        "rui_ro_mac_dinh": "nguy_hiem",
    },
    {
        "id": "home_assistant.climate.dieu_hoa_phong_ngu",
        "ten": "Điều hoà Phòng ngủ",
        "loai": "climate",
        "khu": "Phòng ngủ",
        "tinh_nang": [
            {"hanh_dong": "dat_nhiet_do", "mo_ta": "Đặt nhiệt độ"},
        ],
        "rui_ro_mac_dinh": "ghi",
    },
]


# ==============================================================
# NHÓM 1: CHUẨN HOÁ VĂN BẢN (NFKC, BỎ DẤU, Đ→D, DẤU CÂU) - 8 ca
# ==============================================================

def test_01_chuan_hoa_bo_dau_tieng_viet():
    assert normalize("Đèn ngủ, Phòng ngủ!") == "den ngu phong ngu"


def test_02_chuan_hoa_chu_d_va_d_hoa():
    assert normalize("ĐIỀU HOÀ ĐỨNG") == "dieu hoa dung"


def test_03_chuan_hoa_khoang_trang_va_dau_cau():
    assert normalize("  Bật...   đèn ; trần ??  ") == "bat den tran"


def test_04_chuan_hoa_nfkc_ky_tu_to_hop():
    # Ký tự có dấu dạng decomposed NFD
    s_nfd = "Đe\u0300n"
    assert normalize(s_nfd) == "den"


def test_05_chuan_hoa_chuoi_rong_hoac_none():
    assert normalize("") == ""
    assert normalize(None) == ""


def test_06_chuan_hoa_giu_so_va_chu():
    assert normalize("Đèn số 1 và công tắc 2A") == "den so 1 va cong tac 2a"


def test_07_chuan_hoa_tron_co_dau_va_khong_dau_dong_nhat():
    assert normalize("Đèn trần") == normalize("den tran")


def test_08_chuan_hoa_ky_tu_dieu_khien_va_tab_xuong_dong():
    assert normalize("Đèn\tngủ\nphòng\rkhách") == "den ngu phong khach"


# ==============================================================
# NHÓM 2: TƯƠNG ĐỒNG CHUỖI LCS + TOKEN SORT + OVERLAP - 6 ca
# ==============================================================

def test_09_seq_ratio_trung_tuyet_doi():
    assert _seq_ratio("den tran", "den tran") == 1.0


def test_10_seq_ratio_rong():
    assert _seq_ratio("", "den") == 0.0
    assert _seq_ratio("den", "") == 0.0


def test_11_seq_ratio_sai_khac_nhe():
    # "khac" vs "khach"
    ratio = _seq_ratio("khac", "khach")
    assert 0.8 < ratio < 1.0


def test_12_word_sim_dao_thu_tu_tu():
    q = "phong khach den tran"
    c = "den tran phong khach"
    sim = _word_sim(q, c, q.split(), c.split())
    # Nhờ sort LCS và overlap, điểm phải rất cao (>= 0.85)
    assert sim >= 0.85


def test_13_word_sim_phat_chenh_lech_do_dai():
    q_ngan = "den"
    q_dai = "den tran phong khach ngu ngoai san vuon"
    sim_ngan = _word_sim("den", "den tran phong khach", ["den"], ["den", "tran", "phong", "khach"])
    sim_khop = _word_sim("den tran", "den tran", ["den", "tran"], ["den", "tran"])
    assert sim_ngan < sim_khop


def test_14_word_sim_chuoi_hoan_toan_khac():
    sim = _word_sim("quat", "den", ["quat"], ["den"])
    assert sim < 0.3


# ==============================================================
# NHÓM 3: 3-GRAM JACCARD KÝ TỰ - 4 ca
# ==============================================================

def test_15_char_ngrams_chuoi_ngan():
    assert _char_ngrams("ab") == {"ab"}
    assert _char_ngrams("") == set()


def test_16_char_ngrams_chuoi_binh_thuong():
    ngrams = _char_ngrams("den")
    assert ngrams == {"den"}
    ngrams4 = _char_ngrams("dent")
    assert ngrams4 == {"den", "ent"}


def test_17_jaccard_hai_tap_giong_nhau():
    a = {"den", "tra", "ran"}
    assert _jaccard(a, a) == 1.0


def test_18_jaccard_khong_giao_nhau():
    a = {"den", "tra"}
    b = {"qua", "uat"}
    assert _jaccard(a, b) == 0.0


# ==============================================================
# NHÓM 4: BM25 TRÊN CHÍNH TẬP ỨNG VIÊN - 5 ca
# ==============================================================

def test_19_bm25_token_hiem_co_diem_cao_hon_token_pho_bien():
    # "den" xuất hiện ở 3 docs, "chum" chỉ xuất hiện ở 1 doc
    docs = [
        ["den", "chum", "phong", "khach"],
        ["den", "ban", "phong", "khach"],
        ["den", "ngu", "phong", "ngu"],
    ]
    scores_hiem = _bm25_scores(["chum"], docs)
    scores_pho_bien = _bm25_scores(["den"], docs)
    # doc 0 có từ hiếm "chum"
    assert scores_hiem[0] > 0.0
    assert scores_hiem[1] == 0.0
    assert scores_hiem[2] == 0.0


def test_20_bm25_chuan_hoa_ve_khoang_0_den_1():
    docs = [["den", "tran"], ["quat", "tran"]]
    scores = _bm25_scores(["den"], docs)
    assert max(scores) == 1.0
    assert min(scores) >= 0.0


def test_21_bm25_query_khong_khop_doc_nao():
    docs = [["den", "tran"], ["quat", "tran"]]
    scores = _bm25_scores(["tivi"], docs)
    assert all(s == 0.0 for s in scores)


def test_22_bm25_tap_ung_vien_rong():
    scores = _bm25_scores(["den"], [])
    assert scores == []


def test_23_bm25_query_rong():
    docs = [["den", "tran"]]
    scores = _bm25_scores([], docs)
    assert scores == [0.0]


# ==============================================================
# NHÓM 5: TƯƠNG HỢP HÀNH ĐỘNG & BẬT ≠ TẮT - 8 ca
# ==============================================================

def test_24_intent_affinity_cau_bat_khop_hanh_dong_bat():
    score, opposing = _intent_affinity("bật đèn phòng khách", "bat")
    assert score >= 0.9
    assert not opposing


def test_25_intent_affinity_cau_tat_khop_hanh_dong_tat():
    score, opposing = _intent_affinity("tắt đèn phòng khách", "tat")
    assert score >= 0.9
    assert not opposing


def test_26_intent_affinity_doi_nghich_cau_tat_hanh_dong_bat():
    score, opposing = _intent_affinity("tắt đèn phòng khách", "bat")
    assert score == 0.0
    assert opposing is True


def test_27_intent_affinity_doi_nghich_cau_bat_hanh_dong_tat():
    score, opposing = _intent_affinity("bật đèn phòng ngủ", "tat")
    assert score == 0.0
    assert opposing is True


def test_28_intent_affinity_doi_nghich_mo_va_dong():
    score_mo_dong, opp1 = _intent_affinity("đóng rèm cửa", "mo")
    assert score_mo_dong == 0.0
    assert opp1 is True

    score_dong_mo, opp2 = _intent_affinity("mở rèm cửa", "dong")
    assert score_dong_mo == 0.0
    assert opp2 is True


def test_29_intent_affinity_trung_tinh_khong_chua_tu_hanh_dong():
    score, opposing = _intent_affinity("đèn phòng khách", "bat")
    assert 0.5 <= score <= 0.7
    assert not opposing


def test_30_intent_affinity_khoa_va_mo_khoa():
    score_khoa, opp1 = _intent_affinity("mở khoá cửa", "khoa")
    assert score_khoa == 0.0
    assert opp1 is True

    score_dung, opp2 = _intent_affinity("mở khoá cửa", "mo_khoa")
    assert score_dung >= 0.9
    assert not opp2


def test_31_intent_affinity_tieng_anh_turn_on_turn_off():
    s_on, opp_on = _intent_affinity("turn on living room light", "bat")
    assert s_on >= 0.9
    assert not opp_on

    s_off_bat, opp_off_bat = _intent_affinity("turn off living room light", "bat")
    assert s_off_bat == 0.0
    assert opp_off_bat is True


# ==============================================================
# NHÓM 6: CỔNG CĂN TỪ (_tokens_aligned) - 8 ca
# ==============================================================

def test_32_can_tu_chinh_ta_sai_nhe_nhan():
    # "khac" ~ "khach"
    q_tokens = ["den", "tran", "phong", "khac"]
    c_tokens = ["den", "tran", "phong", "khach"]
    assert _tokens_aligned(q_tokens, c_tokens) is True


def test_33_can_tu_khac_thiet_bi_loai_du_nhieu_tu_trung():
    # Câu nói "quạt trần phòng ngủ" so với "Đèn trần Phòng ngủ"
    # "quat" không có từ tương ứng ở ứng viên -> PHẢI LOẠI
    q_tokens = ["quat", "tran", "phong", "ngu"]
    c_tokens = ["den", "tran", "phong", "ngu"]
    assert _tokens_aligned(q_tokens, c_tokens) is False


def test_34_can_tu_ghep_mot_mot_khong_cho_dung_lap():
    # "may say nha tam" không được mượn 2 lần chữ "may" của "may suoi"
    q_tokens = ["may", "say"]
    c_tokens = ["may", "suoi"]
    assert _tokens_aligned(q_tokens, c_tokens) is False


def test_35_can_tu_ung_vien_rong():
    assert _tokens_aligned(["den"], []) is False


def test_36_can_tu_query_rong():
    assert _tokens_aligned([], ["den"]) is True


def test_37_can_tu_tu_ngan_yeu_cau_nguong_cao():
    # Từ ngắn 2 ký tự: "tv" vs "tb" sai 1 ký tự = sai hẳn
    assert _tokens_aligned(["tv"], ["tb"]) is False
    assert _tokens_aligned(["tv"], ["tv"]) is True


def test_38_can_tu_sai_chinh_ta_nhieu_hon_mot_tu_van_kiem_tra_tung_tu():
    # "den tran" vs "quat ban"
    assert _tokens_aligned(["den", "tran"], ["quat", "ban"]) is False


def test_39_can_tu_bo_qua_tu_dem():
    # "cho toi voi nhe" là từ đệm, đã lọc trước khi gọi căn từ
    q_tokens = ["den", "phong", "khach"]
    c_tokens = ["den", "phong", "khach"]
    assert _tokens_aligned(q_tokens, c_tokens) is True


# ==============================================================
# NHÓM 7: NHẬP NHẰNG & BIÊN MƠ HỒ (mo_ho: true) - 6 ca
# ==============================================================

def test_40_mo_ho_khi_hai_ung_vien_diem_sat_nhau():
    ung_vien = [
        {
            "id": "home_assistant.light.den_chum_1",
            "ten": "Đèn chùm một",
            "loai": "light",
            "tinh_nang": [{"hanh_dong": "bat"}],
        },
        {
            "id": "home_assistant.light.den_chum_2",
            "ten": "Đèn chùm hai",
            "loai": "light",
            "tinh_nang": [{"hanh_dong": "bat"}],
        },
    ]
    # Câu nói chung chung "bật đèn chùm"
    kq = khop_thiet_bi("bật đèn chùm", ung_vien)
    assert kq.mo_ho is True or any(item.get("mo_ho") for item in kq)


def test_41_khong_mo_ho_khi_mot_ung_vien_vuot_troi():
    kq = khop_thiet_bi("bật đèn trần phòng khách", UNG_VIEN_MAU)
    assert kq.mo_ho is False
    assert len(kq) > 0
    assert "den_tran_phong_khach" in kq[0]["entity_id"]


def test_42_mo_ho_co_truong_ly_do_ro_rang():
    ung_vien = [
        {"id": "home_assistant.light.den_a", "ten": "Đèn A", "loai": "light", "tinh_nang": [{"hanh_dong": "bat"}]},
        {"id": "home_assistant.light.den_b", "ten": "Đèn B", "loai": "light", "tinh_nang": [{"hanh_dong": "bat"}]},
    ]
    kq = khop_thiet_bi("bật đèn", ung_vien)
    if kq.mo_ho:
        assert "mơ hồ" in kq.ly_do.lower() or "sát nhau" in kq.ly_do.lower()


def test_43_mo_ho_khong_tu_y_chon_ho():
    # Khi mơ hồ, kết quả đánh dấu mo_ho để phía gọi biết và không gửi lệnh tự động
    ung_vien = [
        {"id": "home_assistant.switch.cong_tac_1", "ten": "Công tắc 1", "loai": "switch", "tinh_nang": [{"hanh_dong": "bat"}]},
        {"id": "home_assistant.switch.cong_tac_2", "ten": "Công tắc 2", "loai": "switch", "tinh_nang": [{"hanh_dong": "bat"}]},
    ]
    kq = khop_thiet_bi("bật công tắc", ung_vien)
    assert kq.mo_ho is True


def test_44_mo_ho_tra_ve_danh_sach_cac_ung_vien_nhap_nhang():
    ung_vien = [
        {"id": "home_assistant.light.den_1", "ten": "Đèn một", "loai": "light", "tinh_nang": [{"hanh_dong": "bat"}]},
        {"id": "home_assistant.light.den_2", "ten": "Đèn hai", "loai": "light", "tinh_nang": [{"hanh_dong": "bat"}]},
    ]
    kq = khop_thiet_bi("bật đèn", ung_vien)
    assert len(kq) >= 2


def test_45_chinh_nguong_bien_margin_tuy_bien():
    # Nếu đặt min_margin rất lớn (0.5), hầu hết ca gần nhau đều thành mơ hồ
    kq = khop_thiet_bi("bật đèn trần", UNG_VIEN_MAU, min_margin=0.5)
    # Có ít nhất 2 đèn trong danh sách
    if len(kq) >= 2 and abs(kq[0]["diem"] - kq[1]["diem"]) < 0.5:
        assert kq.mo_ho is True


# ==============================================================
# NHÓM 8: TỪ KHOÁ GỌI (HOT WORD) BỔ SUNG 18/10 (a) - 5 ca
# ==============================================================

def test_46_hot_word_chuyen_llm_khi_bat_dau_bang_tu_khoa():
    tu_khoa = ["trợ lý", "ơi bot", "hey assistant"]
    kq = khop_thiet_bi("trợ lý bật đèn phòng khách", UNG_VIEN_MAU, tu_khoa_goi=tu_khoa)
    assert kq.chuyen_llm is True
    assert kq.cau_cat == "bật đèn phòng khách"


def test_47_hot_word_khong_kich_hoat_khi_tu_khoa_o_giua_cau():
    tu_khoa = ["trợ lý"]
    # "gọi trợ lý bật đèn" -> không bắt đầu bằng "trợ lý"
    kq = khop_thiet_bi("gọi trợ lý bật đèn", UNG_VIEN_MAU, tu_khoa_goi=tu_khoa)
    assert kq.chuyen_llm is False


def test_48_hot_word_khong_phan_biet_hoa_thuong_va_dau_cau():
    tu_khoa = ["ơi bot"]
    kq = khop_thiet_bi("ƠI BOT, tắt quạt phòng khách", UNG_VIEN_MAU, tu_khoa_goi=tu_khoa)
    assert kq.chuyen_llm is True
    assert kq.cau_cat == "tắt quạt phòng khách"


def test_49_hot_word_rong_thi_chay_khop_nhanh_binh_thuong():
    kq = khop_thiet_bi("bật đèn trần phòng khách", UNG_VIEN_MAU, tu_khoa_goi=[])
    assert kq.chuyen_llm is False
    assert len(kq) > 0


def test_50_hot_word_cat_chinh_xac_phan_thua_khoang_trang():
    tu_khoa = ["web2api"]
    kq = khop_thiet_bi("web2api    mở rèm phòng khách", UNG_VIEN_MAU, tu_khoa_goi=tu_khoa)
    assert kq.chuyen_llm is True
    assert kq.cau_cat == "mở rèm phòng khách"


# ==============================================================
# NHÓM 9: KIỂM KHẢ THI TRƯỚC KHI CHẠY & PHỤC HỒI (b) - 6 ca
# ==============================================================

def test_51_kiem_kha_thi_loai_hanh_dong_khong_duoc_ho_tro():
    # Rèm cửa chỉ có "mo", "dong"; nếu nói "chỉnh độ sáng rèm" -> không khả thi
    ung_vien = [
        {
            "id": "home_assistant.cover.rem",
            "ten": "Rèm cửa",
            "loai": "cover",
            "tinh_nang": [{"hanh_dong": "mo"}, {"hanh_dong": "dong"}],
        }
    ]
    kq = khop_thiet_bi("chỉnh sáng rèm cửa", ung_vien)
    assert len(kq) == 0


def test_52_kiem_kha_thi_phuc_hoi_sang_ung_vien_ke_tiep():
    # Ứng viên 1 (top 1 theo tên) không hỗ trợ hành động, ứng viên 2 hỗ trợ
    ung_vien = [
        {
            "id": "home_assistant.switch.den_led_trang_tri",
            "ten": "Đèn led trang trí",
            "loai": "switch",
            "tinh_nang": [{"hanh_dong": "bat"}, {"hanh_dong": "tat"}],  # switch không có chinh_sang
        },
        {
            "id": "home_assistant.light.den_chum_chinh_sang",
            "ten": "Đèn chùm trang trí",
            "loai": "light",
            "tinh_nang": [{"hanh_dong": "bat"}, {"hanh_dong": "tat"}, {"hanh_dong": "chinh_sang"}],
        },
    ]
    # Lệnh "chỉnh sáng đèn trang trí"
    kq = khop_thiet_bi("chỉnh sáng đèn trang trí", ung_vien)
    assert len(kq) > 0
    # Phải chọn được ứng viên 2 có hỗ trợ chỉnh sáng (đã phục hồi từ ứng viên 1)
    assert kq[0]["entity_id"] == "home_assistant.light.den_chum_chinh_sang"


def test_53_kiem_kha_thi_chi_phuc_hoi_dung_mot_lan():
    # Cả 3 ứng viên đều không khả thi cho hành động yêu cầu
    ung_vien = [
        {"id": "ha.sw1", "ten": "Công tắc một", "loai": "switch", "tinh_nang": [{"hanh_dong": "bat"}]},
        {"id": "ha.sw2", "ten": "Công tắc hai", "loai": "switch", "tinh_nang": [{"hanh_dong": "bat"}]},
        {"id": "ha.sw3", "ten": "Công tắc ba", "loai": "switch", "tinh_nang": [{"hanh_dong": "bat"}]},
    ]
    kq = khop_thiet_bi("chỉnh độ sáng công tắc", ung_vien)
    assert len(kq) == 0


def test_54_kiem_kha_thi_thuc_the_loai_cam_dieu_khien():
    ung_vien = [
        {
            "id": "home_assistant.script.kich_ban_dem",
            "ten": "Kịch bản đêm",
            "loai": "script",  # miền cấm điều khiển
            "tinh_nang": [{"hanh_dong": "bat"}],
        }
    ]
    kq = khop_thiet_bi("bật kịch bản đêm", ung_vien)
    assert len(kq) == 0


def test_55_kiem_kha_thi_thoi_da_ba_ung_vien():
    # Hàm xếp hạng chỉ đánh giá tối đa 3 ứng viên phân biệt để kiểm tra khả thi
    ung_vien = [
        {"id": f"ha.light.den_{i}", "ten": f"Đèn chùm mẫu {i}", "loai": "light", "tinh_nang": [{"hanh_dong": "bat"}]}
        for i in range(10)
    ]
    kq = khop_thiet_bi("bật đèn chùm", ung_vien)
    # Danh sách trả về không được tràn ngập, chỉ lấy các ứng viên hàng đầu
    assert len(kq) <= 3


def test_56_kiem_kha_thi_ghi_ro_ly_do_khi_phuc_hoi():
    ung_vien = [
        {"id": "ha.script.chinh", "ten": "Quạt thông gió", "loai": "script", "tinh_nang": [{"hanh_dong": "tat"}]},
        {"id": "ha.fan.phu", "ten": "Quạt thông gió dự phòng", "loai": "fan", "tinh_nang": [{"hanh_dong": "tat"}]},
    ]
    kq = khop_thiet_bi("tắt quạt thông gió", ung_vien)
    assert len(kq) > 0
    assert kq[0]["entity_id"] == "ha.fan.phu"
    assert "phục hồi" in kq[0]["ly_do"].lower()


# ==============================================================
# NHÓM 10: NỚI BIÊN KHI KHỚP TỪ VỰNG VS KHÔNG NỚI KHI ĐỐI NGHỊCH (c) - 4 ca
# ==============================================================

def test_57_noi_bien_khi_khop_tu_vung_chinh_xac():
    # Hai ứng viên: 1 cái khớp từ vựng chính xác 100%, 1 cái chỉ khớp một phần
    ung_vien = [
        {"id": "ha.light.den_ngu", "ten": "Đèn ngủ", "loai": "light", "tinh_nang": [{"hanh_dong": "bat"}]},
        {"id": "ha.light.den_ngu_tre_em", "ten": "Đèn ngủ trẻ em", "loai": "light", "tinh_nang": [{"hanh_dong": "bat"}]},
    ]
    # "bật đèn ngủ" khớp chính xác nhãn "Đèn ngủ" -> biên được nới, không bị đánh mo_ho
    kq = khop_thiet_bi("bật đèn ngủ", ung_vien, min_margin=0.08)
    assert kq.mo_ho is False
    assert kq[0]["entity_id"] == "ha.light.den_ngu"


def test_58_khong_noi_bien_khi_co_hanh_dong_doi_nghich():
    # Câu nói "tắt" không bao giờ được chọn hành động "bật" kể cả khi điểm chuỗi rất cao
    ung_vien = [
        {"id": "ha.light.den_ngu_1", "ten": "Đèn ngủ", "loai": "light", "tinh_nang": [{"hanh_dong": "bat"}]},
    ]
    kq = khop_thiet_bi("tắt đèn ngủ", ung_vien)
    assert len(kq) == 0


def test_59_doi_nghich_luon_phai_vuot_bien_day_du():
    # So sánh giữa một thực thể có hành động tắt và một thực thể hành động bật
    ung_vien = [
        {"id": "ha.light.den_1", "ten": "Đèn ngủ", "loai": "light", "tinh_nang": [{"hanh_dong": "tat"}]},
        {"id": "ha.light.den_2", "ten": "Đèn ngủ", "loai": "light", "tinh_nang": [{"hanh_dong": "bat"}]},
    ]
    kq = khop_thiet_bi("tắt đèn ngủ", ung_vien)
    assert len(kq) > 0
    assert kq[0]["hanh_dong"] == "tat"


def test_60_khong_nham_lan_tu_phong_voi_tu_on():
    # Từ "phòng" chứa "on" trong không dấu "phong" nhưng không phải hành động bật
    score_phong, opp = _intent_affinity("tắt đèn phòng", "tat")
    assert score_phong >= 0.9
    assert not opp


# ==============================================================
# NHÓM 11: CHỈ MỤC ỨNG VIÊN THEO HASH BỀN VỮNG (d) - 4 ca
# ==============================================================

def test_61_chi_muc_hash_thay_doi_khi_danh_sach_thuc_the_doi():
    cm = ChiMucUngVien()
    h1 = cm.cap_nhat(UNG_VIEN_MAU[:3])
    h2 = cm.cap_nhat(UNG_VIEN_MAU)
    assert h1 != h2


def test_62_chi_muc_khong_dung_lai_khi_hash_trung():
    cm = ChiMucUngVien()
    h1 = cm.cap_nhat(UNG_VIEN_MAU)
    lan_dung_truoc = cm.so_lan_dung_lai
    h2 = cm.cap_nhat(UNG_VIEN_MAU)
    assert h1 == h2
    assert cm.so_lan_dung_lai == lan_dung_truoc + 1


def test_63_chi_muc_tuyet_doi_khong_luu_cau_nguoi_dung():
    cm = ChiMucUngVien()
    cm.cap_nhat(UNG_VIEN_MAU)
    khop_thiet_bi("bật đèn bí mật của gia đình", UNG_VIEN_MAU, chi_muc=cm)
    # Kiểm tra trong toàn bộ thuộc tính của chỉ mục không có câu người dùng
    str_dump = str(cm.__dict__)
    assert "bí mật của gia đình" not in str_dump
    assert "bi mat cua gia dinh" not in str_dump


def test_64_chi_muc_ho_tro_ngon_ngu_tieng_viet_va_tieng_anh():
    cm = ChiMucUngVien(ngon_ngu="vi")
    assert cm.ngon_ngu == "vi"
    cm_en = ChiMucUngVien(ngon_ngu="en")
    assert cm_en.ngon_ngu == "en"


# ==============================================================
# NHÓM 12: CÔNG CỤ CHẨN ĐOÁN giai_thich_khop (e) - 4 ca
# ==============================================================

def test_65_giai_thich_khop_tra_ve_du_cac_tin_hieu():
    gt = giai_thich_khop("bật đèn trần phòng khách", UNG_VIEN_MAU)
    assert "cau_goc" in gt
    assert "cau_chuan_hoa" in gt
    assert "ung_vien" in gt
    top = gt["ung_vien"][0]
    for key in ("entity_id", "ten", "hanh_dong", "diem_tong", "diem_chuoi", "diem_ngram", "diem_bm25", "diem_hanh_dong", "qua_cong_can_tu", "kha_thi", "ly_do"):
        assert key in top


def test_66_giai_thich_khop_tong_trong_so_bang_mot():
    assert round(W_WORD + W_CHAR + W_BM25 + W_INTENT, 4) == 1.0


def test_67_giai_thich_khop_khi_bi_loai_boi_cong_can_tu():
    gt = giai_thich_khop("quạt trần phòng khách", [UNG_VIEN_MAU[0]])  # So với đèn trần
    top = gt["ung_vien"][0]
    assert top["qua_cong_can_tu"] is False
    assert "căn từ" in top["ly_do"].lower()


def test_68_giai_thich_khop_cho_phep_xem_chi_tiet_ghep_tu():
    gt = giai_thich_khop("đèn trần phòng khác", [UNG_VIEN_MAU[0]])
    top = gt["ung_vien"][0]
    assert top["qua_cong_can_tu"] is True


# ==============================================================
# NHÓM 13: BIÊN & CA ĐẶC BIỆT (RỖNG, LẠ, KHÔNG CÓ HÀNH ĐỘNG) - 5 ca
# ==============================================================

def test_69_ung_vien_rong_tra_danh_sach_rong():
    kq = khop_thiet_bi("bật đèn", [])
    assert len(kq) == 0
    assert kq.mo_ho is False
    assert kq.chuyen_llm is False


def test_70_cau_noi_rong_tra_danh_sach_rong():
    kq = khop_thiet_bi("", UNG_VIEN_MAU)
    assert len(kq) == 0


def test_71_cau_chi_toan_dau_cau_va_khoang_trang():
    kq = khop_thiet_bi("... ???  ,,,", UNG_VIEN_MAU)
    assert len(kq) == 0


def test_72_cau_khong_khop_bat_ky_thiet_bi_nao():
    kq = khop_thiet_bi("cho tàu vũ trụ hạ cánh xuống sao hoả", UNG_VIEN_MAU)
    assert len(kq) == 0


def test_73_ket_qua_khop_ho_tro_ca_list_va_dict_access():
    kq = KetQuaKhop(
        [{"entity_id": "ha.light.1", "ten": "Đèn", "hanh_dong": "bat", "diem": 0.9, "ly_do": "ok"}],
        chuyen_llm=False,
        cau_cat="bật đèn",
        mo_ho=False,
        ly_do="khớp tốt",
    )
    # List interface
    assert len(kq) == 1
    assert kq[0]["entity_id"] == "ha.light.1"
    # Property & Dict interface
    assert kq.chuyen_llm is False
    assert kq["chuyen_llm"] is False
    assert kq.get("mo_ho") is False
    assert kq["cau_cat"] == "bật đèn"


# ==============================================================
# NHÓM 14: HIỆU NĂNG - 1.000 ỨNG VIÊN < 100 MS - 2 ca
# ==============================================================

def test_74_hieu_nang_1000_ung_vien_duoi_100ms():
    # Tạo 1.000 ứng viên giả lập với tên và khu vực đa dạng
    nhom_loai = ["light", "switch", "fan", "cover", "climate"]
    cac_phong = ["phòng khách", "phòng ngủ", "nhà bếp", "sân thượng", "ban công", "hành lang", "gara", "tầng hầm"]
    danh_sach_1000 = []
    for i in range(1000):
        loai = nhom_loai[i % len(nhom_loai)]
        phong = cac_phong[i % len(cac_phong)]
        danh_sach_1000.append({
            "id": f"home_assistant.{loai}.thiet_bi_so_{i}",
            "ten": f"Thiết bị số {i} tại {phong}",
            "loai": loai,
            "khu": phong,
            "tinh_nang": [{"hanh_dong": "bat"}, {"hanh_dong": "tat"}],
            "rui_ro_mac_dinh": "ghi",
        })

    # Chạy lần đầu để làm ấm (warm-up cache chỉ mục)
    khop_thiet_bi("bật thiết bị số 542 tại gara", danh_sach_1000)

    # Đo thời gian thực tế
    bat_dau = time.perf_counter()
    kq = khop_thiet_bi("bật thiết bị số 542 tại gara", danh_sach_1000)
    ket_thuc = time.perf_counter()
    thoi_gian_ms = (ket_thuc - bat_dau) * 1000

    assert len(kq) > 0
    assert "thiet_bi_so_542" in kq[0]["entity_id"]
    # Bắt buộc < 100 ms theo hợp đồng
    assert thoi_gian_ms < 100.0, f"Quá chậm: {thoi_gian_ms:.2f} ms >= 100 ms"


def test_75_hieu_nang_sang_loc_nhanh_n_gram_khong_nghen_lcs():
    # Kiểm tra n-gram prefiltering chạy dưới 15ms cho 1.000 ứng viên
    q_grams = _char_ngrams(normalize("bật đèn chùm phòng khách"))
    ung_vien_grams = [_char_ngrams(normalize(f"Thiết bị đèn phòng khách số {i}")) for i in range(1000)]

    t0 = time.perf_counter()
    scores = [_jaccard(q_grams, g) for g in ung_vien_grams]
    t1 = time.perf_counter()
    ms = (t1 - t0) * 1000
    assert len(scores) == 1000
    assert ms < 20.0, f"Jaccard prefilter quá chậm: {ms:.2f} ms"


# ==============================================================
# NHÓM 15: TÍCH HỢP TRONG LỚP Module - 4 ca
# ==============================================================

def test_76_module_khop_thiet_bi_phuong_thuc_async():
    async def _chay():
        m = Module({"tien_to": "home_assistant"})
        # Truyền ứng viên trực tiếp
        kq = await m.khop_thiet_bi("bật đèn trần phòng khách", ung_vien=UNG_VIEN_MAU)
        assert len(kq) > 0
        assert "den_tran_phong_khach" in kq[0]["entity_id"]
    asyncio.run(_chay())


def test_77_module_giai_thich_khop_phuong_thuc():
    async def _chay():
        m = Module({"tien_to": "home_assistant"})
        gt = await m.giai_thich_khop("tắt quạt phòng khách", ung_vien=UNG_VIEN_MAU)
        assert "ung_vien" in gt
        assert len(gt["ung_vien"]) > 0
    asyncio.run(_chay())


def test_78_module_kiem_kha_thi_chuc_nang():
    m = Module({"tien_to": "home_assistant"})
    # Đăng ký danh sách thực thể vào bản đồ nội bộ
    m._ban_do_thuc_the = {
        "light.den_tran": {"mien": "light", "tinh_nang": [{"hanh_dong": "bat"}, {"hanh_dong": "tat"}], "device_class": None},
        "script.chay": {"mien": "script", "tinh_nang": [{"hanh_dong": "bat"}], "device_class": None},
    }
    m._nhom = ["light", "switch"]
    
    ok, loi = m.kiem_kha_thi("home_assistant.light.den_tran", "bat")
    assert ok is True
    assert loi is None

    # Miền cấm
    ok_cam, loi_cam = m.kiem_kha_thi("home_assistant.script.chay", "bat")
    assert ok_cam is False
    assert "không bao giờ" in loi_cam

    # Hành động không hỗ trợ
    ok_hd, loi_hd = m.kiem_kha_thi("home_assistant.light.den_tran", "chinh_sang")
    assert ok_hd is False


def test_79_module_tu_khoa_goi_qua_cau_hinh():
    async def _chay():
        m = Module({"tien_to": "home_assistant"})
        m.nap({"dia_chi": "http://192.0.2.10:8123", "token": "token-test", "tu_khoa_goi": "trợ lý, ơi bot"})
        kq = await m.khop_thiet_bi("trợ lý tắt đèn trần phòng khách", ung_vien=UNG_VIEN_MAU)
        assert kq.chuyen_llm is True
        assert kq.cau_cat == "tắt đèn trần phòng khách"
    asyncio.run(_chay())


# ==============================================================
# NHÓM TEST TÁI HIỆN LỖI 1: PHỦ ĐỊNH & THIẾU ĐỘNG TỪ HÀNH ĐỘNG
# ==============================================================

def test_loi_1_tai_hien_dung_tat_den_phong_khach():
    # Câu «đừng tắt đèn phòng khách»: hiện tại bị nhận nhầm thành hành động "tat"
    kq = khop_thiet_bi("đừng tắt đèn phòng khách", UNG_VIEN_MAU)
    assert kq.mo_ho is True
    assert kq.chuyen_llm is True
    assert len(kq) == 0


def test_loi_1_tai_hien_khong_bat_den():
    # Câu «không bật đèn»: hiện tại bị nhận nhầm thành hành động "bat"
    kq = khop_thiet_bi("không bật đèn", UNG_VIEN_MAU)
    assert kq.mo_ho is True
    assert kq.chuyen_llm is True
    assert len(kq) == 0


def test_loi_1_tai_hien_khong_tat_den():
    kq = khop_thiet_bi("không tắt đèn", UNG_VIEN_MAU)
    assert kq.mo_ho is True
    assert kq.chuyen_llm is True
    assert len(kq) == 0


def test_loi_1_tai_hien_dung_bat_den():
    kq = khop_thiet_bi("đừng bật đèn", UNG_VIEN_MAU)
    assert kq.mo_ho is True
    assert kq.chuyen_llm is True
    assert len(kq) == 0


def test_loi_1_tai_hien_phu_dinh_kep():
    kq = khop_thiet_bi("không được không bật đèn", UNG_VIEN_MAU)
    assert kq.mo_ho is True
    assert kq.chuyen_llm is True
    assert len(kq) == 0


def test_loi_1_tai_hien_cau_chi_co_ten_thiet_bi():
    # Câu «đèn phòng khách» thiếu động từ hành động -> KHÔNG chọn hành động
    kq = khop_thiet_bi("đèn phòng khách", UNG_VIEN_MAU)
    assert len(kq) == 0
    assert kq.mo_ho is True or "hành động" in kq.ly_do.lower()


def test_loi_1_tai_hien_dong_tu_dung_van_nhan_dung():
    ung_vien = [
        {
            "id": "home_assistant.cover.rem_phong_khach",
            "ten": "Rèm phòng khách",
            "loai": "cover",
            "khu": "Phòng khách",
            "tinh_nang": [
                {"hanh_dong": "mo", "mo_ta": "Mở rèm"},
                {"hanh_dong": "dong", "mo_ta": "Đóng rèm"},
                {"hanh_dong": "dung", "mo_ta": "Dừng rèm"},
            ],
            "rui_ro_mac_dinh": "ghi",
        }
    ]
    kq = khop_thiet_bi("dừng rèm phòng khách", ung_vien)
    assert len(kq) == 1
    assert kq[0]["hanh_dong"] == "dung"
    assert kq[0]["entity_id"] == "home_assistant.cover.rem_phong_khach"


def test_loi_1_tai_hien_dot_bien_tat_phu_dinh():
    # Kiểm chứng đột biến: nếu TẮT xét phủ định, câu «đừng tắt...» sẽ bị lọt thành hành động tắt
    kq_tat_phu_dinh = khop_thiet_bi("đừng tắt đèn phòng khách", UNG_VIEN_MAU, bo_qua_phu_dinh=True)
    assert kq_tat_phu_dinh.chuyen_llm is False
    assert len(kq_tat_phu_dinh) > 0
    assert kq_tat_phu_dinh[0]["hanh_dong"] == "tat"

    # Khi BẬT xét phủ định bình thường, lập tức chuyển LLM an toàn
    kq_bat_phu_dinh = khop_thiet_bi("đừng tắt đèn phòng khách", UNG_VIEN_MAU, bo_qua_phu_dinh=False)
    assert kq_bat_phu_dinh.chuyen_llm is True
    assert kq_bat_phu_dinh.mo_ho is True
    assert len(kq_bat_phu_dinh) == 0


# ==============================================================
# NHÓM TEST TÁI HIỆN LỖI 2: TRUYỀN UNG_VIEN GHI ĐÈ _BAN_DO_THUC_THE
# ==============================================================

def test_loi_2_tai_hien_ung_vien_khong_ghi_de_ban_do_thuc_the():
    async def _chay():
        m = Module({"tien_to": "home_assistant"})
        m._ban_do_thuc_the = {
            "light.den_phong_khach": {
                "mien": "light",
                "tinh_nang": [{"hanh_dong": "bat"}, {"hanh_dong": "tat"}],
                "device_class": None,
                "trang_thai": "on",
            }
        }
        ban_do_goc = dict(m._ban_do_thuc_the)

        # Kẻ gọi truyền ung_vien giả chứa miền cấm hoặc thực thể lạ
        ung_vien_gia = [
            {
                "id": "home_assistant.script.kich_ban_nguy_hiem",
                "ten": "Kịch bản nguy hiểm",
                "loai": "light",  # giả mạo thành light để lách kiểm quyền
                "tinh_nang": [{"hanh_dong": "bat"}],
            },
            {
                "id": "home_assistant.light.den_phong_khach",
                "ten": "Đèn phòng khách",
                "loai": "script",  # giả mạo đổi miền của thực thể có sẵn
                "tinh_nang": [{"hanh_dong": "bat"}],
            }
        ]

        # Gọi khop_thiet_bi và giai_thich_khop với danh sách ứng viên giả
        await m.khop_thiet_bi("bật kịch bản nguy hiểm", ung_vien=ung_vien_gia)
        await m.giai_thich_khop("bật kịch bản nguy hiểm", ung_vien=ung_vien_gia)

        # Bảng nội bộ _ban_do_thuc_the KHÔNG ĐƯỢC bị ô nhiễm / ghi đè
        assert "script.kich_ban_nguy_hiem" not in m._ban_do_thuc_the
        assert m._ban_do_thuc_the["light.den_phong_khach"]["mien"] == "light"
        assert m._ban_do_thuc_the == ban_do_goc

        # thuc_hien với thực thể lạ vẫn phải bị từ chối
        kq_th = await m.thuc_hien("home_assistant.script.kich_ban_nguy_hiem", "bat", {})
        assert kq_th["ok"] is False
        assert "không thấy thực thể" in kq_th["loi"]

    asyncio.run(_chay())


# ==============================================================
# NHÓM TEST TÁI HIỆN LỖI 3: XOÁ KHỐI FALLBACK VÀ STUB MỞ CỬA
# ==============================================================

def test_loi_3_tai_hien_module_khong_chua_khoi_fallback_va_stub_mo_cua():
    ma_nguon = (DIR_HA / "module.py").read_text(encoding="utf-8")
    # Phải xoá sạch khối fallback sys.modules và stub mở cửa kiem_kha_thi_ung_vien -> (True, None)
    assert "home_assistant_khop_nhanh" not in ma_nguon
    assert "spec_from_file_location" not in ma_nguon
    assert "def kiem_kha_thi_ung_vien" not in ma_nguon
    # Phải có import tương đối sạch chuẩn production
    assert "from .khop_nhanh import" in ma_nguon


# ==============================================================
# NHÓM TEST TÁI HIỆN LỖI 4: HOT WORD KHỚP & CẮT ĐÚNG RANH GIỚI TỪ
# ==============================================================

def test_loi_4_tai_hien_hot_word_cat_sai_do_dai_khoang_cach():
    tu_khoa = ["trợ lý"]
    # "Trợ   lý" có nhiều khoảng trắng trên chuỗi gốc, len(kw) = 6 sẽ cắt trúng chữ "lý"
    kq = khop_thiet_bi("Trợ   lý, bật đèn phòng khách", UNG_VIEN_MAU, tu_khoa_goi=tu_khoa)
    assert kq.chuyen_llm is True
    assert kq.cau_cat == "bật đèn phòng khách"


def test_loi_4_tai_hien_hot_word_ranh_gioi_tu_khong_bat_nham():
    tu_khoa = ["bot"]
    # "bottle" chứa tiền tố "bot" nhưng không phải ranh giới từ -> không được kích hoạt hot word
    kq = khop_thiet_bi("bottle bật đèn phòng khách", UNG_VIEN_MAU, tu_khoa_goi=tu_khoa)
    assert kq.chuyen_llm is False


def test_loi_4_tai_hien_bep_khong_phai_hot_word_neu_khong_khai():
    # Khi người dùng không khai từ khoá gọi "bếp", câu "bếp nóng quá" không tự kích hoạt hot word
    kq = khop_thiet_bi("bếp nóng quá", UNG_VIEN_MAU, tu_khoa_goi=[])
    assert kq.chuyen_llm is False


# ==============================================================
# NHÓM TEST TÁI HIỆN LỖI 5: TRƯỜNG RỦI RO & BỎ QUÉT SYS.MODULES
# ==============================================================

def test_loi_5_tai_hien_ket_qua_khop_phai_co_truong_rui_ro():
    ung_vien = [
        {
            "id": "home_assistant.lock.khoa_cua_chinh",
            "ten": "Khoá cửa chính",
            "loai": "lock",
            "tinh_nang": [{"hanh_dong": "khoa"}, {"hanh_dong": "mo_khoa"}],
        },
        {
            "id": "home_assistant.water_heater.binh_tam",
            "ten": "Bình nóng lạnh",
            "loai": "water_heater",
            "tinh_nang": [{"hanh_dong": "bat"}, {"hanh_dong": "tat"}],
        },
        {
            "id": "home_assistant.light.den_phong_khach",
            "ten": "Đèn phòng khách",
            "loai": "light",
            "tinh_nang": [{"hanh_dong": "bat"}, {"hanh_dong": "tat"}],
        },
    ]

    # Khoá cửa -> rui_ro == 'nguy_hiem'
    kq_khoa = khop_thiet_bi("khoá cửa chính", ung_vien)
    assert len(kq_khoa) > 0
    assert "rui_ro" in kq_khoa[0]
    assert kq_khoa[0]["rui_ro"] == "nguy_hiem"

    # Bình nóng lạnh -> rui_ro == 'nguy_hiem'
    kq_binh = khop_thiet_bi("bật bình nóng lạnh", ung_vien)
    assert len(kq_binh) > 0
    assert "rui_ro" in kq_binh[0]
    assert kq_binh[0]["rui_ro"] == "nguy_hiem"

    # Đèn thường -> rui_ro == 'ghi'
    kq_den = khop_thiet_bi("bật đèn phòng khách", ung_vien)
    assert len(kq_den) > 0
    assert "rui_ro" in kq_den[0]
    assert kq_den[0]["rui_ro"] == "ghi"


def test_loi_5_tai_hien_khong_con_tim_phan_loai_tu_sys_modules():
    ma_kn = (DIR_HA / "khop_nhanh.py").read_text(encoding="utf-8")
    assert "_tim_phan_loai_tu_he_thong" not in ma_kn
    assert "dang_ky_phan_loai_module" not in ma_kn


def test_loi_5_tai_hien_kiem_kha_thi_ung_vien_khong_mac_dinh_mo():
    # kiem_kha_thi_ung_vien bắt buộc nhận mien_cam, kha_nang_mien, trang_thai_hong
    ent = {"id": "home_assistant.script.test", "loai": "script"}
    with pytest.raises(TypeError):
        # Thiếu các tham số phân loại bắt buộc -> TypeError
        khop_nhanh_mod.kiem_kha_thi_ung_vien(ent, "bat")  # type: ignore


def test_loi_6_tai_hien_khong_con_ban_sao_danh_sach_c1_trong_khop_nhanh():
    """Lỗi 'nhân đôi danh sách': khop_nhanh.py không được chứa bản sao danh sách phân loại."""
    ma_kn = (DIR_HA / "khop_nhanh.py").read_text(encoding="utf-8")
    assert "shell_command" not in ma_kn, "Còn chuỗi shell_command trong khop_nhanh.py"
    assert "alarm_control_panel" not in ma_kn, "Còn chuỗi alarm_control_panel trong khop_nhanh.py"
    assert "_CHUAN_C1" not in ma_kn, "Còn bản sao _CHUAN_C1_* trong khop_nhanh.py"
    assert "_tinh_rui_ro_c1" not in ma_kn, "Còn hàm _tinh_rui_ro_c1 trong khop_nhanh.py"


def test_loi_6_tai_hien_khop_thiet_bi_va_giai_thich_khop_thieu_tham_so_sinh_typeerror():
    """Các hàm trong khop_nhanh.py phải nhận tham số phân loại và hàm rủi ro bắt buộc (TypeError khi thiếu)."""
    with pytest.raises(TypeError):
        khop_nhanh_mod.khop_thiet_bi("bật đèn")  # type: ignore

    with pytest.raises(TypeError):
        khop_nhanh_mod.giai_thich_khop("bật đèn")  # type: ignore


def test_loi_6_tai_hien_rui_ro_lay_tu_nguon_duy_nhat_module_rui_ro_mien(monkeypatch):
    """Trường rui_ro lấy từ _rui_ro_mien do module.py truyền vào (nguồn duy nhất)."""
    async def _chay():
        ung_vien = [
            {
                "id": "home_assistant.cover.cong_gara",
                "ten": "Cổng gara",
                "loai": "cover",
                "device_class": "garage",
                "tinh_nang": [{"hanh_dong": "mo"}, {"hanh_dong": "dong"}],
            },
            {
                "id": "home_assistant.sensor.nhiet_do_phong",
                "ten": "Cảm biến nhiệt độ phòng",
                "loai": "sensor",
                "tinh_nang": [],
            },
        ]

        m = Module({"tien_to": "home_assistant", "ten": "Home Assistant"})
        m._nhom = ["cover", "light", "lock", "water_heater"]

        # 1. Cổng gara (cover + garage) -> nguy_hiem
        kq_gara = await m.khop_thiet_bi("mở cổng gara", ung_vien=ung_vien)
        assert len(kq_gara) > 0
        assert kq_gara[0]["rui_ro"] == "nguy_hiem"

        # 2. Cảm biến -> doc (thông qua giai_thich_khop)
        gt_cb = await m.giai_thich_khop("nhiệt độ phòng", ung_vien=[ung_vien[1]])
        uv_cb = [u for u in gt_cb.get("ung_vien", []) if "nhiet_do_phong" in u["entity_id"]]
        assert len(uv_cb) > 0
        assert uv_cb[0]["rui_ro"] == "doc"

        # 3. Sửa _rui_ro_mien trong module.py thì kết quả khớp đổi theo (chứng minh chỉ có MỘT nguồn)
        def _rui_ro_gia_lap(mien: str, device_class: str | None) -> str:
            return "nguon_duy_nhat_kiem_chung"

        monkeypatch.setattr(module_mod, "_rui_ro_mien", _rui_ro_gia_lap)

        kq_thu_nguon = await m.khop_thiet_bi("mở cổng gara", ung_vien=ung_vien)
        assert len(kq_thu_nguon) > 0
        assert kq_thu_nguon[0]["rui_ro"] == "nguon_duy_nhat_kiem_chung"

    asyncio.run(_chay())




