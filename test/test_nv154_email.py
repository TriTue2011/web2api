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
    """IMAP giả: thư theo UID, ghi nhận thao tác. Hỗ trợ đủ giao thức UID mà
    module dùng: uid SEARCH/FETCH/STORE + select(readonly) + UIDVALIDITY."""
    instances: list = []

    def __init__(self, host, port, timeout=None, ssl_context=None):
        self.host, self.port, self.timeout = host, port, timeout
        self.mails: dict[int, bytes] = {}
        self.seen: set[int] = set()
        self.stored: list = []
        self.uid_goi: list = []       # (lenh, *doi_so)
        self.fetch_goi: list = []    # (uid, bo_phan)
        self.logged_in = None
        self.fail_login = False
        self.uidvalidity = 777
        self._readonly = False
        FakeIMAP.instances.append(self)

    @property
    def untagged_responses(self):
        return {"OK": [f"[UIDVALIDITY {self.uidvalidity}]".encode()]}

    # -- giao thức tối thiểu mà module dùng --
    def login(self, user, pw):
        if self.fail_login or pw == "sai-mat-khau":
            raise imaplib.IMAP4.error("AUTHENTICATIONFAILED")
        self.logged_in = (user, pw)
        return ("OK", [])

    def select(self, box="INBOX", readonly=False):
        self._readonly = readonly
        return ("OK", [b"1"])

    def uid(self, cmd, *args):
        self.uid_goi.append((str(cmd).upper(),) + args)
        c = str(cmd).upper()
        if c == "SEARCH":
            unseen = [u for u in sorted(self.mails) if u not in self.seen]
            return ("OK", [b" ".join(str(u).encode() for u in unseen) or b""])
        if c == "FETCH":
            uid = int(args[0])
            parts = str(args[1]).upper()
            raw = self.mails.get(uid)
            if raw is None:
                return ("NO", [])
            self.fetch_goi.append((uid, parts))
            if "RFC822.SIZE" in parts:
                return ("OK", [(f"{uid} (RFC822.SIZE {len(raw)})".encode(), b"")])
            # BODY.PEEK[] — không đổi cờ Seen (mô phỏng máy chủ thật)
            return ("OK", [(f"{uid} (BODY[] {{{len(raw)}}})".encode(), raw)])
        if c == "STORE":
            uid = int(args[0])
            flags = str(args[2]) if len(args) > 2 else ""
            self.stored.append((uid, args[1], flags))
            if "\\Seen" in flags:
                self.seen.add(uid)
            return ("OK", [])
        return ("BAD", [])

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
        self.mails = {int(k): v for k, v in anh_xa.items()}

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
        "imap_host": "127.0.0.1", "imap_port": 993,
        "smtp_host": "127.0.0.1", "smtp_port": 465,
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
    # Đổi theo hành vi mới có chủ ý (lỗi 8, NV98): cả thư vượt 2 MB bị bỏ
    # TRƯỚC khi tải (kiểm RFC822.SIZE), không còn tải rồi mới bỏ đính kèm —
    # xem test_bo_qua_thu_kich_thuoc_qua_lon_truoc_khi_tai.
    dk = [("lon.bin", b"x" * (9 * 1024 * 1024), "application/octet-stream")]
    with _thu_trong_hop({b"1": _thu_mau(body="than", dinh_kem=dk)}):
        tins = _chay(_mod().hoi_vong())
    assert tins == []
    inst = FakeIMAP.instances[-1]
    assert not any("BODY" in p for _, p in inst.fetch_goi)


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
    # Sổ mới (NV98 — đổi cơ chế định danh có chủ ý): theo UID IMAP +
    # UIDVALIDITY. UID 1 của hộp chính đã nhớ — đây là cơ chế chống trùng;
    # Message-ID không còn là khoá sổ.
    assert 1 in m1._da_thay["chinh"]["uids"]


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
    orig_uid = FakeIMAP.uid

    def _uid(self, cmd, *a):
        raise OSError("dut")

    FakeIMAP.uid = _uid
    try:
        with caplog.at_level(logging.WARNING, logger="modules.email.module"):
            for _ in range(12):
                for acc in m._tai_khoan:  # nới nhịp để mỗi lượt đều thử lại
                    m._lan_poll[acc["id"]] = 0
                assert _chay(m.hoi_vong()) == []
    finally:
        FakeIMAP.uid = orig_uid
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
    orig_uid = FakeIMAP.uid

    def _uid(self, cmd, *a):
        raise OSError("dut mang")

    FakeIMAP.uid = _uid
    try:
        with caplog.at_level(logging.DEBUG):
            _chay(m.hoi_vong())
            _chay(m.suc_khoe())
    finally:
        FakeIMAP.uid = orig_uid
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


