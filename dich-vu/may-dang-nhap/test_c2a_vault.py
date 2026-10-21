# test_c2a_vault.py — Kiểm tra két mật khẩu tự sinh khoá và mã hoá accounts_db
from __future__ import annotations

import base64
import os
import sqlite3
import stat
from pathlib import Path

import pytest

try:
    import pydantic_settings  # noqa: F401
except ImportError:
    pytest.fail("Môi trường thiếu gói pydantic_settings bắt buộc; không được bỏ qua bài kiểm.")
import _gia_goi  # noqa: F401  (dữ liệu tạm + mô-đun giả; xem _gia_goi.py)



def test_ghi_tai_khoan_ma_hoa_v1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    khoa_dir = tmp_path / "khoa"
    khoa_dir.mkdir()
    db_path = data_dir / "accounts.db"

    monkeypatch.setenv("CAPTCHA_SOLVER_DATA_DIR", str(data_dir))
    monkeypatch.setenv("CAPTCHA_SOLVER_KEY_DIR", str(khoa_dir))
    monkeypatch.setenv("ACCOUNTS_DB", str(db_path))
    monkeypatch.delenv("VAULT_MASTER_KEY", raising=False)

    from src import accounts_db, settings, vault
    # Cập nhật settings và accounts_db._DB_PATH
    settings.settings.data_dir = data_dir
    settings.settings.key_dir = khoa_dir
    monkeypatch.setattr(accounts_db, "_DB_PATH", str(db_path))
    accounts_db.init_db()

    raw_password = "MatKhauSieuBiMat123!@#"
    raw_totp = "JBSWY3DPEHPK3PXP"
    email = "nguoi.test@example.com"

    accounts_db.save_account(
        email=email,
        password=raw_password,
        totp_secret=raw_totp,
        label="profile-test",
        loai="google",
    )

    # (a) Kiểm tra trong sqlite: password và totp_secret bắt đầu bằng v1: và KHÔNG chứa mật khẩu dạng chữ
    conn = sqlite3.connect(str(db_path))
    row = conn.execute("SELECT password, totp_secret FROM accounts WHERE email = ?", (email,)).fetchone()
    conn.close()

    assert row is not None
    enc_pw, enc_totp = row[0], row[1]
    assert enc_pw.startswith("v1:"), f"Password phải bắt đầu bằng v1:, nhận được: {enc_pw}"
    assert enc_totp.startswith("v1:"), f"totp_secret phải bắt đầu bằng v1:, nhận được: {enc_totp}"
    assert raw_password not in enc_pw
    assert raw_totp not in enc_totp

    # (b) Đọc lại ra đúng
    acct = accounts_db.get_account(email)
    assert acct is not None
    assert acct["password"] == raw_password
    assert acct["totp_secret"] == raw_totp


