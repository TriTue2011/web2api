"""Module doc-ma-hop-thu (NV121): ĐỌC mã xác minh từ Gmail qua IMAP — chuẩn `ma_xac_minh`, CHỈ ĐỌC.

Mỗi test kèm đột biến (bảng trong nv121-bao-cao.md): đột biến tương ứng phải làm
test ĐỎ, mã thật XANH.

Máy chủ IMAP GIẢ cục bộ (IMAPGia): tối thiểu LOGIN, CAPABILITY, SELECT/EXAMINE,
SEARCH, FETCH BODY.PEEK[] (kể cả lấy một phần <0.262144>), LOGOUT; GHI LẠI MỌI
động từ lệnh nhận được để kiểm «không có lệnh ghi nào» (STORE/EXPUNGE/DELETE/
APPEND/COPY/CREATE/RENAME).
"""

from __future__ import annotations

import json
import logging
import os
import re
import socket
import threading
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from types import SimpleNamespace

import pytest

GOC = Path(__file__).resolve().parents[1]
THU_MUC = GOC / "modules" / "doc-ma-hop-thu"

# Chuỗi GIẢ rõ ràng — không phải bí mật thật.
MAT_KHAU_DUNG = "abcd efgh ijkl mnop"      # mật khẩu ứng dụng giả (16 ký tự, có khoảng trắng)
MAT_KHAU_SAI = "sai1 sai2 sai3 sai4"
EMAIL_GIA = "ban-gia@gmail.com"

TRAN_THU = 262144


def _manifest():
    from app.kham_pha import doc_manifest
    m, loi = doc_manifest(THU_MUC)
    assert loi is None, loi
    return m


def _lop():
    from app.kham_pha import nap_ma, van_tay
    m = _manifest()
    lop, _ = nap_ma(THU_MUC, m, van_tay(THU_MUC))
    return lop, m


def _module(goc=None, tls=False):
    lop, m = _lop()
    if goc:
        return lop(m, _goc_kiem_thu=goc, _kiem_thu_tls=tls)
    return lop(m)


def _module_py():
    """Nạp module.py bằng đường tệp (thư mục module có dấu gạch ngang,
    không import được bằng tên gói)."""
    import importlib.util
    p = THU_MUC / "module.py"
    ten = "doc_ma_hop_thu_nv121"
    spec = importlib.util.spec_from_file_location(ten, p)
    md = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(md)
    return md


def _cau_hinh(mat_khau=MAT_KHAU_DUNG, email=EMAIL_GIA):
    return {"email": email, "mat_khau_ung_dung": [mat_khau]}


def _tho(frm: str, noi_dung: str, luc: datetime, lon: int = 0) -> dict:
    msg = EmailMessage()
    msg["From"] = frm
    msg["To"] = EMAIL_GIA
    msg["Subject"] = "Thong bao"
    msg.set_content(noi_dung)
    tho = msg.as_bytes()
    if lon:
        tho += b"X" * lon
    return {"uid": 0, "luc": luc, "raw": tho}


def _ngay_imap(dt: datetime) -> str:
    return dt.strftime("%d-%b-%Y %H:%M:%S %z")


# ───────────── máy chủ IMAP giả ─────────────

