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
import re
import time
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest

from app import so_module
from app.main import app
from gia import GitHubGia, GoogleGia, MayChuThat, ghi_thu_muc, tep_kho
from ui_chung import CO_CHROME, dang_nhap_quan_tri, trang, trinh_duyet  # noqa: F401 — fixture
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
            monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")  # NV16: NguonGit ghim IP, máy chủ giả ở loopback
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


def nav(trang) -> list[str]:
    """data-nav của mọi liên kết điều hướng đang có trong DOM."""
    return trang.js("[...document.querySelectorAll('[data-nav]')].map((a) => a.dataset.nav)")


def o(id_: str) -> str:
    return f"document.querySelector('[data-o={json.dumps(id_)}]')"


def che_do(trang) -> list[str]:
    """Mã các nút chế độ Studio. Máy tính: «Vẽ ảnh/Tạo video» nằm trong ô soạn, «Trò chuyện» dưới ô (như demo) — thứ tự
    trong DOM khác điện thoại nên so tập, không so thứ tự."""
    return trang.js("[...document.querySelectorAll('[data-che-do]')].map((b) => b.dataset.cheDo)")


def bam_chuot(trang, expr: str, mo_ta: str = ""):
    """Bấm CHUỘT THẬT vào tâm phần tử (Input.dispatchMouseEvent): đi qua kiểm tra điểm bấm của trình duyệt, nên lớp
    nào đè lên phần tử (lớp nền trong suốt, bảng khác…) sẽ nhận cú bấm thay — element.click() thì không thấy."""
    toa = trang.js(f"(() => {{ const e = {expr}; if (!e) return null; e.scrollIntoView({{block: 'nearest'}}); const r = e.getBoundingClientRect();"
                   " const x = r.left + r.width / 2, y = r.top + r.height / 2; const tren = document.elementFromPoint(x, y);"
                   " return [x, y, !!tren && (tren === e || e.contains(tren))]; })()")
    assert toa, f"không thấy phần tử để bấm: {mo_ta or expr}"
    assert toa[2], f"phần tử bị lớp khác đè, cú bấm thật không tới: {mo_ta or expr}"
    for kieu in ("mousePressed", "mouseReleased"):
        trang.cdp.goi("Input.dispatchMouseEvent", {"type": kieu, "x": toa[0], "y": toa[1], "button": "left", "clickCount": 1}, trang.s)
    time.sleep(0.05)


def nhom_tk(trang) -> list[list[str]]:
    """Chip nhóm tài khoản ở Hệ thống → AI → Tài khoản: [[tên, số đếm], …] (như «Gmail 3 · API 4» của demo)."""
    return trang.js("[...document.querySelectorAll('[data-nhom]')].map((b) => [...b.querySelectorAll('span')].map((x) => x.textContent))")


def khong_tran(trang) -> bool:
    return trang.js("document.documentElement.scrollWidth <= window.innerWidth")


# ───────────── ca 0: chưa cài gì ─────────────

def test_ca_0_module_chi_co_module_va_go_tay_bi_chuyen(trang, tmp_path, monkeypatch):
    with loi_that(tmp_path, monkeypatch) as w:
        dang_nhap_quan_tri(trang, w.url)
        trang.cdp.goi("Page.navigate", {"url": w.url + "/"}, trang.s)
        trang.cho("location.pathname.endsWith('/He-thong.dc.html') && document.querySelector('x-dc')?.dataset.dc === 'san-sang'",
                  mo_ta="địa chỉ gốc dẫn tới Hệ thống → Module")
        trang.cho("document.getElementById('ket-noi')", mo_ta="khối Kết nối với web2api")
        assert nav(trang) == ["module", "bao_mat"]        # không Studio, không AI, không Quay lại; «Bảo mật» là tab của lõi
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
        dang_nhap_quan_tri(trang, w.url)
        trang.di(w.url + "/He-thong.dc.html")
        trang.cho(o("ai-gia"), han=15, mo_ta="ô ai-gia (Chưa cài) từ git")
        assert trang.js(o("ai-gia") + ".dataset.tt") == "chua-cai"
        # Khu mặc định là loại có loai_thu_tu nhỏ nhất (AI); ô loại «mang» chỉ hiện khi chọn chip của nó.
        assert trang.js("document.querySelector('[data-loc-loai=\"ai\"]').getAttribute('aria-pressed')") == "true"
        assert not trang.js("!!" + o("mang-gia"))
        trang.bam("document.querySelector('[data-loc-loai=\"mang\"]')")
        trang.cho(o("mang-gia"), mo_ta="ô mang-gia dưới chip Mạng")
        assert trang.js("document.querySelector(\"[data-khu='mang'] .khu-ten\").textContent").startswith("Mạng")
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

        # Nút «Cài» NGAY TRÊN Ô (biểu tượng tải về nền đen như ô chưa cài của demo Tiện ích): không lồng trong nút chọn ô;
        # bấm hai lần liền → MỘT lần cài, không mở bảng chi tiết.
        assert trang.js(nut_o("ai-gia") + ".getAttribute('aria-label')") == "Cài AI giả"
        assert "den" in trang.js(nut_o("ai-gia") + ".className")
        assert trang.js(nut_o("ai-gia") + ".dataset.hanhDong") == "cai"
        assert trang.js(nut_o("ai-gia") + ".closest('button[data-o]') === null && " + o("ai-gia") + ".querySelector('button, a') === null")
        trang.js(DEM_CAI)
        trang.js(f"(() => {{ const b = {nut_o('ai-gia')}; b.click(); b.click(); }})()")
        assert not trang.js("!!document.getElementById('chi-tiet')")
        assert trang.js(o("ai-gia") + ".getAttribute('aria-pressed')") == "false"

        # Cài xong (module khai chat) → tự sang Studio; MỘT dòng nhỏ «Chưa có khoá cho … — Thêm khoá» dẫn tới chỗ thêm
        # khoá (không dải «Đã cài …» — bản demo không có), viên trạng thái của demo chuyển vàng «Chờ khoá».
        trang.cho("location.pathname.endsWith('/Studio.dc.html') && document.querySelector('x-dc')?.dataset.dc === 'san-sang'"
                  " && !!document.getElementById('studio-chua-khoa')", han=20, mo_ta="tự sang Studio sau khi cài")
        assert trang.js("sessionStorage.demCai") == "1"
        assert "da_cai" not in trang.js("location.search")          # tham số chỉ dùng một lần, bỏ khỏi địa chỉ
        assert not trang.js("!!document.getElementById('da-cai')")
        assert trang.chu("#trang-thai-chung") == "Chờ khoá"
        assert trang.js("document.getElementById('trang-thai-chung').getAttribute('href')").endswith("He-thong-AI.dc.html#tk-ai-gia")

        # Studio ở trạng thái rỗng thân thiện — KHÔNG ẩn: chế độ theo khả năng module đã cài khai.
        rong = trang.chu("#studio-chua-khoa")
        assert "Chưa có khoá cho «AI giả»" in rong and "Thêm khoá" in rong, rong
        them = "document.querySelector('#studio-chua-khoa a[data-them-khoa=\"ai-gia\"]')"
        assert trang.js(them + ".getAttribute('href')").endswith("He-thong-AI.dc.html#tk-ai-gia")
        assert "Hệ thống → AI → Tài khoản" in trang.js(them + ".getAttribute('aria-label')")
        assert sorted(che_do(trang)) == ["anh", "chat", "video"]
        assert "studio" in nav(trang)
        assert trang.js("document.querySelectorAll('[data-model-chon]').length") == 0
        assert "chưa có khoá" in trang.js("document.getElementById('model-chua-khoa').textContent")
        assert "Combo" not in trang.js("document.body.innerText")

        # Chưa có khoá: ô soạn và nút gửi bị khoá ngay ở giao diện, KHÔNG gọi /v1 — ở mọi chế độ, cả phím tắt.
        trang.js(DEM_V1)
        assert trang.js("document.getElementById('o-soan').disabled && document.getElementById('nut-gui').disabled")
        trang.bam("document.querySelector('[data-che-do=\"anh\"]')")
        trang.cho("document.querySelector('[data-che-do=\"anh\"]').getAttribute('aria-pressed') === 'true'", mo_ta="chế độ ảnh")
        assert "Chưa có khoá cho «AI giả»" in trang.chu("#studio-chua-khoa")
        assert trang.js("document.getElementById('o-soan').disabled && document.getElementById('nut-gui').disabled")
        trang.js(CTRL_ENTER)
        trang.js("document.querySelector('form.soan').requestSubmit()")
        trang.cho("document.getElementById('loi-studio')", mo_ta="gửi cưỡng bức vẫn bị chặn bằng lời nhắn")
        assert "Chưa có khoá cho «AI giả»" in trang.chu("#loi-studio") and "Hệ thống → AI → Tài khoản" in trang.chu("#loi-studio")
        assert trang.js("window.__v1") == []
        # Điện thoại 390px: không tràn ngang ở trạng thái rỗng.
        trang.khung(390, 844, True)
        assert khong_tran(trang)
        trang.khung(1280, 800)

        # Bấm tới Hệ thống → AI → Tài khoản: biểu mẫu «Thêm API» của ĐÚNG module, focus vào ô khoá.
        trang.bam("document.querySelector('#studio-chua-khoa a[data-them-khoa=\"ai-gia\"]')")
        trang.cho("document.querySelector('x-dc')?.dataset.dc === 'san-sang' && location.pathname.endsWith('/He-thong-AI.dc.html')"
                  " && document.querySelector('#form-them')?.dataset.module === 'ai-gia'", han=15, mo_ta="Hệ thống → AI → Tài khoản › Thêm API")
        assert trang.js("document.getElementById('bang-ai').dataset.muc") == "tai_khoan"
        assert nhom_tk(trang) == [["API", "0"]]
        assert "Chưa có tài khoản API nào" in trang.chu("#tk-trong")
        trang.cho("document.activeElement?.closest('#form-them') && document.activeElement.type === 'password'", mo_ta="focus vào ô khoá")
        trang.js("window.__khongTaiLai = 1")
        trang.go("#form-them [data-truong='api_keys'] input", K_TOT)
        trang.bam("document.getElementById('nut-them-luu')")
        # Lưu khoá xong: một dòng tài khoản (dạng che), nhóm «API 1», dải báo từng khoá — không tải lại trang.
        trang.cho("document.querySelectorAll('[data-tai-khoan=\"ai-gia\"]').length === 1", han=15, mo_ta="dòng tài khoản sau khi lưu khoá")
        assert "Đã thêm khoá vào «AI giả»" in trang.chu("#tin-ai") and "…1111 dùng được" in trang.chu("#tin-ai")
        assert nhom_tk(trang) == [["API", "1"]]
        assert not trang.js("!!document.getElementById('form-them')")
        assert trang.js("window.__khongTaiLai") == 1
        assert "Combo" not in trang.js("document.body.innerText")
        assert K_TOT not in trang.js("document.documentElement.outerHTML")
        assert not any(K_TOT in x for x in trang.state_thanh_phan())

        # Quay lại Studio: hết trạng thái rỗng, chat stream như cũ.
        trang.bam("document.querySelector('[data-nav=\"quay-lai\"]')")
        trang.cho("document.querySelector('x-dc')?.dataset.dc === 'san-sang' && location.pathname.endsWith('/Studio.dc.html') && document.querySelectorAll('[data-model-chon]').length > 0",
                  han=15, mo_ta="Studio có model")
        assert not trang.js("!!document.getElementById('studio-chua-khoa')")
        assert trang.chu("#trang-thai-chung") == "Mọi dịch vụ ổn"
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
        dang_nhap_quan_tri(trang, w.url)
        trang.di(w.url + "/He-thong.dc.html")
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
        assert trang.chu("#h-lien-ket") == "CẤU HÌNH" and "Bước tiếp theo" in trang.chu("#buoc-tiep")
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
        dang_nhap_quan_tri(trang, w.url)
        trang.di(w.url + "/He-thong.dc.html")
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


# ───────────── Hệ thống → AI (chép Bo-nao.dc.html của demo: Tài khoản · Provider) ─────────────

def con_ai(trang) -> list[str]:
    """Mục con của «AI ▾» trên thanh Hệ thống (mở bảng xổ rồi đóng lại) — trang AI không có hàng tab riêng, như demo."""
    trang.bam("document.querySelector('[data-nav=\"ai\"]')")
    trang.cho("document.querySelectorAll('[data-nav-con]').length > 0", mo_ta="bảng xổ AI")
    ds = trang.js("[...document.querySelectorAll('[data-nav-con]')].map((a) => a.dataset.navCon)")
    trang.bam("document.querySelector('[data-nav=\"ai\"]')")
    return ds


# Điều khiển của demo CHƯA có backend — không được có trong DOM (ẩn, không vẽ nút chết).
KHONG_CO = ["Combo", "TOTP", "Bản mẫu minh hoạ", "«:text»",
            "ưu tiên", "hàng đợi", "Xem mã", "Thêm Gmail"]


