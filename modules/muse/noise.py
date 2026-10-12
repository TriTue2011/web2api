"""Giao thức Noise_XX_25519_AESGCM_SHA256 cho Muse.

Cung cấp:
- NoiseXXInitiator (cho client)
- NoiseXXResponder (cho máy chủ giả trong test)
- Nạp trễ thư viện cryptography (X25519, AESGCM)
- Chặn các điểm Curve25519 bậc thấp
- CipherState với định dạng nonce 12 byte (4 byte 0x00 + 8 byte big-endian counter)
"""

from __future__ import annotations

import hashlib
import hmac
import os
from typing import Optional

PROTOCOL_NAME = b"Noise_XX_25519_AESGCM_SHA256"

# Danh sách các điểm X25519 bậc thấp (Low-order points) bị chặn
LOW_ORDER_POINTS = {
    bytes.fromhex("0000000000000000000000000000000000000000000000000000000000000000"),
    bytes.fromhex("0100000000000000000000000000000000000000000000000000000000000000"),
    bytes.fromhex("e0eb7a7c3b41b8ae1656e3faf19fc46ada098dec9c32675dcd4c3103e62f025f"),
    bytes.fromhex("5f9c95bca3508c24b1d0b1559c83ef5b04445c65ba8607a164993a45f340837a"),
    bytes.fromhex("ecffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff7f"),
    bytes.fromhex("edffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff7f"),
    bytes.fromhex("eeffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff7f"),
    bytes.fromhex("c3eb7a7c3b41b8ae1656e3faf19fc46ada098dec9c32675dcd4c3103e62f02df"),
    bytes.fromhex("4f9c95bca3508c24b1d0b1559c83ef5b04445c65ba8607a164993a45f34083fa"),
    bytes.fromhex("dcffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff7f"),
    bytes.fromhex("ddffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff7f"),
    bytes.fromhex("deffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff7f"),
}


def _lay_crypto():
    """Nạp trễ thư viện cryptography khi cần dùng."""
    try:
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        return X25519PrivateKey, X25519PublicKey, AESGCM
    except ImportError as e:
        raise ImportError(
            "Gói 'cryptography' chưa được cài đặt trong môi trường Python. "
            "Cần cài 'cryptography' để thực hiện bắt tay và mã hoá Noise XX."
        ) from e


def kiem_tra_khoa_cong_khai(pub_bytes: bytes) -> None:
    """Kiểm tra và từ chối khoá công khai X25519 không an toàn."""
    pub_bytes = bytes(pub_bytes)  # nhận cả bytearray/memoryview (thư viện khung có thể trả dạng đó)
    if len(pub_bytes) != 32:
        raise ValueError(f"Khoá công khai X25519 phải có độ dài 32 bytes (nhận {len(pub_bytes)})")
    if pub_bytes in LOW_ORDER_POINTS or all(b == 0 for b in pub_bytes):
        raise ValueError("Khoá công khai X25519 thuộc nhóm bậc thấp (low-order point) không an toàn")


def hkdf_hash(ck: bytes, ikm: bytes, so_dau_ra: int = 2) -> list[bytes]:
    """HKDF-SHA256 tuân thủ chuẩn Noise (hàm A trong mã JS Turbopack)."""
    temp_key = hmac.new(ck, ikm, hashlib.sha256).digest()
    out1 = hmac.new(temp_key, b"\x01", hashlib.sha256).digest()
    if so_dau_ra == 1:
        return [out1]
    out2 = hmac.new(temp_key, out1 + b"\x02", hashlib.sha256).digest()
    if so_dau_ra == 2:
        return [out1, out2]
    out3 = hmac.new(temp_key, out2 + b"\x03", hashlib.sha256).digest()
    return [out1, out2, out3]


class CipherState:
    """Trạng thái mã hoá đối xứng AES-GCM với nonce bộ đếm big-endian."""

    def __init__(self, key: Optional[bytes] = None):
        self._key = key
        self._nonce = 0
        self._aesgcm = None
        if key is not None:
            _, _, AESGCM = _lay_crypto()
            self._aesgcm = AESGCM(key)

    @property
    def co_khoa(self) -> bool:
        return self._key is not None

    def dat_khoa(self, key: bytes) -> None:
        self._key = key
        self._nonce = 0
        _, _, AESGCM = _lay_crypto()
        self._aesgcm = AESGCM(key)

    def _tao_nonce_12b(self) -> bytes:
        # 4 byte 0x00 + 8 byte big-endian integer bộ đếm
        return b"\x00\x00\x00\x00" + self._nonce.to_bytes(8, "big")

    def encrypt_with_ad(self, ad: bytes, plaintext: bytes) -> bytes:
        if self._aesgcm is None:
            return plaintext
        nonce_bytes = self._tao_nonce_12b()
        ciphertext = self._aesgcm.encrypt(nonce_bytes, plaintext, ad)
        self._nonce += 1
        return ciphertext

    def decrypt_with_ad(self, ad: bytes, ciphertext: bytes) -> bytes:
        if self._aesgcm is None:
            return ciphertext
        nonce_bytes = self._tao_nonce_12b()
        plaintext = self._aesgcm.decrypt(nonce_bytes, ciphertext, ad)
        self._nonce += 1
        return plaintext