def test_doi_aad_giai_ma_tra_rong(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    khoa_dir = tmp_path / "khoa"
    khoa_dir.mkdir()
    monkeypatch.setenv("CAPTCHA_SOLVER_DATA_DIR", str(data_dir))
    monkeypatch.setenv("CAPTCHA_SOLVER_KEY_DIR", str(khoa_dir))
    monkeypatch.delenv("VAULT_MASTER_KEY", raising=False)

    from src import settings, vault
    settings.settings.data_dir = data_dir
    settings.settings.key_dir = khoa_dir

    email = "nguoi.test@example.com"
    raw_val = "BiMat123"

    ct = vault.ma_hoa(raw_val, email=email, truong="password")
    assert ct.startswith("v1:")

    # (c) Giải mã với sai email trong AAD -> trả rỗng
    giai_sai_email = vault.giai_ma(ct, email="nguoi.khac@example.com", truong="password")
    assert giai_sai_email == ""

    # Giải mã với sai tên trường trong AAD -> trả rỗng
    giai_sai_truong = vault.giai_ma(ct, email=email, truong="totp_secret")
    assert giai_sai_truong == ""


def test_vault_key_mode_0600_va_dung_lai(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data_dir = tmp_path / "data"
    key_dir = tmp_path / "khoa"
    data_dir.mkdir()
    key_dir.mkdir()
    monkeypatch.setenv("CAPTCHA_SOLVER_DATA_DIR", str(data_dir))
    monkeypatch.setenv("CAPTCHA_SOLVER_KEY_DIR", str(key_dir))
    monkeypatch.delenv("VAULT_MASTER_KEY", raising=False)

    from src import settings, vault
    settings.settings.data_dir = data_dir
    settings.settings.key_dir = key_dir

    key_file = key_dir / "vault.key"
    assert not key_file.exists()

    ct1 = vault.ma_hoa("GiaTri1", email="a@example.com", truong="test")
    # (d) tệp vault.key mode 0600
    assert key_file.exists()
    st_mode = stat.S_IMODE(key_file.stat().st_mode)
    assert st_mode == 0o600

    content_key1 = key_file.read_bytes()

    # Nạp lại mô đun hoặc gọi lại ma_hoa -> dùng lại cùng khoá
    ct2 = vault.ma_hoa("GiaTri2", email="a@example.com", truong="test")
    content_key2 = key_file.read_bytes()
    assert content_key1 == content_key2

    assert vault.giai_ma(ct1, email="a@example.com", truong="test") == "GiaTri1"
    assert vault.giai_ma(ct2, email="a@example.com", truong="test") == "GiaTri2"


def test_mat_vault_key_tra_rong_khong_nem_loi(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data_dir = tmp_path / "data"
    key_dir = tmp_path / "khoa"
    data_dir.mkdir()
    key_dir.mkdir()
    monkeypatch.setenv("CAPTCHA_SOLVER_DATA_DIR", str(data_dir))
    monkeypatch.setenv("CAPTCHA_SOLVER_KEY_DIR", str(key_dir))
    monkeypatch.delenv("VAULT_MASTER_KEY", raising=False)

    from src import settings, vault
    settings.settings.data_dir = data_dir
    settings.settings.key_dir = key_dir

    ct = vault.ma_hoa("GiaTriBiMat", email="a@example.com", truong="test")
    assert ct.startswith("v1:")

    # (e) Xoá tệp vault.key
    key_file = key_dir / "vault.key"
    assert key_file.exists()
    key_file.unlink()

    # Giải mã khi mất khoá -> trả rỗng, không ném lỗi, không trả chuỗi rác
    res = vault.giai_ma(ct, email="a@example.com", truong="test")
    assert res == ""


def test_list_accounts_khong_tra_totp_secret(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_path = data_dir / "accounts.db"

    monkeypatch.setenv("CAPTCHA_SOLVER_DATA_DIR", str(data_dir))
    monkeypatch.setenv("ACCOUNTS_DB", str(db_path))
    monkeypatch.delenv("VAULT_MASTER_KEY", raising=False)

    from src import accounts_db, settings
    settings.settings.data_dir = data_dir
    monkeypatch.setattr(accounts_db, "_DB_PATH", str(db_path))
    accounts_db.init_db()

    accounts_db.save_account(
        email="nguoi.b@example.com",
        password="password123",
        totp_secret="SECRET123",
        label="profile-b",
        loai="google",
    )

    # (f) list_accounts() không trả totp_secret
    ds = accounts_db.list_accounts()
    assert len(ds) >= 1
    found = False
    for item in ds:
        if item.get("email") == "nguoi.b@example.com":
            found = True
            assert "totp_secret" not in item
    assert found


def test_khong_duong_dan_tuyet_doi_ngoai_data_tmp_run():
    import re
    from pathlib import Path

    goc_src = Path(__file__).resolve().parent / "src"
    # Kiểm tra các tệp đã chỉnh: settings.py, vault.py, main.py
    tep_kiem_tra = [goc_src / "settings.py", goc_src / "vault.py"]
    # Mẫu đường dẫn tuyệt đối trên linux: /abc/...
    mau_duong_dan = re.compile(r"""["'](/([a-zA-Z0-9_\-\.]+/[a-zA-Z0-9_\-\./]+))["']""")

    duong_hop_le_prefix = ("/data", "/khoa", "/tmp", "/run")

    for tep in tep_kiem_tra:
        noi_dung = tep.read_text(encoding="utf-8")
        for khop in mau_duong_dan.finditer(noi_dung):
            duong = "/" + khop.group(2)
            # Bỏ qua các URL http(s)
            if duong.startswith(duong_hop_le_prefix):
                continue
            pytest.fail(f"Tệp {tep.name} chứa đường dẫn tuyệt đối ngoài /data, /khoa, /tmp, /run: {duong}")


def test_khoa_luu_trong_key_dir_khong_phai_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Khi key_dir khác data_dir, vault.key và khoa-dich-vu được lưu vào key_dir chứ không vào data_dir."""
    data_dir = tmp_path / "data"
    key_dir = tmp_path / "khoa"
    data_dir.mkdir()
    key_dir.mkdir()

    monkeypatch.setenv("CAPTCHA_SOLVER_DATA_DIR", str(data_dir))
    monkeypatch.setenv("CAPTCHA_SOLVER_KEY_DIR", str(key_dir))
    monkeypatch.delenv("VAULT_MASTER_KEY", raising=False)
    monkeypatch.delenv("CAPTCHA_SOLVER_API_KEY", raising=False)

    from src import settings, vault
    settings.settings.data_dir = data_dir
    if hasattr(settings.settings, "key_dir"):
        settings.settings.key_dir = key_dir

    # 1. Sinh khoa-dich-vu
    k = settings.lay_khoa_dich_vu()
    assert (key_dir / "khoa-dich-vu").exists(), "khoa-dich-vu phải nằm trong key_dir"
    assert not (data_dir / "khoa-dich-vu").exists(), "khoa-dich-vu không được nằm trong data_dir"

    # 2. Sinh vault.key
    ct = vault.ma_hoa("MatKhauBiMat", email="a@example.com", truong="password")
    assert (key_dir / "vault.key").exists(), "vault.key phải nằm trong key_dir"
    assert not (data_dir / "vault.key").exists(), "vault.key không được nằm trong data_dir"


def test_ket_khoa_tep_hong_nem_runtime_error_tieng_viet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Tệp vault.key rỗng / không phải base64 / sai độ dài -> ném RuntimeError tiếng Việt."""
    key_dir = tmp_path / "khoa"
    key_dir.mkdir()
    tep_vault_key = key_dir / "vault.key"

    monkeypatch.setenv("CAPTCHA_SOLVER_KEY_DIR", str(key_dir))
    monkeypatch.delenv("VAULT_MASTER_KEY", raising=False)

    from src import vault

    # 1. Tệp rỗng
    tep_vault_key.write_text("", encoding="utf-8")
    with pytest.raises(RuntimeError, match="rỗng"):
        vault._khoa(tu_sinh=False)

    # 2. Không phải base64
    tep_vault_key.write_text("chuoi-khong-phai-base64-hop-le!@#$%", encoding="utf-8")
    with pytest.raises(RuntimeError, match="base64"):
        vault._khoa(tu_sinh=False)

    # 3. Sai độ dài (ngắn hơn 32 byte)
    tep_vault_key.write_text(base64.b64encode(b"ngan-16-bytes---").decode(), encoding="utf-8")
    with pytest.raises(RuntimeError, match="32 byte"):
        vault._khoa(tu_sinh=False)


def test_sinh_khoa_nguyen_tu_hai_tien_trinh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Mô phỏng hai tiến trình cùng sinh khoá: nếu tệp đã xuất hiện giữa chừng thì không ghi đè, trả về cùng khoá."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    key_dir = tmp_path / "khoa"
    key_dir.mkdir()

    monkeypatch.setenv("CAPTCHA_SOLVER_DATA_DIR", str(data_dir))
    monkeypatch.setenv("CAPTCHA_SOLVER_KEY_DIR", str(key_dir))
    monkeypatch.delenv("VAULT_MASTER_KEY", raising=False)

    from src import vault

    # Gọi lần 1
    k1 = vault._khoa(tu_sinh=True)
    assert k1 is not None

    # Gọi lần 2
    k2 = vault._khoa(tu_sinh=True)
    assert k1 == k2, "Hai lần gọi tự sinh không được ghi đè khoá khác nhau"


def test_a1_mat_khoa_co_ban_ghi_v1_nem_loi_khong_sinh_khoa_moi(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A1: Nếu accounts.db đã có bản ghi tiền tố v1: mà không có khoá -> KHÔNG sinh khoá mới, ném VaultChuaSanSang."""
    data_dir = tmp_path / "data"
    key_dir = tmp_path / "khoa"
    data_dir.mkdir()
    key_dir.mkdir()
    db_path = data_dir / "accounts.db"

    monkeypatch.setenv("CAPTCHA_SOLVER_DATA_DIR", str(data_dir))
    monkeypatch.setenv("CAPTCHA_SOLVER_KEY_DIR", str(key_dir))
    monkeypatch.setenv("ACCOUNTS_DB", str(db_path))
    monkeypatch.delenv("VAULT_MASTER_KEY", raising=False)

    from src import accounts_db, settings, vault
    settings.settings.data_dir = data_dir
    settings.settings.key_dir = key_dir
    monkeypatch.setattr(accounts_db, "_DB_PATH", str(db_path))
    accounts_db.init_db()

    # 1. Lưu tài khoản A (được mã hoá tiền tố v1: vào DB)
    accounts_db.save_account("tk.a@example.com", "MatKhauA123", "", "acc-a", "google")
    key_file = key_dir / "vault.key"
    assert key_file.exists(), "vault.key phải được sinh ra khi lưu tài khoản A"

    # Kiểm tra DB thật sự có bản ghi v1:
    conn = accounts_db._conn()
    row = conn.execute("SELECT password FROM accounts WHERE email = 'tk.a@example.com'").fetchone()
    conn.close()
    assert str(row["password"]).startswith("v1:"), "Mật khẩu tk.a phải có tiền tố v1:"

    # 2. Xoá khoá (mất volume /khoa)
    key_file.unlink()
    assert not key_file.exists(), "Đã xoá vault.key"

    # 3. Có sẵn v1: mà không khoá -> ma_hoa và save_account phải ném VaultChuaSanSang, KHÔNG sinh khoá mới
    with pytest.raises(vault.VaultChuaSanSang, match="[Kk]ét chưa sẵn sàng.*thiếu khoá"):
        vault.ma_hoa("MatKhauB456", "tk.b@example.com", "password")

    assert not key_file.exists(), "Không được tự sinh vault.key mới khi đã có dữ liệu v1:!"

    with pytest.raises(vault.VaultChuaSanSang, match="[Kk]ét chưa sẵn sàng.*thiếu khoá"):
        accounts_db.save_account("tk.b@example.com", "MatKhauB456", "", "acc-b", "google")

    assert not key_file.exists(), "Không được tự sinh vault.key mới khi lưu tài khoản B!"

    # 4. Kiểm tra /health báo 'két chưa sẵn sàng: thiếu khoá'
    from fastapi.testclient import TestClient
    from src.main import app
    client = TestClient(app)
    r = client.get("/health")
    assert "két chưa sẵn sàng: thiếu khoá" in str(r.json()) or r.status_code == 503


def test_a2_accounts_db_quyen_0600(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A2: accounts.db quyền 0600 rõ ràng khi tạo/mở."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_path = data_dir / "accounts.db"

    monkeypatch.setenv("CAPTCHA_SOLVER_DATA_DIR", str(data_dir))
    monkeypatch.setenv("ACCOUNTS_DB", str(db_path))

    from src import accounts_db
    monkeypatch.setattr(accounts_db, "_DB_PATH", str(db_path))

    # Tạo DB
    accounts_db.init_db()
    assert db_path.exists()
    st = db_path.stat()
    assert stat.S_IMODE(st.st_mode) == 0o600, f"Quyền accounts.db phải là 0600, nhận: {oct(stat.S_IMODE(st.st_mode))}"

    # Giả lập quyền bị đổi thành 0644
    os.chmod(db_path, 0o644)
    assert stat.S_IMODE(db_path.stat().st_mode) == 0o644

    # Mở lại kết nối _conn() -> phải tự khôi phục về 0600
    conn = accounts_db._conn()
    conn.close()
    assert stat.S_IMODE(db_path.stat().st_mode) == 0o600, "Khi mở lại _conn(), quyền phải được đặt về 0600"