def khong_dieu_khien_chet(trang):
    chu = trang.js("document.body.innerText")
    assert not [x for x in KHONG_CO if x in chu], [x for x in KHONG_CO if x in chu]
    assert trang.js("document.querySelectorAll('#tab-ai, [data-tab]').length") == 0


def test_ai_vua_cai_chua_khoa_co_tai_khoan_va_provider(trang, tmp_path, monkeypatch):
    """Lỗi chủ máy bắt (11/10): cài AI rồi mà không thấy «tài khoản, provider» như demo. Vừa cài, chưa khoá: Tài khoản
    có nhóm «API 0» + nút «＋ Thêm API» + khung rỗng của demo; Provider có ngay nhà cung cấp đã cài với tab Model / Tài
    khoản rỗng chỉ chỗ thêm khoá — không gọi …/suc-khoe (404 khi chưa liên kết). Thêm khoá → dòng tài khoản + model."""
    with loi_that(tmp_path, monkeypatch, modules={"ai-gia": module_ai()}) as w:
        dang_nhap_quan_tri(trang, w.url)
        trang.di(w.url + "/He-thong-AI.dc.html")
        trang.cho("!!document.getElementById('bang-ai')", mo_ta="trang AI")
        assert con_ai(trang) == ["tai_khoan", "provider"]           # Provider có từ lúc CÀI; không Combo
        assert trang.js("document.getElementById('bang-ai').dataset.muc") == "tai_khoan"
        assert nhom_tk(trang) == [["API", "0"]]
        assert trang.chu("#nut-them") == "＋ Tài khoản"
        assert trang.js("getComputedStyle(document.getElementById('nut-them')).backgroundColor") == "rgb(24, 24, 22)"  # nút đen nổi bật
        assert trang.chu("#tk-trong") == "Chưa có tài khoản API nào — bấm « ＋ » ở trên để thêm."
        assert trang.js("document.querySelectorAll('[data-tai-khoan]').length") == 0
        khong_dieu_khien_chet(trang)

        # Provider: «AI giả 0» với Model 0 | Tài khoản 0, khung rỗng + «Mở tab Tài khoản»; không gọi …/suc-khoe.
        trang.js("window.__sk = 0; const f = window.fetch; window.fetch = (u, o) => { if (String(u).includes('/suc-khoe')) window.__sk++; return f(u, o); }")
        trang.bam("document.querySelector('[data-nav=\"ai\"]')")
        bam_chuot(trang, "document.querySelector('[data-nav-con=\"provider\"]')", "Provider trong bảng xổ AI")
        trang.cho("document.getElementById('bang-ai')?.dataset.muc === 'provider' && !!document.getElementById('pv-trong')", mo_ta="Provider rỗng")
        assert trang.js("document.querySelector('[data-provider]').dataset.provider") == "ai-gia"
        assert trang.js("[...document.getElementById('chon-pv').querySelectorAll('span')].map((x) => x.textContent)") == ["AI giả", "0"]
        assert trang.js("[...document.querySelectorAll('[data-pv-tab]')].map((b) => [b.dataset.pvTab, b.innerText.replace(/\\s+/g, ' ')])") \
            == [["model", "Model 0"], ["tk", "Tài khoản 0"]]
        assert "Chưa có khoá nào nên chưa có model của «AI giả»" in trang.chu("#pv-trong")
        trang.bam("document.querySelector('[data-pv-tab=tk]')")
        trang.cho("(document.getElementById('pv-trong')?.innerText || '').includes('Chưa có khoá nào cho «AI giả»')", mo_ta="tab Tài khoản rỗng")
        khong_dieu_khien_chet(trang)
        assert trang.js("window.__sk") == 0
        # «Mở tab Tài khoản» → biểu mẫu thêm khoá của đúng module.
        trang.bam("document.getElementById('nut-mo-tk')")
        trang.cho("document.getElementById('bang-ai')?.dataset.muc === 'tai_khoan' && document.querySelector('#form-them')?.dataset.module === 'ai-gia'",
                  mo_ta="Tài khoản › Thêm API")
        assert trang.js("[...document.querySelectorAll('[data-chon-module]')].map((b) => [b.dataset.chonModule, b.getAttribute('aria-pressed')])") \
            == [["ai-gia", "true"]]
        # Khoá hỏng → không lưu, dải báo lỗi nói rõ từng khoá; khoá rời trang ngay khi gửi.
        trang.go("#form-them [data-truong='api_keys'] input", K_HONG)
        trang.bam("document.getElementById('nut-them-luu')")
        trang.cho("document.getElementById('tin-ai')?.getAttribute('role') === 'alert'", han=15, mo_ta="báo khoá hỏng")
        assert "Chưa lưu được «AI giả»" in trang.chu("#tin-ai") and "…2222 không dùng được" in trang.chu("#tin-ai")
        assert trang.js("document.querySelector(\"#form-them [data-truong='api_keys'] input\").value") == ""
        trang.go("#form-them [data-truong='api_keys'] input", K_TOT)
        trang.bam("document.getElementById('nut-them-luu')")
        trang.cho("document.querySelectorAll('[data-tai-khoan=\"ai-gia\"]').length === 1", han=15, mo_ta="dòng tài khoản")
        assert nhom_tk(trang) == [["API", "1"]]
        dong = trang.chu("[data-tai-khoan='ai-gia']")
        assert "AI giả" in dong and "khoá …1111" in dong, dong
        trang.cho("document.querySelector('[data-tai-khoan=\"ai-gia\"] [data-chip-tk=\"suc-khoe\"]')?.innerText === 'Dùng được'", han=15, mo_ta="nhãn sức khoẻ")
        # Provider có model thật ngay, không tải lại trang.
        trang.bam("document.querySelector('[data-nav=\"ai\"]')")
        bam_chuot(trang, "document.querySelector('[data-nav-con=\"provider\"]')", "Provider")
        trang.cho("document.getElementById('bang-ai')?.dataset.muc === 'provider'", mo_ta="Provider")
        trang.bam("document.querySelector('[data-pv-tab=model]')")   # tab con giữ lựa chọn cũ (Tài khoản) như demo
        trang.cho("document.querySelectorAll('[data-mo-nhom]').length === 2", han=15, mo_ta="nhóm model của provider")
        assert trang.chu("[data-pv-tab='model']").replace("\n", " ") == "Model 3"
        # Nhóm thu gọn sẵn; mở cả hai thì đủ 3 model thật.
        assert trang.js("document.querySelectorAll('[data-model]').length") == 0
        for ma in ("chat", "video"):
            trang.bam(f"document.querySelector('[data-mo-nhom=\"{ma}\"]')")
        trang.cho("document.querySelectorAll('[data-model]').length === 3", mo_ta="model của provider")
        html = trang.js("document.documentElement.outerHTML")
        assert K_TOT not in html and K_HONG not in html
        assert not any(K_TOT in x or K_HONG in x for x in trang.state_thanh_phan())


def test_ai_tai_khoan_hai_nguon_thay_va_xoa_khoa(trang, tmp_path, monkeypatch):
    mods = {"ai-gia": module_ai(), "ai-hai": module_ai("ai-hai", "AI thứ hai", "hai", ("chat",))}
    k3 = "AIzaSyTOT-ba--000000000000000000000003"
    with loi_that(tmp_path, monkeypatch, modules=mods) as w:
        lien_ket(w, "ai-hai", {"api_keys": [K_TOT, k3]})
        dang_nhap_quan_tri(trang, w.url)
        trang.di(w.url + "/He-thong-AI.dc.html")
        trang.cho("!!document.querySelector('[data-mo-khoi-pv=\"ai-hai\"]')", mo_ta="khối provider ai-hai")
        # Danh sách khoá bên trong luôn thu gọn lúc mở trang — bấm tiêu đề khối mới xổ danh sách
        assert trang.js("document.querySelectorAll('[data-tai-khoan]').length") == 0
        trang.bam("document.querySelector('[data-mo-khoi-pv=\"ai-hai\"]')")
        trang.cho("document.querySelectorAll('[data-tai-khoan]').length === 2", mo_ta="hai dòng tài khoản")
        # Mỗi khoá đã lưu là một «tài khoản» (như dòng API của demo: tên nhà cung cấp, «khoá …abcd»); nguồn chưa khoá không có dòng.
        assert trang.js("[...document.querySelectorAll('[data-tai-khoan]')].map((d) => [d.dataset.taiKhoan, d.dataset.khoaSo])") \
            == [["ai-hai", "0"], ["ai-hai", "1"]]
        assert nhom_tk(trang) == [["API", "2"]]
        trang.bam("document.getElementById('nut-them')")
        assert trang.js("[...document.querySelectorAll('[data-chon-module]')].map((b) => [b.dataset.chonModule, b.getAttribute('aria-pressed')])") \
            == [["ai-gia", "false"], ["ai-hai", "false"]]
        assert "Chọn một nguồn AI đã cài." in trang.chu("#form-them")
        trang.bam("document.getElementById('nut-them')")
        # Mở dòng đầu: nhà cung cấp, địa chỉ gốc (Chép), khoá dạng che — không có «Xem» khoá; xoá hai bước.
        trang.bam("document.querySelector('[data-tai-khoan=\"ai-hai\"][data-khoa-so=\"0\"] button')")
        mo = "[data-tai-khoan='ai-hai'][data-khoa-so='0']"
        trang.cho(f"!!document.querySelector({json.dumps(mo + ' [data-hd=xoa]')})", mo_ta="dòng mở")
        assert trang.chu(mo + " [data-chip-tk='vt']") == "#1"
        assert trang.chu(mo + " [data-chip-tk='dang-dung']") == "Đang dùng"
        assert "Đã dùng 12 lượt" in trang.chu(mo + " [data-chip-tk='luot']")
        assert "Lần dùng cuối" in trang.chu(mo + " [data-chip-tk='dung-cuoi']")
        assert trang.js(f"!!document.querySelector({json.dumps(mo + ' [data-thanh-hm=\"chat\"]')})")
        assert not trang.js(f"[...document.querySelectorAll({json.dumps(mo + ' button')})].some((b) => b.innerText.includes('Đặt #1'))")
        khong_dieu_khien_chet(trang)  # dòng mở có «Thứ tự khoá» — xoay vòng, không phải «hàng đợi ưu tiên» của demo
        chu = trang.chu(mo)
        assert "Nhà cung cấp" in chu and "https://gia.example/v1" in chu and "…1111" in chu and "Xem" not in chu, chu
        trang.bam(f"document.querySelector({json.dumps(mo + ' [data-hd=xoa]')})")
        assert trang.chu(mo + " [data-hd=xoa]") == "Bấm lần nữa để xoá"
        trang.bam(f"document.querySelector({json.dumps(mo + ' [data-hd=xoa]')})")
        trang.cho("document.querySelectorAll('[data-tai-khoan]').length === 1", han=15, mo_ta="còn một khoá")
        assert json.loads((tmp_path / "bi_mat" / "ai-hai.json").read_text())["api_keys"] == [k3]
        assert "Đã xoá khoá …1111 khỏi «AI thứ hai»" in trang.chu("#tin-ai")
        # Khoá cuối của trường bắt buộc: không có «Xoá» (lõi từ chối), chỉ «Thay khoá».
        trang.bam("document.querySelector('[data-tai-khoan=\"ai-hai\"] button')")
        trang.cho("!!document.querySelector('[data-tai-khoan=\"ai-hai\"] [data-hd=thay]')", mo_ta="nút Thay khoá")
        assert not trang.js("!!document.querySelector('[data-tai-khoan=\"ai-hai\"] [data-hd=xoa]')")
        trang.bam("document.querySelector('[data-tai-khoan=\"ai-hai\"] [data-hd=thay]')")
        trang.go("[data-tai-khoan='ai-hai'] form[data-sua] input", K_TOT)
        trang.bam("document.querySelector('[data-tai-khoan=\"ai-hai\"] form[data-sua] button[type=submit]')")
        trang.cho("(document.getElementById('tin-ai')?.innerText || '').startsWith('Đã thay khoá …0003')", han=15, mo_ta="đã thay khoá")
        assert json.loads((tmp_path / "bi_mat" / "ai-hai.json").read_text())["api_keys"] == [K_TOT]
        assert "khoá …1111" in trang.chu("[data-tai-khoan='ai-hai']")
        assert K_TOT not in trang.js("document.documentElement.outerHTML")
        assert not any(K_TOT in x for x in trang.state_thanh_phan())
        khong_dieu_khien_chet(trang)


