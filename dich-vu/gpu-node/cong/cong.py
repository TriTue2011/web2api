"""Lớp cổng nút GPU (dich-vu/gpu-node/cong/cong.py).

Chạy trên máy có card NVIDIA, đứng trước các dịch vụ nghe/thị giác/tách âm,
nói đúng «hợp đồng nút GPU» của web2api (NV141):

- Xác thực Bearer bằng khoá riêng của nút: sinh ngẫu nhiên lần đầu, ghi file
  0600 trong volume dữ liệu, in ra console ĐÚNG MỘT LẦN, không có khoá mặc định.
- ``GET /health`` → ``{ok, phien_ban, gpu, mo_hinh_da_nap, hang_doi}``;
  thông tin card đọc từ ``nvidia-smi`` dạng CSV an toàn; không có card hoặc
  nvidia-smi lỗi/in rác → ``gpu: []`` chứ không đổ.
- ``GET /.well-known/w2a-node`` → manifest khả năng của nút.
- Hàng đợi + giới hạn đồng thời (mặc định 1 cho cả nút): vượt hạn chờ thì
  trả 429 kèm ``Retry-After``.
- Danh sách IP được phép (mặc định rỗng = chấp nhận mọi IP gọi tới).
- Chuyển tiếp ngược (reverse proxy) các đường ``/v1/*`` tới dịch vụ nội bộ.

Chỉ dùng thư viện chuẩn — ảnh nhẹ, không thêm gói.
"""

from __future__ import annotations

import http.client
import json
import logging
import os
import secrets
import stat
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

PHIEN_BAN = "1.0.0"

#: Semaphore giới hạn đồng thời của lần ``tao_may_chu`` gần nhất — test dùng
#: để giữ chặt khe và kiểm đường 429.
_semaphore_toan_cuc: threading.Semaphore | None = None

# Đường công khai → tên dịch vụ nội bộ (khớp tiền tố, thứ tự dài trước).
_DUONG_DICH_VU = (
    ("/v1/audio/tach-am", "tach-am"),
    ("/v1/audio/transcriptions", "nghe"),
    ("/v1/chat/completions", "thi-giac"),
    ("/unload", "nghe"),
)

_TIEU_DE_BO_QUA = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "host",
}


def khoi_tao_khoa(thu_muc: str, khoa_ghi_de: str | None = None) -> str:
    """Lấy khoá nút; chưa có thì sinh ngẫu nhiên, ghi file 0600, in ra MỘT lần."""
    os.makedirs(thu_muc, exist_ok=True)
    duong = os.path.join(thu_muc, "khoa")
    if os.path.exists(duong):
        with open(duong, encoding="utf-8") as f:
            return f.read().strip()
    khoa = khoa_ghi_de or secrets.token_urlsafe(32)
    with open(duong, "w", encoding="utf-8") as f:
        f.write(khoa + "\n")
    os.chmod(duong, 0o600)
    # In đúng một lần ở lần sinh đầu — người dựng lấy khoá nối web2api.
    print(f"KHOA NUT GPU (luu lai, chi hien mot lan): {khoa}", flush=True)
    return khoa


