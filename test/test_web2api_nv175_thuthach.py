"""Module thuthach (NV175): nhận diện thử thách chống bot bằng CẤU TRÚC và báo
«cần người» — KHÔNG giải, KHÔNG tích ô, KHÔNG gọi dịch vụ giải hộ.

Test-first: mọi hành vi (phân loại từng loại thử thách, chặn giải, trần kích
thước, bí mật trong URL không lộ, 401/403/IDOR qua API thật) viết TRƯỚC, phải
ĐỎ khi mã chưa có / bị đột biến, XANH khi đúng. Không có mạng: module thuần
phân tích chuỗi do người gọi đưa vào; conftest chặn Internet thật.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.kham_pha import doc_manifest  # noqa: E402

GOC = Path(__file__).resolve().parents[1]
THU_MUC = GOC / "modules" / "thuthach"


def _tai_module(ten_goi: str = "modules_thuthach_pkg"):
    tep = THU_MUC / "module.py"
    spec = importlib.util.spec_from_file_location(
        ten_goi, tep, submodule_search_locations=[str(THU_MUC)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[ten_goi] = mod
    spec.loader.exec_module(mod)
    return mod


def _tai_phat_hien(ten_goi: str = "modules_thuthach_phat_hien_pkg"):
    tep = THU_MUC / "phat_hien.py"
    spec = importlib.util.spec_from_file_location(ten_goi, tep)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[ten_goi] = mod
    spec.loader.exec_module(mod)
    return mod


def _manifest():
    m, loi = doc_manifest(THU_MUC)
    assert loi is None, loi
    return m


def _module(mod=None):
    mod = mod or _tai_module()
    return mod.Module(_manifest())


def _kiem_tra(mod, tham_so):
    inst = _module(mod)
    inst.nap({})
    return asyncio.run(inst.thuc_hien("thuthach.thu_thach", "kiem_tra", tham_so))


# ── Trang mẫu (dữ liệu giả, không phải trang thật) ────────────────────────

HTML_RECAPTCHA_V2 = """<html><body>
<div class="g-recaptcha" data-sitekey="6LeFAKE0000000000000000000000000AA"></div>
<iframe src="https://www.google.com/recaptcha/api2/anchor?ar=1&k=6LeFAKE"
        title="reCAPTCHA"></iframe>
</body></html>"""

HTML_RECAPTCHA_V3 = """<html><head>
<script src="https://www.google.com/recaptcha/api.js?render=6LeFAKE"></script>
<script>grecaptcha.ready(function(){ grecaptcha.execute('6LeFAKE', {action:'submit'}); });</script>
</head><body><form>Đăng nhập</form></body></html>"""

HTML_TURNSTILE = """<html><body>
<iframe src="https://challenges.cloudflare.com/cdn-cgi/challenge-platform/h/g/turnstile/f/ov2"
        style="border:none"></iframe>
