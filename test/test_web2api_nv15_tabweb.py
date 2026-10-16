# NV15 bổ sung: tab_web false→true phải cần duyệt (XSS cùng origin → mất khoá admin)
from app.quyen_module import diff_quyen


def _manifest(tab_web):
    return {"id": "m1", "ten": "M", "tab_web": tab_web}


def test_tab_web_tat_bat_can_duyet():
    """tab_web false→true là tăng quyền (được chạy JS ở origin lõi) → vào 'them'."""
    d = diff_quyen(_manifest(False), _manifest(True))
    assert d["them"], "tab_web false→true phải cần duyệt"
    assert any(m["ten"] == "tab_web" for m in d["them"])


def test_tab_web_bat_tat_khong_can_duyet():
    """tab_web true→false là hạ quyền → không vào 'them'."""
    d = diff_quyen(_manifest(True), _manifest(False))
    assert not any(m["ten"] == "tab_web" for m in d["them"]), \
        "tab_web true→false là hạ quyền, không cần duyệt"


def test_tab_web_khong_doi_khong_bao():
    d = diff_quyen(_manifest(True), _manifest(True))
    assert not any(m["ten"] == "tab_web" for m in d["them"])
    d = diff_quyen(_manifest(False), _manifest(False))
    assert not any(m["ten"] == "tab_web" for m in d["them"])


def test_tab_web_thieu_mac_dinh_tat():
    """Thiếu tab_web coi như False (không có quyền)."""
    d = diff_quyen({"id": "m1"}, _manifest(True))
    assert any(m["ten"] == "tab_web" for m in d["them"])
