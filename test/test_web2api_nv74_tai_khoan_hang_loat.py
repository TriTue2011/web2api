"""Kiểm thử app/tai_khoan_hang_loat.py (NV67 nhóm 3, việc 5–6).

Tách dòng hàng loạt + bộ chạy đăng nhập lần lượt. Không mạng thật: «module» là
hàm giả trong tệp này, triển khai đúng hợp đồng `KetQuaDangNhap` của lõi.
"""

import asyncio
import threading

import pytest

from app.tai_khoan_hang_loat import (
    BiMatTam,
    DaCoLuotChay,
    KetQuaDangNhap,
    LuotChay,
    gian_cach_giay,
    phan_loai_loi,
    tach_dong,
)


# ───────────────────────────── Module giả ───────────────────────────────────

class ModuleGia:
    """Module giả: kịch bản cho từng email, triển khai hợp đồng đăng nhập của lõi."""

    def __init__(self, kich_ban: dict):
        # kich_ban: email -> ("ok", cookie) | ("loi", ma_trang_thai, ma_loi) | ("nem",)
        self.kich_ban = kich_ban
        self.da_goi: list[str] = []
        self.moc_goi: list[float] = []

    async def dang_nhap(self, tai_khoan) -> KetQuaDangNhap:
        self.da_goi.append(tai_khoan.email)
        hanh_vi = self.kich_ban.get(tai_khoan.email, ("ok", "cookie-moi"))
        if hanh_vi[0] == "nem":
            raise RuntimeError("lỗi thô của module — lõi không được để lọt")
        if hanh_vi[0] == "loi":
            _, ma_trang_thai, ma_loi = hanh_vi
            return KetQuaDangNhap(False, ma_trang_thai=ma_trang_thai, ma_loi=ma_loi)
        _, cookie = hanh_vi
        return KetQuaDangNhap(True, cookie=cookie, ma_trang_thai=200)


class DongHoGia:
    def __init__(self):
        self.hien_tai = 1000.0

    def __call__(self):
        return self.hien_tai


# ───────────────────────────── Tách dòng ────────────────────────────────────

def test_hai_khuon_dong_dung():
    ds, loi, trung = tach_dong(
        "ban1@x.com|MatKhau123|JBSWY3DPEHPK3PXP\n"
        "ban2@x.com|cookie=SID=abc; HSID=xyz\n"
        "ban3@x.com|MatKhau456|\n"
    )
    assert not loi and not trung and len(ds) == 3
    assert ds[0].loai == "mat_khau" and ds[0].mat_khau.lay() == "MatKhau123"
    assert ds[0].totp.lay() == "JBSWY3DPEHPK3PXP"
    assert ds[1].loai == "cookie" and ds[1].cookie.lay() == "SID=abc; HSID=xyz"
    assert ds[1].mat_khau is None
    assert ds[2].loai == "mat_khau" and ds[2].totp is None
    assert [d.dong for d in ds] == [1, 2, 3]


def test_dong_chi_email_la_dang_nhap_lai():
    ds, loi, _ = tach_dong("  ban@x.com  \n")
    assert not loi and len(ds) == 1
    assert ds[0].loai == "dang_nhap_lai"
    assert ds[0].email == "ban@x.com"


def test_dong_sai_bao_dung_so_dong():
    ds, loi, _ = tach_dong(
        "# ghi chú\n"          # 1: bỏ qua
        "\n"                  # 2: bỏ qua
        "a@x.com|p1\n"         # 3: đúng
        "khong-phai-email|p\n"  # 4: sai
        "b@x.com|\n"           # 5: thiếu mật khẩu
        "c@x.com|cookie=\n"    # 6: cookie trống
        "d@x.com|p|t|thua\n"   # 7: thừa phần
        "e@x.com|cookie=z|t\n"  # 8: cookie đi với totp
        "|p9\n"                # 9: thiếu email
    )
    assert len(ds) == 1 and ds[0].email == "a@x.com"
    assert [l.dong for l in loi] == [4, 5, 6, 7, 8, 9]


def test_email_trung_giu_lan_dau_khong_phan_biet_hoa_thuong():
    ds, loi, trung = tach_dong("Ban@x.com|p1\nban@X.com|p2\nkhac@x.com|p3\n")
    assert not loi
    assert [d.email for d in ds] == ["Ban@x.com", "khac@x.com"]
    assert ds[0].mat_khau.lay() == "p1"  # giữ lần đầu
    assert [(t.dong, t.email) for t in trung] == [(2, "ban@X.com")]


def test_dong_ghi_chu_va_dong_trong_bo_qua():
    ds, loi, _ = tach_dong("   # ghi chú có khoảng trắng đầu\n\n\ta@x.com|p\n")
    assert not loi and len(ds) == 1 and ds[0].dong == 3


def test_email_doc_hai_bi_tu_choi():
    for xau in ("a<script>@x.com|p", "a@x.com|p", "a<b@x.com|p", 'a"x@x.com|p', "a b@x.com|p"):
        ds, loi, _ = tach_dong(xau + "\n")
        if "<" in xau or '"' in xau or " " in xau.split("@")[0]:
            assert loi, f"phải từ chối: {xau}"
        else:
            assert ds, f"phải nhận: {xau}"


