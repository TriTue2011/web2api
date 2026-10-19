"""NV154 — module `email`: port kênh email của c2a (SMTP gửi + IMAP hỏi vòng).

Máy chủ IMAP/SMTP là GIẢ (lớp giả tiêm qua `_imap_ssl`/`_smtp_ssl`), KHÔNG gọi
Internet. Biến `WEB2API_TEST_CHO_PHEP_LOOPBACK=1` để `cai()` cho qua kiểm
`tu_xa` với máy chủ giả trên 127.0.0.1.
"""
from __future__ import annotations

import asyncio
import contextlib
import imaplib
import json
import logging
import os
import sys
from email.message import EmailMessage
from pathlib import Path

import pytest

GOC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(GOC))
os.environ["WEB2API_TEST_CHO_PHEP_LOOPBACK"] = "1"

from modules.email import module as mem  # noqa: E402


MAT_KHAU = "mat-khau-bi-mat-XYZ-123"


# ── máy chủ giả ──────────────────────────────────────────────────────────────

class FakeIMAP:
    """IMAP giả: giữ thư trong bộ nhớ, ghi nhận thao tác."""
    instances: list = []

    def __init__(self, host, port, timeout=None, ssl_context=None):
        self.host, self.port, self.timeout = host, port, timeout
        self.mails: dict[bytes, bytes] = {}
        self.logged_in = None
        self.stored: list = []
        self.fail_login = False
        FakeIMAP.instances.append(self)

    # -- giao thức tối thiểu mà module dùng --
    def login(self, user, pw):
        if self.fail_login or pw == "sai-mat-khau":
            raise imaplib.IMAP4.error("AUTHENTICATIONFAILED")
        self.logged_in = (user, pw)
        return ("OK", [])

    def select(self, box="INBOX"):
        return ("OK", [b"1"])

    def search(self, charset, *criteria):
        return ("OK", [b" ".join(sorted(self.mails.keys())) or b""])

    def fetch(self, num, parts):
        key = num if isinstance(num, bytes) else str(num).encode()
        raw = self.mails.get(key)
        if raw is None:
            return ("NO", [])
        return ("OK", [(key + b" (RFC822)", raw)])

    def store(self, num, cmd, flags):
        self.stored.append((num, cmd, flags))
        return ("OK", [])

    def starttls(self, ctx=None):
        return ("OK", [])

    def logout(self):
        return ("OK", [])


class FakeSMTP:
    instances: list = []

    def __init__(self, host, port, timeout=None, context=None):
        self.host, self.port = host, port
        self.sent: list = []
        self.logged_in = None
        self.tls = False
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def ehlo(self):
        return (250, b"ok")

    def starttls(self, context=None):
        self.tls = True

    def login(self, user, pw):
        self.logged_in = (user, pw)

    def send_message(self, msg):
        self.sent.append(msg)


@pytest.fixture(autouse=True)
def _sach():
    FakeIMAP.instances.clear()
    FakeSMTP.instances.clear()
    yield
    FakeIMAP.instances.clear()
    FakeSMTP.instances.clear()


@contextlib.contextmanager
def _thu_trong_hop(anh_xa):
    """Mọi instance FakeIMAP mà module tạo ra đều chứa sẵn các thư cho trước."""
    orig = FakeIMAP.__init__

    def _init(self, host, port, timeout=None, ssl_context=None):
        orig(self, host, port, timeout=timeout, ssl_context=ssl_context)
        self.mails = dict(anh_xa)

    FakeIMAP.__init__ = _init
    try:
        yield
    finally:
        FakeIMAP.__init__ = orig


def _thu_mau(from_addr="sep@example.com", subject="Xin chao",
             body="Noi dung thu", html=False, dinh_kem=(),
             message_id="<abc123@example.com>", date="Mon, 19 Oct 2026 10:00:00 +0700"):
    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = "nguoi.a@example.com"
    msg["Subject"] = subject
    if message_id:
        msg["Message-ID"] = message_id
    msg["Date"] = date
    if html:
        msg.set_content(body, subtype="html")
    else:
        msg.set_content(body)
    for ten, du_lieu, kieu in dinh_kem:
        maintype, subtype = kieu.split("/", 1)
        msg.add_attachment(du_lieu, maintype=maintype, subtype=subtype, filename=ten)
    return msg.as_bytes()


def _cau_hinh(allowed_senders=("sep@example.com",), **them):
    acc = {
        "id": "chinh", "label": "Hop chinh", "enabled": True,
        "imap_host": "127.0.0.1", "imap_port": 10993,
        "smtp_host": "127.0.0.1", "smtp_port": 10465,
        "user": "nguoi.a@example.com",
        "security": "ssl", "verify_ssl": True,
        "allowed_senders": list(allowed_senders),
        "mark_seen": True, "max_body_chars": 6000, "poll_seconds": 20,
    }
    acc.update(them)
    return {"tai_khoan": json.dumps([acc], ensure_ascii=False),
            "mat_khau": [MAT_KHAU]}


