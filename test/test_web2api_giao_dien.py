"""Giao diện mới (lát A — KE-HOACH-GIAO-DIEN-MODULE.md) bằng Chrome THẬT, chạy với LÕI THẬT.

Lõi thật (uvicorn + lifespan + vòng nền git) + GitHub giả + module giả (ui_mau.py) — không còn API giả chép tay:
hình dạng phản hồi là của mã lõi. Mỗi ca kiểm đúng điều kiện hiện tab của khung (bảng mục 3 của kế hoạch) và
mục `can` của module (mục 3b) trong DOM: mục không đạt điều kiện KHÔNG có trong DOM, gõ tay đường dẫn tới đó thì
về trang Module kèm lời giải thích.

Trang thay thế (cũ → mới): Khoi-dau.dc.html + Cai-module.dc.html → index.html (chọn trang đầu),
He-thong.dc.html (Hệ thống → Module), He-thong-AI.dc.html (Hệ thống → AI), Studio.dc.html.
"""

from __future__ import annotations

import json
import time
from contextlib import contextmanager

import httpx
import pytest

from app import so_module
from app.main import app
from gia import GitHubGia, MayChuThat, ghi_thu_muc, tep_kho
from ui_chung import CO_CHROME, trang, trinh_duyet  # noqa: F401 — fixture
from gia import OpenAIGia
from ui_mau import XSS_TEN, module_ai, module_khong_khoa, module_mang

pytestmark = pytest.mark.skipif(not CO_CHROME, reason="cần Chrome")

KHOA_QT = "khoa-quan-tri-giao-dien-0123456789ab"
QT = {"Authorization": f"Bearer {KHOA_QT}"}
K_TOT = "AIzaSy-KHOA-MOT-tot-1111"
K_HONG = "AIzaSy-KHOA-HAI-hong-2222"
REPO = "TriTue2011/web2api"


@contextmanager
def loi_that(tmp_path, monkeypatch, modules: dict | None = None, git: dict | None = None):
    """Lõi thật trên thư mục dữ liệu tạm. `modules`: mã module đặt sẵn (đã cài); `git`: module chỉ có trên git."""
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")
    for id_, tep in (modules or {}).items():
        ghi_thu_muc(tmp_path / "modules" / id_, tep)
    if git:
        gh = GitHubGia(REPO)
        gh.day(tep_kho(git), thong_diep="thêm module")
        with MayChuThat(gh.app) as s_gh:
            monkeypatch.setenv("WEB2API_GITHUB_API", s_gh.url)
            monkeypatch.setenv("WEB2API_GIT_REPO", REPO)
            with MayChuThat(app, lifespan="on") as w:
                w.gh = gh  # test đẩy commit mới (bản mới) lên GitHub giả
                yield w
    else:
        with MayChuThat(app, lifespan="on") as w:
            yield w


def lien_ket(w, id_: str, cau_hinh: dict | None = None):
    r = httpx.post(f"{w.url}/api/modules/{id_}/cai", json={"cau_hinh": cau_hinh or {}}, headers=QT)
    assert r.status_code == 200 and r.json()["ok"], r.text


def luu_khoa(trang, khoa: str = KHOA_QT):
    trang.go("#o-khoa", khoa)
    trang.bam("document.getElementById('nut-luu-khoa')")


def nav(trang) -> list[str]:
    """data-nav của mọi liên kết điều hướng đang có trong DOM."""
    return trang.js("[...document.querySelectorAll('[data-nav]')].map((a) => a.dataset.nav)")


def o(id_: str) -> str:
    return f"document.querySelector('[data-o={json.dumps(id_)}]')"


def khong_tran(trang) -> bool:
    return trang.js("document.documentElement.scrollWidth <= window.innerWidth")


# ───────────── ca 0: chưa cài gì ─────────────

def test_ca_0_module_chi_co_module_va_go_tay_bi_chuyen(trang, tmp_path, monkeypatch):
    with loi_that(tmp_path, monkeypatch) as w:
        trang.cdp.goi("Page.navigate", {"url": w.url + "/"}, trang.s)
        trang.cho("location.pathname.endsWith('/He-thong.dc.html') && document.querySelector('x-dc')?.dataset.dc === 'san-sang'",
                  mo_ta="địa chỉ gốc dẫn tới Hệ thống → Module")
        trang.cho("document.getElementById('ket-noi')", mo_ta="khối Kết nối với web2api")
        assert nav(trang) == ["module"]                    # không Studio, không AI, không Quay lại
        assert "/v1" in trang.chu("#dia-chi-v1")
        assert trang.js("document.querySelectorAll('[data-o]').length") == 0
        assert trang.js("!!document.getElementById('o-them')")
        assert "Combo" not in trang.js("document.body.innerText")

        # Gõ tay trang chưa đủ điều kiện → về Module kèm một dòng «cần gì», không dựng màn đó.
        for duong, chu in (("/Studio.dc.html", "chat"), ("/He-thong-AI.dc.html", "AI")):
            trang.js("window.__trangCu = 1")  # đánh dấu trang hiện tại: chỉ chấp nhận trang MỚI sau điều hướng
            trang.cdp.goi("Page.navigate", {"url": w.url + duong}, trang.s)
            trang.cho("!window.__trangCu && document.querySelector('x-dc')?.dataset.dc === 'san-sang' && location.search.includes('thieu=') && !!document.getElementById('thieu')",
                      mo_ta=f"{duong} chuyển về Module")
            dong = trang.chu("#thieu")
            assert "chưa mở được" in dong and chu in dong, dong
            assert not trang.js("!!document.getElementById('bang-studio') || !!document.getElementById('bang-ai')")


# ───────────── luồng chính (chủ máy 11/10): Cài → vào Studio ngay → thêm khoá ở Hệ thống → AI ─────────────

def nut_o(id_: str) -> str:
    """Nút hành động trên ô (Cài / Cập nhật / Thêm khoá) — anh em của nút chọn ô, không lồng trong nó."""
    return f"document.querySelector('[data-o-khung={json.dumps(id_)}] [data-hanh-dong]')"


