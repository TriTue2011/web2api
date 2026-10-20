"""Test NV173b-SUA — cameranha theo soát độc lập C2 (C0–C4).

Đỏ trước/xanh sau cho từng phát hiện:
- C0: qua LÕI THẬT — bí mật (mật khẩu RTSP, ma_webhook, dich_vu_khoa) không
  rơi vào modules.json / GET /api/modules; tab /m/cameranha/ được phục vụ.
- C1: bộ kiểm địa chỉ — RTSP LAN hợp lệ PHẢI qua cai(); IP công khai, loopback
  (cho RTSP), link-local/metadata, tên miền phân giải ra địa chỉ cấm đều bị
  chặn; base/dich_vu_url công khai bị chặn, loopback cho dịch vụ nội bộ dùng được.
- C2: dịch vụ phụ chỉ nhận cam_id đã ĐĂNG KÝ (bảng của chính nó), không nhận
  URL từ lời gọi; ffmpeg có -protocol_whitelist chặt; kind lạ bị từ chối.
- C3: thực thể loa/bộ đàm ở «nguy_hiem»; phiên bộ đàm có trần số phiên, trần
  tổng thời lượng và hết hạn khi im.
- C4: cho_ai_xem=false chặn CẢ chup lẫn chup_cho_ai (cổng ở dữ liệu trả ra);
  dịch vụ kiểm khoá TRƯỚC khi đọc thân; cho_loa thiếu trường = tắt ở dịch vụ.
"""

from __future__ import annotations

import base64
import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytestmark = pytest.mark.anyio

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "modules", "cameranha")))
DV = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "dich-vu", "camera"))
sys.path.insert(0, DV)

#: JPEG tối thiểu dựng tay (SOI + APP0 + SOF0 32×16 + EOI) — không cần ffmpeg.
_JPEG_TAY = bytes.fromhex(
    "ffd8"
    "ffe000104a46494600010100000100010000"
    "ffc00011080020001003012200021101031101"
    "ffd9"
).replace(b"\x08\x00\x20\x00\x10", b"\x08\x00\x10\x00\x20")


@pytest.fixture(autouse=True)
def _tat_loopback_test_cua_loi(monkeypatch):
    """Bộ kiểm của module KHÔNG được dựa vào cờ test của lõi (tu_xa): xoá cờ
    để mặc định dich_vu_url=127.0.0.1 phải tự dùng được theo luật camera."""
    monkeypatch.delenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", raising=False)


def _nap(cau_hinh: dict):
    from module import CameraModule
    manifest = json.load(open(os.path.join(
        os.path.dirname(__file__), "..", "modules", "cameranha", "manifest.json")))
    mod = CameraModule(manifest)
    mod.nap(dict(cau_hinh))
    return mod


def _cai_ds(ds: list, **them):
    ch = {"danh_sach_camera": json.dumps(ds)}
    ch.update(them)
    return ch


# ── C1: bộ kiểm địa chỉ ─────────────────────────────────────────────────────

