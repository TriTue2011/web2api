"""Sao lưu và khôi phục cấu hình web2api (xuất/nhập một tệp, giữ khoá khách).

Quy tắc thiết kế:
- Bài học đắt từ c2a: khôi phục SINH khoá khách mới rồi vứt đi → mọi ứng dụng đang dùng
  khoá cũ chết. Ở đây khôi phục phải GIỮ NGUYÊN các bản ghi khoá khách (đã là băm) để khoá cũ
  vẫn dùng được. Khoá quản trị KHÔNG nằm trong bản sao lưu (đặt lại bằng cách riêng).
- Phạm vi sao lưu:
  * Cấu hình công khai: modules.json, tu_xa.json, nguon_git.json, dia_chi.json, model.json, combo.json.
  * Kho khoá khách: khoa_khach.json (chỉ chứa băm).
  * CHỈ khi có mật khẩu: thư mục bi_mat/ (khoá API của module).
  * Phiên tài khoản (bi_mat/phien/): chỉ khi quản trị đặt rõ kem_phien=true và có mật khẩu.
  * Mã module cài từ git (modules/<id>/): KHÔNG đưa vào: chỉ ghi id + sha_commit trong manifest.
  * Không đưa nhật ký, cache, admin_key, tệp tạm.
- Định dạng: đầu tệp `W2ABACKUP` + phiên bản + kiểu, rồi tar KHÔNG NÉN (manifest.json + các tệp).
  Có mật khẩu: AES-256-GCM, khoá từ scrypt (muối 16 byte, nonce 12 byte, đầu tệp làm Associated Data).
- Khôi phục = dữ liệu NGƯỜI TẢI LÊN điều khiển, nên:
  * Trần THẬT nhỏ (cấu hình chỉ vài KiB): tổng ≤ 2 MiB, mỗi tệp ≤ 1 MiB, ≤ 256 thành viên — kiểm `m.size` ngay
    khi thấy header, đọc tối đa `m.size + 1` byte. Chỉ nhận tar không nén (ứng dụng chỉ tạo loại đó): gzip/bz2/xz
    là đường bom nén (120 KiB → 120 MiB RAM). Tạo bản sao lưu cũng chịu CÙNG trần — ứng dụng không tạo ra bản
    mà chính nó sẽ từ chối.
  * Tham số scrypt phải đúng bộ ứng dụng dùng (`SCRYPT_CHO_PHEP`), lạ thì từ chối TRƯỚC khi băm: header giả
    n=2^20 từng chiếm 1 GiB RAM và 4 giây trước khi biết mật khẩu đúng hay sai.
  * Mỗi tệp đi qua bộ kiểm hình dạng + giá trị của CHÍNH module sở hữu (`_BO_KIEM`); tệp không có bộ kiểm thì
    không khôi phục được. Rồi đối chiếu với trạng thái đang có (`_doi_chieu`): không nới mặc định an toàn (cờ
    nguồn git), không đổi nguồn mã, không để khoá đang có trên máy đi theo cấu hình công khai khác (base_url).
  * `kiem` trả ĐÚNG kế hoạch mà `ap_dung` sẽ ghi (cùng một hàm lập), kèm khác biệt nhạy cảm và sha256 của tệp;
    `ap_dung` đòi sha256 đó (người duyệt chấp nhận đúng tệp mình đã xem).
  * Ghi: lưu bản trước sang <DATA>/.truoc-khoi-phuc-<thời điểm>/ (giữ tối đa 3 bản, CHỨA KHOÁ THẬT dạng rõ, quyền
    0700), ghi từng tệp nguyên tử; lỗi giữa chừng hoặc kiểm chứng sau ghi (tra lại từng khoá khách) không đạt →
    phục hồi mọi tệp đã ghi từ bản trước rồi báo lỗi. Một lượt khôi phục tại một thời điểm.
  * Việc nặng (KDF, giải mã, đọc tar, ghi đĩa) chạy trong luồng phụ, không chặn vòng sự kiện.
  * Mật khẩu không bao giờ lọt vào log/nhật ký/phản hồi/ngoại lệ; tên do người tải lên đặt nêu lại qua `nhac_lai`.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import os
import posixpath
import re
import secrets
import shutil
import struct
import tarfile
import tempfile
import threading
import time
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from app import combo, dia_chi, kho, khoa_khach, nguon_git, nhat_ky, phien
from app.kham_pha import ID_HOP_LE
from app.loi_openai import LoiOpenAI, nhac_lai
from app.so_module import LoiSo, kiem_ban_ghi_tu_xa

log = logging.getLogger("web2api.sao_luu")

MAGIC = b"W2ABACKUP"
PHIEN_BAN = 1
KIEU_THO = 0
KIEU_MA_HOA = 1

# Tham số scrypt của ứng dụng (N=2^15, r=8, p=1 → 32 MiB mỗi lần băm). Thấp hơn khuyến nghị OWASP (2^17) nhưng
# KHÔNG đổi: đổi làm bản sao lưu đã tạo không đọc được, và 2^17 = 128 MiB mỗi lần băm trong container 256 MB.
SCRYPT_N = 32768
SCRYPT_R = 8
SCRYPT_P = 1
#: Bộ tham số được ĐỌC. Header do người tải lên viết → mọi bộ khác bị từ chối trước khi băm.
SCRYPT_CHO_PHEP = frozenset({(SCRYPT_N, SCRYPT_R, SCRYPT_P)})
MUOI_LEN = 16
NONCE_LEN = 12

HEADER_THO_LEN = 11  # 9 magic + 1 version + 1 kieu
HEADER_MA_HOA_LEN = 51  # 9 magic + 1 version + 1 kieu + 12 (N,r,p) + 16 muoi + 12 nonce

#: Trần theo BYTE nhưng chọn theo RAM SAU KHI ĐỌC JSON: JSON đối kháng `[{},{},…]` nở ~26 lần khi thành đối tượng
#: Python (đo: 16 MiB → RSS +417 MiB — container 256 MB chết). 2 MiB tổng → tệ nhất ~+55 MiB. Cấu hình thật chỉ vài
#: KiB (1000 khoá khách ≈ 250 KiB); phiên tài khoản vài chục KiB mỗi tệp.
TRAN_TONG = 2 * 1024 * 1024
TRAN_MOI_TEP = 1024 * 1024
#: Mỗi thành viên chỉ tốn một header 512 byte (trần BYTE mới là chặn bộ nhớ); 256 đủ cho vài module × vài chục phiên.
TRAN_SO_THANH_VIEN = 256
#: Kích thước tệp tải lên tối đa: dữ liệu + header tar (kể cả header PAX cho tên dài) + đầu tệp/tag GCM.
TRAN_TEP_TAI_LEN = TRAN_TONG + (TRAN_SO_THANH_VIEN + 2) * 4096 + HEADER_MA_HOA_LEN + 16
TOI_DA_BAN_TRUOC = 3
TIEN_TO_BAN_TRUOC = ".truoc-khoi-phuc-"

_SHA_COMMIT = re.compile(r"[0-9a-f]{40}")
_SHA256 = re.compile(r"[0-9a-f]{64}")

#: Một lượt khôi phục (kiểm hoặc áp dụng) tại một thời điểm. Khoá LUỒNG (không gắn vòng sự kiện nào) và lấy không
#: chờ: lượt thứ hai trả 429 ngay thay vì xếp hàng giữ tệp tải lên trong RAM.
_KHOA_KHOI_PHUC = threading.Lock()


def _sai(thong_diep: str) -> LoiOpenAI:
    return LoiOpenAI(400, thong_diep, code="sao_luu_khong_hop_le")


# ───────────── bộ kiểm của module sở hữu, theo từng tệp ─────────────

def _kiem_modules(du_lieu: Any) -> dict:
    return {"modules": kho.kiem_tep_modules(du_lieu)}


def _kiem_tu_xa(du_lieu: Any) -> dict:
    # Hình dạng ngoài; từng bản ghi qua kiem_ban_ghi_tu_xa (async — base_url phải qua kiem_url_lan) ở _kiem_tu_xa_url.
    ds = du_lieu.get("modules") if isinstance(du_lieu, dict) else None
    if not isinstance(ds, list) or not all(isinstance(x, dict) for x in ds):
        raise ValueError('cần dạng {"modules": [{...}]}')
    return {"modules": [dict(x) for x in ds]}


def _kiem_nguon_git(du_lieu: Any) -> dict:
    return nguon_git.kiem_cau_hinh(du_lieu, {})


def _kiem_dia_chi(du_lieu: Any) -> dict:
    url = du_lieu.get("url") if isinstance(du_lieu, dict) else None
    if not isinstance(url, str):
        raise ValueError('cần dạng {"url": "<chuỗi>"}')
    try:
        return {"url": dia_chi.kiem_tra_va_chuan_hoa_url(url)}
    except LoiOpenAI as e:
        raise ValueError(e.than["error"]["message"]) from None


def _kiem_model(du_lieu: Any) -> dict:
    return kho.kiem_cai_dat_model(du_lieu)


def _kiem_combo(du_lieu: Any) -> dict:
    if not isinstance(du_lieu, dict):
        raise ValueError("cần dạng đối tượng {<tên combo>: {...}}")
    try:
        return {ten: combo.kiem_hinh_dang(ten, muc) for ten, muc in du_lieu.items()}
    except LoiOpenAI as e:
        raise ValueError(e.than["error"]["message"]) from None


def _kiem_khoa_khach(du_lieu: Any) -> dict:
    return {"khoa": khoa_khach.kiem_tep(du_lieu)}


def _kiem_doi_tuong(du_lieu: Any) -> dict:
    # bi_mat/<id>.json (kho.doc_bi_mat) và bi_mat/phien/<m>/<tk>.json (phien.lay) chỉ dùng được khi là đối tượng.
    if not isinstance(du_lieu, dict):
        raise ValueError("phải là đối tượng JSON")
    return du_lieu


#: Tệp công khai → bộ kiểm của module sở hữu. Tệp không có ở đây (và không thuộc bi_mat/) thì KHÔNG khôi phục được.
_BO_KIEM: dict[str, Callable[[Any], Any]] = {
    "modules.json": _kiem_modules,          # kho.kiem_tep_modules (+ manifest module ở _doi_chieu)
    "tu_xa.json": _kiem_tu_xa,              # so_module.kiem_ban_ghi_tu_xa → tu_xa.kiem_url_lan
    "nguon_git.json": _kiem_nguon_git,      # nguon_git.kiem_cau_hinh (kiem_repo, _duong_an_toan)
    "dia_chi.json": _kiem_dia_chi,          # dia_chi.kiem_tra_va_chuan_hoa_url
    "model.json": _kiem_model,              # kho.kiem_cai_dat_model
    "combo.json": _kiem_combo,              # combo.kiem_hinh_dang (+ trùng tiền tố ở _doi_chieu)
    "khoa_khach.json": _kiem_khoa_khach,    # khoa_khach.kiem_tep
}
#: Giữ tên cũ cho người đọc mã: tập tệp công khai = khoá của bảng bộ kiểm (trừ khoa_khach.json, loại riêng).
TEP_CONG_KHAI_CHO_PHEP = frozenset(_BO_KIEM) - {"khoa_khach.json"}


def _bo_kiem_cua(duong_rel: str) -> Callable[[Any], Any] | None:
    """Bộ kiểm cho một đường trong bản sao lưu; None = ngoài phạm vi khôi phục."""
    if duong_rel in _BO_KIEM:
        return _BO_KIEM[duong_rel]
    parts = duong_rel.split("/")
    if len(parts) == 2 and parts[0] == "bi_mat" and parts[1].endswith(".json"):
        return _kiem_doi_tuong if kho.ten_bi_mat_hop_le(parts[1][:-5]) else None
    if len(parts) == 4 and parts[:2] == ["bi_mat", "phien"] and parts[3].endswith(".json"):
        try:
            phien.kiem_tra_id(parts[2], parts[3][:-5])
        except ValueError:
            return None
        return _kiem_doi_tuong
    return None


def la_tep_cho_phep(duong_rel: str) -> bool:
    """Đường tương đối có thuộc phạm vi bản sao lưu không (manifest.json hoặc tệp có bộ kiểm của module sở hữu)."""
    if not isinstance(duong_rel, str) or not duong_rel:
        return False
    if any(c in duong_rel for c in ("\0", "\r", "\n", "\\")) or posixpath.normpath(duong_rel) != duong_rel:
        return False
    return duong_rel == "manifest.json" or _bo_kiem_cua(duong_rel) is not None


def _quyen_tep(rel: str) -> int:
    # Cùng quyền với đường ghi thường: bí mật và khoá khách 0600 (kho.ghi_bi_mat, khoa_khach), combo 0600 (kho).
    return 0o600 if rel.startswith("bi_mat/") or rel in ("khoa_khach.json", "combo.json") else 0o644


# ───────────── tạo bản sao lưu ─────────────

def tao_ban_sao_luu(
    mat_khau: str | None = None,
    kem_phien: bool = False,
) -> tuple[bytes, str]:
    """Tạo dữ liệu nhị phân của bản sao lưu và tên tệp đính kèm. Đồng bộ (scrypt): gọi qua asyncio.to_thread."""
    co_mat_khau = False
    if mat_khau is not None:
        if not isinstance(mat_khau, str):
            nhat_ky.ghi("quan_tri", "canh_bao", "Tạo bản sao lưu thất bại: mật khẩu không phải chuỗi")
            raise LoiOpenAI(400, "Mật khẩu phải là chuỗi hoặc null.", code="mat_khau_khong_hop_le")
        mk_s = mat_khau.strip()
        if mk_s:
            if len(mk_s) < 12:
                nhat_ky.ghi("quan_tri", "canh_bao", "Tạo bản sao lưu thất bại: mật khẩu quá ngắn (< 12 ký tự)")
                raise LoiOpenAI(400, "Mật khẩu sao lưu phải có ít nhất 12 ký tự.", code="mat_khau_ngan")
            co_mat_khau = True
            mat_khau = mk_s
        else:
            mat_khau = None
    if not isinstance(kem_phien, bool):
        raise LoiOpenAI(400, "«kem_phien» phải là true/false.", code="invalid_request")

    goc = kho.thu_muc_du_lieu()
    tep_can_sao: list[tuple[str, Path]] = []
    for t in sorted(_BO_KIEM):
        p = goc / t
        if p.is_file():
            tep_can_sao.append((t, p))

    if co_mat_khau:
        tm_bimat = goc / "bi_mat"
        if tm_bimat.is_dir():
            for p in sorted(tm_bimat.iterdir()):
                rel = f"bi_mat/{p.name}"
                if p.is_file() and la_tep_cho_phep(rel):
                    tep_can_sao.append((rel, p))
        if kem_phien:
            tm_phien = goc / "bi_mat" / "phien"
            if tm_phien.is_dir():
                for p in sorted(tm_phien.rglob("*.json")):
                    rel = p.relative_to(goc).as_posix()
                    if p.is_file() and la_tep_cho_phep(rel):
                        tep_can_sao.append((rel, p))

    modules_git = []
    tm_modules = kho.thu_muc_modules()
    if tm_modules.is_dir():
        for d in sorted(tm_modules.iterdir()):
            p_nguon = d / ".nguon.json"
            if d.is_dir() and ID_HOP_LE.match(d.name) and p_nguon.is_file():
                try:
                    sha = json.loads(p_nguon.read_text(encoding="utf-8")).get("sha_commit")
                except (OSError, ValueError, AttributeError):
                    continue
                modules_git.append({"id": d.name,
                                    "sha_commit": sha if isinstance(sha, str) and _SHA_COMMIT.fullmatch(sha) else None})

    if len(tep_can_sao) + 1 > TRAN_SO_THANH_VIEN:
        raise LoiOpenAI(400, f"Quá {TRAN_SO_THANH_VIEN} tệp — vượt trần khôi phục.", code="sao_luu_qua_lon")

    noi_dung_cac_tep: dict[str, bytes] = {}
    danh_sach_tep: list[dict[str, Any]] = []
    tong_kich_thuoc = 0
    for rel, p in tep_can_sao:
        try:
            data = p.read_bytes()
        except OSError as e:
            nhat_ky.ghi("quan_tri", "canh_bao", f"Không đọc được tệp {rel} khi sao lưu: {type(e).__name__}")
            continue
        tong_kich_thuoc += len(data)
        if len(data) > TRAN_MOI_TEP or tong_kich_thuoc > TRAN_TONG:
            nhat_ky.ghi("quan_tri", "canh_bao", "Tạo bản sao lưu thất bại: dữ liệu vượt trần khôi phục")
            raise LoiOpenAI(400, f"Tệp {rel} hoặc tổng dữ liệu vượt trần khôi phục (mỗi tệp ≤ "
                                 f"{TRAN_MOI_TEP // 2**20} MiB, tổng ≤ {TRAN_TONG // 2**20} MiB).",
                            code="sao_luu_qua_lon")
        danh_sach_tep.append({"duong": rel, "sha256": hashlib.sha256(data).hexdigest(), "kich_thuoc": len(data)})
        noi_dung_cac_tep[rel] = data

    manifest = {
        "phien_ban": PHIEN_BAN,
        "tao_luc": kho.bay_gio_iso(),
        "tep": danh_sach_tep,
        "modules_git": modules_git,
        "co_bi_mat": co_mat_khau,
        "co_phien": bool(co_mat_khau and kem_phien),
        "ghi_chu": None if co_mat_khau else "đã bỏ bí mật vì không có mật khẩu",
    }
    manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")

    bio = io.BytesIO()
    with tarfile.open(fileobj=bio, mode="w") as tf:
        for rel, data in [("manifest.json", manifest_bytes)] + list(noi_dung_cac_tep.items()):
            ti = tarfile.TarInfo(name=rel)
            ti.size = len(data)
            ti.mtime = int(time.time())
            ti.type = tarfile.REGTYPE
            ti.mode = 0o600 if rel.startswith("bi_mat/") or rel == "khoa_khach.json" else 0o644
            tf.addfile(ti, io.BytesIO(data))
    tar_bytes = bio.getvalue()

    if co_mat_khau and mat_khau:
        muoi = os.urandom(MUOI_LEN)
        nonce = os.urandom(NONCE_LEN)
        header = (MAGIC + bytes([PHIEN_BAN, KIEU_MA_HOA]) + struct.pack(">III", SCRYPT_N, SCRYPT_R, SCRYPT_P)
                  + muoi + nonce)
        khoa_ma = Scrypt(salt=muoi, length=32, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P).derive(mat_khau.encode("utf-8"))
        du_lieu_cuoi = header + AESGCM(khoa_ma).encrypt(nonce, tar_bytes, associated_data=header)
    else:
        du_lieu_cuoi = MAGIC + bytes([PHIEN_BAN, KIEU_THO]) + tar_bytes
    if len(du_lieu_cuoi) > TRAN_TEP_TAI_LEN:
        raise LoiOpenAI(400, "Bản sao lưu vượt trần khôi phục.", code="sao_luu_qua_lon")

    ten_tep = f"web2api-sao-luu-{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.w2a"
    nhat_ky.ghi("quan_tri", "thong_tin", "Tạo bản sao lưu cấu hình thành công", co_mat_khau=co_mat_khau,
                kem_phien=bool(co_mat_khau and kem_phien), so_tep=len(danh_sach_tep))
    return du_lieu_cuoi, ten_tep


# ───────────── đọc bản sao lưu (định dạng + toàn vẹn) ─────────────

def kiem_va_giai_ma(du_lieu: bytes, mat_khau: str | None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Giải mã (nếu mã hoá) và kiểm định dạng + toàn vẹn. Đồng bộ (scrypt, tar): gọi qua asyncio.to_thread.

    Trả (manifest, {đường: đối tượng JSON đã đọc}); sai → LoiOpenAI(400, code="sao_luu_khong_hop_le").
    Hình dạng/giá trị từng tệp do bộ kiểm của module sở hữu quyết (`_kiem_hinh_dang`), không phải ở đây.
    """
    if not isinstance(du_lieu, bytes) or len(du_lieu) < HEADER_THO_LEN or len(du_lieu) > TRAN_TEP_TAI_LEN:
        raise _sai("Bản sao lưu không hợp lệ.")
    if du_lieu[:9] != MAGIC or du_lieu[9] != PHIEN_BAN:
        raise _sai("Bản sao lưu không hợp lệ.")

    kieu = du_lieu[10]
    if kieu == KIEU_MA_HOA:
        if len(du_lieu) < HEADER_MA_HOA_LEN + 16 or not mat_khau or not isinstance(mat_khau, str):
            raise _sai("Bản sao lưu không hợp lệ.")
        header = du_lieu[:HEADER_MA_HOA_LEN]
        tham_so = struct.unpack(">III", du_lieu[11:23])
        # Header do người tải lên viết: tham số lạ → từ chối TRƯỚC khi băm (n=2^20 = 1 GiB RAM).
        if tham_so not in SCRYPT_CHO_PHEP:
            raise _sai("Bản sao lưu không hợp lệ.")
        n, r, p = tham_so
        try:
            khoa_ma = Scrypt(salt=du_lieu[23:39], length=32, n=n, r=r, p=p).derive(mat_khau.encode("utf-8"))
            tar_bytes = AESGCM(khoa_ma).decrypt(du_lieu[39:51], du_lieu[51:], associated_data=header)
        except Exception:
            # Sai mật khẩu hoặc tag/dữ liệu bị sửa → lỗi chung, không phân biệt
            raise _sai("Bản sao lưu không hợp lệ.") from None
    elif kieu == KIEU_THO:
        tar_bytes = du_lieu[HEADER_THO_LEN:]
    else:
        raise _sai("Bản sao lưu không hợp lệ.")

    tho: dict[str, bytes] = {}
    try:
        # "r:" = tar KHÔNG nén: dữ liệu nén (bom nén) không phải header tar hợp lệ → ReadError.
        tf = tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:")
    except (tarfile.TarError, OSError, EOFError):
        raise _sai("Bản sao lưu không hợp lệ (chỉ nhận tar không nén do web2api tạo).") from None
    tong = 0
    with tf:
        while True:  # duyệt THEO LUỒNG: kiểm từng header ngay khi thấy, trước khi đọc nội dung hay header sau
            try:
                m = tf.next()
            except (tarfile.TarError, OSError, EOFError):
                raise _sai("Bản sao lưu không hợp lệ (tar hỏng).") from None
            if m is None:
                break
            if len(tho) >= TRAN_SO_THANH_VIEN:
                raise _sai(f"Bản sao lưu không hợp lệ (quá {TRAN_SO_THANH_VIEN} tệp).")
            if not m.isreg():
                raise _sai(f"Bản sao lưu chứa thành phần không phải tệp thường: {nhac_lai(m.name)}")
            if not la_tep_cho_phep(m.name):
                raise _sai(f"Bản sao lưu chứa tệp ngoài phạm vi cho phép: {nhac_lai(m.name)}")
            if m.name in tho:
                raise _sai(f"Bản sao lưu chứa tệp trùng tên: {nhac_lai(m.name)}")
            tong += m.size
            if m.size > TRAN_MOI_TEP or tong > TRAN_TONG:
                raise _sai("Bản sao lưu không hợp lệ (vượt trần giải nén: mỗi tệp ≤ "
                           f"{TRAN_MOI_TEP // 2**20} MiB, tổng ≤ {TRAN_TONG // 2**20} MiB).")
            f = tf.extractfile(m)
            data = f.read(m.size + 1) if f is not None else b""
            if len(data) != m.size:
                raise _sai(f"Bản sao lưu không hợp lệ (tệp {nhac_lai(m.name)} bị cắt cụt).")
            tho[m.name] = data

    try:
        manifest = json.loads(tho.pop("manifest.json"))
    except KeyError:
        raise _sai("Bản sao lưu không hợp lệ (thiếu manifest.json).") from None
    except ValueError:
        raise _sai("Bản sao lưu không hợp lệ (manifest.json hỏng).") from None
    if not isinstance(manifest, dict) or not isinstance(manifest.get("tep"), list):
        raise _sai("Bản sao lưu không hợp lệ (manifest.json sai hình dạng).")
    pb = manifest.get("phien_ban")
    if type(pb) is not int or pb != PHIEN_BAN:
        raise _sai("Bản sao lưu không hợp lệ (phiên bản manifest không được hỗ trợ).")
    mg = manifest.get("modules_git", [])
    if not isinstance(mg, list) or not all(
            isinstance(x, dict) and isinstance(x.get("id"), str) and ID_HOP_LE.match(x["id"])
            and (x.get("sha_commit") is None or (isinstance(x["sha_commit"], str)
                                                 and _SHA_COMMIT.fullmatch(x["sha_commit"]))) for x in mg):
        raise _sai("Bản sao lưu không hợp lệ (manifest.json: «modules_git» sai hình dạng).")

    da_khai: set[str] = set()
    for item in manifest["tep"]:
        if (not isinstance(item, dict) or not isinstance(item.get("duong"), str)
                or not isinstance(item.get("sha256"), str) or type(item.get("kich_thuoc")) is not int):
            raise _sai("Bản sao lưu không hợp lệ (manifest.json: mục «tep» sai hình dạng).")
        duong = item["duong"]
        if duong in da_khai or duong not in tho:
            raise _sai(f"Bản sao lưu không hợp lệ (manifest khai trùng hoặc thiếu tệp {nhac_lai(duong)}).")
        da_khai.add(duong)
        if len(tho[duong]) != item["kich_thuoc"] or hashlib.sha256(tho[duong]).hexdigest() != item["sha256"]:
            raise _sai(f"Bản sao lưu không hợp lệ (kích thước hoặc băm tệp {nhac_lai(duong)} sai).")
    thua = sorted(set(tho) - da_khai)
    if thua:
        raise _sai(f"Bản sao lưu không hợp lệ (tệp thừa không khai: {nhac_lai(thua[0])}).")

    noi_dung: dict[str, Any] = {}
    for k in list(tho):
        try:
            noi_dung[k] = json.loads(tho.pop(k))  # đọc MỘT lần (bytes → đối tượng), bỏ bản thô ngay
        except (ValueError, RecursionError):
            raise _sai(f"Bản sao lưu không hợp lệ ({nhac_lai(k)} không phải JSON hợp lệ).") from None
    return manifest, noi_dung