# Đếm lời gọi cài trong sessionStorage: trang tự chuyển sang Studio sau khi cài, biến trên window mất theo trang.
DEM_CAI = ("(() => { const f = window.fetch; window.fetch = (u, o) => { if (String(u).includes('api/nguon-git/cai/'))"
           " sessionStorage.demCai = (+sessionStorage.demCai || 0) + 1; return f(u, o); }; })()")
# Ghi mọi lời gọi /v1 của trang (đặt SAU khi trang dựng xong).
DEM_V1 = ("(() => { window.__v1 = []; const f = window.fetch; window.fetch = (u, o) => {"
          " if (/(^|\\/)v1\\//.test(String(u))) window.__v1.push(String(u)); return f(u, o); }; })()")
CTRL_ENTER = ("document.getElementById('o-soan').dispatchEvent(new KeyboardEvent('keydown',"
              " {key: 'Enter', ctrlKey: true, bubbles: true}))")


def test_cai_xong_vao_studio_rong_roi_them_khoa_o_he_thong_ai(trang, tmp_path, monkeypatch):
    git = {"ai-gia": module_ai(), "mang-gia": module_mang()}
    with loi_that(tmp_path, monkeypatch, git=git) as w:
        trang.di(w.url + "/He-thong.dc.html")
        assert trang.js("!!document.getElementById('goi-y-khoa-git')")  # chưa khoá: ô git chưa hiện, có lời nhắc
        luu_khoa(trang)
        trang.cho(o("ai-gia"), han=15, mo_ta="ô ai-gia (Chưa cài) từ git")
        assert trang.js(o("ai-gia") + ".dataset.tt") == "chua-cai"
        # Khu mặc định là loại có loai_thu_tu nhỏ nhất (AI); ô loại «mang» chỉ hiện khi chọn chip của nó.
        assert trang.js("document.querySelector('[data-loc-loai=\"ai\"]').getAttribute('aria-pressed')") == "true"
        assert not trang.js("!!" + o("mang-gia"))
        trang.bam("document.querySelector('[data-loc-loai=\"mang\"]')")
        trang.cho(o("mang-gia"), mo_ta="ô mang-gia dưới chip Mạng")
        assert trang.chu("[data-khu='mang'] .khu-ten").startswith("Mạng")
        trang.bam("document.querySelector('[data-loc-loai=\"ai\"]')")

        # Bảng chi tiết: «Cần gì để chạy» TRƯỚC khi cài.
        trang.bam(o("ai-gia"))
        trang.cho("document.getElementById('can-gi')", mo_ta="bảng chi tiết")
        can = trang.chu("#can-gi")
        assert "64 MB" in can and "chưa đo" in can and "Không cần" in can and "gia.example" in can
        assert "Dung lượng tải" in can and "KB" in can
        assert "Không cần cài gì thêm" in trang.chu("#huong-dan")
        assert trang.chu("#nut-cai") == "Cài"
        trang.js("document.getElementById('chi-tiet').dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}))")
        trang.cho("!document.getElementById('chi-tiet')", mo_ta="đóng bảng chi tiết")

        # Nút «Cài» NGAY TRÊN Ô: không lồng trong nút chọn ô; bấm hai lần liền → MỘT lần cài, không mở bảng chi tiết.
        assert trang.chu("[data-o-khung='ai-gia'] [data-hanh-dong]") == "Cài"
        assert trang.js(nut_o("ai-gia") + ".dataset.hanhDong") == "cai"
        assert trang.js(nut_o("ai-gia") + ".closest('button[data-o]') === null && " + o("ai-gia") + ".querySelector('button, a') === null")
        trang.js(DEM_CAI)
        trang.js(f"(() => {{ const b = {nut_o('ai-gia')}; b.click(); b.click(); }})()")
        assert not trang.js("!!document.getElementById('chi-tiet')")
        assert trang.js(o("ai-gia") + ".getAttribute('aria-pressed')") == "false"

        # Cài xong (module khai chat) → tự sang Studio, kèm dải thông báo dẫn tới chỗ thêm khoá.
        trang.cho("location.pathname.endsWith('/Studio.dc.html') && document.querySelector('x-dc')?.dataset.dc === 'san-sang'"
                  " && !!document.getElementById('da-cai')", han=20, mo_ta="tự sang Studio sau khi cài")
        assert trang.js("sessionStorage.demCai") == "1"
        assert "da_cai" not in trang.js("location.search")          # tải lại trang không hiện lại dải thông báo
        bao = trang.chu("#da-cai")
        assert "Đã cài AI giả" in bao and "Hệ thống → AI" in bao, bao
        assert trang.js("document.querySelector('#da-cai a').getAttribute('href')").endswith("He-thong-AI.dc.html#tk-ai-gia")

        # Studio ở trạng thái rỗng thân thiện — KHÔNG ẩn: chế độ theo khả năng module đã cài khai.
        rong = trang.chu("#studio-chua-khoa")
        assert "Chưa có khoá cho «AI giả»" in rong and "Hệ thống → AI → Tài khoản" in rong, rong
        assert trang.js("[...document.querySelectorAll('[data-che-do]')].map((b) => b.dataset.cheDo)") == ["chat", "anh", "video"]
        assert "studio" in nav(trang)
        assert trang.js("document.querySelectorAll('#chon-model option').length") == 0
        assert "chưa có khoá" in trang.chu("#model-chua-khoa")
        assert "Combo" not in trang.js("document.body.innerText")

        # Gửi khi chưa có khoá: chặn ở giao diện với cùng lời nhắn, KHÔNG gọi /v1 (nút Gửi lẫn Ctrl+Enter).
        trang.js(DEM_V1)
        trang.go("#o-soan", "Chào")
        trang.bam("document.getElementById('nut-gui')")
        trang.cho("document.getElementById('loi-studio')", mo_ta="lời nhắn chặn gửi")
        assert "Chưa có khoá cho «AI giả»" in trang.chu("#loi-studio")
        trang.bam("document.querySelector('[data-che-do=\"anh\"]')")
        trang.cho("!document.getElementById('loi-studio')", mo_ta="đổi chế độ xoá lời nhắn")
        assert "Chưa có khoá cho «AI giả»" in trang.chu("#studio-chua-khoa")
        trang.js(CTRL_ENTER)
        trang.cho("document.getElementById('loi-studio')", mo_ta="Ctrl+Enter cũng bị chặn")
        assert trang.js("window.__v1") == []
        # Điện thoại 390px: không tràn ngang ở trạng thái rỗng.
        trang.khung(390, 844, True)
        assert khong_tran(trang)
        trang.khung(1280, 800)

        # Bấm tới Hệ thống → AI → Tài khoản, ĐÚNG thẻ của module.
        trang.bam("document.querySelector('#studio-chua-khoa a[data-them-khoa=\"ai-gia\"]')")
        trang.cho("document.querySelector('x-dc')?.dataset.dc === 'san-sang' && location.pathname.endsWith('/He-thong-AI.dc.html') && !!document.querySelector('[data-tai-khoan=\"ai-gia\"] form')",
                  han=15, mo_ta="Hệ thống → AI → Tài khoản")
        assert trang.js("[...document.querySelectorAll('[data-tab]')].map((b) => b.dataset.tab)") == ["tai_khoan"]
        assert "Chưa có khoá" in trang.chu("[data-tai-khoan='ai-gia'] .chua-khoa")
        trang.cho("document.activeElement?.closest('[data-tai-khoan]')?.dataset.taiKhoan === 'ai-gia'", mo_ta="focus vào thẻ module")
        trang.js("window.__khongTaiLai = 1")
        trang.go("[data-tai-khoan='ai-gia'] [data-truong='api_keys'] textarea", K_TOT)
        trang.bam("document.querySelector('[data-tai-khoan=\"ai-gia\"] .nut-luu-cau-hinh')")
        # Lưu khoá xong: Provider hiện, chú thích «chưa có khoá» mất — không tải lại trang.
        trang.cho("document.querySelector('[data-tab=\"provider\"]')", han=15, mo_ta="Provider hiện sau khi lưu khoá")
        trang.cho("!document.querySelector('[data-tai-khoan=\"ai-gia\"] .chua-khoa')", mo_ta="hết «chưa có khoá»")
        assert trang.js("window.__khongTaiLai") == 1
        assert "Combo" not in trang.js("document.body.innerText")
        assert K_TOT not in trang.js("document.documentElement.outerHTML")
        assert trang.js("document.querySelector('[data-tai-khoan=\"ai-gia\"] a.mo-trang-chinh').getAttribute('href')").endswith("Studio.dc.html")

        # Quay lại Studio: hết trạng thái rỗng, chat stream như cũ.
        trang.bam("document.querySelector('[data-nav=\"quay-lai\"]')")
        trang.cho("document.querySelector('x-dc')?.dataset.dc === 'san-sang' && location.pathname.endsWith('/Studio.dc.html') && document.querySelectorAll('#chon-model option').length > 0",
                  han=15, mo_ta="Studio có model")
        assert not trang.js("!!document.getElementById('studio-chua-khoa') || !!document.getElementById('da-cai')")
        trang.go("#o-soan", "Chào")
        trang.bam("document.getElementById('nut-gui')")
        trang.cho("document.getElementById('trang-thai')?.innerText === 'Xong.'", han=10, mo_ta="chat xong")
        assert trang.chu("[data-vai='assistant']") == "Xin chào, tôi là model giả."

        # Module loại LẠ (`mang`) không ứng tab chính nào: cài xong (bằng nút trên ô) ở lại trang Module.
        trang.di(w.url + "/He-thong.dc.html")
        trang.bam("document.querySelector('[data-loc-loai=\"mang\"]')")
        trang.cho(nut_o("mang-gia"), mo_ta="nút Cài trên ô mang-gia")
        trang.js("window.__oLai = 1")
        trang.bam(nut_o("mang-gia"))
        trang.cho(o("mang-gia") + "?.dataset.tt === 'dang-chay'", han=15, mo_ta="mang-gia cài xong")
        time.sleep(0.5)
        assert trang.js("window.__oLai") == 1 and trang.js("location.pathname").endswith("/He-thong.dc.html")
        assert trang.js("!!document.getElementById('cai-tin')")       # bảng chi tiết mở, báo đã cài