class TestC1DiaChi:
    async def test_rtsp_lan_hop_le_qua_cai(self):
        m = _nap({})
        kq = await m.cai(_cai_ds([
            {"name": "Pk", "kind": "rtsp",
             "url": "rtsp://admin:mat-khau-gia@10.0.0.6:554/stream"}]))
        assert kq["ok"] is True, kq

    async def test_rtsp_cong_khai_bi_chan(self):
        m = _nap({})
        kq = await m.cai(_cai_ds([
            {"name": "A", "kind": "rtsp", "url": "rtsp://x:1@8.8.8.8/s"}]))
        assert kq["ok"] is False

    async def test_rtsp_loopback_bi_chan(self):
        m = _nap({})
        kq = await m.cai(_cai_ds([
            {"name": "A", "kind": "rtsp", "url": "rtsp://x:1@127.0.0.1:554/s"}]))
        assert kq["ok"] is False

    async def test_rtsp_link_local_bi_chan(self):
        m = _nap({})
        kq = await m.cai(_cai_ds([
            {"name": "A", "kind": "rtsp", "url": "rtsp://x:1@169.254.169.254/s"}]))
        assert kq["ok"] is False

    async def test_ten_mien_phan_giai(self, monkeypatch):
        def fake(host, port, family=0, type=0, proto=0, flags=0):
            ip = "10.0.0.9" if host == "cam-lan.example.invalid" else "8.8.8.8"
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port or 0))]
        monkeypatch.setattr(socket, "getaddrinfo", fake)
        m = _nap({})
        kq = await m.cai(_cai_ds([
            {"name": "A", "kind": "rtsp", "url": "rtsp://u:p@cam-lan.example.invalid/s"}]))
        assert kq["ok"] is True, kq
        kq = await m.cai(_cai_ds([
            {"name": "A", "kind": "rtsp", "url": "rtsp://u:p@cam-ngoai.example.invalid/s"}]))
        assert kq["ok"] is False
        kq = await m.cai(_cai_ds([
            {"name": "A", "kind": "go2rtc",
             "base": "http://cam-ngoai.example.invalid:1984", "src": "x"}]))
        assert kq["ok"] is False

    async def test_base_cong_khai_bi_chan(self):
        m = _nap({})
        kq = await m.cai(_cai_ds([
            {"name": "A", "kind": "go2rtc", "base": "http://8.8.8.8:1984", "src": "x"}]))
        assert kq["ok"] is False

    async def test_dich_vu_url_cong_khai_bi_chan(self):
        m = _nap({})

        async def get_gia(url, headers=None, timeout=20.0):
            return {"ma": 200, "than": b"{}"}
        m._http_get = get_gia
        kq = await m.cai(_cai_ds([], dich_vu_url="http://1.1.1.1:8714",
                                 dich_vu_khoa="k" * 30))
        assert kq["ok"] is False
        assert any("dich_vu_url" in l for l in kq["loi"])

    async def test_dich_vu_url_loopback_mac_dinh_dung_duoc(self):
        """C4: mặc định http://127.0.0.1:8714 (dịch vụ nội bộ quản trị khai)
        KHÔNG bị cai() từ chối — bản cũ qua kiem_url_lan nên bị chặn."""
        m = _nap({})

        async def get_gia(url, headers=None, timeout=20.0):
            return {"ma": 200, "than": b"{}"}
        m._http_get = get_gia
        kq = await m.cai(_cai_ds([], dich_vu_url="http://127.0.0.1:8714",
                                 dich_vu_khoa="k" * 30))
        assert kq["ok"] is True, kq


# ── C4 (module): cổng dữ liệu cho_ai_xem + C3: rủi ro thực thể loa ──────────

@pytest.fixture(scope="module")
def go2rtc_tay():
    class _Go(BaseHTTPRequestHandler):
        def do_GET(self):
            if "frame.jpeg" in self.path and "src=cua" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(_JPEG_TAY)))
                self.end_headers()
                self.wfile.write(_JPEG_TAY)
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Go)
    cong = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield {"base": f"http://127.0.0.1:{cong}", "jpeg": _JPEG_TAY}
    srv.shutdown()


class TestC4CongDuLieu:
    async def test_chup_bi_chan_khi_cho_ai_xem_false(self, go2rtc_tay):
        ch = _cai_ds([{"name": "Cửa", "kind": "go2rtc", "src": "cua",
                       "base": go2rtc_tay["base"]}])
        m = _nap(ch)
        kq = await m.thuc_hien("cam.cua", "chup")
        assert kq["ok"] is False and kq["ma"] == "bi_cam", kq
        assert "anh_base64" not in json.dumps(kq)

    async def test_chup_mo_khi_cho_ai_xem_true(self, go2rtc_tay):
        ch = _cai_ds([{"name": "Cửa", "kind": "go2rtc", "src": "cua",
                       "base": go2rtc_tay["base"]}], cho_ai_xem="true")
        m = _nap(ch)
        kq = await m.thuc_hien("cam.cua", "chup")
        assert kq["ok"] is True
        assert base64.b64decode(kq["trang_thai_moi"]["anh_base64"]) == go2rtc_tay["jpeg"]