def _kiem_hinh_dang(noi_dung: dict[str, Any]) -> dict[str, Any]:
    """Mỗi tệp qua bộ kiểm của module sở hữu; trả bản đã chuẩn hoá — thứ SẼ được ghi (không ghi bytes thô)."""
    sach = {}
    for rel, du_lieu in noi_dung.items():
        bo_kiem = _bo_kiem_cua(rel)
        try:
            sach[rel] = bo_kiem(du_lieu)  # la_tep_cho_phep đã bảo đảm có bộ kiểm
        except ValueError as e:
            raise _sai(f"Bản sao lưu không hợp lệ ({rel}: {nhac_lai(str(e), 300)}).") from None
    return sach


# ───────────── đối chiếu với trạng thái đang có ─────────────

def _che_url(url: Any) -> str | None:
    """URL để hiện cho người duyệt: bỏ phần tài khoản/mật khẩu (userinfo) nếu có."""
    if not isinstance(url, str):
        return None
    try:
        p = urlsplit(url)
        netloc = p.netloc.rsplit("@", 1)[-1]
        return p._replace(netloc=netloc).geturl()
    except ValueError:
        return None


def _doc_an_toan(ham: Callable[[], Any], mac_dinh: Any) -> Any:
    try:
        return ham()
    except kho.KhoHong:
        return mac_dinh