def test_bang_chi_tiet_them_khoa_base_url_bot_khoa_va_go(trang, tmp_path, monkeypatch):
    """Form khoá trong bảng chi tiết (module cài sẵn qua API, chưa khoá): bắt buộc, base_url không bị ghi đè,
    thêm/bớt từng khoá, gỡ → tab biến mất. Studio có từ lúc CÀI (chủ máy 11/10), không đợi liên kết."""
    with loi_that(tmp_path, monkeypatch, git={"ai-gia": module_ai()}) as w:
        assert httpx.post(f"{w.url}/api/nguon-git/cai/ai-gia", headers=QT, timeout=30).status_code == 200
        trang.di(w.url + "/He-thong.dc.html")
        luu_khoa(trang)
        trang.cho(o("ai-gia") + "?.dataset.tt === 'cho-khoa'", han=15, mo_ta="ô «Đã cài — chờ khoá»")
        # Nút trên ô «Thêm khoá»: module mang mã của tab Tài khoản → dẫn tới Hệ thống → AI → Tài khoản đúng module.
        assert trang.js(nut_o("ai-gia") + ".dataset.hanhDong") == "them-khoa"
        assert trang.js(nut_o("ai-gia") + ".getAttribute('href')").endswith("He-thong-AI.dc.html#tk-ai-gia")
        trang.js("window.__khongTaiLai = 1")
        trang.bam(o("ai-gia"))
        trang.cho("document.querySelector('#chi-tiet form.form-cau-hinh')", han=15, mo_ta="bước Thêm tài khoản/khoá API")
        assert trang.chu("#ct-trang-thai") == "Đã cài — chờ khoá"
        assert trang.chu("#moi-nhat") == "Đã là bản mới nhất trên git."   # A0: da_cai_dung
        assert not trang.js("!!document.getElementById('nut-cai')")         # đã cài: không còn «Cài»
        assert "Bước tiếp theo" in trang.js("document.getElementById('h-lien-ket').textContent")
        # CŨ: «AI hiện, Studio chưa (chưa liên kết)». MỚI: đã cài module khai chat → Studio có ngay («Quay lại Studio»).
        assert "ai" in nav(trang)
        assert trang.js("document.querySelector('[data-nav=\"quay-lai\"]')?.getAttribute('href')").endswith("Studio.dc.html")
        assert trang.js("!!document.getElementById('ket-noi')")             # Kết nối vẫn hiện tới liên kết đầu tiên

        # Trường bắt buộc còn trống → không gửi, báo thiếu ngay trên form.
        trang.bam("document.getElementById('nut-luu-cau-hinh')")
        trang.cho("document.querySelector('#chi-tiet .loi-form')", mo_ta="báo thiếu trường bắt buộc")
        assert "Khoá API" in trang.chu("#chi-tiet .loi-form")
        trang.go("#chi-tiet [data-truong='api_keys'] textarea", f"{K_TOT}\n{K_HONG}")
        trang.js("const i = document.querySelector('#chi-tiet [data-truong=\"base_url\"] input');"
                 "i.focus(); i.value = ''; i.dispatchEvent(new Event('input', {bubbles: true}))")
        trang.go("#chi-tiet [data-truong='base_url'] input", "https://proxy-cua-toi.example/v1")
        trang.bam("document.getElementById('nut-luu-cau-hinh')")
        trang.cho("document.getElementById('kq-ok')", han=15, mo_ta="liên kết thành công")
        dong = trang.js("[...document.querySelectorAll('#chi-tiet .ket-qua-cai li')].map((l) => l.className)")
        assert dong == ["kq-tot", "kq-hong"], dong
        # Liên kết đầu tiên: khối Kết nối ẩn, ô «Đang chạy», trang không tải lại.
        trang.cho("!document.getElementById('ket-noi')", mo_ta="Kết nối ẩn sau liên kết đầu tiên")
        assert trang.js("window.__khongTaiLai") == 1
        assert trang.chu("#ct-trang-thai") == "Đang chạy"
        assert not trang.js("!!" + nut_o("ai-gia"))                           # đang chạy, mới nhất: không nút trên ô

        luu = json.loads((tmp_path / "bi_mat" / "ai-gia.json").read_text())
        assert luu["api_keys"] == [K_TOT]
        assert json.loads((tmp_path / "modules.json").read_text())["modules"][0]["cau_hinh_cong_khai"]["base_url"] \
            == "https://proxy-cua-toi.example/v1"
        html = trang.js("document.documentElement.outerHTML")
        assert K_TOT not in html and K_HONG not in html
        assert not any(K_TOT in s or K_HONG in s for s in trang.state_thanh_phan())

        # Thêm một khoá, ĐỂ NGUYÊN ô base_url: base_url đã đặt KHÔNG bị gửi đè bằng mặc định (lỗi cũ của form).
        trang.cho("document.querySelectorAll('#chi-tiet [data-khoa-luu]').length === 1", mo_ta="khoá đã lưu dạng che")
        k3 = "AIzaSyTOT-ba--000000000000000000000003"
        trang.go("#chi-tiet [data-truong='api_keys'] textarea", k3)
        trang.bam("document.getElementById('nut-luu-cau-hinh')")
        trang.cho("document.querySelectorAll('#chi-tiet [data-khoa-luu]').length === 2", han=15, mo_ta="hai khoá")
        assert json.loads((tmp_path / "bi_mat" / "ai-gia.json").read_text())["api_keys"] == [K_TOT, k3]
        assert json.loads((tmp_path / "modules.json").read_text())["modules"][0]["cau_hinh_cong_khai"]["base_url"] \
            == "https://proxy-cua-toi.example/v1"
        # Bớt TỪNG khoá: bấm «Bớt» ở khoá đầu rồi lưu.
        trang.bam("document.querySelector('#chi-tiet [data-khoa-luu=\"0\"] button')")
        trang.bam("document.getElementById('nut-luu-cau-hinh')")
        trang.cho("document.querySelectorAll('#chi-tiet [data-khoa-luu]').length === 1", han=15, mo_ta="còn một khoá")
        assert json.loads((tmp_path / "bi_mat" / "ai-gia.json").read_text())["api_keys"] == [k3]

        # Gỡ → tab do nó mang lại biến mất (tính lại điều kiện), vẫn không tải lại trang.
        trang.bam("document.getElementById('nut-go')")
        trang.bam("document.getElementById('nut-go-han')")
        trang.cho("!document.querySelector('[data-nav=\"quay-lai\"]') && !document.querySelector('[data-nav=\"ai\"]')",
                  han=15, mo_ta="Studio và AI biến mất sau khi gỡ")
        assert trang.js("window.__khongTaiLai") == 1
        trang.cho(o("ai-gia") + "?.dataset.tt === 'chua-cai'", han=15, mo_ta="còn trên git → lại «Chưa cài»")
        assert trang.js(nut_o("ai-gia") + ".dataset.hanhDong") == "cai"