def doc_gpu(lenh_smi: str = "nvidia-smi") -> list[dict]:
    """Đọc card NVIDIA bằng nvidia-smi (CSV an toàn); lỗi → danh sách rỗng."""
    try:
        ra = subprocess.run(
            [lenh_smi, "--query-gpu=name,memory.total,memory.used,"
             "temperature.gpu,utilization.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5)
        if ra.returncode != 0:
            return []
        ket_qua = []
        for dong in ra.stdout.strip().splitlines():
            cot = [c.strip() for c in dong.split(",")]
            if len(cot) != 5:
                continue
            ket_qua.append({
                "ten": cot[0],
                "vram_tong_mb": int(float(cot[1])),
                "vram_dung_mb": int(float(cot[2])),
                "nhiet_c": float(cot[3]),
                "tai_pct": float(cot[4]),
            })
        return ket_qua
    except Exception:
        return []


class _XuLy(BaseHTTPRequestHandler):
    cau_hinh: dict = {}
    nhat_ky: logging.Logger | None = None
    _dang_chay = 0
    _khoa_dem = threading.Lock()

    # -- tiện ích ------------------------------------------------------

    def _tra_json(self, ma: int, doi_tuong, tieu_de: dict | None = None):
        than = json.dumps(doi_tuong, ensure_ascii=False).encode()
        self.send_response(ma)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(than)))
        for k, v in (tieu_de or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(than)

    def _ghi_log(self, ma: int):
        # Chỉ ghi phương thức + đường + mã — KHÔNG ghi tiêu đề (tránh lộ khoá).
        if self.nhat_ky:
            self.nhat_ky.info("%s %s -> %s", self.command, self.path.split("?")[0], ma)

    def _kiem_ip(self) -> bool:
        cho_phep = self.cau_hinh.get("ip_duoc_phep") or []
        if not cho_phep:
            return True
        return self.client_address[0] in cho_phep

    def _xac_thuc(self) -> bool:
        nhan = self.headers.get("Authorization", "")
        return nhan == "Bearer " + self.cau_hinh.get("khoa", "")

    def _tuyen_duong(self) -> str | None:
        duong = urlsplit(self.path).path
        for tien_to, ten in _DUONG_DICH_VU:
            if duong == tien_to or duong.startswith(tien_to + "/"):
                return ten
        return None

    def _chuyen_tiep(self, ten_dv: str):
        co_so = self.cau_hinh["dich_vu"][ten_dv]
        phan = urlsplit(co_so)
        dai = int(self.headers.get("Content-Length", 0) or 0)
        than = self.rfile.read(dai) if dai else None
        tieu_de = {k: v for k, v in self.headers.items()
                   if k.lower() not in _TIEU_DE_BO_QUA}
        lop = (http.client.HTTPSConnection if phan.scheme == "https"
               else http.client.HTTPConnection)
        ket_noi = lop(phan.hostname, phan.port or (443 if phan.scheme == "https" else 80),
                      timeout=600)
        try:
            ket_noi.request(self.command, self.path, body=than, headers=tieu_de)
            dap = ket_noi.getresponse()
            du_lieu = dap.read()
            self.send_response(dap.status)
            for k, v in dap.getheaders():
                if k.lower() not in _TIEU_DE_BO_QUA and k.lower() != "content-length":
                    self.send_header(k, v)
            self.send_header("Content-Length", str(len(du_lieu)))
            self.end_headers()
            self.wfile.write(du_lieu)
            self._ghi_log(dap.status)
        finally:
            ket_noi.close()

    def _phuc_vu(self):
        if not self._kiem_ip():
            self._tra_json(403, {"loi": "ip_khong_duoc_phep"})
            self._ghi_log(403)
            return
        if not self._xac_thuc():
            self._tra_json(401, {"loi": "can_xac_thuc"})
            self._ghi_log(401)
            return
        duong = urlsplit(self.path).path
        ch = self.cau_hinh
        if duong == "/health":
            with self._khoa_dem:
                chay = self._dang_chay
            self._tra_json(200, {
                "ok": True,
                "phien_ban": ch.get("phien_ban", PHIEN_BAN),
                "gpu": doc_gpu(ch.get("nvidia_smi", "nvidia-smi")),
                "mo_hinh_da_nap": ch.get("mo_hinh", []),
                "hang_doi": {"dang_chay": chay, "cho": 0},
            })
            self._ghi_log(200)
            return
        if duong == "/.well-known/w2a-node":
            self._tra_json(200, {
                "id": ch.get("id", "gpu-node"),
                "ten": ch.get("ten", "Máy GPU"),
                "kha_nang": sorted(ch.get("dich_vu", {}).keys()),
                "mo_hinh": ch.get("mo_hinh", []),
                "gioi_han_dong_thoi": ch.get("gioi_han_dong_thoi", 1),
            })
            self._ghi_log(200)
            return
        ten_dv = self._tuyen_duong()
        if ten_dv is None or ten_dv not in ch.get("dich_vu", {}):
            self._tra_json(404, {"loi": "khong_co_duong"})
            self._ghi_log(404)
            return
        sem = self.server.semaphore
        if not sem.acquire(timeout=ch.get("han_cho_giay", 60)):
            self._tra_json(429, {"loi": "nut_dang_ban"},
                           {"Retry-After": "5"})
            self._ghi_log(429)
            return
        try:
            with self._khoa_dem:
                type(self)._dang_chay += 1
            self._chuyen_tiep(ten_dv)
        finally:
            with self._khoa_dem:
                type(self)._dang_chay -= 1
            sem.release()

    do_GET = _phuc_vu
    do_POST = _phuc_vu
    do_DELETE = _phuc_vu

    def log_message(self, *args):
        pass


def tao_may_chu(cau_hinh: dict) -> ThreadingHTTPServer:
    """Dựng máy chủ cổng từ dict cấu hình; chưa bind cố định (cong=0 được)."""
    global _semaphore_toan_cuc
    _semaphore_toan_cuc = threading.Semaphore(cau_hinh.get("gioi_han_dong_thoi", 1))

    nhat_ky = logging.getLogger("cong_node")
    if not nhat_ky.handlers:
        thu_muc = cau_hinh.get("thu_muc_du_lieu", ".")
        os.makedirs(thu_muc, exist_ok=True)
        fh = logging.FileHandler(os.path.join(thu_muc, "cong.log"), encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        nhat_ky.addHandler(fh)
        nhat_ky.setLevel(logging.INFO)

    class XuLy(_XuLy):
        pass

    XuLy.cau_hinh = cau_hinh
    XuLy.nhat_ky = nhat_ky
    may_chu = ThreadingHTTPServer(
        (cau_hinh.get("dia_chi", "127.0.0.1"), cau_hinh.get("cong", 8091)), XuLy)
    may_chu.semaphore = _semaphore_toan_cuc
    return may_chu


def doc_cau_hinh_tu_moi_truong() -> dict:
    """Cấu hình chạy thật từ biến môi trường GPU_NODE_*."""
    dich_vu = {}
    if os.getenv("NGHE_URL", "http://nghe:5000"):
        dich_vu["nghe"] = os.getenv("NGHE_URL", "http://nghe:5000")
    if os.getenv("THI_GIAC_URL"):
        dich_vu["thi-giac"] = os.environ["THI_GIAC_URL"]
    if os.getenv("TACH_AM_URL"):
        dich_vu["tach-am"] = os.environ["TACH_AM_URL"]
    return {
        "dia_chi": os.getenv("GPU_NODE_LISTEN", "127.0.0.1"),
        "cong": int(os.getenv("GPU_NODE_PORT", "8091")),
        "thu_muc_du_lieu": os.getenv("GPU_NODE_DATA", "/data"),
        "dich_vu": dich_vu,
        "gioi_han_dong_thoi": int(os.getenv("GPU_NODE_DONG_THOI", "1")),
        "han_cho_giay": int(os.getenv("GPU_NODE_HAN_CHO", "120")),
        "ip_duoc_phep": [x.strip() for x in os.getenv("GPU_NODE_IP_CHO_PHEP", "").split(",") if x.strip()],
        "ten": os.getenv("GPU_NODE_TEN", "Máy GPU"),
        "id": os.getenv("GPU_NODE_ID", "gpu-node"),
        "phien_ban": PHIEN_BAN,
        "nvidia_smi": os.getenv("NVIDIA_SMI", "nvidia-smi"),
        "mo_hinh": [m.strip() for m in os.getenv("GPU_NODE_MO_HINH", "large-v3").split(",") if m.strip()],
    }


def main():
    cau_hinh = doc_cau_hinh_tu_moi_truong()
    cau_hinh["khoa"] = khoi_tao_khoa(cau_hinh["thu_muc_du_lieu"])
    may_chu = tao_may_chu(cau_hinh)
    print(f"cong dang nghe {cau_hinh['dia_chi']}:{may_chu.server_address[1]}", flush=True)
    may_chu.serve_forever()


if __name__ == "__main__":
    main()