def test_provider_chon_nha_cung_cap_model_va_tai_khoan(trang, tmp_path, monkeypatch):
    mods = {"ai-gia": module_ai(), "ai-hai": module_ai("ai-hai", "AI thứ hai", "hai", ("chat",))}
    with loi_that(tmp_path, monkeypatch, modules=mods) as w:
        lien_ket(w, "ai-gia", {"api_keys": [K_TOT]})
        dang_nhap_quan_tri(trang, w.url)
        trang.di(w.url + "/He-thong-AI.dc.html#provider")
        trang.cho("document.querySelectorAll('[data-nhom-md]').length === 2", han=15, mo_ta="nhóm model của ai-gia")
        assert trang.js("document.querySelector('[data-provider]').dataset.provider") == "ai-gia"
        # Danh sách model LUÔN THU GỌN khi vào trang (chủ máy: «danh sách model thì luôn ẩn»): chưa mở nhóm nào thì không có [data-model].
        assert trang.js("document.querySelectorAll('[data-model]').length") == 0
        assert trang.js("[...document.querySelectorAll('[data-mo-nhom]')].map((b) => [b.dataset.moNhom, b.getAttribute('aria-expanded')])") \
            == [["chat", "false"], ["video", "false"]]
        # Bấm mở nhóm «Trò chuyện» → model hiện ra; mở «Video» → model video hiện ra.
        trang.bam("document.querySelector('[data-mo-nhom=\"chat\"]')")
        trang.cho("document.querySelectorAll('[data-nhom-md=\"chat\"] [data-model]').length === 2", mo_ta="mở nhóm trò chuyện")
        trang.bam("document.querySelector('[data-mo-nhom=\"video\"]')")
        trang.cho("document.querySelectorAll('[data-nhom-md=\"video\"] [data-model]').length === 1", mo_ta="mở nhóm video")
        assert trang.js("[...document.querySelectorAll('[data-nhom-md]')].map((g) => [g.dataset.nhomMd, [...g.querySelectorAll('[data-model]')].map((m) => m.innerText.split('\\n')[0])])") \
            == [["chat", ["gia/nhanh", "gia/<img src=x onerror=window.__xss=5>"]], ["video", ["gia/phim-1"]]]
        # Nhãn chỉ giữ phần suy ra được từ dữ liệu («hiểu ảnh» của demo không có trường nào chứng minh).
        assert trang.chu("[data-nhom-md='chat']").startswith("TRÒ CHUYỆN\n") and "HIỂU ẢNH" not in trang.chu("[data-nhom-md='chat']")
        assert not trang.js("window.__xss")
        assert "AI giả · model" in trang.chu("#bang-ai")
        khong_dieu_khien_chet(trang)
        # «Làm mới danh sách» gọi lại /v1/models thật.
        trang.js("window.__md = 0; const f = window.fetch; window.fetch = (u, o) => { if (/v1\\/models/.test(String(u))) window.__md++; return f(u, o); }")
        trang.bam("document.getElementById('nut-lam-moi-md')")
        trang.cho("(document.getElementById('tin-ai')?.innerText || '').includes('3 model')", mo_ta="đã làm mới")
        assert trang.js("window.__md") == 1
        # Tab Tài khoản: sức khoẻ từng khoá, thứ tự #1, Đang dùng, thanh hạn mức khi có trần, không liệt kê danh sách model.
        trang.bam("document.querySelector('[data-pv-tab=tk]')")
        trang.cho("document.querySelector('[data-khoa-pv]')?.innerText.includes('Đã kết nối')", han=15, mo_ta="sức khoẻ khoá")
        assert trang.chu("[data-khoa-pv='0'] [data-vt]") == "#1"
        assert trang.chu("[data-khoa-pv='0'] [data-dang-dung]") == "Đang dùng"
        trang.bam("document.querySelector('[data-khoa-pv=\"0\"] button')")
        trang.cho("!!document.querySelector('[data-kv=kn-anh]')", mo_ta="chi tiết khoá")
        assert trang.chu("[data-kv=kn-anh]").startswith("bị từ chối (HTTP 403)")
        assert trang.chu("[data-kv=kn-video]") == "chưa rõ (chưa dùng thật lần nào)"
        assert trang.chu("[data-kv=do-tre]").startswith("5 ms") and trang.chu("[data-kv=goc]") == "https://gia.example/v1"
        assert trang.chu("[data-kv=so-model]") == "3"
        # Dòng tài khoản mở ra KHÔNG liệt kê model (chỉ có số model ở trên, không có [data-model] bên trong dòng tài khoản)
        assert trang.js("document.querySelectorAll('[data-khoa-pv] [data-model]').length") == 0
        # Thanh hạn mức vẽ khi có toi_da
        assert trang.js("!!document.querySelector('[data-khoa-pv=\"0\"] [data-thanh-hm=\"chat\"]')")
        # Bảng xổ provider chỉ hiện provider ĐÃ CÓ khoá (ai-hai 0 khoá KHÔNG hiện).
        trang.bam("document.getElementById('chon-pv')")
        trang.cho("document.querySelectorAll('[data-pv]').length === 1", mo_ta="bảng xổ provider chỉ hiện provider có khoá")
        assert trang.js("[...document.querySelectorAll('[data-pv]')].map((b) => [b.dataset.pv, b.innerText.replace(/\\s+/g, ' ').trim()])") \
            == [["ai-gia", "AI giả 1"]]
        trang.bam("document.getElementById('chon-pv')")

        # Thêm khoá cho ai-hai: ai-hai xuất hiện trong bảng xổ provider
        lien_ket(w, "ai-hai", {"api_keys": [K_TOT]})
        trang.di(w.url + "/He-thong.dc.html")
        trang.di(w.url + "/He-thong-AI.dc.html#provider")
        trang.cho("document.querySelector('[data-provider]')?.dataset.provider === 'ai-gia'", mo_ta="vào lại provider")
        trang.bam("document.getElementById('chon-pv')")
        trang.cho("document.querySelectorAll('[data-pv]').length === 2", mo_ta="bảng xổ đủ 2 provider có khoá")
        assert trang.js("[...document.querySelectorAll('[data-pv]')].map((b) => [b.dataset.pv, b.innerText.replace(/\\s+/g, ' ').trim()])") \
            == [["ai-gia", "AI giả 1"], ["ai-hai", "AI thứ hai 1"]]
        trang.bam("document.querySelector('[data-pv=\"ai-hai\"]')")
        trang.cho("document.querySelector('[data-provider]')?.dataset.provider === 'ai-hai'", mo_ta="chọn ai-hai")
        assert not trang.js("!!document.querySelector('[role=listbox]')")
        assert trang.js("document.querySelector('[data-pv-tab=model]').getAttribute('aria-selected')") == "true"
        # Đổi lại sang ai-gia: danh sách model lại THU GỌN (không nhớ trạng thái mở cũ)
        trang.bam("document.getElementById('chon-pv')")
        trang.cho("document.querySelectorAll('[data-pv]').length === 2", mo_ta="bảng xổ provider")
        trang.bam("document.querySelector('[data-pv=\"ai-gia\"]')")
        trang.cho("document.querySelector('[data-provider]')?.dataset.provider === 'ai-gia'", mo_ta="quay lại ai-gia")
        assert trang.js("document.querySelectorAll('[data-model]').length") == 0


def test_tai_khoan_suc_khoe_toi_thieu_va_het_han_muc(trang, tmp_path, monkeypatch):
    """Trường bộ đếm là TUỲ CHỌN của hợp đồng `suc_khoe`: module chỉ báo phần tối thiểu thì trang ẩn «#n», «Đang dùng»,
    số lượt, thanh hạn mức mà không vỡ; module báo hết hạn mức nhưng không có trần thì nói rõ lý do nghỉ, không vẽ thanh."""
    k2 = "AIzaSyTOT-hai-000000000000000000000002"
    monkeypatch.setenv("GIA_SK", "cu")
    with loi_that(tmp_path, monkeypatch, modules={"ai-gia": module_ai()}) as w:
        lien_ket(w, "ai-gia", {"api_keys": [K_TOT, k2]})
        dang_nhap_quan_tri(trang, w.url)
        trang.di(w.url + "/He-thong-AI.dc.html")
        trang.cho("!!document.querySelector('[data-mo-khoi-pv=\"ai-gia\"]')", mo_ta="khối provider ai-gia")
        trang.bam("document.querySelector('[data-mo-khoi-pv=\"ai-gia\"]')")
        mo = "[data-tai-khoan='ai-gia'][data-khoa-so='0']"
        trang.cho(f"document.querySelector({json.dumps(mo + ' [data-chip-tk=suc-khoe]')})?.innerText === 'Dùng được'", han=15, mo_ta="sức khoẻ tối thiểu")
        assert trang.js(f"[...document.querySelectorAll({json.dumps(mo + ' [data-chip-tk]')})].map((c) => c.dataset.chipTk)") == ["suc-khoe"]
        trang.bam(f"document.querySelector({json.dumps(mo + ' button')})")
        trang.cho(f"!!document.querySelector({json.dumps(mo + ' [data-hd=xoa]')})", mo_ta="dòng mở")
        assert trang.js(f"document.querySelectorAll({json.dumps(mo + ' [data-thanh-hm], ' + mo + ' [data-kv-tk=thu-tu], ' + mo + ' [data-kv-tk=da-dung]')}).length") == 0
        assert "Số khoá" in trang.chu(mo)
        trang.bam("document.querySelector('[data-nav=\"ai\"]')")
        bam_chuot(trang, "document.querySelector('[data-nav-con=\"provider\"]')", "Provider")
        trang.bam("document.querySelector('[data-pv-tab=tk]')")
        trang.cho("document.querySelectorAll('[data-khoa-pv]').length === 2 && document.querySelector('[data-khoa-pv]').innerText.includes('Đã kết nối')", han=15, mo_ta="Provider › Tài khoản")
        assert not trang.js("!!document.querySelector('[data-khoa-pv] [data-vt], [data-khoa-pv] [data-dang-dung], [data-khoa-pv] [data-thanh-hm]')")

        # Module đổi sang báo hết hạn mức (không trần): «Làm mới» → nhãn nói lý do nghỉ, «#n» + «Đang dùng» đúng khoá, không thanh.
        monkeypatch.setenv("GIA_SK", "het")
        trang.bam("document.querySelector('[data-khoa-pv=\"0\"] button')")
        trang.cho("!!document.querySelector('[data-khoa-pv=\"0\"] [data-hd=lam-moi]')", mo_ta="nút Làm mới")
        trang.bam("document.querySelector('[data-khoa-pv=\"0\"] [data-hd=lam-moi]')")
        trang.cho("(document.querySelector('[data-khoa-pv=\"0\"]')?.innerText || '').includes('Hết hạn mức, hồi lúc')", han=15, mo_ta="nhãn hết hạn mức")
        assert trang.js("[...document.querySelectorAll('[data-khoa-pv] [data-vt]')].map((x) => x.innerText)") == ["#1", "#2"]
        assert trang.js("[...document.querySelectorAll('[data-khoa-pv]')].map((r) => !!r.querySelector('[data-dang-dung]'))") == [False, True]
        assert trang.chu("[data-kv=kn-chat]").startswith("hết hạn mức, hồi lúc")
        assert not trang.js("!!document.querySelector('[data-thanh-hm]')")
        assert not trang.js("!!document.querySelector('[data-kv=nghi]')")  # lý do + giờ hồi đã nằm ở dòng «Trò chuyện»
        khong_dieu_khien_chet(trang)


# ───────────── Studio ─────────────

def _mo_studio(trang, w):
    dang_nhap_quan_tri(trang, w.url)
    trang.di(w.url + "/Studio.dc.html")
    trang.cho("document.querySelectorAll('[data-model-chon]').length > 0", han=15, mo_ta="danh sách model")


def test_studio_chat_stream_dung_va_don_khuc(trang, tmp_path, monkeypatch):
    with loi_that(tmp_path, monkeypatch, modules={"ai-gia": module_ai()}) as w:
        lien_ket(w, "ai-gia", {"api_keys": [K_TOT]})
        _mo_studio(trang, w)
        assert sorted(che_do(trang)) == ["anh", "chat", "video"]
        assert "studio" in nav(trang) and "he-thong" in nav(trang)
        models = trang.js("[...document.querySelectorAll('[data-model-chon]')].map((x) => x.dataset.modelChon)")
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
        trang.go("#o-soan", "một chú mèo")
        trang.bam("document.getElementById('nut-gui')")
        trang.cho("document.getElementById('loi-studio')", han=10, mo_ta="lỗi 403")
        loi = trang.chu("#loi-studio")
        assert "hạng miễn phí" in loi and "Không khoá nào được phép tạo ảnh (giả, như khoá hạng miễn phí)." in loi, loi

        trang.bam("document.querySelector('[data-che-do=\"video\"]')")
        assert trang.js("[...document.querySelectorAll('[data-model-chon]')].map((x) => x.dataset.modelChon)") == ["gia/phim-1"]
        trang.go("#o-soan", "sóng biển")
        trang.bam("document.getElementById('nut-gui')")
        trang.cho("document.querySelector('#video-kq video')?.src.startsWith('blob:')", han=20, mo_ta="video tải về thành blob")
        co = trang.js("fetch(document.getElementById('tai-video').href).then((r) => r.blob()).then((b) => b.size)")
        assert co == len(b"VIDEO-GIA-") * 100
        assert trang.js("document.getElementById('tai-video').getAttribute('download')").endswith(".mp4")


