"""Giao diện mới chạy với LÕI THẬT và module gemini-api THẬT (Google giả, GitHub giả) — trọn đường chủ máy đi.

test_web2api_giao_dien.py đo khung với module giả; ở đây là module thật của kho để chắc hợp đồng manifest →
khung (loai_ten, cung_cap, truong_cau_hinh, suc_khoe theo khả năng) đúng với mã thật:
  mở địa chỉ gốc → Hệ thống → Module → dán khoá quản trị → ô gemini-api «Chưa cài» từ git → Cài (không khoá)
  → tự sang Studio (trạng thái rỗng: dòng «Chưa có khoá … — Thêm khoá») → bấm tới Hệ thống → AI → Tài khoản → thêm khoá → Kiểm tra
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
from ui_chung import CO_CHROME, dang_nhap_quan_tri, trang, trinh_duyet  # noqa: F401 — fixture

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
        monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")  # NV16: NguonGit ghim IP, máy chủ giả ở loopback
        monkeypatch.setenv("WEB2API_GIT_REPO", REPO)  # như docker-compose.yml: không cấu hình tay
        monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")
        with MayChuThat(app, lifespan="on") as w:
            dang_nhap_quan_tri(trang, w.url)
            trang.di(w.url + "/", mo_muc=False)  # NV256: qua di() để vượt chặn LNA của Chromium ≥138
            trang.cho("location.pathname.endsWith('/He-thong.dc.html') && document.querySelector('x-dc')?.dataset.dc === 'san-sang'",
                      mo_ta="địa chỉ gốc → Hệ thống → Module")
            o = "document.querySelector('[data-o=\"gemini-api\"]')"
            trang.cho(o, han=15, mo_ta="ô gemini-api từ git")
            assert trang.js(o + ".dataset.tt") == "chua-cai"
            assert trang.js(o + ".closest('[data-lane]').dataset.lane") == "ai"   # NV255: làn thay khu (sửa selector)
            assert trang.js("document.querySelector(\"[data-lane='ai'] .lane-ten\").textContent").startswith("AI")   # loai_ten từ manifest thật (sửa selector)
            trang.js("window.__khongTaiLai = 1")

            trang.bam(o)
            trang.cho("document.getElementById('nut-cai')", mo_ta="nút Cài")
            assert "generativelanguage.googleapis.com" in trang.chu("#can-gi") and "httpx" in trang.chu("#can-gi")
            trang.bam("document.getElementById('nut-cai')")
            # Hộp xác nhận cài mới hiện ra (NV61: cài module mới từ git phải qua xác nhận)
            trang.cho("!!document.getElementById('xac-nhan-cai')", han=15, mo_ta="hộp xác nhận cài mới")
            assert "Gemini" in trang.chu("#xac-nhan-cai") or "gemini-api" in trang.chu("#xac-nhan-cai")
            trang.bam("document.getElementById('nut-dong-y-cai')")
            # Cài xong: module khai chat → tự sang Studio (trang chính); chưa có khoá → MỘT dòng «Chưa có khoá cho … — Thêm khoá».
            trang.cho("document.querySelector('x-dc')?.dataset.dc === 'san-sang' && location.pathname.endsWith('/Studio.dc.html') && !!document.getElementById('studio-chua-khoa')", han=20,
                      mo_ta="tự sang Studio sau khi cài")
            assert "Chưa có khoá cho «Gemini" in trang.chu("#studio-chua-khoa")
            assert sorted(trang.js("[...document.querySelectorAll('[data-che-do]')].map((b) => b.dataset.cheDo)")) == ["anh", "chat", "video"]

            # «Thêm khoá» → Hệ thống → AI → Tài khoản: biểu mẫu «Thêm API» của gemini-api (trường từ manifest thật).
            trang.bam("document.querySelector('#studio-chua-khoa a[data-them-khoa=\"gemini-api\"]')")
            trang.cho("location.pathname.endsWith('/He-thong-AI.dc.html') && document.querySelector('#form-them')?.dataset.module === 'gemini-api'"
                      " && !!document.querySelector(\"#form-them [data-truong='api_keys'] input\")", han=15, mo_ta="biểu mẫu Thêm API")
            assert "Chưa có tài khoản API nào" in trang.chu("#tk-trong")
            trang.js("window.__khongTaiLai = 1")
            trang.go("#form-them [data-truong='api_keys'] input", K_XAU)
            trang.go("#form-them [data-truong='base_url'] input", s_g.url)
            trang.bam("document.getElementById('nut-them-luu')")
            trang.cho("document.getElementById('tin-ai')?.getAttribute('role') === 'alert'", han=15, mo_ta="khoá xấu bị từ chối")
            assert f"…{K_XAU[-4:]} không dùng được" in trang.chu("#tin-ai") and "HTTP 401" in trang.chu("#tin-ai")
            trang.go("#form-them [data-truong='api_keys'] input", K_TOT)
            trang.bam("document.getElementById('nut-them-luu')")
            trang.cho("document.querySelectorAll('[data-tai-khoan=\"gemini-api\"]').length === 1", han=15, mo_ta="liên kết xong")
            tin = trang.chu("#tin-ai")
            assert f"…{K_TOT[-4:]} dùng được" in tin and "2 model" in tin, tin
            assert json.loads((tmp_path / "bi_mat" / "gemini-api.json").read_text())["api_keys"] == [K_TOT]
            assert trang.js("window.__khongTaiLai") == 1

            # Studio: chat stream qua /v1 thật → Google giả; ba chế độ vì manifest khai chat, anh, video.
            trang.bam("document.querySelector('[data-nav=\"quay-lai\"]')")
            trang.cho("document.querySelector('x-dc')?.dataset.dc === 'san-sang' && location.pathname.endsWith('/Studio.dc.html') && document.querySelectorAll('[data-model-chon]').length > 0",
                      han=15, mo_ta="Studio có model")
            assert not trang.js("!!document.getElementById('studio-chua-khoa')")
            assert sorted(trang.js("[...document.querySelectorAll('[data-che-do]')].map((b) => b.dataset.cheDo)")) == ["anh", "chat", "video"]
            trang.go("#o-soan", "Chào")
            trang.bam("document.getElementById('nut-gui')")
            trang.cho("document.getElementById('trang-thai')?.innerText === 'Xong.'", han=15, mo_ta="chat xong")
            assert trang.chu("[data-vai='assistant']") == "MộtHai"
            gui = [x for x in g.nhat_ky if x["duong"] == "chat"][-1]
            assert gui["khoa"] == K_TOT and gui["than"]["stream"] is True

            # Hệ thống → AI → Provider: model thật (/v1/models qua Google giả) + sức khoẻ thật, kể cả ảnh/video theo khoá.
            trang.di(w.url + "/He-thong-AI.dc.html#provider")
            # Danh sách model thu gọn sẵn: mở nhóm «Trò chuyện» mới thấy model thật.
            trang.cho("!!document.querySelector('[data-provider=\"gemini-api\"] [data-mo-nhom=\"chat\"]')", han=15, mo_ta="nhóm model thật")
            assert not trang.js("!!document.querySelector('[data-provider=\"gemini-api\"] [data-model]')")
            trang.bam("document.querySelector('[data-mo-nhom=\"chat\"]')")
            trang.cho("document.querySelector('[data-provider=\"gemini-api\"] [data-model]')", mo_ta="model thật")
            assert "gemini/gemini-2.5-flash" in trang.chu("[data-provider='gemini-api']")
            trang.bam("document.querySelector('[data-pv-tab=tk]')")
            trang.cho("document.querySelector('[data-khoa-pv]')?.innerText.includes('Đã kết nối')", han=15, mo_ta="sức khoẻ")
            assert f"…{K_TOT[-4:]}" in trang.chu("[data-khoa-pv='0']")
            trang.bam("document.querySelector('[data-khoa-pv=\"0\"] button')")
            trang.cho("!!document.querySelector('[data-kv=kn-anh]')", mo_ta="chi tiết khoá")
            assert trang.chu("[data-kv=kn-anh]").startswith("chưa rõ"), trang.chu("[data-khoa-pv='0']")

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
