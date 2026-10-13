"""Bộ kiểm thử toàn diện cho CLI muse-web: không mạng thật, mock protocol & chat.

Kiểm tra đủ 10 yêu cầu:
1. `cookie them` từ stdin/--tep, không nhận qua argv, quyền 0600/0700, ds chỉ …abcd, tên xấu/xuống dòng/ký tự điều khiển -> 2.
2. Không có cookie trong stdout/stderr/thông báo lỗi.
3. `gui` thành công, JSON chuẩn, --dong JSONL, việc gửi NGUYÊN VĂN, stdin/--tep.
4. Mã thoát: hết giờ -> 3, AuthError -> 4, GatewayError -> 5, không rảnh -> 6, sai cú pháp -> 2.
5. Hai tài khoản: AuthError trước khi gửi chuyển B; đã da_gui KHÔNG chuyển, không giao 2 lần.
6. Khóa flock: giữ A thì chọn B; giữ cả hai -> 6; nhả khóa khi xong kể cả lỗi.
7. Kết nối chết trước khi gửi -> nối lại đúng 1 lần, stream mở đúng 1 lần.
8. `lich-su`: lọc đúng, giới hạn đúng, không rò cookie.
9. Quyền rộng hơn 0600 -> từ chối đọc (mã 2) kèm cách sửa; ghi nguyên tử.
10. `kiem`: ok/loi từng tài khoản, không chứa nội dung thượng nguồn.
"""

from __future__ import annotations

import argparse
import fcntl
import io
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

# Bỏ qua import mismatch khi pytest nhận cả dich-vu/muse-web và test/test_muse_web.py
os.environ["PY_IGNORE_IMPORTMISMATCH"] = "1"

import pytest

from modules.muse2api import chat, protocol
GOC_DICH_VU = Path(__file__).resolve().parent
if str(GOC_DICH_VU) not in sys.path:
    sys.path.insert(0, str(GOC_DICH_VU))

import muse_web


# ── Bộ giả lập (Mock) ────────────────────────────────────────────────────


class GatewayGia:
    def __init__(self, cookies: str = ""):
        self.cookies = cookies
        self.lich_su: list[dict] = []
        self.open_calls: list[dict] = []
        self.da_dong = False
        self.loi_call_json: Exception | None = None

    def call_json(self, method: str, body=None, timeout=30):
        if self.loi_call_json is not None:
            raise self.loi_call_json
        if method == "chat.history":
            limit = (body or {}).get("limit", 15)
            return {"chat_events": list(self.lich_su[:limit])}
        return {}

    def _open(self, method: str, body=None, **kw):
        self.open_calls.append({"method": method, "body": body})
        return len(self.open_calls)

    def close(self):
        self.da_dong = True


class ChatSessionGia:
    def __init__(self, gw, node_id: str, poll_interval: float = 0.01,
                 wait_s: float = 300.0, quiet_s: float = 0.1):
        self.gw = gw
        self.node_id = node_id
        self.poll_interval = poll_interval
        self.wait_s = wait_s
        self.quiet_s = quiet_s
        self.da_gui = False
        self.loi_khi_run: Exception | None = None
        self.deltas: list[str] = ["Kết quả mẫu 1", " và kết quả mẫu 2"]

    def run(self, prompt: str, co_huy: threading.Event | None = None):
        self.gw._open("chat.stream", body={"prompt": prompt})
        self.da_gui = True
        if self.loi_khi_run:
            raise self.loi_khi_run
        for d in self.deltas:
            if co_huy and co_huy.is_set():
                return
            yield d


# ── Tiện ích chạy CLI trong test ──────────────────────────────────────────


def chay_cli(argv: list[str], tmp_path: Path, monkeypatch, stdin_str: str | None = None,
             stdin_bytes: bytes | None = None, kho: Path | None = None) -> tuple[int, str, str]:
    monkeypatch.setenv("MUSE_WEB_DIR", str(kho if kho is not None else tmp_path))
    old_stdin = sys.stdin
    buf_out = io.StringIO()
    buf_err = io.StringIO()
    if stdin_str is not None:
        stdin_bytes = stdin_str.encode("utf-8")
    if stdin_bytes is not None:
        # Giống stdin thật: lớp chữ bọc lớp byte (.buffer)
        sys.stdin = io.TextIOWrapper(io.BytesIO(stdin_bytes), encoding="utf-8")
    monkeypatch.setattr(sys, "stdout", buf_out)
    monkeypatch.setattr(sys, "stderr", buf_err)
    try:
        code = muse_web.main(argv)
    finally:
        sys.stdin = old_stdin
    return code, buf_out.getvalue(), buf_err.getvalue()


# ── Ca 1: Quản lý cookie (thêm, ds, xoa, cờ lạ, định dạng) ─────────────


class TestQuanLyCookie:
    def test_cookie_them_va_ds_va_xoa(self, tmp_path, monkeypatch):
        # 1. Thêm từ stdin
        cookie_val = "session_token=testcookievalue1234; other=data5678"
        code, out, _ = chay_cli(["cookie", "them", "--ten", "acc-chinh"], tmp_path, monkeypatch, stdin_str=cookie_val + "\n")
        assert code == 0
        res = json.loads(out)
        assert res["ok"] is True
        assert res["ten"] == "acc-chinh"
        assert res["khoa"] == "…1234"  # 4 ký tự cuối của giá trị cookie đầu

        # Kiểm tra quyền file cookies.json và thư mục
        tep_cookie = tmp_path / "cookies.json"
        assert tep_cookie.exists()
        assert (tep_cookie.stat().st_mode & 0o777) == 0o600
        assert (tmp_path.stat().st_mode & 0o777) == 0o700

        # 2. Thêm qua --tep
        tep_phu = tmp_path / "cookie_acc2.txt"
        tep_phu.write_text("token2=val87654321; secure=1", encoding="utf-8")
        code, out, _ = chay_cli(["cookie", "them", "--ten", "acc_phu", "--tep", str(tep_phu)], tmp_path, monkeypatch)
        assert code == 0

        # 3. Liệt kê cookie ds
        code, out, _ = chay_cli(["cookie", "ds"], tmp_path, monkeypatch)
        assert code == 0
        ds = json.loads(out)
        assert len(ds) == 2
        assert ds[0]["ten"] == "acc-chinh"
        assert ds[0]["khoa"] == "…1234"
        assert ds[1]["ten"] == "acc_phu"
        assert ds[1]["khoa"] == "…4321"

        # 4. Xóa cookie
        code, out, _ = chay_cli(["cookie", "xoa", "--ten", "acc-chinh"], tmp_path, monkeypatch)
        assert code == 0
        res = json.loads(out)
        assert res["ok"] is True
        assert res["ten"] == "acc-chinh"

        # Liệt kê lại chỉ còn 1
        code, out, _ = chay_cli(["cookie", "ds"], tmp_path, monkeypatch)
        assert code == 0
        ds = json.loads(out)
        assert len(ds) == 1
        assert ds[0]["ten"] == "acc_phu"

    def test_cookie_tu_choi_nhan_qua_argv(self, tmp_path, monkeypatch):
        # Truyền cờ lạ hoặc đối số vị trí -> thoát 2
        code, out, _ = chay_cli(["cookie", "them", "--ten", "acc1", "--cookie", "k=v"], tmp_path, monkeypatch)
        assert code == 2
        res = json.loads(out)
        assert res["ok"] is False
        assert res["ma"] == "yeu_cau_sai"

        code, out, _ = chay_cli(["cookie", "them", "--ten", "acc1", "k1=v1"], tmp_path, monkeypatch)
        assert code == 2
        res = json.loads(out)
        assert res["ok"] is False

    def test_cookie_ten_xau_hoac_noi_dung_hong(self, tmp_path, monkeypatch):
        # Tên không hợp lệ
        for bad_name in ["ACC_HOA", "acc!bad", "-batdau", "_gachduoi", "a" * 33]:
            code, out, _ = chay_cli(["cookie", "them", "--ten", bad_name], tmp_path, monkeypatch, stdin_str="k=v123456789")
            assert code == 2
            res = json.loads(out)
            assert res["ok"] is False
            assert res["ma"] == "yeu_cau_sai"

        # Cookie rỗng
        code, out, _ = chay_cli(["cookie", "them", "--ten", "acc1"], tmp_path, monkeypatch, stdin_str="   \n")
        assert code == 2
        res = json.loads(out)
        assert res["ok"] is False

        # Cookie xuống dòng giữa chừng
        code, out, _ = chay_cli(["cookie", "them", "--ten", "acc1"], tmp_path, monkeypatch, stdin_str="k1=v1\nk2=v2\n")
        assert code == 2
        res = json.loads(out)
        assert res["ok"] is False

        # Cookie có ký tự điều khiển
        code, out, _ = chay_cli(["cookie", "them", "--ten", "acc1"], tmp_path, monkeypatch, stdin_str="k1=v1\x00abc")
        assert code == 2
        res = json.loads(out)
        assert res["ok"] is False