def test_bi_mat_tam_xoa_va_repr_khong_lo():
    bm = BiMatTam("MatKhau123")
    assert bm.lay() == "MatKhau123"
    assert not bm.da_xoa()
    bm.xoa()
    assert bm.da_xoa()
    assert "MatKhau123" not in repr(bm) and "MatKhau123" not in str(bm)
    bm.xoa()  # gọi lại vẫn an toàn
    assert bm.da_xoa()


# ───────────────────────────── Bộ chạy ──────────────────────────────────────

def _chay(luot: LuotChay):
    return asyncio.run(luot.chay())


def test_gian_cach_30_giay_bang_dong_ho_gia():
    dong_ho = DongHoGia()
    ngu_calls: list[float] = []
    moc_goi: list[float] = []

    async def ngu_gia(giay: float):
        ngu_calls.append(giay)
        dong_ho.hien_tai += giay

    async def ham(tk):
        moc_goi.append(dong_ho())
        return KetQuaDangNhap(True, cookie="c")

    ds, _, _ = tach_dong("a@x.com|p\nb@x.com|p\nc@x.com|p\n")
    tom_tat = _chay(LuotChay(ds, ham, dong_ho=dong_ho, ngu=ngu_gia))
    assert tom_tat.xong == 3
    assert ngu_calls == [30.0, 30.0]  # lần đầu không chờ, các lần sau chờ đủ 30s
    khoang = [b - a for a, b in zip(moc_goi, moc_goi[1:])]
    assert all(k >= 30.0 for k in khoang), khoang


def test_gian_cach_doc_tu_bien_moi_truong(monkeypatch):
    monkeypatch.setenv("WEB2API_DANG_NHAP_GIAN_CACH_GIAY", "5")
    assert gian_cach_giay() == 5.0
    monkeypatch.setenv("WEB2API_DANG_NHAP_GIAN_CACH_GIAY", "khong-phai-so")
    assert gian_cach_giay() == 30.0
    monkeypatch.setenv("WEB2API_DANG_NHAP_GIAN_CACH_GIAY", "-10")
    assert gian_cach_giay() == 0.0
    monkeypatch.setenv("WEB2API_DANG_NHAP_GIAN_CACH_GIAY", "99999")
    assert gian_cach_giay() == 3600.0


def test_dung_sau_2_hong_lien_tiep():
    mod = ModuleGia({
        "a@x.com": ("loi", 401, ""),
        "b@x.com": ("loi", 403, ""),
        "c@x.com": ("ok", "cookie-c"),
    })
    ds, _, _ = tach_dong("a@x.com|p\nb@x.com|p\nc@x.com|p\n")
    tom_tat = _chay(LuotChay(ds, mod.dang_nhap, ngu=lambda g: asyncio.sleep(0)))
    assert [b.trang_thai for b in tom_tat.ket_qua] == ["loi", "loi", "bo_qua"]
    assert tom_tat.ket_qua[2].ma_loi == "hong_2_lien_tiep"
    assert mod.da_goi == ["a@x.com", "b@x.com"]  # c không được gọi
    assert tom_tat.ket_qua[0].ma_loi == "sai_chung_thuc"  # 401 → mã từ trạng thái
    assert tom_tat.ket_qua[1].ma_loi == "bi_cam"          # 403 → mã từ trạng thái


def test_hong_khong_lien_tiep_khong_dung():
    mod = ModuleGia({
        "a@x.com": ("loi", 500, ""),
        "b@x.com": ("ok", "cookie-b"),
        "c@x.com": ("loi", 429, ""),
        "d@x.com": ("ok", "cookie-d"),
    })
    ds, _, _ = tach_dong("a@x.com|p\nb@x.com|p\nc@x.com|p\nd@x.com|p\n")
    tom_tat = _chay(LuotChay(ds, mod.dang_nhap, ngu=lambda g: asyncio.sleep(0)))
    assert [b.trang_thai for b in tom_tat.ket_qua] == ["loi", "xong", "loi", "xong"]
    assert tom_tat.ket_qua[0].ma_loi == "loi_may_chu"
    assert tom_tat.ket_qua[2].ma_loi == "han_muc"
    assert tom_tat.loi == 2 and tom_tat.xong == 2 and tom_tat.bo_qua == 0


def test_dung_giua_chung_giu_ket_qua_da_xong():
    ds, _, _ = tach_dong("a@x.com|p\nb@x.com|p\nc@x.com|p\n")
    luot = LuotChay(ds, None, ngu=lambda g: asyncio.sleep(0))  # type: ignore[arg-type]
    goi = 0

    async def ham(tk):
        nonlocal goi
        goi += 1
        if goi == 1:
            luot.dung()  # dừng ngay sau tài khoản đầu
        return KetQuaDangNhap(True, cookie="c")

    luot._ham_dang_nhap = ham
    tom_tat = _chay(luot)
    assert [b.trang_thai for b in tom_tat.ket_qua] == ["xong", "bo_qua", "bo_qua"]
    assert all(b.ma_loi == "nguoi_dung_dung" for b in tom_tat.ket_qua[1:])
    assert tom_tat.xong == 1