async def _lap_ke_hoach(du_lieu: bytes, mat_khau: str | None, so: Any, ng: Any,
                        sha256_can: str | None = None) -> dict[str, Any]:
    """Kế hoạch khôi phục — MỘT hàm cho cả `kiem` (trả cho người duyệt) lẫn `ap_dung` (ghi đúng thứ này)."""
    sha = await asyncio.to_thread(lambda: hashlib.sha256(du_lieu).hexdigest())
    if sha256_can is not None and sha256_can != sha:
        raise LoiOpenAI(409, "Tệp áp dụng không phải tệp đã «Kiểm tra» (sha256 lệch) — kiểm tra lại đúng tệp này.",
                        code="chua_kiem_dung_tep")
    manifest, noi_dung = await asyncio.to_thread(kiem_va_giai_ma, du_lieu, mat_khau)
    sach = await asyncio.to_thread(_kiem_hinh_dang, noi_dung)
    del noi_dung

    # Bản ghi module từ xa: CÙNG bộ kiểm của đường đăng ký (chặn SSRF bằng kiem_url_lan).
    if "tu_xa.json" in sach:
        ds, id_thay, tt_thay = [], set(), set()
        for i, rec in enumerate(sach["tu_xa.json"]["modules"]):
            try:
                chuan = await kiem_ban_ghi_tu_xa(rec)
            except LoiSo as e:
                raise _sai(f"Bản sao lưu không hợp lệ (tu_xa.json: modules[{i}]: {nhac_lai(str(e), 300)}).") from None
            if chuan["id"] in id_thay or chuan["tien_to"] in tt_thay:
                raise _sai(f"Bản sao lưu không hợp lệ (tu_xa.json: modules[{i}]: id hoặc tiền tố trùng mục trước).")
            id_thay.add(chuan["id"])
            tt_thay.add(chuan["tien_to"])
            cai_luc = rec.get("cai_luc")
            ds.append({**chuan, "cai_luc": cai_luc if isinstance(cai_luc, str) else kho.bay_gio_iso()})
        sach["tu_xa.json"] = {"modules": ds}
    return _doi_chieu(manifest, sach, so, ng, sha)


