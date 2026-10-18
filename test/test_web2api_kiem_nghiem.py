"""BÀI KIỂM NGHIỆM CHÍNH: thêm/đổi/gỡ module qua git mà KHÔNG khởi động lại lõi.

Lõi chạy là MỘT tiến trình uvicorn THẬT (tiến trình con, không phải TestClient), khởi động một
lần với thư mục dữ liệu rỗng, nguồn git trỏ GitHub giả. Suốt bài: PID và thời điểm khởi động
của tiến trình (đọc /proc/<pid>/stat) không đổi, và một luồng gọi /health song song không lỗi lần nào.
"""

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx

from conftest import MOI_TRUONG_CHAN_MANG, cai_git_hai_luot
from gia import GitHubGia, GoogleGia, MayChuThat, cong_trong, tep_echo, tep_kho

GOC_WEB2API = Path(__file__).resolve().parents[1]
KHOA_QT = "khoa-quan-tri-kiem-nghiem-0123456789"
KHOA_GEMINI = "AIzaSyKIEMNGHIEM-000000000000000000009"
REPO = "TriTue2011/web2api"


def gio_khoi_dong(pid: int) -> str:
    # Trường 22 của /proc/<pid>/stat (sau tên lệnh trong ngoặc): thời điểm tiến trình bắt đầu, tính theo tick.
    return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]


def cho(dieu_kien, giay=20, mo_ta=""):
    han = time.monotonic() + giay
    while time.monotonic() < han:
        v = dieu_kien()
        if v:
            return v
        time.sleep(0.1)
    raise AssertionError(f"hết {giay} s chờ: {mo_ta}")


def tep_gemini() -> dict[str, bytes]:
    goc = GOC_WEB2API / "modules" / "gemini-api"
    return {str(p.relative_to(goc)): p.read_bytes() for p in goc.rglob("*")
            if p.is_file() and "__pycache__" not in p.parts}


