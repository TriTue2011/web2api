"""Dịch vụ ML phụ «nhìn nhà»: YOLO + dò mặt + vector, qua HTTP.

Hợp đồng (Bearer bắt buộc mọi đường, khoá từ biến môi trường ``NHINNHA_KHOA``):
- ``GET /suc-khoe`` → ``{"ok": true, "yolo": {"model": ..., "da_tai": bool},
  "khuon_mat": {"bo": ..., "da_tai": bool}}`` — KHÔNG chứa khoá/vector/tên.
- ``POST /quet`` ← ``{"anh_b64": "...", "nhan_mat": bool}``
  → ``{"vat_the": [...], "mat": [{"hop", "diem_do", "moc", "vector_b64",
  "net", "chuan"}]}``. Dịch vụ TRẢ vector thô cho module trong nhà; việc ánh
  xạ vector↔tên chỉ diễn ra trong sổ khuôn mặt của module — dịch vụ không
  biết tên ai.

Port phần orchestrate từ c2a `services/nhin_nha.py` (phan_tich_khung, mo_ta,
ve_khung, trang_thai) với mô hình «một khung, không luồng canh» — dịch vụ
không giữ ảnh quá một lượt /quet.

Chỉ thư viện chuẩn ở lõi đường đi (numpy/opencv/onnxruntime import LƯỜI).
"""
from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

log = logging.getLogger("nhin_nha.server")

#: Trần ảnh (base64) — 14 MB ≈ 10,5 MB ảnh thật.
TRAN_ANH_B64 = 14_000_000


def _lay_khoa() -> str:
    k = os.environ.get("NHINNHA_KHOA", "")
    if len(k) < 24:
        raise SystemExit("NHINNHA_KHOA phải đặt, tối thiểu 24 ký tự "
                         "(sinh: head -c 32 /dev/urandom | base64)")
    return k


class MayChuML:
    def __init__(self, thu_muc_model: str = "/model", ten_yolo: str = "yolo26n",
                 bo_mat: str = "buffalo_s", luong: int = 2):
        from pathlib import Path
        self.thu_muc = Path(thu_muc_model)
        self.ten_yolo = ten_yolo
        self.bo_mat = bo_mat
        self.luong = luong
        self._yolo = None
        self._mat = None
        self._khoa = threading.RLock()

    def _yolo_ngu(self):
        with self._khoa:
            if self._yolo is None:
                from yolo import BoPhatHien, get as lay_model
                mh = lay_model(self.ten_yolo) or lay_model("yolo26n")
                self._yolo = BoPhatHien(self.thu_muc / mh.tep, luong=self.luong)
            return self._yolo

    def _mat_ngu(self):
        with self._khoa:
            if self._mat is None:
                from khuon_mat import BoNhanMat, get as lay_bo
                bo = lay_bo(self.bo_mat) or lay_bo("buffalo_s")
                self._mat = BoNhanMat(self.thu_muc, bo, luong=self.luong)
            return self._mat

    def suc_khoe(self) -> dict:
        return {"ok": True,
                "yolo": {"model": self.ten_yolo,
                         "da_tai": self._yolo is not None},
                "khuon_mat": {"bo": self.bo_mat,
                              "da_tai": self._mat is not None}}

    def quet(self, anh_b64: str, nhan_mat: bool) -> dict:
        import yolo as _yolo_mod
        if len(str(anh_b64 or "")) > TRAN_ANH_B64:
            raise ValueError("anh_qua_lon")
        try:
            anh = base64.b64decode(str(anh_b64 or ""), validate=True)
        except (binascii.Error, ValueError) as e:
            raise ValueError("anh_sai") from e
        bgr = _yolo_mod.doc_anh(anh)
        cao, rong = bgr.shape[:2]
        vat_the = [v.as_dict(rong, cao) for v in
                   self._yolo_ngu().phat_hien(bgr, nguong=0.35)]
        mat = []
        if nhan_mat:
            bo_ngu = self._mat_ngu()
            for m in bo_ngu.phan_tich(bgr):
                mat.append({
                    "hop": [int(x) for x in m.hop], "diem_do": round(m.diem, 3),
                    "moc": [[round(float(x), 1), round(float(y), 1)]
                            for x, y in m.moc.tolist()] if hasattr(m.moc, "tolist")
                    else [[round(float(x), 1), round(float(y), 1)] for x, y in m.moc],
                    "vector_b64": base64.b64encode(m.vector.tobytes()).decode(),
                    "net": round(m.net, 2), "chuan": round(m.chuan, 3)})
        return {"vat_the": vat_the, "mat": mat}

    @staticmethod
    def mo_ta(kq: dict) -> str:
        """Port `nhin_nha.mo_ta`: một câu tiếng Việt từ kết quả /quet."""
        vat_the = kq.get("vat_the", [])
        mat = kq.get("mat", [])
        phan = []
        if mat:
            phan.append(f"{len(mat)} mặt")
        dem: dict[str, int] = {}
        for v in vat_the:
            dem[v["ten"]] = dem.get(v["ten"], 0) + 1
        for ten, so in sorted(dem.items(), key=lambda kv: -kv[1]):
            phan.append(f"{so} {ten}")
        return ", ".join(phan) or "không thấy gì"


