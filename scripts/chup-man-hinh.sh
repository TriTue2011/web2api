#!/usr/bin/env bash
# Chụp ảnh màn hình THẬT của web2api cho README — chạy lại bao nhiêu lần cũng được.
#
# Làm gì: trong MỘT tiến trình Python dựng lõi web2api tạm (uvicorn, thư mục dữ liệu tạm, khoá quản trị ngẫu nhiên),
# GitHub GIẢ (repo có module gemini-api thật của kho + hai module mẫu khác loại) và Google GIẢ (test/gia.py) — không
# gọi Internet, không có khoá thật nào; rồi điều khiển Chrome headless qua DevTools (test/ui_chung.py) đi đúng luồng
# người dùng và chụp ở hai khổ: máy tính 1280×800 và điện thoại 390×844 (giả lập thiết bị bằng DevTools):
#   trong        — lõi rỗng: Hệ thống → Module, khối «Kết nối với web2api»
#   nhieu-o      — đã dán khoá: lưới ô nhiều loại, nhiều trạng thái (Chưa cài · Có bản mới · Đang chạy; «Ghi chú
#                  (mẫu)» đặt tay mà cũng có trên git nên là «Có bản mới»)
#   chi-tiet     — bảng chi tiết «Cần gì để chạy» của gemini-api trước khi cài
#   sau-cai      — ngay sau khi cài gemini-api (chưa khoá): Studio với ô soạn bị khoá và một dòng «Chưa có khoá
#                  cho … — Thêm khoá» tới Hệ thống → AI → Tài khoản
#   ai-sau-cai   — cùng lúc ấy, Hệ thống → AI → Tài khoản (neo #tk-gemini-api): nhóm «API 0», biểu mẫu «Thêm API»
#   provider-rong — Hệ thống → AI → Provider khi chưa có khoá: «Model 0 | Tài khoản 0», khung rỗng + «Mở tab Tài khoản»
#   sau-lien-ket — sau khi liên kết nguồn AI đầu tiên (khoá GIẢ, Google giả): Hệ thống → AI → Provider › Model (model
#                  thật theo nhóm khả năng); nút «Quay lại» (về Studio), không có Combo
#   studio       — Studio: một lượt chat stream qua /v1 thật, câu trả lời markdown (in đậm, danh sách, mã)
# Lưu PNG vào docs/anh/<tên>-may-tinh.png và <tên>-dien-thoai.png, mỗi ảnh ≤ 250 KB (quá thì giảm màu).
#
# Dùng (từ gốc kho):  scripts/chup-man-hinh.sh
# Biến tuỳ chọn: CHROME (mặc định /usr/bin/google-chrome), PYTHON (mặc định .venv ở gốc kho, không có thì python3).
# Python đó cần requirements.txt + requirements-dev.txt (websockets, Pillow).
set -euo pipefail

GOC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # gốc kho web2api (tính theo vị trí script)
export WEB2API_CHROME="${CHROME:-/usr/bin/google-chrome}"
if [[ -z "${PYTHON:-}" ]]; then
  if [[ -x "$GOC/.venv/bin/python" ]]; then PYTHON="$GOC/.venv/bin/python"; else PYTHON=python3; fi
fi
TAM="$(mktemp -d /tmp/web2api-chup.XXXXXX)"
don_dep() { [[ "$TAM" == /tmp/web2api-chup.* ]] && rm -r -- "$TAM"; }
trap don_dep EXIT
# Chặn Internet cho cả lõi tạm: mọi yêu cầu không phải 127.0.0.1 vào proxy chết (như test/conftest.py).
export HTTP_PROXY=http://127.0.0.1:9 HTTPS_PROXY=http://127.0.0.1:9 ALL_PROXY=http://127.0.0.1:9 NO_PROXY=127.0.0.1,localhost
unset WEB2API_GIT_TOKEN WEB2API_ADMIN_KEY

"$PYTHON" - "$GOC" "$TAM" <<'EOF'
import base64, io, json, os, secrets, subprocess, sys, time, urllib.request
from pathlib import Path

goc, tam = Path(sys.argv[1]), Path(sys.argv[2])
sys.path[:0] = [str(goc / "test"), str(goc)]
ra = goc / "docs" / "anh"
ra.mkdir(parents=True, exist_ok=True)
du_lieu = tam / "data"
(du_lieu / "modules").mkdir(parents=True)
khoa_qt = "chup-" + secrets.token_hex(16)
os.environ.update(WEB2API_DATA=str(du_lieu), WEB2API_ADMIN_KEY=khoa_qt, WEB2API_MAY_RAM_MB="8192", WEB2API_MAY_CPU="4")

