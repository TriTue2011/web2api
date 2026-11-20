"""NV305-bao-mat — Công tắc noVNC runtime phía dịch vụ may-dang-nhap.

Module thuần novnc_bat_tat (chỉ stdlib, không FastAPI) để test được trên máy này
(máy này không import được src.main vì thiếu patchright — như giới hạn của NV305):
- Cờ bền /khoa/novnc-bat («bat»/«tat», ghi nguyên tử 0600): cờ có thì cờ thắng
  biến môi trường lúc khởi động (entrypoint đọc cờ trước).
- Bật khi chưa đặt mật khẩu qua giao diện → từ chối (không bao giờ chạy xem
  không mật khẩu, nhất quán bo-sung: không -nopw).
- Bật/tắt dùng supervisorctl khi chương trình do supervisor quản; nếu không thì
  spawn/kill trực tiếp bằng PID file (chỉ giết đúng PID mình tạo, không pkill mò).
- Lệnh spawn đúng như supervisord-novnc.conf: x11vnc -passwdfile, không -nopw;
  websockify 0.0.0.0:6080 → 127.0.0.1:5900.

Test dùng thư mục tạm + hàm giả cho cổng/tiến trình/supervisorctl, không docker thật.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src import novnc_bat_tat as nbt


class CongGia:
    """Bộ kiểm cổng giả: trạng thái nghe do test tự lật."""

    def __init__(self, x11=False, ws=False):
        self.trang_thai = {5900: x11, 6080: ws}

    def __call__(self, cong):
        return self.trang_thai[cong]


class TienTrinhGia:
    """Bộ chạy tiến trình giả: ghi lại lệnh spawn/kill, không chạy thật."""

    def __init__(self):
        self.lenhs = []
        self.kills = []
        self.pid_tiep = [1000]

    def spawn(self, lenh):
        self.pid_tiep[0] += 1
        self.lenhs.append(list(lenh))
        return self.pid_tiep[0]

    def kill(self, pid, lenh):
        self.kills.append(pid)


class SupervisorGia:
    """supervisorctl giả: chương trình nào được khai là 'do supervisor quản'."""

    def __init__(self, quan=None, trang_thai=None):
        self.quan = set(quan or [])
        self.trang_thai = trang_thai or {}
        self.lenh = []

    def __call__(self, *doi_so):
        self.lenh.append(list(doi_so))
        if doi_so[0] == "status":
            ten = doi_so[1]
            if ten not in self.quan:
                return None
            return self.trang_thai.get(ten, "STOPPED")
        return True  # start/stop luôn thành công


def _khoa(tmp_path):
    khoa = tmp_path / "khoa"
    khoa.mkdir(parents=True, exist_ok=True)
    return khoa


def test_co_bat_tat_vong_tron(tmp_path):
    khoa = _khoa(tmp_path)
    assert nbt.doc_co(khoa) is None
    nbt.ghi_co(khoa, True)
    assert nbt.doc_co(khoa) is True
    nbt.ghi_co(khoa, False)
    assert nbt.doc_co(khoa) is False
    tep = khoa / nbt.TEN_TEP_CO
    assert (tep.stat().st_mode & 0o777) == 0o600
    nbt.xoa_co(khoa)
    assert nbt.doc_co(khoa) is None


@pytest.mark.parametrize("noi_dung", [b"linh-tinh", b"", b"\xff", b"bat\nlinh-tinh", b"bat\0"])
def test_co_hong_giu_tat_du_mac_dinh_bat(tmp_path, noi_dung):
    khoa = _khoa(tmp_path)
    (khoa / nbt.TEN_TEP_CO).write_bytes(noi_dung)
    assert nbt.doc_co(khoa) is False
    assert nbt.trang_thai(khoa, kiem_cong=CongGia(), bat_mac_dinh=True)["bat"] is False


@pytest.mark.parametrize("loi", [PermissionError, IsADirectoryError, OSError])
def test_loi_doc_co_khong_duoc_dung_mac_dinh_bat(tmp_path, monkeypatch, loi):
    khoa = _khoa(tmp_path)
    tep = khoa / nbt.TEN_TEP_CO
    tep.write_text("bat", encoding="utf-8")
    doc_goc = Path.read_text

    def doc(duong_dan, *args, **kwargs):
        if duong_dan == tep:
            raise loi("loi doc gia")
        return doc_goc(duong_dan, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", doc)
    assert nbt.doc_co(khoa) is False
    assert nbt.trang_thai(khoa, kiem_cong=CongGia(), bat_mac_dinh=True)["bat"] is False


def test_chi_thieu_co_moi_dung_mac_dinh_bat(tmp_path):
    khoa = _khoa(tmp_path)
    assert nbt.doc_co(khoa) is None
    assert nbt.trang_thai(khoa, kiem_cong=CongGia(), bat_mac_dinh=True)["bat"] is True


def test_bat_tu_choi_khi_chua_dat_mat_khau(tmp_path):
    khoa = _khoa(tmp_path)
    cong = CongGia()
    tt = TienTrinhGia()
    sup = SupervisorGia()
    with pytest.raises(ValueError, match="Chưa đặt mật khẩu"):
        nbt.bat(khoa, kiem_cong=cong, spawn=tt.spawn, supervisor=sup)
    assert tt.lenhs == []
    assert nbt.doc_co(khoa) is None, "bật hỏng thì cờ không được ghi là bật"


def _dat_mat_khau(khoa):
    (khoa / "mat-khau-vnc").write_text("VncBm01", encoding="utf-8")
    (khoa / "mat-khau-vnc-chu-dat").write_text("", encoding="utf-8")


def test_bat_spawn_dung_lenh_va_khong_nopw(tmp_path):
    khoa = _khoa(tmp_path)
    _dat_mat_khau(khoa)
    cong = CongGia()
    tt = TienTrinhGia()
    sup = SupervisorGia()

    def kiem_cong_mo_dan(cong_so):
        # Sau khi spawn, cổng coi như đã nghe
        cong.trang_thai[5900] = any(x[0].endswith("x11vnc") for x in tt.lenhs)
        cong.trang_thai[6080] = any(x[0].endswith("websockify") for x in tt.lenhs)
        return cong.trang_thai[cong_so]

    kq = nbt.bat(khoa, kiem_cong=kiem_cong_mo_dan, spawn=tt.spawn, supervisor=sup)
    assert kq["bat"] is True and kq["da_ap_dung"] is True and kq["dang_chay"] is True
    assert nbt.doc_co(khoa) is True
    assert len(tt.lenhs) == 2
    lenh_x11, lenh_ws = tt.lenhs
    assert lenh_x11[0].endswith("x11vnc")
    assert "-passwdfile" in lenh_x11 and "-nopw" not in lenh_x11
    assert "127.0.0.1" in lenh_x11 and "5900" in lenh_x11
    assert lenh_ws[0].endswith("websockify")
    assert "0.0.0.0:6080" in lenh_ws and "127.0.0.1:5900" in lenh_ws
    assert (khoa / "novnc-x11vnc.pid").exists()
    assert (khoa / "novnc-websockify.pid").exists()


def test_bat_khi_dang_chay_thi_khong_spawn_lai(tmp_path):
    khoa = _khoa(tmp_path)
    _dat_mat_khau(khoa)
    cong = CongGia(x11=True, ws=True)
    tt = TienTrinhGia()
    sup = SupervisorGia()
    kq = nbt.bat(khoa, kiem_cong=cong, spawn=tt.spawn, supervisor=sup)
    assert kq["bat"] is True and kq["da_ap_dung"] is False
    assert tt.lenhs == []
    assert nbt.doc_co(khoa) is True


def test_bat_dung_supervisorctl_khi_do_supervisor_quan(tmp_path):
    khoa = _khoa(tmp_path)
    _dat_mat_khau(khoa)
    cong = CongGia()
    tt = TienTrinhGia()
    sup = SupervisorGia(quan={"x11vnc", "novnc"}, trang_thai={"x11vnc": "STOPPED", "novnc": "STOPPED"})

    def kiem_cong(cong_so):
        # Sau khi supervisorctl start, cổng coi như đã nghe
        ten = "x11vnc" if cong_so == 5900 else "novnc"
        return ["start", ten] in sup.lenh or cong.trang_thai[cong_so]

    kq = nbt.bat(khoa, kiem_cong=kiem_cong, spawn=tt.spawn, supervisor=sup)
    assert tt.lenhs == [], "do supervisor quản thì không spawn trực tiếp"
    assert ["start", "x11vnc"] in sup.lenh
    assert ["start", "novnc"] in sup.lenh
    assert kq["bat"] is True and nbt.doc_co(khoa) is True


def test_tat_giet_dung_pid_va_luu_co_tat_ben(tmp_path):
    khoa = _khoa(tmp_path)
    _dat_mat_khau(khoa)
    nbt.ghi_co(khoa, True)
    (khoa / "novnc-x11vnc.pid").write_text("1001", encoding="utf-8")
    (khoa / "novnc-websockify.pid").write_text("1002", encoding="utf-8")
    tt = TienTrinhGia()
    sup = SupervisorGia()
    kq = nbt.tat(khoa, kiem_cong=CongGia(), kill=tt.kill, supervisor=sup,
                kiem_pid=lambda pid, tk: True)
    assert kq["bat"] is False
    assert sorted(tt.kills) == [1001, 1002]
    assert nbt.doc_co(khoa) is False
    assert (khoa / nbt.TEN_TEP_CO).read_text() == "tat"
    assert nbt.trang_thai(khoa, kiem_cong=CongGia(), bat_mac_dinh=True)["bat"] is False
    assert nbt.doc_mat_khau(khoa) == "VncBm01"
    assert not (khoa / "novnc-x11vnc.pid").exists()


def test_tat_dung_supervisorctl_khi_do_supervisor_quan(tmp_path):
    khoa = _khoa(tmp_path)
    nbt.ghi_co(khoa, True)
    tt = TienTrinhGia()
    sup = SupervisorGia(quan={"x11vnc", "novnc"}, trang_thai={"x11vnc": "RUNNING", "novnc": "RUNNING"})
    kq = nbt.tat(khoa, kiem_cong=CongGia(), kill=tt.kill, supervisor=sup,
                kiem_pid=lambda pid, tk: True)
    assert ["stop", "x11vnc"] in sup.lenh
    assert ["stop", "novnc"] in sup.lenh
    assert tt.kills == []
    assert kq["bat"] is False


def test_trang_thai_bao_du_bool_khong_mat_khau(tmp_path):
    khoa = _khoa(tmp_path)
    _dat_mat_khau(khoa)
    nbt.ghi_co(khoa, True)
    tt = nbt.trang_thai(khoa, kiem_cong=CongGia(x11=True, ws=False))
    assert tt["bat"] is True and tt["x11vnc"] is True and tt["websockify"] is False
    assert tt["dang_chay"] is False and tt["da_dat"] is True
    assert "mat_khau" not in tt


def test_hien_mat_khau_doi_danh_dau(tmp_path):
    """Đọc mật khẩu chỉ khi có tệp đánh dấu (đặt qua giao diện); tệp cũ sót lại thì từ chối."""
    khoa = _khoa(tmp_path)
    (khoa / "mat-khau-vnc").write_text("MatKhauCuSot", encoding="utf-8")
    with pytest.raises(ValueError, match="Chưa đặt mật khẩu"):
        nbt.doc_mat_khau(khoa)
    _dat_mat_khau(khoa)
    assert nbt.doc_mat_khau(khoa) == "VncBm01"


@pytest.mark.parametrize("dau", ["bat", "tat", "reload", "password"])
@pytest.mark.parametrize("sau", ["bat", "tat", "reload", "password"])
def test_hai_luong_dung_chung_khoa_toan_vong(tmp_path, monkeypatch, dau, sau):
    """Dừng lượt đầu ở biên OS; lượt sau phải đợi, kể cả ghi + reload lồng nhau."""
    from src import mat_khau_vnc

    khoa = _khoa(tmp_path)
    _dat_mat_khau(khoa)
    dang_song = {}
    da_spawn = []
    da_kill = []
    da_chan = threading.Event()
    cho_tiep = threading.Event()
    luot_sau_da_den = threading.Event()
    chen_ngang = []
    loi = []
    ket_qua = {}
    khoa_gia = getattr(nbt, "KHOA_NOVNC", threading.RLock())

    class KhoaTheoDoi:
        def __enter__(self):
            if threading.current_thread().name == "nv305-sau":
                luot_sau_da_den.set()
            khoa_gia.acquire()
            return self

        def __exit__(self, *args):
            khoa_gia.release()

    # Theo dõi lần thử lấy khóa, không dùng sleep để đoán luồng sau đã chạy.
    # Mã cũ chưa có khóa vẫn chạy tới OS giả và bị bắt vì chen vào lượt đầu.
    monkeypatch.setattr(nbt, "KHOA_NOVNC", KhoaTheoDoi(), raising=False)

    def bien_os():
        ten = threading.current_thread().name
        if ten == "nv305-dau" and not da_chan.is_set():
            da_chan.set()
            assert cho_tiep.wait(5), "Test chưa nhả biên OS của lượt đầu"
        elif ten == "nv305-sau":
            if not cho_tiep.is_set():
                chen_ngang.append("OS")
            luot_sau_da_den.set()

    def spawn(lenh):
        bien_os()  # Dừng sau kiểm PID, trước spawn và trước ghi PID.
        pid = 1001 + len(da_spawn)
        da_spawn.append(pid)
        dang_song[pid] = list(lenh)
        return pid

    def kill(pid, lenh):
        bien_os()
        assert dang_song.pop(pid) == lenh
        da_kill.append(pid)

    def kiem_pid(pid, lenh):
        return dang_song.get(pid) == lenh

    def kiem_cong(cong):
        exe = nbt.LENH_X11[0] if cong == 5900 else nbt.LENH_WS[0]
        return any(lenh[0] == exe for lenh in list(dang_song.values()))

    def supervisor(*args):
        return None

    ghi_goc = nbt._ghi_tep_0600

    def ghi(tep, noi_dung):
        ghi_goc(tep, noi_dung)
        bien_os()  # Với password: dừng sau ghi mật khẩu, trước marker + reload.

    monkeypatch.setattr(nbt, "_ghi_tep_0600", ghi)
    monkeypatch.setattr(nbt, "_supervisor_thuc", supervisor)
    monkeypatch.setattr(nbt, "_spawn_thuc", spawn)
    monkeypatch.setattr(nbt, "_kill_thuc", kill)
    monkeypatch.setattr(nbt, "_pid_khop", kiem_pid)

    def goi(hanh_dong, mat_khau):
        if hanh_dong == "bat":
            return nbt.bat(khoa, kiem_cong=kiem_cong, spawn=spawn, supervisor=supervisor)
        if hanh_dong == "tat":
            return nbt.tat(khoa, kiem_cong=kiem_cong, kill=kill,
                           supervisor=supervisor, kiem_pid=kiem_pid)
        if hanh_dong == "reload":
            return nbt.nap_lai_x11(khoa)
        return mat_khau_vnc.ghi_mat_khau_vnc(mat_khau, khoa)

    if dau != "bat":
        goi("bat", "")

    def chay(thu_tu, hanh_dong, mat_khau):
        try:
            ket_qua[thu_tu] = goi(hanh_dong, mat_khau)
        except BaseException as exc:
            loi.append(exc)
        finally:
            if thu_tu == "sau":
                if not cho_tiep.is_set():
                    chen_ngang.append("hoan thanh")
                luot_sau_da_den.set()

    luong_dau = threading.Thread(target=chay, args=("dau", dau, "DauGia01"),
                                 name="nv305-dau", daemon=True)
    luong_sau = threading.Thread(target=chay, args=("sau", sau, "SauGia02"),
                                 name="nv305-sau", daemon=True)
    luong_dau.start()
    try:
        assert da_chan.wait(5), "Lượt đầu chưa tới biên OS giả"
        luong_sau.start()
        assert luot_sau_da_den.wait(5), "Lượt sau chưa thử lấy khóa hoặc gọi OS giả"
    finally:
        cho_tiep.set()
        luong_dau.join(5)
        if luong_sau.ident is not None:
            luong_sau.join(5)

    assert not luong_dau.is_alive() and not luong_sau.is_alive(), "Khóa phải reentrant"
    assert not loi, loi
    assert set(ket_qua) == {"dau", "sau"}
    assert not chen_ngang, "Lượt sau chen vào trước khi lượt đầu hoàn tất"
    if sau == "password":
        assert ket_qua["sau"] == {"ok": True, "da_nap_lai": dau != "tat"}
    mat_khau = "SauGia02" if sau == "password" else "DauGia01" if dau == "password" else "VncBm01"
    assert nbt.doc_mat_khau(khoa) == mat_khau
    bat_cuoi = sau == "bat" or (sau != "tat" and dau != "tat")
    assert nbt.doc_co(khoa) is bat_cuoi
    assert len(dang_song) == (2 if bat_cuoi else 0)
    if dau == sau == "bat":
        assert da_spawn == [1001, 1002], "Hai lượt bật không được spawn trùng và ghi đè PID"
    for lenh in dang_song.values():
        assert lenh in (nbt.lenh_x11(khoa), nbt.LENH_WS)
        assert mat_khau not in lenh
    goi("tat", "")
    assert not dang_song, "Tắt phải dừng hết mọi PID đã spawn, không để tiến trình mồ côi"
    assert sorted(da_kill) == da_spawn
    assert not (khoa / nbt.PID_X11).exists()
    assert not (khoa / nbt.PID_WS).exists()
    assert nbt.doc_mat_khau(khoa) == mat_khau