def test_model_anh_nhom_tao_anh_va_studio_ve_anh(trang, tmp_path, monkeypatch):
    """Module khai `kha_nang: ["anh"]` cho một model → Provider có nhóm «Tạo ảnh» (thu gọn, giữa Trò chuyện và Video);
    Studio «Vẽ ảnh» chỉ liệt kê model đó, «Trò chuyện» không lẫn nó. Giao diện chỉ đọc `kha_nang`, không đọc tên."""
    monkeypatch.setenv("GIA_MODEL_ANH", "1")
    with loi_that(tmp_path, monkeypatch, modules={"ai-gia": module_ai()}) as w:
        lien_ket(w, "ai-gia", {"api_keys": [K_TOT]})
        dang_nhap_quan_tri(trang, w.url)
        trang.di(w.url + "/He-thong-AI.dc.html#provider")
        trang.cho("document.querySelectorAll('[data-nhom-md]').length === 3", han=15, mo_ta="ba nhóm model")
        assert trang.js("document.querySelectorAll('[data-model]').length") == 0
        assert trang.js("[...document.querySelectorAll('[data-mo-nhom]')].map((b) => [b.dataset.moNhom, b.getAttribute('aria-expanded')])") \
            == [["chat", "false"], ["anh", "false"], ["video", "false"]]
        assert trang.chu("[data-nhom-md='anh']").startswith("TẠO ẢNH")
        trang.bam("document.querySelector('[data-mo-nhom=\"anh\"]')")
        trang.cho("document.querySelectorAll('[data-nhom-md=\"anh\"] [data-model]').length === 1", mo_ta="mở nhóm Tạo ảnh")
        assert trang.js("document.querySelector('[data-nhom-md=\"anh\"] [data-model]').innerText.split('\\n')[0]") == "gia/ve-1"
        khong_dieu_khien_chet(trang)

        trang.di(w.url + "/Studio.dc.html")  # khoá quản trị đã lưu ở trên
        trang.cho("document.querySelectorAll('[data-model-chon]').length > 0", han=15, mo_ta="danh sách model")
        chon = "[...document.querySelectorAll('[data-model-chon]')].map((x) => x.dataset.modelChon)"
        assert "gia/nhanh" in trang.js(chon) and "gia/ve-1" not in trang.js(chon)
        trang.bam("document.querySelector('[data-che-do=\"anh\"]')")
        trang.cho(f"JSON.stringify({chon}) === JSON.stringify(['gia/ve-1'])", mo_ta="Vẽ ảnh chỉ liệt kê model ảnh")


def _module_gia_model(id_: str, tien_to: str, cung_cap: tuple[str, ...], cac_model: list[dict]) -> dict[str, bytes]:
    manifest = {
        "id": id_,
        "ten": f"AI {id_}",
        "mo_ta": f"Module giả {id_}",
        "phien_ban": "1.0.0",
        "loai": "ai",
        "loai_ten": "AI",
        "loai_thu_tu": 10,
        "hang": "A",
        "giao_thuc": "openai",
        "tien_to": tien_to,
        "cung_cap": list(cung_cap),
        "truong_cau_hinh": [
            {
                "khoa": "api_keys",
                "nhan": "Khoá API",
                "kieu": "danh_sach_bi_mat",
                "bat_buoc": True,
                "mac_dinh": None,
            }
        ],
        "tai_nguyen": {"ram_them_mb": 64, "dia_mb": None, "gpu": False, "mang_ra": [], "ghi_chu": ""},
    }
    phuong_thuc_anh = """
    async def anh(self, body):
        return 200, {"content-type": "application/json"}, b'{"created": 1, "data": []}'
""" if "anh" in cung_cap else ""
    ma = f"""
class Module:
    def __init__(self, manifest):
        self.manifest = manifest
        self.tien_to = manifest["tien_to"]
        self.khoa = []
    def nap(self, cau_hinh):
        self.khoa = list(cau_hinh.get("api_keys") or [])
    async def cai(self, cau_hinh):
        return {{"ok": True, "ket_qua_tung_khoa": [{{"khoa": "…1111", "ok": True, "do_tre_ms": 5, "so_model": len({cac_model!r}), "loi": None}}], "cau_hinh_luu": cau_hinh}}
    async def chat(self, body, stream):
        return 200, {{"content-type": "application/json"}}, b'{{"choices": [{{"message": {{"content": "ok"}}}}]}}'
{phuong_thuc_anh}
    async def models(self):
        return {cac_model!r}
    async def suc_khoe(self):
        return {{"ok": True, "khoa": [{{"khoa": "…1111", "ok": True, "do_tre_ms": 5, "so_model": len({cac_model!r}), "loi": None}}]}}
    async def dung(self):
        pass
"""
    return {
        "manifest.json": json.dumps(manifest, ensure_ascii=False).encode(),
        "module.py": ma.encode(),
    }


def test_studio_hien_model_ca_hai_module_khi_mot_module_khai_mot_khong(trang, tmp_path, monkeypatch):
    """Khi có hai module đã liên kết:
    - Module A (chat + anh): không khai `kha_nang` cho model chat, model ảnh khai `["anh"]`;
    - Module B (chat): model chat khai `["chat"]`.
    Studio không được để module B làm mất model chat của module A:
    - Chế độ Trò chuyện hiện model của CẢ HAI (A lẫn B), model ảnh của A không lọt vào Trò chuyện;
    - Chế độ Vẽ ảnh hiện model ảnh của A."""
    mod_a = _module_gia_model("ai-a", "a", ("chat", "anh"), [
        {"id": "a/chat-1", "object": "model"},
        {"id": "a/ve-1", "object": "model", "kha_nang": ["anh"]},
    ])
    mod_b = _module_gia_model("ai-b", "b", ("chat",), [
        {"id": "b/chat-1", "object": "model", "kha_nang": ["chat"]},
    ])
    with loi_that(tmp_path, monkeypatch, modules={"ai-a": mod_a, "ai-b": mod_b}) as w:
        lien_ket(w, "ai-a", {"api_keys": [K_TOT]})
        lien_ket(w, "ai-b", {"api_keys": [K_TOT]})
        _mo_studio(trang, w)

        # Chế độ Trò chuyện (mặc định): hiện model chat của cả A lẫn B, model ảnh của A không lọt vào.
        chon = "[...document.querySelectorAll('[data-model-chon]')].map((x) => x.dataset.modelChon)"
        ds_chat = trang.js(chon)
        assert "a/chat-1" in ds_chat
        assert "b/chat-1" in ds_chat
        assert "a/ve-1" not in ds_chat

        # Chế độ Vẽ ảnh: hiện model ảnh của A, không lẫn model chat của A hay B.
        trang.bam("document.querySelector('[data-che-do=\"anh\"]')")
        trang.cho(f"{chon}.includes('a/ve-1')", han=15, mo_ta="chế độ Vẽ ảnh hiện model ảnh của A")
        ds_anh = trang.js(chon)
        assert "a/ve-1" in ds_anh
        assert "a/chat-1" not in ds_anh
        assert "b/chat-1" not in ds_anh


def test_studio_chi_hien_che_do_module_khai(trang, tmp_path, monkeypatch):
    with loi_that(tmp_path, monkeypatch, modules={"ai-hai": module_ai("ai-hai", "AI chỉ chat", "hai", ("chat",))}) as w:
        lien_ket(w, "ai-hai", {"api_keys": [K_TOT]})
        _mo_studio(trang, w)
        assert che_do(trang) == ["chat"]


def test_studio_man_dau_hoi_thoai_tao_moi_va_thanh_duoi(trang, tmp_path, monkeypatch):
    """Studio theo Main.dc.html của bản demo: màn đầu (viên trạng thái + tiêu đề + ô soạn + chip model, KHÔNG gợi ý),
    gửi → lượt hội thoại thay màn đầu, «Tạo mới» về màn đầu; trên điện thoại thanh dưới đáy chỉ có mục đang có thật."""
    with loi_that(tmp_path, monkeypatch, modules={"ai-hai": module_ai("ai-hai", "AI chỉ chat", "hai", ("chat",))}) as w:
        lien_ket(w, "ai-hai", {"api_keys": [K_TOT]})
        _mo_studio(trang, w)
        # Màn đầu: viên «Mọi dịch vụ ổn», tiêu đề, ô soạn, chip model mang model THẬT đang chọn (từ /v1/models).
        assert trang.chu("#trang-thai-chung") == "Mọi dịch vụ ổn"
        assert trang.chu(".st-tieu-de") == "Bạn muốn làm gì hôm nay?"
        assert trang.js("document.getElementById('o-soan').placeholder") == "Hỏi bất kỳ điều gì, hoặc giao một việc cần làm…"
        assert trang.js("document.getElementById('chon-model').dataset.model") == "hai/nhanh"
        assert "hai/nhanh" in trang.chu("#chon-model")
        # Demo hiện hành KHÔNG có gợi ý ở màn đầu.
        assert not trang.js("!!document.querySelector('.y-tuong, [aria-label=\"Gợi ý\"]')")
        # Hàng việc dưới ô soạn đúng thứ tự demo, chỉ mục có chức năng thật: Trò chuyện · Tạo mới · Lịch sử.
        assert trang.js("[...document.querySelectorAll('[role=toolbar] button')].map((b) => b.innerText.trim())") == ["Trò chuyện", "Tạo mới", "Lịch sử"]
        # Chỉ chế độ module khai (chỉ chat): không «Vẽ ảnh»/«Tạo video»; phần demo chưa có chức năng thật không được vẽ
        # (Dịch, Vietsub, Lồng tiếng, tab Tác phẩm/Đời sống, đính kèm, loa, micro, avatar).
        chu = trang.js("document.body.innerText")
        for x in ("Vẽ ảnh", "Tạo video", "Dịch", "Vietsub", "Lồng tiếng", "Tác phẩm", "Đời sống"):
            assert x not in chu, x
        assert not trang.js("!!document.querySelector('[aria-label=\"Đính kèm tệp\"], [aria-label=\"Nói để nhập chữ\"], "
                            "[aria-label^=\"Chọn giọng đọc\"], [aria-label=\"Tài khoản\"]')")
        # Bảng chọn model mở/đóng như popover của demo; chọn một model → chip đổi theo.
        assert trang.js("document.getElementById('bang-model').hidden")
        trang.bam("document.getElementById('chon-model')")
        trang.cho("!document.getElementById('bang-model').hidden", mo_ta="mở bảng model")
        assert trang.js("document.querySelector('[data-model-chon=\"hai/nhanh\"]').getAttribute('aria-pressed')") == "true"
        # Chọn bằng CHUỘT THẬT dòng thứ hai (nằm đè lên ô soạn): không lớp nào được che dòng model.
        bam_chuot(trang, "document.querySelectorAll('[data-model-chon]')[1]", "dòng model thứ hai (1280)")
        trang.cho("document.getElementById('bang-model').hidden", mo_ta="chọn xong thì đóng")
        assert trang.js("document.getElementById('chon-model').dataset.model") != "hai/nhanh"
        trang.khung(390, 844, True)
        bam_chuot(trang, "document.getElementById('chon-model')", "chip model (390)")
        trang.cho("!document.getElementById('bang-model').hidden", mo_ta="mở bảng model ở 390")
        bam_chuot(trang, "document.querySelector('[data-model-chon=\"hai/nhanh\"]')", "dòng model (390)")
        trang.cho("document.getElementById('bang-model').hidden && document.getElementById('chon-model').dataset.model === 'hai/nhanh'",
                  mo_ta="chọn lại hai/nhanh ở 390")
        r = trang.js("(() => { const b = document.getElementById('bang-model'); b.hidden = false; const r = b.getBoundingClientRect(); b.hidden = true; return [r.left, r.right]; })()")
        assert r[0] >= 0 and r[1] <= 390, r          # không bị cắt mép như bản demo ở khổ hẹp
        trang.khung(1280, 800)

        # Gửi: màn đầu nhường chỗ cho lượt hội thoại (bong bóng người hỏi + câu trả lời), ô soạn trống lại.
        trang.js("const o = document.getElementById('o-soan'); o.value = ''; o.dispatchEvent(new Event('input', {bubbles: true}))")
        trang.go("#o-soan", "Chào")
        trang.bam("document.getElementById('nut-gui')")
        trang.cho("document.getElementById('trang-thai')?.innerText === 'Xong.'", han=10, mo_ta="chat xong")
        assert not trang.js("!!document.querySelector('.st-tieu-de')")
        assert trang.chu("[data-vai='user']") == "Chào"
        assert trang.chu("[data-vai='assistant']") == "Xin chào, tôi là model giả."
        assert trang.js("document.getElementById('o-soan').value") == ""
        # Tạo mới: xoá hội thoại, màn đầu trở lại.
        trang.bam("document.getElementById('nut-xoa')")
        trang.cho("!!document.querySelector('.st-tieu-de') && !document.querySelector('[data-vai]')", mo_ta="Tạo mới xoá hội thoại")

        # Điện thoại: thanh trên thu gọn, thanh dưới hiện với ĐÚNG các mục đang có (Studio, Hệ thống).
        assert trang.js("getComputedStyle(document.querySelector('nav.bn')).display") == "none"
        trang.khung(390, 844, True)
        trang.cho("getComputedStyle(document.querySelector('nav.bn')).display !== 'none'", mo_ta="thanh dưới ở khổ 390")
        assert trang.js("[...document.querySelectorAll('[data-nav-duoi]')].map((a) => a.dataset.navDuoi)") == ["studio", "he-thong"]
        assert trang.js("getComputedStyle(document.getElementById('thanh-tab')).display") == "none"
        assert trang.js("document.querySelector('[data-nav-duoi=\"studio\"]').getAttribute('aria-current')") == "page"
        assert khong_tran(trang)