import httpx
from PIL import Image
from gia import GitHubGia, GoogleGia, MayChuThat, ghi_thu_muc, tep_kho
from ui_chung import CDP, CHROME, Trang, _cong_trong
from ui_mau import module_khong_khoa, module_mang

KHOA_GIA = "AIzaSyUI-TOT-0000000000000000000000tot1"  # chuỗi GIẢ có trong allowlist .gitleaks.toml
gem_goc = goc / "modules" / "gemini-api"
gem = {str(p.relative_to(gem_goc)): p.read_bytes() for p in gem_goc.rglob("*") if p.is_file() and "__pycache__" not in p.parts}
ghi_chu = module_khong_khoa("ghi-chu", "Ghi chú (mẫu)", "app", "ghichu", loai_ten="App", loai_thu_tu=20,
                            mo_ta="Module mẫu loại «App» để minh hoạ lưới nhiều loại.")
gh = GitHubGia("TriTue2011/web2api")
gh.day(tep_kho({"gemini-api": gem, "mang-gia": module_mang(), "ghi-chu": ghi_chu}))
g = GoogleGia()
g.hanh_vi[KHOA_GIA] = {"kieu": "tot"}
# Câu trả lời stream của Google giả có in đậm, danh sách, mã — ảnh Studio cho thấy markdown được dựng thành định dạng.
g.khuc_chat = ["Chào bạn! Tôi là **trợ lý AI** ", "trả lời qua web2api.\n\n", "Tôi có thể giúp bạn:\n\n",
               "- **Tóm tắt** một đoạn văn dài\n", "- Soạn thư trả lời khách hàng\n",
               "- Giải thích khái niệm kèm ví dụ, như `GET /v1/models`\n\n", "Bạn muốn bắt đầu từ việc nào?"]

from app import so_module
so_module.THU_MUC_ANH = du_lieu / "anh-rong"
from app.main import app

anh = []

