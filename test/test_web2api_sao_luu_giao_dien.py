"""Test giao diện Chrome cho tính năng Sao lưu & Khôi phục cấu hình.

Chạy bằng Chrome THẬT trên LÕI THẬT theo khuôn mẫu test/test_web2api_giao_dien.py và test/ui_chung.py:
- Mở trang He-thong-Bao-mat.dc.html ở 1280×800 và 390×844.
- Nhập khoá quản trị để mở các khối quản trị.
- Thẻ «Sao lưu & Khôi phục cấu hình» (#sao-luu-khoi-phuc) hiện diện đầy đủ.
- Ô nhập mật khẩu sao lưu, checkbox kèm phiên, nút «Xuất sao lưu».
- Ô chọn tệp khôi phục, ô mật khẩu giải mã, nút «Kiểm tra» và «Áp dụng».
- Không tràn ngang ở 390px trên điện thoại.
- «Áp dụng» tắt cho tới khi «Kiểm tra» đạt với CHÍNH tệp đó; kiểm xong hiện khác biệt nhạy cảm; Áp dụng mở hộp
  xác nhận và CHƯA ghi gì; đổi tệp thì phải kiểm lại; bản có mục bị từ chối thì Áp dụng vẫn tắt.
- Dữ liệu từ tệp tải lên (nhãn khoá khách có thẻ HTML) hiện thành chữ, không chạy (không innerHTML).
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import tarfile
from contextlib import contextmanager

import pytest

from app import sao_luu, so_module
from app.main import app
from gia import MayChuThat
from ui_chung import CO_CHROME, dang_nhap_quan_tri, trang, trinh_duyet  # noqa: F401 — fixture

pytestmark = pytest.mark.skipif(not CO_CHROME, reason="cần Chrome")

KHOA_QT = "khoa-quan-tri-giao-dien-0123456789ab"
QT = {"Authorization": f"Bearer {KHOA_QT}"}


@contextmanager
def loi_that(tmp_path, monkeypatch):
    """Lõi thật uvicorn với thư mục dữ liệu tạm."""
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")
    with MayChuThat(app, lifespan="on") as w:
        yield w


def test_giao_dien_sao_luu_khoi_phuc_1280(trang, tmp_path, monkeypatch):
    """Máy tính 1280x800: Thẻ sao lưu và khôi phục hiện đầy đủ sau khi đăng nhập quản trị."""
    with loi_that(tmp_path, monkeypatch) as w:
        trang.khung(1280, 800)
        dang_nhap_quan_tri(trang, w.url)
        trang.di(f"{w.url}/He-thong-Bao-mat.dc.html")

        # Đã đăng nhập: thẻ Sao lưu & Khôi phục hiện diện
        assert trang.cho("document.getElementById('sao-luu-khoi-phuc') !== null")

        # 3. Các thành phần xuất sao lưu
        assert trang.cho("document.getElementById('o-mat-khau-sao-luu') !== null")
        assert trang.cho("document.getElementById('cb-kem-phien') !== null")
        assert trang.cho("document.getElementById('nut-tai-sao-luu') !== null")

        # 4. Các thành phần khôi phục
        assert trang.cho("document.getElementById('o-tep-khoi-phuc') !== null")
        assert trang.cho("document.getElementById('o-mat-khau-khoi-phuc') !== null")
        assert trang.cho("document.getElementById('nut-kiem-khoi-phuc') !== null")
        assert trang.cho("document.getElementById('nut-ap-dung-khoi-phuc') !== null")

        # 5. Nhập thử mật khẩu sao lưu
        trang.go("#o-mat-khau-sao-luu", "MatKhauAnToan123!")
        val_mk = trang.js("document.getElementById('o-mat-khau-sao-luu').value")
        assert val_mk == "MatKhauAnToan123!"


def test_giao_dien_sao_luu_khoi_phuc_dien_thoai_390(trang, tmp_path, monkeypatch):
    """Điện thoại 390x844: Không tràn ngang khi hiện thẻ sao lưu và khôi phục."""
    with loi_that(tmp_path, monkeypatch) as w:
        trang.khung(390, 844)
        dang_nhap_quan_tri(trang, w.url)
        trang.di(f"{w.url}/He-thong-Bao-mat.dc.html")
        trang.cho("document.getElementById('sao-luu-khoi-phuc') !== null")

        # Kiểm tra không tràn ngang ở 390px
        cuon_ngang = trang.js("document.documentElement.scrollWidth <= 390")
        assert cuon_ngang, "Trang bị tràn ngang ở màn hình 390px"


def _ban_sao_luu(tep: dict) -> bytes:
    """Bản sao lưu KHÔNG mã hoá đúng định dạng ứng dụng (như test_web2api_sao_luu_an_toan.tao_ban)."""
    nd = {k: json.dumps(v).encode("utf-8") for k, v in tep.items()}
    man = {"phien_ban": 1, "tao_luc": "2026-10-13T00:00:00+00:00", "modules_git": [],
           "tep": [{"duong": k, "sha256": hashlib.sha256(v).hexdigest(), "kich_thuoc": len(v)} for k, v in nd.items()]}
    bio = io.BytesIO()
    with tarfile.open(fileobj=bio, mode="w") as tf:
        for k, v in [("manifest.json", json.dumps(man).encode("utf-8"))] + list(nd.items()):
            ti = tarfile.TarInfo(k)
            ti.size = len(v)
            tf.addfile(ti, io.BytesIO(v))
    return sao_luu.MAGIC + bytes([1, sao_luu.KIEU_THO]) + bio.getvalue()


def _chon_tep(trang, du_lieu: bytes, ten: str = "b.w2a") -> None:
    """Đặt tệp vào ô chọn tệp như người chọn (DataTransfer) và phát sự kiện change."""
    b64 = base64.b64encode(du_lieu).decode("ascii")
    trang.js(f"""(() => {{
      const bin = atob('{b64}'); const u = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) u[i] = bin.charCodeAt(i);
      const dt = new DataTransfer(); dt.items.add(new File([u], {json.dumps(ten)}));
      const o = document.getElementById('o-tep-khoi-phuc'); o.files = dt.files;
      o.dispatchEvent(new Event('change', {{ bubbles: true }}));
      return true; }})()""")


def _mo_trang(trang, w):
    trang.khung(1280, 800)
    dang_nhap_quan_tri(trang, w.url)
    trang.di(f"{w.url}/He-thong-Bao-mat.dc.html")
    trang.cho("document.getElementById('nut-ap-dung-khoi-phuc') !== null")


TAT = "document.getElementById('nut-ap-dung-khoi-phuc').disabled"


def test_ap_dung_chi_sau_kiem_dung_tep_va_xac_nhan(trang, tmp_path, monkeypatch):
    nhan_xau = '<img src=x onerror="window.__xss=1">'
    ban = _ban_sao_luu({
        "dia_chi.json": {"url": "https://w2a.example.com"},
        "khoa_khach.json": {"khoa": [{"id": "kk_moi", "nhan": nhan_xau, "bat": True, "pham_vi": ["chat"],
                                      "tao_luc": None, "dung_cuoi": None, "so_luot": 0, "bam": "a" * 64}]},
    })
    with loi_that(tmp_path, monkeypatch) as w:
        _mo_trang(trang, w)
        assert trang.js(TAT) is True, "Áp dụng phải tắt khi chưa kiểm"
        _chon_tep(trang, ban)
        assert trang.js(TAT) is True, "chọn tệp chưa đủ — phải «Kiểm tra» trước"

        trang.bam("document.getElementById('nut-kiem-khoi-phuc')")
        trang.cho("document.getElementById('khac-biet-khoi-phuc') !== null", mo_ta="hiện khác biệt sau kiểm")
        chu = trang.chu("#khac-biet-khoi-phuc")
        assert "Khoá khách" in chu and "kk_moi" in chu and "chat" in chu and "[mới]" in chu
        assert "dia_chi.json" in chu
        assert nhan_xau in chu, "nhãn có thẻ HTML phải hiện nguyên chữ"
        assert trang.js("document.querySelector('#khac-biet-khoi-phuc img') === null")
        assert trang.js("window.__xss === undefined")
        assert trang.js(TAT) is False, "kiểm đạt → Áp dụng bật"

        trang.bam("document.getElementById('nut-ap-dung-khoi-phuc')")
        trang.cho("document.getElementById('hop-xac-nhan-khoi-phuc') !== null", mo_ta="hộp xác nhận")
        assert not (tmp_path / "dia_chi.json").exists(), "mở hộp xác nhận chưa được ghi gì"
        trang.bam("document.getElementById('nut-huy-khoi-phuc')")
        assert trang.cho("document.getElementById('hop-xac-nhan-khoi-phuc') === null")
        assert not (tmp_path / "dia_chi.json").exists()

        trang.bam("document.getElementById('nut-ap-dung-khoi-phuc')")
        trang.cho("document.getElementById('nut-xac-nhan-khoi-phuc') !== null")
        trang.bam("document.getElementById('nut-xac-nhan-khoi-phuc')")
        trang.cho("document.getElementById('thong-bao-khoi-phuc') !== null", mo_ta="báo khôi phục xong")
        assert ".truoc-khoi-phuc-" in trang.chu("#thong-bao-khoi-phuc")
        assert json.loads((tmp_path / "dia_chi.json").read_text())["url"] == "https://w2a.example.com"
        assert trang.js(TAT) is True, "áp dụng xong → phải kiểm lại mới áp dụng tiếp"
        assert trang.js("document.getElementById('o-mat-khau-khoi-phuc').type") == "password"
        assert trang.js("JSON.stringify(Object.assign({}, localStorage)).indexOf('w2a.example.com') === -1")


def test_doi_tep_sau_kiem_thi_phai_kiem_lai(trang, tmp_path, monkeypatch):
    with loi_that(tmp_path, monkeypatch) as w:
        _mo_trang(trang, w)
        _chon_tep(trang, _ban_sao_luu({"dia_chi.json": {"url": "https://a.example.com"}}), "a.w2a")
        trang.bam("document.getElementById('nut-kiem-khoi-phuc')")
        trang.cho(f"!{TAT}", mo_ta="kiểm đạt")
        _chon_tep(trang, _ban_sao_luu({"dia_chi.json": {"url": "https://b.example.com"}}), "b.w2a")
        assert trang.cho(TAT, mo_ta="đổi tệp → Áp dụng tắt lại")
        assert trang.js("document.getElementById('khac-biet-khoi-phuc') === null")


def test_ban_co_muc_bi_tu_choi_khong_ap_dung_duoc(trang, tmp_path, monkeypatch):
    (tmp_path / "bi_mat").mkdir(mode=0o700)
    (tmp_path / "bi_mat" / "xa1.json").write_text(json.dumps({"api_key": "sk-lan-0000000000000000000000000000xyzw"}))
    rec = {"id": "xa1", "ten": "GPU", "tien_to": "xa1", "base_url": "http://192.168.1.220:8000/v1",
           "cai_luc": "2026-10-13T00:00:00+00:00"}
    (tmp_path / "tu_xa.json").write_text(json.dumps({"modules": [rec]}))
    ban = _ban_sao_luu({"tu_xa.json": {"modules": [{**rec, "base_url": "http://192.168.1.99:8000/v1"}]}})
    with loi_that(tmp_path, monkeypatch) as w:
        _mo_trang(trang, w)
        _chon_tep(trang, ban)
        trang.bam("document.getElementById('nut-kiem-khoi-phuc')")
        trang.cho("document.getElementById('tu-choi-khoi-phuc') !== null", mo_ta="báo bị từ chối")
        chu = trang.chu("#khac-biet-khoi-phuc")
        assert "BỊ TỪ CHỐI" in chu and "xa1" in chu
        assert "http://192.168.1.220:8000/v1 → http://192.168.1.99:8000/v1" in chu
        assert trang.js(TAT) is True
        trang.khung(390, 844)  # kết quả kiểm (URL dài, sha, lý do dài) không làm tràn ngang trên điện thoại
        assert trang.cho("document.documentElement.scrollWidth <= 390", mo_ta="không tràn ngang ở 390px sau kiểm")