def _doi_chieu(manifest: dict, sach: dict[str, Any], so: Any, ng: Any, sha: str) -> dict[str, Any]:
    goc = kho.thu_muc_du_lieu()
    ghi: dict[str, Any] = dict(sach)
    bo: list[str] = []
    giu_lai: list[dict] = []
    tu_choi: list[dict] = []
    khac_biet: dict[str, Any] = {}

    def bi_mat_cu_o_lai(id_: str) -> bool:
        """Khoá của id này đang trên máy và bản sao lưu KHÔNG mang khoá thay thế → khoá cũ sẽ đi tiếp."""
        return (goc / "bi_mat" / f"{id_}.json").is_file() and f"bi_mat/{id_}.json" not in sach

    # nguon_git.json — nguồn MÃ chạy trong lõi.
    if "nguon_git.json" in sach:
        moi = sach["nguon_git.json"]
        hien_tai = ng.cau_hinh() if ng is not None else nguon_git.doc_cau_hinh()
        dinh_danh = ("repo", "nhanh", "thu_muc")
        kb: dict[str, Any] = {"hien_tai": None if hien_tai is None else
                              {k: hien_tai.get(k) for k in (*dinh_danh, "chu_ky_giay", "tu_dong", "yeu_cau_chu_ky")},
                              "ban_sao_luu": dict(moi), "se_ghi": None}
        tm_mod = kho.thu_muc_modules()
        co_module_git = tm_mod.is_dir() and any(d.is_dir() and not d.name.startswith(".") for d in tm_mod.iterdir())
        doi = [k for k in dinh_danh if hien_tai is not None and hien_tai.get(k) != moi[k]]
        if doi or (hien_tai is None and co_module_git):
            # Nguồn MỚI = lần đồng bộ ĐẦU của nguồn đó, và lần đầu tự cập nhật module đã cài có bản khác
            # (NguonGit._tu_dong) dù tu_dong tắt; chữ ký không cứu được (kẻ xấu ký được commit của kho mình).
            # Đổi nguồn mã phải là việc quản trị làm tay ở PUT /api/nguon-git.
            del ghi["nguon_git.json"]
            bo.append("nguon_git.json")
            for k in doi or ["repo"]:
                giu_lai.append({"tep": "nguon_git.json", "truong": k,
                                "ly_do": "khôi phục không đổi nguồn mã module (đổi nguồn = tự cập nhật module đã "
                                         "cài ở lần đồng bộ đầu); muốn đổi thì đặt tay ở Hệ thống → Module"})
        else:
            # KHÔNG nới mặc định an toàn: hai cờ giữ giá trị ĐANG có (chưa cấu hình = mặc định tắt).
            se_ghi = {**moi, "tu_dong": bool((hien_tai or {}).get("tu_dong", False)),
                      "yeu_cau_chu_ky": bool((hien_tai or {}).get("yeu_cau_chu_ky", False))}
            for k in ("tu_dong", "yeu_cau_chu_ky"):
                if moi[k] != se_ghi[k]:
                    giu_lai.append({"tep": "nguon_git.json", "truong": k, "ly_do": "khôi phục không nới cờ an toàn "
                                    "của nguồn git — giữ giá trị đang có; đổi tay ở Hệ thống → Module nếu muốn"})
            ghi["nguon_git.json"] = se_ghi
            kb["se_ghi"] = dict(se_ghi)
        khac_biet["nguon_git"] = kb

    catalog = getattr(so, "catalog", {}) or {}

    # tu_xa.json — URL mà lõi gửi yêu cầu (KÈM khoá) tới.
    if "tu_xa.json" in sach:
        cu = {m.get("id"): m for m in _doc_an_toan(kho.doc_tu_xa, []) if isinstance(m, dict)}
        tt_catalog = {km.tien_to: id_ for id_, km in catalog.items()}
        ds = []
        for rec in sach["tu_xa.json"]["modules"]:
            c = cu.get(rec["id"])
            trang_thai = "moi" if c is None else "giu_nguyen" if all(
                c.get(k) == rec[k] for k in ("ten", "tien_to", "base_url")) else "doi"
            ds.append({"id": rec["id"], "ten": rec["ten"], "tien_to": rec["tien_to"], "base_url": _che_url(rec["base_url"]),
                       "base_url_hien_tai": _che_url(c.get("base_url")) if c else None, "trang_thai": trang_thai})
            if rec["id"] in catalog or rec["tien_to"] in tt_catalog:
                tu_choi.append({"tep": "tu_xa.json", "id": rec["id"],
                                "ly_do": "trùng id hoặc tiền tố với module có mã đang cài (sẽ chiếm tuyến của nó)"})
            elif bi_mat_cu_o_lai(rec["id"]) and (c is None or c.get("base_url") != rec["base_url"]):
                tu_choi.append({"tep": "tu_xa.json", "id": rec["id"],
                                "ly_do": "base_url khác địa chỉ mà khoá API đang lưu trên máy đi cùng — khoá cũ sẽ bị "
                                         "gửi tới máy mới; gỡ module từ xa này rồi khôi phục, hoặc dùng bản sao lưu có "
                                         "mật khẩu (mang khoá của chính nó)"})
        moi_id = {r["id"] for r in sach["tu_xa.json"]["modules"]}
        khac_biet["tu_xa"] = ds
        khac_biet["tu_xa_se_go"] = sorted(i for i in cu if isinstance(i, str) and i not in moi_id)

    # modules.json — cấu hình công khai của module có mã; ghép với bi_mat/<id>.json lúc nạp.
    if "modules.json" in sach:
        cu = {m.get("id"): m for m in kho.doc_modules()[0] if isinstance(m, dict)}
        ds = []
        for m in sach["modules.json"]["modules"]:
            km = catalog.get(m["id"])
            if km is not None:
                try:
                    kho.kiem_cau_hinh_cong_khai(km.truong_cau_hinh, m["cau_hinh_cong_khai"])
                except ValueError as e:
                    raise _sai(f"Bản sao lưu không hợp lệ (modules.json: {m['id']}: {e}).") from None
                bi_mat_k = {t["khoa"] for t in km.truong_cau_hinh if t["kieu"] in kho.KIEU_BI_MAT}
            else:
                bi_mat_k = set()  # không biết manifest → so MỌI trường (dạng che cũng là dấu khoá đi cùng)
            c = cu.get(m["id"])
            ck_cu = (c or {}).get("cau_hinh_cong_khai") or {}
            ck_moi = m["cau_hinh_cong_khai"]
            truong_doi = sorted(k for k in set(ck_cu) | set(ck_moi) if k not in bi_mat_k and ck_cu.get(k) != ck_moi.get(k))
            trang_thai = "moi" if c is None else "doi" if truong_doi else "giu_nguyen"
            ds.append({"id": m["id"], "trang_thai": trang_thai, "truong_doi": truong_doi})
            if bi_mat_cu_o_lai(m["id"]) and trang_thai != "giu_nguyen":
                tu_choi.append({"tep": "modules.json", "id": m["id"],
                                "ly_do": "cấu hình công khai khác cấu hình mà khoá API đang lưu trên máy đi cùng — gỡ "
                                         "cấu hình module này rồi khôi phục, hoặc dùng bản sao lưu có mật khẩu"})
        moi_id = {m["id"] for m in sach["modules.json"]["modules"]}
        khac_biet["modules"] = ds
        khac_biet["modules_se_go"] = sorted(i for i in cu if isinstance(i, str) and i not in moi_id)

    if "combo.json" in sach:
        for ten in sach["combo.json"]:
            if so is not None and so.tim_theo_tien_to(ten) is not None:
                tu_choi.append({"tep": "combo.json", "id": ten, "ly_do": "tên combo trùng tiền tố của một module"})
        khac_biet["combo"] = sorted(sach["combo.json"])

    if "khoa_khach.json" in sach:
        kb_khoa = khoa_khach.khac_biet(sach["khoa_khach.json"]["khoa"])
        khac_biet["khoa_khach"] = kb_khoa
        for m in kb_khoa["khoa"]:
            # Khoá ĐANG có chủ mà bản sao lưu bỏ/đổi chủ: thành khoá không chủ (hết trần chủ, sống khi chủ bị tắt) hoặc
            # sang trần người khác — là leo thang quyền, không phải khôi phục. Gán chủ cho khoá không chủ (thu hẹp)
            # chỉ được đánh dấu `doi_chu`.
            if m.get("chu_id_hien_tai") and m["chu_id"] != m["chu_id_hien_tai"]:
                tu_choi.append({"tep": "khoa_khach.json", "id": m["id"],
                                "ly_do": "khoá đang có chủ mà bản sao lưu bỏ hoặc đổi chủ (khoá sẽ thoát trần của chủ "
                                         "và không còn tắt theo chủ) — thu hồi khoá này rồi khôi phục, hoặc sửa bản "
                                         "sao lưu giữ đúng chủ"})

    bi_mat = sorted(k for k in sach if k.startswith("bi_mat/"))
    if bi_mat:
        khac_biet["bi_mat"] = bi_mat

    doi_cau_hinh = {r[len("bi_mat/"):-len(".json")] for r in bi_mat if r.count("/") == 1}
    for m in khac_biet.get("modules", []):
        if m["trang_thai"] != "giu_nguyen":
            doi_cau_hinh.add(m["id"])
    doi_cau_hinh.update(khac_biet.get("modules_se_go", []))
    return {"sha256": sha, "manifest": manifest, "ghi": ghi, "bo": sorted(bo), "giu_lai": giu_lai,
            "tu_choi": tu_choi, "khac_biet": khac_biet, "doi_cau_hinh": doi_cau_hinh}