class TestC3RuiRoThucThe:
    async def test_loa_o_nguy_hiem(self):
        m = _nap(dict(_cai_ds([{"name": "Cửa", "kind": "go2rtc", "src": "cua",
                                "base": "http://10.0.0.6:1984"}]), loa_bat="true"))
        theo_id = {e["id"]: e for e in await m.thuc_the()}
        assert theo_id["cam.phat_cua"]["rui_ro_mac_dinh"] == "nguy_hiem"
        assert theo_id["cam.cua"]["rui_ro_mac_dinh"] == "doc"


# ── C2 (module): chỉ gửi cam_id, đăng ký bảng trước ─────────────────────────

class TestC2ModuleDangKy:
    def _module_dich_vu(self):
        ch = _cai_ds([{"name": "Cửa", "kind": "go2rtc", "src": "cua",
                       "base": "http://10.0.0.6:1984"}],
                     dich_vu_url="http://10.0.0.7:8714",
                     dich_vu_khoa="k" * 30, cho_ai_xem="true")
        m = _nap(ch)
        cac_lan = []

        async def post_gia(url, than, headers=None, timeout=30.0):
            cac_lan.append((url, than))
            if url.endswith("/dang-ky"):
                return {"ma": 200, "json": {"ok": True, "so_camera": 1}}
            if url.endswith("/chup"):
                return {"ma": 200, "json": {"ok": True,
                        "anh_b64": base64.b64encode(_JPEG_TAY).decode()}}
            return {"ma": 200, "json": {"ok": True}}
        m._http_post = post_gia
        return m, cac_lan

    async def test_chup_gui_cam_id_va_dang_ky_truoc(self):
        m, cac_lan = self._module_dich_vu()
        kq = await m.thuc_hien("cam.cua", "chup")
        assert kq["ok"] is True, kq
        duong = [u.rsplit("/", 1)[-1] for u, _ in cac_lan]
        assert duong == ["dang-ky", "chup"], duong
        than_chup = cac_lan[1][1]
        assert than_chup.get("cam_id") == "cua"
        assert "cam" not in than_chup  # KHÔNG gửi bản ghi/URL theo lời gọi nữa
        than_dk = cac_lan[0][1]
        assert than_dk["danh_sach"][0]["id"] == "cua"
        assert than_dk["danh_sach"][0]["base"] == "http://10.0.0.6:1984"

    async def test_khong_dang_ky_lai_khi_bang_khong_doi(self):
        m, cac_lan = self._module_dich_vu()
        await m.thuc_hien("cam.cua", "chup")
        await m.thuc_hien("cam.cua", "chup")
        duong = [u.rsplit("/", 1)[-1] for u, _ in cac_lan]
        assert duong == ["dang-ky", "chup", "chup"], duong

    async def test_dang_ky_lai_sau_khi_nap_lai(self):
        m, cac_lan = self._module_dich_vu()
        await m.thuc_hien("cam.cua", "chup")
        m.nap(dict(m._cau_hinh))  # cấu hình nạp lại → phải đăng ký lại
        await m.thuc_hien("cam.cua", "chup")
        duong = [u.rsplit("/", 1)[-1] for u, _ in cac_lan]
        assert duong == ["dang-ky", "chup", "dang-ky", "chup"], duong


# ── C2/C3/C4 (dịch vụ phụ): kiểm bản ghi, whitelist, trần bộ đàm, cho_loa ────