def _mod(**kw):
    m = mem.Module({}, _imap_ssl=FakeIMAP, _imap=FakeIMAP,
                   _smtp_ssl=FakeSMTP, _smtp=FakeSMTP)
    m.nap(_cau_hinh(**kw))
    return m


def _chay(coro):
    return asyncio.run(coro)


# ── đường nhận ────────────────────────────────────────────────────────────────

def test_nhan_thu_van_ban():
    m = _mod()
    with _thu_trong_hop({b"1": _thu_mau()}):
        tins = _chay(m.hoi_vong())
    assert len(tins) == 1
    tin = tins[0]
    assert tin["nguoi_gui"] == "sep@example.com"
    assert "Noi dung thu" in tin["van_ban"]
    assert tin["id_tin"].startswith("email:chinh:")
    assert tin["la_nhom"] is False


def test_thu_html_duoc_loc_thanh_van_ban():
    html = ("<html><head><style>.x{color:red}</style></head><body>"
            "<script>alert(1)</script><p>Xin <b>chao</b> ban</p></body></html>")
    with _thu_trong_hop({b"1": _thu_mau(html=True, body=html)}):
        tins = _chay(_mod().hoi_vong())
    assert len(tins) == 1
    vb = tins[0]["van_ban"]
    assert "Xin chao ban" in vb
    assert "<script" not in vb and "alert(1)" not in vb and "<style" not in vb


def test_dinh_kem_van_ban_duoc_doc():
    dk = [("ghi-chu.txt", "noi dung tep dinh kem".encode("utf-8"), "text/plain")]
    with _thu_trong_hop({b"1": _thu_mau(dinh_kem=dk)}):
        tins = _chay(_mod().hoi_vong())
    assert len(tins) == 1
    assert "noi dung tep dinh kem" in tins[0]["van_ban"]


def test_dinh_kem_nhi_phan_khong_doc_duoc():
    dk = [("du-lieu.bin", b"\xff\xfe\x00\x01" * 100, "application/octet-stream")]
    with _thu_trong_hop({b"1": _thu_mau(body="than thu", dinh_kem=dk)}):
        tins = _chay(_mod().hoi_vong())
    assert len(tins) == 1
    assert "than thu" in tins[0]["van_ban"]
    # nội dung nhị phân KHÔNG được trích vào văn bản (không ký tự thay thế)
    assert "\ufffd" not in tins[0]["van_ban"]


def test_dinh_kem_qua_lon_bi_bo():
    dk = [("lon.bin", b"x" * (9 * 1024 * 1024), "application/octet-stream")]
    with _thu_trong_hop({b"1": _thu_mau(body="than", dinh_kem=dk)}):
        tins = _chay(_mod().hoi_vong())
    # thư vẫn xử lý được, không treo, không đọc tệp quá lớn
    assert len(tins) == 1


def test_tieu_de_ma_hoa_duoc_giai():
    subj = "=?UTF-8?B?VGjGsCBt4budaSBt4bubaQ==?="  # "Thư mời mới"
    with _thu_trong_hop({b"1": _thu_mau(subject=subj)}):
        tins = _chay(_mod().hoi_vong())
    assert len(tins) == 1
    assert "Thư mời mới" in tins[0]["van_ban"]


def test_thu_di_dang_khong_lam_sap_vong():
    with _thu_trong_hop({b"1": b"day khong phai email hop le \xff\xfe"}):
        tins = _chay(_mod().hoi_vong())  # không ném
    assert tins == []


def test_thu_qua_dai_bi_cat():
    with _thu_trong_hop({b"1": _thu_mau(body="a" * 9000)}):
        tins = _chay(_mod().hoi_vong())
    assert len(tins) == 1
    assert len(tins[0]["van_ban"]) <= 4000  # trần của lõi kenh_vao


# ── danh sách người gửi ───────────────────────────────────────────────────────

def test_nguoi_gui_ngoai_danh_sach_bi_bo():
    with _thu_trong_hop({b"1": _thu_mau(from_addr="la@example.org")}):
        tins = _chay(_mod().hoi_vong())
    assert tins == []


def test_mac_dinh_danh_sach_rong_khong_nhan_ai():
    with _thu_trong_hop({b"1": _thu_mau()}):
        tins = _chay(_mod(allowed_senders=[]).hoi_vong())
    assert tins == []


def test_khop_chuoi_con_khong_duoc_chap_nhan():
    # "admin" trong danh sách KHÔNG được khớp "admin@evil.com"
    with _thu_trong_hop({b"1": _thu_mau(from_addr="admin@evil.com")}):
        tins = _chay(_mod(allowed_senders=["admin"]).hoi_vong())
    assert tins == []