@contextmanager
def _mot_luot():
    if not _KHOA_KHOI_PHUC.acquire(blocking=False):
        raise LoiOpenAI(429, "Đang có một lượt khôi phục khác — thử lại sau ít giây.", loai="rate_limit_error",
                        code="khoi_phuc_dang_chay", headers={"Retry-After": "5"})
    try:
        yield
    finally:
        _KHOA_KHOI_PHUC.release()


def _trang_thai_bi_mat(ghi: dict) -> str:
    return "có trong bản sao lưu" if any(k.startswith("bi_mat/") for k in ghi) else "không có trong bản sao lưu"


async def kiem_khoi_phuc(du_lieu: bytes, mat_khau: str | None, so: Any = None, ng: Any = None) -> dict[str, Any]:
    """Chế độ `kiem`: lập kế hoạch, KHÔNG ghi gì; trả đúng thứ `ap_dung` sẽ làm và các khác biệt nhạy cảm."""
    with _mot_luot():
        kh = await _lap_ke_hoach(du_lieu, mat_khau, so, ng)
    nhat_ky.ghi("quan_tri", "thong_tin", "Kiểm tra bản sao lưu (chế độ kiem)", so_tep=len(kh["ghi"]),
                tu_choi=len(kh["tu_choi"]))
    return {"ok": True, "che_do": "kiem", "sha256": kh["sha256"], "se_ap_dung": sorted(kh["ghi"]), "se_bo": kh["bo"],
            "bi_mat": _trang_thai_bi_mat(kh["ghi"]), "modules_git": kh["manifest"].get("modules_git", []),
            "co_the_ap_dung": not kh["tu_choi"], "tu_choi": kh["tu_choi"], "giu_lai": kh["giu_lai"],
            "khac_biet": kh["khac_biet"]}


