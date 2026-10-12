"""Quản lý phân mảnh và lắp ráp khung vận chuyển Noise (NoiseTransportFrame).

Tuân thủ đúng các thông số và cơ chế phòng vệ của mã nguồn Muse Turbopack:
- Tối đa 65.489 bytes/mảnh
- Tối đa 256 mảnh/lắp ráp
- Tối đa 16 lắp ráp đồng thời
- Ngân sách 16 MiB/lắp ráp
- Hết hạn 60 giây
- Kiểm tra tính nhất quán và trạng thái nhiễm độc (poisoning) khi gặp lỗi.
"""

from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass, field
from typing import Optional

from .protobuf import NoiseTransportFrame

MAX_CHUNK_PAYLOAD = 65489
MAX_CHUNKS = 256
MAX_CONCURRENT_ASSEMBLIES = 16
MAX_ASSEMBLY_BYTES = 16 * 1024 * 1024  # 16 MiB
ASSEMBLY_TIMEOUT_SEC = 60.0

MIN_INT64 = -(1 << 63)
MAX_INT64 = (1 << 63) - 1


def sinh_chunk_id() -> int:
    """Sinh chunk_id ngẫu nhiên kiểu int64 có dấu."""
    val = int.from_bytes(os.urandom(8), "big", signed=False)
    if val >= (1 << 63):
        return val - (1 << 64)
    return val


def tach_khung(payload: bytes, chunk_id: Optional[int] = None) -> list[NoiseTransportFrame]:
    """Tách khối payload thành các mảnh NoiseTransportFrame <= 65489 bytes."""
    if chunk_id is None:
        chunk_id = sinh_chunk_id()
    if not (MIN_INT64 <= chunk_id <= MAX_INT64):
        raise ValueError(f"chunk_id nằm ngoài phạm vi int64: {chunk_id}")

    if len(payload) == 0:
        return [NoiseTransportFrame(chunk_id=chunk_id, chunk_index=0, total_chunks=1, payload=b"")]

    total = math.ceil(len(payload) / MAX_CHUNK_PAYLOAD)
    if total > MAX_CHUNKS:
        raise ValueError(f"Payload quá lớn cho khung Noise ({len(payload)} bytes, {total} chunks > {MAX_CHUNKS})")

    danh_sach = []
    for i in range(total):
        bat_dau = i * MAX_CHUNK_PAYLOAD
        het = min(bat_dau + MAX_CHUNK_PAYLOAD, len(payload))
        danh_sach.append(NoiseTransportFrame(
            chunk_id=chunk_id,
            chunk_index=i,
            total_chunks=total,
            payload=payload[bat_dau:het],
        ))
    return danh_sach


@dataclass
class _TienTrinhLapRap:
    total_chunks: int
    chunks: dict[int, bytes] = field(default_factory=dict)
    total_bytes: int = 0
    created_at: float = field(default_factory=time.time)
    last_updated: float = field(default_factory=time.time)


class BoGhepKhung:
    """Bộ giải mã và lắp ráp các mảnh NoiseTransportFrame."""

    def __init__(self):
        self._assemblies: dict[int, _TienTrinhLapRap] = {}
        self._poisoned: bool = False

    def them_khung(self, frame: NoiseTransportFrame) -> Optional[bytes]:
        """Thêm một mảnh khung vào bộ đệm lắp ráp. Trả về bytes nếu hoàn tất, None nếu đang chờ thêm."""
        if self._poisoned:
            raise RuntimeError("NoiseFrameDecoder: poisoned after prior failure")

        cid = frame.chunk_id
        idx = frame.chunk_index
        tot = frame.total_chunks
        pld = frame.payload

        try:
            if not (MIN_INT64 <= cid <= MAX_INT64):
                raise ValueError(f"chunk_id nằm ngoài phạm vi int64: {cid}")
            if tot < 1 or tot > MAX_CHUNKS:
                raise ValueError(f"total_chunks không hợp lệ: {tot}")
            if idx < 0 or idx >= tot:
                raise ValueError(f"chunk_index {idx} nằm ngoài khoảng [0, {tot})")
            if len(pld) > MAX_CHUNK_PAYLOAD:
                raise ValueError(f"payload mảnh quá lớn ({len(pld)} bytes > {MAX_CHUNK_PAYLOAD})")

            self._don_dep_qua_han()

            if cid not in self._assemblies:
                if len(self._assemblies) >= MAX_CONCURRENT_ASSEMBLIES:
                    raise RuntimeError("too many pending noise frame assemblies")
                now = time.time()
                self._assemblies[cid] = _TienTrinhLapRap(
                    total_chunks=tot,
                    created_at=now,
                    last_updated=now,
                )

            asm = self._assemblies[cid]
            if asm.total_chunks != tot:
                self._assemblies.pop(cid, None)
                raise ValueError(f"inconsistent totalChunks for chunkId: expected {asm.total_chunks}, got {tot}")

            if idx in asm.chunks:
                self._assemblies.pop(cid, None)
                raise ValueError(f"duplicate chunkIndex {idx}")

            asm.last_updated = time.time()
            asm.total_bytes += len(pld)
            if asm.total_bytes > MAX_ASSEMBLY_BYTES:
                self._assemblies.pop(cid, None)
                raise ValueError("assembly exceeded byte budget")

            asm.chunks[idx] = pld

            if len(asm.chunks) < asm.total_chunks:
                return None

            # Đã gom đủ tất cả các mảnh
            self._assemblies.pop(cid, None)
            if asm.total_chunks == 1:
                return asm.chunks[0]

            manh = []
            for i in range(asm.total_chunks):
                chunk = asm.chunks.get(i)
                if chunk is None:
                    raise RuntimeError(f"missing chunk {i}/{asm.total_chunks}")
                manh.append(chunk)
            return b"".join(manh)

        except Exception:
            self._poisoned = True
            raise

    def _don_dep_qua_han(self) -> None:
        now = time.time()
        qua_han = [k for k, v in self._assemblies.items() if now - v.created_at > ASSEMBLY_TIMEOUT_SEC]
        for k in qua_han:
            self._assemblies.pop(k, None)