class IMAPGia:
    """IMAP giả tối thiểu trên socket thường: LOGIN, CAPABILITY, SELECT/EXAMINE,
    SEARCH, FETCH (BODY.PEEK[], có lấy một phần), LOGOUT. Ghi mọi động từ lệnh."""

    def __init__(self, hop_thu: dict, mat_khau_dung: str = MAT_KHAU_DUNG):
        # hop_thu: {"INBOX": [tho...], ...}; gán uid theo thứ tự
        self.hop_thu = {}
        for ten, ds in hop_thu.items():
            for i, t in enumerate(ds, 1):
                t = dict(t)
                t["uid"] = i
                ds[i - 1] = t
            self.hop_thu[ten] = ds
        self.mat_khau_dung = mat_khau_dung.replace(" ", "")   # module tự bỏ khoảng trắng như Google
        self.lenh_da_nhan: list[str] = []     # động từ (LOGIN, SELECT, ...)
        self.lenh_tho: list[str] = []         # dòng lệnh thô (để kiểm FETCH một phần)
        self._dung = threading.Event()
        self._sk = socket.socket()
        self._sk.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sk.bind(("127.0.0.1", 0))
        self._sk.listen(8)
        self.cong = self._sk.getsockname()[1]
        self._luong = threading.Thread(target=self._chay, daemon=True)

    @property
    def goc(self):
        return f"127.0.0.1:{self.cong}"

    def __enter__(self):
        self._luong.start()
        return self

    def __exit__(self, *_):
        self._dung.set()
        try:
            socket.create_connection(("127.0.0.1", self.cong), timeout=2).close()
        except OSError:
            pass
        self._luong.join(10)
        self._sk.close()

    def _chay(self):
        self._sk.settimeout(0.5)
        while not self._dung.is_set():
            try:
                kn, _ = self._sk.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._phuc_vu, args=(kn,), daemon=True).start()

    def _gui(self, f, s: str | bytes):
        f.sendall(s if isinstance(s, bytes) else s.encode("utf-8", "replace"))

    def _phuc_vu(self, kn):
        hop = None
        dang_nhap = False
        try:
            with kn:
                f = kn.makefile("rb")
                self._gui(kn, "* OK IMAP4rev1 gia san sang\r\n")
                while True:
                    dong = f.readline(65536)
                    if not dong:
                        break
                    dong_s = dong.decode("utf-8", "replace").strip()
                    if not dong_s:
                        continue
                    self.lenh_tho.append(dong_s)
                    phan = dong_s.split(None, 2)
                    the, dong_tu = phan[0], phan[1].upper() if len(phan) > 1 else ""
                    tham = phan[2] if len(phan) > 2 else ""
                    self.lenh_da_nhan.append(dong_tu)
                    if dong_tu == "CAPABILITY":
                        self._gui(kn, "* CAPABILITY IMAP4rev1\r\n%s OK xong\r\n" % the)
                    elif dong_tu == "LOGIN":
                        # LOGIN <user> <pass> (pass có thể có ngoặc kép)
                        m = re.match(r'^"?(?P<u>[^"\s]+)"?\s+(?P<p>.+)$', tham)
                        ok = bool(m) and m.group("p").strip('"') == self.mat_khau_dung
                        dang_nhap = ok
                        self._gui(kn, "%s %s\r\n" % (the, "OK dang nhap xong" if ok else "NO [AUTHENTICATIONFAILED] sai"))
                    elif dong_tu in ("SELECT", "EXAMINE"):
                        if not dang_nhap:
                            self._gui(kn, "%s NO chua dang nhap\r\n" % the)
                            continue
                        ten = tham.strip().strip('"')
                        if ten in self.hop_thu:
                            hop = ten
                            n = len(self.hop_thu[ten])
                            self._gui(kn, "* %d EXISTS\r\n%s OK da mo %s\r\n" % (n, the, ten))
                        else:
                            self._gui(kn, "%s NO khong co hop thu\r\n" % the)
                    elif dong_tu == "SEARCH":
                        if hop is None:
                            self._gui(kn, "%s NO chua chon hop\r\n" % the)
                            continue
                        uids = " ".join(str(t["uid"]) for t in self.hop_thu[hop])
                        self._gui(kn, "* SEARCH %s\r\n%s OK tim xong\r\n" % (uids, the))
                    elif dong_tu == "FETCH":
                        if hop is None:
                            self._gui(kn, "%s NO chua chon hop\r\n" % the)
                            continue
                        # Chỉ trả các thư trong TẬP YÊU CẦU — như máy chủ thật
                        # (khách hàng FETCH từng uid một; trả bừa toàn bộ hộp
                        #  làm khách đọc nhầm thư đầu tiên).
                        tap = tham.split(None, 1)[0] if tham else ""
                        uids_yeu_cau = set()
                        for phan_tap in tap.split(","):
                            if ":" in phan_tap:
                                dau_tap, cuoi_tap = phan_tap.split(":", 1)
                                try:
                                    uids_yeu_cau.update(range(int(dau_tap), int(cuoi_tap) + 1))
                                except ValueError:
                                    pass
                            else:
                                try:
                                    uids_yeu_cau.add(int(phan_tap))
                                except ValueError:
                                    pass
                        if not uids_yeu_cau:
                            uids_yeu_cau = {t["uid"] for t in self.hop_thu[hop]}
                        lay_mot_phan = re.search(r"<0\.(\d+)>", tham)
                        gioi_han = int(lay_mot_phan.group(1)) if lay_mot_phan else None
                        for t in self.hop_thu[hop]:
                            if t["uid"] not in uids_yeu_cau:
                                continue
                            tho = t["raw"] if gioi_han is None else t["raw"][:gioi_han]
                            self._gui(kn, "* %d FETCH (UID %d INTERNALDATE \"%s\" BODY[] {%d}\r\n"
                                          % (t["uid"], t["uid"], _ngay_imap(t["luc"]), len(tho)))
                            self._gui(kn, tho)
                            self._gui(kn, b")\r\n")
                        self._gui(kn, "%s OK lay xong\r\n" % the)
                    elif dong_tu == "LOGOUT":
                        self._gui(kn, "* BYE tam biet\r\n%s OK dang xuat\r\n" % the)
                        break
                    elif dong_tu == "NOOP":
                        self._gui(kn, "%s OK\r\n" % the)
                    else:
                        self._gui(kn, "%s BAD khong ho tro: %s\r\n" % (the, dong_tu))
        except (OSError, ConnectionError):
            pass

    def co_lenh_ghi(self) -> list[str]:
        return [l for l in self.lenh_da_nhan
                if l in {"STORE", "EXPUNGE", "DELETE", "APPEND", "COPY", "CREATE", "RENAME"}]


