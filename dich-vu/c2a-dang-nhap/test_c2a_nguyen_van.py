# test_c2a_nguyen_van.py — Kiểm tra tính nguyên văn của logic c2a và dấu vết chỉnh sửa # W2A:
from __future__ import annotations

import ast
import fnmatch
import hashlib
from pathlib import Path

import pytest

try:
    import pydantic_settings  # noqa: F401
except ImportError:
    pytest.fail("Môi trường thiếu gói pydantic_settings bắt buộc; không được bỏ qua bài kiểm.")
import _gia_goi  # noqa: F401  (dữ liệu tạm + mô-đun giả; xem _gia_goi.py)


GOC_DICH_VU = Path(__file__).resolve().parent
BANG_BAM_PATH = GOC_DICH_VU / "nguon-c2a" / "BANG-BAM-GOC.txt"


def test_dong_nguon_dau_tep():
    """Mọi tệp .py trong src/ có dòng đầu bắt đầu bằng # NGUỒN: TriTue2011/chatgpt2api@ và chứa captcha-solver/src/."""
    src_dir = GOC_DICH_VU / "src"
    loi = []
    for f in src_dir.rglob("*.py"):
        lines = f.read_text(encoding="utf-8").splitlines()
        if not lines:
            loi.append(f"{f.name}: tệp rỗng")
            continue
        first_line = lines[0]
        if first_line.startswith("#!") and len(lines) > 1:
            first_line = lines[1]
        if not (first_line.startswith("# NGUỒN: TriTue2011/chatgpt2api@") and "captcha-solver/src/" in first_line):
            loi.append(f"{f.name}: dòng đầu không đúng chuẩn nguồn c2a ({first_line[:50]}...)")
    assert not loi, f"Có tệp thiếu dòng nguồn: {loi}"


def test_cac_moc_logic_con_nguyen():
    """Kiểm tra các mốc logic then chốt của c2a vẫn còn nguyên."""
    # 1. Hằng _VAO_O_MAT_KHAU_S == 420.0 trong auto_login
    from src import auto_login
    assert getattr(auto_login, "_VAO_O_MAT_KHAU_S") == 420.0, "_VAO_O_MAT_KHAU_S phải bằng 420.0"

    # 2. vault.TIEN_TO == "v1:" và dùng AESGCM
    from src import vault
    assert getattr(vault, "TIEN_TO") == "v1:"
    tree_vault = ast.parse((GOC_DICH_VU / "src" / "vault.py").read_text(encoding="utf-8"))
    co_aesgcm = any(
        isinstance(n, ast.Name) and n.id == "AESGCM" for n in ast.walk(tree_vault)
    )
    assert co_aesgcm, "vault.py phải sử dụng thuật toán AESGCM"

    # 3. accounts_db có LOAI_GOOGLE và LOAI_OPENAI
    from src import accounts_db
    assert getattr(accounts_db, "LOAI_GOOGLE") == "google"
    assert getattr(accounts_db, "LOAI_OPENAI") == "openai"

    # 4. browser_pool có cả đường firefox lẫn chromium (kiểm settings.browser bằng AST)
    tree_pool = ast.parse((GOC_DICH_VU / "src" / "browser_pool.py").read_text(encoding="utf-8"))
    dung_browser_attr = any(
        isinstance(n, ast.Attribute) and n.attr == "browser" for n in ast.walk(tree_pool)
    )
    assert dung_browser_attr, "browser_pool.py phải tham chiếu settings.browser"

    # 5. pyotp được import trong auto_login
    tree_auto = ast.parse((GOC_DICH_VU / "src" / "auto_login.py").read_text(encoding="utf-8"))
    co_pyotp = any(
        (isinstance(n, ast.Import) and any("pyotp" in a.name for a in n.names)) or
        (isinstance(n, ast.ImportFrom) and n.module and "pyotp" in n.module)
        for n in ast.walk(tree_auto)
    )
    assert co_pyotp, "auto_login.py phải import pyotp"


def kiem_tep(rel: str, noi_dung_bytes: bytes, bang_bam: dict[str, str]) -> list[str]:
    """Kiểm tra tệp so với bảng băm gốc.
    Nếu thân tệp (từ dòng 2 trở đi, bỏ qua shebang và dòng nguồn) không có '# W2A:'
    thì băm SHA256 phải khớp nguyên văn với bảng băm gốc.
    """
    hash_goc = bang_bam.get(rel)
    if not hash_goc:
        return [f"{rel}: không có trong bảng băm gốc"]
    cur_hash = hashlib.sha256(noi_dung_bytes).hexdigest()
    lines = noi_dung_bytes.decode("utf-8", errors="replace").splitlines()

    idx = 0
    if idx < len(lines) and lines[idx].startswith("#!"):
        idx += 1
    if idx < len(lines) and lines[idx].startswith("# NGUỒN:"):
        idx += 1

    than_lines = lines[idx:]
    dong_w2a = [l for l in than_lines if "# W2A:" in l]

    loi = []
    if not dong_w2a:
        if cur_hash != hash_goc:
            loi.append(f"{rel}: lệch băm ({cur_hash} != {hash_goc}) nhưng thân KHÔNG có '# W2A:'")
    return loi