with MayChuThat(gh.app) as s_gh, MayChuThat(g.app) as s_g:
    os.environ.update(WEB2API_GITHUB_API=s_gh.url, WEB2API_GIT_REPO="TriTue2011/web2api")
    with MayChuThat(app, lifespan="on") as w:
        QT = {"Authorization": f"Bearer {khoa_qt}"}
        cong = _cong_trong()
        p = subprocess.Popen([CHROME, "--headless=new", "--disable-gpu", "--no-sandbox", "--no-proxy-server", "--hide-scrollbars",
                              f"--remote-debugging-port={cong}", f"--user-data-dir={tam / 'chrome'}", "--no-first-run",
                              "--host-resolver-rules=MAP fonts.googleapis.com ~NOTFOUND, MAP fonts.gstatic.com ~NOTFOUND",
                              "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        mo = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for _ in range(200):
            try:
                ws = json.loads(mo.open(f"http://127.0.0.1:{cong}/json/version", timeout=1).read())["webSocketDebuggerUrl"]
                break
            except OSError:
                time.sleep(0.05)
        cdp = CDP(ws)
        try:
            tid = cdp.goi("Target.createTarget", {"url": "about:blank"})["targetId"]
            t = Trang(cdp, cdp.goi("Target.attachToTarget", {"targetId": tid, "flatten": True})["sessionId"])

            def chup(ten: str, chuan_bi):
                for hau_to, rong, cao, dt in (("may-tinh", 1280, 800, False), ("dien-thoai", 390, 844, True)):
                    t.khung(rong, cao, dt)
                    chuan_bi()
                    time.sleep(0.4)
                    r = cdp.goi("Page.captureScreenshot", {"format": "png"}, t.s)
                    tep = ra / f"{ten}-{hau_to}.png"
                    tep.write_bytes(base64.b64decode(r["data"]))
                    anh.append(tep)

            def mo_trang(duong: str, cho: str = "true"):
                t.cdp.goi("Page.navigate", {"url": w.url + duong}, t.s)
                t.cho("document.querySelector('x-dc')?.dataset.dc === 'san-sang' && (" + cho + ")", han=20, mo_ta=duong)

            # 1. Lõi rỗng.
            chup("trong", lambda: mo_trang("/", "location.pathname.endsWith('He-thong.dc.html') && !!document.getElementById('ket-noi')"))
            # 2. Nhiều ô nhiều loại: «Mạng (mẫu)» cài + liên kết, «Ghi chú (mẫu)» cài chưa liên kết, gemini-api chỉ trên git.
            han = time.monotonic() + 20
            while time.monotonic() < han and len(httpx.get(f"{w.url}/api/modules/thong-bao", headers=QT).json()["danh_sach"]) < 3:
                time.sleep(0.2)
            assert httpx.post(f"{w.url}/api/nguon-git/cai/mang-gia", headers=QT, timeout=30).status_code == 200
            ghi_thu_muc(du_lieu / "modules" / "ghi-chu", ghi_chu)  # đặt tay (không .nguon.json) → «Có bản mới»
            httpx.post(f"{w.url}/api/modules/nap-lai", headers=QT, timeout=30).raise_for_status()
            t.js(f"localStorage.setItem('web2api_khoa', {json.dumps(khoa_qt)})")

            def nhieu_o():
                mo_trang("/He-thong.dc.html", "document.querySelectorAll('[data-loc-loai]').length >= 4")
                t.bam("document.querySelector('[data-loc-loai=\"tat-ca\"]')")
                t.cho("document.querySelectorAll('[data-o]').length >= 3", mo_ta="ba ô")
                t.js("document.getElementById('loc-loai').scrollIntoView({block: 'start'}); window.scrollBy(0, -70)")
            chup("nhieu-o", nhieu_o)

            # 3. Bảng chi tiết «Cần gì để chạy» của gemini-api (trước khi cài).
            def chi_tiet():
                mo_trang("/He-thong.dc.html?c=1#module-gemini-api", "!!document.getElementById('can-gi')")
                t.js("document.getElementById('chi-tiet').scrollIntoView({block: 'start'}); window.scrollBy(0, -70)")
            chup("chi-tiet", chi_tiet)

            # 4. Cài gemini-api (chưa khoá) → trang Module chuyển sang Studio?da_cai=… : trạng thái rỗng «Chưa có khoá …».
            assert httpx.post(f"{w.url}/api/nguon-git/cai/gemini-api", headers=QT, timeout=30).status_code == 200
            chup("sau-cai", lambda: mo_trang("/Studio.dc.html?da_cai=gemini-api", "!!document.getElementById('studio-chua-khoa')"))
            # 4b. Hệ thống → AI ngay sau khi cài: Tài khoản (biểu mẫu Thêm API của đúng module) và Provider rỗng.
            def ai_sau_cai():
                mo_trang("/He-thong-AI.dc.html?a=1#tk-gemini-api", "!!document.querySelector('#form-them input')")
                t.js("document.activeElement.blur()")
            chup("ai-sau-cai", ai_sau_cai)
            chup("provider-rong", lambda: mo_trang("/He-thong-AI.dc.html?a=2#provider", "!!document.getElementById('pv-trong')"))

            # 5. Liên kết nguồn AI đầu tiên (khoá GIẢ, Google giả) → Hệ thống → AI → Provider.
            r = httpx.post(f"{w.url}/api/modules/gemini-api/cai", headers=QT, timeout=30,
                           json={"cau_hinh": {"api_keys": [KHOA_GIA], "base_url": s_g.url}})
            assert r.json()["ok"], r.text
            chup("sau-lien-ket", lambda: mo_trang("/He-thong-AI.dc.html?a=3#provider", "document.querySelectorAll('[data-model]').length > 0"))

            # 6. Studio: một lượt chat stream.
            def studio():
                mo_trang("/Studio.dc.html", "document.querySelectorAll('[data-model-chon]').length > 0")
                t.go("#o-soan", "Chào bạn, bạn là ai?")
                t.bam("document.getElementById('nut-gui')")
                t.cho("document.getElementById('trang-thai')?.innerText === 'Xong.'", han=20, mo_ta="chat xong")
                t.js("document.activeElement.blur()")
            chup("studio", studio)
        finally:
            cdp.dong()
            p.kill()
            p.wait(10)

# Trần 250 KB/ảnh: quá thì giảm về bảng 256 màu (chữ vẫn nét, nền phẳng nén tốt).
TRAN = 250 * 1024
hong = False
for tep in anh:
    if tep.stat().st_size > TRAN:
        Image.open(tep).convert("RGB").quantize(256).save(tep, optimize=True)
    co = tep.stat().st_size
    rong, cao = Image.open(tep).size
    print(f"{tep.name}: {rong}x{cao}, {co // 1024} KB" + ("  << VƯỢT 250 KB" if co > TRAN else ""))
    hong |= co > TRAN
sys.exit(1 if hong else 0)
EOF
