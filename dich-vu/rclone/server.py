"""Máy chủ dịch vụ rclone (NV161): cầu nối tới nhị phân rclone trong container phụ.

Nguồn port (LUẬT PORT, chủ máy 19/10 — chỉ đổi lớp kết nối/khuôn module/xác
thực, không viết lại): /opt/claude-c2a/chatgpt2api @ d879b03
  - services/rclone_service.py (480 dòng): ba ràng buộc an toàn ở đầu tệp GIỮ
    NGUYÊN Ý — (1) đường cục bộ khoá trong thư mục làm việc bằng resolve(),
    (2) không bao giờ qua shell (danh sách đối số), (3) tệp cấu hình chứa token
    thật ở riêng, quyền 0600, không lẫn vào log/phản hồi.
  - api/rclone.py (FastAPI): lớp router QUẢN TRỊ — không port nguyên (là lớp
    c2a-specific); module web2api gọi thẳng các hàm dịch vụ qua HTTP bên dưới.
Khác c2a: chạy tiến trình riêng trong container (không trong tiến trình web2api);
client_secret không lưu ở dịch vụ, truyền theo từng lượt đổi mã; thư mục làm
việc riêng /data/workspace (không chung với Office); chuyển tệp nhị phân qua
hai bước /tep-len + /tep-xuong (id hệ thống sinh) thay vì đọc qua tiến trình
chung; mọi đường (trừ /suc-khoe) kiểm Bearer TRƯỚC khi đọc thân.

Hợp đồng:
- Chỉ nhận JSON ``{"lenh", "tham_so"}`` với ``lenh`` trong TẬP ĐÓNG (ánh xạ sang
  lệnh rclone an toàn); mọi tham số đường dẫn qua ``resolve()`` dưới thư mục
  làm việc; tên remote/loại/khoá tham số qua regex; không bao giờ truyền chuỗi
  thô vào shell (luôn ``shell=False``).
- Hết giờ 30 s (tra cứu) / 600 s (truyền); đầu ra cắt ở trần.
- Cấu hình rclone chứa token R2/S3/Drive thật: tệp 0600, API chỉ trả bản đã che;
  log không chứa bí mật.
- Binary rclone (Go, MIT) do Dockerfile tự tải + ghim SHA-256 từng kiến trúc.

Đường KHÔNG cần khoá (ngoại lệ có chủ đích, duy nhất): ``GET /suc-khoe`` — chỉ
trả ``{"ok": true}`` để Docker/module kiểm còn sống, không đọc hay lộ trạng thái
nào. Mọi đường khác đi qua kiểm Bearer TRƯỚC khi đọc thân yêu cầu.

Chỉ dùng thư viện chuẩn (không thêm gói).
"""

from __future__ import annotations

import base64
import hmac
import io
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode, urlparse, parse_qs
from urllib.request import Request, urlopen

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("dich_vu.rclone")

#: Tập lệnh ĐÓNG của dịch vụ — tên op (không phải lệnh rclone thô).
#: Mỗi op ánh xạ sang đúng một cách gọi rclone an toàn bên dưới.
LENH_CHO_PHEP = frozenset({
    "trang_thai", "remotes", "config_xem", "config_ghi",
    "them_remote", "xoa_remote", "kiem_tra",
    "drive_start", "drive_exchange", "luu_khoa_json",
    "liet_ke", "doc_chu", "tai_ve", "gui_len", "xoa", "chuyen_tep",
})

HET_GIO_NHANH = 30    # tra cứu: version, config dump, lsjson, about
HET_GIO_TRUYEN = 600  # truyền file: copyto, cat, config create
TRAN_DOC_BYTES = 2_000_000   # trần đọc thẳng nội dung file text
TRAN_TAI_LEN_MAC_DINH = 25 * 1024 * 1024
TRAN_THAN_JSON = 1024 * 1024
TRAN_TEP_QUA_LENH = 25 * 1024 * 1024  # trần byte trả về cho tai_ve qua API
MAX_MUC = 500             # trần số mục liệt kê thư mục
#: ID tệp do hệ thống sinh: 16 ký tự hex.
MAU_ID_TEP = re.compile(r"^[a-f0-9]{16}$")

