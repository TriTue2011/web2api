"""Giao diện mới chạy với LÕI THẬT và module gemini-api THẬT (Google giả, GitHub giả) — trọn đường chủ máy đi.

test_web2api_giao_dien.py đo khung với module giả; ở đây là module thật của kho để chắc hợp đồng manifest →
khung (loai_ten, cung_cap, truong_cau_hinh, suc_khoe theo khả năng) đúng với mã thật:
  mở địa chỉ gốc → Hệ thống → Module → dán khoá quản trị → ô gemini-api «Chưa cài» từ git → Cài (không khoá)
  → tự sang Studio (trạng thái rỗng + dải «Đã cài …») → bấm tới Hệ thống → AI → Tài khoản → thêm khoá → Kiểm tra
  và lưu (Google giả) → Provider hiện, không tải lại trang → Quay lại Studio → chat stream qua /v1 thật
  → Provider: model + sức khoẻ thật → gỡ.
Nguồn git lấy từ biến môi trường như docker-compose.yml đặt sẵn (không cấu hình tay).
"""

import json
from pathlib import Path

import pytest

from app import so_module
from app.main import app
from gia import GitHubGia, GoogleGia, MayChuThat, tep_kho
from ui_chung import CO_CHROME, trang, trinh_duyet  # noqa: F401 — fixture

GOC_GEMINI = Path(__file__).resolve().parents[1] / "modules" / "gemini-api"
KHOA_QT = "khoa-quan-tri-ui-that-0123456789abcd"
K_TOT = "AIzaSyUI-TOT-0000000000000000000000tot1"
K_XAU = "AIzaSyUI-XAU-0000000000000000000000xau2"
REPO = "TriTue2011/web2api"

pytestmark = pytest.mark.skipif(not CO_CHROME, reason="cần Chrome")


def _tep_gemini():
    return {str(p.relative_to(GOC_GEMINI)): p.read_bytes() for p in GOC_GEMINI.rglob("*")
            if p.is_file() and "__pycache__" not in p.parts}


