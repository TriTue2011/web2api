"""Dịch vụ tách âm (dich-vu/gpu-node/tach-am/tach_am.py).

Bỏ lời gốc, giữ nhạc/hiệu ứng trước khi lồng TTS — học từ fw-tach-am của c2a:
model ONNX MDX nhẹ mặc định, batch 1, khúc 300 giây; một request một lúc
(tránh OOM VRAM); xong tệp là giải phóng.

Đường riêng (ngoài chuẩn OpenAI, khai trong manifest của nút):
``POST /v1/audio/tach-am`` (multipart: file) →
``{"ok": true, "khong_loi": "<tên tệp>", "nhac": "<tên tệp>",
"dau_vao_giay": ..., "giay_xu_ly": ...}``

Tệp kết quả nằm trong thư mục đầu ra của dịch vụ; cổng nút không phục vụ tệp
tĩnh — web2api lấy qua đường riêng của nút khi cần.
"""

from __future__ import annotations

import os
import tempfile
import threading
import time
import uuid

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse

app = FastAPI()

_khoa = threading.Lock()
_mo_hinh = None

MO_HINH = os.getenv("TACH_AM_MODEL", "MDX23C.onnx")
THU_MUC_RA = os.getenv("TACH_AM_RA", "/data/tach-am")


def _lay_mo_hinh():
    """Nạp lười audio-separator; import ở đây để test không cần gói này."""
    global _mo_hinh
    if _mo_hinh is None:
        from audio_separator.separator import Separator
        _mo_hinh = Separator(
            output_dir=THU_MUC_RA,
            output_format="wav",
            model_file_dir=os.getenv("TACH_AM_MODEL_DIR", "/data"),
        )
        _mo_hinh.load_model(MO_HINH)
    return _mo_hinh


@app.get("/health")
def suc_khoe():
    return {"ok": True, "mo_hinh": MO_HINH, "da_nap": _mo_hinh is not None}


@app.post("/v1/audio/tach-am")
async def tach_am(tep: UploadFile = File(...)):
    duoi = os.path.splitext(tep.filename or "a.wav")[1].lower() or ".wav"
    if duoi not in (".wav", ".mp3", ".flac", ".ogg", ".m4a"):
        raise HTTPException(400, f"định dạng không hỗ trợ: {duoi!r}")
    fd, duong = tempfile.mkstemp(suffix=duoi)
    try:
        with os.fdopen(fd, "wb") as f:
            while True:
                khuc = await tep.read(1 << 20)
                if not khuc:
                    break
                f.write(khuc)
        ten = f"tach-{uuid.uuid4().hex}{duoi}"
        t0 = time.time()
        with _khoa:
            mo_hinh = _lay_mo_hinh()
            # audio-separator ghi 2 stem vào THU_MUC_RA theo quy ước tên.
            ten_ra = mo_hinh.separate(duong)
        giay = round(time.time() - t0, 2)
        if isinstance(ten_ra, (list, tuple)) and len(ten_ra) >= 2:
            khong_loi, nhac = ten_ra[0], ten_ra[1]
        else:
            khong_loi, nhac = str(ten_ra), ""
        return JSONResponse({
            "ok": True, "ten": ten, "khong_loi": khong_loi, "nhac": nhac,
            "giay_xu_ly": giay,
        })
    finally:
        try:
            os.unlink(duong)
        except OSError:
            pass