# ───────────── ghi + hoàn tác ─────────────

def _sao_luu_truoc_khi_ap_dung(goc: Path) -> tuple[Path, int]:
    """Chép cấu hình hiện tại sang <DATA>/.truoc-khoi-phuc-<thời điểm>/ (0700; CHỨA KHOÁ THẬT dạng rõ — bi_mat/).
    Giữ tối đa TOI_DA_BAN_TRUOC bản. Trả (thư mục vừa tạo, số bản đang giữ)."""
    thoi_diem = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    tm_truoc = goc / f"{TIEN_TO_BAN_TRUOC}{thoi_diem}-{secrets.token_hex(3)}"
    os.mkdir(tm_truoc, 0o700)
    os.chmod(tm_truoc, 0o700)  # mkdir chịu umask; thư mục này DO khôi phục tạo
    for f in sorted(_BO_KIEM):
        if (goc / f).is_file():
            shutil.copy2(goc / f, tm_truoc / f)
    if (goc / "bi_mat").is_dir():
        shutil.copytree(goc / "bi_mat", tm_truoc / "bi_mat", symlinks=True)
    ds_truoc = sorted((d for d in goc.iterdir() if d.is_dir() and d.name.startswith(TIEN_TO_BAN_TRUOC)),
                      key=lambda x: (x.stat().st_mtime, x.name))
    for d in ds_truoc[:-TOI_DA_BAN_TRUOC]:
        if d != tm_truoc:
            shutil.rmtree(d, ignore_errors=True)
    so_ban = sum(1 for d in goc.iterdir() if d.is_dir() and d.name.startswith(TIEN_TO_BAN_TRUOC))
    return tm_truoc, so_ban