# Câu trả lời DÀI (nhiều đoạn, danh sách, khối mã, chữ dài không dấu cách) — dài hơn mọi vùng hội thoại trên điện thoại.
TRA_DAI = ([f"Đoạn {i}: câu trả lời dài để đo vùng hội thoại trên điện thoại, chữ phải đọc được tới tận mép phải. \n\n"
            for i in range(1, 9)]
           + ["- mục một của danh sách khá dài để xuống dòng\n", "- mục hai\n\n", "```\nmot_dong_ma_rat_dai = 'khong_xuong_dong'\n```\n\n",
              "Chuoi_dai_khong_dau_cach_" * 6 + "\n\n", "Đoạn cuối."])
# Số đo Studio: chiều cao vùng hội thoại so với khung nhìn; số hình chữ nhật CHỮ (từng dòng của từng nút chữ, cùng khối
# bong bóng / đoạn trả lời) giao với hình chữ nhật của hai nút nổi ↑↓ NỚI RA 8px mỗi phía — không giao thôi chưa đủ:
# bản demo đặt nút cách mép chữ 4px, bóng đổ 12px phủ lên chữ (đo trên bố cục cũ: «giao» trần = 0 mà vẫn che);
# đỉnh câu trả lời cuối so với vùng hội thoại.
DO_STUDIO = """(() => {
  const c = document.getElementById('cuon-luot'); const rc = c.getBoundingClientRect();
  const nut = [...document.querySelectorAll('a[href="#cu-nhat"], a[href="#moi-nhat"]')].map((a) => a.getBoundingClientRect());
  const CACH = 8; const giao = (a, b) => a.left < b.right + CACH && b.left - CACH < a.right && a.top < b.bottom + CACH && b.top - CACH < a.bottom;
  const hop = []; const w = document.createTreeWalker(c, NodeFilter.SHOW_TEXT);
  for (let n; (n = w.nextNode());) { if (!n.textContent.trim()) continue; const r = document.createRange(); r.selectNodeContents(n); hop.push(...r.getClientRects()); }
  c.querySelectorAll('[data-vai], .md > *').forEach((e) => hop.push(e.getBoundingClientRect()));
  const tra = [...c.querySelectorAll('[data-vai="assistant"]')].pop();
  return { cao: rc.height, khung: innerHeight, so_nut: nut.length, so_hop: hop.length,
           giao: hop.filter((r) => r.width && r.height && nut.some((b) => giao(r, b))).length,
           dinh_tra: tra ? tra.getBoundingClientRect().top - rc.top : null };
})()"""
# Kiểu tính được của các phần bố cục Studio (để chốt: màn đầu và máy tính giữ y hệt demo).
KIEU_STUDIO = """(() => { const g = (e, k) => e ? getComputedStyle(e)[k] : null; const q = (s) => document.querySelector(s);
  const c = q('#cuon-luot'); const n = q('a[href="#moi-nhat"]');
  return { o_soan: q('#o-soan').getBoundingClientRect().height, nut_viec: q('#nut-xoa').getBoundingClientRect().height,
           dong_tt: g(q('#trang-thai-chung').parentElement, 'padding'), cuon_phai: g(c, 'paddingRight'),
           nut_noi: n ? n.getBoundingClientRect().width : null }; })()"""


def test_studio_dien_thoai_hoi_thoai_dai_khong_bi_che(trang, tmp_path, monkeypatch):
    """Chủ máy báo «Che hết nội dung phản hồi»: trên điện thoại sau khi gửi, vùng hội thoại chỉ còn ≈190 px và hai nút
    nổi ↑↓ đè lên chữ. Khi ĐÃ có hội thoại ở khổ ≤1023: vùng hội thoại chiếm phần còn lại (≥45% khung nhìn 390×844),
    nút nổi có cột lề riêng (không giao chữ ở mọi vị trí cuộn), Tạo mới / Lịch sử vẫn bấm được bằng chuột thật, trả lời
    dài thì đầu câu trả lời còn trong vùng nhìn. Màn đầu và máy tính 1280 giữ nguyên bố cục demo."""
    with loi_that(tmp_path, monkeypatch, modules={"ai-hai": module_ai("ai-hai", "AI chỉ chat", "hai", ("chat",))}) as w:
        lien_ket(w, "ai-hai", {"api_keys": [K_TOT]})
        _mo_studio(trang, w)
        man_dau_may_tinh = trang.js(KIEU_STUDIO)
        assert man_dau_may_tinh == {"o_soan": 96, "nut_viec": 44, "dong_tt": "16px 28px", "cuon_phai": None, "nut_noi": None}, man_dau_may_tinh
        trang.khung(390, 844, True)
        time.sleep(0.2)
        assert trang.js(KIEU_STUDIO) == man_dau_may_tinh   # màn đầu điện thoại: y hệt demo (chỉ đổi khi đã có hội thoại)
        trang.chup("studio-dai-dien-thoai-man-dau")

        monkeypatch.setenv("GIA_KHUC", json.dumps(TRA_DAI))
        monkeypatch.setenv("GIA_TRE", "0.03")
        trang.go("#o-soan", "Kể dài cho tôi nghe")
        trang.bam("document.getElementById('nut-gui')")
        trang.cho("document.getElementById('trang-thai')?.innerText === 'Xong.'", han=20, mo_ta="trả lời dài xong")
        time.sleep(0.2)
        trang.js("document.activeElement.blur()")
        do = trang.js(DO_STUDIO)
        trang.chup("studio-dai-dien-thoai")
        # (a) vùng hội thoại chiếm phần còn lại của màn hình.
        assert do["so_nut"] == 2 and do["so_hop"] > 20, do
        assert do["cao"] >= 0.45 * do["khung"], do
        # Hàng «Trò chuyện / Tạo mới / Lịch sử» gọn thành MỘT hàng, nút vẫn cao 44 (bấm bằng ngón tay); ô nhập 2 dòng.
        assert len(set(trang.js("[...document.querySelectorAll('[role=toolbar] button')].map((b) => Math.round(b.getBoundingClientRect().top))"))) == 1
        kieu = trang.js(KIEU_STUDIO)
        assert kieu["nut_viec"] == 44 and kieu["o_soan"] == 68, kieu
        # (c) trả lời dài hơn vùng nhìn: tự cuộn theo nhưng DỪNG ở đầu câu trả lời (đầu câu trả lời còn trong vùng nhìn).
        assert trang.js("(() => { const c = document.getElementById('cuon-luot'); return c.scrollHeight - c.clientHeight - c.scrollTop > 100; })()"), \
            "câu trả lời phải dài hơn vùng nhìn để đo được việc dừng ở đầu"
        assert 0 <= do["dinh_tra"] < do["cao"] / 2, do
        # (b) nút nổi không giao chữ ở đầu, giữa và cuối hội thoại.
        for phan in (0, 0.5, 1):
            trang.js(f"(() => {{ const c = document.getElementById('cuon-luot'); c.scrollTop = (c.scrollHeight - c.clientHeight) * {phan}; }})()")
            time.sleep(0.05)
            d = trang.js(DO_STUDIO)
            assert d["giao"] == 0, (phan, d)
        # Nút nổi vẫn làm đúng việc: ↑ lên yêu cầu đầu, ↓ xuống cuối.
        bam_chuot(trang, "document.querySelector('a[href=\"#cu-nhat\"]')", "nút lên đầu")
        trang.cho("document.getElementById('cuon-luot').scrollTop === 0", mo_ta="lên đầu")
        bam_chuot(trang, "document.querySelector('a[href=\"#moi-nhat\"]')", "nút xuống cuối")
        trang.cho("(() => { const c = document.getElementById('cuon-luot'); return c.scrollHeight - c.clientHeight - c.scrollTop < 2; })()", mo_ta="xuống cuối")
        assert khong_tran(trang)

        # Lịch sử và Tạo mới vẫn bấm được bằng CHUỘT THẬT (không lớp nào đè).
        bam_chuot(trang, "document.getElementById('nut-lich-su')", "Lịch sử (390, đã có hội thoại)")
        trang.cho("!!document.getElementById('bang-lich-su') && document.querySelectorAll('[data-cuoc]').length === 1", mo_ta="bảng Lịch sử")
        _dong_lich_su(trang)
        bam_chuot(trang, "document.getElementById('nut-xoa')", "Tạo mới (390, đã có hội thoại)")
        trang.cho("!!document.querySelector('.st-tieu-de') && !document.querySelector('[data-vai]')", mo_ta="Tạo mới về màn đầu")
        assert trang.js(KIEU_STUDIO) == man_dau_may_tinh   # về màn đầu: bố cục demo trở lại

        # Máy tính 1280 khi đã có hội thoại: giữ nguyên bố cục demo (ô soạn 3 dòng, nút 44, cột lề 60 của nút nổi).
        trang.khung(1280, 800)
        monkeypatch.setenv("GIA_KHUC", json.dumps(["Ngắn."]))
        trang.go("#o-soan", "Chào")
        trang.bam("document.getElementById('nut-gui')")
        trang.cho("document.getElementById('trang-thai')?.innerText === 'Xong.'", han=10, mo_ta="chat xong ở 1280")
        assert trang.js(KIEU_STUDIO) == {"o_soan": 96, "nut_viec": 44, "dong_tt": "16px 28px", "cuon_phai": "60px", "nut_noi": 46}


# ───────────── Studio: Lịch sử trò chuyện (nút «Lịch sử» của demo) ─────────────

LS = "web2api_lich_su_tro_chuyen"
DONG_CUOC = "[...document.querySelectorAll('[data-cuoc]')].map((b) => b.innerText.replace(/\\s+/g, ' ').trim())"
BAT_THAN_CHAT = ("(() => { window.__chat = []; const f = window.fetch; window.fetch = (u, o) => {"
                 " if (String(u).endsWith('v1/chat/completions')) window.__chat.push(JSON.parse(o.body)); return f(u, o); }; })()")


def _mo_lich_su(trang):
    trang.bam("document.getElementById('nut-lich-su')")
    trang.cho("!!document.getElementById('bang-lich-su')", mo_ta="bảng Lịch sử")


def _dong_lich_su(trang):
    # Như demo: bấm ra ngoài (lớp nền trong suốt phía sau bảng) thì đóng.
    trang.bam("document.getElementById('bang-lich-su').previousElementSibling")
    trang.cho("!document.getElementById('bang-lich-su')", mo_ta="đóng bảng Lịch sử")


