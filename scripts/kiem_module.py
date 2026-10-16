#!/usr/bin/env python3
"""Kiem module cho tac gia: kiem manifest bang dung bo kiem cua loi, quet tinh
ma nguon (ast, khong nhap, khong chay) va doi chieu voi quyen da khai.

Cach dung:
    python3 -m scripts.kiem_module <thu-muc-module> [--json]

Ma thoat: 0 = dat; 1 = co loi manifest hoac vi pham; 2 = khong doc duoc.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

GOC_KHO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(GOC_KHO))
from app.kham_pha import kiem_manifest  # noqa: E402

# ---------------------------------------------------------------- hang so

DOI_DAI_TOI_DA = 200  # chuoi lay tu ma module luon bi cat ngan o day

# Cac goc import duoc xem la "goi mang".
MANG_GOC = {"httpx", "requests", "aiohttp", "urllib", "urllib3", "socket",
            "websockets", "websocket", "http", "ssl"}
# Cac goc import duoc xem la "tien trinh con".
TIEN_TRINH_CON_GOC = {"subprocess"}
# Cac goc import duoc xem la "tuan tu hoa nguy hiem".
TUAN_TU_HOA_GOC = {"pickle", "marshal", "shelve"}
# Ham nguy hiem goi truc tiep.
HAM_NGUY_HIEM = {"eval", "exec", "compile", "__import__"}
# Phuong thuc doc bien moi truong cua os.
DOC_ENV = {"getenv", "environ"}
# Khong gian ten loi: module duoc phep dung, khong can khai yeu_cau_goi.
BO_QUA_GOI = {"app"}
# (MAU_MIEN cũ đã bỏ: chỉ trích miền từ URL đầy đủ trong _mien_trong_chuoi.)
# Mau chuoi giong bi mat nhung cung: khoa/token/mat khau gan truc tiep.
MAU_BI_MAT = re.compile(
    r"(?i)(api[_-]?key|secret|token|passwd|password|mat_khau|khoa)"
    r"\s*[:=]\s*['\"][^'\"]{8,}['\"]")
# Mau IP noi bo.
MAU_IP_NOI_BO = re.compile(
    r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|192\.168\.\d{1,3}\.\d{1,3}"
    r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b")
# Mau chuoi base64/hex dai dang ngo.
MAU_CHUOI_DAI = re.compile(r"['\"]([A-Za-z0-9+/=_-]{40,})['\"]")

try:
    STDLIB = set(sys.stdlib_module_names)
except AttributeError:  # Python < 3.10, du phong
    STDLIB = set()


def lam_sach(s: str) -> str:
    """Cat ngan va loai ky tu dieu khien: noi dung ma la du lieu khong tin
    duoc, khong dua nguyen van vo han ra ngoai (ke ca vao JSON cho AI doc)."""
    s = "".join(c for c in s if c == "\n" or c == "\t" or ord(c) >= 32)
    if len(s) > DOI_DAI_TOI_DA:
        s = s[:DOI_DAI_TOI_DA] + "…"
    return s


def goi_kho_san() -> set[str]:
    """Cac goi da co trong anh chay cua kho (requirements.txt) — module dung
    khong can khai lai trong yeu_cau_goi."""
    goi = set()
    try:
        noi_dung = (GOC_KHO / "requirements.txt").read_text(encoding="utf-8")
    except OSError:
        return goi
    for dong in noi_dung.splitlines():
        dong = dong.strip()
        if not dong or dong.startswith("#"):
            continue
        ten = re.split(r"[<>=!~\s\[]", dong, 1)[0].strip().lower()
        if ten:
            goi.add(ten.replace("-", "_"))
    return goi


class KetQuaQuet:
    def __init__(self) -> None:
        self.nhap: set[str] = set()          # goc import
        self.mien: set[str] = set()          # ten mien phat hien
        self.mo_tep: list[str] = []          # vi tri goi open()
        self.tien_trinh_con: list[str] = []
        self.nguy_hiem: list[str] = []       # exec/eval/pickle...
        self.doc_env: list[str] = []
        self.tat_tls: list[str] = []         # verify=False
        self.loi_goi_mang: list[str] = []    # goi mang khong ro mien


class BoQuet(ast.NodeVisitor):
    def __init__(self, tep: str) -> None:
        self.tep = tep
        self.kq = KetQuaQuet()

    def _vi_tri(self, node: ast.AST) -> str:
        return f"{self.tep}:{node.lineno}"

    def _day_du(self, node: ast.AST) -> str:
        """Ten day du cua Name/Attribute, vd «httpx.get»."""
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return self._day_du(node.value) + "." + node.attr
        return ""

    def _goc(self, day_du: str) -> str:
        return day_du.split(".")[0]

    def visit_Import(self, node: ast.Import) -> None:
        for a in node.names:
            self.kq.nhap.add(a.name.split(".")[0])
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level:
            # Import tuong doi (from .client import …): module noi bo, bo qua.
            self.generic_visit(node)
            return
        if node.module:
            self.kq.nhap.add(node.module.split(".")[0])
        self.generic_visit(node)

    def _mien_trong_chuoi(self, s: str) -> None:
        # Chỉ trích miền từ URL đầy đủ (có ://): đó mới là "gọi mạng".
        # Chuỗi thường nhắc tên miền (vd kiểm tra redirect) không tính.
        for m in re.finditer(r"https?://([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
                             r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+)",
                             s, re.IGNORECASE):
            mien = m.group(1).lower().rstrip(".")
            nhan_cuoi = mien.rsplit(".", 1)[-1]
            if len(nhan_cuoi) >= 2 and nhan_cuoi.isalpha():
                self.kq.mien.add(mien)

    def _quet_chuoi_doi_so(self, node: ast.Call) -> None:
        for a in list(node.args) + [k.value for k in node.keywords]:
            if isinstance(a, ast.Constant) and isinstance(a.value, str):
                self._mien_trong_chuoi(a.value)

    def visit_Constant(self, node: ast.Constant) -> None:
        # Quét mọi hằng chuỗi (gán hằng, docstring, f-string phần tĩnh):
        # tên miền có thể nằm ở hằng MAY_CHU chứ không chỉ đối số gọi.
        if isinstance(node.value, str):
            self._mien_trong_chuoi(node.value)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        ten = self._day_du(node.func)
        goc = self._goc(ten)
        vt = self._vi_tri(node)
        if goc in MANG_GOC:
            self._quet_chuoi_doi_so(node)
            self.kq.loi_goi_mang.append(f"{vt}: {lam_sach(ten)}")
        elif ten == "open":
            self.kq.mo_tep.append(vt)
        elif goc in TIEN_TRINH_CON_GOC or ten in {"os.system", "os.popen"}:
            self.kq.tien_trinh_con.append(f"{vt}: {lam_sach(ten)}")
        elif ten in HAM_NGUY_HIEM or goc in TUAN_TU_HOA_GOC:
            self.kq.nguy_hiem.append(f"{vt}: {lam_sach(ten)}")
        elif goc == "os" and any(ten == f"os.{d}" for d in DOC_ENV):
            self.kq.doc_env.append(f"{vt}: {lam_sach(ten)}")
        for kw in node.keywords:
            if (kw.arg == "verify" and isinstance(kw.value, ast.Constant)
                    and kw.value.value is False):
                self.kq.tat_tls.append(vt)
        self.generic_visit(node)


def quet_tep(duong: Path, goc: Path) -> KetQuaQuet:
    bq = BoQuet(str(duong.relative_to(goc)))
    try:
        cay = ast.parse(duong.read_text(encoding="utf-8"), filename=str(duong))
    except (OSError, SyntaxError, ValueError):
        return bq.kq
    bq.visit(cay)
    return bq.kq


def quet_ro_bi_mat(duong: Path, goc: Path) -> list[str]:
    """Canh bao chuoi giong bi mat nhung cung hoac IP noi bo trong ma."""
    ket: list[str] = []
    try:
        dong_list = duong.read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError):
        return ket
    ten = str(duong.relative_to(goc))
    for i, dong in enumerate(dong_list, 1):
        if MAU_BI_MAT.search(dong):
            ket.append(f"{ten}:{i}: chuỗi giống khoá/token gán trực tiếp")
        if MAU_IP_NOI_BO.search(dong):
            ket.append(f"{ten}:{i}: địa chỉ IP nội bộ trong mã")
        m = MAU_CHUOI_DAI.search(dong)
        if m and len(m.group(1)) >= 64:
            ket.append(f"{ten}:{i}: chuỗi mã hoá dài đáng ngờ "
                       f"({len(m.group(1))} ký tự)")
    return ket


def _mien_khop(mien: str, khai: list[str]) -> bool:
    mien = mien.lower()
    for k in khai:
        k = k.lower().rstrip(".")
        if mien == k or mien.endswith("." + k):
            return True
    return False


def kiem_module(thu_muc: Path) -> tuple[int, dict]:
    """Tra (ma thoat, du kien co cau truc). Khong nhap, khong chay ma module."""
    loi_doc: list[str] = []
    loi_manifest: list[str] = []
    vi_pham: list[str] = []
    canh_bao: list[str] = []
    du_kien: dict = {"tep_quyet": [], "nhap": [], "mien": []}

    try:
        noi_dung = (thu_muc / "manifest.json").read_text(encoding="utf-8")
    except FileNotFoundError:
        return 2, {"loi": ["thiếu manifest.json"]}
    except OSError as e:
        return 2, {"loi": [f"không đọc được manifest.json: {e}"]}
    try:
        manifest = json.loads(noi_dung)
    except ValueError as e:
        return 2, {"loi": [f"manifest.json không phải JSON hợp lệ: {e}"]}

    ly_do = kiem_manifest(manifest)
    if ly_do is not None:
        loi_manifest.append(ly_do)

    mang_ra = manifest.get("tai_nguyen", {}).get("mang_ra", []) \
        if isinstance(manifest.get("tai_nguyen"), dict) else []
    yeu_cau_goi = manifest.get("yeu_cau_goi", []) \
        if isinstance(manifest.get("yeu_cau_goi"), list) else []
    kho_san = goi_kho_san()

    tep_py = sorted(p for p in thu_muc.rglob("*.py")
                    if "__pycache__" not in p.parts)
    if not tep_py:
        loi_doc.append("không tìm thấy tệp .py nào trong module")
    tong = KetQuaQuet()
    for tep in tep_py:
        kq = quet_tep(tep, thu_muc)
        tong.nhap |= kq.nhap
        tong.mien |= kq.mien
        tong.mo_tep += kq.mo_tep
        tong.tien_trinh_con += kq.tien_trinh_con
        tong.nguy_hiem += kq.nguy_hiem
        tong.doc_env += kq.doc_env
        tong.tat_tls += kq.tat_tls
        tong.loi_goi_mang += kq.loi_goi_mang
        canh_bao += quet_ro_bi_mat(tep, thu_muc)

    du_kien["tep_quyet"] = [str(p.relative_to(thu_muc)) for p in tep_py]
    du_kien["nhap"] = sorted(lam_sach(n) for n in tong.nhap)
    du_kien["mien"] = sorted(lam_sach(m) for m in tong.mien)

    # Doi chieu: goi mang ra mien chua khai trong mang_ra.
    for mien in sorted(tong.mien):
        if not _mien_khop(mien, mang_ra):
            vi_pham.append(
                f"gọi mạng tới «{mien}» nhưng tai_nguyen.mang_ra không khai "
                f"(đã khai: {mang_ra or 'trong'})")
    # Doi chieu: import goi ngoai khong khai trong yeu_cau_goi.
    for goi in sorted(tong.nhap):
        if goi in STDLIB or goi == "__future__" or goi in BO_QUA_GOI:
            continue
        if goi in kho_san:
            continue  # goi da co san trong anh chay cua kho
        if goi not in yeu_cau_goi:
            vi_pham.append(
                f"dùng gói «{goi}» nhưng yeu_cau_goi không khai")
    for vt in tong.tien_trinh_con:
        vi_pham.append(f"mở tiến trình con tại {vt} — phải khai nguy_hiem")
    for vt in tong.nguy_hiem:
        vi_pham.append(f"gọi nguy hiểm tại {vt} — phải khai nguy_hiem")
    for vt in tong.tat_tls:
        vi_pham.append(f"tắt kiểm tra TLS (verify=False) tai {vt}")

    du_kien["loi_manifest"] = loi_manifest
    du_kien["vi_pham"] = vi_pham
    du_kien["canh_bao"] = canh_bao

    if loi_doc:
        return 2, {"loi": loi_doc, **du_kien}
    if loi_manifest or vi_pham:
        return 1, du_kien
    return 0, du_kien


def in_van_ban(ten: str, ma: int, dk: dict) -> None:
    loi = dk.get("loi", [])
    loi_manifest = dk.get("loi_manifest", [])
    vi_pham = dk.get("vi_pham", [])
    canh_bao = dk.get("canh_bao", [])
    tong_loi = len(loi) + len(loi_manifest) + len(vi_pham)
    ket_luan = "đạt" if ma == 0 else "cần sửa"
    print(f"KẾT LUẬN: {ket_luan} — {tong_loi} mục cần sửa, "
          f"{len(canh_bao)} cảnh báo (module «{ten}»)")
    for muc in loi + loi_manifest:
        print(f"  [lỗi] {muc}")
    for muc in vi_pham:
        print(f"  [vi phạm] {muc}")
    for muc in canh_bao:
        print(f"  [cảnh báo] {muc}")
    if ma == 0:
        nhap = dk.get("nhap", [])
        mien = dk.get("mien", [])
        print(f"  đã quét {len(dk.get('tep_quyet', []))} tệp .py, "
              f"{len(nhap)} gói import, {len(mien)} tên miền.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Kiểm module web2api cho tác giả (không chạy mã module).")
    ap.add_argument("thu_muc", help="thư mục chứa manifest.json của module")
    ap.add_argument("--json", action="store_true",
                    help="in dữ kiện có cấu trúc dạng JSON")
    ns = ap.parse_args(argv)
    thu_muc = Path(ns.thu_muc)
    if not thu_muc.is_dir():
        msg = f"không phải thư mục: {ns.thu_muc}"
        if ns.json:
            print(json.dumps({"loi": [msg]}, ensure_ascii=False))
        else:
            print(f"KẾT LUẬN: cần sửa — 1 mục cần sửa, 0 cảnh báo\n"
                  f"  [lỗi] {msg}")
        return 2
    ma, dk = kiem_module(thu_muc)
    ten = thu_muc.name
    try:
        m = json.loads((thu_muc / "manifest.json").read_text(encoding="utf-8"))
        if isinstance(m, dict) and isinstance(m.get("id"), str):
            ten = m["id"]
    except (OSError, ValueError):
        pass
    if ns.json:
        print(json.dumps({"module": lam_sach(ten), "ma_thoat": ma,
                           **{k: v for k, v in dk.items()}},
                          ensure_ascii=False, indent=2))
    else:
        in_van_ban(ten, ma, dk)
    return ma


if __name__ == "__main__":
    sys.exit(main())