def test_cai_gemini_tu_git_qua_giao_dien_voi_loi_that(trang, tmp_path, monkeypatch):
    gh = GitHubGia(REPO)
    gh.day(tep_kho({"gemini-api": _tep_gemini()}), thong_diep="thêm module gemini-api")
    g = GoogleGia()
    g.hanh_vi.update({K_TOT: {"kieu": "tot"}, K_XAU: {"kieu": "401"}})
    with MayChuThat(gh.app) as s_gh, MayChuThat(g.app) as s_g:
        monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
        monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
        monkeypatch.setenv("WEB2API_GITHUB_API", s_gh.url)
        monkeypatch.setenv("WEB2API_GIT_REPO", REPO)  # như docker-compose.yml: không cấu hình tay
        monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")
        with MayChuThat(app, lifespan="on") as w:
            trang.cdp.goi("Page.navigate", {"url": w.url + "/"}, trang.s)
            trang.cho("location.pathname.endsWith('/He-thong.dc.html') && document.querySelector('x-dc')?.dataset.dc === 'san-sang'",
                      mo_ta="địa chỉ gốc → Hệ thống → Module")
            trang.go("#o-khoa", KHOA_QT)
            trang.bam("document.getElementById('nut-luu-khoa')")
            o = "document.querySelector('[data-o=\"gemini-api\"]')"
            trang.cho(o, han=15, mo_ta="ô gemini-api từ git")
            assert trang.js(o + ".dataset.tt") == "chua-cai"
            assert trang.js(o + ".closest('[data-khu]').dataset.khu") == "ai"
            assert trang.chu("[data-khu='ai'] .khu-ten").startswith("AI")   # loai_ten từ manifest thật
            trang.js("window.__khongTaiLai = 1")

            trang.bam(o)
            trang.cho("document.getElementById('nut-cai')", mo_ta="nút Cài")
            assert "generativelanguage.googleapis.com" in trang.chu("#can-gi") and "httpx" in trang.chu("#can-gi")
            trang.bam("document.getElementById('nut-cai')")
            # Cài xong: module khai chat → tự sang Studio (trang chính) kèm dải thông báo; Studio rỗng vì chưa có khoá.
            trang.cho("document.querySelector('x-dc')?.dataset.dc === 'san-sang' && location.pathname.endsWith('/Studio.dc.html') && !!document.getElementById('da-cai')", han=20,
                      mo_ta="tự sang Studio sau khi cài")
            assert "Đã cài Gemini" in trang.chu("#da-cai"), trang.chu("#da-cai")
            assert "Chưa có khoá cho «Gemini" in trang.chu("#studio-chua-khoa")
            assert trang.js("[...document.querySelectorAll('[data-che-do]')].map((b) => b.dataset.cheDo)") == ["chat", "anh", "video"]

            # Dải thông báo → Hệ thống → AI → Tài khoản, đúng thẻ gemini-api.
            trang.bam("document.querySelector('#da-cai a')")
            tk = "[data-tai-khoan='gemini-api']"
            trang.cho(f"location.pathname.endsWith('/He-thong-AI.dc.html') && !!document.querySelector({json.dumps(tk + ' textarea')})",
                      han=15, mo_ta="form khoá ở Hệ thống → AI → Tài khoản")
            assert "Chưa có khoá" in trang.chu(tk + " .chua-khoa")
            trang.js("window.__khongTaiLai = 1")
            trang.go(tk + " [data-truong='api_keys'] textarea", f"{K_TOT}\n{K_XAU}")
            trang.go(tk + " [data-truong='base_url'] input", s_g.url)
            trang.bam(f"document.querySelector({json.dumps(tk + ' .nut-luu-cau-hinh')})")
            trang.cho(f"!!document.querySelector({json.dumps(tk + ' .ket-qua-cai li')})", han=15, mo_ta="liên kết xong")
            dong = trang.js(f"[...document.querySelectorAll({json.dumps(tk + ' .ket-qua-cai li')})].map((l) => l.innerText.replace(/\\s+/g, ' '))")
            assert f"…{K_TOT[-4:]} · dùng được" in dong[0] and "2 model" in dong[0], dong
            assert f"…{K_XAU[-4:]} · không dùng được" in dong[1] and "HTTP 401" in dong[1], dong
            assert json.loads((tmp_path / "bi_mat" / "gemini-api.json").read_text())["api_keys"] == [K_TOT]
            trang.cho("document.querySelector('[data-tab=\"provider\"]')", han=15, mo_ta="Provider hiện ngay")
            assert trang.js("window.__khongTaiLai") == 1

            # Studio: chat stream qua /v1 thật → Google giả; ba chế độ vì manifest khai chat, anh, video.
            trang.bam("document.querySelector('[data-nav=\"quay-lai\"]')")
            trang.cho("document.querySelector('x-dc')?.dataset.dc === 'san-sang' && location.pathname.endsWith('/Studio.dc.html') && document.querySelectorAll('#chon-model option').length > 0",
                      han=15, mo_ta="Studio có model")
            assert not trang.js("!!document.getElementById('studio-chua-khoa')")
            assert trang.js("[...document.querySelectorAll('[data-che-do]')].map((b) => b.dataset.cheDo)") == ["chat", "anh", "video"]
            trang.go("#o-soan", "Chào")
            trang.bam("document.getElementById('nut-gui')")
            trang.cho("document.getElementById('trang-thai')?.innerText === 'Xong.'", han=15, mo_ta="chat xong")
            assert trang.chu("[data-vai='assistant']") == "MộtHai"
            gui = [x for x in g.nhat_ky if x["duong"] == "chat"][-1]
            assert gui["khoa"] == K_TOT and gui["than"]["stream"] is True

            # Hệ thống → AI → Provider: model thật + sức khoẻ thật, kể cả trạng thái ảnh/video theo khoá.
            trang.di(w.url + "/He-thong-AI.dc.html#provider")
            trang.cho("document.querySelector('[data-provider=\"gemini-api\"] .suc-khoe')", han=15, mo_ta="sức khoẻ")
            the = trang.chu("[data-provider='gemini-api']")
            assert "gemini/gemini-2.5-flash" in the and f"…{K_TOT[-4:]}" in the and "anh: chưa rõ" in the, the

            html = trang.js("document.documentElement.outerHTML")
            assert K_TOT not in html and K_XAU not in html
            assert not any(K_TOT in s or K_XAU in s for s in trang.state_thanh_phan())

            # Gỡ: module còn trên git → lại «Chưa cài»; bí mật bị xoá.
            trang.di(w.url + "/He-thong.dc.html#module-gemini-api")
            trang.cho("document.getElementById('nut-go')", mo_ta="nút Gỡ")
            trang.bam("document.getElementById('nut-go')")
            trang.bam("document.getElementById('nut-go-han')")
            trang.cho(o + "?.dataset.tt === 'chua-cai'", han=15, mo_ta="lại «Chưa cài»")
            assert not trang.js("!!document.querySelector('[data-nav=\"quay-lai\"]')")
            assert not (tmp_path / "bi_mat" / "gemini-api.json").exists()
    assert all(x["auth"] is None for x in gh.nhat_ky)  # kho public: đọc ẩn danh