class TestC2KiemBanGhi:
    def test_loai_kind_la_va_scheme_la(self):
        import chup
        assert chup.kiem_ban_ghi({"name": "A", "kind": "khac"}) is not None
        assert chup.kiem_ban_ghi({"name": "A", "kind": "rtsp",
                                  "url": "file:///etc/passwd"}) is not None
        assert chup.kiem_ban_ghi({"name": "A", "kind": "rtsp",
                                  "url": "concat:/etc/passwd|/etc/hostname"}) is not None
        assert chup.kiem_ban_ghi({"name": "A", "kind": "rtsp",
                                  "url": "http://10.0.0.5/s"}) is not None

    def test_chan_dia_chi_ngoai_lan(self):
        import chup
        assert chup.kiem_ban_ghi({"name": "A", "kind": "rtsp",
                                  "url": "rtsp://u:p@8.8.8.8/s"}) is not None
        assert chup.kiem_ban_ghi({"name": "A", "kind": "rtsp",
                                  "url": "rtsp://u:p@169.254.169.254/s"}) is not None
        # Loopback của chính container dịch vụ KHÔNG phải địa chỉ camera/go2rtc.
        assert chup.kiem_ban_ghi({"name": "A", "kind": "go2rtc",
                                  "base": "http://127.0.0.1:1984", "src": "x"}) is not None
        assert chup.kiem_ban_ghi({"name": "A", "kind": "go2rtc",
                                  "base": "http://8.8.8.8:1984", "src": "x"}) is not None
        # src là TÊN luồng, không phải URL.
        assert chup.kiem_ban_ghi({"name": "A", "kind": "go2rtc",
                                  "base": "http://10.0.0.6:1984",
                                  "src": "http://127.0.0.1/x"}) is not None

    def test_ban_ghi_hop_le_va_ghim_ip(self):
        import chup
        cam = {"name": "A", "kind": "go2rtc", "base": "http://10.0.0.6:1984",
               "src": "cua"}
        assert chup.kiem_ban_ghi(cam) is None
        assert "10.0.0.6" in (cam.get("_base_ghim") or "")
        cam2 = {"name": "B", "kind": "rtsp", "url": "rtsp://u:p@10.0.0.5:554/s"}
        assert chup.kiem_ban_ghi(cam2) is None

    def test_ffmpeg_protocol_whitelist(self, monkeypatch):
        import chup

        class _P:
            returncode = 0
            stdout = _JPEG_TAY
            stderr = b""
        argv_luu = []

        def run_gia(argv, **kw):
            argv_luu.append(argv)
            return _P()
        monkeypatch.setattr(chup.subprocess, "run", run_gia)
        jpeg = chup._chup_rtsp({"url": "rtsp://u:p@10.0.0.5:554/s"}, 5.0)
        assert jpeg == _JPEG_TAY
        argv = argv_luu[0]
        i = argv.index("-i")
        j = argv.index("-protocol_whitelist")
        # Cờ đầu vào phải đứng TRƯỚC -i và mang đúng danh sách đóng.
        assert j < i and argv[j + 1] == "rtsp,tcp,udp,rtp", argv
        assert argv[i + 1] == "rtsp://u:p@10.0.0.5:554/s"

    def test_kind_la_khong_goi_ffmpeg(self, monkeypatch):
        import chup

        def run_cam(*a, **kw):
            raise AssertionError("không được gọi tiến trình con cho kind lạ")
        monkeypatch.setattr(chup.subprocess, "run", run_cam)
        with pytest.raises(chup.LoiCamera):
            chup.chup("A", {"kind": "khac", "url": "file:///etc/passwd"})


class TestC4ChoLoaDichVu:
    def test_thieu_cho_loa_la_tat(self):
        import kenh
        kenh.dat_so({"A": {"name": "A", "kind": "rtsp",
                           "url": "rtsp://u:p@10.0.0.5/s"}})
        with pytest.raises(kenh.LoiLoa):
            kenh._lay_cho_phat("A")
        kenh.dat_so({"A": {"name": "A", "kind": "rtsp", "cho_loa": True,
                           "url": "rtsp://u:p@10.0.0.5/s"}})
        ten, _cam = kenh._lay_cho_phat("A")
        assert ten == "A"