def test_them_khoa_tren_o_module_khong_co_tab_tai_khoan_mo_bang_chi_tiet(trang, tmp_path, monkeypatch):
    """Module cần khoá nhưng KHÔNG mang mã của tab Tài khoản (loại «app»): nút «Thêm khoá» trên ô mở bảng chi
    tiết ngay ở bước thêm khoá, một lần bấm, không rời trang."""
    tep = module_ai("app-khoa", "App cần khoá", "appk", ())
    m = json.loads(tep["manifest.json"])
    m.update(loai="app", loai_ten="App", loai_thu_tu=20)
    tep["manifest.json"] = json.dumps(m, ensure_ascii=False).encode()
    with loi_that(tmp_path, monkeypatch, modules={"app-khoa": tep}) as w:
        trang.di(w.url + "/He-thong.dc.html")
        luu_khoa(trang)
        trang.cho(nut_o("app-khoa"), han=15, mo_ta="nút trên ô")
        assert trang.js(nut_o("app-khoa") + ".tagName") == "BUTTON"
        assert trang.chu("[data-o-khung='app-khoa'] [data-hanh-dong]") == "Thêm khoá"
        trang.js("window.__oLai = 1")
        trang.bam(nut_o("app-khoa"))
        trang.cho("document.activeElement?.closest('#lien-ket') && document.activeElement.tagName === 'TEXTAREA'",
                  han=10, mo_ta="focus vào ô khoá trong bảng chi tiết")
        assert trang.js("window.__oLai") == 1
        assert trang.js(o("app-khoa") + ".getAttribute('aria-pressed')") == "true"
        assert trang.js("document.querySelectorAll('#chi-tiet').length") == 1