class SymmetricState:
    """Trạng thái đối xứng duy trì chaining key (ck), handshake hash (h) và CipherState."""

    def __init__(self):
        # h khởi tạo = SHA256 của tên giao thức đệm 4 byte 0x00 thành 32 byte
        pad = PROTOCOL_NAME.ljust(32, b"\x00")
        self.ck = pad
        self.h = hashlib.sha256(pad).digest()
        self.cipher = CipherState(None)

    def mix_hash(self, data: bytes) -> None:
        self.h = hashlib.sha256(self.h + data).digest()

    def mix_key(self, ikm: bytes) -> None:
        out = hkdf_hash(self.ck, ikm, 2)
        self.ck = out[0]
        self.cipher.dat_khoa(out[1])

    def encrypt_and_hash(self, plaintext: bytes) -> bytes:
        if self.cipher.co_khoa:
            ciphertext = self.cipher.encrypt_with_ad(self.h, plaintext)
            self.mix_hash(ciphertext)
            return ciphertext
        self.mix_hash(plaintext)
        return plaintext

    def decrypt_and_hash(self, ciphertext: bytes) -> bytes:
        if self.cipher.co_khoa:
            plaintext = self.cipher.decrypt_with_ad(self.h, ciphertext)
            self.mix_hash(ciphertext)
            return plaintext
        self.mix_hash(ciphertext)
        return ciphertext

    def split(self) -> tuple[CipherState, CipherState]:
        out = hkdf_hash(self.ck, b"", 2)
        return CipherState(out[0]), CipherState(out[1])


class NoiseXXInitiator:
    """Phía Client khởi tạo bắt tay Noise XX."""

    def __init__(self, static_priv_bytes: Optional[bytes] = None):
        X25519PrivateKey, _, _ = _lay_crypto()
        if static_priv_bytes:
            self._s = X25519PrivateKey.from_private_bytes(static_priv_bytes)
        else:
            self._s = X25519PrivateKey.generate()
        self._sym = SymmetricState()
        self._e: Optional[any] = None
        self._re: Optional[bytes] = None
        self._rs: Optional[bytes] = None
        self._step = 0

    @property
    def handshake_hash(self) -> bytes:
        return self._sym.h

    @property
    def static_public_bytes(self) -> bytes:
        from cryptography.hazmat.primitives import serialization
        return self._s.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw
        )

    def write_message_1(
        self,
        payload: bytes = b"",
        ephemeral_priv_bytes: Optional[bytes] = None,
    ) -> bytes:
        """Thông điệp 1: -> e, payload."""
        if self._step != 0:
            raise RuntimeError(f"Sai bước handshake: mong đợi bước 0, hiện tại {self._step}")
        X25519PrivateKey, _, _ = _lay_crypto()
        if ephemeral_priv_bytes is not None:
            self._e = X25519PrivateKey.from_private_bytes(ephemeral_priv_bytes)
        else:
            self._e = X25519PrivateKey.generate()
        from cryptography.hazmat.primitives import serialization
        e_pub = self._e.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw
        )
        kiem_tra_khoa_cong_khai(e_pub)
        self._sym.mix_hash(e_pub)
        cipher_payload = self._sym.encrypt_and_hash(payload)
        self._step = 1
        return e_pub + cipher_payload

    def read_message_2(self, msg: bytes) -> bytes:
        """Thông điệp 2: <- e, ee, s, es, payload."""
        if self._step != 1:
            raise RuntimeError(f"Sai bước handshake: mong đợi bước 1, hiện tại {self._step}")
        if len(msg) < 96:
            raise ValueError(f"Thông điệp 2 quá ngắn ({len(msg)} < 96 bytes)")
        _, X25519PublicKey, _ = _lay_crypto()
        re_bytes = msg[:32]
        kiem_tra_khoa_cong_khai(re_bytes)
        self._re = re_bytes
        self._sym.mix_hash(re_bytes)

        # ee
        re_key = X25519PublicKey.from_public_bytes(re_bytes)
        ee = self._e.exchange(re_key)
        self._sym.mix_key(ee)

        # s
        s_encrypted = msg[32:80]  # 32 byte pub + 16 byte tag
        s_bytes = self._sym.decrypt_and_hash(s_encrypted)
        kiem_tra_khoa_cong_khai(s_bytes)
        self._rs = s_bytes

        # es
        rs_key = X25519PublicKey.from_public_bytes(s_bytes)
        es = self._e.exchange(rs_key)
        self._sym.mix_key(es)

        # payload
        payload_enc = msg[80:]
        payload = self._sym.decrypt_and_hash(payload_enc)
        self._step = 2
        return payload

    def write_message_3(self, payload: bytes = b"") -> bytes:
        """Thông điệp 3: -> s, se, payload."""
        if self._step != 2:
            raise RuntimeError(f"Sai bước handshake: mong đợi bước 2, hiện tại {self._step}")
        _, X25519PublicKey, _ = _lay_crypto()
        s_pub = self.static_public_bytes
        s_enc = self._sym.encrypt_and_hash(s_pub)

        # se
        re_key = X25519PublicKey.from_public_bytes(self._re)
        se = self._s.exchange(re_key)
        self._sym.mix_key(se)

        payload_enc = self._sym.encrypt_and_hash(payload)
        self._step = 3
        return s_enc + payload_enc

    def split(self) -> tuple[CipherState, CipherState]:
        """Tách kênh sau thông điệp 3 -> (send_cipher, recv_cipher)."""
        if self._step != 3:
            raise RuntimeError("Chưa hoàn thành handshake 3 bước trước khi split")
        self._step = 4
        return self._sym.split()