def _yeu_cau(sau_luc: datetime, **them):
    yc = {"hop_thu": EMAIL_GIA, "tu_mien": ["openai.com"],
          "sau_luc": sau_luc.isoformat(), "do_dai": [4, 8], "cho_giay": 60}
    yc.update(them)
    return yc


@pytest.fixture()
def bat_loopback(monkeypatch):
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")


BAY_GIO = datetime(2026, 10, 18, 12, 0, 0, tzinfo=timezone.utc)


# ───────────── hợp đồng và cấu hình ─────────────

def test_manifest_hop_le_va_khai_chuan():
    from app.kham_pha import kiem_chuan_module, kiem_manifest
    m = _manifest()
    loi = kiem_manifest(m)
    assert loi is None, loi
    assert m["cung_cap"] == ["ma_xac_minh"]
    assert m["id"] == "doc-ma-hop-thu"
    assert "webhook" not in m
    khoa = {t["khoa"]: t["kieu"] for t in m["truong_cau_hinh"]}
    assert khoa["mat_khau_ung_dung"] == "danh_sach_bi_mat"
    assert "email" in khoa
    lop, _ = _lop()
    assert kiem_chuan_module(["ma_xac_minh"], lop) == {}
    assert not hasattr(lop, "webhook")
    for ten in ("hop_thu_ra", "lay_ma", "nap", "cai", "suc_khoe", "dung"):
        assert callable(getattr(lop, ten, None)), ten


def test_hop_thu_ra_che_khong_lo_mat_khau():
    mod = _module()
    mod.nap(_cau_hinh())
    ds = mod.hop_thu_ra()
    assert ds == [{"id": EMAIL_GIA, "email_che": "b***@gmail.com"}]
    tho = json.dumps(ds)
    assert MAT_KHAU_DUNG.replace(" ", "") not in tho
    assert "mat_khau" not in tho.lower()