# ───────────── Hệ thống → AI ─────────────

def test_ai_da_cai_chua_khoa_chi_co_tai_khoan(trang, tmp_path, monkeypatch):
    mods = {"ai-gia": module_ai(), "ai-hai": module_ai("ai-hai", "AI thứ hai", "hai", ("chat",))}
    with loi_that(tmp_path, monkeypatch, modules=mods) as w:
        lien_ket(w, "ai-hai", {"api_keys": [K_TOT]})
        trang.di(w.url + "/He-thong.dc.html")
        luu_khoa(trang)
        trang.cho("document.querySelector('[data-nav=\"ai\"]')", mo_ta="tab AI")
        # CŨ: «chưa liên kết → chưa có Studio». MỚI (chủ máy 11/10): đã cài module khai chat → Studio có.
        assert trang.js("document.querySelector('[data-nav=\"quay-lai\"]')?.getAttribute('href')").endswith("Studio.dc.html")
        trang.di(w.url + "/He-thong-AI.dc.html")
        trang.cho("document.querySelector('[data-tab=\"tai_khoan\"]')", mo_ta="tab Tài khoản")
        tabs = trang.js("[...document.querySelectorAll('[data-tab]')].map((b) => b.dataset.tab)")
        assert tabs == ["tai_khoan", "provider"], tabs        # KHÔNG Combo
        trang.cho("document.querySelector('[data-tai-khoan=\"ai-gia\"] form')", mo_ta="form thêm khoá")
        # Module CHƯA liên kết đứng ĐẦU tab, có chú thích «chưa có khoá»; module đã liên kết thì không.
        assert trang.js("[...document.querySelectorAll('[data-tai-khoan]')].map((a) => a.dataset.taiKhoan)") == ["ai-gia", "ai-hai"]
        assert "Chưa có khoá" in trang.chu("[data-tai-khoan='ai-gia'] .chua-khoa")
        assert not trang.js("!!document.querySelector('[data-tai-khoan=\"ai-hai\"] .chua-khoa')")


def test_ai_chi_cai_chua_khoa_khong_provider(trang, tmp_path, monkeypatch):
    with loi_that(tmp_path, monkeypatch, modules={"ai-gia": module_ai()}) as w:
        trang.di(w.url + "/He-thong.dc.html")
        luu_khoa(trang)
        trang.di(w.url + "/He-thong-AI.dc.html")
        trang.cho("document.querySelector('[data-tai-khoan=\"ai-gia\"] form')", mo_ta="form thêm khoá")
        tabs = trang.js("[...document.querySelectorAll('[data-tab]')].map((b) => b.dataset.tab)")
        assert tabs == ["tai_khoan"], tabs                   # KHÔNG Provider, KHÔNG Combo
        # Gõ tay tab Provider khi chưa đủ → về Module kèm lời giải thích.
        trang.js("window.__trangCu = 1")
        trang.cdp.goi("Page.navigate", {"url": w.url + "/He-thong-AI.dc.html#provider"}, trang.s)
        trang.cho("!window.__trangCu && document.querySelector('x-dc')?.dataset.dc === 'san-sang' && location.pathname.endsWith('/He-thong.dc.html') && !!document.getElementById('thieu')",
                  mo_ta="chuyển")
        assert "Provider" in trang.chu("#thieu")


def test_provider_model_that_suc_khoe_va_khong_combo(trang, tmp_path, monkeypatch):
    mods = {"ai-gia": module_ai(), "ai-hai": module_ai("ai-hai", "AI thứ hai", "hai", ("chat",))}
    with loi_that(tmp_path, monkeypatch, modules=mods) as w:
        lien_ket(w, "ai-gia", {"api_keys": [K_TOT]})
        trang.di(w.url + "/He-thong.dc.html")
        luu_khoa(trang)
        trang.di(w.url + "/He-thong-AI.dc.html#provider")
        trang.cho("document.querySelector('[data-provider=\"ai-gia\"] .suc-khoe')", han=15, mo_ta="sức khoẻ ai-gia")
        tabs = trang.js("[...document.querySelectorAll('[data-tab]')].map((b) => b.dataset.tab)")
        assert tabs == ["tai_khoan", "provider"], tabs
        the = trang.chu("[data-provider='ai-gia']")
        assert "gia/nhanh" in the and "gia/phim-1" in the and "video" in the
        assert "anh: bị từ chối (HTTP 403)" in the and "video: chưa rõ" in the, the
        assert not trang.js("window.__xss")                 # id model chứa HTML hiện như chữ
        # Liên kết nguồn AI thứ hai: đủ «≥ 2 AI» nhưng Combo chưa có backend → vẫn KHÔNG có trong DOM.
        lien_ket(w, "ai-hai", {"api_keys": [K_TOT]})
        trang.di(w.url + "/He-thong-AI.dc.html?lan=2#provider")  # đổi query để trình duyệt tải lại thật
        trang.cho("document.querySelector('[data-provider=\"ai-hai\"]')", han=15, mo_ta="provider thứ hai")
        assert "Combo" not in trang.js("document.body.innerText")
        assert trang.js("document.querySelectorAll('[data-tab=\"combo\"]').length") == 0