def test_bang_bam_goc_va_w2a():
    """Đối chiếu bảng băm gốc: tệp không đổi phải khớp băm; tệp đổi phải có # W2A:."""
    assert BANG_BAM_PATH.is_file(), "Thiếu tệp BANG-BAM-GOC.txt"

    lines = BANG_BAM_PATH.read_text(encoding="utf-8").splitlines()
    bang_bam = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(maxsplit=1)
        if len(parts) == 2:
            bang_bam[parts[1].strip()] = parts[0].strip()

    assert len(bang_bam) > 0, "Bảng băm gốc rỗng"

    tep_trong_src = set()
    for f in (GOC_DICH_VU / "src").rglob("*.py"):
        rel = str(f.relative_to(GOC_DICH_VU))
        tep_trong_src.add(rel)

    # 1. Mọi tệp trong bảng còn tồn tại
    thieu = [t for t in bang_bam if t not in tep_trong_src]
    assert not thieu, f"Có tệp trong bảng băm bị xoá: {thieu}"

    # 2. Không có tệp .py mới trong src ngoài bảng băm
    moi = [t for t in tep_trong_src if t not in bang_bam]
    assert not moi, f"Có tệp mới ngoài bảng băm: {moi}"

    # 3. Kiểm tra sha256 và dấu vết # W2A: qua hàm kiem_tep
    sai_khong_w2a = []
    lech_co_w2a = []

    for rel, hash_goc in bang_bam.items():
        f = GOC_DICH_VU / rel
        data = f.read_bytes()
        cur_hash = hashlib.sha256(data).hexdigest()
        loi = kiem_tep(rel, data, bang_bam)
        if loi:
            sai_khong_w2a.extend(loi)

        lines = data.decode("utf-8", errors="replace").splitlines()
        idx = 0
        if idx < len(lines) and lines[idx].startswith("#!"):
            idx += 1
        if idx < len(lines) and lines[idx].startswith("# NGUỒN:"):
            idx += 1
        so_dong_w2a = len([l for l in lines[idx:] if "# W2A:" in l])
        if cur_hash != hash_goc or so_dong_w2a > 0:
            lech_co_w2a.append((rel, so_dong_w2a))

    assert not sai_khong_w2a, f"Tệp lệch băm mà thiếu '# W2A:': {sai_khong_w2a}"
    assert len(lech_co_w2a) > 0, "Phải có tệp được chỉnh sửa với '# W2A:'"



def test_import_src_main_thanh_cong():
    """Kiểm tra import src.main thành công với các mock và app là FastAPI."""
    from fastapi import FastAPI
    from src.main import app
    assert isinstance(app, FastAPI), "src.main.app phải là thể hiện của FastAPI"


def test_dot_bien_kiem_tep_w2a():
    """Đột biến kiểm tra tính đúng đắn của kiem_tep:
    (a) sửa một byte của vault.py mà không thêm W2A -> kiem_tep trả lỗi;
    (b) thêm một dòng '# W2A:' hợp lệ -> kiem_tep không trả lỗi;
    (c) chỉ giữ dòng nguồn (có '# W2A:') mà đổi thân -> kiem_tep trả lỗi.
    """
    goc_bytes = (GOC_DICH_VU / "src" / "vault.py").read_bytes()
    # Tạo bản chuẩn không có dòng W2A nào ở thân
    lines = goc_bytes.splitlines()
    dong_nguon = lines[0]
    than_khong_w2a = [l for l in lines[1:] if b"# W2A:" not in l]
    chuan_bytes = dong_nguon + b"\n" + b"\n".join(than_khong_w2a) + b"\n"
    hash_chuan = hashlib.sha256(chuan_bytes).hexdigest()
    bang_bam = {"src/vault.py": hash_chuan}

    # (a) Sửa một byte không thêm W2A -> lỗi
    sua_bytes = chuan_bytes + b"\n# sua mot byte khong co dau w2a\n"
    loi_a = kiem_tep("src/vault.py", sua_bytes, bang_bam)
    assert len(loi_a) > 0, "Sửa byte mà không thêm W2A phải trả lỗi"

    # (b) Thêm dòng '# W2A:' hợp lệ -> không lỗi
    hop_le_bytes = sua_bytes + b"\n# W2A: sua hop le co chu thich\n"
    loi_b = kiem_tep("src/vault.py", hop_le_bytes, bang_bam)
    assert not loi_b, f"Có dòng '# W2A:' ở thân thì không được trả lỗi: {loi_b}"

    # (c) Chỉ giữ dòng nguồn (dù dòng nguồn có chứa '# W2A:') mà đổi thân không W2A -> lỗi
    than_doi = dong_nguon + b"\nprint('than moi khong co w2a')\n"
    loi_c = kiem_tep("src/vault.py", than_doi, bang_bam)
    assert len(loi_c) > 0, "Chỉ giữ dòng nguồn mà đổi thân không W2A phải trả lỗi"