#: Tên remote: chữ/số/._- , 1..64 ký tự (giữ nguyên từ c2a).
_TEN_REMOTE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
#: Loại remote đứng trước dấu ``--`` của CLI (giữ nguyên từ c2a).
_LOAI_REMOTE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
#: Tên tham số remote sau ``--`` (giữ nguyên từ c2a).
_KHOA_THAM_SO_REMOTE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")
#: Khoá nào là bí mật thì che khi trả cấu hình ra giao diện (giữ nguyên từ c2a).
_KHOA_BI_MAT = re.compile(
    r"(token|pass|password|secret|key|credentials|client_secret|sa_file)",
    re.I)


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
    k = (os.environ.get("RCLONE_KHOA", "") if khoa is None else khoa).strip()
    if len(k) < 24:
        raise RuntimeError("RCLONE_KHOA bắt buộc và phải dài ít nhất 24 ký tự.")
    if len(set(k)) < 10:
        raise RuntimeError("RCLONE_KHOA quá đơn điệu: cần ít nhất 10 ký tự khác nhau.")
    return k


def lay_cau_hinh() -> dict:
    return {
        "host": os.environ.get("RCLONE_HOST", "127.0.0.1"),
        "port": _so("RCLONE_PORT", 8702),
        "thu_muc_goc": Path(os.environ.get("RCLONE_THU_MUC_GOC", "/data")),
        "bin": os.environ.get("RCLONE_BIN", "/usr/local/bin/rclone"),
        "het_gio": max(1, _so("RCLONE_HET_GIO", HET_GIO_NHANH)),
        "het_gio_dai": max(1, _so("RCLONE_HET_GIO_DAI", HET_GIO_TRUYEN)),
        "tran_tai_len": max(1, _so("RCLONE_TRAN_TAI_LEN", TRAN_TAI_LEN_MAC_DINH)),
    }


def thu_muc_data() -> Path:
    d = Path(lay_cau_hinh()["thu_muc_goc"])
    d.mkdir(parents=True, exist_ok=True)
    return d


def thu_muc_lam_viec() -> Path:
    """Thư mục DUY NHẤT phía máy cục bộ mà rclone được đọc/ghi (ràng buộc 1).

    Riêng của dịch vụ này (không chung với Office): /data/workspace.
    """
    d = thu_muc_data() / "workspace"
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    return d.resolve()


def conf_path() -> Path:
    """Tệp cấu hình rclone (ràng buộc 3): riêng, 0600, trên volume data."""
    d = thu_muc_data() / "rclone"
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    p = d / "rclone.conf"
    if not p.exists():
        p.touch()
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass
    return p


def duong_nhi_phan(cau_hinh: dict) -> str:
    """Đường nhị phân rclone — phải tồn tại và thực thi được."""
    bin_path = cau_hinh["bin"]
    if not (Path(bin_path).is_file() and os.access(bin_path, os.X_OK)):
        raise RuntimeError(f"Không thấy nhị phân rclone thực thi được ở {bin_path!r}.")
    return bin_path


def _duong_dan_cuc_bo(p: str, thu_muc_goc: Path) -> Path:
    """Ép đường dẫn cục bộ nằm trong thư mục làm việc (ràng buộc 1)."""
    goc = thu_muc_goc.resolve()
    dd = Path(str(p or "").strip())
    if not dd.is_absolute():
        dd = goc / dd
    that = dd.resolve()  # đi hết symlink
    if that != goc and goc not in that.parents:
        raise LoiYeuCau(403, "duong_dan_ngoai_thu_muc")
    return that


