"""Kiểm bảo mật dịch vụ, chạy được bằng unittest stdlib khi chưa có pytest.

Mọi tệp thử nằm trong cwd; mọi tiến trình/cổng hệ thống đều được giả.
Test HTTP dùng FastAPI thật nếu đã cài, không cài thêm gói để chạy test.
"""
from __future__ import annotations

import ast
import asyncio
import importlib.util
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

GOC = Path(os.path.relpath(__file__)).parent
sys.path.insert(0, str(GOC))
from src import mat_khau_vnc as mk
from src import novnc_bat_tat as nbt


class TestMatKhau(unittest.TestCase):
    def setUp(self):
        self.tam = tempfile.TemporaryDirectory(prefix=".nv305-test-", dir=".")
        self.addCleanup(self.tam.cleanup)
        self.khoa = Path(self.tam.name)

    def dat(self, gia_tri="VncGia01"):
        with patch.object(nbt, "nap_lai_x11", return_value=False):
            return mk.ghi_mat_khau_vnc(gia_tri, self.khoa)

    def test_validation_dung_bien_khong_cat_hoac_ep_kieu(self):
        for gia_tri in ("a", "Ab!$;123", "!@#$%^&*"):
            with self.subTest(hop_le=gia_tri):
                self.assertEqual(mk.kiem_mat_khau_vnc(gia_tri), "")
        for gia_tri in (None, 1, True, [], {}, "", "123456789", "ab cd", " ab", "ab ",
                        "abc\n", "a\r", "a\t", "a\0", "a\x1b", "a\x7f", "mậtkhẩu", "abc\u00a0"):
            with self.subTest(kieu=type(gia_tri).__name__):
                self.assertEqual(mk.kiem_mat_khau_vnc(gia_tri), mk.LOI_MAT_KHAU)
                with self.assertRaisesRegex(ValueError, "1–8"):
                    self.dat(gia_tri)
        self.assertEqual(list(self.khoa.iterdir()), [])

    def test_ghi_nguyen_tu_0600_va_chi_tra_bool(self):
        thay = os.replace
        dich = []

        def kiem_tra(tam, that):
            self.assertEqual(Path(os.path.relpath(tam)).parent, Path(os.path.relpath(that)).parent)
            self.assertEqual(Path(tam).stat().st_mode & 0o777, 0o600)
            dich.append(Path(that).name)
            thay(tam, that)

        with patch.object(nbt.os, "replace", side_effect=kiem_tra):
            kq = self.dat()
        self.assertEqual(dich, [nbt.TEN_TEP_MAT_KHAU, nbt.TEN_TEP_DANH_DAU])
        self.assertEqual(kq, {"ok": True, "da_nap_lai": False})
        self.assertTrue(mk.trang_thai_da_dat(self.khoa))
        self.assertEqual(nbt.doc_mat_khau(self.khoa), "VncGia01")
        for ten in dich:
            self.assertEqual((self.khoa / ten).stat().st_mode & 0o777, 0o600)
        self.assertEqual(len(list(self.khoa.iterdir())), 2)

    def test_ghi_hong_khong_danh_dau_va_khong_ro_hoac_de_tep_tam(self):
        with patch.object(nbt.os, "replace", side_effect=OSError("gia")):
            with self.assertRaises(OSError):
                self.dat()
        self.assertFalse(mk.trang_thai_da_dat(self.khoa))
        self.assertEqual(list(self.khoa.iterdir()), [])

    def test_ton_trong_mat_khau_cu_khi_ghi_hong(self):
        self.dat("CuGia001")
        with patch.object(nbt.os, "replace", side_effect=OSError("gia")):
            with self.assertRaises(OSError):
                self.dat("MoiGia01")
        self.assertEqual(nbt.doc_mat_khau(self.khoa), "CuGia001")

    def test_khong_di_theo_symlink_khi_ghi(self):
        dich = self.khoa / "dich-gia"
        dich.write_text("giu-nguyen")
        (self.khoa / nbt.TEN_TEP_MAT_KHAU).symlink_to(dich.name)
        self.dat()
        self.assertEqual(dich.read_text(), "giu-nguyen")
        self.assertFalse((self.khoa / nbt.TEN_TEP_MAT_KHAU).is_symlink())

    def test_nap_lai_hong_van_luu_va_khong_log_bi_mat(self):
        with patch.object(nbt, "nap_lai_x11", side_effect=OSError("VncGia01")):
            with self.assertLogs(mk.log, level="WARNING") as log:
                kq = mk.ghi_mat_khau_vnc("VncGia01", self.khoa)
        self.assertEqual(kq, {"ok": True, "da_nap_lai": False})
        self.assertNotIn("VncGia01", str(log.output))
        self.assertEqual(nbt.doc_mat_khau(self.khoa), "VncGia01")

    def test_chua_dat_hoac_thieu_mat_khau_khong_duoc_hien_va_bat(self):
        for ten in (nbt.TEN_TEP_MAT_KHAU, nbt.TEN_TEP_DANH_DAU):
            with self.subTest(thieu=ten):
                self.dat()
                (self.khoa / ten).unlink()
                self.assertFalse(mk.trang_thai_da_dat(self.khoa))
                with self.assertRaises(ValueError):
                    nbt.doc_mat_khau(self.khoa)
                with self.assertRaises(ValueError):
                    nbt.bat(self.khoa)

    def test_mat_khau_hong_khong_cho_bat_du_co_marker(self):
        self.dat()
        (self.khoa / nbt.TEN_TEP_MAT_KHAU).write_text("abc\n")
        with self.assertRaises(ValueError):
            nbt.bat(self.khoa)

    def test_tat_ben_thang_mac_dinh_bat_va_giu_mat_khau(self):
        self.dat()
        nbt.ghi_co(self.khoa, True)
        kq = nbt.tat(self.khoa, kiem_cong=lambda _: False, supervisor=lambda *_: None)
        self.assertFalse(kq["bat"])
        self.assertFalse(nbt.doc_co(self.khoa))
        self.assertEqual((self.khoa / nbt.TEN_TEP_CO).read_text(), "tat")
        tt = nbt.trang_thai(self.khoa, kiem_cong=lambda _: False, bat_mac_dinh=True)
        self.assertFalse(tt["bat"])
        self.assertTrue(all(isinstance(x, bool) for x in tt.values()))
        self.assertEqual(nbt.doc_mat_khau(self.khoa), "VncGia01")

    def test_tat_chua_du_phai_bao_loi_nhung_co_van_tat(self):
        with self.assertRaises(RuntimeError):
            nbt.tat(self.khoa, kiem_cong=lambda _: True, supervisor=lambda *_: False)
        self.assertFalse(nbt.doc_co(self.khoa))

    def test_bat_do_dang_khong_ghi_de_co_tat(self):
        self.dat()
        nbt.ghi_co(self.khoa, False)
        with self.assertRaises(RuntimeError):
            nbt.bat(self.khoa, kiem_cong=lambda _: False, spawn=lambda _: 1234,
                    supervisor=lambda *_: None, ngu=lambda _: None)
        self.assertFalse(nbt.doc_co(self.khoa))

    def test_bat_timeout_retry_giu_pid_cham_va_tat_duoc_tien_trinh_goc(self):
        for cong_cham in (5900, 6080):
            with self.subTest(cong_cham=cong_cham):
                self.dat()
                nbt.ghi_co(self.khoa, False)
                tien_trinh = {}
                da_spawn = []
                da_kill = []
                retry = False

                def mo_cong_cham():
                    for p in tien_trinh.values():
                        if p["song"]:
                            p["nghe"] = True

                def spawn(lenh):
                    pid = 1001 + len(da_spawn)
                    cong = 5900 if lenh[0] == nbt.LENH_X11[0] else 6080
                    # Khi retry spawn trùng, tiến trình cũ vừa chiếm cổng;
                    # tiến trình mới chết vì port busy, đúng ca advisor tái hiện.
                    if retry:
                        mo_cong_cham()
                    bi_trung = any(p["song"] and p["cong"] == cong for p in tien_trinh.values())
                    tien_trinh[pid] = {"lenh": list(lenh), "cong": cong,
                                       "song": not bi_trung, "nghe": cong != cong_cham}
                    da_spawn.append(pid)
                    return pid

                def kiem_cong(cong):
                    return any(p["song"] and p["nghe"] and p["cong"] == cong
                               for p in tien_trinh.values())

                def ngu(_):
                    if retry:
                        mo_cong_cham()

                def doc_proc(duong_dan):
                    self.assertEqual(duong_dan.name, "cmdline")
                    p = tien_trinh.get(int(duong_dan.parent.name))
                    if not p or not p["song"]:
                        raise FileNotFoundError("PID giả không còn sống")
                    return ("\0".join(p["lenh"]) + "\0").encode()

                def kill(pid, lenh):
                    self.assertEqual(tien_trinh[pid]["lenh"], lenh)
                    tien_trinh[pid]["song"] = False
                    da_kill.append(pid)

                with patch.object(Path, "read_bytes", autospec=True, side_effect=doc_proc):
                    with self.assertRaises(RuntimeError):
                        nbt.bat(self.khoa, kiem_cong=kiem_cong, spawn=spawn,
                                supervisor=lambda *_: None, ngu=ngu)
                    self.assertFalse(nbt.doc_co(self.khoa))
                    self.assertEqual((self.khoa / nbt.PID_X11).read_text(), "1001")
                    self.assertEqual((self.khoa / nbt.PID_WS).read_text(), "1002")
                    retry = True
                    kq = nbt.bat(self.khoa, kiem_cong=kiem_cong, spawn=spawn,
                                 supervisor=lambda *_: None, ngu=ngu)
                    pid_sau_retry = [(self.khoa / ten).read_text() for ten in (nbt.PID_X11, nbt.PID_WS)]
                    loi_tat = None
                    try:
                        nbt.tat(self.khoa, kiem_cong=kiem_cong, kill=kill, supervisor=lambda *_: None)
                    except RuntimeError as exc:
                        loi_tat = str(exc)

                self.assertEqual(da_spawn, [1001, 1002], "Retry không được spawn đè PID còn khớp argv")
                self.assertEqual(pid_sau_retry, ["1001", "1002"])
                self.assertTrue(kq["dang_chay"])
                self.assertIsNone(loi_tat)
                self.assertEqual(sorted(da_kill), [1001, 1002])
                self.assertFalse(any(p["song"] for p in tien_trinh.values()))
                self.assertFalse((self.khoa / nbt.PID_X11).exists())
                self.assertFalse((self.khoa / nbt.PID_WS).exists())
                self.assertFalse(nbt.doc_co(self.khoa))

    def test_khoi_dong_thay_pid_hong_chet_hoac_khong_khop_argv(self):
        tep = self.khoa / nbt.PID_X11
        lenh = nbt.lenh_x11(self.khoa)
        for noi_dung, proc in (("hong", b""), ("-1", b""), ("1001", b""),
                               ("1001", b"/usr/bin/logger\0x11vnc\0")):
            with self.subTest(noi_dung=noi_dung, proc=proc):
                tep.write_text(noi_dung)
                spawn = Mock(return_value=2001)
                with patch.object(Path, "read_bytes", return_value=proc):
                    nbt._khoi_dong("x11vnc", lenh, tep, spawn, lambda *_: None)
                spawn.assert_called_once_with(lenh)
                self.assertEqual(tep.read_text(), "2001")

    def test_bat_khong_spawn_lai_nua_da_chay(self):
        self.dat()
        cong = {5900: True, 6080: False}
        lenh = []

        def spawn(argv):
            lenh.append(argv)
            cong[6080] = True
            return 1234

        nbt.bat(self.khoa, kiem_cong=cong.__getitem__, spawn=spawn, supervisor=lambda *_: None)
        self.assertEqual(lenh, [nbt.LENH_WS])
        self.assertTrue(nbt.doc_co(self.khoa))

    def test_bat_ca_hai_bang_supervisor_khong_spawn_ngoai(self):
        self.dat()
        chay = set()

        def supervisor(hanh_dong, ten):
            if hanh_dong == "status":
                return "RUNNING" if ten in chay else "STOPPED"
            chay.add(ten)
            return True

        spawn = Mock()
        nbt.bat(self.khoa, kiem_cong=lambda cong: ("x11vnc" if cong == 5900 else "novnc") in chay,
                spawn=spawn, supervisor=supervisor)
        self.assertEqual(chay, {"x11vnc", "novnc"})
        spawn.assert_not_called()

    def test_bat_ca_hai_truc_tiep_dung_lenh_va_luu_pid(self):
        self.dat()
        cong_mo = set()
        lenhs = []

        def spawn(lenh):
            lenhs.append(lenh)
            cong_mo.add(5900 if lenh[0] == nbt.LENH_X11[0] else 6080)
            return 1234 + len(lenhs)

        kq = nbt.bat(self.khoa, kiem_cong=lambda cong: cong in cong_mo,
                     spawn=spawn, supervisor=lambda *_: None)
        self.assertTrue(kq["dang_chay"])
        self.assertEqual(lenhs, [nbt.lenh_x11(self.khoa), nbt.LENH_WS])
        self.assertNotIn("-nopw", lenhs[0])
        self.assertEqual((self.khoa / nbt.PID_X11).read_text(), "1235")
        self.assertEqual((self.khoa / nbt.PID_WS).read_text(), "1236")

    def test_tat_supervisor_du_hai_chuong_trinh(self):
        sup = Mock(side_effect=["RUNNING", True, "RUNNING", True])
        kq = nbt.tat(self.khoa, kiem_cong=lambda _: False, supervisor=sup)
        self.assertTrue(kq["da_ap_dung"])
        sup.assert_any_call("stop", "novnc")
        sup.assert_any_call("stop", "x11vnc")

    def test_co_hong_khong_log_noi_dung(self):
        (self.khoa / nbt.TEN_TEP_CO).write_text("BiMatGia")
        with self.assertLogs(nbt.log, level="WARNING") as log:
            self.assertIs(nbt.doc_co(self.khoa), False)
        self.assertNotIn("BiMatGia", str(log.output))

    def test_dung_pid_hop_le_ghi_nhan_tac_dong(self):
        tep = self.khoa / nbt.PID_X11
        tep.write_text("1234")
        kill = Mock()
        self.assertTrue(nbt._dung("x11vnc", tep, nbt.LENH_X11, kill,
                                  lambda *_: None, lambda *_: True))
        kill.assert_called_once_with(1234, nbt.LENH_X11)

    def test_pid_cu_khong_duoc_giet_va_van_dung_supervisor(self):
        for pid in ("1234", "-1", "0", "1", "hong"):
            tep = self.khoa / nbt.PID_X11
            tep.write_text(pid)
            kill = Mock()
            supervisor = Mock(side_effect=["RUNNING", True])
            self.assertTrue(nbt._dung("x11vnc", tep, nbt.LENH_X11, kill,
                                      supervisor, lambda *_: False))
            kill.assert_not_called()
            self.assertFalse(tep.exists())
            supervisor.assert_any_call("stop", "x11vnc")

    def test_reload_supervisor_khong_shell_va_khong_bi_mat_trong_argv(self):
        run = Mock(side_effect=[SimpleNamespace(stdout="x11vnc RUNNING pid 1234", returncode=0),
                                SimpleNamespace(stdout="", returncode=0)])
        with patch.object(nbt.subprocess, "run", run):
            self.assertTrue(nbt.nap_lai_x11(self.khoa))
        self.assertEqual(run.call_args_list[-1].args[0],
                         ["supervisorctl", "-c", nbt.CONF_SUPERVISOR_NOVNC, "restart", "x11vnc"])
        for goi in run.call_args_list:
            self.assertFalse(goi.kwargs.get("shell", False))

    def test_reload_khong_bat_lai_khi_co_tat(self):
        nbt.ghi_co(self.khoa, False)
        with patch.object(nbt, "_supervisor_thuc", return_value="FATAL") as sup:
            self.assertFalse(nbt.nap_lai_x11(self.khoa))
        sup.assert_not_called()

    def test_reload_spawn_chi_cho_pid_dung(self):
        (self.khoa / nbt.PID_X11).write_text("1234")
        with patch.object(nbt, "_supervisor_thuc", return_value=None), \
             patch.object(nbt, "_pid_khop", return_value=True), \
             patch.object(nbt, "_kill_thuc") as kill, \
             patch.object(nbt, "_spawn_thuc", return_value=5678) as spawn:
            self.assertTrue(nbt.nap_lai_x11(self.khoa))
        lenh = nbt.lenh_x11(self.khoa)
        kill.assert_called_once_with(1234, lenh)
        spawn.assert_called_once_with(lenh)
        self.assertEqual((self.khoa / nbt.PID_X11).read_text(), "5678")
        self.assertEqual(lenh[lenh.index("-passwdfile") + 1], str(self.khoa / nbt.TEN_TEP_MAT_KHAU))


