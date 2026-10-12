"""Đối chiếu bắt tay Noise XX của module muse với cài đặt ĐỘC LẬP (`noiseprotocol`).

Test tự đối chiếu (hai bên cùng do một người viết) chỉ chứng minh mã chạy đúng như người viết nghĩ — lần đầu bắt tay SAI
(InvalidTag) mà 23 test vẫn xanh vì bị bỏ qua. Ở đây bên kia là thư viện của người khác: hash bắt tay phải khớp và dữ liệu
mã hoá đi lại được ở CẢ HAI vai. Thiếu thư viện thì bỏ qua."""
import importlib.util
import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("noise.connection")
pytest.importorskip("cryptography")

GOC = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("muse_noise_doc_lap", GOC / "modules" / "muse" / "noise.py")
muse_noise = importlib.util.module_from_spec(spec)
sys.modules["muse_noise_doc_lap"] = muse_noise
spec.loader.exec_module(muse_noise)

from noise.connection import Keypair, NoiseConnection  # noqa: E402

TEN = b"Noise_XX_25519_AESGCM_SHA256"


def _thu_vien(la_initiator: bool) -> NoiseConnection:
    c = NoiseConnection.from_name(TEN)
    c.set_as_initiator() if la_initiator else c.set_as_responder()
    c.set_keypair_from_private_bytes(Keypair.STATIC, os.urandom(32))
    c.start_handshake()
    return c


def test_ta_la_initiator_thu_vien_la_responder():
    a, b = muse_noise.NoiseXXInitiator(), _thu_vien(False)
    b.read_message(a.write_message_1(b"p1"))
    assert a.read_message_2(bytes(b.write_message(b"p2"))) == b"p2"
    b.read_message(a.write_message_3(b"p3"))
    assert a.handshake_hash == b.get_handshake_hash()
    gui, nhan = a.split()
    assert b.decrypt(gui.encrypt_with_ad(b"", b"xin chao")) == b"xin chao"
    assert nhan.decrypt_with_ad(b"", b.encrypt(b"tra loi")) == b"tra loi"


def test_ta_la_responder_thu_vien_la_initiator():
    c, d = _thu_vien(True), muse_noise.NoiseXXResponder()
    d.read_message_1(bytes(c.write_message(b"q1")))
    assert c.read_message(d.write_message_2(b"q2")) == b"q2"
    d.read_message_3(bytes(c.write_message(b"q3")))
    assert d.handshake_hash == c.get_handshake_hash()
