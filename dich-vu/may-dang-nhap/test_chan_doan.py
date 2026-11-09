# test_chan_doan.py — Q-D (đề nghị xin quyền 04/11): nhật ký chẩn đoán đăng nhập phải KHÔNG chứa bí mật.
# Chỉ có tên miền + đường dẫn (không query/fragment: OAuth mang login_hint/state/code), selector đã che email, vòng đệm có trần.
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _gia_goi  # noqa: F401

from src import chan_doan


def setup_function(_):
    chan_doan.xoa()


def test_url_bo_query_fragment_va_ma_dai():
    u = "https://accounts.google.com/o/oauth2/auth?login_hint=chu.nha%40gmail.com&state=abc&code=4/0AeanS0Zxxxxxxxxxxxxxxxxx#frag"
    r = chan_doan.lam_sach_url(u)
    assert r == "accounts.google.com/o/oauth2/auth"
    r2 = chan_doan.lam_sach_url("https://chatgpt.com/c/68f3a1b2-1234-5678-9abc-def012345678")
    assert r2 == "chatgpt.com/c/«dai»"
    assert chan_doan.lam_sach_url("chu.nha@gmail.com/signin") == "" or "@" not in chan_doan.lam_sach_url("chu.nha@gmail.com/signin")


def test_selector_che_email_va_cat_do_dai():
    s = chan_doan.che_selector('div[data-identifier="chu.nha@example.com"]')
    assert "chu.nha@example.com" not in s and "«email»" in s
    assert len(chan_doan.che_selector("a" * 1000)) <= chan_doan.TRAN_SELECTOR


def test_ghi_va_doc_theo_thu_tu_moi_nhat_cuoi_va_co_tran():
    for i in range(chan_doan.TRAN_VONG + 20):
        chan_doan.ghi("bam", selector=f"#n{i}", ket_qua="khong_thay")
    r = chan_doan.doc(50)
    assert len(r) == 50 and r[-1]["selector"] == f"#n{chan_doan.TRAN_VONG + 19}"
    assert len(chan_doan.doc(10_000)) == chan_doan.TRAN_VONG


def test_ghi_chi_nhan_truong_cho_phep_va_loai_hop_le():
    chan_doan.ghi("bam", selector="#a", ket_qua="bam_duoc", mat_khau="xyz", cookie="sid=1", email="a@b.co")
    chan_doan.ghi("khong_hop_le", selector="#b")
    r = chan_doan.doc(10)
    assert len(r) == 1 and set(r[0]) <= {"luc", "loai", "selector", "url", "ket_qua", "ma_loi"}
    assert "xyz" not in str(r) and "a@b.co" not in str(r)


def test_gan_trang_ghi_dieu_huong_chi_khung_chinh():
    class Khung:
        def __init__(self, url, cha=None):
            self.url, self.parent_frame = url, cha

    class Trang:
        def __init__(self):
            self.nghe, self.main_frame = {}, Khung("https://x.com/")

        def on(self, ten, fn):
            self.nghe[ten] = fn

    t = Trang()
    chan_doan.gan_trang(t)
    t.nghe["framenavigated"](Khung("https://accounts.google.com/signin?hint=chu.nha@example.com#x", cha=None))
    t.nghe["framenavigated"](Khung("https://iframe.example/ads", cha=object()))  # khung con: bỏ
    r = chan_doan.doc(10)
    assert [b["url"] for b in r] == ["accounts.google.com/signin"] and r[0]["loai"] == "dieu_huong"


def test_url_che_jwt_dau_cham_dau_bang_va_noi_dung_data():
    assert chan_doan.lam_sach_url("https://x.example/cb/eyJhbGciOi.eyJzdWIiOiIx.c2ln") == "x.example/cb/«dai»"
    assert chan_doan.lam_sach_url("https://x.example/a%3Db=c;d/ok") == "x.example/«dai»/ok"
    assert chan_doan.lam_sach_url("data:text/html;base64,PGh0bWw+bWF0LWtoYXU=") == "data:«noi-dung»"
    assert chan_doan.lam_sach_url("blob:https://x.example/uuid") == "blob:«noi-dung»"