def _lay_cac_tep_duoc_import(goc_src: Path) -> set[Path]:
    """Duyệt AST từ src/main.py để thu thập tất cả các tệp src/**/*.py được import theo quan hệ phụ thuộc."""
    goc_src = goc_src.resolve()
    da_duyet = set()
    can_duyet = {(goc_src / "main.py").resolve()}

    while can_duyet:
        curr = can_duyet.pop()
        if curr in da_duyet or not curr.is_file():
            continue
        da_duyet.add(curr)
        tree = ast.parse(curr.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.level and node.level > 0:
                    base_dir = curr.parent
                    if node.level > 1:
                        for _ in range(node.level - 1):
                            base_dir = base_dir.parent
                    if node.module:
                        rel_parts = node.module.split(".")
                        target_file = base_dir.joinpath(*rel_parts).with_suffix(".py")
                        target_pkg = base_dir.joinpath(*rel_parts, "__init__.py")
                        if target_file.is_file():
                            can_duyet.add(target_file.resolve())
                        elif target_pkg.is_file():
                            can_duyet.add(target_pkg.resolve())
                    else:
                        for alias in node.names:
                            target_file = base_dir.joinpath(alias.name).with_suffix(".py")
                            if target_file.is_file():
                                can_duyet.add(target_file.resolve())
    return da_duyet


def _bi_dockerignore_loai(rel_path: str, cac_mau: list[str]) -> bool:
    """Kiểm tra đường dẫn tương đối có khớp một trong các mẫu trong .dockerignore không."""
    for mau in cac_mau:
        if fnmatch.fnmatch(rel_path, mau):
            return True
        if mau.endswith("/") and rel_path.startswith(mau):
            return True
        if "/" not in mau and fnmatch.fnmatch(Path(rel_path).name, mau):
            return True
    return False


def test_anh_chi_chua_tep_duoc_import():
    """Kiểm tra bằng AST từ src/main.py:
    (a) Mọi tệp trong tập được import KHÔNG bị .dockerignore loại.
    (b) Mọi kịch bản dò (capture_*, probe_api*, debug_fetch*, find_api_fields, log_requests, test_flow, cli) bị .dockerignore loại.
    """
    goc_src = (GOC_DICH_VU / "src").resolve()
    tap_import = _lay_cac_tep_duoc_import(goc_src)
    assert tap_import, "Tập import không được rỗng"

    dockerignore_path = GOC_DICH_VU / ".dockerignore"
    assert dockerignore_path.is_file(), "Thiếu tệp .dockerignore"
    cac_mau = [
        line.strip()
        for line in dockerignore_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]

    # (a) Mọi tệp trong tập import KHÔNG bị .dockerignore loại
    bi_loai_nham = []
    for p in tap_import:
        rel = str(p.relative_to(GOC_DICH_VU))
        if _bi_dockerignore_loai(rel, cac_mau):
            bi_loai_nham.append(rel)
    assert not bi_loai_nham, f"Tệp trong tập import bị .dockerignore loại nhầm: {bi_loai_nham}"

    # Kiểm tra danh sách các module cốt lõi đã đo bằng tay
    cac_ten_stem = {p.stem for p in tap_import}
    ky_vong_co = {
        "main", "accounts_db", "auto_login", "browser_pool", "chatgpt_login",
        "claude_web_login", "codex_google_onboard", "gemini_web_login",
        "github_codex_onboard", "grok_web_login", "openai_native_login",
        "settings", "vault", "workspaces",
    }
    assert ky_vong_co.issubset(cac_ten_stem), f"Thiếu module cốt lõi trong tập import: {ky_vong_co - cac_ten_stem}"
    assert "cli" not in cac_ten_stem, "cli không được nằm trong tập import của dịch vụ web"

    # (b) Nay mọi tệp src/**/*.py (trừ __init__.py) PHẢI nằm trong tập import từ main.py (không còn tệp mồ côi)
    tat_ca_py = set(goc_src.rglob("*.py"))
    khong_thuoc_import = [
        p for p in tat_ca_py
        if p.resolve() not in tap_import and p.name != "__init__.py"
    ]
    assert not khong_thuoc_import, f"Mọi tệp src/**/*.py phải nằm trong tập import từ main.py, phát hiện tệp mồ côi: {khong_thuoc_import}"