class TestC3BoDamTran:
    def _mo(self, server, bo_dam, ten):
        return bo_dam.mo(ten, {"name": ten, "kind": "rtsp",
                               "url": "rtsp://u:p@10.0.0.5/s", "cho_loa": True})

    def test_tran_so_phien(self, monkeypatch):
        monkeypatch.setenv("CAM_BO_DAM_TOI_DA_PHIEN", "1")
        import server
        bd = server._BoDam()
        self._mo(server, bd, "A")
        with pytest.raises(Exception):
            self._mo(server, bd, "B")

    def test_phien_het_han_khi_im(self, monkeypatch):
        monkeypatch.setenv("CAM_BO_DAM_HET_HAN_GIAY", "0.2")
        import server
        bd = server._BoDam()
        ma = self._mo(server, bd, "A")
        time.sleep(0.35)
        with pytest.raises(Exception):
            bd.them(ma, b"\xd5" * 160)

    def test_tran_tong_thoi_luong(self, monkeypatch):
        monkeypatch.setenv("CAM_BO_DAM_TONG_GIAY", "0.05")
        import server
        bd = server._BoDam()
        ma = self._mo(server, bd, "A")
        bd.them(ma, b"\xd5" * 160)          # 0,02 s — dưới trần
        with pytest.raises(Exception):
            bd.them(ma, b"\xd5" * 480)      # +0,06 s → vượt trần tổng


# ── Dịch vụ phụ chạy thật: hợp đồng mới + khoá trước thân + SSRF ────────────

def _cong_cua_pid(pid: int) -> int:
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


@pytest.fixture(scope="module")
def dich_vu_that():
    khoa = "khoa-test-nv173b-du-24-ky-tu-xyz"
    env = dict(os.environ, CAM_KHOA=khoa, CAM_CONG="0", CAM_HOST="127.0.0.1",
               CAM_TEST_CHO_PHEP_LOOPBACK="1", PYTHONPATH=DV)
    p = subprocess.Popen([sys.executable, os.path.join(DV, "server.py")],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
    time.sleep(2.0)
    assert p.poll() is None, "dịch vụ phụ phải chạy được"
    try:
        yield {"khoa": khoa, "url": f"http://127.0.0.1:{_cong_cua_pid(p.pid)}"}
    finally:
        p.terminate()
        p.wait(timeout=10)


@pytest.fixture(scope="module")
def may_metadata_gia():
    """Đích SSRF giả (đóng vai metadata/LAN nội bộ): đếm lượt chạm."""
    dem = {"n": 0}

    class _M(BaseHTTPRequestHandler):
        def do_GET(self):
            dem["n"] += 1
            than = b'{"AccessKeyId":"GIA-AKIA-000"}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(than)))
            self.end_headers()
            self.wfile.write(than)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _M)
    cong = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield {"url": f"http://127.0.0.1:{cong}", "dem": dem}
    srv.shutdown()