# ───────────── Studio ─────────────

def _mo_studio(trang, w):
    trang.di(w.url + "/Studio.dc.html")
    luu_khoa(trang)
    trang.cho("document.querySelectorAll('#chon-model option').length > 0", han=15, mo_ta="danh sách model")


def test_studio_chat_stream_dung_va_don_khuc(trang, tmp_path, monkeypatch):
    with loi_that(tmp_path, monkeypatch, modules={"ai-gia": module_ai()}) as w:
        lien_ket(w, "ai-gia", {"api_keys": [K_TOT]})
        _mo_studio(trang, w)
        assert trang.js("[...document.querySelectorAll('[data-che-do]')].map((b) => b.dataset.cheDo)") == ["chat", "anh", "video"]
        assert "studio" in nav(trang) and "he-thong" in nav(trang)
        models = trang.js("[...document.querySelectorAll('#chon-model option')].map((x) => x.value)")
        assert "gia/nhanh" in models and "gia/phim-1" not in models   # model khai kha_nang=["video"] không vào chat
        assert not trang.js("!!document.getElementById('canh-bao-tien')")

        monkeypatch.setenv("GIA_TRE", "0.15")
        trang.go("#o-soan", "Chào")
        trang.bam("document.getElementById('nut-gui')")
        # Chữ hiện DẦN: thấy một phần trước khi xong.
        trang.cho("(document.querySelector('[data-vai=\"assistant\"]')?.innerText || '').startsWith('Xin')", mo_ta="khúc đầu")
        assert trang.chu("#trang-thai") != "Xong."
        trang.cho("document.getElementById('trang-thai')?.innerText === 'Xong.'", han=10, mo_ta="xong")
        assert trang.chu("[data-vai='assistant']") == "Xin chào, tôi là model giả."

        # Dừng giữa chừng: giữ phần đã nhận.
        monkeypatch.setenv("GIA_TRE", "0.4")
        trang.go("#o-soan", "Lần hai")
        trang.bam("document.getElementById('nut-gui')")
        trang.cho("document.querySelectorAll('[data-vai=\"assistant\"]')[1]?.innerText.startsWith('Xin')", mo_ta="khúc đầu lần hai")
        trang.bam("document.getElementById('nut-dung')")
        trang.cho("document.getElementById('trang-thai')?.innerText.includes('Đã dừng')", mo_ta="đã dừng")
        assert trang.js("document.querySelectorAll('[data-vai=\"assistant\"]')[1].innerText") != "Xin chào, tôi là model giả."

        # 80 khúc trong MỘT lần ghi: không vướng chốt «quá 50 lần dựng» của dc-lite.
        monkeypatch.setenv("GIA_KHUC", json.dumps([f"k{i} " for i in range(80)]))
        monkeypatch.setenv("GIA_DON", "1")
        trang.bam("document.getElementById('nut-xoa')")
        trang.go("#o-soan", "Nhiều khúc")
        trang.bam("document.getElementById('nut-gui')")
        trang.cho("document.getElementById('trang-thai')?.innerText === 'Xong.'", han=10, mo_ta="xong 80 khúc")
        assert trang.js("document.querySelector('[data-vai=\"assistant\"]').textContent") == "".join(f"k{i} " for i in range(80))
        assert not trang.js("!!document.getElementById('dc-loi')")


def test_studio_anh_video_va_khoa_mien_phi(trang, tmp_path, monkeypatch):
    with loi_that(tmp_path, monkeypatch, modules={"ai-gia": module_ai()}) as w:
        lien_ket(w, "ai-gia", {"api_keys": [K_TOT]})
        _mo_studio(trang, w)
        trang.bam("document.querySelector('[data-che-do=\"anh\"]')")
        trang.cho("document.getElementById('canh-bao-tien')", mo_ta="cảnh báo tốn tiền")
        assert "miễn phí" in trang.chu("#canh-bao-tien")
        trang.go("#o-soan", "một chú mèo")
        trang.bam("document.getElementById('nut-gui')")
        trang.cho("document.querySelector('#ket-qua-anh img')?.complete && document.querySelector('#ket-qua-anh img').naturalWidth === 1",
                  han=10, mo_ta="ảnh hiển thị được")
        assert trang.js("document.querySelector('#ket-qua-anh a[download]').getAttribute('href').startsWith('data:image/png;base64,')")

        # 403 no_key_permitted (khoá hạng miễn phí): lời nhắn thân thiện + giữ NGUYÊN VĂN lỗi máy chủ.
        monkeypatch.setenv("GIA_ANH", "403")
        trang.bam("document.getElementById('nut-gui')")
        trang.cho("document.getElementById('loi-studio')", han=10, mo_ta="lỗi 403")
        loi = trang.chu("#loi-studio")
        assert "hạng miễn phí" in loi and "Không khoá nào được phép tạo ảnh (giả, như khoá hạng miễn phí)." in loi, loi

        trang.bam("document.querySelector('[data-che-do=\"video\"]')")
        assert trang.js("[...document.querySelectorAll('#chon-model option')].map((x) => x.value)") == ["gia/phim-1"]
        trang.go("#o-soan", "sóng biển")
        trang.bam("document.getElementById('nut-gui')")
        trang.cho("document.querySelector('#video-kq video')?.src.startsWith('blob:')", han=20, mo_ta="video tải về thành blob")
        co = trang.js("fetch(document.getElementById('tai-video').href).then((r) => r.blob()).then((b) => b.size)")
        assert co == len(b"VIDEO-GIA-") * 100
        assert trang.js("document.getElementById('tai-video').getAttribute('download')").endswith(".mp4")


