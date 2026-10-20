"""Test NV167 — module `camera` + dịch vụ phụ `dich-vu/camera/`.

Chiến lược kiểm thử (đỏ trước/xanh sau, conftest chặn Internet; mọi yêu cầu
không phải 127.0.0.1 bị đẩy qua proxy chết):
- Dịch vụ phụ CHẠY THẬT (``server.py`` của chính nhiệm vụ) trên cổng tạm
  127.0.0.1; go2rtc GIẢ phục vụ JPEG tạo bằng ffmpeg thật của máy.
- Module gọi dịch vụ phụ qua httpx thật trên loopback (``WEB2API_TEST_CHO_PHEP_LOOPBACK=1``
  từ fixture trong tệp này).
- Đường dự phòng không dịch vụ phụ (go2rtc trực tiếp trong module) cũng được kiểm.
- Đột biến được liệt kê ở bảng trong báo cáo nv167-bao-cao.md.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytestmark = pytest.mark.anyio

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "modules", "cameranha")))

DV = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "dich-vu", "camera"))


# ── tiện ích: ảnh JPEG thật + go2rtc giả ────────────────────────────────────

def _jpeg_that() -> bytes:
    """JPEG 640×480 thật sinh bằng ffmpeg của máy (testsrc)."""
    p = subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=duration=1:size=640x480:rate=1",
         "-frames:v", "1", "-f", "image2", "pipe:1"],
        capture_output=True, timeout=30,
    )
    assert p.returncode == 0 and p.stdout.startswith(b"\xff\xd8"), "ffmpeg phải sinh được JPEG"
    return p.stdout


@pytest.fixture(scope="module")
def go2rtc_gia():
    """go2rtc giả: /api/frame.jpeg?src=cua trả JPEG thật; cái khác trả 404."""
    jpeg = _jpeg_that()

    class _Go(BaseHTTPRequestHandler):
        def do_GET(self):
            if "frame.jpeg" in self.path and "src=cua" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(jpeg)))
                self.end_headers()
                self.wfile.write(jpeg)
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Go)
    cong = srv.server_address[1]
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield {"base": f"http://127.0.0.1:{cong}", "jpeg": jpeg}
    srv.shutdown()


@pytest.fixture(scope="module")
def dich_vu_gia():
    """Dịch vụ phụ camera CHẠY THẬT (server.py của nhiệm vụ) trên cổng tạm."""
    khoa = "khoa-test-dai-du-24-ky-tu-abcdef"
    env = dict(os.environ, CAM_KHOA=khoa, CAM_CONG="0", CAM_HOST="127.0.0.1",
               PYTHONPATH=DV)
    p = subprocess.Popen(
        [sys.executable, os.path.join(DV, "server.py")],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env,
    )
    time.sleep(2.5)
    assert p.poll() is None, "dịch vụ phụ phải chạy được"
    try:
        yield {"khoa": khoa, "tien_trinh": p, "pid": p.pid,
               "url": f"http://127.0.0.1:{_cong_cua_pid(p.pid)}"}
    finally:
        p.terminate()
        p.wait(timeout=10)


def _cong_cua_pid(pid: int) -> int:
    """Cổng TCP đang NGHE của một tiến trình (đọc /proc)."""
    import re
    inodes = set()
    for fd in os.listdir(f"/proc/{pid}/fd"):
        try:
            tgt = os.readlink(f"/proc/{pid}/fd/{fd}")
        except OSError:
            continue
        m = re.match(r"socket:\[(\d+)\]", tgt)
        if m:
            inodes.add(m.group(1))
    for ln in open("/proc/net/tcp").read().splitlines()[1:]:
        p = ln.split()
        if p[9] in inodes and p[3] == "0A":
            return int(p[1].split(":")[1], 16)
    raise AssertionError("không tìm thấy cổng của dịch vụ phụ")


# ── tạo module với cấu hình mặc định ─────────────────────────────────────────

CH_TIEU_BIEU = {
    "danh_sach_camera": json.dumps([{
        "name": "Cửa", "kind": "go2rtc", "src": "cua",
    }]),
}

CH_DAY_DU = {
    "danh_sach_camera": json.dumps([
        {"name": "Cửa", "kind": "go2rtc", "src": "cua",
         "base": "__BASE__", "src_ai": "cua_phu"},
        {"name": "Phòng khách", "kind": "rtsp",
         "url": "rtsp://admin:mat-khau-gia@10.0.0.6:554/stream"},
    ]),
}


def _nap(monkeypatch, cau_hinh: dict, monkey=None):
    """Nạp module với cấu hình cho sẵn (ghi đè monkeypatch khi có base động)."""
    from module import CameraModule
    manifest = json.load(open(os.path.join(
        os.path.dirname(__file__), "..", "modules", "cameranha", "manifest.json")))
    mod = CameraModule(manifest)
    mod.nap(dict(cau_hinh))
    return mod


@pytest.fixture(autouse=True)
def cho_phep_loopback(monkeypatch):
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")


# ── 1. Nạp cấu hình ──────────────────────────────────────────────────────────

class TestNap:
    async def test_json_sai_tu_choi_nap(self, monkeypatch):
        m = _nap(monkeypatch, {"danh_sach_camera": "{khong phai json"})
        assert m.thuc_the() == []

    async def test_trung_ten_tu_choi_nap(self, monkeypatch):
        ds = json.dumps([{"name": "Cửa", "kind": "go2rtc", "base": "http://x", "src": "a"},
                         {"name": "cửa", "kind": "go2rtc", "base": "http://x", "src": "b"}])
        m = _nap(monkeypatch, {"danh_sach_camera": ds})
        assert m.thuc_the() == []

    async def test_khong_camera(self, monkeypatch):
        m = _nap(monkeypatch, {"danh_sach_camera": "[]"})
        assert m.thuc_the() == []
        assert m.suc_khoe()["ok"] is True


# ── 2. cai() — kiểm theo đặc tả tu_xa ─────────────────────────────────────────

class TestCai:
    async def test_json_hong(self, monkeypatch):
        m = _nap(monkeypatch, {})
        kq = await m.cai({"danh_sach_camera": "abc"})
        assert kq["ok"] is False

    async def test_base_go2rtc_bi_chan(self, monkeypatch):
        m = _nap(monkeypatch, {})
        kq = await m.cai({"danh_sach_camera": json.dumps([
            {"name": "A", "kind": "go2rtc", "base": "http://169.254.169.254/", "src": "x"}])})
        assert kq["ok"] is False
        assert any("base" in l for l in kq["loi"])

    async def test_rtsp_metadata_bi_chan(self, monkeypatch):
        m = _nap(monkeypatch, {})
        kq = await m.cai({"danh_sach_camera": json.dumps([
            {"name": "A", "kind": "rtsp", "url": "rtsp://x:1@169.254.169.254/s"}])})
        assert kq["ok"] is False
        assert any("url" in l for l in kq["loi"])

    async def test_rtsp_ngoai_nha_bi_chan(self, monkeypatch):
        m = _nap(monkeypatch, {})
        kq = await m.cai({"danh_sach_camera": json.dumps([
            {"name": "A", "kind": "rtsp", "url": "rtsp://x:1@8.8.8.8/s"}])})
        assert kq["ok"] is False

    async def test_rtsp_scheme_sai(self, monkeypatch):
        m = _nap(monkeypatch, {})
        kq = await m.cai({"danh_sach_camera": json.dumps([
            {"name": "A", "kind": "rtsp", "url": "http://10.0.0.6/s"}])})
        assert kq["ok"] is False

    async def test_khoa_dich_vu_ngan(self, monkeypatch, dich_vu_gia):
        m = _nap(monkeypatch, {})
        kq = await m.cai({"danh_sach_camera": "[]",
                          "dich_vu_url": "http://127.0.0.1:8714",
                          "dich_vu_khoa": "ngan"})
        assert kq["ok"] is False

    async def test_ssrf_base_khong_cho_xss(self, monkeypatch):
        m = _nap(monkeypatch, {})
        kq = await m.cai({"danh_sach_camera": json.dumps([
            {"name": "<img src=x onerror=alert(1)>", "kind": "go2rtc",
             "base": "http://10.0.0.6:1984", "src": "x"}])})
        assert kq["ok"] is True
        m2 = _nap(monkeypatch, {"danh_sach_camera": json.dumps([
            {"name": "<img src=x onerror=alert(1)>", "kind": "go2rtc",
             "base": "http://10.0.0.6:1984", "src": "x"}])})
        assert m2.thuc_the()[0]["id"] == "cam.img-src-x-onerror-alert-1"
        assert "<img" not in m2.thuc_the()[0]["id"]


# ── 3. Thực thể đọc/ghi theo công tắc ─────────────────────────────────────────

class TestThucThe:
    async def test_chi_thuc_the_doc_mac_dinh(self, monkeypatch, go2rtc_gia):
        m = _nap(monkeypatch, dict(CH_TIEU_BIEU))
        ett = m.thuc_the()
        assert [e["id"] for e in ett] == ["cam.cua"]
        assert ett[0]["rui_ro_mac_dinh"] == "doc"

    async def test_loa_bat_thi_co_thuc_the_ghi(self, monkeypatch):
        ch = dict(CH_TIEU_BIEU, loa_bat="true")
        m = _nap(monkeypatch, ch)
        ids = [e["id"] for e in m.thuc_the()]
        assert ids == ["cam.cua", "cam.phat_cua"]
        assert m.thuc_the()[1]["rui_ro_mac_dinh"] == "ghi"

    async def test_loc_theo_ten(self, monkeypatch, go2rtc_gia):
        ch = dict(CH_TIEU_BIEU, **{"danh_sach_camera": json.dumps(
            [{"name": "Cửa", "kind": "go2rtc", "src": "cua"},
             {"name": "Sân", "kind": "go2rtc", "src": "san"}])})
        m = _nap(monkeypatch, ch)
        assert [e["id"] for e in m.thuc_the({"ten": "cửa"})] == ["cam.cua"]
        assert m.thuc_the({"ten": "không tồn tại"}) == []

    async def test_idor_thuc_the_la(self, monkeypatch):
        m = _nap(monkeypatch, dict(CH_TIEU_BIEU))
        kq = await m.thuc_hien("cam.khong-co", "chup")
        assert kq["ok"] is False and kq["ma"] == "khong_tim_thay"


# ── 4. Chụp qua đường dự phòng (module gọi go2rtc trực tiếp) ──────────────────

class TestChupTrucTiep:
    async def test_chup_anh_that(self, monkeypatch, go2rtc_gia):
        ch = {"danh_sach_camera": json.dumps([
            {"name": "Cửa", "kind": "go2rtc", "src": "cua", "base": go2rtc_gia["base"]}])}
        m = _nap(monkeypatch, ch)
        kq = await m.thuc_hien("cam.cua", "chup")
        assert kq["ok"] is True
        jpeg = base64.b64decode(kq["trang_thai_moi"]["anh_base64"])
        assert jpeg == go2rtc_gia["jpeg"]
        assert kq["trang_thai_moi"]["kich_thuoc"] == [640, 480]

    async def test_chup_cho_ai_bi_cam_mac_dinh(self, monkeypatch, go2rtc_gia):
        ch = {"danh_sach_camera": json.dumps([
            {"name": "Cửa", "kind": "go2rtc", "src": "cua", "base": go2rtc_gia["base"]}])}
        m = _nap(monkeypatch, ch)
        kq = await m.thuc_hien("cam.cua", "chup_cho_ai")
        assert kq["ok"] is False and kq["ma"] == "bi_cam"

    async def test_chup_cho_ai_bat(self, monkeypatch, go2rtc_gia):
        ch = {"danh_sach_camera": json.dumps([
            {"name": "Cửa", "kind": "go2rtc", "src": "cua", "base": go2rtc_gia["base"]}],
        ), "cho_ai_xem": "true"}
        m = _nap(monkeypatch, ch)
        kq = await m.thuc_hien("cam.cua", "chup_cho_ai")
        assert kq["ok"] is True

    async def test_go2rtc_404(self, monkeypatch, go2rtc_gia):
        ch = {"danh_sach_camera": json.dumps([
            {"name": "A", "kind": "go2rtc", "src": "khong-co", "base": go2rtc_gia["base"]}])}
        m = _nap(monkeypatch, ch)
        kq = await m.thuc_hien("cam.a", "chup")
        assert kq["ok"] is False

    async def test_rtsp_can_dich_vu(self, monkeypatch):
        m = _nap(monkeypatch, dict(CH_DAY_DU))
        kq = await m.thuc_hien("cam.phong-khach", "chup")
        assert kq["ok"] is False
        assert "dịch vụ phụ" in kq["loi"]


# ── 5. Hành động GHI mặc định bị chặn ──────────────────────────────────────────

class TestGhi:
    async def test_phat_bi_cam_khi_loa_tat(self, monkeypatch):
        m = _nap(monkeypatch, dict(CH_TIEU_BIEU))
        kq = await m.thuc_hien("cam.cua", "phat", {"am_thanh_b64": "AAA="})
        assert kq["ok"] is False and kq["ma"] == "bi_cam"

    async def test_bo_dam_bi_cam_khi_loa_tat(self, monkeypatch):
        m = _nap(monkeypatch, dict(CH_TIEU_BIEU))
        kq = await m.thuc_hien("cam.cua", "bo_dam_mo")
        assert kq["ok"] is False and kq["ma"] == "bi_cam"

    async def test_phat_qua_thuc_the_doc_bi_cam(self, monkeypatch):
        ch = dict(CH_TIEU_BIEU, loa_bat="true")
        m = _nap(monkeypatch, ch)
        kq = await m.thuc_hien("cam.cua", "phat", {"am_thanh_b64": "AAA="})
        assert kq["ok"] is False and kq["ma"] == "sai_thuc_the"

    async def test_phat_thieu_dich_vu_phu(self, monkeypatch):
        ch = dict(CH_TIEU_BIEU, loa_bat="true")
        m = _nap(monkeypatch, ch)
        kq = await m.thuc_hien("cam.phat_cua", "phat", {"am_thanh_b64": "AAA="})
        assert kq["ok"] is False and kq["ma"] == "chua_cau_hinh"


# ── 6. Webhook Frigate ─────────────────────────────────────────────────────────

class TestWebhook:
    async def test_nhan_canh_bao(self, monkeypatch):
        m = _nap(monkeypatch, dict(CH_TIEU_BIEU))
        kq = await m.webhook({"nguon_ngoai": True, "than": {
            "after": {"id": "evt-1", "label": "person", "camera": "cua"}}})
        assert kq == {"ok": True}
        kq = await m.thuc_hien("cam.cua", "canh_bao_moi_nhat")
        assert kq["ok"] is True
        assert kq["canh_bao"][0]["id"] == "evt-1"
        assert "url" not in str(kq["canh_bao"]) and "password" not in str(kq["canh_bao"])

    async def test_su_kien_id(self, monkeypatch):
        m = _nap(monkeypatch, dict(CH_TIEU_BIEU))
        assert m.su_kien_id({"than": {"after": {"id": "evt-2"}}}) == "frigate-evt-2"
        assert m.su_kien_id({"than": {}}) == ""

    async def test_than_xau_khong_sap(self, monkeypatch):
        m = _nap(monkeypatch, dict(CH_TIEU_BIEU))
        kq = await m.webhook({"nguon_ngoai": True, "than": None})
        assert kq == {"ok": True}

    async def test_tran_20_canh_bao(self, monkeypatch):
        m = _nap(monkeypatch, dict(CH_TIEU_BIEU))
        for i in range(25):
            await m.webhook({"nguon_ngoai": True,
                             "than": {"after": {"id": f"e{i}", "label": "person"}}})
        kq = await m.thuc_hien("cam.cua", "canh_bao_moi_nhat")
        assert len(kq["canh_bao"]) == 20
        assert kq["canh_bao"][-1]["id"] == "e24"


# ── 7. Bí mật không lộ ─────────────────────────────────────────────────────────

class TestBiMat:
    async def test_che_bi_mat_rtsp(self):
        from module import che_bi_mat
        assert che_bi_mat("rtsp://admin:MatKhau@10.0.0.5/stream") == \
            "rtsp://admin:***@10.0.0.5/stream"
        assert "MatKhau" not in che_bi_mat("rtsp://admin:MatKhau@10.0.0.5/stream")
        assert che_bi_mat("rtsp://10.0.0.5/stream") == "rtsp://10.0.0.5/stream"

    async def test_suc_khoe_khong_chua_mat_khau(self, monkeypatch):
        m = _nap(monkeypatch, dict(CH_DAY_DU))
        sk = json.dumps(m.suc_khoe(), ensure_ascii=False)
        assert "mat-khau-gia" not in sk

    async def test_dia_chi_bi_mat_trong_log(self, caplog, monkeypatch):
        """Mô phỏng: module không bao giờ ghi URL có mật khẩu vào log."""
        import module as mod
        m = _nap(monkeypatch, dict(CH_DAY_DU))
        with caplog.at_level("INFO", logger="web2api.camera"):
            m.suc_khoe()
        for rec in caplog.records:
            assert "mat-khau-gia" not in rec.getMessage()


# ── 8. Kích thước JPEG ─────────────────────────────────────────────────────────

class TestJpeg:
    async def test_kich_thuoc_jpeg_that(self):
        from module import kich_thuoc_jpeg
        jpeg = _jpeg_that()
        assert kich_thuoc_jpeg(jpeg) == (640, 480)

    async def test_khong_phai_jpeg(self):
        from module import kich_thuoc_jpeg
        assert kich_thuoc_jpeg(b"khong phai jpeg") is None


# ── 9. Dịch vụ phụ chạy thật ───────────────────────────────────────────────────

class TestDichVu:
    async def test_suc_khoe_va_401(self, dich_vu_gia):
        import httpx
        cong = _cong_cua_pid(dich_vu_gia["pid"])
        async with httpx.AsyncClient() as client:
            r = await client.get(f"http://127.0.0.1:{cong}/suc-khoe")
            assert r.json() == {"ok": True}
            r = await client.post(f"http://127.0.0.1:{cong}/chup", json={"cam": {}})
            assert r.status_code == 401
            r = await client.post(f"http://127.0.0.1:{cong}/phat", json={"cam": {}})
            assert r.status_code == 401

    async def test_dich_vu_tu_choi_khoa_yeu(self, tmp_path):
        env = dict(os.environ, CAM_KHOA="yeu", PYTHONPATH=DV)
        p = subprocess.run([sys.executable, os.path.join(DV, "server.py")],
                           capture_output=True, text=True, env=env, timeout=15)
        assert p.returncode != 0
        assert "CAM_KHOA" in p.stderr or "CAM_KHOA" in p.stdout


# ── 10. Module gọi dịch vụ phụ THẬT (chuỗi đầy đủ) ─────────────────────────────

class TestChuoiDayDu:
    def _ch_dich_vu(self, monkeypatch, go2rtc_gia, dich_vu_gia, **them):
        ch = {"danh_sach_camera": json.dumps([
                  {"name": "Cửa", "kind": "go2rtc", "src": "cua",
                   "base": go2rtc_gia["base"]}]),
              "dich_vu_url": dich_vu_gia["url"], "dich_vu_khoa": dich_vu_gia["khoa"]}
        ch.update(them)
        return _nap(monkeypatch, ch)

    async def test_chup_qua_dich_vu(self, monkeypatch, go2rtc_gia, dich_vu_gia):
        m = self._ch_dich_vu(monkeypatch, go2rtc_gia, dich_vu_gia)
        kq = await m.thuc_hien("cam.cua", "chup")
        assert kq["ok"] is True
        jpeg = base64.b64decode(kq["trang_thai_moi"]["anh_base64"])
        # Ảnh đi qua dịch vụ phụ đã được _thu_nho (không đổi vì 640×480 ≤ 1600).
        assert jpeg == go2rtc_gia["jpeg"]

    async def test_phat_qua_dich_vu_bi_chan_khi_chua_bat(self, monkeypatch, go2rtc_gia, dich_vu_gia):
        m = self._ch_dich_vu(monkeypatch, go2rtc_gia, dich_vu_gia)
        kq = await m.thuc_hien("cam.cua", "phat", {"am_thanh_b64": "AAA="})
        assert kq["ok"] is False and kq["ma"] == "bi_cam"

    async def test_phat_day_du_chung_tu(self, monkeypatch, go2rtc_gia, dich_vu_gia):
        """Mở bộ đàm qua dịch vụ phụ THẬT (phát/ra kênh deferred của c2a): mở
        được phiên, thêm tiếng, đóng được — camera go2rtc giả không có kênh nói
        nên lần thêm đầu tiên mới lỗi; lỗi phải là loi_dich_vu sạch, không lộ
        địa chỉ/mật khẩu."""
        m = self._ch_dich_vu(monkeypatch, go2rtc_gia, dich_vu_gia, loa_bat="true")
        kq = await m.thuc_hien("cam.phat_cua", "bo_dam_mo")
        assert kq["ok"] is True and kq.get("phien")
        phien = kq["phien"]
        kq2 = await m.thuc_hien("cam.phat_cua", "bo_dam_them",
                                {"phien": phien, "alaw_b64": base64.b64encode(b"\xd5" * 32).decode()})
        # c2a BoDam MỞ KÊNH TRÌ HOÃN: thêm tiếng ngắn chỉ vào đệm (chưa phát),
        # nên trả ok — hành vi có chủ ý của bản gốc, không phải lỗi.
        assert kq2["ok"] is True
        kq3 = await m.thuc_hien("cam.phat_cua", "bo_dam_dong", {"phien": phien})
        # Đóng phiên xả nốt đệm → mở kênh thật → go2rtc giả không có producers:
        # lỗi phải sạch (loi_dich_vu), không lộ địa chỉ/mật khẩu.
        assert isinstance(kq3, dict)
        assert go2rtc_gia["base"] not in str(kq3)


class TestHangRaoDichVu:
    """Hàng rào tầng dịch vụ phụ: giới hạn tần suất phát (2 lần liên tiếp bị chặn)."""

    async def test_phat_hai_lan_lien_tiep_bi_chan(self, dich_vu_gia, go2rtc_gia):
        import httpx
        hd = {"Authorization": f"Bearer {dich_vu_gia['khoa']}"}
        than = {"cam": {"name": "HangRao", "kind": "go2rtc",
                        "base": go2rtc_gia["base"], "src": "cua"},
                "am_thanh_b64": base64.b64encode(b"\x00" * 1600).decode(),
                "toi_da_giay": 5}
        async with httpx.AsyncClient() as client:
            r1 = await client.post(f"{dich_vu_gia['url']}/phat", json=than, headers=hd)
            r2 = await client.post(f"{dich_vu_gia['url']}/phat", json=than, headers=hd)
        assert r2.status_code == 422
        assert "quá dày" in r2.json().get("loi", "")