<div class="cf-turnstile" data-sitekey="0x4FAKE"></div>
</body></html>"""

HTML_CAPTCHA_CHU = """<html><body><form>
<img src="/captcha/image/abc123/" alt="captcha">
<label>Nhập mã trong hình <input type="text" name="captcha_code"></label>
</form></body></html>"""

HTML_2FA = """<html><body><form>
<input type="tel" name="totpPin" autocomplete="one-time-code">
</form></body></html>"""

HTML_SACH = """<html><body><h1>Trang thường</h1>
<form><input type="text" name="q"><button>Tìm</button></form></body></html>"""

URL_BOTGUARD = "https://accounts.google.com/signin/v2/challenge/botguard?flowName=GlifWebSignIn"
URL_RECAPTCHA = "https://accounts.google.com/v3/signin/challenge/recaptcha"


# ── A. Phân loại thuần (phat_hien) ────────────────────────────────────────

def test_phat_hien_recaptcha_v2():
    ph = _tai_phat_hien()
    kq = ph.phan_loai(HTML_RECAPTCHA_V2, "https://host.example.invalid/login")
    assert kq["co_thu_thach"] is True
    assert kq["loai"] == "recaptcha_v2"
    assert kq["can_nguoi"] is True


def test_phat_hien_recaptcha_v3_khong_nham_v2():
    ph = _tai_phat_hien()
    kq = ph.phan_loai(HTML_RECAPTCHA_V3, "https://host.example.invalid/login")
    assert kq["co_thu_thach"] is True
    assert kq["loai"] == "recaptcha_v3"


def test_v2_thang_v3_khi_ca_hai():
    """Trang có cả widget v2 và grecaptcha.execute → phải báo v2 (thử thách
    nhìn thấy được, người phải xử lý trực tiếp)."""
    ph = _tai_phat_hien()
    kq = ph.phan_loai(HTML_RECAPTCHA_V2 + HTML_RECAPTCHA_V3, "")
    assert kq["loai"] == "recaptcha_v2"


def test_phat_hien_turnstile():
    ph = _tai_phat_hien()
    kq = ph.phan_loai(HTML_TURNSTILE, "https://host.example.invalid/")
    assert kq["co_thu_thach"] is True
    assert kq["loai"] == "turnstile"
    assert kq["can_nguoi"] is True


def test_phat_hien_captcha_chu():
    ph = _tai_phat_hien()
    kq = ph.phan_loai(HTML_CAPTCHA_CHU, "https://host.example.invalid/dang-ky")
    assert kq["co_thu_thach"] is True
    assert kq["loai"] == "captcha_chu"


def test_phat_hien_2fa_can_nguoi_nhap_ma():
    """Trang nhập mã 2FA không phải CAPTCHA nhưng máy cũng KHÔNG tự nhập:
    phân loại riêng và vẫn «cần người»."""
    ph = _tai_phat_hien()
    kq = ph.phan_loai(HTML_2FA, "https://accounts.google.com/signin/challenge/totp")
    assert kq["co_thu_thach"] is True
    assert kq["loai"] == "xac_thuc_hai_lop"
    assert kq["can_nguoi"] is True


def test_phat_hien_botguard_qua_url():
    ph = _tai_phat_hien()
    kq = ph.phan_loai("<html><body>Đang kiểm tra…</body></html>", URL_BOTGUARD)
    assert kq["co_thu_thach"] is True
    assert kq["loai"] == "botguard"


def test_phat_hien_recaptcha_qua_url():
    ph = _tai_phat_hien()
    kq = ph.phan_loai("", URL_RECAPTCHA)
    assert kq["co_thu_thach"] is True
    assert kq["loai"] == "recaptcha_v2"


def test_trang_sach_khong_thu_thach():
    ph = _tai_phat_hien()
    kq = ph.phan_loai(HTML_SACH, "https://host.example.invalid/tim-kiem")
    assert kq["co_thu_thach"] is False
    assert kq["loai"] is None
    assert kq["can_nguoi"] is False


def test_rong_khong_loi():
    ph = _tai_phat_hien()
    kq = ph.phan_loai("", "")
    assert kq["co_thu_thach"] is False


def test_html_qua_lon_bi_tu_choi():
    ph = _tai_phat_hien()
    lon = "<html>" + "a" * (ph.TRAN_HTML_KY_TU + 1)
    with pytest.raises(ph.LoiThuThach) as e:
        ph.phan_loai(lon, "")
    assert e.value.ma == "html_qua_lon"


def test_dau_vao_sai_kieu_bi_tu_choi():
    ph = _tai_phat_hien()
    with pytest.raises(ph.LoiThuThach):
        ph.phan_loai(123, "")
    with pytest.raises(ph.LoiThuThach):
        ph.phan_loai("", {"url": "x"})


def test_ket_qua_khong_chua_du_lieu_giai():
    """Kết quả phân loại KHÔNG được chứa dữ liệu giúp giải thử thách:
    không sitekey, không token, không trích đoạn HTML gốc."""
    ph = _tai_phat_hien()
    kq = ph.phan_loai(HTML_RECAPTCHA_V2, "https://host.example.invalid/login")
    chuoi = json.dumps(kq, ensure_ascii=False)
    assert "6LeFAKE" not in chuoi
    assert "sitekey" not in chuoi.lower()
    assert "token" not in chuoi.lower()


def test_url_query_bi_mat_khong_lo():
    """URL có tham số bí mật trong query: phân loại chỉ dùng đường dẫn,
    kết quả và log không chứa giá trị bí mật."""
    ph = _tai_phat_hien()
    url = ("https://accounts.google.com/signin/v2/challenge/botguard"
           "?token=BI-MAT-XYZ-123&password=abc")
    kq = ph.phan_loai("", url)
    chuoi = json.dumps(kq, ensure_ascii=False)
    assert kq["loai"] == "botguard"
    assert "BI-MAT-XYZ-123" not in chuoi
    assert "abc" not in chuoi.replace("botguard", "")
    # Qua đường module: TOÀN BỘ kết quả không phản chiếu URL/nguyên liệu vào.
    kq2 = _kiem_tra(None, {"html": "", "url": url})
    assert kq2["ok"] is True
    chuoi2 = json.dumps(kq2, ensure_ascii=False)
    assert "BI-MAT-XYZ-123" not in chuoi2
    assert "accounts.google.com" not in chuoi2
    assert "challenge/botguard" not in chuoi2


# ── B. Hợp đồng module (thiet_bi) ─────────────────────────────────────────

def test_thuc_the_va_trang_thai():
    inst = _module()
    inst.nap({})
    tt = asyncio.run(inst.thuc_the())
    assert len(tt) == 1
    assert tt[0]["id"] == "thuthach.thu_thach"
    assert tt[0]["rui_ro_mac_dinh"] == "doc"
    hanh_dong = {t["hanh_dong"] for t in tt[0]["tinh_nang"]}
    assert hanh_dong == {"kiem_tra", "loai_ho_tro"}
    kq = asyncio.run(inst.trang_thai(["thuthach.thu_thach"]))
    assert kq["thuthach.thu_thach"]["gia_tri"] == "san_sang"
    assert asyncio.run(inst.trang_thai(["thuthach.khong_co"])) == {}


def test_kiem_tra_qua_module_bao_can_nguoi():
    kq = _kiem_tra(None, {"html": HTML_TURNSTILE,
                          "url": "https://host.example.invalid/"})
    assert kq["ok"] is True
    assert kq["ket_qua"]["co_thu_thach"] is True
    assert kq["ket_qua"]["loai"] == "turnstile"
    assert kq["ket_qua"]["can_nguoi"] is True
    assert kq["ket_qua"]["trang_thai"] == "can_nguoi"
    hd = " ".join(kq["ket_qua"]["huong_dan"])
    assert "noVNC" in hd


def test_kiem_tra_trang_sach():
    kq = _kiem_tra(None, {"html": HTML_SACH,
                          "url": "https://host.example.invalid/"})
    assert kq["ok"] is True
    assert kq["ket_qua"]["co_thu_thach"] is False
    assert kq["ket_qua"]["can_nguoi"] is False
    assert kq["ket_qua"]["trang_thai"] == "khong_thu_thach"


def test_kiem_tra_html_qua_lon_tra_loi():
    ph = _tai_phat_hien()
    kq = _kiem_tra(None, {"html": "a" * (ph.TRAN_HTML_KY_TU + 1)})
    assert kq["ok"] is False
    assert "html_qua_lon" in kq["loi"]


def test_kiem_tra_thieu_ca_html_va_url():
    kq = _kiem_tra(None, {})
    assert kq["ok"] is False


def test_hanh_dong_giai_bi_tu_choi():
    """Mọi hành động «giải»/lạ đều bị từ chối — module không có đường giải."""
    inst = _module()
    inst.nap({})
    for hd in ("giai", "giai_captcha", "solve", "tich_o", "vuot_qua", "xoa"):
        kq = asyncio.run(inst.thuc_hien("thuthach.thu_thach", hd, {}))
        assert kq["ok"] is False, hd


def test_thuc_the_la_bi_tu_choi():
    inst = _module()
    inst.nap({})
    kq = asyncio.run(inst.thuc_hien("thuthach.khong_co", "kiem_tra",
                                    {"html": HTML_SACH}))
    assert kq["ok"] is False


def test_loai_ho_tro():
    inst = _module()
    inst.nap({})
    kq = asyncio.run(inst.thuc_hien("thuthach.thu_thach", "loai_ho_tro", {}))
    assert kq["ok"] is True
    loai = {m["loai"] for m in kq["ket_qua"]["loai_ho_tro"]}
    assert {"recaptcha_v2", "recaptcha_v3", "turnstile", "captcha_chu",
            "xac_thuc_hai_lop", "botguard"} <= loai
    for m in kq["ket_qua"]["loai_ho_tro"]:
        assert m["cach_xu_ly"] == "can_nguoi"


def test_cai_va_suc_khoe_khong_mang():
    inst = _module()
    r = asyncio.run(inst.cai({}))
    assert r["ok"] is True
    r = asyncio.run(inst.cai({"kenh_no_vnc": "Hệ thống → Trình duyệt → noVNC"}))
    assert r["ok"] is True
    assert r["cau_hinh_luu"]["kenh_no_vnc"]
    r = asyncio.run(inst.cai({"kenh_no_vnc": 123}))
    assert r["ok"] is False
    sk = asyncio.run(inst.suc_khoe())
    assert sk["ok"] is True
    assert sk["che_do"] == "chi_nhan_dien"


def test_ma_nguon_khong_co_duong_giai():
    """Quét tĩnh: module không chứa cơ chế giải/vượt thử thách nào."""
    nguon = (THU_MUC / "module.py").read_text(encoding="utf-8") + \
        (THU_MUC / "phat_hien.py").read_text(encoding="utf-8")
    thap = nguon.lower()
    for cam in ("2captcha", "capsolver", "nextcaptcha", "rucaptcha",
                ".click(", "getresponse", "solve_recaptcha",
                "solve_turnstile", "api_key_solver", "giai_captcha("):
        assert cam not in thap, cam


def test_nhat_ky_khong_lo_query_url(caplog):
    """URL chứa bí mật cũng không vào log khi kiểm tra."""
    bi_mat = "BI-MAT-LOG-456"
    url = ("https://accounts.google.com/signin/v2/challenge/botguard"
           f"?access_token={bi_mat}")
    with caplog.at_level(logging.DEBUG):
        kq = _kiem_tra(None, {"html": "", "url": url})
    assert kq["ok"] is True
    assert bi_mat not in caplog.text


# ── C. 401/403/IDOR qua API thật ─────────────────────────────────────────

KHOA_QUAN_TRI = "khoa-quan-tri-test-nv175-0123456789ab"


def test_api_401_403_idor(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app import so_module
    from app.main import app

    anh_chi_module = tmp_path / "anh-chi-thuthach"
    anh_chi_module.mkdir()
    (anh_chi_module / "thuthach").symlink_to(THU_MUC, target_is_directory=True)
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QUAN_TRI)
    monkeypatch.setattr(so_module, "THU_MUC_ANH", anh_chi_module)
    so_cu = getattr(app.state, "so", None)
    try:
        with TestClient(app) as client:
            r = client.post("/api/modules/nap-lai",
                            headers={"Authorization": f"Bearer {KHOA_QUAN_TRI}"})
            assert r.status_code == 200
            r = client.post("/api/modules/thuthach/cai",
                            headers={"Authorization": f"Bearer {KHOA_QUAN_TRI}"},
                            json={"cau_hinh": {}})
            assert r.status_code == 200

            than = {"hanh_dong": "kiem_tra",
                    "tham_so": {"html": HTML_TURNSTILE,
                                "url": "https://host.example.invalid/"}}
            # Không khoá -> 401
            r = client.post("/api/thiet-bi/thuthach.thu_thach/hanh-dong",
                            json=than)
            assert r.status_code == 401
            # Khoá khách sai phạm vi -> 403
            from app import khoa_khach
            _, khoa_chat = khoa_khach.tao_khoa(nhan="khach chat",
                                               pham_vi=["chat"])
            r = client.post("/api/thiet-bi/thuthach.thu_thach/hanh-dong",
                            headers={"Authorization": f"Bearer {khoa_chat}"},
                            json=than)
            assert r.status_code == 403
            # Thực thể không tồn tại -> 404 (IDOR)
            r = client.post("/api/thiet-bi/thuthach.khong_co/hanh-dong",
                            headers={"Authorization": f"Bearer {KHOA_QUAN_TRI}"},
                            json=than)
            assert r.status_code == 404
            # Thực thể mới: rủi ro hiệu lực = nguy_hiem cho tới khi quản trị
            # xác nhận thực thể (SEC-004f). Gửi xac_nhan kèm xac_nhan:true.
            r = client.post("/api/thiet-bi/thuthach.thu_thach/hanh-dong",
                            headers={"Authorization": f"Bearer {KHOA_QUAN_TRI}"},
                            json=than)
            assert r.status_code == 409
            r = client.post("/api/thiet-bi/thuthach.thu_thach/xac-nhan",
                            headers={"Authorization": f"Bearer {KHOA_QUAN_TRI}"},
                            json={"xac_nhan": True})
            assert r.status_code == 200
            # Quản trị -> 200, kết quả «cần người»
            r = client.post("/api/thiet-bi/thuthach.thu_thach/hanh-dong",
                            headers={"Authorization": f"Bearer {KHOA_QUAN_TRI}"},
                            json=than)
            assert r.status_code == 200
            assert r.json()["ket_qua"]["loai"] == "turnstile"
            assert r.json()["ket_qua"]["can_nguoi"] is True
    finally:
        if so_cu is None:
            if hasattr(app.state, "so"):
                delattr(app.state, "so")
        else:
            app.state.so = so_cu
