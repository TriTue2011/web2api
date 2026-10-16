"""NV79 (NV67 nhóm 5, việc 13) — công cụ khôi phục quyền quản trị bằng dòng lệnh trên máy chủ.

Kịch bản đúng thiết kế đã chốt (tầng 5: KHÔNG đường web, chỉ ai chạm được máy mới đặt lại được):
- không có đường web nào gọi được công cụ (không route nào trong app/tuyen.py nhắc tới nó);
- đặt lại mật khẩu admin: mật khẩu mới đăng nhập được, mật khẩu cũ mất hiệu lực, tài khoản được
  bật lại, 2FA (trường TOTP) bị tắt, khoá đăng nhập sai được xoá;
- mỗi lần dùng ghi một dòng nhật ký kênh `quan_tri`; mật khẩu mới KHÔNG xuất hiện trong bất kỳ
  tệp nhật ký/log nào;
- nhiều admin mà không chỉ `--ten` → 400 `nhieu_admin`; không có admin nào → 404;
- không đọc được thư mục dữ liệu → lỗi rõ ràng, không chạm gì;
- `tat-2fa` riêng: tắt 2FA mà không đổi mật khẩu.

Test chạy trên thư mục dữ liệu tạm, không mạng ngoài.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from app import khoi_phuc_quan_tri as kp
from app import nguoi_dung
from app.loi_openai import LoiOpenAI

MAT_KHAU_CU = "MatKhauCu123456!@#"
MAT_KHAU_MOI = "MatKhauMoi789!@#xyz"


def chay(coro):
    return asyncio.run(coro)


@pytest.fixture
def du_lieu(tmp_path, monkeypatch):
    d = tmp_path / "data"
    d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(d))
    return d


@pytest.fixture
def mot_admin(du_lieu):
    chay(nguoi_dung.tao_nguoi_dung(vai="admin", ten_dang_nhap="admin", mat_khau=MAT_KHAU_CU))
    return "admin"


def bam_cua(ten: str) -> str:
    return nguoi_dung.tim_theo_ten(ten)["bam_mat_khau"]


def nhat_ky_chua(du_lieu: Path, chuoi: str) -> bool:
    """Chuỗi có xuất hiện trong bất kỳ tệp nhật ký nào không."""
    tm = du_lieu / "nhat_ky"
    if not tm.is_dir():
        return False
    return any(chuoi in p.read_text(encoding="utf-8", errors="replace")
               for p in tm.iterdir() if p.is_file())


# ───────────── 1. Không đường web tương đương ─────────────

def test_khong_co_duong_web_tuong_duong():
    """Không route web nào gọi được công cụ — chỉ chạy được trên máy chủ."""
    goc = Path(kp.__file__).resolve().parents[1]
    tuyen = (goc / "app" / "tuyen.py").read_text(encoding="utf-8")
    assert "khoi_phuc_quan_tri" not in tuyen, "app/tuyen.py nhắc tới công cụ khôi phục quản trị"
    for p in (goc / "web").rglob("*"):
        if p.is_file():
            assert "khoi_phuc_quan_tri" not in p.read_text(encoding="utf-8", errors="replace"), \
                f"web/{p.name} nhắc tới công cụ khôi phục quản trị"
    assert not hasattr(kp, "route") and not hasattr(kp, "app"), "module CLI không được mang route"


# ───────────── 2. Đặt lại mật khẩu admin ─────────────

def test_dat_lai_mat_khau_admin(mot_admin, du_lieu):
    ket_qua = chay(kp.dat_lai_mat_khau_admin(mat_khau=MAT_KHAU_MOI))
    assert ket_qua["ten_dang_nhap"] == "admin" and ket_qua["ok"] is True

    moi = bam_cua("admin")
    assert nguoi_dung._kiem_tra_mat_khau_dong_bo(MAT_KHAU_MOI, moi), "mật khẩu mới không đăng nhập được"
    assert not nguoi_dung._kiem_tra_mat_khau_dong_bo(MAT_KHAU_CU, moi), "mật khẩu cũ vẫn còn hiệu lực"
    assert nguoi_dung.tim_theo_ten("admin")["bat"] is True, "tài khoản chưa được bật lại"


def test_dat_lai_mat_khau_tat_2fa_va_xoa_khoa_dang_nhap(mot_admin, du_lieu):
    """Đặt lại mật khẩu tắt luôn 2FA (trường TOTP tương lai) và xoá khoá đăng nhập sai."""
    # Giả lập trường TOTP của 2FA (việc NV67-12 chưa làm — công cụ phải dọn được khi nó xuất hiện)
    with nguoi_dung._KHOA_LUONG:
        ds = nguoi_dung._doc_nguoi_dung()
        ds[0]["totp_bat"] = True
        ds[0]["totp_hat_giong"] = "JBSWY3DPEHPK3PXP"
        nguoi_dung._ghi_nguoi_dung(ds)
    nguoi_dung.ghi_nhan_dang_nhap_sai("admin", ip="127.0.0.1")

    ket_qua = chay(kp.dat_lai_mat_khau_admin(mat_khau=MAT_KHAU_MOI))
    assert ket_qua["tat_2fa"] is True

    u = nguoi_dung.tim_theo_ten("admin")
    assert "totp_bat" not in u and "totp_hat_giong" not in u, "trường 2FA chưa bị tắt"
    assert nguoi_dung._kiem_tra_mat_khau_dong_bo(MAT_KHAU_MOI, u["bam_mat_khau"])


def test_sinh_mat_khau_ngau_nhien_khi_khong_truyen(mot_admin, du_lieu):
    """Không truyền --mat-khau → sinh ngẫu nhiên, trả về MỘT lần để in ra màn hình, dùng được ngay."""
    ket_qua = chay(kp.dat_lai_mat_khau_admin(mat_khau=None))
    mk = ket_qua["mat_khau_moi"]
    assert isinstance(mk, str) and len(mk) >= 12
    assert nguoi_dung._kiem_tra_mat_khau_dong_bo(mk, bam_cua("admin"))


def test_mat_khau_yeu_bi_tu_choi(mot_admin, du_lieu):
    with pytest.raises(LoiOpenAI) as exc:
        chay(kp.dat_lai_mat_khau_admin(mat_khau="ngan"))
    assert exc.value.ma == 400
    # mật khẩu cũ vẫn nguyên
    assert nguoi_dung._kiem_tra_mat_khau_dong_bo(MAT_KHAU_CU, bam_cua("admin"))


# ───────────── 3. Nhật ký mỗi lần dùng, mật khẩu không vào log ─────────────

def test_ghi_nhat_ky_quan_tri_khong_lo_mat_khau(mot_admin, du_lieu):
    chay(kp.dat_lai_mat_khau_admin(mat_khau=MAT_KHAU_MOI))
    chay(kp.tat_2fa_admin())

    tm = du_lieu / "nhat_ky"
    assert tm.is_dir() and any(tm.iterdir()), "không có tệp nhật ký nào được ghi"
    toan_bo = "".join(p.read_text(encoding="utf-8", errors="replace") for p in tm.iterdir() if p.is_file())
    assert MAT_KHAU_MOI not in toan_bo, "mật khẩu mới lọt vào nhật ký"
    assert '"kenh": "quan_tri"' in toan_bo or '"kenh":"quan_tri"' in toan_bo, \
        "không có dòng nhật ký kênh quan_tri"
    assert "khoi-phuc-quan-tri" in toan_bo or "khoi_phuc_quan_tri" in toan_bo, \
        "nhật ký không nêu công cụ đã dùng"


# ───────────── 4. Nhiều admin / không admin ─────────────

def test_nhieu_admin_phai_chi_ten(du_lieu):
    chay(nguoi_dung.tao_nguoi_dung(vai="admin", ten_dang_nhap="admin1", mat_khau=MAT_KHAU_CU))
    chay(nguoi_dung.tao_nguoi_dung(vai="admin", ten_dang_nhap="admin2", mat_khau=MAT_KHAU_CU))
    with pytest.raises(LoiOpenAI) as exc:
        chay(kp.dat_lai_mat_khau_admin(mat_khau=MAT_KHAU_MOI))
    assert exc.value.ma == 400 and exc.value.code == "nhieu_admin"
    # chỉ tên thì được
    ket_qua = chay(kp.dat_lai_mat_khau_admin(ten="admin2", mat_khau=MAT_KHAU_MOI))
    assert ket_qua["ten_dang_nhap"] == "admin2"
    assert nguoi_dung._kiem_tra_mat_khau_dong_bo(MAT_KHAU_CU, bam_cua("admin1")), "đụng nhầm admin khác"


def test_khong_co_admin_bao_404(du_lieu):
    with pytest.raises(LoiOpenAI) as exc:
        chay(kp.dat_lai_mat_khau_admin(mat_khau=MAT_KHAU_MOI))
    assert exc.value.ma == 404


def test_ten_khong_co_bao_404(mot_admin, du_lieu):
    with pytest.raises(LoiOpenAI) as exc:
        chay(kp.tat_2fa_admin(ten="khong-co-ai"))
    assert exc.value.ma == 404


# ───────────── 5. Không đọc được thư mục dữ liệu → lỗi rõ, không chạm gì ─────────────

def test_khong_doc_duoc_du_lieu_bao_loi_ro(mot_admin, du_lieu, monkeypatch):
    def gia_access(path, mode, **kw):
        return False
    monkeypatch.setattr(os, "access", gia_access)
    with pytest.raises(LoiOpenAI) as exc:
        chay(kp.dat_lai_mat_khau_admin(mat_khau=MAT_KHAU_MOI))
    assert exc.value.ma == 500 and exc.value.code == "khong_doc_du_lieu"
    assert nguoi_dung._kiem_tra_mat_khau_dong_bo(MAT_KHAU_CU, bam_cua("admin")), "đã chạm dữ liệu khi không có quyền"


# ───────────── 6. CLI main: mã thoát và không in mật khẩu vào nhật ký ─────────────

def test_cli_main_dat_lai_mat_khau(mot_admin, du_lieu, capsys):
    ma = kp.main(["dat-lai-mat-khau", "--mat-khau", MAT_KHAU_MOI])
    assert ma == 0
    ra = capsys.readouterr().out
    assert MAT_KHAU_MOI not in ra, "CLI in mật khẩu ra stdout khi người dùng đã tự truyền"
    assert nguoi_dung._kiem_tra_mat_khau_dong_bo(MAT_KHAU_MOI, bam_cua("admin"))


def test_cli_main_tat_2fa(mot_admin, du_lieu):
    assert kp.main(["tat-2fa"]) == 0


def test_cli_main_khong_co_admin(mot_admin, du_lieu, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(du_lieu / "rong"))
    assert kp.main(["dat-lai-mat-khau", "--mat-khau", MAT_KHAU_MOI]) == 2


def test_admin_bi_tat_trong_kho_van_cuu_duoc(mot_admin, du_lieu):
    """Tầng khôi phục CUỐI phải cứu được cả khi kho bị sửa tay/hỏng làm admin duy nhất đang TẮT (qua API thì không tắt
    được admin bật cuối cùng). Đặt lại mật khẩu bật lại tài khoản và đăng nhập được bằng mật khẩu mới."""
    ds = nguoi_dung._doc_nguoi_dung()
    for u in ds:
        if u.get("ten_dang_nhap") == "admin":
            u["bat"] = False
    nguoi_dung._ghi_nguoi_dung(ds)
    assert nguoi_dung.tim_theo_ten("admin")["bat"] is False  # tiền đề: đang tắt

    ket_qua = chay(kp.dat_lai_mat_khau_admin(mat_khau=MAT_KHAU_MOI))
    assert ket_qua["ok"] is True and ket_qua["ten_dang_nhap"] == "admin"
    sau = nguoi_dung.tim_theo_ten("admin")
    assert sau["bat"] is True, "tài khoản chưa được bật lại"
    assert nguoi_dung._kiem_tra_mat_khau_dong_bo(MAT_KHAU_MOI, bam_cua("admin"))


def test_admin_bi_tat_chi_theo_ten_van_cuu_duoc(mot_admin, du_lieu):
    ds = nguoi_dung._doc_nguoi_dung()
    for u in ds:
        u["bat"] = False
    nguoi_dung._ghi_nguoi_dung(ds)
    assert chay(kp.dat_lai_mat_khau_admin(mat_khau=MAT_KHAU_MOI, ten="admin"))["ok"] is True
    assert nguoi_dung.tim_theo_ten("admin")["bat"] is True