def test_studio_lich_su_tao_moi_luu_nap_tim(trang, tmp_path, monkeypatch):
    """Tạo mới lưu cuộc cũ vào Lịch sử (localStorage của trình duyệt); bảng Lịch sử như demo: tiêu đề = tin đầu tiên,
    dòng phụ «Hôm nay · HH:MM · N lượt», «Đang mở», tìm theo chữ; chọn một cuộc → các lượt chữ trở lại và lượt sau gửi
    kèm chúng; tải lại trang vẫn mở cuộc đang nói (như demo). Không khoá quản trị, không tin giờ trong dữ liệu lưu."""
    with loi_that(tmp_path, monkeypatch, modules={"ai-hai": module_ai("ai-hai", "AI chỉ chat", "hai", ("chat",))}) as w:
        lien_ket(w, "ai-hai", {"api_keys": [K_TOT]})
        _mo_studio(trang, w)
        _mo_lich_su(trang)
        assert trang.chu("#bang-lich-su").replace("\n", " ").startswith("Lịch sử trò chuyện chọn một cuộc để nói tiếp")
        assert "Chưa có cuộc trò chuyện nào." in trang.chu("#bang-lich-su")
        assert trang.js("document.getElementById('nut-lich-su').getAttribute('aria-expanded')") == "true"
        _dong_lich_su(trang)

        _gui(trang, "Giá xăng hôm nay", 1)
        _gui(trang, "Còn dầu thì sao?", 2)
        trang.bam("document.getElementById('nut-xoa')")
        trang.cho("!!document.querySelector('.st-tieu-de') && !document.querySelector('[data-vai]')", mo_ta="Tạo mới về màn đầu")
        _gui(trang, "Soạn thư   trả lời khách", 1)   # tiêu đề gộp khoảng trắng thành một dòng

        _mo_lich_su(trang)
        dong = trang.js(DONG_CUOC)
        assert len(dong) == 2, dong
        assert re.fullmatch(r"Soạn thư trả lời khách Hôm nay · \d\d:\d\d · 1 lượt Đang mở", dong[0]), dong
        assert re.fullmatch(r"Giá xăng hôm nay Hôm nay · \d\d:\d\d · 2 lượt", dong[1]), dong
        # Tìm: lọc theo tiêu đề, không phân biệt hoa thường (như demo).
        trang.go("#tim-lich-su", "XĂNG")
        trang.cho("document.querySelectorAll('[data-cuoc]').length === 1", mo_ta="lọc còn một cuộc")
        trang.go("#tim-lich-su", "zzz")
        trang.cho("document.querySelectorAll('[data-cuoc]').length === 0", mo_ta="không cuộc nào khớp")
        assert "Không có cuộc nào khớp." in trang.chu("#bang-lich-su")
        trang.js("const o = document.getElementById('tim-lich-su'); o.value = ''; o.dispatchEvent(new Event('input', {bubbles: true}))")
        trang.cho("document.querySelectorAll('[data-cuoc]').length === 2", mo_ta="bỏ lọc")

        # Chọn cuộc cũ: bảng đóng, các lượt chữ trở lại; lượt tiếp theo gửi kèm lịch sử của cuộc đó.
        bam_chuot(trang, "[...document.querySelectorAll('[data-cuoc]')].find((b) => b.innerText.includes('Giá xăng'))", "dòng Lịch sử")
        trang.cho("!document.getElementById('bang-lich-su') && document.querySelectorAll('[data-vai=\"user\"]').length === 2", mo_ta="nạp cuộc cũ")
        assert trang.js("[...document.querySelectorAll('[data-vai=\"user\"]')].map((x) => x.innerText)") == ["Giá xăng hôm nay", "Còn dầu thì sao?"]
        assert trang.js("document.querySelectorAll('[data-vai=\"assistant\"]').length") == 2
        trang.js(BAT_THAN_CHAT)
        _gui(trang, "Cảm ơn", 3)
        tin = trang.js("window.__chat")[-1]["messages"]
        assert [m["role"] for m in tin] == ["user", "assistant", "user", "assistant", "system", "user"], tin
        assert [m["content"] for m in tin if m["role"] == "user"] == ["Giá xăng hôm nay", "Còn dầu thì sao?", "Cảm ơn"]

        # Dữ liệu lưu: chỉ lượt chữ; không khoá quản trị, không tin giờ.
        luu = trang.js(f"localStorage.getItem({json.dumps(LS)})")
        assert KHOA_QT not in luu and "Giờ hiện tại" not in luu and "system" not in luu
        ds = json.loads(luu)
        assert [c["ten"] for c in ds] == ["Giá xăng hôm nay", "Soạn thư trả lời khách"]
        assert [len(c["luot"]) for c in ds] == [3, 1]

        # Tải lại trang: cuộc đang nói được mở lại (demo nhớ cuộc đang mở trong trình duyệt).
        trang.di(w.url + "/Studio.dc.html")
        trang.cho("document.querySelectorAll('[data-vai=\"user\"]').length === 3", mo_ta="mở lại cuộc đang nói")
        # Tạo mới thì tải lại là màn đầu.
        trang.bam("document.getElementById('nut-xoa')")
        trang.di(w.url + "/Studio.dc.html")
        trang.cho("document.querySelectorAll('[data-model-chon]').length > 0", han=15, mo_ta="Studio")
        assert trang.js("!!document.querySelector('.st-tieu-de') && !document.querySelector('[data-vai]')")


def test_studio_lich_su_tran_so_cuoc_va_dung_luong(trang, tmp_path, monkeypatch):
    with loi_that(tmp_path, monkeypatch, modules={"ai-hai": module_ai("ai-hai", "AI chỉ chat", "hai", ("chat",))}) as w:
        lien_ket(w, "ai-hai", {"api_keys": [K_TOT]})
        _mo_studio(trang, w)
        # 60 cuộc cũ có sẵn + dữ liệu hỏng (bị bỏ qua) → lưu thêm một cuộc: còn đúng 50 cuộc, cuộc mới đứng đầu.
        trang.js(f"""localStorage.setItem({json.dumps(LS)}, JSON.stringify([{{id: 1, ten: 'hỏng'}}, 'rác'].concat(
            Array.from({{length: 60}}, (_, i) => ({{id: 'cu' + i, ten: 'Cuộc cũ ' + i, luc: 1000 + i,
              luot: [{{hoi: 'hỏi ' + i, tra: 'đáp', model: 'hai/nhanh'}}]}})))))""")
        _gui(trang, "Cuộc mới nhất", 1)
        ds = json.loads(trang.js(f"localStorage.getItem({json.dumps(LS)})"))
        assert len(ds) == 50 and ds[0]["ten"] == "Cuộc mới nhất" and ds[-1]["id"] == "cu11", (len(ds), ds[-1])
        # Dung lượng: hai cuộc cũ ~600 nghìn ký tự mỗi cuộc → vượt trần 1 triệu ký tự: bỏ cuộc cũ nhất, giữ cuộc mới.
        trang.bam("document.getElementById('nut-xoa')")
        trang.js(f"""localStorage.setItem({json.dumps(LS)}, JSON.stringify([1, 2].map((i) => ({{id: 'to' + i, ten: 'To ' + i,
            luc: 1000 + i, luot: [{{hoi: 'x', tra: 'a'.repeat(600000), model: 'hai/nhanh'}}]}}))))""")
        _gui(trang, "Sau cuộc to", 1)
        luu = trang.js(f"localStorage.getItem({json.dumps(LS)})")
        assert len(luu) <= 1_000_000
        assert [c["id"] for c in json.loads(luu)][1:] == ["to2"] and json.loads(luu)[0]["ten"] == "Sau cuộc to"
        _mo_lich_su(trang)
        assert len(trang.js(DONG_CUOC)) == 2
        _dong_lich_su(trang)

        # Nhãn thời điểm đủ bốn dạng của demo: Hôm nay / Hôm qua / thứ trong tuần / dd/mm (mốc giữa trưa, tránh nửa đêm).
        trang.js(f"""(() => {{ const luc = (n) => {{ const d = new Date(); d.setHours(12, 0, 0, 0); d.setDate(d.getDate() - n); return d.getTime(); }};
            localStorage.setItem({json.dumps(LS)}, JSON.stringify([0, 1, 3, 10].map((n) => ({{id: 'n' + n, ten: 'Cách ' + n + ' ngày',
              luc: luc(n), luot: [{{hoi: 'h', tra: 'đ', model: 'hai/nhanh'}}]}})))); }})()""")
        _mo_lich_su(trang)
        dong = trang.js(DONG_CUOC)
        assert re.fullmatch(r"Cách 0 ngày Hôm nay · 12:00 · 1 lượt", dong[0]), dong
        assert re.fullmatch(r"Cách 1 ngày Hôm qua · 12:00 · 1 lượt", dong[1]), dong
        assert re.fullmatch(r"Cách 3 ngày (Thứ (Hai|Ba|Tư|Năm|Sáu|Bảy)|Chủ Nhật) · 12:00 · 1 lượt", dong[2]), dong
        assert re.fullmatch(r"Cách 10 ngày \d\d/\d\d · 12:00 · 1 lượt", dong[3]), dong


def test_studio_tao_moi_khi_video_dang_chay_khong_ghi_vao_cuoc_moi(trang, tmp_path, monkeypatch):
    """«Tạo mới» khi video còn đang tạo (máy chủ không huỷ được): màn mới gửi được ngay, và kết quả/lỗi/trạng thái
    của việc cũ không hiện vào cuộc mới."""
    with loi_that(tmp_path, monkeypatch, modules={"ai-gia": module_ai()}) as w:
        lien_ket(w, "ai-gia", {"api_keys": [K_TOT]})
        _mo_studio(trang, w)
        trang.bam("document.querySelector('[data-che-do=\"video\"]')")
        trang.go("#o-soan", "sóng biển")
        trang.bam("document.getElementById('nut-gui')")
        trang.cho("document.querySelector('[role=progressbar]')", mo_ta="video đang tạo")
        trang.bam("document.getElementById('nut-xoa')")
        trang.cho("!!document.querySelector('.st-tieu-de') && !document.querySelector('[data-vai]')", mo_ta="Tạo mới")
        trang.go("#o-soan", "mô tả mới")
        trang.cho("!document.getElementById('nut-gui').disabled", mo_ta="nút gửi bật lại ngay")
        time.sleep(5.5)   # video giả xong sau ~4 giây (1 giây + một lần hỏi 3 giây)
        assert trang.js("!document.querySelector('[data-vai]') && !document.getElementById('loi-studio') && !document.getElementById('trang-thai') && !document.getElementById('video-kq')")
        assert not trang.js("document.getElementById('nut-gui').disabled")


def test_studio_trinh_duyet_chan_luu_tru_thi_an_lich_su(trang, tmp_path, monkeypatch):
    """localStorage bị chặn (ẩn danh / chặn dữ liệu trang): không có nút «Lịch sử» (không để nút chết); chat và
    «Tạo mới» vẫn chạy, khoá quản trị chỉ sống trong trang."""
    trang.cdp.goi("Page.enable", {}, trang.s)   # cần bật miền Page thì script mới chạy ở trang mới
    trang.cdp.goi("Page.addScriptToEvaluateOnNewDocument", {"source": "for (const k of ['getItem', 'setItem', 'removeItem', 'clear'])"
                  " Storage.prototype[k] = () => { throw new DOMException('bị chặn', 'SecurityError'); };"}, trang.s)
    with loi_that(tmp_path, monkeypatch, modules={"ai-hai": module_ai("ai-hai", "AI chỉ chat", "hai", ("chat",))}) as w:
        lien_ket(w, "ai-hai", {"api_keys": [K_TOT]})
        _mo_studio(trang, w)
        trang.js(BAT_LOI_JS)
        assert trang.js("(() => { try { localStorage.setItem('a', '1'); return false; } catch (e) { return true; } })()")
        assert not trang.js("!!document.getElementById('nut-lich-su')")
        assert trang.js("[...document.querySelectorAll('[role=toolbar] button')].map((b) => b.innerText.trim())") == ["Trò chuyện", "Tạo mới"]
        _gui(trang, "Chào", 1)
        trang.bam("document.getElementById('nut-xoa')")
        trang.cho("!!document.querySelector('.st-tieu-de') && !document.querySelector('[data-vai]')", mo_ta="Tạo mới")
        assert not trang.js("!!document.getElementById('dc-loi')") and trang.js("window.__loiJs") == []


# ───────────── Studio: markdown an toàn + giờ thật trong lời gọi ─────────────

MD = "[data-vai='assistant']"
# Bắt lỗi JS chưa ai bắt (kể cả lỗi trong attributeChangedCallback của <md-chu> — trình duyệt báo qua sự kiện error).
BAT_LOI_JS = "window.__loiJs = []; window.addEventListener('error', (e) => window.__loiJs.push(String(e.message)))"
CAU_MD = [
    "## Kết quả\n", "Đây là **đậm** và *nghiêng*, mã `x = 1`.\n\n",
    "- một\n- hai\n  - con lồng\n\n", "1. bước một\n2. bước hai\n\n",
    "```py\nprint(\"xin chào\")\n```\n\n",
    "| Tên | Giá |\n|---|---:|\n| táo | 5 |\n\n", "Xem [tài liệu](https://vd.example/tai-lieu).",
]


def _gui(trang, chu: str, so_tra_loi: int):
    trang.go("#o-soan", chu)
    trang.bam("document.getElementById('nut-gui')")
    trang.cho(f"document.querySelectorAll({json.dumps(MD)}).length === {so_tra_loi} && document.getElementById('trang-thai')?.innerText === 'Xong.'",
              han=15, mo_ta=f"trả lời lượt {so_tra_loi}")