def _ghi_tep(p: Path, noi_dung: bytes, quyen: int) -> None:
    fd, tam = tempfile.mkstemp(dir=p.parent, prefix=f".{p.name}.", suffix=".tam")
    try:
        os.fchmod(fd, quyen)  # quyền TRƯỚC nội dung: không khoảnh khắc nào bí mật nằm trong tệp đọc được
        with os.fdopen(fd, "wb") as f:
            f.write(noi_dung)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tam, p)
    except BaseException:
        try:
            os.unlink(tam)
        except FileNotFoundError:
            pass
        raise


class _NhatKyGhi:
    """Những gì một lượt ghi đã làm — đủ để hoàn tác: tệp đã thay (và trước đó có hay chưa), thư mục đã tạo."""

    def __init__(self, goc: Path, tm_truoc: Path):
        self.goc, self.tm_truoc = goc, tm_truoc
        self.tep: list[tuple[str, bool]] = []
        self.thu_muc: list[Path] = []
        self.so_ban = 0


def _ghi_ke_hoach(goc: Path, ghi: dict[str, Any], nk: _NhatKyGhi) -> None:
    for rel in sorted(ghi):
        p = goc / rel
        # Chỉ tạo (0700) và chmod thư mục DO khôi phục tạo, bên dưới bi_mat/; không đụng thư mục gốc dữ liệu.
        thieu = [d for d in reversed(p.relative_to(goc).parents) if str(d) != "." and not (goc / d).exists()]
        for d in thieu:
            os.mkdir(goc / d, 0o700)
            os.chmod(goc / d, 0o700)
            nk.thu_muc.append(goc / d)
        noi_dung = json.dumps(ghi[rel], ensure_ascii=False, indent=2).encode("utf-8")
        co_truoc = p.exists()
        _ghi_tep(p, noi_dung, _quyen_tep(rel))
        nk.tep.append((rel, co_truoc))


def _hoan_tac(nk: _NhatKyGhi) -> list[str]:
    """Phục hồi mọi tệp đã ghi từ bản .truoc-khoi-phuc-* vừa tạo; tệp chưa có trước đó thì xoá. Trả lỗi (nếu có)."""
    loi = []
    for rel, co_truoc in reversed(nk.tep):
        p = nk.goc / rel
        try:
            if co_truoc:
                nguon = nk.tm_truoc / rel
                _ghi_tep(p, nguon.read_bytes(), os.stat(nguon).st_mode & 0o777)
            else:
                p.unlink(missing_ok=True)
        except OSError as e:
            loi.append(f"{rel}: {type(e).__name__}")
    for d in reversed(nk.thu_muc):
        try:
            d.rmdir()
        except OSError:
            pass
    nk.tep.clear()
    nk.thu_muc.clear()
    return loi


@contextmanager
def _giu_khoa_ghi():
    """Khoá khoá khách (để nhịp đồng bộ không ghi cache cũ đè) + khoá kho (không xen với PUT cấu hình) suốt lượt ghi."""
    with ExitStack() as st:
        st.enter_context(khoa_khach.thay_tep_tu_ngoai())
        st.enter_context(kho.giu_khoa_kho())
        yield