def test_them_doi_go_module_qua_git_khong_khoi_dong_lai(tmp_path):
    gh = GitHubGia(REPO)
    g = GoogleGia()
    g.hanh_vi[KHOA_GEMINI] = {"kieu": "tot"}
    with MayChuThat(gh.app) as s_gh, MayChuThat(g.app) as s_g:
        cong = cong_trong()
        env = {k: v for k, v in os.environ.items() if not k.startswith("WEB2API_") and "proxy" not in k.lower()}
        env.update(MOI_TRUONG_CHAN_MANG)
        env.update({"WEB2API_DATA": str(tmp_path / "data"), "WEB2API_ADMIN_KEY": KHOA_QT,
                    "WEB2API_GITHUB_API": s_gh.url, "WEB2API_GIT_REPO": REPO,
                    "WEB2API_TEST_CHO_PHEP_LOOPBACK": "1"})  # NV16: NguonGit ghim IP, máy chủ giả ở loopback
        nhat_ky = tmp_path / "loi.log"
        with open(nhat_ky, "wb") as f:
            p = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--app-dir", str(GOC_WEB2API),
                                  "--host", "127.0.0.1", "--port", str(cong)], env=env, stdout=f, stderr=subprocess.STDOUT)
        url = f"http://127.0.0.1:{cong}"
        dung_lai = threading.Event()
        loi_health: list = []
        so_health = [0]
        try:
            def song():
                try:
                    return httpx.get(url + "/health", timeout=1).status_code == 200
                except httpx.HTTPError:
                    return False
            cho(song, mo_ta="lõi khởi động")
            pid, bat_dau = p.pid, gio_khoi_dong(p.pid)

            def hoi_health():
                with httpx.Client(base_url=url, timeout=5) as hc:
                    while not dung_lai.is_set():
                        try:
                            r = hc.get("/health")
                            if r.status_code != 200:
                                loi_health.append(r.status_code)
                        except httpx.HTTPError as e:
                            loi_health.append(repr(e))
                        so_health[0] += 1
                        time.sleep(0.05)
            luong = threading.Thread(target=hoi_health, daemon=True)
            luong.start()
            h = httpx.Client(base_url=url, headers={"Authorization": f"Bearer {KHOA_QT}"}, timeout=15)

            def tb():
                return h.get("/api/modules/thong-bao").json()

            def trang_thai(id_):
                return next((x["trang_thai"] for x in tb()["danh_sach"] if x["id"] == id_), None)

            def noi(model="echo/lap-lai"):
                r = h.post("/v1/chat/completions", json={"model": model, "messages": [{"role": "user", "content": "ơi"}]})
                assert r.status_code == 200, r.text
                return r.json()["choices"][0]["message"]["content"]

            # Khởi đầu: kho git rỗng, chưa có module nào.
            assert h.get("/api/nguon-git").json()["cau_hinh"]["repo"] == REPO  # điền sẵn từ WEB2API_GIT_REPO
            assert h.put("/api/nguon-git", json={"repo": REPO, "chu_ky_giay": 1}).status_code == 200
            cho(lambda: tb()["dong_bo"]["kiem_luc"], mo_ta="vòng nền thăm dò lần đầu")
            assert tb()["so_moi"] == 0 and h.get("/v1/models").json()["data"] == []

            # (a) đẩy echo → thông báo → cài → dùng được, có tab.
            gh.day(tep_kho({"echo": tep_echo("echo v1", "1.0.0")}))
            cho(lambda: trang_thai("echo") == "moi", mo_ta="thông báo echo mới")
            assert h.get("/api/modules").json()["thong_bao_so"] == 1
            assert cai_git_hai_luot(h, "echo").status_code == 200
            assert noi() == "echo v1: ơi"
            assert [x["id"] for x in h.get("/v1/models").json()["data"]] == ["echo/lap-lai"]
            ds = h.get("/api/modules").json()["modules"]
            assert ds[0]["tab_web"]["duong"] == "/m/echo/echo.dc.html"
            assert "echo v1" in httpx.get(url + "/m/echo/echo.dc.html").text

            # (b) bản 2 đổi hành vi → co_ban_moi → cài → hành vi mới; bản 3 hỏng → hoàn về bản 2.
            gh.day(tep_kho({"echo": tep_echo("echo v2", "2.0.0")}))
            cho(lambda: trang_thai("echo") == "co_ban_moi", mo_ta="thông báo bản mới")
            assert h.post("/api/nguon-git/cai/echo").status_code == 200
            assert noi() == "echo v2: ơi"
            gh.day(tep_kho({"echo": tep_echo("echo v3", "3.0.0", hong=True)}))
            cho(lambda: next((x for x in tb()["danh_sach"] if x["phien_ban_moi"] == "3.0.0"), None), mo_ta="thấy bản 3")
            r = h.post("/api/nguon-git/cai/echo")
            assert r.status_code == 422 and "hoàn về bản cũ" in r.json()["error"]["message"]
            assert noi() == "echo v2: ơi"

            # (c) đẩy module Gemini API của repo → cài → cấu hình khoá → dùng với Google giả.
            gh.day(tep_kho({"echo": tep_echo("echo v3", "3.0.0", hong=True), "gemini-api": tep_gemini()}))
            cho(lambda: trang_thai("gemini-api") == "moi", mo_ta="thông báo gemini-api")
            r = cai_git_hai_luot(h, "gemini-api")
            assert r.status_code == 200 and r.json()["cai"] is None  # cần khoá → chưa tự cài cấu hình
            r = h.post("/api/modules/gemini-api/cai",
                       json={"cau_hinh": {"api_keys": [KHOA_GEMINI], "base_url": s_g.url}})
            assert r.status_code == 200 and r.json()["ok"], r.text
            assert noi("gemini/gemini-2.5-flash") == "chào từ Google giả"
            assert [x for x in g.nhat_ky if "than" in x][-1]["than"]["model"] == "gemini-2.5-flash"  # sau khi thêm khoá lõi còn hỏi /models nền (NV102)

            # (d) tự cài + yêu cầu chữ ký: commit chưa ký không tự cài; commit đã ký đổi mã module ĐÃ cài cũng không
            # tự chạy (NV49 R-1) — giữ chờ duyệt, quản trị duyệt (danh sách «them» rỗng + SHA) thì mới chạy.
            assert h.put("/api/nguon-git", json={"repo": REPO, "chu_ky_giay": 1, "tu_dong": True,
                                                 "yeu_cau_chu_ky": True}).status_code == 200
            gh.day(tep_kho({"echo": tep_echo("echo v4", "4.0.0"), "gemini-api": tep_gemini()}), xac_minh=False)
            cho(lambda: any(x["phien_ban_moi"] == "4.0.0" and x["ghi_chu"] and "chữ ký" in x["ghi_chu"]
                            for x in tb()["danh_sach"]), mo_ta="ghi chú thiếu chữ ký")
            assert noi() == "echo v2: ơi"
            gh.day(tep_kho({"echo": tep_echo("echo v5", "5.0.0"), "gemini-api": tep_gemini()}), xac_minh=True)
            x5 = cho(lambda: next((x for x in tb()["danh_sach"] if x["phien_ban_moi"] == "5.0.0" and x["ghi_chu"]
                                   and "ĐỔI MÃ" in x["ghi_chu"]), None), mo_ta="bản đã ký được giữ chờ duyệt")
            assert noi() == "echo v2: ơi"
            r = h.post("/api/nguon-git/cai/echo", json={"chap_nhan_quyen_them": [], "sha_commit": x5["commit"]["sha"],
                                                       "xac_nhan_lan_2": True})  # NV50: duyệt cần xác nhận lần 2
            assert r.status_code == 200, r.text
            assert noi() == "echo v5: ơi"
            assert tb()["so_moi"] == 0

            # (e) gỡ từng module → mất khỏi /v1/models, /m/<id>/ 404.
            assert h.put("/api/nguon-git", json={"repo": REPO, "chu_ky_giay": 1, "tu_dong": False}).status_code == 200
            assert h.delete("/api/modules/echo").status_code == 200
            assert [x["id"].split("/")[0] for x in h.get("/v1/models").json()["data"]] == ["gemini", "gemini"]
            assert httpx.get(url + "/m/echo/echo.dc.html").status_code == 404
            assert trang_thai("echo") == "moi"  # còn trên git → lại hiện là module mới
            assert h.delete("/api/modules/gemini-api").status_code == 200
            assert h.get("/v1/models").json()["data"] == []

            dung_lai.set()
            luong.join(5)
            assert p.poll() is None and p.pid == pid and gio_khoi_dong(pid) == bat_dau
            assert loi_health == [] and so_health[0] > 20
        finally:
            dung_lai.set()
            p.terminate()
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait(5)
    log = nhat_ky.read_text(errors="replace")
    assert KHOA_GEMINI not in log and KHOA_QT not in log
    assert "Traceback" not in log, log[-3000:]