def test_studio_markdown_an_toan_va_stream_do(trang, tmp_path, monkeypatch):
    with loi_that(tmp_path, monkeypatch, modules={"ai-gia": module_ai()}) as w:
        lien_ket(w, "ai-gia", {"api_keys": [K_TOT]})
        _mo_studio(trang, w)
        trang.js(BAT_LOI_JS)

        # (1) Markdown thành phần tử thật; không còn «**» hiển thị.
        monkeypatch.setenv("GIA_KHUC", json.dumps(CAU_MD))
        _gui(trang, "Cho tôi một ví dụ", 1)
        q = f"document.querySelector({json.dumps(MD)})"
        dem = trang.js(f"""(() => {{ const m = {q}; const n = (s) => m.querySelectorAll(s).length;
            return {{ h2: n('h2'), strong: n('strong'), em: n('em'), code: n('p code'), li: n('li'), long: n('ul ul > li'),
                      ol: n('ol > li'), pre: n('pre > code'), th: n('table th'), td: n('table td'), bang: n('.md-bang > table') }}; }})()""")
        assert dem == {"h2": 1, "strong": 1, "em": 1, "code": 1, "li": 5, "long": 1, "ol": 2, "pre": 1, "th": 2, "td": 2, "bang": 1}, dem
        assert trang.js(f"{q}.querySelector('pre').dataset.ngonNgu") == "py"
        assert trang.js(f"{q}.querySelector('pre code').textContent") == 'print("xin chào")'
        assert trang.js(f"getComputedStyle({q}.querySelector('td + td')).textAlign") == "right"
        a = trang.js(f"(() => {{ const a = {q}.querySelector('a'); return [a.href, a.target, a.rel, a.innerText]; }})()")
        assert a == ["https://vd.example/tai-lieu", "_blank", "noopener noreferrer", "tài liệu"]
        chu = trang.chu(MD)
        assert "**" not in chu and "##" not in chu and "```" not in chu and "|---" not in chu, chu
        # dc-lite dựng lại cả trang mỗi phím gõ: câu trả lời đã dựng KHÔNG được mất (custom element tự giữ con).
        trang.go("#o-soan", "gõ dở")
        trang.cho("document.getElementById('o-soan').value === 'gõ dở'", mo_ta="ô soạn nhận chữ")
        assert trang.js(f"{q}.querySelectorAll('strong, table, pre').length") == 3
        trang.js("const o = document.getElementById('o-soan'); o.value = ''; o.dispatchEvent(new Event('input', {bubbles: true}))")
        # Tin người dùng vẫn là chữ thô.
        monkeypatch.setenv("GIA_KHUC", json.dumps(["Ổn."]))
        _gui(trang, "**không đậm**", 2)
        assert trang.chu("[data-vai='user']") == "Cho tôi một ví dụ"
        assert trang.js("document.querySelectorAll('[data-vai=\"user\"]')[1].innerText") == "**không đậm**"
        assert trang.js("document.querySelectorAll('[data-vai=\"user\"] strong').length") == 0
        assert trang.js(f"{q}.querySelectorAll('strong, table, pre').length") == 3   # lượt cũ vẫn nguyên sau lượt mới

        # Bảng rộng trên điện thoại: cuộn ngang TRONG khung bọc, trang không tràn ngang.
        trang.khung(390, 844, True)
        monkeypatch.setenv("GIA_KHUC", json.dumps(["| Thiết bị | Phòng | Trạng thái | Lần bật gần nhất | Ghi chú |\n|---|---|---|---|---|\n",
                                                   "| Đèn trần | Phòng khách | đang bật | 19:42 hôm qua | điều khiển bằng công tắc |\n"]))
        _gui(trang, "Bảng rộng", 3)
        bang = f"document.querySelectorAll({json.dumps(MD)})[2].querySelector('.md-bang')"
        assert trang.js(f"{bang}.scrollWidth > {bang}.clientWidth + 40"), "bảng rộng phải cuộn trong khung bọc"
        assert khong_tran(trang)
        trang.js(f"{bang}.scrollIntoView({{block: 'center'}})")
        trang.chup("studio-bang-rong-dien-thoai")
        trang.khung(1280, 800)

        # (2) HTML/URL độc trong câu trả lời: hiện thành chữ, không tạo phần tử nguy hiểm, không chạy.
        trang.bam("document.getElementById('nut-xoa')")
        monkeypatch.setenv("GIA_KHUC", json.dumps([
            "<script>window.__xss=1</script> ", '<img src=x onerror="window.__xss=2"> ',
            "[x](javascript:window.__xss=3) ", "[y](JavaScript:window.__xss=4) ", "[z](data:text/html,<b>5</b>) ",
            "![a](https://vd.example/a.png) <iframe src=\"javascript:window.__xss=6\"></iframe>"]))
        _gui(trang, "Thử", 1)
        assert trang.js(f"{q}.querySelectorAll('script, img, iframe, a, b, [onerror]').length") == 0
        chu = trang.chu(MD)
        for nguyen_van in ("<script>window.__xss=1</script>", "<img src=x onerror=", "[x](javascript:window.__xss=3)",
                           "[y](JavaScript:", "[z](data:text/html,<b>5</b>)", "![a](https://vd.example/a.png)", "<iframe"):
            assert nguyen_van in chu, (nguyen_van, chu)
        time.sleep(0.3)
        assert trang.js("window.__xss === undefined")

        # (6) Stream dở: «**» chưa đóng là chữ thường, khối ``` chưa đóng là khối mã đang mở — không văng lỗi JS.
        trang.bam("document.getElementById('nut-xoa')")
        monkeypatch.setenv("GIA_TRE", "0.5")
        monkeypatch.setenv("GIA_KHUC", json.dumps(["Đang **dở", " dang** rồi.\n\n```js\nlet a", " = 1\n```\nXong **hẳn**."]))
        trang.go("#o-soan", "Stream dở")
        trang.bam("document.getElementById('nut-gui')")
        trang.cho(f"({q}?.innerText || '') === 'Đang **dở'", mo_ta="khúc 1: ** chưa đóng")
        assert trang.js(f"{q}.querySelectorAll('strong').length") == 0
        trang.cho(f"!!{q}.querySelector('pre[data-dang-mo] code')", mo_ta="khúc 2: khối mã đang mở")
        assert trang.js(f"{q}.querySelector('pre code').textContent") == "let a"
        assert trang.js(f"{q}.querySelectorAll('strong').length") == 1
        trang.cho("document.getElementById('trang-thai')?.innerText === 'Xong.'", han=10, mo_ta="stream dở xong")
        assert trang.js(f"{q}.querySelector('pre code').textContent") == "let a = 1"
        assert not trang.js(f"{q}.querySelector('pre').hasAttribute('data-dang-mo')")
        assert trang.js(f"{q}.querySelectorAll('strong').length") == 2
        assert trang.js("window.__loiJs") == []
        assert not trang.js("!!document.getElementById('dc-loi')")


def _tep_gemini() -> dict:
    goc = Path(__file__).resolve().parents[1] / "modules" / "gemini-api"
    return {str(p.relative_to(goc)): p.read_bytes() for p in goc.rglob("*") if p.is_file() and "__pycache__" not in p.parts}


def test_studio_gan_gio_that_ngay_truoc_tin_cuoi(trang, tmp_path, monkeypatch):
    """Model không có đồng hồ: Studio gắn giờ của TRÌNH DUYỆT (múi giờ của nó, không viết chết Việt Nam) vào một tin
    system ngay trước tin người dùng cuối; đo ở thân yêu cầu mà Google giả NHẬN qua lõi + module gemini-api thật."""
    g = GoogleGia()
    g.hanh_vi[K_TOT] = {"kieu": "tot"}
    g.khuc_chat = ["Trả lời **một**."]
    with MayChuThat(g.app) as s_g, loi_that(tmp_path, monkeypatch, modules={"gemini-api": _tep_gemini()}) as w:
        lien_ket(w, "gemini-api", {"api_keys": [K_TOT], "base_url": s_g.url})
        trang.cdp.goi("Emulation.setTimezoneOverride", {"timezoneId": "America/New_York"}, trang.s)
        _mo_studio(trang, w)
        nam = trang.js("String(new Date().getFullYear())")
        _gui(trang, "Hôm nay thứ mấy?", 1)
        tin = [x for x in g.nhat_ky if x["duong"] == "chat"][-1]["than"]["messages"]
        assert [m["role"] for m in tin] == ["system", "user"] and tin[1]["content"] == "Hôm nay thứ mấy?", tin
        gio = tin[0]["content"]
        assert "America/New_York" in gio and nam in gio and "Việt Nam" not in gio, gio
        # Không hiện trong khung hội thoại, không nằm trong state (lịch sử) của trang.
        assert "Giờ hiện tại" not in trang.js("document.body.innerText")
        assert not any("Giờ hiện tại" in s for s in trang.state_thanh_phan())
        assert trang.chu(MD) == "Trả lời một." and trang.js(f"document.querySelectorAll({json.dumps(MD + ' strong')}).length") == 1

        # Lượt hai tính lại giờ (đồng hồ trình duyệt giả sang 02/01/2031 14:30 UTC = 09:30 ở New York), không dồn tin cũ.
        trang.js("(() => { const D = Date; const T = D.UTC(2031, 0, 2, 14, 30); window.Date = class extends D {"
                 " constructor(...a) { if (a.length) super(...a); else super(T); } static now() { return T; } }; })()")
        _gui(trang, "Còn bây giờ?", 2)
        tin2 = [x for x in g.nhat_ky if x["duong"] == "chat"][-1]["than"]["messages"]
        assert [m["role"] for m in tin2] == ["user", "assistant", "system", "user"], tin2
        assert [m["content"] for m in tin2 if m["role"] != "system"] == ["Hôm nay thứ mấy?", "Trả lời **một**.", "Còn bây giờ?"]
        assert "09:30" in tin2[2]["content"] and "02/01/2031" in tin2[2]["content"] and gio != tin2[2]["content"], tin2[2]
        assert "Giờ hiện tại" not in trang.js("document.body.innerText")


# Đồng hồ trình duyệt giả: 02/01/2031 14:30 UTC (thứ Năm) = 21:30 ở Việt Nam (+07:00) = 09:30 ở New York.
DONG_HO_2031 = ("(() => { const D = Date; const T = D.UTC(2031, 0, 2, 14, 30); window.Date = class extends D {"
                " constructor(...a) { if (a.length) super(...a); else super(T); } static now() { return T; } }; })()")


def test_studio_gio_theo_tz_cua_may_chu_khong_theo_trinh_duyet(trang, tmp_path, monkeypatch):
    """Chủ máy: «giờ hiện tại theo env». Lõi khai TZ=Asia/Ho_Chi_Minh, trình duyệt đặt ở New York → tin giờ mà Google
    giả NHẬN mang tên + độ lệch của Việt Nam và giờ Việt Nam (21:30), không phải giờ New York (09:30)."""
    g = GoogleGia()
    g.hanh_vi[K_TOT] = {"kieu": "tot"}
    g.khuc_chat = ["Được."]
    monkeypatch.setenv("TZ", "Asia/Ho_Chi_Minh")  # lõi đọc TZ lúc khởi động: đặt TRƯỚC loi_that
    with MayChuThat(g.app) as s_g, loi_that(tmp_path, monkeypatch, modules={"gemini-api": _tep_gemini()}) as w:
        lien_ket(w, "gemini-api", {"api_keys": [K_TOT], "base_url": s_g.url})
        trang.cdp.goi("Emulation.setTimezoneOverride", {"timezoneId": "America/New_York"}, trang.s)
        _mo_studio(trang, w)
        trang.js(DONG_HO_2031)
        _gui(trang, "Mấy giờ rồi?", 1)
        tin = [x for x in g.nhat_ky if x["duong"] == "chat"][-1]["than"]["messages"]
        assert [m["role"] for m in tin] == ["system", "user"], tin
        gio = tin[0]["content"]
        assert "Asia/Ho_Chi_Minh" in gio and "+07:00" in gio and "21:30" in gio and "Thứ Năm" in gio and "02/01/2031" in gio, gio
        assert "America/New_York" not in gio and "09:30" not in gio, gio


@pytest.mark.parametrize("mui_gio", ["Không/Có", "localtime"])
def test_studio_mui_gio_intl_khong_nhan_thi_lui_ve_trinh_duyet(trang, tmp_path, monkeypatch, mui_gio):
    """`mui_gio` tới trình duyệt mà Intl không nhận (tên lạ; «localtime» zoneinfo nhận nhưng Intl thì không) → lùi về
    múi giờ của trình duyệt, không lỗi JS, lượt chat vẫn đi. Ghi đè thẳng trên sổ để vượt bộ kiểm của lõi."""
    g = GoogleGia()
    g.hanh_vi[K_TOT] = {"kieu": "tot"}
    g.khuc_chat = ["Được."]
    with MayChuThat(g.app) as s_g, loi_that(tmp_path, monkeypatch, modules={"gemini-api": _tep_gemini()}) as w:
        app.state.so.mui_gio = mui_gio
        lien_ket(w, "gemini-api", {"api_keys": [K_TOT], "base_url": s_g.url})
        trang.cdp.goi("Emulation.setTimezoneOverride", {"timezoneId": "America/New_York"}, trang.s)
        _mo_studio(trang, w)
        assert trang.js("window.Khung.taiTinhNang().then((j) => j.mui_gio)") == mui_gio
        trang.js(BAT_LOI_JS)
        trang.js(DONG_HO_2031)
        _gui(trang, "Mấy giờ rồi?", 1)
        gio = [x for x in g.nhat_ky if x["duong"] == "chat"][-1]["than"]["messages"][0]["content"]
        assert "America/New_York" in gio and "09:30" in gio and "02/01/2031" in gio and mui_gio not in gio, gio
        assert trang.js("window.__loiJs") == []  # BAT_LOI_JS (khai ở trên, dùng chung với ca markdown)