def test_nap_rong_loi_khong_cau_hinh():
    import asyncio
    mod = _module()
    mod.nap({})
    assert mod.hop_thu_ra() == []
    kq = asyncio.run(mod.lay_ma(_yeu_cau(BAY_GIO)))
    assert kq == {"ok": False, "ma_loi": "khong_co_hop_thu"}
    assert MAT_KHAU_DUNG.replace(" ", "") not in json.dumps(kq)


# ───────────── đọc mã ─────────────

def test_lay_ma_thanh_cong_tu_dung_mien(bat_loopback):
    import asyncio
    sau = BAY_GIO - timedelta(minutes=5)
    hop = {"INBOX": [
        _tho("noreply@x.openai.com", "Ma xac minh cua ban la 482916. Dung trong 10 phut.", BAY_GIO - timedelta(minutes=1)),
    ]}
    with IMAPGia(hop) as srv:
        mod = _module(srv.goc)
        mod.nap(_cau_hinh())
        kq = asyncio.run(mod.lay_ma(_yeu_cau(sau)))
    assert kq == {"ok": True, "ma": "482916"}
    assert srv.co_lenh_ghi() == []
    assert "EXAMINE" in srv.lenh_da_nhan or "SELECT" in srv.lenh_da_nhan


def test_mien_gia_mao_bi_bo_du_moi_hon(bat_loopback):
    import asyncio
    sau = BAY_GIO - timedelta(minutes=10)
    hop = {"INBOX": [
        _tho("noreply@openai.com", "Ma cua ban: 111111", BAY_GIO - timedelta(minutes=9)),
        _tho("noreply@evilopenai.com", "Ma cua ban: 999999", BAY_GIO - timedelta(minutes=1)),
    ]}
    with IMAPGia(hop) as srv:
        mod = _module(srv.goc)
        mod.nap(_cau_hinh())
        kq = asyncio.run(mod.lay_ma(_yeu_cau(sau)))
    assert kq == {"ok": True, "ma": "111111"}


def test_thu_cu_hon_sau_luc_bi_bo(bat_loopback):
    import asyncio
    sau = BAY_GIO - timedelta(minutes=5)
    hop = {"INBOX": [
        _tho("noreply@openai.com", "Ma cua ban: 222222", BAY_GIO - timedelta(minutes=30)),
        _tho("noreply@openai.com", "Ma cua ban: 333333", BAY_GIO - timedelta(minutes=1)),
    ]}
    with IMAPGia(hop) as srv:
        mod = _module(srv.goc)
        mod.nap(_cau_hinh())
        kq = asyncio.run(mod.lay_ma(_yeu_cau(sau)))
    assert kq == {"ok": True, "ma": "333333"}


def test_nhieu_thu_lay_moi_nhat(bat_loopback):
    import asyncio
    sau = BAY_GIO - timedelta(minutes=10)
    hop = {"INBOX": [
        _tho("noreply@sub.openai.com", "Ma: 444444", BAY_GIO - timedelta(minutes=8)),
        _tho("noreply@openai.com", "Ma: 555555", BAY_GIO - timedelta(minutes=3)),
        _tho("noreply@openai.com", "Ma: 666666", BAY_GIO - timedelta(minutes=1)),
    ]}
    with IMAPGia(hop) as srv:
        mod = _module(srv.goc)
        mod.nap(_cau_hinh())
        kq = asyncio.run(mod.lay_ma(_yeu_cau(sau)))
    assert kq == {"ok": True, "ma": "666666"}


def test_so_dai_hon_do_dai_va_dinh_so_khac_khong_nhan_nham(bat_loopback):
    import asyncio
    sau = BAY_GIO - timedelta(minutes=5)
    hop = {"INBOX": [
        _tho("noreply@openai.com",
             "Lien he 0912345678. Ma xac minh cua ban: 482916. Don 20261018123456.",
             BAY_GIO - timedelta(minutes=1)),
    ]}
    with IMAPGia(hop) as srv:
        mod = _module(srv.goc)
        mod.nap(_cau_hinh())
        kq = asyncio.run(mod.lay_ma(_yeu_cau(sau, do_dai=[6, 6])))
    assert kq == {"ok": True, "ma": "482916"}