class TestTienTrinh(unittest.TestCase):
    def test_pid_khop_chinh_xac_argv_va_chong_pid_nhom(self):
        for lenh in (nbt.LENH_X11, nbt.LENH_WS):
            for cmd, dung in ((lenh, True), (["/usr/bin/python3", *nbt.LENH_WS], lenh == nbt.LENH_WS),
                              (["/usr/bin/other", *lenh], False),
                              (["/usr/bin/logger", "x11vnc", "websockify"], False),
                              ([lenh[0] + "-gia", *lenh[1:]], False),
                              ([*lenh, "extra"], False)):
                with patch.object(Path, "read_bytes", return_value=("\0".join(cmd) + "\0").encode()):
                    self.assertEqual(nbt._pid_khop(1234, lenh), dung)
            with patch.object(Path, "read_bytes") as doc:
                for pid in (-1234, -1, 0, 1):
                    self.assertFalse(nbt._pid_khop(pid, lenh))
                doc.assert_not_called()

    def test_pidfd_chi_gui_signal_sau_khi_kiem_va_dong_fd(self):
        with patch.object(nbt.os, "pidfd_open", return_value=90) as mo, \
             patch.object(nbt, "_pid_khop", return_value=False) as kiem, \
             patch.object(nbt.signal, "pidfd_send_signal") as gui, \
             patch.object(nbt.os, "close") as dong:
            nbt._kill_thuc(1234, nbt.LENH_X11)
        mo.assert_called_once_with(1234)
        kiem.assert_called_once_with(1234, nbt.LENH_X11)
        gui.assert_not_called()
        dong.assert_called_once_with(90)

    def test_supervisor_stopped_exit_3_khong_bi_hieu_la_unmanaged(self):
        kq = SimpleNamespace(stdout="x11vnc STOPPED Not started", returncode=3)
        with patch.object(nbt.subprocess, "run", return_value=kq):
            self.assertEqual(nbt._supervisor_thuc("status", "x11vnc"), "STOPPED")

    def test_supervisor_khong_tim_thay_moi_tra_none(self):
        kq = SimpleNamespace(stdout="x11vnc: ERROR (no such process)", returncode=1)
        with patch.object(nbt.subprocess, "run", return_value=kq):
            self.assertIsNone(nbt._supervisor_thuc("status", "x11vnc"))


