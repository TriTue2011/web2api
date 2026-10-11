"""Trang «Module» (web/Cai-module.dc.html) chạy với LÕI THẬT — xác nhận hợp đồng giữa giao diện và mã lõi.

test_web2api_cai_module_ui.py đo trang với API GIẢ (gia_api_ui.py) chép theo mã lõi; tự nó không chứng
minh trang chạy với lõi thật. Ở đây đi trọn đường chủ máy sẽ đi, trên lõi thật (uvicorn, lifespan,
vòng nền git) + GitHub giả + Google giả:
  dán khoá quản trị → thấy thông báo gemini-api từ git → bấm cài → form cấu hình mở sẵn → dán khoá
  API (mỗi dòng một khoá) → Kiểm tra và lưu → kết quả từng khoá → Thử chat (stream) → sức khoẻ → gỡ.
Nguồn git lấy từ biến môi trường như docker-compose.yml đặt sẵn (không cấu hình tay).
"""

import json
from pathlib import Path

import pytest

from app import so_module
from app.main import app
from gia import GitHubGia, GoogleGia, MayChuThat, tep_kho
from test_web2api_cai_module_ui import _DONG_KQ, CHROME, _o, _the_module, trang, trinh_duyet  # noqa: F401 — fixture

GOC_GEMINI = Path(__file__).resolve().parents[1] / "modules" / "gemini-api"
KHOA_QT = "khoa-quan-tri-ui-that-0123456789abcd"
K_TOT = "AIzaSyUI-TOT-0000000000000000000000tot1"
K_XAU = "AIzaSyUI-XAU-0000000000000000000000xau2"
REPO = "TriTue2011/web2api"