# ── vòng sửa theo soát độc lập (agy) + chép lớp bảo vệ NV125 ─────────────────

def _cfg_sua(**acc_them):
    cfg = _cau_hinh()
    acc = json.loads(cfg["tai_khoan"])[0]
    acc.update(acc_them)
    cfg["tai_khoan"] = json.dumps([acc], ensure_ascii=False)
    return cfg


def _mod_trong():
    return mem.Module({}, _imap_ssl=FakeIMAP, _imap=FakeIMAP,
                      _smtp_ssl=FakeSMTP, _smtp=FakeSMTP)


def test_cai_mang_noi_bo_dung_va_chan_ssrf():
    # Lỗi 1: cai() đọc cờ mang_noi_bo CŨ. (a) khai "co" cho máy chủ nội bộ hợp
    # lệ phải qua; (b) sau đó tắt cờ + máy chủ metadata phải bị CHẶN.
    m = _mod_trong()
    cfg = _cfg_sua(imap_host="192.168.10.5", smtp_host="192.168.10.5")
    cfg["mang_noi_bo"] = "co"
    kq = _chay(m.cai(cfg))
    assert kq["ok"] is True, kq
    cfg2 = _cfg_sua(imap_host="169.254.169.254", smtp_host="169.254.169.254")
    cfg2["mang_noi_bo"] = ""
    kq2 = _chay(m.cai(cfg2))
    assert kq2["ok"] is False


def test_mark_seen_false_khong_nghen_khi_qua_muoi_thu():
    # Lỗi 2: 12 thư, mark_seen tắt — thư 11, 12 vẫn phải tới, không kẹt ở 10
    # thư đầu (chống trùng bằng sổ UID, không phụ thuộc cờ \Seen).
    thu = {str(i).encode(): _thu_mau(message_id=f"<m{i}@example.com>",
                                     subject=f"Thu {i}")
           for i in range(1, 13)}
    m = _mod(mark_seen=False)
    with _thu_trong_hop(thu):
        l1 = _chay(m.hoi_vong())
        m._lan_poll = {}     # bỏ nhịp lùi dần giữa các lượt (chỉ trong test)
        l2 = _chay(m.hoi_vong())
        m._lan_poll = {}
        l3 = _chay(m.hoi_vong())
    assert len(l1) == 10 and len(l2) == 2 and l3 == []
    assert len({t["id_tin"] for t in l1 + l2}) == 12
    inst = FakeIMAP.instances[-1]
    assert inst._readonly is True        # không đánh dấu → mở chỉ đọc
    assert inst.stored == []              # tuyệt đối không ghi \Seen


def test_tu_choi_thu_co_nhieu_header_from():
    # Lỗi 3 (ưu tiên a): thư 2 header From — đầu là người được phép, sau là
    # kẻ tấn công → phải bỏ (RFC 5322 3.6: đúng một From).
    raw = (b"From: sep@example.com\r\n"
           b"From: ke-tan-cong@evil.com\r\n"
           b"To: nguoi.a@example.com\r\nSubject: Gia danh\r\n"
           b"Message-ID: <dup@example.com>\r\n"
           b"Date: Mon, 19 Oct 2026 10:00:00 +0700\r\n"
           b"Content-Type: text/plain; charset=utf-8\r\n\r\nLenh gia danh")
    with _thu_trong_hop({b"1": raw}):
        tins = _chay(_mod().hoi_vong())
    assert tins == []


