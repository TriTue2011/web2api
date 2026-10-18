"""Máy chủ dịch vụ van-phong-office (NV138): gọi OfficeCLI trong container phụ.

Chạy trong container riêng cạnh web2api (xem Dockerfile cùng thư mục), nghe NỘI BỘ
(127.0.0.1/mạng Docker nội bộ), xác thực bằng Bearer khoá riêng. KHÔNG có mạng ra
ngoài (triển khai với network none — xem README).

Hợp đồng (theo spec NV138, học từ c2a `services/officecli.py`):
- Chỉ nhận JSON ``{"lenh", "doi_so[]"}`` với ``lenh`` trong TẬP ĐÓNG (19 lệnh);
  ``mcp`` và mọi lệnh ngoài tập bị chặn TRƯỚC khi gọi nhị phân.
- Mọi đối số đường dẫn qua ``resolve()`` dưới thư mục làm việc; từ chối ký tự
  điều khiển, «..», symlink ra ngoài; không bao giờ truyền chuỗi thô vào shell
  (luôn dùng danh sách đối số, ``shell=False``).
- Hết giờ 60 s (lệnh dài batch/merge/import: 180 s); đầu ra ≤ 80.000 ký tự.
- Tệp đưa vào/ra qua /tai-len và /tai-xuong: ≤ 25 MB, kiểm magic bytes zip
  OOXML; tên tệp do hệ thống sinh (không dùng tên khách khai).
- OfficeCLI có kiểm tra cập nhật nền → BẮT BUỘC ``OFFICECLI_SKIP_UPDATE=1``
  (môi trường) và ``officecli config autoUpdate false`` (lúc dựng ảnh, xem
  Dockerfile); thiếu biến thì dịch vụ TỪ CHỐI khởi động.

Đường KHÔNG cần khoá (ngoại lệ có chủ đích, duy nhất): ``GET /suc-khoe`` — chỉ
trả ``{"ok": true}`` để Docker/module kiểm còn sống, không đọc hay lộ trạng thái
nào. Mọi đường khác đi qua kiểm Bearer TRƯỚC khi đọc thân yêu cầu.

Chỉ dùng thư viện chuẩn (không thêm gói).
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("dich_vu.van_phong_office")

#: Tập lệnh ĐÓNG — học từ c2a (services/officecli.py::_ALLOWED_CMDS). Mọi lệnh
#: khác của OfficeCLI (mcp, raw, raw-set, add-part, watch, install, config…)
#: bị chặn TRƯỚC khi gọi nhị phân.
LENH_CHO_PHEP = frozenset({
    "create", "view", "get", "query", "set", "add", "remove", "move", "swap",
    "validate", "batch", "merge", "dump", "open", "close", "save", "help",
    "import", "refresh",
})
#: Lệnh tốn thời gian (theo c2a: batch/merge dùng timeout dài).
LENH_HET_GIO_DAI = frozenset({"batch", "merge", "import"})

HET_GIO_MAC_DINH = 60
HET_GIO_DAI = 180
TRAN_DAU_RA = 80_000
TRAN_TAI_LEN_MAC_DINH = 25 * 1024 * 1024
TRAN_THAN_JSON = 1024 * 1024
DUOI_OOXML = (".docx", ".xlsx", ".pptx")
DUOI_DU_LIEU = (".json", ".xml")
#: ID tệp do hệ thống sinh: 16 ký tự hex.
MAU_ID_TEP = re.compile(r"^[a-f0-9]{16}$")
#: Đối số là «hình dạng đường dẫn» (ngoài phần tử đầu luôn là đường dẫn tài liệu):
#: chứa phân tách thư mục hoặc có đuôi tệp đã biết → phải resolve() dưới thư mục làm việc.
DUOI_CAN_CHUA = DUOI_OOXML + DUOI_DU_LIEU
#: Cờ của officecli nhận đường dẫn tệp dữ liệu (theo README chính thức:
#: `merge … --data`, `batch … --input`).
CO_NHAN_DU_LIEU = {"merge": "--data", "batch": "--input"}


class LoiYeuCau(Exception):
    """Yêu cầu sai khuôn — mang mã HTTP, không lộ chi tiết nội bộ."""

    def __init__(self, ma: int, loi: str):
        super().__init__(loi)
        self.ma = ma
        self.loi = loi


def _so(ten: str, mac_dinh: int) -> int:
    try:
        return int(os.environ.get(ten, str(mac_dinh)))
    except (ValueError, TypeError):
        return mac_dinh


def kiem_tra_khoa(khoa: str | None = None) -> str:
    """Khoá dịch vụ bắt buộc, ≥ 24 ký tự và ≥ 10 ký tự khác nhau (chặn «aaaa…»).

    KHÔNG in giá trị khoá ra log hay thông báo lỗi.
    """
    k = (os.environ.get("OFFICE_KHOA", "") if khoa is None else khoa).strip()
    if len(k) < 24:
        raise RuntimeError("OFFICE_KHOA bắt buộc và phải dài ít nhất 24 ký tự.")
    if len(set(k)) < 10:
        raise RuntimeError("OFFICE_KHOA quá đơn điệu: cần ít nhất 10 ký tự khác nhau.")
    return k


def lay_cau_hinh() -> dict:
    return {
        "host": os.environ.get("OFFICE_HOST", "127.0.0.1"),
        "port": _so("OFFICE_PORT", 8701),
        "thu_muc_goc": Path(os.environ.get("OFFICE_THU_MUC_GOC", "/data")),
        "bin": os.environ.get("OFFICE_BIN", "/usr/local/bin/officecli"),
        "het_gio": max(1, _so("OFFICE_HET_GIO_MAC_DINH", HET_GIO_MAC_DINH)),
        "het_gio_dai": max(1, _so("OFFICE_HET_GIO_DAI", HET_GIO_DAI)),
        "tran_tai_len": max(1, _so("OFFICE_TRAN_TAI_LEN", TRAN_TAI_LEN_MAC_DINH)),
    }


def duong_nhi_phan(cau_hinh: dict) -> str:
    """Đường nhị phân officecli — phải tồn tại và thực thi được (không thì từ chối khởi động)."""
    bin_path = cau_hinh["bin"]
    if not (Path(bin_path).is_file() and os.access(bin_path, os.X_OK)):
        raise RuntimeError(f"Không thấy nhị phân officecli thực thi được ở {bin_path!r}.")
    return bin_path


def kiem_tra_lenh(lenh) -> str:
    """Lệnh phải là chuỗi trong tập đóng — chặn TRƯỚC mọi xử lý khác."""
    if not isinstance(lenh, str) or lenh not in LENH_CHO_PHEP:
        raise LoiYeuCau(400, "lenh_khong_ho_tro")
    return lenh


def _co_ky_tu_dieu_khien(s: str) -> bool:
    return any(ord(c) < 32 or ord(c) == 127 for c in s)


def _la_doi_so_duong_dan(s: str, vi_tri_dau: bool) -> bool:
    """Phần tử đầu luôn là đường dẫn tài liệu. Các phần tử sau là đường dẫn nếu
    có HÌNH DẠNG đường dẫn theo cấu trúc: đường tuyệt đối, trỏ tới tệp đã tồn
    tại, hoặc có đuôi tệp dữ liệu đã biết — nguyên tắc cấu trúc, không liệt kê
    tên cờ. Bộ chọn phần tử (vd ``run:contains(x)``) không có hình dạng này nên
    đi qua như dữ liệu (vẫn không qua shell)."""
    if vi_tri_dau:
        return True
    thap = s.lower()
    return (
        os.path.isabs(s)
        or thap.endswith(DUOI_CAN_CHUA)
        or Path(s).exists()
    )


def chua_duong_dan(duong: str, thu_muc_goc: Path) -> Path:
    """Resolve() đường dẫn và bắt buộc nằm dưới thư mục làm việc (chặn «..» và symlink ra ngoài)."""
    if _co_ky_tu_dieu_khien(duong):
        raise LoiYeuCau(400, "doi_so_khong_hop_le")
    # Coi cả «\» là phân tách thư mục (phòng khi diễn giải đa nền tảng).
    if ".." in Path(duong.replace("\\", "/")).parts:
        raise LoiYeuCau(400, "doi_so_khong_hop_le")
    goc = thu_muc_goc.resolve()
    dich = (goc / duong).resolve() if not os.path.isabs(duong) else Path(duong).resolve()
    try:
        dich.relative_to(goc)
    except ValueError:
        raise LoiYeuCau(403, "duong_dan_ngoai_thu_muc") from None
    return dich


def lam_sach_doi_so(lenh: str, doi_so, thu_muc_goc: Path) -> list[str]:
    """Kiểm và chuẩn hoá doi_so thành danh sách đối số cho tiến trình con.

    - Mỗi phần tử là chuỗi (hoặc dict → ghi tệp JSON tạm trong thư mục làm việc,
      thay bằng cặp cờ --data/--input + đường dẫn, theo cú pháp officecli).
    - Từ chối ký tự điều khiển ở MỌI đối số; đối số hình dạng đường dẫn phải
      resolve() dưới thư mục làm việc. Không bao giờ qua shell.
    """
    if not isinstance(doi_so, list):
        raise LoiYeuCau(400, "doi_so_phai_la_danh_sach")
    sach: list[str] = []
    for i, muc in enumerate(doi_so):
        if isinstance(muc, dict):
            # Dữ liệu có cấu trúc (merge/batch): ghi tệp tạm tên ngẫu nhiên
            # trong thư mục làm việc, truyền đường dẫn qua cờ của lệnh.
            ten_tam = f".du-lieu-{secrets.token_hex(8)}.json"
            duong_tam = thu_muc_goc / ten_tam
            duong_tam.write_text(json.dumps(muc, ensure_ascii=False), encoding="utf-8")
            try:
                os.chmod(duong_tam, 0o600)
            except OSError:
                pass
            sach.append(CO_NHAN_DU_LIEU.get(lenh, "--data"))
            sach.append(str(chua_duong_dan(ten_tam, thu_muc_goc)))
            continue
        if not isinstance(muc, str):
            raise LoiYeuCau(400, "doi_so_khong_hop_le")
        if _co_ky_tu_dieu_khien(muc):
            raise LoiYeuCau(400, "doi_so_khong_hop_le")
        if _la_doi_so_duong_dan(muc, i == 0):
            sach.append(str(chua_duong_dan(muc, thu_muc_goc)))
        else:
            sach.append(muc)
    return sach


def chay_officecli(bin_path: str, lenh: str, doi_so_sach: list[str],
                   thu_muc_goc: Path, het_gio: int) -> dict:
    """Gọi nhị phân bằng danh sách đối số (không shell), môi trường tối thiểu."""
    env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
        "OFFICECLI_SKIP_UPDATE": "1",
    }
    # Chỉ cho test: nhị phân GIẢ (ho_tro_test/officecli_gia.py) đọc cờ này để
    # đổi chế độ (ngủ/đầu ra dài); nhị phân thật bỏ qua biến lạ, không ảnh hưởng.
    if "OFFICE_GIA_CHE_DO" in os.environ:
        env["OFFICE_GIA_CHE_DO"] = os.environ["OFFICE_GIA_CHE_DO"]
    try:
        proc = subprocess.run(
            [bin_path, lenh, *doi_so_sach],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=het_gio,
            cwd=str(thu_muc_goc),
            env=env,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "het_gio": True, "ma_thoat": None,
                "dau_ra": f"Timeout sau {het_gio}s", "bi_cat": False}
    dau_ra = (proc.stdout or "").strip() or (proc.stderr or "").strip() or "OK"
    bi_cat = len(dau_ra) > TRAN_DAU_RA
    if bi_cat:
        dau_ra = dau_ra[:TRAN_DAU_RA] + "\n…[cat]"
    return {"ok": proc.returncode == 0, "het_gio": False,
            "ma_thoat": proc.returncode, "dau_ra": dau_ra, "bi_cat": bi_cat}


# ── Tải lên / tải xuống ────────────────────────────────────────────────────────

def _tach_multipart(than: bytes, ranh_gioi: bytes) -> tuple[str, bytes]:
    """Tách multipart/form-data tối giản: chỉ lấy trường «tep» đầu tiên."""
    dau = b"--" + ranh_gioi
    for phan in than.split(dau):
        if b'Content-Disposition' not in phan or b'name="tep"' not in phan:
            continue
        try:
            dau_tep, du_lieu = phan.split(b"\r\n\r\n", 1)
        except ValueError:
            continue
        du_lieu = du_lieu[: -2] if du_lieu.endswith(b"\r\n") else du_lieu
        ten = ""
        m = re.search(rb'filename="([^"]{1,200})"', dau_tep)
        if m:
            try:
                ten = m.group(1).decode("utf-8", "replace")
            except ValueError:
                ten = ""
        return ten, du_lieu
    raise LoiYeuCau(400, "thieu_truong_tep")


def xu_ly_tai_len(than: bytes, kieu_noi_dung: str, thu_muc_goc: Path, tran: int) -> dict:
    """Nhận tệp OOXML: kiểm kích thước, magic bytes zip; tên do hệ thống sinh."""
    m = re.search(r"boundary=([^;\s]+)", kieu_noi_dung or "")
    if not m:
        raise LoiYeuCau(400, "thieu_boundary")
    if len(than) > tran + 1024 * 1024:
        raise LoiYeuCau(413, "tep_qua_lon")
    ten_khach, du_lieu = _tach_multipart(than, m.group(1).encode("ascii", "ignore"))
    if len(du_lieu) > tran:
        raise LoiYeuCau(413, "tep_qua_lon")
    if not du_lieu.startswith(b"PK\x03\x04"):
        raise LoiYeuCau(400, "sai_dinh_dang_tep")
    duoi = Path(ten_khach).suffix.lower()
    if duoi not in DUOI_OOXML:
        raise LoiYeuCau(400, "sai_dinh_dang_tep")
    id_tep = secrets.token_hex(8)
    dich = chua_duong_dan(id_tep + duoi, thu_muc_goc)
    dich.write_bytes(du_lieu)
    try:
        os.chmod(dich, 0o600)
    except OSError:
        pass
    return {"ok": True, "id": id_tep, "ten": id_tep + duoi, "co_byte": len(du_lieu)}


def xu_ly_tai_xuong(id_tep: str, thu_muc_goc: Path) -> tuple[bytes, str]:
    """Trả nội dung tệp theo id hệ thống sinh (đúng khuôn, đúng đuôi OOXML)."""
    if not isinstance(id_tep, str) or not MAU_ID_TEP.fullmatch(id_tep):
        raise LoiYeuCau(404, "khong_thay_tep")
    for duoi in DUOI_OOXML:
        dich = chua_duong_dan(id_tep + duoi, thu_muc_goc)
        if dich.is_file():
            return dich.read_bytes(), duoi
    raise LoiYeuCau(404, "khong_thay_tep")


KIEU_THEO_DUOI = {".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                  ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                  ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation"}


# ── Máy chủ HTTP ──────────────────────────────────────────────────────────────

class XuLy(BaseHTTPRequestHandler):
    """Xử lý HTTP: kiểm Bearer TRƯỚC khi đọc thân (trừ /suc-khoe)."""

    khoa_dich_vu: str = ""
    cau_hinh: dict = {}
    server_version = "VanPhongOffice/1.0"

    def log_message(self, fmt, *args):
        log.info("%s %s", self.address_string(), fmt % args)

    # -- tiện ích ---------------------------------------------------------
    def _tra_json(self, ma: int, obj: dict):
        du_lieu = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(ma)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(du_lieu)))
        self.end_headers()
        self.wfile.write(du_lieu)

    def _xac_thuc(self) -> bool:
        """So sánh thời gian hằng; thiếu/sai → 401, không lộ lý do chi tiết."""
        nhan = self.headers.get("Authorization", "")
        if not nhan.startswith("Bearer "):
            return False
        return hmac.compare_digest(nhan[7:].strip(), self.khoa_dich_vu)

    def _doc_than(self, tran: int) -> bytes:
        try:
            dai = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise LoiYeuCau(400, "thieu_content_length")
        if dai < 0 or dai > tran:
            raise LoiYeuCau(413, "than_qua_lon")
        return self.rfile.read(dai) if dai else b""

    # -- định tuyến --------------------------------------------------------
    def do_GET(self):
        duong = urlsplit(self.path).path
        if duong == "/suc-khoe":
            self._tra_json(200, {"ok": True})
            return
        if not self._xac_thuc():
            self._tra_json(401, {"loi": "khong_xac_thuc"})
            return
        if duong.startswith("/tai-xuong/"):
            try:
                du_lieu, duoi = xu_ly_tai_xuong(
                    duong[len("/tai-xuong/"):], self.cau_hinh["thu_muc_goc"])
                self.send_response(200)
                self.send_header("Content-Type", KIEU_THEO_DUOI[duoi])
                self.send_header("Content-Length", str(len(du_lieu)))
                self.end_headers()
                self.wfile.write(du_lieu)
            except LoiYeuCau as e:
                self._tra_json(e.ma, {"loi": e.loi})
            return
        self._tra_json(404, {"loi": "khong_co_duong"})

    def do_POST(self):
        duong = urlsplit(self.path).path
        if not self._xac_thuc():
            # Đọc và bỏ thân để giữ kết nối sạch, nhưng không xử lý gì.
            try:
                self._doc_than(TRAN_THAN_JSON)
            except LoiYeuCau:
                pass
            self._tra_json(401, {"loi": "khong_xac_thuc"})
            return
        try:
            if duong == "/lenh":
                than = self._doc_than(TRAN_THAN_JSON)
                try:
                    yeu_cau = json.loads(than.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    raise LoiYeuCau(400, "than_khong_phai_json")
                if not isinstance(yeu_cau, dict):
                    raise LoiYeuCau(400, "than_khong_phai_json")
                lenh = kiem_tra_lenh(yeu_cau.get("lenh"))
                thu_muc = self.cau_hinh["thu_muc_goc"]
                doi_so_sach = lam_sach_doi_so(lenh, yeu_cau.get("doi_so", []), thu_muc)
                het_gio = (self.cau_hinh["het_gio_dai"] if lenh in LENH_HET_GIO_DAI
                           else self.cau_hinh["het_gio"])
                ket_qua = chay_officecli(self.cau_hinh["bin"], lenh, doi_so_sach,
                                         thu_muc, het_gio)
                log.info("lenh=%s doi_so=%d ok=%s", lenh, len(doi_so_sach), ket_qua["ok"])
                self._tra_json(200, ket_qua)
            elif duong == "/tai-len":
                than = self._doc_than(self.cau_hinh["tran_tai_len"] + 1024 * 1024)
                ket_qua = xu_ly_tai_len(than, self.headers.get("Content-Type", ""),
                                        self.cau_hinh["thu_muc_goc"],
                                        self.cau_hinh["tran_tai_len"])
                log.info("tai-len id=%s co_byte=%d", ket_qua["id"], ket_qua["co_byte"])
                self._tra_json(200, ket_qua)
            else:
                self._tra_json(404, {"loi": "khong_co_duong"})
        except LoiYeuCau as e:
            self._tra_json(e.ma, {"loi": e.loi})
        except (ConnectionError, BrokenPipeError):
            pass


def tao_may_chu() -> tuple[ThreadingHTTPServer, dict]:
    """Dựng máy chủ (chưa chạy): kiểm khoá, cờ tắt cập nhật, nhị phân, thư mục."""
    khoa = kiem_tra_khoa()
    if os.environ.get("OFFICECLI_SKIP_UPDATE") != "1":
        raise RuntimeError(
            "OFFICECLI_SKIP_UPDATE=1 bắt buộc (OfficeCLI có kiểm tra cập nhật nền).")
    cau_hinh = lay_cau_hinh()
    cau_hinh["bin"] = duong_nhi_phan(cau_hinh)
    thu_muc = cau_hinh["thu_muc_goc"]
    thu_muc.mkdir(parents=True, exist_ok=True)
    # Kiểm tra khởi động: gọi --version với cờ tắt cập nhật (không mạng ra ngoài).
    try:
        proc = subprocess.run(
            [cau_hinh["bin"], "--version"],
            capture_output=True, timeout=15, cwd=str(thu_muc),
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                 "HOME": os.environ.get("HOME", "/tmp"),
                 "OFFICECLI_SKIP_UPDATE": "1"},
        )
        log.info("officecli --version: %s", (proc.stdout or b"").decode("utf-8", "replace")[:80])
    except (subprocess.TimeoutExpired, OSError) as e:
        raise RuntimeError(f"Không khởi động được officecli: {type(e).__name__}")
    XuLy.khoa_dich_vu = khoa
    XuLy.cau_hinh = cau_hinh
    srv = ThreadingHTTPServer((cau_hinh["host"], cau_hinh["port"]), XuLy)
    srv.daemon_threads = True
    return srv, cau_hinh


def main() -> None:
    srv, cau_hinh = tao_may_chu()
    log.info("van-phong-office nghe %s:%d, thư mục %s",
             cau_hinh["host"], srv.server_address[1], cau_hinh["thu_muc_goc"])
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