def test_studio_anh_video_khong_gan_tin_gio(trang, tmp_path, monkeypatch):
    with loi_that(tmp_path, monkeypatch, modules={"ai-gia": module_ai()}) as w:
        lien_ket(w, "ai-gia", {"api_keys": [K_TOT]})
        _mo_studio(trang, w)
        trang.js("(() => { window.__than = []; const f = window.fetch; window.fetch = (u, o) => {"
                 " if (o && o.method === 'POST' && /(^|\\/)v1\\//.test(String(u))) window.__than.push([String(u), o.body]); return f(u, o); }; })()")
        trang.bam("document.querySelector('[data-che-do=\"anh\"]')")
        trang.go("#o-soan", "một chú mèo")
        trang.bam("document.getElementById('nut-gui')")
        trang.cho("document.querySelector('#ket-qua-anh img')", han=10, mo_ta="ảnh")
        trang.bam("document.querySelector('[data-che-do=\"video\"]')")
        trang.go("#o-soan", "sóng biển")
        trang.bam("document.getElementById('nut-gui')")
        trang.cho("document.querySelector('#video-kq video')", han=20, mo_ta="video")
        than = {u: json.loads(b) for u, b in trang.js("window.__than")}
        assert set(than) == {"v1/images/generations", "v1/videos"}, than
        assert than["v1/images/generations"]["prompt"] == "một chú mèo" and than["v1/videos"] == {"model": "gia/phim-1", "prompt": "sóng biển"}
        assert not any("messages" in b or "Giờ hiện tại" in json.dumps(b, ensure_ascii=False) for b in than.values())


def test_thanh_he_thong_bang_xo_ai_va_thanh_duoi(trang, tmp_path, monkeypatch):
    """Thanh Hệ thống như demo: mục AI là bảng xổ chỉ gồm tab con ĐANG có (không Combo); điện thoại có thanh dưới."""
    with loi_that(tmp_path, monkeypatch, modules={"ai-gia": module_ai()}) as w:
        lien_ket(w, "ai-gia", {"api_keys": [K_TOT]})
        dang_nhap_quan_tri(trang, w.url)
        trang.di(w.url + "/He-thong.dc.html")
        trang.cho("document.querySelector('[data-nav=\"ai\"]')", mo_ta="mục AI")
        assert trang.js("document.querySelector('[data-nav=\"ai\"]').getAttribute('aria-expanded')") == "false"
        assert not trang.js("!!document.querySelector('[data-nav-con]')")
        trang.bam("document.querySelector('[data-nav=\"ai\"]')")
        trang.cho("document.querySelectorAll('[data-nav-con]').length > 0", mo_ta="bảng xổ AI")
        assert trang.js("[...document.querySelectorAll('[data-nav-con]')].map((a) => a.dataset.navCon)") == ["tai_khoan", "provider"]
        bam_chuot(trang, "document.querySelector('[data-nav-con=\"provider\"]')", "mục Provider trong bảng xổ AI")
        trang.cho("location.pathname.endsWith('/He-thong-AI.dc.html') && document.querySelector('x-dc')?.dataset.dc === 'san-sang'"
                  " && document.getElementById('bang-ai')?.dataset.muc === 'provider'", han=10,
                  mo_ta="tới tab Provider")
        trang.khung(390, 844, True)
        trang.cho("getComputedStyle(document.querySelector('nav.bn')).display !== 'none'", mo_ta="thanh dưới Hệ thống")
        # Thứ tự thanh Hệ thống của demo: «AI» (nút mở tờ Tài khoản / Provider) rồi «Module» ở chỗ ô «Tiện ích» của demo.
        assert trang.js("[...document.querySelectorAll('[data-nav-duoi]')].map((a) => a.dataset.navDuoi)") == ["ai", "module", "bao_mat"]
        trang.bam("document.querySelector('[data-nav-duoi=\"ai\"]')")
        trang.cho("document.querySelectorAll('[data-nav-con-duoi]').length === 2", mo_ta="tờ AI ở thanh dưới")
        assert trang.js("[...document.querySelectorAll('[data-nav-con-duoi]')].map((a) => a.dataset.navConDuoi)") == ["tai_khoan", "provider"]
        bam_chuot(trang, "document.querySelector('[data-nav-con-duoi=\"tai_khoan\"]')", "mục Tài khoản trên tờ ở thanh dưới")
        trang.cho("document.querySelector('x-dc')?.dataset.dc === 'san-sang' && document.getElementById('bang-ai')?.dataset.muc === 'tai_khoan'",
                  han=10, mo_ta="tới tab Tài khoản từ thanh dưới")
        assert khong_tran(trang)


# ───────────── loại lạ, mục `can`, một hàm đánh giá ─────────────

def test_loai_la_tab_module_va_muc_can_cung_lat_voi_khung(trang, tmp_path, monkeypatch):
    with loi_that(tmp_path, monkeypatch, modules={"mang-gia": module_mang()}) as w:
        lien_ket(w, "mang-gia")
        dang_nhap_quan_tri(trang, w.url)
        trang.di(w.url + "/He-thong.dc.html#module-mang-gia")
        trang.cho("document.getElementById('muc-module')", mo_ta="mục của module")
        # Tab riêng của module loại lạ nằm TRONG thanh Hệ thống (không ngang hàng Studio); chưa có Studio thì không có «Quay lại».
        assert trang.js("!document.querySelector('[data-nav=\"quay-lai\"]')")
        tab_md = trang.js("document.querySelector('[data-nav=\"m-mang-gia\"]')?.getAttribute('href')")
        assert tab_md == "He-thong-Module.dc.html?m=mang-gia", tab_md
        assert trang.js("[...document.querySelectorAll('#muc-module [data-muc]')].map((x) => x.dataset.muc)") == ["bang"]
        assert "«Tự động điều chỉnh» chưa mở" in trang.chu("#muc-module") and "chat" in trang.chu("#muc-module")
        assert not trang.js("!!document.querySelector('[data-muc=\"tu-dong\"]')")
        # Cài (CHƯA liên kết) một module chat: tab «chính» Studio của khung hiện ngay (phạm vi «đã cài»), còn mục
        # `can: ["chat"]` của module vẫn đợi chat ĐÃ LIÊN KẾT — cùng một hàm `thieu`, khác phạm vi khai trong điều kiện.
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
        # Thanh trên cùng chỉ có Studio: tab riêng của module đã chuyển vào thanh Hệ thống.
        assert nav(trang)[:1] == ["studio"] and "m-mang-gia" not in nav(trang), nav(trang)


def test_chi_tiet_khong_chen_html(trang, tmp_path, monkeypatch):
    mods = {"xss-gia": module_khong_khoa("xss-gia", XSS_TEN, "ai", "xss", mo_ta="<b>đậm</b><script>window.__xss=6</script>")}
    with loi_that(tmp_path, monkeypatch, modules=mods) as w:
        dang_nhap_quan_tri(trang, w.url)
        trang.di(w.url + "/He-thong.dc.html")
        trang.cho(o("xss-gia"), mo_ta="ô xss")
        trang.bam(o("xss-gia"))
        trang.cho("document.getElementById('chi-tiet-ten')", mo_ta="chi tiết")
        assert trang.chu("#chi-tiet-ten") == XSS_TEN
        assert not trang.js("window.__xss") and not trang.js("!!document.querySelector('#chi-tiet img, #chi-tiet b')")
        # Esc đóng bảng chi tiết, focus về ô.
        trang.js("document.getElementById('chi-tiet').dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}))")
        trang.cho("!document.getElementById('chi-tiet') && document.activeElement?.dataset.o === 'xss-gia'", mo_ta="đóng + focus")


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
        dang_nhap_quan_tri(trang, w.url)
        trang.di(w.url + "/He-thong.dc.html")
        # Chip lọc loại chỉ có khi đã đọc xong danh sách module (lõi rỗng thì không có hàng chip loại).
        trang.cho("document.querySelector('[data-loc-loai=\"tat-ca\"]')", mo_ta="chip loại")
        trang.bam("document.querySelector('[data-loc-loai=\"tat-ca\"]')")
        trang.cho("document.querySelectorAll('[data-o]').length === 4", mo_ta="4 ô")
        assert khong_tran(trang)
        # Ô là khung chứa nút chọn ô + nút hành động anh em (không lồng button trong button) — đo cả khung ô.
        cao_o = trang.js("[...document.querySelectorAll('[data-khu=\"app\"] .luoi-o > .o-mod')].map((b) => Math.round(b.getBoundingClientRect().height))")
        assert len(set(cao_o)) == 1, cao_o
        trang.bam(o("ai-gia"))
        trang.cho("document.getElementById('chi-tiet')", mo_ta="chi tiết")
        assert khong_tran(trang)
        if dt:  # như demo Tiện ích ở 390px: bảng chi tiết nằm DƯỚI lưới (không phải tờ nổi), cuộn tới khi chọn ô
            assert trang.js("getComputedStyle(document.getElementById('chi-tiet')).position") == "static"
            assert trang.js("document.getElementById('chi-tiet').getBoundingClientRect().top + scrollY"
                            " >= Math.max(...[...document.querySelectorAll('.o-mod')].map((e) => e.getBoundingClientRect().bottom + scrollY))")
        for duong in ("/He-thong-AI.dc.html", "/Studio.dc.html"):
            trang.di(w.url + duong)
            assert khong_tran(trang), duong
        # Nút đủ lớn để bấm bằng ngón tay (≥ 40 px) ở các nút chính.
        trang.di(w.url + "/He-thong.dc.html")
        thap = trang.js("[...document.querySelectorAll('button.nut, button.chip, button.nut-chu, button.nut-bt, a.nut-chu')].filter((b) => b.offsetParent)"
                        ".map((b) => b.getBoundingClientRect().height).filter((h) => h < 40)")
        assert not thap, thap


@pytest.mark.parametrize("rong,dien_thoai", [(1000, True), (1100, False)])
def test_moc_1023_thanh_dieu_huong_va_noi_dung_cung_moc(trang, tmp_path, monkeypatch, rong, dien_thoai):
    """Mốc điện thoại của demo là 1023px cho CẢ thanh điều hướng lẫn nội dung: ở 881–1023px thanh dưới hiện thì nội dung
    cũng theo bố cục điện thoại (lề 16px như demo) — trước đây nội dung đổi ở 880px nên khoảng ấy lệch nhau."""
    with loi_that(tmp_path, monkeypatch, modules={"ai-gia": module_ai()}) as w:
        lien_ket(w, "ai-gia", {"api_keys": [K_TOT]})
        trang.khung(rong, 800)
        dang_nhap_quan_tri(trang, w.url)
        for duong in ("/He-thong.dc.html", "/He-thong-AI.dc.html"):
            trang.di(w.url + duong)
            trang.cho("document.querySelector('x-dc')?.dataset.dc === 'san-sang'", mo_ta=duong)
            bn = trang.js("getComputedStyle(document.querySelector('nav.bn')).display") != "none"
            tab = trang.js("getComputedStyle(document.getElementById('thanh-tab')).display") != "none"
            le = trang.js("parseFloat(getComputedStyle(document.querySelector('main.pg')).paddingLeft)")
            assert (bn, tab, le) == ((True, False, 16) if dien_thoai else (False, True, 32)), (duong, bn, tab, le)
            assert khong_tran(trang), duong


def test_go_tren_o_bam_hai_lan(trang, tmp_path, monkeypatch):
    """Biểu tượng xoá trên ô (như dòng Tiện ích của demo): lần đầu chỉ đổi nền đỏ nhạt và báo «bấm lần nữa», lần hai mới gỡ."""
    with loi_that(tmp_path, monkeypatch, modules={"mang-gia": module_mang()}) as w:
        lien_ket(w, "mang-gia")
        dang_nhap_quan_tri(trang, w.url)
        trang.di(w.url + "/He-thong.dc.html")
        trang.bam("document.querySelector('[data-loc-loai=\"tat-ca\"]')")
        xoa = "document.querySelector('[data-go-o=\"mang-gia\"]')"
        trang.cho(xoa, han=15, mo_ta="biểu tượng xoá trên ô")
        assert trang.js(xoa + ".getAttribute('aria-label')") == "Gỡ Mạng giả"
        assert not trang.js(xoa + ".hasAttribute('data-hanh-dong')")       # nút hành động chính vẫn là [data-hanh-dong]
        trang.bam(xoa)
        trang.cho("(document.getElementById('tin-module')?.innerText || '').includes('Bấm biểu tượng xoá lần nữa')", mo_ta="hỏi lại")
        assert " do" in trang.js(xoa + ".className") and trang.js(o("mang-gia") + ".dataset.tt") == "dang-chay"
        trang.bam(xoa)
        trang.cho("!document.querySelector('[data-o=\"mang-gia\"]')", han=15, mo_ta="đã gỡ")
        assert "Đã gỡ «Mạng giả»" in trang.chu("#tin-module")
        assert not (tmp_path / "modules" / "mang-gia").exists()


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
        dang_nhap_quan_tri(trang, w.url)
        trang.di(w.url + "/He-thong.dc.html")
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
        monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")  # NV16: NguonGit ghim IP, máy chủ giả ở loopback
        dang_nhap_quan_tri(trang, w.url)
        trang.di(w.url + "/He-thong.dc.html")
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
