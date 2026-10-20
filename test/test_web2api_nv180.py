"""NV180 — hướng dẫn và số liệu phải ĐỦ và ĐỒNG BỘ.

Bắt các phát hiện «thiếu hướng dẫn / lệch dữ liệu» của NV155b mà test cơ học
(test_web2api_readme.py) không bắt được: mo_ta của trường bí mật phải nói
LẤY Ở ĐÂU / TỰ ĐẶT thế nào; README gốc phải kể đủ dịch vụ phụ trong dich-vu/;
tai_nguyen chưa đo thì phải ghi rõ «chưa đo» KÈM cách đo (không bịa số);
bảng trạng thái không được trùng hàng hay thiếu module của đợt.
"""

import json
import re
from pathlib import Path

GOC = Path(__file__).resolve().parents[1]
README = (GOC / "README.md").read_text(encoding="utf-8")
THAM_CHIEU = (GOC / "docs" / "THAM-CHIEU.md").read_text(encoding="utf-8")
MUC_1 = THAM_CHIEU.split("## 2.")[0]


def _manifest(mid: str) -> dict:
    return json.loads((GOC / "modules" / mid / "manifest.json").read_text(encoding="utf-8"))


def _mo_ta(mid: str, khoa: str) -> str:
    for truong in _manifest(mid)["truong_cau_hinh"]:
        if truong["khoa"] == khoa:
            return truong.get("mo_ta") or ""
    raise AssertionError(f"{mid} không có trường {khoa}")


def _hang_bang(van_ban: str, mid: str) -> int:
    """Số hàng bảng Markdown (| … `id` … | …) nhắc tới module."""
    return sum(
        1
        for dong in van_ban.splitlines()
        if dong.startswith("|") and f"`{mid}`" in dong
    )


# --- (1) mo_ta trường bí mật: nói rõ LẤY Ở ĐÂU / TỰ ĐẶT thế nào ---

def test_mo_ta_device_key_noi_cach_lay_khoa():
    mo_ta = _mo_ta("muse", "device_key")
    # Phải nói khoá gắn với đăng ký thiết bị bằng OTP và hiện chưa có cách lấy
    # tự động — người dùng mới đọc mo_ta là biết mình đang ở đâu.
    assert "OTP" in mo_ta
    assert "chưa" in mo_ta.lower()


def test_mo_ta_proxy_token_noi_cach_tu_dat():
    mo_ta = _mo_ta("gemini-api", "proxy_token")
    assert "wrangler secret put PROXY_TOKEN" in mo_ta


def test_mo_ta_api_keys_co_chu_thich_khoa_tra_phi():
    mo_ta = _mo_ta("gemini-api", "api_keys")
    assert "trả phí" in mo_ta
    assert "ảnh" in mo_ta and "video" in mo_ta.lower()


# --- (2) README gốc: đủ dịch vụ phụ + chạy nền cho cầu nối ---

def test_readme_goc_ke_du_dich_vu_phu_kem_link():
    thu_muc = sorted(p.name for p in (GOC / "dich-vu").iterdir() if p.is_dir())
    assert thu_muc, "không thấy thư mục dich-vu/"
    for ten in thu_muc:
        assert f"dich-vu/{ten}/README.md" in README, f"README gốc thiếu link README của {ten}"


def test_readme_goc_co_huong_dan_chay_nen_antigravity():
    assert "systemd" in README
    muc = README.split("antigravity-cau-noi", 1)[1][:6000] if "antigravity-cau-noi" in README else ""
    assert "chạy nền" in muc.lower()


# --- (3) tai_nguyen: không bịa số; chưa đo thì ghi cách đo ---

def test_tai_nguyen_dich_vu_nang_ghi_ro_chua_do_va_cach_do():
    for mid in ("vnc-xa", "van-phong"):
        tn = _manifest(mid)["tai_nguyen"]
        assert tn["ram_them_mb"] is None and tn["dia_mb"] is None, (
            f"{mid}: số phải là số ĐO THẬT — máy làm việc không có Docker nên để null"
        )
        ghi_chu = (tn.get("ghi_chu") or "").lower()
        assert "chưa đo" in ghi_chu, f"{mid}: ghi_chu phải nói rõ chưa đo"
        assert "docker images" in ghi_chu and "docker stats" in ghi_chu, (
            f"{mid}: ghi_chu phải nêu cách đo đĩa (docker images) và RAM (docker stats)"
        )


# --- (4) Đồng bộ bảng: không trùng hàng, không thiếu module đợt này ---

def test_bang_readme_goc_khong_trung_hang_module():
    for p in sorted((GOC / "modules").glob("*/manifest.json")):
        mid = json.loads(p.read_text(encoding="utf-8"))["id"]
        assert _hang_bang(README, mid) <= 1, f"README gốc trùng hàng {mid}"


def test_tham_chieu_muc_1_khong_trung_va_du_module_dot_nay():
    for mid in ("van-phong", "facebook", "thuthach", "tuya", "lich-ics",
                "chitieu", "hoa-don", "dich", "nhaptaikhoan",
                "tien-ich-doi-song", "rclone"):
        assert _hang_bang(MUC_1, mid) == 1, f"THAM-CHIEU mục 1: hàng {mid} phải có đúng 1"


def test_tham_chieu_muc_4_khong_trung_tieu_muc_module():
    tieu_muc = re.findall(r"^### Module (\S+)", THAM_CHIEU, flags=re.M)
    trung = {m for m in tieu_muc if tieu_muc.count(m) > 1}
    assert not trung, f"tiểu mục module bị trùng trong THAM-CHIEU: {trung}"
