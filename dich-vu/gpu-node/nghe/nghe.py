"""Dịch vụ nghe (dich-vu/gpu-node/nghe/nghe.py) — faster-whisper trên GPU.

Nói ĐÚNG đường OpenAI ``POST /v1/audio/transcriptions`` (multipart: file,
model, response_format, language):

- ``response_format=json`` → ``{"text": ...}`` (chuẩn OpenAI).
- ``response_format=text`` → văn bản thuần.
- ``response_format=verbose_json`` → mốc thời gian TỪNG TỪ + ``avg_logprob``
  (độ tự tin) từng đoạn — như fw-nghe của c2a.

Một lượt giải mã một lúc (khoá): quá tải VRAM là CUDA OOM, chết cả tiến trình
chứ không phải hỏng một lượt — học từ c2a fw-nghe.

Hàm định dạng kết quả là hàm thuần (không cần faster-whisper) để test được
trên máy không có GPU.
"""

from __future__ import annotations

import gc
import os
import tempfile
import threading
import time

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse

app = FastAPI()

_khoa = threading.Lock()
_mo_hinh = None

MO_HINH = os.getenv("FW_MODEL", "large-v3")
KIEU_TINH = os.getenv("FW_COMPUTE", "int8_float16")
CAN_VRAM_MB = float(os.getenv("FW_CAN_VRAM_MB", "3000"))


def _lay_mo_hinh():
    """Nạp lười faster-whisper; import ở đây để test không cần gói này."""
    global _mo_hinh
    if _mo_hinh is None:
        from faster_whisper import WhisperModel
        _mo_hinh = WhisperModel(MO_HINH, device="cuda", compute_type=KIEU_TINH,
                               download_root=os.getenv("HF_HOME", "/data"))
    return _mo_hinh


def dinh_dang_verbose_json(doan: list, ngon_ngu: str, xac_suat: float,
                           dai_giay: float) -> dict:
    """Đoạn faster-whisper → verbose_json chuẩn OpenAI (mốc từng từ)."""
    return {
        "task": "transcribe",
        "language": ngon_ngu,
        "duration": round(dai_giay, 1),
        "text": " ".join(s["chu"] for s in doan).strip(),
        "words": [
            {"word": t["chu"], "start": t["bat_dau"], "end": t["ket_thuc"],
             "probability": t.get("xac_suat")}
            for s in doan for t in s.get("tu", [])
        ],
        "segments": [
            {"id": i, "seek": 0, "start": s["bat_dau"], "end": s["ket_thuc"],
             "text": s["chu"], "tokens": [], "temperature": 0.0,
             "avg_logprob": s.get("tu_tin"), "compression_ratio": 0.0,
             "no_speech_prob": 0.0}
            for i, s in enumerate(doan)
        ],
    }


@app.get("/health")
def suc_khoe():
    return {"ok": True, "mo_hinh": MO_HINH, "kieu_tinh": KIEU_TINH,
            "da_nap": _mo_hinh is not None}


@app.post("/unload")
def go_mo_hinh():
    """Nhả hẳn Whisper/CUDA để dịch vụ khác dùng card."""
    global _mo_hinh
    with _khoa:
        _mo_hinh = None
        gc.collect()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass
    return {"ok": True, "da_nap": False}


@app.post("/v1/audio/transcriptions")
async def nghe(tep: UploadFile = File(...), model: str = Form("whisper-1"),
               response_format: str = Form("json"), language: str = Form(""),
               temperature: float = Form(0.0)):
    dinh_dang = (response_format or "json").lower()
    if dinh_dang not in ("json", "text", "verbose_json"):
        raise HTTPException(400, f"response_format không hỗ trợ: {dinh_dang!r}")
    duoi = os.path.splitext(tep.filename or "a.wav")[1] or ".wav"
    fd, duong = tempfile.mkstemp(suffix=duoi)
    try:
        with os.fdopen(fd, "wb") as f:
            while True:
                khuc = await tep.read(1 << 20)
                if not khuc:
                    break
                f.write(khuc)
        t0 = time.time()
        with _khoa:
            mo_hinh = _lay_mo_hinh()
            doan, thong_tin = mo_hinh.transcribe(
                duong, language=(language or None), word_timestamps=True,
                vad_filter=True)
            ket_qua = [{
                "bat_dau": round(s.start, 3), "ket_thuc": round(s.end, 3),
                "chu": s.text.strip(), "tu_tin": round(s.avg_logprob, 4),
                "tu": [{"chu": w.word, "bat_dau": round(w.start, 3),
                        "ket_thuc": round(w.end, 3),
                        "xac_suat": round(w.probability, 3)}
                       for w in (s.words or [])],
            } for s in doan]
            ngon_ngu = thong_tin.language
            xac_suat = round(thong_tin.language_probability, 3)
            dai_giay = round(thong_tin.duration, 1)
        xu_ly = round(time.time() - t0, 2)
        if dinh_dang == "text":
            return PlainTextResponse(" ".join(s["chu"] for s in ket_qua))
        if dinh_dang == "verbose_json":
            ra = dinh_dang_verbose_json(ket_qua, ngon_ngu, xac_suat, dai_giay)
            ra["giay_xu_ly"] = xu_ly
            return JSONResponse(ra)
        return {"text": " ".join(s["chu"] for s in ket_qua).strip()}
    finally:
        try:
            os.unlink(duong)
        except OSError:
            pass