class TestC2DichVuThat:
    async def test_than_cu_khong_con_duoc_nhan(self, dich_vu_that, may_metadata_gia):
        """Lời gọi kiểu cũ {cam: {...URL tuỳ ý...}} KHÔNG được thực thi nữa:
        dịch vụ không chạm vào địa chỉ trong thân (SSRF chết tại gốc)."""
        import httpx
        hd = {"Authorization": f"Bearer {dich_vu_that['khoa']}"}
        than = {"cam": {"name": "X", "kind": "go2rtc",
                        "base": may_metadata_gia["url"], "src": "cua"}}
        async with httpx.AsyncClient() as client:
            r = await client.post(f"{dich_vu_that['url']}/chup", json=than, headers=hd)
        assert r.status_code != 200, r.text
        assert may_metadata_gia["dem"]["n"] == 0

    async def test_dang_ky_chan_ban_ghi_xau(self, dich_vu_that, may_metadata_gia):
        import httpx
        hd = {"Authorization": f"Bearer {dich_vu_that['khoa']}"}
        than = {"danh_sach": [{"id": "x", "name": "X", "kind": "go2rtc",
                               "base": may_metadata_gia["url"], "src": "cua"}]}
        async with httpx.AsyncClient() as client:
            r = await client.post(f"{dich_vu_that['url']}/dang-ky", json=than, headers=hd)
        # CAM_TEST_CHO_PHEP_LOOPBACK chỉ nới cho test; ở đây kiểm lớp scheme/kind:
        # bản ghi này hợp lệ về địa chỉ test nên phải qua — đổi sang địa chỉ công
        # khai thì bị chặn ngay tại đăng ký, không chờ tới lúc chụp.
        assert r.status_code == 200, r.text
        than2 = {"danh_sach": [{"id": "y", "name": "Y", "kind": "rtsp",
                                "url": "file:///etc/passwd"}]}
        async with httpx.AsyncClient() as client:
            r2 = await client.post(f"{dich_vu_that['url']}/dang-ky", json=than2, headers=hd)
        assert r2.status_code == 400, r2.text
        assert may_metadata_gia["dem"]["n"] == 0

    async def test_chup_bang_cam_id_sau_dang_ky(self, dich_vu_that, go2rtc_tay):
        import httpx
        hd = {"Authorization": f"Bearer {dich_vu_that['khoa']}"}
        async with httpx.AsyncClient() as client:
            r = await client.post(f"{dich_vu_that['url']}/chup",
                                  json={"cam_id": "chua-dang-ky"}, headers=hd)
            assert r.status_code != 200
            than = {"danh_sach": [{"id": "cua", "name": "Cửa", "kind": "go2rtc",
                                   "base": go2rtc_tay["base"], "src": "cua"}]}
            r = await client.post(f"{dich_vu_that['url']}/dang-ky", json=than, headers=hd)
            assert r.status_code == 200, r.text
            r = await client.post(f"{dich_vu_that['url']}/chup",
                                  json={"cam_id": "cua"}, headers=hd)
            assert r.status_code == 200, r.text
            assert base64.b64decode(r.json()["anh_b64"]) == go2rtc_tay["jpeg"]
            # Giữ-kết-nối (keep-alive) sau hợp đồng mới: gọi tiếp trên cùng
            # client không được 501/đứt (bài học NV167).
            r = await client.post(f"{dich_vu_that['url']}/chup",
                                  json={"cam_id": "cua"}, headers=hd)
            assert r.status_code == 200, r.text

    async def test_khoa_kiem_truoc_khi_doc_than(self, dich_vu_that):
        """C4: khoá sai + thân khai 4 MB nhưng chỉ gửi 100 byte → dịch vụ phải
        trả 401 NGAY (đọc tối đa ~1 KB rồi đóng), không chờ đủ thân."""
        cong = int(dich_vu_that["url"].rsplit(":", 1)[1])
        s = socket.create_connection(("127.0.0.1", cong), timeout=8)
        try:
            s.sendall(b"POST /chup HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                      b"Authorization: Bearer khoa-sai-hoan-toan-123456\r\n"
                      b"Content-Type: application/json\r\n"
                      b"Content-Length: 4194304\r\n\r\n" + b"x" * 100)
            s.settimeout(8)
            tra = b""
            try:
                while b"\r\n\r\n" not in tra:
                    doan = s.recv(4096)
                    if not doan:
                        break
                    tra += doan
            except socket.timeout:
                tra = b""
            assert tra.startswith(b"HTTP/1.1 401"), tra[:200]
        finally:
            s.close()


# ── C0 qua LÕI THẬT: bí mật không rơi vào tệp/phản hồi công khai ─────────────

KHOA_QT_173B = "khoa-quan-tri-test-nv173b-0123456789"
QT_173B = {"Authorization": f"Bearer {KHOA_QT_173B}"}
MK_RTSP_DAU = "MkRtspDau173b"
MA_WEBHOOK_DAU = "MaWebhookDau173b-0123456789abcdef"
KHOA_DV_DAU = "KhoaDichVuDau173b-0123456789abcdef"