def _ghi_trong_khoa(goc: Path, ghi: dict[str, Any]) -> _NhatKyGhi:
    with _giu_khoa_ghi():
        try:
            tm_truoc, so_ban = _sao_luu_truoc_khi_ap_dung(goc)
        except OSError as e:
            raise LoiOpenAI(500, f"Không tạo được bản lưu trước khi khôi phục ({type(e).__name__}) — chưa ghi gì.",
                            code="khoi_phuc_loi_ghi") from None
        nk = _NhatKyGhi(goc, tm_truoc)
        nk.so_ban = so_ban
        try:
            _ghi_ke_hoach(goc, ghi, nk)
        except Exception as e:
            loi = _hoan_tac(nk)
            raise _loi_hoan_tac(f"ghi tệp lỗi ({type(e).__name__})", loi, tm_truoc) from None
        return nk


def _hoan_tac_trong_khoa(nk: _NhatKyGhi) -> list[str]:
    with _giu_khoa_ghi():
        return _hoan_tac(nk)


def _loi_hoan_tac(ly_do: str, loi: list[str], tm_truoc: Path) -> LoiOpenAI:
    if loi:
        return LoiOpenAI(500, f"Khôi phục thất bại: {ly_do}; hoàn tác CHƯA trọn ({'; '.join(loi)}) — bản trước "
                              f"khôi phục còn nguyên ở <DATA>/{tm_truoc.name}/", code="khoi_phuc_hoan_tac_loi")
    return LoiOpenAI(500, f"Khôi phục thất bại: {ly_do} — đã hoàn tác, cấu hình như trước khi khôi phục.",
                     code="khoi_phuc_da_hoan_tac")


def _kiem_chung(ghi: dict[str, Any], so: Any) -> str | None:
    """Sau ghi + nạp lại: từng khoá khách của bản sao lưu phải (1) đọc lại được ĐÚNG bản ghi đã ghi (id, bật, phạm vi,
    chủ — bản ghi THÔ theo băm, lõi không giữ khoá thô) và (2) thấy được qua đường tra THẬT của xác thực. Không so
    phạm vi/bật HIỆU LỰC với tệp: chính sách trần chủ (hạ trần, chủ tắt, máy mới chưa có chủ) áp lúc chạy và được
    phép khác bản ghi — so nó từng làm hỏng (500) mọi khôi phục có khoá có chủ. Cùng kiểm từng module từ xa đã khôi
    phục. Trả lý do hỏng hoặc None."""
    for k in (ghi.get("khoa_khach.json") or {}).get("khoa", []):
        r = khoa_khach.ban_ghi_theo_bam(k["bam"])
        if (r is None or r.get("id") != k["id"] or r.get("bat", True) != k["bat"]
                or list(r.get("pham_vi", ["*"])) != k["pham_vi"] or r.get("chu_id") != k.get("chu_id")):
            return f"khoá khách {k['id']} không đọc lại được đúng như bản sao lưu"
        t = khoa_khach.tim_theo_bam(k["bam"])
        if t is None or t.get("id") != k["id"]:
            return f"khoá khách {k['id']} không tra được qua đường xác thực"
    if so is not None:
        for rec in (ghi.get("tu_xa.json") or {}).get("modules", []):
            if rec["id"] not in so.dang_chay:
                return f"module từ xa {rec['id']} không chạy sau khi nạp lại"
    return None


async def ap_dung_khoi_phuc(
    du_lieu: bytes,
    mat_khau: str | None,
    so: Any = None,
    ng: Any = None,
    sha256_da_kiem: str | None = None,
) -> dict[str, Any]:
    """Chế độ `ap_dung`: lập CÙNG kế hoạch như `kiem`, từ chối nếu còn mục bị từ chối, ghi nguyên tử, nạp lại và
    kiểm chứng; hỏng ở bất kỳ bước nào sau khi đã ghi → hoàn tác về trạng thái trước."""
    if not isinstance(sha256_da_kiem, str) or not _SHA256.fullmatch(sha256_da_kiem):
        raise LoiOpenAI(409, "Áp dụng cần «sha256» của tệp đã «Kiểm tra» (trả về ở chế độ kiem).",
                        code="chua_kiem_dung_tep")
    with _mot_luot():
        kh = await _lap_ke_hoach(du_lieu, mat_khau, so, ng, sha256_can=sha256_da_kiem)
        if kh["tu_choi"]:
            ds = "; ".join(f"{t['tep']} «{nhac_lai(t['id'])}»: {t['ly_do']}" for t in kh["tu_choi"])
            raise LoiOpenAI(409, f"Không áp dụng — có mục bị từ chối: {ds}", code="khoi_phuc_xung_dot")
        goc = kho.thu_muc_du_lieu()
        ghi = kh["ghi"]
        nk = await asyncio.to_thread(_ghi_trong_khoa, goc, ghi)

        ket_qua_nap_lai: dict = {}
        try:
            if so is not None:
                ket_qua_nap_lai = await so.nap_lai_tu_kho(kh["doi_cau_hinh"])
            ly_do = _kiem_chung(ghi, so)
        except Exception as e:
            log.exception("Nạp lại sau khôi phục lỗi")
            ly_do = f"nạp lại lỗi ({type(e).__name__})"
        if ly_do:
            loi = await asyncio.to_thread(_hoan_tac_trong_khoa, nk)
            if so is not None:
                try:
                    await so.nap_lai_tu_kho(kh["doi_cau_hinh"])
                except Exception:
                    log.exception("Nạp lại sau hoàn tác lỗi")
            nhat_ky.ghi("quan_tri", "loi", f"Khôi phục bị hoàn tác: {ly_do}")
            raise _loi_hoan_tac(f"kiểm chứng sau ghi không đạt: {ly_do}", loi, nk.tm_truoc)

    if ng is not None and "nguon_git.json" in ghi:
        ng.danh_thuc()
    nhat_ky.ghi("quan_tri", "thong_tin", "Khôi phục cấu hình thành công (chế độ áp dụng)", so_tep=len(ghi),
                bi_mat=_trang_thai_bi_mat(ghi), ban_truoc=nk.tm_truoc.name)
    return {
        "ok": True,
        "da_ap_dung": sorted(ghi),
        "da_bo": kh["bo"],
        "giu_lai": kh["giu_lai"],
        "bi_mat": "đã khôi phục" if any(k.startswith("bi_mat/") for k in ghi) else "không có trong bản sao lưu",
        "nap_lai": ket_qua_nap_lai,
        # Vị trí bản lưu trước khôi phục (CHỨA khoá thật — xem docs/THAM-CHIEU.md cách xoá), không lộ nội dung.
        "ban_truoc": {"thu_muc": nk.tm_truoc.name, "so_ban_giu": nk.so_ban, "toi_da": TOI_DA_BAN_TRUOC},
    }