pytestmark = pytest.mark.skipif(not Path(CHROME).exists(), reason="cần Chrome")


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
            trang.di(w.url + "/Cai-module.dc.html")
            trang.go("#o-khoa", KHOA_QT)
            trang.bam("document.getElementById('nut-luu-khoa')")
            trang.cho("document.querySelector('#khoa-quan-tri [data-co-khoa]')", mo_ta="khoá quản trị đã lưu")

            # Nguồn git đến từ biến môi trường; thông báo có gemini-api «mới», thanh trên có chấm báo.
            trang.cho("document.querySelector('[data-thong-bao=\"gemini-api\"]')", han=15, mo_ta="thông báo gemini-api")
            assert trang.chu("#so-thong-bao") == "1"
            assert trang.js("!!document.querySelector('#thanh-tab .cham')")
            assert REPO in trang.chu("#nguon-git")
            assert "Mới" in trang.chu("[data-thong-bao='gemini-api']")

            trang.bam("document.querySelector('[data-thong-bao=\"gemini-api\"] .nut-cai-git')")
            # Module cần khoá → cài (mã về máy) KHÔNG cần khoá, chưa liên kết; trang mở sẵn form thêm khoá.
            trang.cho(_the_module("gemini-api") + "?.querySelector('form.form-cai')", han=15, mo_ta="form cấu hình mở sẵn")
            assert "không cần khoá API" in trang.chu("#thong-bao .tin-cai-git")
            assert "chưa có khoá" in trang.chu("[data-da-cai='gemini-api']")  # đã cài, hiện ngay ở «Module đã cài»

            trang.go(_o("gemini-api", "api_keys"), f"{K_TOT}\n{K_XAU}")
            trang.go(_o("gemini-api", "base_url"), s_g.url)
            trang.bam(_the_module("gemini-api") + ".querySelector('.nut-cai')", "Kiểm tra và lưu")
            trang.cho(_the_module("gemini-api") + "?.querySelector('.dung-ngay')", han=15, mo_ta="khối Dùng ngay")
            dong = trang.js(_DONG_KQ.format('[data-module="gemini-api"] .ket-qua-cai li'))
            assert [d[0] for d in dong] == ["tot", "hong"], dong
            assert f"…{K_TOT[-4:]} · dùng được" in dong[0][1] and "2 model" in dong[0][1], dong
            assert f"…{K_XAU[-4:]} · không dùng được" in dong[1][1] and "HTTP 401" in dong[1][1], dong
            assert json.loads((tmp_path / "bi_mat" / "gemini-api.json").read_text())["api_keys"] == [K_TOT]

            # Thử chat (stream) qua /v1 thật → Google giả.
            trang.bam(_the_module("gemini-api") + ".querySelector('.nut-thu-chat')")
            trang.cho("document.getElementById('chat-model')?.value.startsWith('gemini/')", mo_ta="model gemini chọn sẵn")
            trang.js("document.getElementById('chat-model').value = 'gemini/gemini-2.5-flash';"
                     "document.getElementById('chat-model').dispatchEvent(new Event('change', {bubbles: true}))")
            trang.go("#chat-nhap", "Chào")
            trang.bam("document.getElementById('nut-gui')")
            trang.cho("(document.getElementById('chat-tt')?.innerText || '').startsWith('Xong')", han=15, mo_ta="chat xong")
            assert trang.chu("#tra-loi") == "MộtHai"
            gui = [x for x in g.nhat_ky if x["duong"] == "chat"][-1]
            assert gui["khoa"] == K_TOT and gui["than"]["model"] == "gemini-2.5-flash" and gui["than"]["stream"] is True

            # Sức khoẻ từ lõi thật: {ok, base_url, khoa: [... dang_nghi_den]} hiện đúng.
            the = "document.querySelector('[data-da-cai=\"gemini-api\"]')"
            trang.cho(the, mo_ta="gemini-api ở «Module đã cài»")
            trang.bam(the + ".querySelector('.nut-suc-khoe')")
            trang.cho(the + ".querySelector('.suc-khoe li')", mo_ta="kết quả sức khoẻ")
            sk = trang.js(_DONG_KQ.format('[data-da-cai="gemini-api"] .suc-khoe li'))
            assert len(sk) == 1 and sk[0][0] == "tot" and f"…{K_TOT[-4:]}" in sk[0][1], sk
            assert not any(x["duong"] == "chat" for x in g.nhat_ky[-1:])  # sức khoẻ không gọi chat

            # Không còn khoá API nào trên trang.
            html = trang.js("document.documentElement.outerHTML")
            assert K_TOT not in html and K_XAU not in html
            assert not any(K_TOT in s or K_XAU in s for s in trang.state_thanh_phan())

            # Gỡ → biến khỏi «đã cài»; module còn trên git nên lại thành thông báo «mới».
            trang.bam(the + ".querySelector('.nut-go')")
            trang.cho(the + ".querySelector('.xac-nhan-go')", mo_ta="xác nhận gỡ")
            trang.bam(the + ".querySelector('.nut-go-han')")
            trang.cho("!document.querySelector('[data-da-cai=\"gemini-api\"]')", mo_ta="đã gỡ")
            # Module còn trên git → máy chủ coi lại là «mới»; trang tải lại thông báo sau khi gỡ (đã sửa:
            # trước đó trang giữ danh sách cũ nên chấm báo không hiện lại).
            trang.cho("!!document.querySelector('#thanh-tab .cham')", mo_ta="chấm báo hiện lại sau khi gỡ")
            trang.cho("document.querySelector('[data-thong-bao=\"gemini-api\"]')", mo_ta="thông báo gemini-api lại hiện")
            tb = json.loads(trang.js(f"fetch('api/modules/thong-bao', {{headers: {{Authorization: 'Bearer {KHOA_QT}'}}}})"
                                     ".then((r) => r.text())"))
            assert [(x["id"], x["trang_thai"]) for x in tb["danh_sach"]] == [("gemini-api", "moi")]
            assert not (tmp_path / "bi_mat" / "gemini-api.json").exists()
    assert all(x["auth"] is None for x in gh.nhat_ky)  # kho public: đọc ẩn danh