@pytest.fixture
def moi_truong_loi(tmp_path, monkeypatch):
    import shutil
    from pathlib import Path

    from fastapi.testclient import TestClient

    from app import so_module
    from app.main import app
    from app.so_module import SoModule

    data_dir = tmp_path / "data"
    (data_dir / "modules").mkdir(parents=True)
    anh_dir = tmp_path / "anh"
    anh_dir.mkdir(parents=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT_173B)
    monkeypatch.setenv("WEB2API_KHOA_CHINH", bytes(range(32)).hex())
    monkeypatch.setattr(so_module, "THU_MUC_ANH", anh_dir)

    # Dịch vụ phụ GIẢ cho cai() thăm /suc-khoe.
    class _DV(BaseHTTPRequestHandler):
        def do_GET(self):
            than = b'{"ok": true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(than)))
            self.end_headers()
            self.wfile.write(than)

        def log_message(self, *a):
            pass

    dv = ThreadingHTTPServer(("127.0.0.1", 0), _DV)
    threading.Thread(target=dv.serve_forever, daemon=True).start()
    dv_url = f"http://127.0.0.1:{dv.server_address[1]}"

    goc = Path(__file__).resolve().parents[1]
    shutil.copytree(goc / "modules" / "cameranha", data_dir / "modules" / "cameranha",
                    ignore=shutil.ignore_patterns("__pycache__"))
    with TestClient(app) as client:
        client.app.state.so = SoModule()
        r = client.post("/api/modules/nap-lai", headers=QT_173B)
        assert r.status_code == 200, r.text
        assert "cameranha" in r.json()["them"], r.json()
        yield client, data_dir, dv_url
    dv.shutdown()


def test_c0_bi_mat_khong_ro_va_tab_duoc_phuc_vu(moi_truong_loi):
    client, data_dir, dv_url = moi_truong_loi
    ds = [{"name": "Cua", "kind": "rtsp",
           "url": f"rtsp://admin:{MK_RTSP_DAU}@10.0.0.6:554/stream"}]
    r = client.post("/api/modules/cameranha/cai", headers=QT_173B, json={"cau_hinh": {
        "danh_sach_camera": json.dumps(ds),
        "dich_vu_url": dv_url, "dich_vu_khoa": KHOA_DV_DAU,
        "ma_webhook": MA_WEBHOOK_DAU, "loa_bat": "true",
    }})
    assert r.status_code == 200, r.text
    assert r.json().get("ok") is not False, r.json()

    # 1) Tệp công khai modules.json không chứa bất kỳ dấu bí mật nào.
    chuoi_modules = (data_dir / "modules.json").read_text(encoding="utf-8")
    for dau in (MK_RTSP_DAU, MA_WEBHOOK_DAU, KHOA_DV_DAU):
        assert dau not in chuoi_modules

    # 2) GET /api/modules không chứa dấu bí mật.
    r = client.get("/api/modules", headers=QT_173B)
    assert r.status_code == 200, r.text
    for dau in (MK_RTSP_DAU, MA_WEBHOOK_DAU, KHOA_DV_DAU):
        assert dau not in r.text

    # 3) Bí mật nằm ở kho bí mật (đọc lại qua app.kho — đã giải mã bằng khoá chính).
    from app import kho
    bi_mat = kho.doc_bi_mat("cameranha")
    assert MK_RTSP_DAU in json.dumps(bi_mat)

    # 4) Tab của module được lõi phục vụ.
    r = client.get("/m/cameranha/index.html")
    assert r.status_code == 200, r.status_code

    # 5) C3 qua lõi: thực thể loa hiện với rủi ro «nguy_hiem».
    r = client.get("/api/thiet-bi?module=cameranha", headers=QT_173B)
    assert r.status_code == 200, r.text
    theo_id = {e["id"]: e for e in r.json()["thuc_the"]}
    assert theo_id["cam.phat_cua"]["rui_ro_mac_dinh"] == "nguy_hiem"
    assert theo_id["cam.cua"]["rui_ro_mac_dinh"] == "doc"