def test_imap_dung_uid_search_va_uid_store():
    # Lỗi 4: mọi thao tác qua UID (uid SEARCH/FETCH/STORE), không số thứ tự.
    m = _mod()
    with _thu_trong_hop({b"1": _thu_mau()}):
        assert len(_chay(m.hoi_vong())) == 1
    inst = FakeIMAP.instances[-1]
    lenhs = [g[0] for g in inst.uid_goi]
    assert "SEARCH" in lenhs and "STORE" in lenhs
    assert any("\\Seen" in str(g[-1]) for g in inst.uid_goi if g[0] == "STORE")


def test_uidvalidity_doi_reset_so_uid():
    # Lỗi 4 (tt): UIDVALIDITY đổi → UID cũ thuộc hộp khác → phải xử lý lại.
    m = _mod()
    with _thu_trong_hop({b"1": _thu_mau()}):
        assert len(_chay(m.hoi_vong())) == 1
        m._lan_poll = {}
        orig = FakeIMAP.__init__

        def _init888(self, host, port, timeout=None, ssl_context=None):
            orig(self, host, port, timeout=timeout, ssl_context=ssl_context)
            self.uidvalidity = 888

        FakeIMAP.__init__ = _init888
        try:
            tins = _chay(m.hoi_vong())
        finally:
            FakeIMAP.__init__ = orig
    assert len(tins) == 1


def _thu_co_ar(ar):
    msg = EmailMessage()
    msg["From"] = "sep@example.com"
    msg["To"] = "nguoi.a@example.com"
    msg["Subject"] = "Thu AR"
    msg["Message-ID"] = "<ar@example.com>"
    msg["Date"] = "Mon, 19 Oct 2026 10:00:00 +0700"
    if ar:
        msg["Authentication-Results"] = ar
    msg.set_content("Noi dung thu AR")
    return msg.as_bytes()


def test_spf_fail_bi_bo():
    # Lớp SPF/DKIM chép từ NV125: AR có spf=fail → bỏ thư.
    ar = "mx.example.com; spf=fail smtp.mailfrom=sep@example.com; dkim=pass"
    with _thu_trong_hop({b"1": _thu_co_ar(ar)}):
        assert _chay(_mod().hoi_vong()) == []


def test_dkim_softfail_bi_bo():
    ar = "mx.example.com; spf=pass; dkim=softfail header.d=example.com"
    with _thu_trong_hop({b"1": _thu_co_ar(ar)}):
        assert _chay(_mod().hoi_vong()) == []


def test_ar_pass_va_khong_co_ar_van_nhan():
    ar = "mx.example.com; spf=pass; dkim=pass"
    with _thu_trong_hop({b"1": _thu_co_ar(ar)}):
        assert len(_chay(_mod().hoi_vong())) == 1
    with _thu_trong_hop({b"1": _thu_co_ar("")}):
        assert len(_chay(_mod().hoi_vong())) == 1


def test_readme_va_tham_chieu_canh_bao_spoofing_from():
    # Lỗi 5: tài liệu phải cảnh báo mạo danh From khi không có SPF/DKIM.
    readme = (GOC / "modules" / "email" / "README.md").read_text(encoding="utf-8")
    tham = (GOC / "docs" / "THAM-CHIEU.md").read_text(encoding="utf-8")
    for tai_lieu in (readme, tham):
        assert "giả mạo" in tai_lieu and "SPF/DKIM" in tai_lieu


def _cau_hinh_hai_hop():
    def _acc(id_):
        return {"id": id_, "label": f"Hop {id_}", "enabled": True,
                "imap_host": "127.0.0.1", "imap_port": 993,
                "smtp_host": "127.0.0.1", "smtp_port": 465,
                "user": f"{id_}@example.com", "security": "ssl",
                "verify_ssl": True, "allowed_senders": ["sep@example.com"],
                "mark_seen": True, "max_body_chars": 6000, "poll_seconds": 20}
    return {"tai_khoan": json.dumps([_acc("mot"), _acc("hai")],
                                     ensure_ascii=False),
            "mat_khau": ["mk-mot-bi-mat", "mk-hai-bi-mat"]}


def test_dung_ngat_ngay_vong_nhieu_hop_thu():
    # Lỗi 6: dung() sau hộp đầu → hộp còn lại KHÔNG được kết nối nữa.
    m = _mod_trong()
    m.nap(_cau_hinh_hai_hop())
    orig = mem.Module._poll_account_sync

    def _poll_va_dung(self, acc):
        self._dung = True     # gỡ module NGAY trong lượt hỏi
        return orig(self, acc)

    mem.Module._poll_account_sync = _poll_va_dung
    try:
        _chay(m.hoi_vong())
    finally:
        mem.Module._poll_account_sync = orig
    assert len(FakeIMAP.instances) == 1  # chỉ hộp đầu từng kết nối