class TestEntrypoint(unittest.TestCase):
    def test_bo_kiem_entrypoint_cu_va_dot_bien(self):
        # Chỉ thực thi các bài kiểm entrypoint, không import/chạy bài đọc Dockerfile/compose.
        cay = ast.parse((GOC / "test_c2a_dockerfile.py").read_text())
        ten = {"kiem_entrypoint", "test_entrypoint_chuan", "test_dot_bien_entrypoint"}
        mod = ast.Module(body=[n for n in cay.body if isinstance(n, ast.FunctionDef) and n.name in ten],
                         type_ignores=[])
        ns = {"re": re, "_doc_entrypoint": lambda: (GOC / "entrypoint.sh").read_text()}
        exec(compile(mod, "test_c2a_dockerfile.py:entrypoint-only", "exec"), ns)
        ns["test_entrypoint_chuan"]()
        ns["test_dot_bien_entrypoint"]()

    def test_lua_chon_conf_thuc_thi_bang_sh_trong_cwd(self):
        script = (GOC / "entrypoint.sh").read_text()
        # Giữ toàn bộ nhánh chọn conf thật; thay các thao tác hệ thống bằng builtin giả.
        script = "mkdir() { :; }; chmod() { :; };\n" + script
        script = script.replace("exec /usr/bin/supervisord -c", "printf '%s\\n'")
        with tempfile.TemporaryDirectory(prefix=".nv305-entrypoint-", dir=".") as tam:
            co = Path(tam) / "novnc-bat"
            script = script.replace("/khoa", tam)
            for gia_tri, env, cho in ((None, "", "supervisord.conf"), (None, "1", "supervisord-novnc.conf"),
                                     (None, "true", "supervisord-novnc.conf"),
                                     (b"tat", "1", "supervisord.conf"), (b"bat", "", "supervisord-novnc.conf"),
                                     (b"bat\n", "", "supervisord-novnc.conf"),
                                     (b"linh-tinh", "1", "supervisord.conf"),
                                     (b"", "true", "supervisord.conf"),
                                     (b"\xff", "1", "supervisord.conf"),
                                     (b"bat\nlinh-tinh", "1", "supervisord.conf"),
                                     (b"bat\0", "1", "supervisord.conf")):
                if gia_tri is not None:
                    co.write_bytes(gia_tri)
                else:
                    co.unlink(missing_ok=True)
                kq = subprocess.run(["sh", "-s"], input=script, text=True, capture_output=True,
                                    env={**os.environ, "CAPTCHA_SOLVER_BAT_NOVNC": env,
                                         "PYTHONPATH": str(GOC)}, check=True)
                self.assertEqual(Path(kq.stdout.strip()).name, cho)
            # Lỗi đọc thật, kể cả khi test chạy bằng root: cờ là thư mục.
            co.unlink()
            co.mkdir()
            kq = subprocess.run(["sh", "-s"], input=script, text=True, capture_output=True,
                                env={**os.environ, "CAPTCHA_SOLVER_BAT_NOVNC": "1",
                                     "PYTHONPATH": str(GOC)}, check=True)
            self.assertEqual(Path(kq.stdout.strip()).name, "supervisord.conf")

    def test_chan_doan_va_cac_hanh_vi_main_duoc_giu(self):
        cay = ast.parse((GOC / "src/main.py").read_text())
        nhap = [alias.name for n in cay.body if isinstance(n, ast.ImportFrom) and n.level == 1
                for alias in n.names]
        self.assertIn("chan_doan", nhap)
        self.assertTrue(any(isinstance(n, ast.AsyncFunctionDef) and n.name == "api_chan_doan" for n in cay.body))