# ── Ca 2: Tuyệt đối không rò rỉ cookie trong stdout/stderr/lỗi ─────────


class TestBaoMatKhongRoCookie:
    def test_khong_ro_cookie_trong_moi_dau_ra(self, tmp_path, monkeypatch):
        chuoi_bi_mat = "super_secret_cookie_token_99999"
        cookie_raw = f"hatch_sess={chuoi_bi_mat}; extra=abc"

        # Thêm cookie
        code, out, err = chay_cli(["cookie", "them", "--ten", "acc1"], tmp_path, monkeypatch, stdin_str=cookie_raw)
        assert chuoi_bi_mat not in out
        assert chuoi_bi_mat not in err

        # Liệt kê ds
        code, out, err = chay_cli(["cookie", "ds"], tmp_path, monkeypatch)
        assert chuoi_bi_mat not in out
        assert chuoi_bi_mat not in err

        # Lỗi kiem tra
        def fake_check(c):
            raise protocol.AuthError(f"Rejected session with cookie={c}")
        monkeypatch.setattr(protocol, "fetch_access_token", fake_check)

        code, out, err = chay_cli(["kiem"], tmp_path, monkeypatch)
        assert chuoi_bi_mat not in out
        assert chuoi_bi_mat not in err


# ── Ca 3: Lệnh gui thành công, --dong JSONL, gửi nguyên văn ───────────


class TestGuiThanhCong:
    def test_gui_mac_dinh_va_nguyen_van(self, tmp_path, monkeypatch):
        # Chuẩn bị cookie
        tep_c = tmp_path / "cookies.json"
        tep_c.write_text(json.dumps({"tai_khoan": [{"ten": "acc1", "cookies": "k=val123456789"}]}), encoding="utf-8")
        tep_c.chmod(0o600)

        gw_gia = GatewayGia()
        sess_gia = ChatSessionGia(gw_gia, "node1")
        monkeypatch.setattr(protocol, "Gateway", lambda c: gw_gia)
        monkeypatch.setattr(chat, "ChatSession", lambda gw, node_id, **kw: sess_gia)

        noi_dung_viec = "Tìm và phân tích lỗi bộ nhớ trong tệp main.c"
        code, out, err = chay_cli(["gui", noi_dung_viec], tmp_path, monkeypatch)
        assert code == 0
        res = json.loads(out)
        assert res["ok"] is True
        assert res["van_ban"] == "Kết quả mẫu 1 và kết quả mẫu 2"
        assert res["tai_khoan"] == "acc1"
        assert "giay" in res

        # Kiểm tra gửi NGUYÊN VĂN vào ChatSession
        assert len(gw_gia.open_calls) == 1
        assert gw_gia.open_calls[0]["body"]["prompt"] == noi_dung_viec

    def test_gui_dong_jsonl(self, tmp_path, monkeypatch):
        tep_c = tmp_path / "cookies.json"
        tep_c.write_text(json.dumps({"tai_khoan": [{"ten": "acc1", "cookies": "k=val123456789"}]}), encoding="utf-8")
        tep_c.chmod(0o600)

        gw_gia = GatewayGia()
        sess_gia = ChatSessionGia(gw_gia, "node1")
        monkeypatch.setattr(protocol, "Gateway", lambda c: gw_gia)
        monkeypatch.setattr(chat, "ChatSession", lambda gw, node_id, **kw: sess_gia)

        code, out, _ = chay_cli(["gui", "--dong", "Việc stream"], tmp_path, monkeypatch)
        assert code == 0
        lines = [line.strip() for line in out.strip().split("\n") if line.strip()]
        assert len(lines) == 3
        j1 = json.loads(lines[0])
        assert j1["delta"] == "Kết quả mẫu 1"
        j2 = json.loads(lines[1])
        assert j2["delta"] == " và kết quả mẫu 2"
        j3 = json.loads(lines[2])
        assert j3["ok"] is True
        assert j3["xong"] is True
        assert j3["tai_khoan"] == "acc1"

    def test_gui_tu_stdin_va_tep(self, tmp_path, monkeypatch):
        tep_c = tmp_path / "cookies.json"
        tep_c.write_text(json.dumps({"tai_khoan": [{"ten": "acc1", "cookies": "k=val123456789"}]}), encoding="utf-8")
        tep_c.chmod(0o600)

        gw_gia = GatewayGia()
        sess_gia = ChatSessionGia(gw_gia, "node1")
        monkeypatch.setattr(protocol, "Gateway", lambda c: gw_gia)
        monkeypatch.setattr(chat, "ChatSession", lambda gw, node_id, **kw: sess_gia)

        # Đọc từ stdin với '-'
        code, out, _ = chay_cli(["gui", "-"], tmp_path, monkeypatch, stdin_str="Nhiệm vụ từ stdin")
        assert code == 0
        assert gw_gia.open_calls[-1]["body"]["prompt"] == "Nhiệm vụ từ stdin"

        # Đọc từ --tep
        tep_viec = tmp_path / "task.txt"
        tep_viec.write_text("Nhiệm vụ từ file", encoding="utf-8")
        code, out, _ = chay_cli(["gui", "--tep", str(tep_viec)], tmp_path, monkeypatch)
        assert code == 0
        assert gw_gia.open_calls[-1]["body"]["prompt"] == "Nhiệm vụ từ file"


# ── Ca 4: Các mã thoát chuẩn ───────────────────────────────────────────


class TestMaThoat:
    def test_het_gio_ma_3(self, tmp_path, monkeypatch):
        tep_c = tmp_path / "cookies.json"
        tep_c.write_text(json.dumps({"tai_khoan": [{"ten": "acc1", "cookies": "k=val123456789"}]}), encoding="utf-8")
        tep_c.chmod(0o600)

        gw_gia = GatewayGia()
        sess_gia = ChatSessionGia(gw_gia, "node1")
        sess_gia.loi_khi_run = TimeoutError("No reply within 300s")
        monkeypatch.setattr(protocol, "Gateway", lambda c: gw_gia)
        monkeypatch.setattr(chat, "ChatSession", lambda gw, node_id, **kw: sess_gia)

        code, out, _ = chay_cli(["gui", "Test timeout"], tmp_path, monkeypatch)
        assert code == 3
        res = json.loads(out)
        assert res["ok"] is False
        assert res["ma"] == "het_gio"

    def test_auth_error_ma_4(self, tmp_path, monkeypatch):
        tep_c = tmp_path / "cookies.json"
        tep_c.write_text(json.dumps({"tai_khoan": [{"ten": "acc1", "cookies": "k=val123456789"}]}), encoding="utf-8")
        tep_c.chmod(0o600)

        def fake_gw(c):
            raise protocol.AuthError("401 unauthorized")
        monkeypatch.setattr(protocol, "Gateway", fake_gw)

        code, out, _ = chay_cli(["gui", "Test auth error"], tmp_path, monkeypatch)
        assert code == 4
        res = json.loads(out)
        assert res["ok"] is False
        assert res["ma"] == "khoa_hong"

    def test_gateway_error_ma_5(self, tmp_path, monkeypatch):
        tep_c = tmp_path / "cookies.json"
        tep_c.write_text(json.dumps({"tai_khoan": [{"ten": "acc1", "cookies": "k=val123456789"}]}), encoding="utf-8")
        tep_c.chmod(0o600)

        def fake_gw(c):
            raise protocol.GatewayError(502, "bad gateway")
        monkeypatch.setattr(protocol, "Gateway", fake_gw)

        code, out, _ = chay_cli(["gui", "Test network error"], tmp_path, monkeypatch)
        assert code == 5
        res = json.loads(out)
        assert res["ok"] is False
        assert res["ma"] == "loi_ket_noi"

    def test_sai_cu_phap_ma_2(self, tmp_path, monkeypatch):
        code, out, _ = chay_cli(["lenh_la_khong_ton_tai"], tmp_path, monkeypatch)
        assert code == 2
        res = json.loads(out)
        assert res["ok"] is False
        assert res["ma"] == "yeu_cau_sai"


# ── Ca 5: Hai tài khoản, AuthError trước gửi & da_gui không giao 2 lần ──


