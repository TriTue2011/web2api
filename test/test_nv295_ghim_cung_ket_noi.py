"""NV295: ghim trên chính TLS stream gửi HTTP, không chỉ socket probe.

Chỉ nối 127.0.0.1; cert/key và thông tin đăng nhập đều giả do test tạo.
Tái dùng bộ sinh cert cryptography và giao thức đăng nhập của test NV111.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import os
import socket
import ssl
import sys
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from test_web2api_nv111_zte_e2633 import (
    MAT_KHAU_GIA,
    TAI_KHOAN_GIA,
    _chung_chi_tu_ky,
    _HandlerGia,
    _TrangThaiGia,
)

DUONG_TOKEN = "/function_module/login_module/login_page/logintoken_lua.lua"


@pytest.fixture(autouse=True)
def chi_noi_loopback(monkeypatch):
    """Chặn trước syscall, kể cả client production dùng trust_env=False."""
    da_chan = []
    getaddrinfo_that = socket.getaddrinfo
    connect_that = socket.socket.connect
    connect_ex_that = socket.socket.connect_ex

    def kiem_host(host):
        if host not in ("127.0.0.1", b"127.0.0.1"):
            da_chan.append(host)
            raise AssertionError("Test TLS chỉ được nối 127.0.0.1")

    def getaddrinfo(host, *args, **kwargs):
        kiem_host(host)
        return getaddrinfo_that(host, *args, **kwargs)

    def connect(sock, dia_chi):
        kiem_host(dia_chi[0])
        return connect_that(sock, dia_chi)

    def connect_ex(sock, dia_chi):
        kiem_host(dia_chi[0])
        return connect_ex_that(sock, dia_chi)

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    yield
    assert not da_chan, "Có lần thử DNS/kết nối ngoài loopback bị chặn"


@pytest.fixture
def mod(monkeypatch):
    ten = "zte_e2633_nv295_ghim_test"
    spec = importlib.util.spec_from_file_location(ten, "modules/zte-e2633/module.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, ten, module)
    spec.loader.exec_module(module)
    return module


class _TlsGhiNhan(ssl.SSLSocket):
    """Ghi mọi byte plaintext đọc từ TLS, cả khi HTTP chưa parse được."""

    def read(self, len=1024, buffer=None):
        kq = super().read(len, buffer)
        du_lieu = bytes(memoryview(buffer)[:kq]) if buffer is not None else kq
        self.da_nhan.extend(du_lieu)
        return kq


class _MayChuDoiCert(ThreadingHTTPServer):
    # server_close chờ mọi handler xong rồi mới kiểm byte, tránh assert đua luồng.
    daemon_threads = False
    block_on_close = True

    def get_request(self):
        sock, dia_chi = self.socket.accept()
        sock.settimeout(3.0)
        dung_b = (self.che_do == "luon_b"
                  or (self.che_do == "sau_probe" and bool(self.ket_noi))
                  or (self.che_do == "sau_get_token" and self.da_get_token))
        ten = "b" if dung_b else "a"
        ban_ghi = {"cert": ten, "da_nhan": bytearray()}
        self.ket_noi.append(ban_ghi)
        try:
            tls = self.ngu_canh[ten].wrap_socket(sock, server_side=True)
        except BaseException:
            sock.close()
            raise
        tls.da_nhan = ban_ghi["da_nhan"]
        return tls, dia_chi

    def handle_error(self, request, client_address):
        self.loi_handler.append(type(sys.exception()).__name__)


@pytest.fixture
def may_chu_tls(tmp_path, request):
    ngu_canh = {}
    van_tay = {}
    ca = getattr(request, "param", None) == "b_ky_boi_a"
    if ca:
        khoa_ca = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        ten_ca = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "ca-gia-nv295")])
    for ten in ("a", "b"):
        thu_muc = Path(os.path.relpath(tmp_path)) / ten
        thu_muc.mkdir()
        if ca:
            khoa = (khoa_ca if ten == "a" else
                    rsa.generate_private_key(public_exponent=65537, key_size=2048))
            chu_the = (ten_ca if ten == "a" else x509.Name([
                x509.NameAttribute(NameOID.COMMON_NAME, "la-gia-nv295")]))
            bay_gio = datetime.now(timezone.utc)
            chung_chi = (x509.CertificateBuilder()
                        .subject_name(chu_the).issuer_name(ten_ca)
                        .public_key(khoa.public_key())
                        .serial_number(x509.random_serial_number())
                        .not_valid_before(bay_gio - timedelta(minutes=5))
                        .not_valid_after(bay_gio + timedelta(days=1))
                        .add_extension(x509.BasicConstraints(ca=ten == "a", path_length=None),
                                       critical=True)
                        .sign(khoa_ca, hashes.SHA256()))
            cert, key = thu_muc / "gia.crt", thu_muc / "gia.key"
            cert.write_bytes(chung_chi.public_bytes(serialization.Encoding.PEM))
            key.write_bytes(khoa.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption(),
            ))
            van_tay[ten] = hashlib.sha256(
                chung_chi.public_bytes(serialization.Encoding.DER)).hexdigest()
        else:
            cert, key, van_tay[ten] = _chung_chi_tu_ky(thu_muc)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.sslsocket_class = _TlsGhiNhan
        ctx.load_cert_chain(os.path.relpath(cert), os.path.relpath(key))
        ngu_canh[ten] = ctx
    assert van_tay["a"] != van_tay["b"]

    @contextmanager
    def tao(che_do):
        tt = _TrangThaiGia()

        class Handler(_HandlerGia):
            # HTTP/1.0 buộc kết nối mới cho token/POST; không mock HTTP client.
            protocol_version = "HTTP/1.0"

            def handle(self):
                try:
                    super().handle()
                except (BrokenPipeError, ConnectionResetError):
                    # Probe chỉ đọc cert rồi đóng: TLS 1.3 có thể báo EPIPE
                    # lúc server đọc tiếp. Chỉ chấp nhận nếu chưa nhận byte HTTP.
                    if self.connection.da_nhan:
                        raise

            def _gui(self, ma, than, kieu="text/html; charset=utf-8", cookie=None):
                if self.path == DUONG_TOKEN:
                    # Đổi trước khi trả token để POST không đua với việc đổi cert.
                    self.server.da_get_token = True
                super()._gui(ma, than, kieu, cookie)

        Handler.tt = tt
        srv = _MayChuDoiCert(("127.0.0.1", 0), Handler)
        srv.che_do = che_do
        srv.ngu_canh = ngu_canh
        srv.van_tay = van_tay
        srv.tt = tt
        srv.ket_noi = []
        srv.loi_handler = []
        srv.da_get_token = False
        srv.dia_chi = f"https://127.0.0.1:{srv.server_address[1]}"
        luong = threading.Thread(
            target=srv.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True,
        )
        luong.start()
        try:
            yield srv
        finally:
            srv.shutdown()
            srv.server_close()
            luong.join(timeout=5)
            assert not luong.is_alive()
            assert srv.loi_handler == [], "Máy chủ TLS giả bị lỗi handler"

    return tao


async def _thu_dang_nhap(mod, srv):
    khach = mod.MayKhachZte(
        srv.dia_chi, TAI_KHOAN_GIA, MAT_KHAU_GIA,
        kiem_chung_chi_tls=True, van_tay_tls=srv.van_tay["a"],
    )
    loi = None
    try:
        try:
            await khach.dang_nhap()
        except mod.ZteLoiVanTay as exc:
            # Chỉ thu đúng lỗi ghim để kiểm đồng thời lỗi và byte đã rò.
            # Ngoại lệ khác phải làm test hỏng với traceback gốc.
            loi = exc
        return loi, khach.co_phien
    finally:
        await khach.dong()


@pytest.mark.parametrize("che_do", ["sau_probe", "luon_b", "sau_get_token"])
def test_cert_sai_khong_nhan_http_hoac_bi_mat(mod, may_chu_tls, che_do):
    with may_chu_tls(che_do) as srv:
        loi, co_phien = asyncio.run(_thu_dang_nhap(mod, srv))

    ket_noi_b = [k for k in srv.ket_noi if k["cert"] == "b"]
    assert ket_noi_b, "Kịch bản chưa thực sự trình chứng chỉ B"
    if che_do == "sau_get_token":
        assert srv.da_get_token, "Phải lấy token bằng cert A trước khi đổi sang B"
    da_nhan = b"".join(bytes(k["da_nhan"]) for k in ket_noi_b)
    # Chỉ hiện các boolean khi đỏ: hash mật khẩu cũng là bí mật.
    assert {
        "loi_ZteLoiVanTay": isinstance(loi, mod.ZteLoiVanTay),
        "co_phien": co_phien,
        "B_nhan_byte_HTTP": bool(da_nhan),
        "B_nhan_truong_Password": b"Password=" in da_nhan,
        "B_nhan_mat_khau": MAT_KHAU_GIA.encode() in da_nhan,
        "B_nhan_hash_mat_khau": srv.tt.bam_dung.encode() in da_nhan,
    } == {
        "loi_ZteLoiVanTay": True,
        "co_phien": False,
        "B_nhan_byte_HTTP": False,
        "B_nhan_truong_Password": False,
        "B_nhan_mat_khau": False,
        "B_nhan_hash_mat_khau": False,
    }


@pytest.mark.parametrize("may_chu_tls", [None, "b_ky_boi_a"], indirect=True)
def test_cert_dung_van_dang_nhap_thanh_cong(mod, may_chu_tls):
    with may_chu_tls("luon_a") as srv:
        loi, co_phien = asyncio.run(_thu_dang_nhap(mod, srv))

    assert loi is None
    assert co_phien is True
    assert srv.tt.lan_dang_nhap == 1
    assert all(k["cert"] == "a" for k in srv.ket_noi)
    da_nhan = b"".join(bytes(k["da_nhan"]) for k in srv.ket_noi)
    assert b"GET / HTTP/1.1\r\n" in da_nhan
    assert b"POST / HTTP/1.1\r\n" in da_nhan
    assert b"Password=" + srv.tt.bam_dung.encode() in da_nhan


@pytest.mark.parametrize("may_chu_tls", ["b_ky_boi_a"], indirect=True)
def test_chung_chi_con_cua_ca_da_ghim_van_phai_khop_van_tay(mod, may_chu_tls):
    # Tin CA A không được biến thành tin mọi chứng chỉ con của A: ghim là SHA-256.
    test_cert_sai_khong_nhan_http_hoac_bi_mat(mod, may_chu_tls, "sau_probe")


def test_cookie_dung_lai_van_kiem_chung_chi_truoc_http(mod, may_chu_tls):
    async def doc_bang_cookie(srv):
        khach = mod.MayKhachZte(
            srv.dia_chi, TAI_KHOAN_GIA, MAT_KHAU_GIA,
            van_tay_tls=srv.van_tay["a"],
        )
        khach.nhan_phien_da_co("phien-gia-da-co")
        assert srv.ket_noi == []  # nap() nhận cookie nhưng chưa được nối mạng.
        try:
            with pytest.raises(mod.ZteLoiVanTay):
                await khach.doc_may()
        finally:
            await khach.dong()

    with may_chu_tls("sau_probe") as srv:
        srv.tt.phien.add("phien-gia-da-co")
        asyncio.run(doc_bang_cookie(srv))

    ket_noi_b = [k for k in srv.ket_noi if k["cert"] == "b"]
    assert ket_noi_b
    assert all(not k["da_nhan"] for k in ket_noi_b)
    assert srv.tt.lan_dang_nhap == 0


def test_doi_ghim_dung_lai_client_va_bo_cookie_cu(mod, may_chu_tls):
    async def doi_ghim(srv):
        khach = mod.MayKhachZte(
            srv.dia_chi, TAI_KHOAN_GIA, MAT_KHAU_GIA,
            van_tay_tls=srv.van_tay["a"],
        )
        try:
            await khach.dang_nhap()
            client_cu = khach._client
            assert client_cu is not None
            assert client_cu.cookies.get(mod.TEN_GOI_COOKIE_PHIEN) == "phien-gia-1"

            srv.che_do = "luon_b"
            khach._van_tay = srv.van_tay["b"]
            await khach.dang_nhap()

            assert client_cu.is_closed
            assert khach._client is not client_cu
            assert khach.co_phien
        finally:
            await khach.dong()

    with may_chu_tls("luon_a") as srv:
        asyncio.run(doi_ghim(srv))

    assert srv.tt.lan_dang_nhap == 2
    ket_noi_b = [k for k in srv.ket_noi if k["cert"] == "b"]
    assert ket_noi_b
    assert all(b"phien-gia-1" not in bytes(k["da_nhan"]) for k in ket_noi_b)