def test_het_gio_khi_khong_co_ma(bat_loopback):
    import asyncio
    sau = BAY_GIO - timedelta(minutes=5)
    hop = {"INBOX": [
        _tho("noreply@openai.com", "Chao ban, khong co ma o day.", BAY_GIO - timedelta(minutes=1)),
    ]}
    with IMAPGia(hop) as srv:
        mod = _module(srv.goc)
        mod.nap(_cau_hinh())
        kq = asyncio.run(mod.lay_ma(_yeu_cau(sau, cho_giay=5)))
    assert kq == {"ok": False, "ma_loi": "het_gio"}


def test_cho_giay_bi_kep_ve_5_giay(bat_loopback):
    import asyncio, time
    sau = BAY_GIO - timedelta(minutes=5)
    hop = {"INBOX": [_tho("noreply@openai.com", "Khong co ma.", BAY_GIO - timedelta(minutes=1))]}
    with IMAPGia(hop) as srv:
        mod = _module(srv.goc)
        mod.nap(_cau_hinh())
        bat_dau = time.monotonic()
        kq = asyncio.run(mod.lay_ma(_yeu_cau(sau, cho_giay=1)))
        mat = time.monotonic() - bat_dau
    assert kq == {"ok": False, "ma_loi": "het_gio"}
    assert mat >= 4.5, "cho_giay=1 phải được kẹp lên tối thiểu 5 giây"


def test_thu_khong_lo_bi_cat_256kb(bat_loopback):
    import asyncio
    sau = BAY_GIO - timedelta(minutes=5)
    hop = {"INBOX": [
        _tho("noreply@openai.com", "Ma: 777777", BAY_GIO - timedelta(minutes=1), lon=2 * 1024 * 1024),
    ]}
    with IMAPGia(hop) as srv:
        mod = _module(srv.goc)
        mod.nap(_cau_hinh())
        kq = asyncio.run(mod.lay_ma(_yeu_cau(sau)))
    assert kq == {"ok": True, "ma": "777777"}
    assert any("<0.262144>" in d for d in srv.lenh_tho), "FETCH phải lấy một phần ≤ 256KB"


# ───────────── lỗi và bí mật ─────────────

def test_sai_mat_khau_khong_lo_bi_mat(bat_loopback, caplog):
    import asyncio
    with IMAPGia({"INBOX": []}, mat_khau_dung="mat-khau-that-cua-server-gia") as srv, \
            caplog.at_level(logging.DEBUG):
        mod = _module(srv.goc)
        mod.nap(_cau_hinh(mat_khau=MAT_KHAU_SAI))
        kq = asyncio.run(mod.cai(_cau_hinh(mat_khau=MAT_KHAU_SAI)))
    assert kq["ok"] is False and kq["ma_loi"] == "sai_dang_nhap"
    nhat_ky = caplog.text
    assert MAT_KHAU_SAI.replace(" ", "") not in nhat_ky
    assert "mat-khau-that-cua-server-gia" not in nhat_ky
    assert "mat_khau" not in json.dumps(kq).lower()


def test_lay_ma_sai_mat_khau(bat_loopback):
    import asyncio
    with IMAPGia({"INBOX": []}) as srv:
        mod = _module(srv.goc)
        mod.nap(_cau_hinh(mat_khau=MAT_KHAU_SAI))
        kq = asyncio.run(mod.lay_ma(_yeu_cau(BAY_GIO)))
    assert kq == {"ok": False, "ma_loi": "sai_dang_nhap"}