class NoiseXXResponder:
    """Phía Server phản hồi bắt tay Noise XX (cho máy chủ giả)."""

    def __init__(self, static_priv_bytes: Optional[bytes] = None):
        X25519PrivateKey, _, _ = _lay_crypto()
        if static_priv_bytes:
            self._s = X25519PrivateKey.from_private_bytes(static_priv_bytes)
        else:
            self._s = X25519PrivateKey.generate()
        self._sym = SymmetricState()
        self._e: Optional[any] = None
        self._re: Optional[bytes] = None
        self._rs: Optional[bytes] = None
        self._step = 0

    @property
    def handshake_hash(self) -> bytes:
        return self._sym.h

    @property
    def static_public_bytes(self) -> bytes:
        from cryptography.hazmat.primitives import serialization
        return self._s.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw
        )

    def read_message_1(self, msg: bytes) -> bytes:
        """Đọc thông điệp 1: <- e, payload."""
        if self._step != 0:
            raise RuntimeError(f"Sai bước handshake: mong đợi bước 0, hiện tại {self._step}")
        if len(msg) < 32:
            raise ValueError(f"Thông điệp 1 quá ngắn ({len(msg)} < 32 bytes)")
        re_bytes = msg[:32]
        kiem_tra_khoa_cong_khai(re_bytes)
        self._re = re_bytes
        self._sym.mix_hash(re_bytes)
        payload = self._sym.decrypt_and_hash(msg[32:])
        self._step = 1
        return payload

    def write_message_2(
        self,
        payload: bytes = b"",
        ephemeral_priv_bytes: Optional[bytes] = None,
    ) -> bytes:
        """Thông điệp 2: -> e, ee, s, es, payload."""
        if self._step != 1:
            raise RuntimeError(f"Sai bước handshake: mong đợi bước 1, hiện tại {self._step}")
        X25519PrivateKey, X25519PublicKey, _ = _lay_crypto()
        if ephemeral_priv_bytes is not None:
            self._e = X25519PrivateKey.from_private_bytes(ephemeral_priv_bytes)
        else:
            self._e = X25519PrivateKey.generate()
        from cryptography.hazmat.primitives import serialization
        e_pub = self._e.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw
        )
        kiem_tra_khoa_cong_khai(e_pub)
        self._sym.mix_hash(e_pub)

        # ee
        re_key = X25519PublicKey.from_public_bytes(self._re)
        ee = self._e.exchange(re_key)
        self._sym.mix_key(ee)

        # s
        s_pub = self.static_public_bytes
        s_enc = self._sym.encrypt_and_hash(s_pub)

        # es
        es = self._s.exchange(re_key)
        self._sym.mix_key(es)

        payload_enc = self._sym.encrypt_and_hash(payload)
        self._step = 2
        return e_pub + s_enc + payload_enc

    def read_message_3(self, msg: bytes) -> bytes:
        """Đọc thông điệp 3: <- s, se, payload."""
        if self._step != 2:
            raise RuntimeError(f"Sai bước handshake: mong đợi bước 2, hiện tại {self._step}")
        if len(msg) < 48:
            raise ValueError(f"Thông điệp 3 quá ngắn ({len(msg)} < 48 bytes)")
        _, X25519PublicKey, _ = _lay_crypto()
        s_enc = msg[:48]
        s_bytes = self._sym.decrypt_and_hash(s_enc)
        kiem_tra_khoa_cong_khai(s_bytes)
        self._rs = s_bytes

        # se
        rs_key = X25519PublicKey.from_public_bytes(s_bytes)
        se = self._e.exchange(rs_key)
        self._sym.mix_key(se)

        payload = self._sym.decrypt_and_hash(msg[48:])
        self._step = 3
        return payload

    def split(self) -> tuple[CipherState, CipherState]:
        """Tách kênh sau thông điệp 3 -> (recv_cipher, send_cipher)."""
        if self._step != 3:
            raise RuntimeError("Chưa hoàn thành handshake 3 bước trước khi split")
        self._step = 4
        c1, c2 = self._sym.split()
        return c1, c2  # Responder: c1 là initiator send -> responder recv; c2 là initiator recv -> responder send