class LopXuLy(BaseHTTPRequestHandler):
    may_chu_ml: MayChuML
    khoa: str

    def log_message(self, fmt, *a):
        log.info("%s %s", self.address_string(), fmt % a)

    def _tra(self, ma: int, obj: dict):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(ma)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _xac_thuc(self) -> bool:
        # So sánh trên bytes: đầu Authorization có ký tự ngoài ASCII làm
        # compare_digest(str, str) ném TypeError → luồng chết, kết nối đóng
        # (N5). Trên bytes, bất kỳ byte nào cũng so được; lệch thì 401 sạch.
        nhan = str(self.headers.get("Authorization") or "").encode("utf-8")
        dung = secrets.compare_digest(nhan, f"Bearer {self.khoa}".encode("utf-8"))
        if not dung:
            self._tra(401, {"loi": "sai khoa"})
            return False
        return True

    def _than_json(self) -> dict | None:
        try:
            n = int(str(self.headers.get("Content-Length", "0") or 0))
        except (TypeError, ValueError):
            return None  # Content-Length lạ → 400
        if n < 0:
            return None  # Content-Length âm: read(-1) sẽ treo chờ khách (N5)
        if n > TRAN_ANH_B64 + 4096:
            return None
        try:
            return json.loads(self.rfile.read(n).decode())
        except Exception:
            return None

    def do_GET(self):
        duong = urlsplit(self.path).path
        if not self._xac_thuc():
            return
        if duong == "/suc-khoe":
            return self._tra(200, self.may_chu_ml.suc_khoe())
        return self._tra(404, {"loi": "khong thay"})

    def do_POST(self):
        duong = urlsplit(self.path).path
        if not self._xac_thuc():
            return
        if duong != "/quet":
            return self._tra(404, {"loi": "khong thay"})
        than = self._than_json()
        if than is None:
            return self._tra(400, {"loi": "than sai"})
        try:
            kq = self.may_chu_ml.quet(than.get("anh_b64", ""),
                                      bool(than.get("nhan_mat", False)))
        except ValueError as e:
            ma = 413 if str(e) == "anh_qua_lon" else 400
            return self._tra(ma, {"loi": str(e)})
        except Exception:
            log.exception("quet that bai")
            return self._tra(500, {"loi": "loi noi bo"})
        return self._tra(200, kq)


def chay() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    khoa = _lay_khoa()
    LopXuLy.may_chu_ml = MayChuML(
        thu_muc_model=os.environ.get("NHINNHA_MODEL_DIR", "/model"),
        ten_yolo=os.environ.get("NHINNHA_YOLO", "yolo26n"),
        bo_mat=os.environ.get("NHINNHA_BO_MAT", "buffalo_s"),
        luong=int(os.environ.get("NHINNHA_LUONG", "2")))
    LopXuLy.khoa = khoa
    cong = int(os.environ.get("PORT", "8703"))
    # Trong container phải nghe 0.0.0.0 thì cổng publish phía máy chủ
    # (chỉ publish 127.0.0.1:8703) mới tới được; chạy trực tiếp ngoài
    # container thì mặc định vẫn chỉ loopback. Dockerfile đặt NHINNHA_HOST.
    host = os.environ.get("NHINNHA_HOST", "127.0.0.1")
    srv = ThreadingHTTPServer((host, cong), LopXuLy)
    log.info("dich-vu nhin-nha nghe %s:%d", host, cong)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    chay()