def test_extract_body_fallback_khi_charset_la():
    # Lỗi 7: charset lạ không được làm mất thân thư — fallback UTF-8.
    than = "Nội dung có dấu: Tiếng Việt rõ ràng".encode("utf-8")
    raw = (b"From: sep@example.com\r\nTo: nguoi.a@example.com\r\n"
           b"Subject: Charset\r\nMessage-ID: <cs@example.com>\r\n"
           b"Date: Mon, 19 Oct 2026 10:00:00 +0700\r\n"
           b"MIME-Version: 1.0\r\n"
           b"Content-Type: text/plain; charset=x-la-123\r\n"
           b"Content-Transfer-Encoding: 8bit\r\n\r\n") + than
    with _thu_trong_hop({b"1": raw}):
        tins = _chay(_mod().hoi_vong())
    assert len(tins) == 1
    assert "Tiếng Việt" in tins[0]["van_ban"]


def test_bo_qua_thu_kich_thuoc_qua_lon_truoc_khi_tai():
    # Lỗi 8: thư 3 MB bị bỏ theo RFC822.SIZE; BODY của nó KHÔNG bao giờ fetch.
    lon = _thu_mau(body="x" * (3 * 1024 * 1024), message_id="<lon@example.com>")
    m = _mod()
    with _thu_trong_hop({b"1": lon, b"2": _thu_mau(message_id="<nho@example.com>")}):
        tins = _chay(m.hoi_vong())
        m._lan_poll = {}
        lai = _chay(m.hoi_vong())
    assert len(tins) == 1 and lai == []          # chỉ thư nhỏ được xử lý
    inst = FakeIMAP.instances[0]                 # lượt poll đầu
    assert not any(u == 1 and "BODY" in p for u, p in inst.fetch_goi)
    assert any(u == 1 and "SIZE" in p for u, p in inst.fetch_goi)


def test_bao_mat_mac_dinh_la_ssl_khong_plain():
    # Đậy đột biến DB1 của agy: mặc định không bao giờ rơi về plain.
    assert mem._sec_mode({}) == "ssl"
    assert mem._sec_mode({"security": "plain"}) == "ssl"
    m = _mod_trong()
    kq = _chay(m.cai(_cfg_sua(security="plain")))
    assert kq["ok"] is False


def test_cai_tu_choi_cong_khong_tls(monkeypatch):
    # Ép TLS (port NV125): IMAP 143 / SMTP 2525 / gán sai cặp 465+starttls.
    # (conftest dọn env giữa test → bật loopback bằng monkeypatch.)
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    m = _mod_trong()
    assert _chay(m.cai(_cfg_sua(imap_port=143)))["ok"] is False
    assert _chay(m.cai(_cfg_sua(smtp_port=2525)))["ok"] is False
    assert _chay(m.cai(_cfg_sua(smtp_port=465, security="starttls")))["ok"] is False
    assert _chay(m.cai(_cfg_sua(smtp_port=587, security="starttls")))["ok"] is True


def test_lech_so_mat_khau_hop_thu_bi_tu_choi():
    # Đậy đột biến DB5 của agy: 2 hộp 1 mật khẩu → từ chối (cũ chỉ thử []).
    cfg = _cau_hinh_hai_hop()
    cfg["mat_khau"] = ["chi-mot-mat-khau"]
    m = _mod_trong()
    assert _chay(m.cai(cfg))["ok"] is False
    m.nap(cfg)
    assert m._tai_khoan == []


def test_id_tin_giu_tien_to_mid():
    # Đậy đột biến DB2 của agy: id_tin giữ tiền tố mid: khi có Message-ID.
    with _thu_trong_hop({b"1": _thu_mau(message_id="<git@example.com>")}):
        tins = _chay(_mod().hoi_vong())
    assert len(tins) == 1
    assert ":mid:git@example.com" in tins[0]["id_tin"]