def _kiem_remote(duong_dan: str) -> str:
    """Đường dẫn đám mây phải dạng `ten_remote:duong/dan`."""
    s = str(duong_dan or "").strip()
    if ":" not in s:
        raise LoiYeuCau(400, "thieu_ten_remote")
    ten = s.split(":", 1)[0]
    if not _TEN_REMOTE.match(ten):
        raise LoiYeuCau(400, "ten_remote_khong_hop_le")
    return s


def _rclone() -> str:
    exe = shutil.which(cau_hinh_toan_cuc()["bin"]) or cau_hinh_toan_cuc()["bin"]
    p = Path(exe)
    if not (p.is_file() and os.access(p, os.X_OK)):
        raise FileNotFoundError("chưa cài rclone trong image")
    return str(p)


def cau_hinh_toan_cuc() -> dict:
    return _CAU_HINH


_CAU_HINH: dict = {}


def _chay(args: list[str], *, timeout: int = HET_GIO_NHANH) -> tuple[bool, str, str]:
    """Chạy một lệnh rclone bằng DANH SÁCH đối số (ràng buộc 2: không shell)."""
    lenh = [_rclone(), "--config", str(conf_path()), *args]
    try:
        r = subprocess.run(lenh, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, "", f"quá {timeout} giây không xong"
    except FileNotFoundError as exc:
        return False, "", str(exc)
    if r.returncode != 0:
        # stderr của rclone có thể kèm URL có token — cắt ngắn, chỉ ghi cảnh báo.
        loi = (r.stderr or "").strip()[:400]
        log.warning("rclone %s lỗi: %s", args[0] if args else "?", loi[:200])
        return False, r.stdout or "", loi
    return True, r.stdout or "", ""


# ── Trạng thái & cấu hình ────────────────────────────────────────────────────

def san_sang() -> dict:
    try:
        _rclone()
    except FileNotFoundError as exc:
        return {"ok": False, "error": str(exc), "version": "", "remotes": []}
    ok, out, err = _chay(["version"])
    ban = ""
    if ok:
        dong = (out.strip().splitlines() or [""])[0]
        ban = dong.replace("rclone", "").strip()
    return {"ok": ok, "error": err, "version": ban, "remotes": remotes()}


def remotes() -> list[dict]:
    ok, out, _ = _chay(["config", "dump"])
    if not ok:
        return []
    try:
        data = json.loads(out or "{}")
    except json.JSONDecodeError:
        return []
    return [{"name": ten, "type": str((cfg or {}).get("type") or "")}
            for ten, cfg in sorted(data.items())]


def config_da_che() -> str:
    """Nội dung rclone.conf với mọi giá trị bí mật thay bằng •••."""
    try:
        raw = conf_path().read_text("utf-8")
    except OSError:
        return ""
    ra = []
    for dong in raw.splitlines():
        if "=" in dong and not dong.lstrip().startswith((";", "#", "[")):
            khoa, _, gt = dong.partition("=")
            if _KHOA_BI_MAT.search(khoa) and gt.strip():
                ra.append(f"{khoa}= •••")
                continue
        ra.append(dong)
    return "\n".join(ra)


def dat_config(noi_dung: str) -> dict:
    """Ghi đè rclone.conf; hỏng thì trả lại bản cũ."""
    text = str(noi_dung or "")
    if "•••" in text:
        return {"ok": False, "error":
                "Nội dung còn dấu ••• — đó là bản đã che, hãy dán cấu hình thật"}
    p = conf_path()
    try:
        cu = p.read_text("utf-8")
    except OSError:
        cu = ""
    p.write_text(text, "utf-8")
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    ok, _, err = _chay(["config", "dump"])
    if not ok:
        p.write_text(cu, "utf-8")
        return {"ok": False, "error": f"cấu hình không hợp lệ, đã giữ bản cũ: {err[:200]}"}
    return {"ok": True, "remotes": remotes()}


# ── Đăng nhập Google Drive bằng đường dẫn (không cần dòng lệnh) ────────────
# Port từ c2a services/rclone_service.py: cùng lối — máy chủ dựng đường dẫn,
# người dùng cấp quyền rồi dán lại đường dẫn đích chứa mã. Dùng urllib của
# thư viện chuẩn thay curl_cffi (không thêm gói).
_DRIVE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
_DRIVE_TOKEN = "https://oauth2.googleapis.com/token"
_DRIVE_REDIRECT = "http://127.0.0.1:53682/"


def drive_duong_dan_dang_nhap(client_id: str, scope: str = "drive") -> dict:
    cid = str(client_id or "").strip()
    if not cid:
        return {"ok": False, "error": "Thiếu Client ID"}
    q = urlencode({
        "client_id": cid,
        "redirect_uri": _DRIVE_REDIRECT,
        "response_type": "code",
        "scope": f"https://www.googleapis.com/auth/{str(scope or 'drive').strip()}",
        "access_type": "offline",
        "prompt": "consent",
    })
    return {"ok": True, "auth_url": f"{_DRIVE_AUTH}?{q}"}


def _ma_tu_duong_dan(s: str) -> str:
    s = str(s or "").strip()
    if not s:
        return ""
    if "code=" not in s:
        return s
    return (parse_qs(urlparse(s).query).get("code") or [""])[0]


def drive_doi_ma_lay_token(ten: str, client_id: str, client_secret: str,
                           redirect_url: str, *, scope: str = "drive",
                           root_folder_id: str = "") -> dict:
    """Đổi mã cấp quyền lấy token rồi khai luôn remote Drive."""
    if not _TEN_REMOTE.match(str(ten or "")):
        return {"ok": False, "error": "Tên kho chỉ gồm chữ, số, dấu . _ -"}
    ma = _ma_tu_duong_dan(redirect_url)
    if not ma:
        return {"ok": False, "error": "Không tìm thấy mã trong đường dẫn vừa dán"}
    du_lieu = urlencode({
        "code": ma,
        "client_id": str(client_id or "").strip(),
        "client_secret": str(client_secret or "").strip(),
        "redirect_uri": _DRIVE_REDIRECT,
        "grant_type": "authorization_code",
    }).encode()
    try:
        req = Request(_DRIVE_TOKEN, data=du_lieu,
                      headers={"Content-Type": "application/x-www-form-urlencoded"})
        with urlopen(req, timeout=60) as resp:
            than = resp.read().decode("utf-8", "replace")
            ma_http = resp.status
    except Exception as exc:
        return {"ok": False, "error": f"Không gọi được Google: {str(exc)[:150]}"}
    if ma_http != 200:
        return {"ok": False, "error": f"Google từ chối: {than[:200]}"}
    try:
        tok = json.loads(than)
    except json.JSONDecodeError:
        return {"ok": False, "error": "Google trả dữ liệu không đọc được"}
    if not tok.get("refresh_token"):
        return {"ok": False, "error":
                "Google không trả refresh_token — token sẽ hết hạn sau một giờ. "
                "Hãy bấm lại nút đăng nhập và cấp quyền lại từ đầu."}
    from datetime import datetime, timedelta, timezone
    het_han = datetime.now(timezone.utc) + timedelta(
        seconds=int(tok.get("expires_in") or 3600))
    token_json = json.dumps({
        "access_token": tok.get("access_token"),
        "token_type": tok.get("token_type") or "Bearer",
        "refresh_token": tok.get("refresh_token"),
        "expiry": het_han.isoformat(),
    })
    tham_so = {"client_id": client_id, "client_secret": client_secret,
               "token": token_json, "scope": scope}
    if str(root_folder_id or "").strip():
        tham_so["root_folder_id"] = root_folder_id
    return tao_remote(ten, "drive", tham_so)


def luu_khoa_json(ten: str, noi_dung: str) -> dict:
    """Lưu tệp khoá JSON tài khoản dịch vụ (0600), trả đường dẫn để điền vào form."""
    try:
        du_lieu = json.loads(noi_dung or "")
    except (json.JSONDecodeError, TypeError):
        return {"ok": False, "error": "Tệp không phải JSON hợp lệ"}
    if not isinstance(du_lieu, dict):
        return {"ok": False, "error": "Tệp JSON không đúng dạng khoá tài khoản dịch vụ"}
    thieu = [k for k in ("client_email", "private_key") if not du_lieu.get(k)]
    if thieu:
        return {"ok": False, "error":
                "Đây không phải tệp khoá tài khoản dịch vụ (thiếu "
                + ", ".join(thieu) + "). Tải lại tệp JSON từ Google Cloud."}
    an_toan = re.sub(r"[^A-Za-z0-9._-]+", "_", str(ten or "").strip())[:60]
    an_toan = an_toan.removesuffix(".json") or "khoa"
    p = conf_path().parent / f"{an_toan}.json"
    p.write_text(json.dumps(du_lieu), "utf-8")
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    return {"ok": True, "duong_dan": str(p), "email": str(du_lieu.get("client_email") or "")}


def tao_remote(ten: str, loai: str, tham_so: dict) -> dict:
    """Khai một remote không cần OAuth. --obscure BẮT BUỘC (giữ nguyên từ c2a)."""
    ten = str(ten or "").strip()
    if not _TEN_REMOTE.match(ten):
        return {"ok": False, "error": "tên remote chỉ gồm chữ, số, dấu . _ -"}
    loai = str(loai or "").strip()
    if not _LOAI_REMOTE.match(loai):
        return {"ok": False, "error": "loại remote không hợp lệ"}
    args = ["config", "create", ten, loai, "--obscure", "--"]
    for k, v in (tham_so or {}).items():
        k = str(k).strip()
        if not _KHOA_THAM_SO_REMOTE.match(k):
            return {"ok": False, "error": f"tên tham số remote không hợp lệ: {k[:60]}"}
        if k and str(v).strip():
            args += [k, str(v)]
    ok, _, err = _chay(args, timeout=HET_GIO_TRUYEN)
    return {"ok": ok, "error": err[:200], "remotes": remotes()}


def xoa_remote(ten: str) -> dict:
    if not _TEN_REMOTE.match(str(ten or "")):
        return {"ok": False, "error": "tên remote không hợp lệ"}
    ok, _, err = _chay(["config", "delete", str(ten)])
    return {"ok": ok, "error": err[:200], "remotes": remotes()}


def kiem_tra(ten: str) -> dict:
    """Thử gọi remote một lần. Backend không hỗ trợ `about` → thử lsd."""
    if not _TEN_REMOTE.match(str(ten or "")):
        return {"ok": False, "error": "tên remote không hợp lệ"}
    ok, out, err = _chay(["about", f"{ten}:", "--json"], timeout=HET_GIO_TRUYEN)
    if ok:
        try:
            return {"ok": True, "dung_luong": json.loads(out or "{}")}
        except json.JSONDecodeError:
            return {"ok": True, "dung_luong": {}}
    ok2, _, err2 = _chay(["lsd", f"{ten}:", "--max-depth", "1"], timeout=HET_GIO_TRUYEN)
    if ok2:
        return {"ok": True, "dung_luong": {}, "ghi_chu": "remote không báo dung lượng"}
    return {"ok": False, "error": (err or err2)[:200]}


# ── Đọc / tải / gửi ─────────────────────────────────────────────────────────

def liet_ke(duong_dan: str, *, max_muc: int = MAX_MUC) -> dict:
    try:
        dd = _kiem_remote(duong_dan)
    except LoiYeuCau as e:
        return {"ok": False, "error": e.loi}
    ok, out, err = _chay(["lsjson", dd, "--max-depth", "1"], timeout=HET_GIO_TRUYEN)
    if not ok:
        return {"ok": False, "error": err[:200]}
    try:
        muc = json.loads(out or "[]")
    except json.JSONDecodeError:
        return {"ok": False, "error": "rclone trả dữ liệu không đọc được"}
    ra = [{"ten": m.get("Name"), "la_thu_muc": bool(m.get("IsDir")),
           "co": int(m.get("Size") or 0), "sua_luc": m.get("ModTime") or ""}
          for m in muc[:max_muc]]
    return {"ok": True, "muc": ra, "tong": len(muc), "bi_cat": len(muc) > max_muc}


def _tep_tam(id_tep: str, thu_muc_goc: Path) -> Path:
    if not MAU_ID_TEP.fullmatch(id_tep or ""):
        raise LoiYeuCau(404, "khong_thay_tep")
    p = (thu_muc_goc / f"tep-{id_tep}.bin").resolve()
    if p.parent != thu_muc_goc.resolve():
        raise LoiYeuCau(404, "khong_thay_tep")
    if not p.is_file():
        raise LoiYeuCau(404, "khong_thay_tep")
    return p


def tai_ve(duong_dan: str) -> dict:
    """Tải file từ đám mây về thư mục làm việc; trả id để lấy qua /tep-xuong.

    Trả id thay vì nội dung: giữ phản hồi /lenh nhỏ, module lấy byte qua đường
    riêng (tránh JSON khổng lồ).
    """
    try:
        dd = _kiem_remote(duong_dan)
        ten = Path(dd.split(":", 1)[1] or "").name
        if not ten:
            return {"ok": False, "error": "không suy được tên file để lưu"}
        goc = thu_muc_lam_viec()
        dich = _duong_dan_cuc_bo(ten, goc)
    except LoiYeuCau as e:
        return {"ok": False, "error": e.loi}
    ok, _, err = _chay(["copyto", dd, str(dich)], timeout=HET_GIO_TRUYEN)
    if not ok:
        return {"ok": False, "error": err[:200]}
    id_tep = secrets.token_hex(8)
    dich_moi = goc / f"tep-{id_tep}.bin"
    dich.rename(dich_moi)
    try:
        os.chmod(dich_moi, 0o600)
    except OSError:
        pass
    return {"ok": True, "id_tep": id_tep, "ten": ten,
            "co": dich_moi.stat().st_size}


def gui_len(id_tep: str, thu_muc_dam_may: str, ten: str = "") -> dict:
    """Gửi tệp đã tải lên (qua /tep-len) lên đám mây."""
    try:
        goc = thu_muc_lam_viec()
        nguon = _tep_tam(id_tep, goc)
        dich_goc = _kiem_remote(thu_muc_dam_may)
    except LoiYeuCau as e:
        return {"ok": False, "error": e.loi}
    ten = str(ten or "").strip()
    if not ten or "/" in ten or "\\" in ten or ".." in ten:
        return {"ok": False, "error": "ten_tep_khong_hop_le"}
    dich = dich_goc.rstrip("/") + "/" + ten
    ok, _, err = _chay(["copyto", str(nguon), dich], timeout=HET_GIO_TRUYEN)
    return ({"ok": True, "duong_dan": dich}
            if ok else {"ok": False, "error": err[:200]})


def doc_chu(duong_dan: str, *, max_bytes: int = TRAN_DOC_BYTES) -> dict:
    """Đọc thẳng nội dung một file text trên đám mây, không lưu xuống đĩa."""
    try:
        dd = _kiem_remote(duong_dan)
    except LoiYeuCau as e:
        return {"ok": False, "error": e.loi}
    ok, out, err = _chay(["cat", dd, "--count", str(int(max_bytes))],
                         timeout=HET_GIO_TRUYEN)
    if not ok:
        return {"ok": False, "error": err[:200]}
    return {"ok": True, "noi_dung": out,
            "bi_cat": len(out.encode("utf-8", "ignore")) >= max_bytes}


def xoa(duong_dan: str) -> dict:
    """Xoá một file trên đám mây. Chỉ file."""
    try:
        dd = _kiem_remote(duong_dan)
    except LoiYeuCau as e:
        return {"ok": False, "error": e.loi}
    ok, _, err = _chay(["deletefile", dd], timeout=HET_GIO_TRUYEN)
    return {"ok": ok, "error": err[:200]}


def chuyen_tep(nguon: str, dich: str) -> dict:
    """Chuyển tệp; không ghi đè tệp khác ở đích (--immutable)."""
    try:
        src, dst = _kiem_remote(nguon), _kiem_remote(dich)
        if src.split(":", 1)[0] != dst.split(":", 1)[0]:
            return {"ok": False, "error": "chỉ chuyển trong cùng kho"}
    except LoiYeuCau as e:
        return {"ok": False, "error": e.loi}
    ok, _, err = _chay(["moveto", src, dst, "--immutable"], timeout=HET_GIO_TRUYEN)
    return {"ok": ok, "duong_dan": dst if ok else src, "error": err[:200]}


# ── Định tuyến op ───────────────────────────────────────────────────────────

def xu_ly_lenh(lenh, tham_so: dict) -> dict:
    """lenh phải trong tập đóng — chặn TRƯỚC mọi xử lý khác."""
    if not isinstance(lenh, str) or lenh not in LENH_CHO_PHEP:
        raise LoiYeuCau(400, "lenh_khong_ho_tro")
    ts = tham_so if isinstance(tham_so, dict) else {}
    if lenh == "trang_thai":
        return san_sang()
    if lenh == "remotes":
        return {"ok": True, "remotes": remotes()}
    if lenh == "config_xem":
        return {"ok": True, "noi_dung": config_da_che()}
    if lenh == "config_ghi":
        return dat_config(str(ts.get("noi_dung") or ""))
    if lenh == "them_remote":
        return tao_remote(str(ts.get("ten") or ""), str(ts.get("loai") or ""),
                          dict(ts.get("tham_so") or {}))
    if lenh == "xoa_remote":
        return xoa_remote(str(ts.get("ten") or ""))
    if lenh == "kiem_tra":
        return kiem_tra(str(ts.get("ten") or ""))
    if lenh == "drive_start":
        return drive_duong_dan_dang_nhap(str(ts.get("client_id") or ""),
                                         str(ts.get("scope") or "drive"))
    if lenh == "drive_exchange":
        return drive_doi_ma_lay_token(
            str(ts.get("ten") or ""), str(ts.get("client_id") or ""),
            str(ts.get("client_secret") or ""),
            str(ts.get("redirect_url") or ""),
            scope=str(ts.get("scope") or "drive"),
            root_folder_id=str(ts.get("root_folder_id") or ""))
    if lenh == "luu_khoa_json":
        return luu_khoa_json(str(ts.get("ten") or ""), str(ts.get("noi_dung") or ""))
    if lenh == "liet_ke":
        return liet_ke(str(ts.get("duong_dan") or ""))
    if lenh == "doc_chu":
        return doc_chu(str(ts.get("duong_dan") or ""))
    if lenh == "tai_ve":
        return tai_ve(str(ts.get("duong_dan") or ""))
    if lenh == "gui_len":
        return gui_len(str(ts.get("id_tep") or ""), str(ts.get("thu_muc") or ""),
                       str(ts.get("ten") or ""))
    if lenh == "xoa":
        return xoa(str(ts.get("duong_dan") or ""))
    if lenh == "chuyen_tep":
        return chuyen_tep(str(ts.get("nguon") or ""), str(ts.get("dich") or ""))
    raise LoiYeuCau(400, "lenh_khong_ho_tro")


# ── Máy chủ HTTP ────────────────────────────────────────────────────────────

class XuLy(BaseHTTPRequestHandler):
    """Kiểm Bearer TRƯỚC khi đọc thân (trừ /suc-khoe)."""

    khoa_dich_vu: str = ""
    server_version = "RcloneBridge/1.0"

    def log_message(self, fmt, *args):
        log.info("%s %s", self.address_string(), fmt % args)

    def _tra_json(self, ma: int, obj: dict):
        du_lieu = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(ma)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(du_lieu)))
        self.end_headers()
        self.wfile.write(du_lieu)

    def _xac_thuc(self) -> bool:
        nhan = self.headers.get("Authorization", "")
        if not nhan.startswith("Bearer "):
            return False
        token = nhan[7:].strip()
        return hmac.compare_digest(token.encode("utf-8", "surrogateescape"),
                                   self.khoa_dich_vu.encode("utf-8"))

    def _doc_than(self, tran: int) -> bytes:
        try:
            dai = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise LoiYeuCau(400, "thieu_content_length")
        if dai < 0 or dai > tran:
            raise LoiYeuCau(413, "than_qua_lon")
        return self.rfile.read(dai) if dai else b""

    def do_GET(self):
        from urllib.parse import urlsplit as _us
        duong = _us(self.path).path
        if duong == "/suc-khoe":
            self._tra_json(200, {"ok": True})
            return
        if not self._xac_thuc():
            self._tra_json(401, {"loi": "khong_xac_thuc"})
            return
        if duong.startswith("/tep-xuong/"):
            try:
                p = _tep_tam(duong[len("/tep-xuong/"):], thu_muc_lam_viec())
                du_lieu = p.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(du_lieu)))
                self.end_headers()
                self.wfile.write(du_lieu)
            except LoiYeuCau as e:
                self._tra_json(e.ma, {"loi": e.loi})
            return
        self._tra_json(404, {"loi": "khong_co_duong"})

    def do_POST(self):
        from urllib.parse import urlsplit as _us
        duong = _us(self.path).path
        if not self._xac_thuc():
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
                kq = xu_ly_lenh(yeu_cau.get("lenh"), yeu_cau.get("tham_so", {}))
                # config_xem là dữ liệu nhạy cảm đã che — ghi log ở mức debug, không info.
                if yeu_cau.get("lenh") != "config_xem":
                    log.info("lenh=%s ok=%s", yeu_cau.get("lenh"), kq.get("ok"))
                self._tra_json(200, kq)
            elif duong == "/tep-len":
                than = self._doc_than(_CAU_HINH["tran_tai_len"] + 1024 * 1024)
                id_tep = secrets.token_hex(8)
                dich = thu_muc_lam_viec() / f"tep-{id_tep}.bin"
                dich.write_bytes(than)
                try:
                    os.chmod(dich, 0o600)
                except OSError:
                    pass
                log.info("tep-len id=%s co_byte=%d", id_tep, len(than))
                self._tra_json(200, {"ok": True, "id_tep": id_tep, "co": len(than)})
            else:
                self._tra_json(404, {"loi": "khong_co_duong"})
        except LoiYeuCau as e:
            self._tra_json(e.ma, {"loi": e.loi})
        except (ConnectionError, BrokenPipeError):
            pass