def test_khop_theo_domain_duoc_chap_nhan():
    with _thu_trong_hop({b"1": _thu_mau(from_addr="sep@congty.vn")}):
        tins = _chay(_mod(allowed_senders=["@congty.vn"]).hoi_vong())
    assert len(tins) == 1


# ── UID đã thấy / vòng lỗi ───────────────────────────────────────────────────

def test_uid_da_thay_khong_xu_ly_lai():
    m = _mod()
    with _thu_trong_hop({b"1": _thu_mau(message_id="<mot@example.com>")}):
        assert len(_chay(m.hoi_vong())) == 1
        assert _chay(m.hoi_vong()) == []  # lượt 2: UID đã thấy


def test_khoi_dong_lai_khong_phat_lai_thu_da_xu_ly(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    from app import kho as kho_mod
    m1 = _mod()
    m1.kho = kho_mod.kho_rieng("email")
    m1.nap(_cau_hinh())
    with _thu_trong_hop({b"1": _thu_mau(message_id="<mot@example.com>")}):
        assert len(_chay(m1.hoi_vong())) == 1
        # thể hiện MỚI như sau khởi động lại, cùng kho
        m2 = mem.Module({}, _imap_ssl=FakeIMAP, _imap=FakeIMAP,
                        _smtp_ssl=FakeSMTP, _smtp=FakeSMTP)
        m2.kho = kho_mod.kho_rieng("email")
        m2.nap(_cau_hinh())
        assert _chay(m2.hoi_vong()) == []
    assert ("mot@example.com" in json.dumps(m1._da_thay)
            or any("mot@example.com" in u for d in m1._da_thay.values() for u in d))


def test_quyen_0600_cho_so_uid(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    from app import kho as kho_mod
    m = _mod()
    m.kho = kho_mod.kho_rieng("email")
    m.nap(_cau_hinh())
    with _thu_trong_hop({b"1": _thu_mau(message_id="<mot@example.com>")}):
        _chay(m.hoi_vong())
    p = tmp_path / "bi_mat" / "mo-dun" / "email" / "da_thay.json"
    assert p.is_file()
    assert oct(p.stat().st_mode & 0o777) == "0o600"


def test_loi_lien_tiep_thi_dung_va_bao_mot_lan(caplog):
    m = _mod()
    orig_search = FakeIMAP.search

    def _search(self, charset, *c):
        raise OSError("dut")

    FakeIMAP.search = _search
    try:
        with caplog.at_level(logging.WARNING, logger="modules.email.module"):
            for _ in range(12):
                for acc in m._tai_khoan:  # nới nhịp để mỗi lượt đều thử lại
                    m._lan_poll[acc["id"]] = 0
                assert _chay(m.hoi_vong()) == []
    finally:
        FakeIMAP.search = orig_search
    so_lan_dung = sum(1 for r in caplog.records
                      if "dung vi loi lien tiep" in r.getMessage())
    assert so_lan_dung == 1
    # đã dừng: lượt tiếp theo không tạo kết nối mới
    n_truoc = len(FakeIMAP.instances)
    _chay(m.hoi_vong())
    assert len(FakeIMAP.instances) == n_truoc


def test_go_module_dung_vong_nen():
    m = _mod()
    _chay(m.dung())
    assert _chay(m.hoi_vong()) == []
    assert FakeIMAP.instances == []


def test_mark_seen_danh_dau_da_doc():
    m = _mod()
    with _thu_trong_hop({b"1": _thu_mau()}):
        _chay(m.hoi_vong())
    inst = FakeIMAP.instances[-1]
    assert any("\\Seen" in str(f) for _, _, f in inst.stored)


# ── đường gửi ─────────────────────────────────────────────────────────────────

def _gui(m, nguoi_nhan="nhan@example.com", tieu_de="Tieu de", van_ban="Noi dung"):
    dich = {"kenh_id": "chinh", "id": "chinh", "ten": "Hop chinh"}
    return _chay(m.gui_tin(dich, {"van_ban": van_ban, "tieu_de": tieu_de,
                                  "nguoi_nhan": nguoi_nhan}))


def test_gui_email_dung_tieu_de_nguoi_nhan():
    m = _mod()
    kq = _gui(m)
    assert kq["ok"] is True
    assert len(FakeSMTP.instances) == 1
    smtp = FakeSMTP.instances[0]
    assert smtp.logged_in[0] == "nguoi.a@example.com"
    assert len(smtp.sent) == 1
    msg = smtp.sent[0]
    assert msg["To"] == "nhan@example.com"
    assert msg["Subject"] == "Tieu de"
    assert "Noi dung" in msg.get_content()


def test_tu_choi_nguoi_nhan_la_khi_co_danh_sach_gui():
    m = _mod()
    m.nap({**_cau_hinh(), "nguoi_nhan_cho_phep": "sep@example.com"})
    kq = _gui(m, nguoi_nhan="la@example.org")
    assert kq["ok"] is False
    assert kq["ma"] == "nguoi_nhan_la"
    assert FakeSMTP.instances == []


def test_chan_chen_header_trong_tieu_de():
    m = _mod()
    kq = _gui(m, tieu_de="Xin chao\r\nBcc: ke-la@example.org")
    assert kq["ok"] is False
    assert kq["ma"] == "tieu_de_sai"
    assert FakeSMTP.instances == []


def test_chan_chen_header_trong_nguoi_nhan():
    m = _mod()
    kq = _gui(m, nguoi_nhan="nhan@example.com\r\nBcc: x@y.z")
    assert kq["ok"] is False
    assert FakeSMTP.instances == []


# ── bí mật / cài đặt / sức khoẻ ──────────────────────────────────────────────

def test_mat_khau_khong_vao_log(caplog):
    # Gỡ TẠM bộ lọc chống rò rỉ để kiểm LỚP ĐẦU: mã không được ghi mật khẩu
    # vào log (bộ lọc chỉ là phòng thủ sâu, không thay thế kiểm này).
    loggers = [logging.getLogger(n) for n in ("imaplib", "smtplib", "modules.email.module")]
    saved = {lg: list(lg.filters) for lg in loggers}
    for lg in loggers:
        lg.filters = [f for f in lg.filters if not getattr(f, "_email_loc", False)]
    m = _mod()
    orig_search = FakeIMAP.search

    def _search(self, charset, *c):
        raise OSError("dut mang")

    FakeIMAP.search = _search
    try:
        with caplog.at_level(logging.DEBUG):
            _chay(m.hoi_vong())
            _chay(m.suc_khoe())
    finally:
        FakeIMAP.search = orig_search
        for lg, fs in saved.items():
            lg.filters = fs
    for r in caplog.records:
        assert MAT_KHAU not in r.getMessage(), f"mat khau lot vao log: {r.getMessage()[:80]}"


def test_suc_khoe_khong_lo_mat_khau_va_khong_nem():
    m = _mod()
    sk = _chay(m.suc_khoe())
    assert isinstance(sk, dict)
    assert MAT_KHAU not in json.dumps(sk, ensure_ascii=False)


def test_cai_chan_ssrf():
    m = mem.Module({}, _imap_ssl=FakeIMAP, _imap=FakeIMAP,
                   _smtp_ssl=FakeSMTP, _smtp=FakeSMTP)
    cfg = _cau_hinh()
    acc = json.loads(cfg["tai_khoan"])[0]
    acc["imap_host"] = "169.254.169.254"  # link-local: metadata đám mây
    cfg["tai_khoan"] = json.dumps([acc])
    kq = _chay(m.cai(cfg))
    assert kq["ok"] is False  # lỗi sạch, không ném


def test_cai_dia_chi_di_dang_loi_sach():
    m = mem.Module({}, _imap_ssl=FakeIMAP, _imap=FakeIMAP,
                   _smtp_ssl=FakeSMTP, _smtp=FakeSMTP)
    cfg = _cau_hinh()
    acc = json.loads(cfg["tai_khoan"])[0]
    acc["imap_host"] = "imap exam ple.com\r\n"
    cfg["tai_khoan"] = json.dumps([acc])
    kq = _chay(m.cai(cfg))
    assert kq["ok"] is False


def test_cai_thieu_mat_khau_loi_sach():
    m = mem.Module({}, _imap_ssl=FakeIMAP, _imap=FakeIMAP,
                   _smtp_ssl=FakeSMTP, _smtp=FakeSMTP)
    cfg = _cau_hinh()
    cfg["mat_khau"] = []
    kq = _chay(m.cai(cfg))
    assert kq["ok"] is False


def test_kenh_ra_khong_chua_bi_mat():
    m = _mod()
    ds = m.kenh_ra()
    assert ds[0]["id"] == "chinh"
    for d in ds:
        assert MAT_KHAU not in json.dumps(d, ensure_ascii=False)


def test_suc_khoe_bao_chua_ket_noi_duoc():
    m = _mod()
    orig_login = FakeIMAP.login

    def _login(self, user, pw):
        raise imaplib.IMAP4.error("AUTHENTICATIONFAILED")

    FakeIMAP.login = _login
    try:
        sk = _chay(m.suc_khoe())
    finally:
        FakeIMAP.login = orig_login
    assert sk["ok"] is False
    assert "chưa kết nối được" in sk["tai_khoan"][0]["loi"]