CO_HTTP = all(importlib.util.find_spec(ten) is not None
              for ten in ("fastapi", "httpx", "pydantic_settings"))


@unittest.skipUnless(CO_HTTP, "Thiếu FastAPI/httpx/pydantic-settings trong runtime hiện tại")
class TestHttp(unittest.TestCase):
    def setUp(self):
        self.tam = tempfile.TemporaryDirectory(prefix=".nv305-http-", dir=".")
        self.addCleanup(self.tam.cleanup)
        self.khoa = Path(self.tam.name)
        self.headers = {"Authorization": "Bearer khoa-gia-0123456789abcdef-ghi-ro-la-gia"}
        env = patch.dict(os.environ, {
            "CAPTCHA_SOLVER_API_KEY": self.headers["Authorization"].split()[1],
            "CAPTCHA_SOLVER_DATA_DIR": str(self.khoa), "CAPTCHA_SOLVER_KEY_DIR": str(self.khoa),
            "ACCOUNTS_DB": str(self.khoa / "accounts.db"), "CAPTCHA_SOLVER_TINH_NANG_BAT": "dang_nhap",
            "HTTP_PROXY": "http://127.0.0.1:9", "HTTPS_PROXY": "http://127.0.0.1:9",
            "ALL_PROXY": "http://127.0.0.1:9", "NO_PROXY": "127.0.0.1,localhost",
        })
        env.start()
        self.addCleanup(env.stop)
        # Không đọc .env khi Settings() được import; browser dùng stub sẵn có của repo.
        with patch("pydantic_settings.sources.DotEnvSettingsSource.__call__", return_value={}):
            import _gia_goi  # noqa: F401
            from src import main
        self.main = main
        cai = patch.object(main.settings, "key_dir", self.khoa)
        cai.start()
        self.addCleanup(cai.stop)
        from fastapi.testclient import TestClient
        self.client = TestClient(main.app)
        self.addCleanup(self.client.close)

    def test_moi_route_can_bearer_va_nhom_dang_nhap(self):
        routes = [("PUT", "/v1/novnc/mat-khau"), ("GET", "/v1/novnc/mat-khau"),
                  ("GET", "/v1/novnc/mat-khau/trang-thai"), ("PUT", "/v1/novnc/bat-tat"),
                  ("GET", "/v1/novnc/trang-thai")]
        for phuong_thuc, url in routes:
            for headers in ({}, {"Authorization": "Bearer khoa-gia-sai"}):
                self.assertEqual(self.client.request(phuong_thuc, url, headers=headers).status_code, 401)
            with patch.dict(os.environ, {"CAPTCHA_SOLVER_TINH_NANG_BAT": "giai_captcha"}):
                r = self.client.request(phuong_thuc, url, headers=self.headers)
                self.assertEqual(r.status_code, 403)
                self.assertEqual(r.json()["ma_loi"], "tinh_nang_tat")

    def test_dat_trang_thai_va_hien_chi_dung_truong_bi_mat(self):
        with patch.object(nbt, "nap_lai_x11", return_value=False):
            r = self.client.put("/v1/novnc/mat-khau", headers=self.headers, json={"mat_khau": "VncGia01"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"ok": True, "da_nap_lai": False})
        r = self.client.get("/v1/novnc/mat-khau/trang-thai", headers=self.headers)
        self.assertEqual(r.json(), {"ok": True, "da_dat": True})
        r = self.client.get("/v1/novnc/mat-khau", headers=self.headers)
        self.assertEqual(r.json(), {"ok": True, "mat_khau": "VncGia01"})
        self.assertEqual(r.headers["cache-control"], "no-store")

    def test_loi_body_khong_ro_mat_khau_ke_ca_loi_pydantic(self):
        for url, truong in (("/v1/novnc/mat-khau", "mat_khau"), ("/v1/novnc/bat-tat", "bat")):
            for body in ({truong: {"bi_mat": "BiMatGia"}}, ["BiMatGia"], {"bi_mat": "BiMatGia"},
                         {truong: "BiMatGia dai"}, {truong: None}, {truong: 1}):
                r = self.client.put(url, headers=self.headers, json=body)
                self.assertEqual(r.status_code, 400)
                self.assertNotIn("BiMatGia", r.text)
                self.assertNotIn("input", r.text)
            r = self.client.put(url, headers={**self.headers, "Content-Type": "application/json"},
                                content='{"BiMatGia":')
            self.assertEqual(r.status_code, 400)
            self.assertNotIn("BiMatGia", r.text)

    def test_hien_chua_dat_400_va_loi_he_thong_khong_ro(self):
        r = self.client.get("/v1/novnc/mat-khau", headers=self.headers)
        self.assertEqual(r.status_code, 400)
        for ham, method, url, body in (("doc_mat_khau", "GET", "/v1/novnc/mat-khau", None),
                                       ("bat", "PUT", "/v1/novnc/bat-tat", {"bat": True})):
            with patch.object(nbt, ham, side_effect=OSError("BiMatGia")):
                with self.assertLogs(self.main.logger, level="WARNING") as log:
                    r = self.client.request(method, url, headers=self.headers, json=body)
            self.assertEqual(r.status_code, 502)
            self.assertNotIn("BiMatGia", r.text + str(log.output))

    def test_tat_route_co_ben_va_health_ton_trong_co(self):
        # Chỉ giả biên hệ thống, gọi thật handler -> tat() -> ghi cờ.
        with patch.dict(os.environ, {"CAPTCHA_SOLVER_BAT_NOVNC": "1"}), \
             patch.object(self.main.vault, "trang_thai_ket", return_value=(True, "san_sang")), \
             patch.object(nbt.socket, "create_connection", side_effect=ConnectionRefusedError), \
             patch.object(nbt.subprocess, "run", side_effect=FileNotFoundError):
            r = self.client.put("/v1/novnc/bat-tat", headers=self.headers, json={"bat": False})
            self.assertEqual(r.status_code, 200)
            self.assertFalse(r.json()["bat"])
            self.assertIsNone(self.client.get("/health").json()["novnc"])
            tt = self.client.get("/v1/novnc/trang-thai", headers=self.headers)
            self.assertFalse(tt.json()["bat"])
            self.assertTrue(all(isinstance(v, bool) for v in tt.json().values()))

    def test_lifespan_khoi_dong_theo_co_khong_ro_loi(self):
        pool = SimpleNamespace(start=unittest.mock.AsyncMock(), stop=unittest.mock.AsyncMock())

        async def chay():
            async with self.main.lifespan(self.main.app):
                pass

        nbt.ghi_co(self.khoa, True)
        with patch.object(self.main, "pool", pool), \
             patch.object(nbt, "bat", side_effect=OSError("BiMatGia")) as bat:
            with self.assertLogs(self.main.logger, level="WARNING") as log:
                asyncio.run(chay())
        bat.assert_called_once_with(self.khoa)
        pool.stop.assert_awaited_once()
        self.assertNotIn("BiMatGia", str(log.output))


if __name__ == "__main__":
    unittest.main()
