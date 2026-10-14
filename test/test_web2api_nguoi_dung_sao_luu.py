"""Tích hợp giữa hai tính năng: khoá API CÓ CHỦ (nguoi_dung) và khôi phục sao lưu (sao_luu).

`khoa_khach.kiem_tep` chuẩn hoá bản ghi khoá khi khôi phục; nếu nó làm rơi `chu_id` thì khoá của người dùng trở thành
khoá «không chủ» sau khôi phục — hết bị ràng buộc theo trần HIỆN TẠI của chủ và không còn tắt theo chủ."""

import pytest

from app import khoa_khach


@pytest.fixture
def du_lieu(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    khoa_khach.lam_moi_cache()
    yield tmp_path
    khoa_khach.lam_moi_cache()


def _ban_ghi_luu():
    khoa_khach.dong_bo_dia()
    return [dict(k) for k in khoa_khach._doc_tu_dia_neu_can()]


def test_kiem_tep_giu_chu_id_khi_khoi_phuc(du_lieu):
    khoa_khach.tao_khoa(nhan="cua-bob", pham_vi=["chat"], chu_id="bob123")
    khoa_khach.tao_khoa(nhan="khong-chu", pham_vi=["chat"])
    ds = _ban_ghi_luu()
    ra = khoa_khach.kiem_tep({"khoa": ds})
    theo_nhan = {k["nhan"]: k for k in ra}
    assert theo_nhan["cua-bob"].get("chu_id") == "bob123", "khôi phục làm rơi chu_id của khoá người dùng"
    assert "chu_id" not in theo_nhan["khong-chu"], "khoá không chủ không được tự có chủ"


@pytest.mark.parametrize("xau", [123, ["x"], {"a": 1}, "co khoang trang", "a" * 65, "../x", "id\n"])
def test_kiem_tep_tu_choi_chu_id_la(du_lieu, xau):
    khoa_khach.tao_khoa(nhan="x", pham_vi=["chat"], chu_id="bob123")
    ds = _ban_ghi_luu()
    ds[0]["chu_id"] = xau
    with pytest.raises(ValueError):
        khoa_khach.kiem_tep({"khoa": ds})


def test_tim_theo_bam_cung_cong_voi_tim_theo_khoa_tho(du_lieu):
    """Đường tra thật của xác thực (`tim_theo_bam`) và lớp bọc băm-rồi-tra (`tim_theo_khoa_tho`) cho cùng kết quả,
    để kiểm chứng sau khôi phục (dùng `tim_theo_bam`) không lệch với đường xác thực."""
    import hashlib
    _, tho = khoa_khach.tao_khoa(nhan="a", pham_vi=["chat"])
    bam = hashlib.sha256(tho.encode()).hexdigest()
    assert khoa_khach.tim_theo_bam(bam) == khoa_khach.tim_theo_khoa_tho(tho)
    assert khoa_khach.tim_theo_bam("0" * 64) is None
    assert khoa_khach.tim_theo_bam(None) is None


# ───────────── Soát lần hai: khôi phục khoá CÓ CHỦ bằng đường ap_dung thật ─────────────

import asyncio  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402

from app import nguoi_dung, sao_luu  # noqa: E402
from app.loi_openai import LoiOpenAI  # noqa: E402

MK = "MatKhauKiemThu-12345"


def _nguoi(vai, ten, pv=None):
    nd, _, _ = asyncio.run(nguoi_dung.tao_nguoi_dung(vai=vai, ten_dang_nhap=ten, mat_khau=MK, pham_vi_toi_da=pv))
    return nd["id"]


def _kiem(ban):
    return asyncio.run(sao_luu.kiem_khoi_phuc(ban, None))


def _ap(ban):
    return asyncio.run(sao_luu.ap_dung_khoi_phuc(ban, None, sha256_da_kiem=hashlib.sha256(ban).hexdigest()))


def _ban_tu_tep(tep_khoa: dict) -> bytes:
    """Bản sao lưu dựng từ một nội dung khoa_khach.json tuỳ ý (qua chính đường tạo bản của ứng dụng)."""
    p = khoa_khach._p_khoa_khach()
    cu = p.read_bytes() if p.exists() else None
    p.write_text(json.dumps(tep_khoa))
    khoa_khach.lam_moi_cache()
    ban, _ = sao_luu.tao_ban_sao_luu(None, False)
    if cu is None:
        p.unlink()
    else:
        p.write_bytes(cu)
    khoa_khach.lam_moi_cache()
    return ban


def _muc_kb(kq, id_):
    return next(k for k in kq["khac_biet"]["khoa_khach"]["khoa"] if k["id"] == id_)


@pytest.fixture
def may_a(du_lieu):
    """Máy A: admin + bob (trần chat) có một khoá có chủ; trả (id bob, khoá thô, id khoá, bản sao lưu)."""
    _nguoi("admin", "admin-a")
    bob = _nguoi("user", "bobby", ["chat"])
    k, tho = khoa_khach.tao_khoa(nhan="cua-bob", pham_vi=["chat"], chu_id=bob)
    khoa_khach.dong_bo_dia()
    ban, _ = sao_luu.tao_ban_sao_luu(None, False)
    return bob, tho, k["id"], ban


def test_b1_khoi_phuc_cung_may_giu_khoa_co_chu(may_a):
    bob, tho, id_k, ban = may_a
    assert _kiem(ban)["co_the_ap_dung"] is True
    _ap(ban)
    r = khoa_khach.tim_theo_khoa_tho(tho)
    assert r["id"] == id_k and r["bat"] is True and r["pham_vi"] == ["chat"] and r["chu_id"] == bob


def test_b2_ha_tran_chu_roi_khoi_phuc_khong_500_va_tran_van_ap(may_a):
    """Trần của chủ đã hạ về []: khôi phục phải THÀNH CÔNG (kiểm chứng thứ đã GHI), và trần vẫn áp lúc chạy."""
    bob, tho, id_k, ban = may_a
    nguoi_dung.sua_nguoi_dung(bob, pham_vi_toi_da=[])
    assert _kiem(ban)["co_the_ap_dung"] is True
    _ap(ban)
    r = khoa_khach.tim_theo_khoa_tho(tho)
    assert r["id"] == id_k and r["pham_vi"] == [], "chính sách trần chủ bị nới qua khôi phục"
    tho_ghi = khoa_khach.ban_ghi_theo_bam(hashlib.sha256(tho.encode()).hexdigest())
    assert tho_ghi["pham_vi"] == ["chat"] and tho_ghi["chu_id"] == bob


def test_b3_chu_tat_roi_khoi_phuc_canh_bao_va_khoa_dong(may_a):
    bob, tho, id_k, ban = may_a
    nguoi_dung.sua_nguoi_dung(bob, bat=False)
    kq = _kiem(ban)
    assert kq["co_the_ap_dung"] is True
    assert "sẽ bị đóng sau khôi phục" in _muc_kb(kq, id_k)["canh_bao"]
    _ap(ban)
    assert khoa_khach.tim_theo_khoa_tho(tho)["bat"] is False


def test_b4_khoi_phuc_len_may_moi_chua_co_nguoi_dung(may_a, tmp_path, monkeypatch):
    """Máy mới chưa có nguoi_dung.json (tệp này KHÔNG vào bản sao lưu): khoá có chủ khôi phục được, bị đóng, có cảnh báo."""
    _, tho, id_k, ban = may_a
    moi = tmp_path / "may-moi"
    moi.mkdir()
    monkeypatch.setenv("WEB2API_DATA", str(moi))
    khoa_khach.lam_moi_cache()
    kq = _kiem(ban)
    assert "nguoi_dung.json" not in kq["se_ap_dung"]
    assert kq["co_the_ap_dung"] is True and "sẽ bị đóng sau khôi phục" in _muc_kb(kq, id_k)["canh_bao"]
    _ap(ban)
    assert (moi / "khoa_khach.json").is_file() and not (moi / "nguoi_dung.json").exists()
    assert khoa_khach.tim_theo_khoa_tho(tho)["bat"] is False


def test_bo_chu_id_khoi_khoa_dang_co_chu_bi_tu_choi(may_a):
    """Bản sao lưu bỏ `chu_id` của khoá đang có chủ: kiem phải báo «doi_chu» và không cho áp dụng (khoá thành không
    chủ = thoát trần chủ và sống khi chủ bị tắt)."""
    bob, tho, id_k, _ = may_a
    tep = json.loads(khoa_khach._p_khoa_khach().read_text())
    for k in tep["khoa"]:
        k.pop("chu_id", None)
        k["pham_vi"] = ["*"]
    ban = _ban_tu_tep(tep)
    kq = _kiem(ban)
    m = _muc_kb(kq, id_k)
    assert m["trang_thai"] == "doi_chu" and m["chu_id"] is None
    assert m["mo_rong_pham_vi"] is True
    assert kq["co_the_ap_dung"] is False
    assert any(t["tep"] == "khoa_khach.json" and t["id"] == id_k for t in kq["tu_choi"])
    with pytest.raises(LoiOpenAI) as e:
        _ap(ban)
    assert e.value.ma == 409
    r = khoa_khach.tim_theo_khoa_tho(tho)
    assert r["chu_id"] == bob and r["pham_vi"] == ["chat"]


def test_doi_chu_id_sang_nguoi_khac_bi_tu_choi(may_a):
    bob, _, id_k, _ = may_a
    adm = nguoi_dung.tim_theo_ten("admin-a")["id"]
    tep = json.loads(khoa_khach._p_khoa_khach().read_text())
    tep["khoa"][0]["chu_id"] = adm
    kq = _kiem(_ban_tu_tep(tep))
    assert _muc_kb(kq, id_k)["trang_thai"] == "doi_chu" and kq["co_the_ap_dung"] is False


def test_them_chu_cho_khoa_khong_chu_chi_danh_dau(du_lieu):
    """Khoá không chủ được GÁN chủ (thu hẹp theo trần chủ): đánh dấu doi_chu nhưng không chặn."""
    bob = _nguoi("user", "bobby", ["chat"])
    _nguoi("admin", "admin-a")
    k, _ = khoa_khach.tao_khoa(nhan="x", pham_vi=["chat"])
    khoa_khach.dong_bo_dia()
    tep = json.loads(khoa_khach._p_khoa_khach().read_text())
    tep["khoa"][0]["chu_id"] = bob
    kq = _kiem(_ban_tu_tep(tep))
    assert _muc_kb(kq, k["id"])["trang_thai"] == "doi_chu" and kq["co_the_ap_dung"] is True


def test_danh_dau_mo_rong_pham_vi(du_lieu):
    k1, _ = khoa_khach.tao_khoa(nhan="a", pham_vi=["chat"])
    k2, _ = khoa_khach.tao_khoa(nhan="b", pham_vi=["*"])
    khoa_khach.dong_bo_dia()
    tep = json.loads(khoa_khach._p_khoa_khach().read_text())
    tep["khoa"][0]["pham_vi"] = ["chat", "anh"]
    tep["khoa"][1]["pham_vi"] = ["chat"]
    tep["khoa"].append({**tep["khoa"][1], "id": "kk_moi", "bam": "a" * 64, "pham_vi": ["*"]})
    tep["khoa"].append({**tep["khoa"][1], "id": "kk_moi2", "bam": "b" * 64, "pham_vi": ["chat"]})
    kq = _kiem(_ban_tu_tep(tep))
    assert _muc_kb(kq, k1["id"])["mo_rong_pham_vi"] is True
    assert _muc_kb(kq, k2["id"])["mo_rong_pham_vi"] is False
    assert _muc_kb(kq, "kk_moi")["mo_rong_pham_vi"] is True
    assert _muc_kb(kq, "kk_moi2")["mo_rong_pham_vi"] is False


def test_khoi_phuc_khoa_pham_vi_co_hau_to(du_lieu):
    """Phạm vi «thiet_bi:ghi» hợp lệ ở đường tạo khoá thì sao lưu → khôi phục cũng phải được (một bộ kiểm duy nhất)."""
    _, tho = khoa_khach.tao_khoa(nhan="td", pham_vi=["thiet_bi:ghi", "chat"])
    khoa_khach.dong_bo_dia()
    ban, _ = sao_luu.tao_ban_sao_luu(None, False)
    assert _kiem(ban)["co_the_ap_dung"] is True
    _ap(ban)
    assert khoa_khach.tim_theo_khoa_tho(tho)["pham_vi"] == ["thiet_bi:ghi", "chat"]


@pytest.mark.parametrize("pv", [[], [f"m{i}" for i in range(65)], ["a:b:c"], ["Chat"], [""]])
def test_kiem_tep_cung_quy_tac_voi_duong_tao(du_lieu, pv):
    khoa_khach.tao_khoa(nhan="x", pham_vi=["chat"])
    ds = _ban_ghi_luu()
    ds[0]["pham_vi"] = pv
    with pytest.raises(ValueError):
        khoa_khach.kiem_tep({"khoa": ds})


def test_khoi_phuc_duoc_khi_tep_khoa_hien_tai_hong(du_lieu):
    """Tệp khoá đang hỏng: khôi phục là ĐƯỜNG CỨU — kiem nêu rõ kho hiện tại hỏng, ap_dung ghi được, khoá dùng lại được."""
    _, tho = khoa_khach.tao_khoa(nhan="a", pham_vi=["chat"])
    khoa_khach.dong_bo_dia()
    ban, _ = sao_luu.tao_ban_sao_luu(None, False)
    khoa_khach._p_khoa_khach().write_text("{hong")
    khoa_khach.lam_moi_cache()
    kq = _kiem(ban)
    assert kq["khac_biet"]["khoa_khach"]["kho_hien_tai_hong"] is True
    assert kq["co_the_ap_dung"] is True
    _ap(ban)
    assert khoa_khach.tim_theo_khoa_tho(tho)["pham_vi"] == ["chat"]