def test_studio_chi_hien_che_do_module_khai(trang, tmp_path, monkeypatch):
    with loi_that(tmp_path, monkeypatch, modules={"ai-hai": module_ai("ai-hai", "AI chỉ chat", "hai", ("chat",))}) as w:
        lien_ket(w, "ai-hai", {"api_keys": [K_TOT]})
        _mo_studio(trang, w)
        assert trang.js("[...document.querySelectorAll('[data-che-do]')].map((b) => b.dataset.cheDo)") == ["chat"]


# ───────────── loại lạ, mục `can`, một hàm đánh giá ─────────────

def test_loai_la_tab_module_va_muc_can_cung_lat_voi_khung(trang, tmp_path, monkeypatch):
    with loi_that(tmp_path, monkeypatch, modules={"mang-gia": module_mang()}) as w:
        lien_ket(w, "mang-gia")
        trang.di(w.url + "/He-thong.dc.html#module-mang-gia")
        trang.cho("document.getElementById('muc-module')", mo_ta="mục của module")
        # Tab riêng của module loại lạ: «Quay lại» trỏ tab đó (chưa có Studio), đường tương đối /m/<id>/.
        ql = trang.js("document.querySelector('[data-nav=\"quay-lai\"]')?.getAttribute('href')")
        assert ql and ql.endswith("/m/mang-gia/index.html"), ql
        assert trang.js("[...document.querySelectorAll('#muc-module [data-muc]')].map((x) => x.dataset.muc)") == ["bang"]
        assert "«Tự động điều chỉnh» chưa mở" in trang.chu("#muc-module") and "chat" in trang.chu("#muc-module")
        assert not trang.js("!!document.querySelector('[data-muc=\"tu-dong\"]')")
        # Cài (CHƯA liên kết) một module chat: tab «chính» Studio của khung hiện ngay (phạm vi «đã cài»), còn mục
        # `can: ["chat"]` của module vẫn đợi chat ĐÃ LIÊN KẾT — cùng một hàm `thieu`, khác phạm vi khai trong điều kiện.
        luu_khoa(trang)
        ghi_thu_muc(tmp_path / "modules" / "ai-gia", module_ai())
        assert httpx.post(f"{w.url}/api/modules/nap-lai", headers=QT, timeout=30).status_code == 200
        trang.di(w.url + "/He-thong.dc.html?lan=2#module-mang-gia")
        trang.cho("document.getElementById('muc-module')", mo_ta="mục của module (lần 2)")
        assert trang.js("document.querySelector('[data-nav=\"quay-lai\"]').getAttribute('href')").endswith("Studio.dc.html")
        assert trang.js("[...document.querySelectorAll('#muc-module [data-muc]')].map((x) => x.dataset.muc)") == ["bang"]
        # Liên kết module chat → mục tu-dong mở.
        lien_ket(w, "ai-gia", {"api_keys": [K_TOT]})
        trang.di(w.url + "/He-thong.dc.html?lan=3#module-mang-gia")
        trang.cho("document.querySelectorAll('#muc-module [data-muc]').length === 2", mo_ta="mục tu-dong mở")
        trang.di(w.url + "/Studio.dc.html")
        assert nav(trang)[:2] == ["studio", "m-mang-gia"]


def test_chi_tiet_khong_chen_html_va_401(trang, tmp_path, monkeypatch):
    mods = {"xss-gia": module_khong_khoa("xss-gia", XSS_TEN, "ai", "xss", mo_ta="<b>đậm</b><script>window.__xss=6</script>")}
    with loi_that(tmp_path, monkeypatch, modules=mods) as w:
        trang.di(w.url + "/He-thong.dc.html")
        trang.cho(o("xss-gia"), mo_ta="ô xss")
        trang.bam(o("xss-gia"))
        trang.cho("document.getElementById('chi-tiet-ten')", mo_ta="chi tiết")
        assert trang.chu("#chi-tiet-ten") == XSS_TEN
        assert not trang.js("window.__xss") and not trang.js("!!document.querySelector('#chi-tiet img, #chi-tiet b')")
        # Esc đóng bảng chi tiết, focus về ô.
        trang.js("document.getElementById('chi-tiet').dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}))")
        trang.cho("!document.getElementById('chi-tiet') && document.activeElement?.dataset.o === 'xss-gia'", mo_ta="đóng + focus")
        # Khoá sai → báo ngay ở khối khoá.
        luu_khoa(trang, "khoa-sai-0000000000000000")
        trang.cho("document.getElementById('khoa-loi')", han=10, mo_ta="báo 401")
        assert "401" in trang.chu("#khoa-loi")


# ───────────── điện thoại 390px, ô bằng nhau ─────────────

@pytest.mark.parametrize("rong,cao,dt", [(390, 844, True), (1280, 800, False)])
def test_khong_tran_ngang_va_o_cao_bang_nhau(trang, tmp_path, monkeypatch, rong, cao, dt):
    mods = {"ai-gia": module_ai(), "mang-gia": module_mang(),
            "app-gia": module_khong_khoa("app-gia", "Ghi chú giả có tên dài để xuống dòng thử độ cao ô", "app", "ghichu",
                                         loai_ten="App", loai_thu_tu=20),
            "app-hai": module_khong_khoa("app-hai", "Lịch", "app", "lich", loai_ten="App", loai_thu_tu=20, mo_ta="Ngắn.")}
    with loi_that(tmp_path, monkeypatch, modules=mods) as w:
        lien_ket(w, "ai-gia", {"api_keys": [K_TOT]})
        trang.khung(rong, cao, dt)
        trang.di(w.url + "/He-thong.dc.html")
        trang.bam("document.querySelector('[data-loc-loai=\"tat-ca\"]')")
        trang.cho("document.querySelectorAll('[data-o]').length === 4", mo_ta="4 ô")
        assert khong_tran(trang)
        # Ô là khung chứa nút chọn ô + nút hành động anh em (không lồng button trong button) — đo cả khung ô.
        cao_o = trang.js("[...document.querySelectorAll('[data-khu=\"app\"] .luoi-o > .o-mod')].map((b) => Math.round(b.getBoundingClientRect().height))")
        assert len(set(cao_o)) == 1, cao_o
        trang.bam(o("ai-gia"))
        trang.cho("document.getElementById('chi-tiet')", mo_ta="chi tiết")
        assert khong_tran(trang)
        if dt:  # tờ trượt từ dưới lên
            assert trang.js("getComputedStyle(document.getElementById('chi-tiet')).position") == "fixed"
        for duong in ("/He-thong-AI.dc.html", "/Studio.dc.html"):
            trang.di(w.url + duong)
            assert khong_tran(trang), duong
        # Nút đủ lớn để bấm bằng ngón tay (≥ 40 px) ở các nút chính.
        trang.di(w.url + "/He-thong.dc.html")
        thap = trang.js("[...document.querySelectorAll('button.nut, button.chip')].filter((b) => b.offsetParent)"
                        ".map((b) => b.getBoundingClientRect().height).filter((h) => h < 40)")
        assert not thap, thap