def test_loi_mang_khong_lo_mat_khau(bat_loopback, caplog):
    import asyncio
    cong_chet = 9  # không ai nghe
    with caplog.at_level(logging.DEBUG):
        mod = _module(f"127.0.0.1:{cong_chet}")
        mod.nap(_cau_hinh())
        kq = asyncio.run(mod.lay_ma(_yeu_cau(BAY_GIO, cho_giay=5)))
    assert kq == {"ok": False, "ma_loi": "mang_loi"}
    assert MAT_KHAU_DUNG.replace(" ", "") not in caplog.text
    assert MAT_KHAU_DUNG.replace(" ", "") not in json.dumps(kq)


def test_yeu_cau_sai_va_hop_thu_la(bat_loopback):
    import asyncio
    with IMAPGia({"INBOX": []}) as srv:
        mod = _module(srv.goc)
        mod.nap(_cau_hinh())
        assert asyncio.run(mod.lay_ma({"hop_thu": EMAIL_GIA}))["ma_loi"] == "yeu_cau_sai"
        assert asyncio.run(mod.lay_ma(_yeu_cau(BAY_GIO, hop_thu="la@example.com")))["ma_loi"] == "khong_co_hop_thu"
        yc = _yeu_cau(BAY_GIO); yc["sau_luc"] = "khong-phai-ngay"
        assert asyncio.run(mod.lay_ma(yc))["ma_loi"] == "yeu_cau_sai"


def test_cai_thanh_cong_chi_dang_nhap_rong(bat_loopback):
    import asyncio
    with IMAPGia({"INBOX": [_tho("a@openai.com", "x", BAY_GIO)]}) as srv:
        mod = _module(srv.goc)
        kq = asyncio.run(mod.cai(_cau_hinh()))
    assert kq["ok"] is True and kq["so_hop_thu"] == 1
    assert "LOGIN" in srv.lenh_da_nhan and "LOGOUT" in srv.lenh_da_nhan
    assert "SEARCH" not in srv.lenh_da_nhan and "FETCH" not in srv.lenh_da_nhan


# ───────────── chống SSRF: ghi đè gốc chỉ cho test ─────────────

def test_ghi_de_goc_bi_bo_qua_khi_khong_phai_test():
    mod = _module("127.0.0.1:1143")   # KHÔNG đặt biến test → phải bỏ qua
    host, cong, tls, xac_thuc, kiem_thu = mod._noi_dau()
    assert (host, cong) == ("imap.gmail.com", 993)
    assert kiem_thu is False


def test_ghi_de_goc_khong_phai_loopback_bi_bo_qua(bat_loopback):
    mod = _module("93.184.216.34:143")  # IP ngoài dù có biến test → bỏ qua
    host, cong, tls, xac_thuc, kiem_thu = mod._noi_dau()
    assert (host, cong) == ("imap.gmail.com", 993)


# ───────────── kiểm đơn vị: ranh giới miền và regex ─────────────

def test_mien_khop_theo_ranh_gioi_nhan():
    md = _module_py()
    assert md._mien_khop("openai.com", ["openai.com"])
    assert md._mien_khop("x.openai.com", ["openai.com"])
    assert md._mien_khop("X.OPENAI.COM", ["openai.com"])
    assert not md._mien_khop("evilopenai.com", ["openai.com"])
    assert not md._mien_khop("openai.com.evil.com", ["openai.com"])


def test_regex_ma_chat():
    md = _module_py()
    assert md._tim_ma("Ma cua ban: 482916", 6, 6) == "482916"
    assert md._tim_ma("Lien he 0912345678", 4, 8) is None          # số điện thoại dài hơn
    assert md._tim_ma("Don 20261018123456", 4, 8) is None          # chuỗi dài hơn
    assert md._tim_ma("Ma 12345 nhe", 6, 6) is None                # ngắn hơn
    assert md._tim_ma("Ma: 12-34-56", 6, 6) is None                # không liền
    assert md._tim_ma("Ma: 123456", 4, 8) == "123456"