def tao_may_chu() -> tuple[ThreadingHTTPServer, dict]:
    """Dựng máy chủ (chưa chạy): kiểm khoá, nhị phân, thư mục."""
    khoa = kiem_tra_khoa()
    cau_hinh = lay_cau_hinh()
    cau_hinh["bin"] = duong_nhi_phan(cau_hinh)
    thu_muc_lam_viec()  # tạo + khoá quyền
    conf_path()         # tạo tệp 0600
    # Kiểm tra khởi động: rclone version (không mạng).
    proc = subprocess.run(
        [cau_hinh["bin"], "version"],
        capture_output=True, timeout=15,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
             "HOME": os.environ.get("HOME", "/tmp")},
    )
    log.info("rclone version: %s",
             (proc.stdout or b"").decode("utf-8", "replace")[:80].strip())
    global _CAU_HINH
    _CAU_HINH = cau_hinh
    XuLy.khoa_dich_vu = khoa
    srv = ThreadingHTTPServer((cau_hinh["host"], cau_hinh["port"]), XuLy)
    srv.daemon_threads = True
    return srv, cau_hinh


def main() -> None:
    srv, cau_hinh = tao_may_chu()
    log.info("rclone nghe %s:%d, thư mục %s",
             cau_hinh["host"], srv.server_address[1], cau_hinh["thu_muc_goc"])
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