# ───────────── «Cập nhật» (A0) và «＋ Thêm module» ─────────────

def test_ban_moi_tren_git_hien_cap_nhat(trang, tmp_path, monkeypatch):
    with loi_that(tmp_path, monkeypatch, git={"ai-gia": module_ai()}) as w:
        r = httpx.post(f"{w.url}/api/nguon-git/cai/ai-gia", headers=QT, timeout=30)
        assert r.status_code == 200, r.text
        tep = module_ai()
        m = json.loads(tep["manifest.json"])
        m["phien_ban"] = "1.1.0"
        tep["manifest.json"] = json.dumps(m, ensure_ascii=False).encode()
        w.gh.day(tep_kho({"ai-gia": tep}), thong_diep="ai-gia 1.1.0")
        assert httpx.put(f"{w.url}/api/nguon-git", headers=QT, json={}).status_code == 200   # thăm dò ngay
        trang.di(w.url + "/He-thong.dc.html")
        luu_khoa(trang)
        trang.cho(o("ai-gia") + "?.dataset.tt === 'ban-moi'", han=15, mo_ta="ô «Có bản mới»")
        trang.bam(o("ai-gia"))
        trang.cho("document.getElementById('nut-cai')", mo_ta="nút")
        assert trang.chu("#nut-cai") == "Cập nhật"
        assert "1.0.0 → 1.1.0" in trang.chu("#ct-trang-thai")
        trang.bam("document.getElementById('nut-cai')")
        trang.cho("(document.getElementById('cai-tin')?.innerText || '').startsWith('Đã cập nhật bản 1.1.0')", han=15,
                  mo_ta="đã cập nhật")
        trang.cho("document.getElementById('moi-nhat')", mo_ta="bản mới nhất")
        assert trang.js(o("ai-gia") + ".dataset.tt") == "cho-khoa"


def test_them_module_tu_xa_va_nguon_git_qua_giao_dien(trang, tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    khoa_tx = "sk-lan-0000000000000000000000000000xyzw"
    o_gia = OpenAIGia(khoa=khoa_tx)
    gh = GitHubGia("chu-may/kho-khac")
    gh.day(tep_kho({"mang-gia": module_mang()}))
    with MayChuThat(o_gia.app) as s_o, MayChuThat(gh.app) as s_gh, loi_that(tmp_path, monkeypatch) as w:
        monkeypatch.setenv("WEB2API_GITHUB_API", s_gh.url)
        trang.di(w.url + "/He-thong.dc.html")
        luu_khoa(trang)
        trang.js("window.__khongTaiLai = 1")
        trang.bam("document.getElementById('o-them')")
        trang.cho("document.getElementById('tu-xa')", mo_ta="khối Từ xa")
        trang.go("#tx-id", "may-gpu")
        trang.go("#tx-ten", "Máy GPU trong nhà")
        trang.go("#tx-tien-to", "gpu")
        trang.go("#tx-url", s_o.url + "/v1")
        trang.go("#tu-xa input[type=password]", khoa_tx)
        trang.bam("document.querySelector('#tu-xa button[type=submit]')")
        # Module từ xa là dịch vụ chat chuẩn OpenAI → tính «ai» + «chat»: ô Đang chạy ở khu AI, Studio hiện ngay.
        trang.cho(o("may-gpu") + "?.dataset.tt === 'dang-chay'", han=15, mo_ta="ô module từ xa")
        assert trang.js(o("may-gpu") + ".closest('[data-khu]').dataset.khu") == "ai"
        trang.cho("document.querySelector('[data-nav=\"quay-lai\"]')?.getAttribute('href').endsWith('Studio.dc.html')",
                  mo_ta="Studio hiện")
        assert trang.js("window.__khongTaiLai") == 1
        html = trang.js("document.documentElement.outerHTML")
        assert khoa_tx not in html and not any(khoa_tx in x for x in trang.state_thanh_phan())

        # Nguồn git qua form → module trên kho đó hiện thành ô «Chưa cài».
        trang.bam("document.getElementById('o-them')")
        trang.cho("document.getElementById('ng-repo')", mo_ta="form nguồn git")
        trang.js("const i = document.getElementById('ng-repo'); i.value = ''; i.dispatchEvent(new Event('input', {bubbles: true}))")
        trang.go("#ng-repo", "chu-may/kho-khac")
        trang.bam("document.querySelector('form.form-ng button[type=submit]')")
        trang.cho("(document.querySelector('#nguon-git [role=status]')?.innerText || '').startsWith('Đã lưu')", han=10,
                  mo_ta="đã lưu nguồn git")
        trang.bam("document.querySelector('[data-loc-loai=\"tat-ca\"]')")
        trang.cho(o("mang-gia") + "?.dataset.tt === 'chua-cai'", han=15, mo_ta="ô từ kho mới")