class TestChuyenTaiKhoan:
    def test_auth_error_truoc_gui_chuyen_sang_tai_khoan_b(self, tmp_path, monkeypatch):
        tep_c = tmp_path / "cookies.json"
        tep_c.write_text(
            json.dumps({"tai_khoan": [
                {"ten": "acc1", "cookies": "k=val11111111"},
                {"ten": "acc2", "cookies": "k=val22222222"},
            ]}),
            encoding="utf-8",
        )
        tep_c.chmod(0o600)

        gw_b = GatewayGia("acc2")
        sess_b = ChatSessionGia(gw_b, "node2")

        def fake_gw(c):
            if "val11111111" in c:
                raise protocol.AuthError("acc1 expired")
            return gw_b

        monkeypatch.setattr(protocol, "Gateway", fake_gw)
        monkeypatch.setattr(chat, "ChatSession", lambda gw, node_id, **kw: sess_b)

        code, out, _ = chay_cli(["gui", "Nhiệm vụ chuyển tài khoản"], tmp_path, monkeypatch)
        assert code == 0
        res = json.loads(out)
        assert res["ok"] is True
        assert res["tai_khoan"] == "acc2"
        assert len(gw_b.open_calls) == 1

    def test_da_gui_roi_khong_chuyen_tai_khoan_va_khong_giao_2_lan(self, tmp_path, monkeypatch):
        tep_c = tmp_path / "cookies.json"
        tep_c.write_text(
            json.dumps({"tai_khoan": [
                {"ten": "acc1", "cookies": "k=val11111111"},
                {"ten": "acc2", "cookies": "k=val22222222"},
            ]}),
            encoding="utf-8",
        )
        tep_c.chmod(0o600)

        gw_a = GatewayGia("acc1")
        gw_b = GatewayGia("acc2")
        sess_a = ChatSessionGia(gw_a, "node1")
        # Gặp AuthError SAU KHI đã gọi open chat.stream
        sess_a.loi_khi_run = protocol.AuthError("Session lost after send")
        sess_b = ChatSessionGia(gw_b, "node2")

        def fake_gw(c):
            return gw_a if "val11111111" in c else gw_b

        def fake_sess(gw, node_id, **kw):
            return sess_a if gw is gw_a else sess_b

        monkeypatch.setattr(protocol, "Gateway", fake_gw)
        monkeypatch.setattr(chat, "ChatSession", fake_sess)

        code, out, _ = chay_cli(["gui", "Không được giao 2 lần"], tmp_path, monkeypatch)
        # Vì đã gửi việc rồi, không được chuyển sang acc2
        assert code == 4
        res = json.loads(out)
        assert res["ok"] is False
        assert len(gw_a.open_calls) == 1
        assert len(gw_b.open_calls) == 0  # Tuyệt đối không giao trên acc2


# ── Ca 6: Khóa flock tài khoản ──────────────────────────────────────────