def test_mat_khau_bi_xoa_ngay_sau_tung_tai_khoan():
    thu_tu_xoa: list[tuple[str, bool]] = []
    ds_ref: list = []

    async def ham_ghi(tk):
        for truoc in ds_ref:
            thu_tu_xoa.append((truoc.email, truoc.mat_khau.da_xoa()))
        return KetQuaDangNhap(True, cookie="c")

    ds, _, _ = tach_dong("a@x.com|matkhau-a\nb@x.com|matkhau-b\n")
    ds_ref.extend(ds)
    tom_tat = _chay(LuotChay(ds, ham_ghi, ngu=lambda g: asyncio.sleep(0)))
    assert tom_tat.xong == 2
    # lúc gọi tài khoản 2 (b@x.com), mật khẩu của a@x.com đã toàn 0
    assert ("a@x.com", True) in thu_tu_xoa
    # sau lượt chạy, mọi bí mật đều bị xoá
    for tk in ds:
        assert tk.mat_khau.da_xoa()
        assert all(b == 0 for b in tk.mat_khau._buf)


def test_mat_khau_bi_xoa_ca_khi_module_nem_loi():
    mod = ModuleGia({"a@x.com": ("nem",), "b@x.com": ("ok", "c")})
    ds, _, _ = tach_dong("a@x.com|p1\nb@x.com|p2\n")
    tom_tat = _chay(LuotChay(ds, mod.dang_nhap, ngu=lambda g: asyncio.sleep(0)))
    assert tom_tat.ket_qua[0].trang_thai == "loi"
    assert tom_tat.ket_qua[0].ma_loi == "loi_mang"  # module ném → coi như lỗi mạng
    assert "lỗi thô" not in str(tom_tat.ket_qua[0].ma_loi)
    assert all(tk.mat_khau.da_xoa() for tk in ds)


def test_ma_loi_la_cua_module_bi_thay_bang_ma_trang_thai():
    mod = ModuleGia({"a@x.com": ("loi", 401, "Sai mật khẩu rồi bạn ơi!")})
    ds, _, _ = tach_dong("a@x.com|p\n")
    tom_tat = _chay(LuotChay(ds, mod.dang_nhap, ngu=lambda g: asyncio.sleep(0)))
    assert tom_tat.ket_qua[0].ma_loi == "sai_chung_thuc"


def test_ket_qua_khong_chua_bi_mat():
    async def ham(tk):
        assert tk.cookie.lay() == "SID=bi-mat-that"
        return KetQuaDangNhap(True, cookie="cookie-moi-cua-module")

    ds, _, _ = tach_dong("a@x.com|cookie=SID=bi-mat-that\n")
    tom_tat = _chay(LuotChay(ds, ham, ngu=lambda g: asyncio.sleep(0)))
    ban_ghi = tom_tat.ket_qua[0]
    chuoi = repr(ban_ghi) + str(tom_tat.__dict__)
    assert "SID=bi-mat-that" not in chuoi
    assert "cookie-moi-cua-module" not in chuoi
    assert not any("cookie" in k for k in ban_ghi.__dict__)


def test_chi_mot_luot_chay_mot_luc():
    dang_chay = threading.Event()
    tha = threading.Event()

    async def ham_treo(tk):
        dang_chay.set()
        await asyncio.to_thread(tha.wait, 10)
        return KetQuaDangNhap(True, cookie="c")

    ds, _, _ = tach_dong("a@x.com|p\n")
    loi_thread: list = []

    def chay_a():
        try:
            asyncio.run(LuotChay(ds, ham_treo).chay())
        except Exception as e:  # noqa: BLE001
            loi_thread.append(e)

    t = threading.Thread(target=chay_a)
    t.start()
    assert dang_chay.wait(10)
    try:
        with pytest.raises(DaCoLuotChay):
            asyncio.run(LuotChay(ds, ham_treo, ngu=lambda g: asyncio.sleep(0)).chay())
    finally:
        tha.set()
        t.join(10)
    assert not loi_thread  # lượt A chạy xong bình thường, khoá đã nhả


def test_luot_chay_rong():
    async def ham(tk):  # pragma: no cover
        raise AssertionError("không được gọi")

    tom_tat = _chay(LuotChay([], ham))
    assert (tom_tat.tong, tom_tat.xong, tom_tat.loi, tom_tat.bo_qua) == (0, 0, 0, 0)


def test_phan_loai_loi_theo_ma_trang_thai():
    assert phan_loai_loi(None) == "loi_mang"
    assert phan_loai_loi(401) == "sai_chung_thuc"
    assert phan_loai_loi(403) == "bi_cam"
    assert phan_loai_loi(404) == "khong_tim_thay"
    assert phan_loai_loi(429) == "han_muc"
    assert phan_loai_loi(400) == "yeu_cau_sai"
    assert phan_loai_loi(502) == "loi_may_chu"
    assert phan_loai_loi(200) == "loi_khong_ro"