class TestKhoaFlock:
    def test_giu_khoa_a_tu_dong_chuyen_b_va_giu_ca_hai_ra_ma_6(self, tmp_path, monkeypatch):
        tep_c = tmp_path / "cookies.json"
        tep_c.write_text(
            json.dumps({"tai_khoan": [
                {"ten": "acc1", "cookies": "k=val11111111"},
                {"ten": "acc2", "cookies": "k=val22222222"},
            ]}),
            encoding="utf-8",
        )
        tep_c.chmod(0o600)

        khoa_dir = tmp_path / "khoa"
        khoa_dir.mkdir(mode=0o700, exist_ok=True)
        lock_a = khoa_dir / "acc1.lock"
        fd_a = os.open(lock_a, os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(fd_a, fcntl.LOCK_EX | fcntl.LOCK_NB)

        gw_b = GatewayGia("acc2")
        sess_b = ChatSessionGia(gw_b, "node2")
        monkeypatch.setattr(protocol, "Gateway", lambda c: gw_b)
        monkeypatch.setattr(chat, "ChatSession", lambda gw, node_id, **kw: sess_b)

        # 1. acc1 bận -> tự động nhận acc2
        code, out, _ = chay_cli(["gui", "Chạy khi acc1 bận"], tmp_path, monkeypatch)
        assert code == 0
        res = json.loads(out)
        assert res["tai_khoan"] == "acc2"

        # 2. Khóa luôn acc2 -> hết tài khoản rảnh -> mã 6
        lock_b = khoa_dir / "acc2.lock"
        fd_b = os.open(lock_b, os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(fd_b, fcntl.LOCK_EX | fcntl.LOCK_NB)

        code, out, _ = chay_cli(["gui", "Không có tài khoản rảnh"], tmp_path, monkeypatch)
        assert code == 6
        res = json.loads(out)
        assert res["ok"] is False
        assert res["ma"] == "khong_co_tai_khoan_ranh"

        # Dọn khóa
        fcntl.flock(fd_a, fcntl.LOCK_UN)
        fcntl.flock(fd_b, fcntl.LOCK_UN)
        os.close(fd_a)
        os.close(fd_b)

    def test_nha_khoa_khi_hoan_tat_ke_ca_khi_loi(self, tmp_path, monkeypatch):
        tep_c = tmp_path / "cookies.json"
        tep_c.write_text(json.dumps({"tai_khoan": [{"ten": "acc1", "cookies": "k=val11111111"}]}), encoding="utf-8")
        tep_c.chmod(0o600)

        gw = GatewayGia()
        sess = ChatSessionGia(gw, "node1")
        sess.loi_khi_run = ValueError("Lỗi giữa chừng")
        monkeypatch.setattr(protocol, "Gateway", lambda c: gw)
        monkeypatch.setattr(chat, "ChatSession", lambda gw, node_id, **kw: sess)

        code, out, _ = chay_cli(["gui", "Lỗi ném ngoại lệ"], tmp_path, monkeypatch)
        assert code == 5
        # Lỗi xảy ra SAU khi chat.stream đã mở → không được mở lại lần hai
        assert len(gw.open_calls) == 1
        assert json.loads(out)["viec_da_gui"] is True

        # Kiểm tra xem file lock có bị giữ không
        lock_file = tmp_path / "khoa" / "acc1.lock"
        fd = os.open(lock_file, os.O_RDWR)
        # Thử lấy flock không chặn: nếu đã được nhả thì phải thành công
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


# ── Ca 7: Kết nối chết trước khi gửi -> nối lại đúng một lần ────────────


class TestNoiLaiKetNoi:
    def test_noi_lai_dung_mot_lan_truoc_khi_gui(self, tmp_path, monkeypatch):
        tep_c = tmp_path / "cookies.json"
        tep_c.write_text(json.dumps({"tai_khoan": [{"ten": "acc1", "cookies": "k=val11111111"}]}), encoding="utf-8")
        tep_c.chmod(0o600)

        gw_1 = GatewayGia("lan 1")
        gw_2 = GatewayGia("lan 2")
        so_lan_khoi_tao = 0

        def fake_gw(c):
            nonlocal so_lan_khoi_tao
            so_lan_khoi_tao += 1
            if so_lan_khoi_tao == 1:
                return gw_1
            return gw_2

        sess_1 = ChatSessionGia(gw_1, "node1")
        # Giả lập kết nối chết trước khi gọi open
        def fake_run_fail(prompt, co_huy=None):
            raise protocol.GatewayError(-1, "connection reset by peer")
        sess_1.run = fake_run_fail

        sess_2 = ChatSessionGia(gw_2, "node2")

        def fake_sess(gw, node_id, **kw):
            return sess_1 if gw is gw_1 else sess_2

        monkeypatch.setattr(protocol, "Gateway", fake_gw)
        monkeypatch.setattr(chat, "ChatSession", fake_sess)

        code, out, _ = chay_cli(["gui", "Thử nối lại"], tmp_path, monkeypatch)
        assert code == 0
        assert so_lan_khoi_tao == 2
        assert gw_1.da_dong is True
        assert len(gw_2.open_calls) == 1


# ── Ca 8: Lệnh lich-su lọc đúng, giới hạn đúng, không rò cookie ─────────


class TestLichSu:
    def test_lich_su_loc_va_gioi_han(self, tmp_path, monkeypatch):
        tep_c = tmp_path / "cookies.json"
        chuoi_bi_mat = "sec_val_43210987"
        tep_c.write_text(json.dumps({"tai_khoan": [{"ten": "acc1", "cookies": f"k={chuoi_bi_mat}"}]}), encoding="utf-8")
        tep_c.chmod(0o600)

        gw = GatewayGia()
        gw.lich_su = [
            {"event_name": "message.user", "seq": 1, "payload": {"display_text": "Chào bạn"}},
            {"event_name": "background.push", "seq": 2, "payload": {"display_text": "Bỏ qua sự kiện push"}},
            {"event_name": "message.assistant", "seq": 3, "payload": {"display_text": ""}},  # không chữ -> bỏ
            {"event_name": "message.assistant", "seq": 4, "payload": {"display_text": "Tôi là Muse"}},
        ]
        monkeypatch.setattr(protocol, "Gateway", lambda c: gw)

        code, out, err = chay_cli(["lich-su", "--gioi-han", "10"], tmp_path, monkeypatch)
        assert code == 0
        items = json.loads(out)
        assert len(items) == 2
        assert items[0] == {"seq": 1, "vai": "user", "van_ban": "Chào bạn"}
        assert items[1] == {"seq": 4, "vai": "assistant", "van_ban": "Tôi là Muse"}
        assert chuoi_bi_mat not in out
        assert chuoi_bi_mat not in err


# ── Ca 9: Kho cookie quyền lệch > 0600 & Ghi nguyên tử ──────────────────


class TestQuyenKhoVaGhiNguyenTu:
    def test_kho_cookie_quyen_lech_tu_choi_va_bao_cach_sua(self, tmp_path, monkeypatch):
        tep_c = tmp_path / "cookies.json"
        tep_c.write_text(json.dumps({"tai_khoan": [{"ten": "acc1", "cookies": "k=v"}]}), encoding="utf-8")
        tep_c.chmod(0o644)  # Quyền rộng hơn 0600

        code, out, _ = chay_cli(["cookie", "ds"], tmp_path, monkeypatch)
        assert code == 2
        res = json.loads(out)
        assert res["ok"] is False
        assert res["ma"] == "yeu_cau_sai"
        assert "rộng hơn 0600" in res["loi"]
        assert "chmod 600" in res["loi"]

    def test_ghi_nguyen_tu_khong_de_tep_tam(self, tmp_path, monkeypatch):
        code, _, _ = chay_cli(["cookie", "them", "--ten", "acc1"], tmp_path, monkeypatch, stdin_str="k=val123456789")
        assert code == 0
        # Kiểm tra không có tệp tạm .tmp_... trong thư mục
        tmps = list(tmp_path.glob(".tmp_cookies_*"))
        assert len(tmps) == 0


# ── Ca 10: Lệnh kiem tra & Không chứa nội dung thượng nguồn ─────────────


class TestKiemTraKhongChuaNoiDungThuongNguon:
    def test_kiem_ok_va_loi_khong_lo_email_thuong_nguon(self, tmp_path, monkeypatch):
        tep_c = tmp_path / "cookies.json"
        tep_c.write_text(
            json.dumps({"tai_khoan": [
                {"ten": "acc1", "cookies": "k=ok123456789"},
                {"ten": "acc2", "cookies": "k=err123456789"},
            ]}),
            encoding="utf-8",
        )
        tep_c.chmod(0o600)

        email_nhay_cam = "internal_admin_user@meta.ai"

        def fake_token(c):
            if "err123456789" in c:
                raise protocol.GatewayError(500, f"Upstream error leaking {email_nhay_cam}")
            return "access_tok"

        def fake_session(c):
            return {"vm_id": "vm123"}

        monkeypatch.setattr(protocol, "fetch_access_token", fake_token)
        monkeypatch.setattr(protocol, "fetch_session_info", fake_session)

        code, out, err = chay_cli(["kiem"], tmp_path, monkeypatch)
        assert code == 0  # Vì có acc1 ok nên ok toàn lượt = True -> thoát 0
        res = json.loads(out)
        assert len(res) == 2
        assert res[0]["ten"] == "acc1"
        assert res[0]["ok"] is True
        assert res[0]["loi"] is None

        assert res[1]["ten"] == "acc2"
        assert res[1]["ok"] is False
        assert "GatewayError" in res[1]["loi"]
        # Tuyệt đối không chứa email thượng nguồn
        assert email_nhay_cam not in out
        assert email_nhay_cam not in err


# ══ Các ca bổ sung sau lượt soát độc lập ═══════════════════════════════════

TEP_CLI = GOC_DICH_VU / "muse_web.py"
GOC_KHO_TEST = GOC_DICH_VU.parents[1]

MOT_TK = [{"ten": "acc1", "cookies": "k=val11111111"}]
HAI_TK = [{"ten": "acc1", "cookies": "k=val11111111"}, {"ten": "acc2", "cookies": "k=val22222222"}]

# Chuỗi bí mật GIẢ, đặc trưng để quét đầu ra (không phải cookie thật)
BI_MAT = "hatch_sess=BIMATGIA0000ZZZZ9999qqqq"
BI_MAT_TEN = "bimatgia0000zzzz9999qqqqwwww"  # khớp mẫu tên hợp lệ nhưng không có trong kho
DAU_BI_MAT = "bimatgia0000zzzz9999"  # so sánh trên bản chữ thường của đầu ra


def ghi_kho(thu_muc: Path, ds: list[dict]) -> None:
    tep = thu_muc / "cookies.json"
    tep.write_text(json.dumps({"tai_khoan": ds}), encoding="utf-8")
    tep.chmod(0o600)


def khoa_ranh(thu_muc: Path, ten: str) -> bool:
    """True nếu lấy được flock độc quyền không chặn trên khoá của tài khoản."""
    p = thu_muc / "khoa" / f"{ten}.lock"
    if not p.exists():
        return True
    fd = os.open(p, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return True
    except BlockingIOError:
        return False
    finally:
        os.close(fd)


class BoGia:
    """Gateway/ChatSession giả: đếm số lần dựng Gateway và gom mọi lần mở chat.stream
    trên MỌI gateway vào một danh sách chung. kich_ban[i] là hành vi của phiên trên gateway thứ i."""

    def __init__(self, kich_ban, loi_gateway=None):
        self.kich_ban = kich_ban
        self.loi_gateway = loi_gateway or []
        self.so_gateway = 0
        self.open_calls: list[dict] = []
        self.wait_s: list[float] = []

    def gateway(self, cookies):
        i = self.so_gateway
        self.so_gateway += 1
        if i < len(self.loi_gateway) and self.loi_gateway[i] is not None:
            raise self.loi_gateway[i]
        gw = GatewayGia(cookies)
        gw.open_calls = self.open_calls
        gw.lan = i
        return gw

    def session(self, gw, node_id, wait_s=300.0, **kw):
        self.wait_s.append(wait_s)
        hanh_vi = self.kich_ban[min(gw.lan, len(self.kich_ban) - 1)]

        class Phien:
            da_gui = False

            def run(self, prompt, co_huy=None):
                return hanh_vi(self, gw, prompt)

        return Phien()

    def cai(self, monkeypatch):
        monkeypatch.setattr(protocol, "Gateway", self.gateway)
        monkeypatch.setattr(chat, "ChatSession", self.session)


def hv_ok(s, gw, prompt):
    gw._open("chat.stream", body={"prompt": prompt})
    s.da_gui = True
    yield "xong"


def hv_chet_truoc_khi_gui(s, gw, prompt):
    # Ví dụ _last_seq hỏng vì ws đã chết: chưa mở chat.stream
    raise protocol.GatewayError(-1, "connection reset by peer")
    yield  # pragma: no cover


def hv_chet_sau_khi_gui(s, gw, prompt):
    gw._open("chat.stream", body={"prompt": prompt})
    s.da_gui = True
    raise protocol.GatewayError(-1, "connection reset by peer")
    yield  # pragma: no cover


def hv_het_gio_sau_khi_gui(s, gw, prompt):
    gw._open("chat.stream", body={"prompt": prompt})
    s.da_gui = True
    raise TimeoutError("no assistant reply")
    yield  # pragma: no cover


def hv_mot_phan_roi_chet(s, gw, prompt):
    gw._open("chat.stream", body={"prompt": prompt})
    s.da_gui = True
    yield "Phần đầu "
    yield "phần hai"
    raise protocol.GatewayError(-1, "poll lỗi 3 lần liên tiếp")


def chan_mang(monkeypatch):
    """Bảo đảm không ca nào chạm mạng thật."""
    def _loi(*a, **kw):
        raise protocol.GatewayError(599, "mạng bị chặn trong test")
    monkeypatch.setattr(protocol, "fetch_access_token", _loi)
    monkeypatch.setattr(protocol, "fetch_session_info", _loi)
    monkeypatch.setattr(protocol, "Gateway", _loi)


# ── Mục 1: lỗi cú pháp / đầu vào không chép giá trị người gọi ─────────────


CA_DAU_VAO_SAI = [
    ["cookie", "them", "--ten", "acc1", BI_MAT],
    ["cookie", "them", "--ten", BI_MAT],
    ["cookie", "them", "--ten=" + BI_MAT],
    ["cookie", "them", "--te=" + BI_MAT],
    ["cookie", "xoa", "--ten", BI_MAT],
    ["cookie", "xoa", "--ten", BI_MAT_TEN],
    ["cookie", "them", "--ten", "acc1", "--tep", "/khong/co/" + BI_MAT],
    ["cookie", "ds", BI_MAT],
    ["cookie", BI_MAT],
    [BI_MAT],
    ["gui", "--tep", "/khong/co/" + BI_MAT],
    ["gui", "--tai-khoan", BI_MAT, "việc"],
    ["gui", "--tai-khoan", BI_MAT_TEN, "việc"],
    ["gui", "--cho", BI_MAT, "việc"],
    ["gui", "--cho-ranh", BI_MAT, "việc"],
    ["gui", "--" + BI_MAT_TEN + "=" + BI_MAT, "việc"],
    ["gui", "việc", BI_MAT],
    ["kiem", "--tai-khoan", BI_MAT],
    ["kiem", "--tai-khoan", BI_MAT_TEN],
    ["kiem", BI_MAT],
    ["lich-su", "--tai-khoan", BI_MAT_TEN],
    ["lich-su", "--gioi-han", BI_MAT],
    ["lich-su", "--cho-ranh", BI_MAT],
]


class TestKhongChepDauVaoVaoLoi:
    @pytest.mark.parametrize("argv", CA_DAU_VAO_SAI)
    def test_loi_dau_vao_khong_chep_bi_mat(self, argv, tmp_path, monkeypatch):
        ghi_kho(tmp_path, MOT_TK)  # kho CÓ tài khoản để chạm tới nhánh «không tìm thấy»
        chan_mang(monkeypatch)
        code, out, err = chay_cli(argv, tmp_path, monkeypatch, stdin_str="")
        assert code == 2
        res = json.loads(out)
        assert res["ok"] is False and res["ma"] == "yeu_cau_sai"
        assert DAU_BI_MAT not in (out + err).lower()

    @pytest.mark.parametrize("argv", [
        ["cookie", "them", "--ten", "acc1", BI_MAT],
        ["cookie", "them", "--ten", BI_MAT],
        ["gui", "--cho", BI_MAT, "việc"],
        [BI_MAT],
    ])
    def test_loi_dau_vao_tien_trinh_that_khong_chep_bi_mat(self, argv, tmp_path):
        env = dict(os.environ, MUSE_WEB_DIR=str(tmp_path), PYTHONDONTWRITEBYTECODE="1")
        p = subprocess.run([sys.executable, str(TEP_CLI), *argv], stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, env=env, timeout=60)
        assert p.returncode == 2
        assert json.loads(p.stdout)["ma"] == "yeu_cau_sai"
        assert DAU_BI_MAT not in (p.stdout + p.stderr).lower()

    def test_loi_neu_ten_tham_so(self, tmp_path, monkeypatch):
        ghi_kho(tmp_path, MOT_TK)
        chan_mang(monkeypatch)
        _, out, _ = chay_cli(["gui", "--cho", "abc", "việc"], tmp_path, monkeypatch)
        assert "--cho" in json.loads(out)["loi"]
        _, out, _ = chay_cli(["cookie", "them", "--ten", "Sai!"], tmp_path, monkeypatch)
        assert "--ten" in json.loads(out)["loi"]
        _, out, _ = chay_cli(["gui", "--tai-khoan", "khongco", "việc"], tmp_path, monkeypatch)
        assert "--tai-khoan" in json.loads(out)["loi"]
        _, out, _ = chay_cli(["cookie", "ds", "a", "b"], tmp_path, monkeypatch)
        assert "2 đối số lạ" in json.loads(out)["loi"]


# ── Mục 2: không giao việc hai lần; JSON lỗi nói rõ việc đã gửi chưa ──────


class TestKhongGiaoHaiLan:
    def test_ws_chet_sau_khi_gui_khong_noi_lai(self, tmp_path, monkeypatch):
        ghi_kho(tmp_path, MOT_TK)
        bo = BoGia([hv_chet_sau_khi_gui, hv_ok])
        bo.cai(monkeypatch)
        code, out, _ = chay_cli(["gui", "việc"], tmp_path, monkeypatch)
        assert code == 5
        assert bo.so_gateway == 1
        assert len(bo.open_calls) == 1
        res = json.loads(out)
        assert res["ma"] == "loi_ket_noi"
        assert res["viec_da_gui"] is True
        assert "lich-su" in res["loi"]
        assert khoa_ranh(tmp_path, "acc1")

    def test_ws_chet_truoc_khi_gui_noi_lai_dung_mot_lan(self, tmp_path, monkeypatch):
        ghi_kho(tmp_path, MOT_TK)
        bo = BoGia([hv_chet_truoc_khi_gui, hv_ok])
        bo.cai(monkeypatch)
        code, out, _ = chay_cli(["gui", "việc"], tmp_path, monkeypatch)
        assert code == 0
        assert bo.so_gateway == 2
        assert len(bo.open_calls) == 1

    def test_ws_chet_truoc_khi_gui_ca_hai_lan_dung_lai(self, tmp_path, monkeypatch):
        ghi_kho(tmp_path, MOT_TK)
        bo = BoGia([hv_chet_truoc_khi_gui, hv_chet_truoc_khi_gui, hv_ok])
        bo.cai(monkeypatch)
        code, out, _ = chay_cli(["gui", "việc"], tmp_path, monkeypatch)
        assert code == 5
        assert bo.so_gateway == 2
        assert bo.open_calls == []
        assert json.loads(out)["viec_da_gui"] is False

    def test_gateway_khong_dung_duoc_viec_chua_gui(self, tmp_path, monkeypatch):
        ghi_kho(tmp_path, MOT_TK)
        loi = protocol.GatewayError(502, "bad gateway")
        bo = BoGia([hv_ok], loi_gateway=[loi, loi])
        bo.cai(monkeypatch)
        code, out, _ = chay_cli(["gui", "việc"], tmp_path, monkeypatch)
        assert code == 5
        assert json.loads(out)["viec_da_gui"] is False

    def test_het_gio_bat_tay_viec_chua_gui(self, tmp_path, monkeypatch):
        ghi_kho(tmp_path, MOT_TK)
        bo = BoGia([hv_ok], loi_gateway=[TimeoutError("Noise handshake quá hạn")])
        bo.cai(monkeypatch)
        code, out, _ = chay_cli(["gui", "việc"], tmp_path, monkeypatch)
        assert code == 3
        assert json.loads(out)["viec_da_gui"] is False
        assert bo.open_calls == []

    def test_het_gio_sau_khi_gui_bao_da_gui(self, tmp_path, monkeypatch):
        ghi_kho(tmp_path, MOT_TK)
        bo = BoGia([hv_het_gio_sau_khi_gui, hv_ok])
        bo.cai(monkeypatch)
        code, out, _ = chay_cli(["gui", "việc"], tmp_path, monkeypatch)
        assert code == 3
        res = json.loads(out)
        assert res["viec_da_gui"] is True
        assert "lich-su" in res["loi"]
        assert len(bo.open_calls) == 1


# ── Mục 3: thời gian chờ phải hữu hạn, dương, có trần ─────────────────────


class TestThoiGianCho:
    @pytest.mark.parametrize("co", [
        ["--cho", "nan"], ["--cho", "inf"], ["--cho", "-inf"], ["--cho", "0"], ["--cho", "-1"],
        ["--cho-ranh", "nan"], ["--cho-ranh", "inf"], ["--cho-ranh", "-1"],
    ])
    def test_gui_tu_choi_thoi_gian_khong_hop_le_va_khong_gui_gi(self, co, tmp_path, monkeypatch):
        ghi_kho(tmp_path, MOT_TK)
        bo = BoGia([hv_ok])
        bo.cai(monkeypatch)
        code, out, _ = chay_cli(["gui", *co, "việc"], tmp_path, monkeypatch)
        assert code == 2
        assert json.loads(out)["ma"] == "yeu_cau_sai"
        assert bo.so_gateway == 0
        assert bo.open_calls == []

    @pytest.mark.parametrize("gia_tri", ["nan", "inf", "-1"])
    def test_lich_su_tu_choi_cho_ranh_khong_hop_le(self, gia_tri, tmp_path, monkeypatch):
        ghi_kho(tmp_path, MOT_TK)
        bo = BoGia([hv_ok])
        bo.cai(monkeypatch)
        code, _, _ = chay_cli(["lich-su", "--cho-ranh", gia_tri], tmp_path, monkeypatch)
        assert code == 2
        assert bo.so_gateway == 0

    @pytest.mark.parametrize("cho,mong", [("5000", 1800.0), ("1800", 1800.0), ("60", 60.0)])
    def test_tran_cho_1800(self, cho, mong, tmp_path, monkeypatch):
        ghi_kho(tmp_path, MOT_TK)
        bo = BoGia([hv_ok])
        bo.cai(monkeypatch)
        code, _, _ = chay_cli(["gui", "--cho", cho, "việc"], tmp_path, monkeypatch)
        assert code == 0
        assert bo.wait_s == [mong]

    @pytest.mark.parametrize("lenh", [["gui", "việc"], ["lich-su"]])
    def test_tran_cho_ranh_3600(self, lenh, tmp_path, monkeypatch):
        ghi_kho(tmp_path, MOT_TK)
        bo = BoGia([hv_ok])
        bo.cai(monkeypatch)
        da_ghi: list[float] = []
        goc = muse_web.KhoaTaiKhoan.khoa

        def khoa_ghi(self, cho_ranh=0.0):
            da_ghi.append(cho_ranh)
            return goc(self, 0.0)

        monkeypatch.setattr(muse_web.KhoaTaiKhoan, "khoa", khoa_ghi)
        code, _, _ = chay_cli([lenh[0], "--cho-ranh", "99999", *lenh[1:]], tmp_path, monkeypatch)
        assert code == 0
        assert da_ghi == [3600.0]

    def test_cho_ranh_cho_roi_moi_lay_duoc_khoa(self, tmp_path, monkeypatch):
        ghi_kho(tmp_path, MOT_TK)
        bo = BoGia([hv_ok])
        bo.cai(monkeypatch)
        khoa_dir = tmp_path / "khoa"
        khoa_dir.mkdir(mode=0o700, exist_ok=True)
        fd = os.open(khoa_dir / "acc1.lock", os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

        def nha():
            time.sleep(0.5)
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

        t = threading.Thread(target=nha)
        t0 = time.time()
        t.start()
        code, out, _ = chay_cli(["gui", "--cho-ranh", "5", "việc"], tmp_path, monkeypatch)
        t.join()
        assert code == 0
        assert time.time() - t0 >= 0.4
        assert json.loads(out)["tai_khoan"] == "acc1"


# ── Mục 4: lỗi mạng sau khi đã nhận chữ giữ lại phần đã nhận ──────────────


class TestGiuMotPhan:
    def test_che_do_thuong_tra_mot_phan(self, tmp_path, monkeypatch):
        ghi_kho(tmp_path, MOT_TK)
        bo = BoGia([hv_mot_phan_roi_chet, hv_ok])
        bo.cai(monkeypatch)
        code, out, _ = chay_cli(["gui", "việc"], tmp_path, monkeypatch)
        assert code == 5
        res = json.loads(out)
        assert res["mot_phan"] == "Phần đầu phần hai"
        assert res["viec_da_gui"] is True
        assert "lich-su" in res["loi"]
        assert len(bo.open_calls) == 1

    def test_che_do_dong_giu_hanh_vi(self, tmp_path, monkeypatch):
        ghi_kho(tmp_path, MOT_TK)
        bo = BoGia([hv_mot_phan_roi_chet, hv_ok])
        bo.cai(monkeypatch)
        code, out, _ = chay_cli(["gui", "--dong", "việc"], tmp_path, monkeypatch)
        assert code == 5
        dong = [json.loads(x) for x in out.strip().splitlines()]
        assert [d.get("delta") for d in dong[:2]] == ["Phần đầu ", "phần hai"]
        assert dong[2]["ok"] is False and dong[2]["viec_da_gui"] is True
        assert "mot_phan" not in dong[2]


# ── Mục 5: đọc-sửa-ghi kho cookie có khoá ─────────────────────────────────


class TestKhoaKhoCookie:
    def test_hai_cookie_them_cung_luc_khong_mat_tai_khoan(self, tmp_path, monkeypatch):
        kho = tmp_path / "kho"
        kho.mkdir(mode=0o700)
        monkeypatch.setenv("MUSE_WEB_DIR", str(kho))
        dir_path = muse_web.lay_thu_muc_kho()
        goc = muse_web.doc_kho_cookie

        def doc_cham(d):
            ds = goc(d)
            time.sleep(0.3)  # nới rộng khe đua giữa đọc và ghi
            return ds

        monkeypatch.setattr(muse_web, "doc_kho_cookie", doc_cham)
        monkeypatch.setattr(sys, "stdout", io.StringIO())
        loi: list[BaseException] = []
        teps = {}
        for ten in ("tk-a", "tk-b"):
            teps[ten] = tmp_path / f"{ten}.txt"
            teps[ten].write_text(f"k=val{ten}12345678", encoding="utf-8")

        def chay(ten):
            try:
                muse_web.xu_ly_cookie_them(argparse.Namespace(ten=ten, tep=str(teps[ten])), dir_path)
            except BaseException as e:  # noqa: BLE001 — gom để khẳng định rỗng
                loi.append(e)

        luong = [threading.Thread(target=chay, args=(t,)) for t in ("tk-a", "tk-b")]
        for t in luong:
            t.start()
        for t in luong:
            t.join()
        assert loi == []
        assert sorted(i["ten"] for i in goc(dir_path)) == ["tk-a", "tk-b"]

    def test_xoa_va_them_cung_luc_giu_ca_hai_hieu_ung(self, tmp_path, monkeypatch):
        kho = tmp_path / "kho"
        kho.mkdir(mode=0o700)
        ghi_kho(kho, [{"ten": "tk-cu", "cookies": "k=valcu12345678"}])
        monkeypatch.setenv("MUSE_WEB_DIR", str(kho))
        dir_path = muse_web.lay_thu_muc_kho()
        goc = muse_web.doc_kho_cookie

        def doc_cham(d):
            ds = goc(d)
            time.sleep(0.3)
            return ds

        monkeypatch.setattr(muse_web, "doc_kho_cookie", doc_cham)
        monkeypatch.setattr(sys, "stdout", io.StringIO())
        tep_moi = tmp_path / "moi.txt"
        tep_moi.write_text("k=valmoi12345678", encoding="utf-8")
        loi: list[BaseException] = []

        def them():
            try:
                muse_web.xu_ly_cookie_them(argparse.Namespace(ten="tk-moi", tep=str(tep_moi)), dir_path)
            except BaseException as e:  # noqa: BLE001
                loi.append(e)

        def xoa():
            try:
                muse_web.xu_ly_cookie_xoa(argparse.Namespace(ten="tk-cu"), dir_path)
            except BaseException as e:  # noqa: BLE001
                loi.append(e)

        luong = [threading.Thread(target=them), threading.Thread(target=xoa)]
        for t in luong:
            t.start()
        for t in luong:
            t.join()
        assert loi == []
        assert [i["ten"] for i in goc(dir_path)] == ["tk-moi"]


# ── Mục 6: nhập cookie từ bàn phím không hiện chữ ─────────────────────────


class StdinTty:
    def __init__(self):
        self.da_doc = False

    def isatty(self):
        return True

    def read(self, *a):
        self.da_doc = True
        return ""

    @property
    def buffer(self):
        self.da_doc = True
        raise AssertionError("không được đọc stdin thô khi là tty")


class TestNhapCookieTuTty:
    @pytest.mark.parametrize("them_co", [[], ["--tep", "-"]])
    def test_tty_dung_getpass(self, them_co, tmp_path, monkeypatch):
        tty = StdinTty()
        goi: list[str] = []

        def gp(prompt=""):
            goi.append(prompt)
            return "hatch_sess=giatrigia00005678"

        monkeypatch.setattr(sys, "stdin", tty)
        monkeypatch.setattr("getpass.getpass", gp)
        code, out, err = chay_cli(["cookie", "them", "--ten", "acc1", *them_co], tmp_path, monkeypatch)
        assert code == 0
        assert json.loads(out)["khoa"] == "…5678"
        assert len(goi) == 1
        assert tty.da_doc is False
        assert "giatrigia" not in out + err


# ── Mục 7: kho phải thuộc người chạy, không là liên kết tượng trưng ───────


def _can_root():
    return pytest.mark.skipif(os.geteuid() != 0, reason="cần root để đổi chủ sở hữu tệp")


class TestQuyenKhoNghiemNgat:
    def test_tu_choi_cookies_json_la_symlink(self, tmp_path, monkeypatch):
        kho = tmp_path / "kho"
        kho.mkdir(mode=0o700)
        dich = tmp_path / "that.json"
        dich.write_text(json.dumps({"tai_khoan": [{"ten": "acc1", "cookies": BI_MAT}]}), encoding="utf-8")
        dich.chmod(0o600)
        (kho / "cookies.json").symlink_to(dich)
        code, out, err = chay_cli(["cookie", "ds"], tmp_path, monkeypatch, kho=kho)
        assert code == 2
        assert json.loads(out)["ma"] == "yeu_cau_sai"
        assert DAU_BI_MAT not in (out + err).lower()

    def test_tu_choi_thu_muc_kho_la_symlink(self, tmp_path, monkeypatch):
        that = tmp_path / "that"
        that.mkdir(mode=0o700)
        lien_ket = tmp_path / "lien_ket"
        lien_ket.symlink_to(that)
        code, out, _ = chay_cli(["cookie", "ds"], tmp_path, monkeypatch, kho=lien_ket)
        assert code == 2
        assert json.loads(out)["ma"] == "yeu_cau_sai"

    @_can_root()
    def test_tu_choi_cookies_json_khac_chu(self, tmp_path, monkeypatch):
        kho = tmp_path / "kho"
        kho.mkdir(mode=0o700)
        ghi_kho(kho, MOT_TK)
        os.chown(kho / "cookies.json", 65534, -1)
        code, out, _ = chay_cli(["cookie", "ds"], tmp_path, monkeypatch, kho=kho)
        assert code == 2
        assert json.loads(out)["ma"] == "yeu_cau_sai"

    @_can_root()
    def test_tu_choi_thu_muc_kho_khac_chu(self, tmp_path, monkeypatch):
        kho = tmp_path / "kho"
        kho.mkdir(mode=0o700)
        os.chown(kho, 65534, -1)
        code, out, _ = chay_cli(["cookie", "ds"], tmp_path, monkeypatch, kho=kho)
        assert code == 2
        assert json.loads(out)["ma"] == "yeu_cau_sai"

    def test_khong_chmod_duoc_thu_muc_bao_ma_2(self, tmp_path, monkeypatch):
        kho = tmp_path / "kho"
        kho.mkdir(mode=0o755)

        def tu_choi(*a, **kw):
            raise PermissionError(1, "Operation not permitted")

        monkeypatch.setattr(os, "chmod", tu_choi)
        monkeypatch.setattr(os, "fchmod", tu_choi)
        code, out, _ = chay_cli(["cookie", "ds"], tmp_path, monkeypatch, kho=kho)
        assert code == 2
        assert json.loads(out)["ma"] == "yeu_cau_sai"


# ── Mục 8: đầu vào/kho sai ra mã 2; lich-su xoay tài khoản ────────────────


KHO_HONG = [
    "{khong phai json",
    "[]",
    '{"tai_khoan": {}}',
    '{"tai_khoan": ["x"]}',
    '{"tai_khoan": [{"cookies": "k=v12345678"}]}',
    '{"tai_khoan": [{"ten": "acc1"}]}',
    '{"tai_khoan": [{"ten": 1, "cookies": "k=v12345678"}]}',
    '{"tai_khoan": [{"ten": "acc1", "cookies": ["k=v"]}]}',
]


class TestDauVaoVaKhoSai:
    @pytest.mark.parametrize("noi_dung", KHO_HONG)
    @pytest.mark.parametrize("lenh", [["cookie", "ds"], ["cookie", "xoa", "--ten", "acc1"],
                                      ["kiem"], ["gui", "việc"], ["lich-su"]])
    def test_kho_hong_ma_2(self, noi_dung, lenh, tmp_path, monkeypatch):
        tep = tmp_path / "cookies.json"
        tep.write_text(noi_dung, encoding="utf-8")
        tep.chmod(0o600)
        chan_mang(monkeypatch)
        code, out, _ = chay_cli(lenh, tmp_path, monkeypatch)
        assert code == 2
        assert json.loads(out)["ma"] == "yeu_cau_sai"

    def test_cookie_them_kho_hong_ma_2_khong_ghi_de(self, tmp_path, monkeypatch):
        tep = tmp_path / "cookies.json"
        tep.write_text("[]", encoding="utf-8")
        tep.chmod(0o600)
        code, _, _ = chay_cli(["cookie", "them", "--ten", "acc1"], tmp_path, monkeypatch, stdin_str="k=v12345678")
        assert code == 2
        assert tep.read_text(encoding="utf-8") == "[]"

    def test_stdin_khong_phai_utf8_ma_2(self, tmp_path, monkeypatch):
        code, out, _ = chay_cli(["cookie", "them", "--ten", "acc1"], tmp_path, monkeypatch,
                                stdin_bytes=b"k=\xff\xfe12345678")
        assert code == 2
        assert json.loads(out)["ma"] == "yeu_cau_sai"
        ghi_kho(tmp_path, MOT_TK)
        bo = BoGia([hv_ok])
        bo.cai(monkeypatch)
        code, out, _ = chay_cli(["gui", "-"], tmp_path, monkeypatch, stdin_bytes=b"vi\xffec")
        assert code == 2
        assert bo.so_gateway == 0

    def test_tep_khong_phai_utf8_ma_2(self, tmp_path, monkeypatch):
        tep = tmp_path / "nhi_phan.bin"
        tep.write_bytes(b"\xff\xfe\x00k=v")
        code, _, _ = chay_cli(["cookie", "them", "--ten", "acc1", "--tep", str(tep)], tmp_path, monkeypatch)
        assert code == 2
        ghi_kho(tmp_path, MOT_TK)
        bo = BoGia([hv_ok])
        bo.cai(monkeypatch)
        code, _, _ = chay_cli(["gui", "--tep", str(tep)], tmp_path, monkeypatch)
        assert code == 2
        assert bo.so_gateway == 0

    @pytest.mark.parametrize("lenh", [["gui", "việc"], ["kiem"], ["lich-su"]])
    def test_kho_rong_ma_2_chi_cach_them(self, lenh, tmp_path, monkeypatch):
        chan_mang(monkeypatch)
        code, out, _ = chay_cli(lenh, tmp_path, monkeypatch)
        assert code == 2
        res = json.loads(out)
        assert res["ma"] == "yeu_cau_sai"
        assert "cookie them" in res["loi"]

    def test_lich_su_auth_error_chuyen_tai_khoan_ke(self, tmp_path, monkeypatch):
        ghi_kho(tmp_path, HAI_TK)
        gw_b = GatewayGia("acc2")
        gw_b.lich_su = [{"event_name": "message.assistant", "seq": 1, "payload": {"display_text": "Từ acc2"}}]

        def fake_gw(c):
            if "val11111111" in c:
                raise protocol.AuthError("acc1 hết hạn")
            return gw_b

        monkeypatch.setattr(protocol, "Gateway", fake_gw)
        code, out, _ = chay_cli(["lich-su"], tmp_path, monkeypatch)
        assert code == 0
        assert json.loads(out)[0]["van_ban"] == "Từ acc2"
        assert khoa_ranh(tmp_path, "acc1") and khoa_ranh(tmp_path, "acc2")

    def test_lich_su_moi_tai_khoan_auth_error_ma_4(self, tmp_path, monkeypatch):
        ghi_kho(tmp_path, HAI_TK)

        def fake_gw(c):
            raise protocol.AuthError("hết hạn")

        monkeypatch.setattr(protocol, "Gateway", fake_gw)
        code, out, _ = chay_cli(["lich-su"], tmp_path, monkeypatch)
        assert code == 4
        assert json.loads(out)["ma"] == "khoa_hong"
        assert khoa_ranh(tmp_path, "acc1") and khoa_ranh(tmp_path, "acc2")


# ── Mục 9: khe hở test còn lại ────────────────────────────────────────────


class TestKheHoConLai:
    def test_cookie_them_trung_ten_cap_nhat_khong_them_dong(self, tmp_path, monkeypatch):
        chay_cli(["cookie", "them", "--ten", "acc1"], tmp_path, monkeypatch, stdin_str="k=aaaa11111111")
        code, _, _ = chay_cli(["cookie", "them", "--ten", "acc1"], tmp_path, monkeypatch, stdin_str="k=bbbb22222222")
        assert code == 0
        _, out, _ = chay_cli(["cookie", "ds"], tmp_path, monkeypatch)
        assert json.loads(out) == [{"ten": "acc1", "khoa": "…2222"}]

    def test_cookie_xoa_ten_khong_ton_tai_ma_2(self, tmp_path, monkeypatch):
        ghi_kho(tmp_path, MOT_TK)
        code, out, _ = chay_cli(["cookie", "xoa", "--ten", "acc9"], tmp_path, monkeypatch)
        assert code == 2
        assert json.loads(out)["ma"] == "yeu_cau_sai"
        assert json.loads((tmp_path / "cookies.json").read_text())["tai_khoan"] == MOT_TK

    def test_gui_stdin_rong_ma_2(self, tmp_path, monkeypatch):
        ghi_kho(tmp_path, MOT_TK)
        bo = BoGia([hv_ok])
        bo.cai(monkeypatch)
        code, _, _ = chay_cli(["gui", "-"], tmp_path, monkeypatch, stdin_str="")
        assert code == 2
        assert bo.so_gateway == 0

    @pytest.mark.parametrize("loai", ["khong_co", "thu_muc"])
    @pytest.mark.parametrize("lenh", [["gui"], ["cookie", "them", "--ten", "acc1"]])
    def test_tep_khong_ton_tai_hoac_la_thu_muc_ma_2(self, loai, lenh, tmp_path, monkeypatch):
        ghi_kho(tmp_path, MOT_TK)
        bo = BoGia([hv_ok])
        bo.cai(monkeypatch)
        duong = tmp_path / "khong_co.txt" if loai == "khong_co" else tmp_path
        code, out, _ = chay_cli([*lenh, "--tep", str(duong)], tmp_path, monkeypatch)
        assert code == 2
        assert json.loads(out)["ma"] == "yeu_cau_sai"
        assert bo.so_gateway == 0

    def test_lich_su_nha_khoa_khi_xong_va_khi_loi(self, tmp_path, monkeypatch):
        ghi_kho(tmp_path, MOT_TK)
        gw = GatewayGia()
        monkeypatch.setattr(protocol, "Gateway", lambda c: gw)
        code, _, _ = chay_cli(["lich-su"], tmp_path, monkeypatch)
        assert code == 0
        assert khoa_ranh(tmp_path, "acc1")
        assert gw.da_dong is True
        gw.loi_call_json = protocol.GatewayError(502, "bad gateway")
        code, _, _ = chay_cli(["lich-su"], tmp_path, monkeypatch)
        assert code == 5
        assert khoa_ranh(tmp_path, "acc1")

    def _kho_kiem(self, tmp_path, monkeypatch, loi_theo_tk):
        ghi_kho(tmp_path, HAI_TK)

        def fake_token(c):
            loi = loi_theo_tk["acc1" if "val11111111" in c else "acc2"]
            if loi is not None:
                raise loi
            return "tok"

        monkeypatch.setattr(protocol, "fetch_access_token", fake_token)
        monkeypatch.setattr(protocol, "fetch_session_info", lambda c: {"vm_id": "vm"})
        return chay_cli(["kiem"], tmp_path, monkeypatch)

    def test_kiem_moi_tai_khoan_auth_error_ma_4(self, tmp_path, monkeypatch):
        code, out, _ = self._kho_kiem(tmp_path, monkeypatch,
                                      {"acc1": protocol.AuthError("x"), "acc2": protocol.AuthError("y")})
        assert code == 4
        assert [r["ok"] for r in json.loads(out)] == [False, False]

    def test_kiem_loi_mang_ma_5(self, tmp_path, monkeypatch):
        code, _, _ = self._kho_kiem(tmp_path, monkeypatch,
                                    {"acc1": protocol.GatewayError(502, "x"), "acc2": protocol.GatewayError(502, "y")})
        assert code == 5
        code, _, _ = self._kho_kiem(tmp_path, monkeypatch,
                                    {"acc1": protocol.AuthError("x"), "acc2": protocol.GatewayError(502, "y")})
        assert code == 5


# ── Mục 8/9: chạy CLI thật bằng tiến trình con với cổng GIẢ ────────────────


TRINH_CHAY_GIA = r'''
import os, sys, time
sys.path.insert(0, sys.argv[1])
sys.path.insert(0, sys.argv[2])
from modules.muse2api import chat, protocol
import muse_web

DANH_DAU = os.environ["MUSE_GIA_DANH_DAU"]
CHE_DO = os.environ["MUSE_GIA_CHE_DO"]


def ghi(chu):
    with open(DANH_DAU, "a", encoding="utf-8") as fh:
        fh.write(chu + "\n")


class GW:
    def __init__(self, cookies):
        pass

    def _open(self, method, body=None, **kw):
        ghi("mo")

    def close(self):
        ghi("dong")


class S:
    def __init__(self, gw, node_id, **kw):
        self.gw = gw
        self.da_gui = False

    def run(self, prompt, co_huy=None):
        self.gw._open("chat.stream", body={})
        self.da_gui = True
        if CHE_DO == "treo":
            ghi("san_sang")  # ghi SAU khi main đã cài bộ bắt tín hiệu
            while not co_huy.wait(0.05):
                pass
            return
        i = 0
        while not co_huy.is_set():  # "nhieu": phát chữ mãi
            yield f"phan {i} "
            i += 1
            time.sleep(0.02)


protocol.Gateway = GW
chat.ChatSession = S
sys.exit(muse_web.main(sys.argv[3:]))
'''


def chay_tien_trinh_gia(tmp_path: Path, kho: Path, che_do: str, argv: list[str]):
    trinh = tmp_path / "chay_gia.py"
    trinh.write_text(TRINH_CHAY_GIA, encoding="utf-8")
    danh_dau = tmp_path / "danh_dau.txt"
    env = dict(os.environ, MUSE_WEB_DIR=str(kho), PYTHONDONTWRITEBYTECODE="1",
               MUSE_GIA_DANH_DAU=str(danh_dau), MUSE_GIA_CHE_DO=che_do)
    p = subprocess.Popen([sys.executable, str(trinh), str(GOC_KHO_TEST), str(GOC_DICH_VU), *argv],
                         stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, env=env)
    return p, danh_dau


def doc_danh_dau(danh_dau: Path) -> list[str]:
    return danh_dau.read_text(encoding="utf-8").split() if danh_dau.exists() else []


class TestTienTrinhThat:
    @pytest.mark.parametrize("tin_hieu,ma", [(signal.SIGTERM, 143), (signal.SIGINT, 130)])
    def test_tin_hieu_thoat_dung_ma_dong_ket_noi_nha_khoa(self, tin_hieu, ma, tmp_path):
        kho = tmp_path / "kho"
        kho.mkdir(mode=0o700)
        ghi_kho(kho, MOT_TK)
        p, danh_dau = chay_tien_trinh_gia(tmp_path, kho, "treo", ["gui", "việc dài"])
        try:
            han = time.time() + 30
            while "san_sang" not in doc_danh_dau(danh_dau):
                assert p.poll() is None, p.stderr.read()
                assert time.time() < han, "tiến trình con không sẵn sàng"
                time.sleep(0.05)
            assert not khoa_ranh(kho, "acc1")  # đang giữ khoá trong lúc chạy
            p.send_signal(tin_hieu)
            _, err = p.communicate(timeout=30)
        finally:
            if p.poll() is None:
                p.kill()
                p.communicate()
        assert p.returncode == ma
        assert "dong" in doc_danh_dau(danh_dau)
        assert doc_danh_dau(danh_dau).count("mo") == 1
        assert khoa_ranh(kho, "acc1")
        assert "Traceback" not in err

    def test_ong_dan_vo_giua_dong_thoat_em(self, tmp_path):
        kho = tmp_path / "kho"
        kho.mkdir(mode=0o700)
        ghi_kho(kho, MOT_TK)
        p, danh_dau = chay_tien_trinh_gia(tmp_path, kho, "nhieu", ["gui", "--dong", "việc"])
        try:
            dong_dau = p.stdout.readline()
            assert json.loads(dong_dau)["delta"].startswith("phan")
            p.stdout.close()  # người đọc bỏ đi giữa chừng
            err = p.stderr.read()
            p.wait(timeout=30)
        finally:
            if p.poll() is None:
                p.kill()
                p.wait()
        assert p.returncode == 141
        assert "Traceback" not in err
        assert "Exception ignored" not in err
        assert khoa_ranh(kho, "acc1")
        assert doc_danh_dau(danh_dau).count("mo") == 1
