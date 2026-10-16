# NV15 bổ sung: tab_web None→dict phải cần duyệt (XSS cùng origin → mất khoá admin)
from app.quyen_module import diff_quyen

TAB_WEB_THAT = {"nhan": "Quản lý module", "trang": "web/index.html"}


def _manifest(tab_web=None):
    m = {"id": "m1", "ten": "M"}
    if tab_web is not None:
        m["tab_web"] = tab_web
    return m


def test_tab_web_tat_bat_can_duyet():
    """tab_web None→dict là tăng quyền (được chạy JS ở origin lõi) → vào 'them'."""
    d = diff_quyen(_manifest(None), _manifest(TAB_WEB_THAT))
    assert d["them"], "tab_web None→dict phải cần duyệt"
    assert any(m["ten"] == "tab_web" for m in d["them"])


def test_tab_web_bat_tat_khong_can_duyet():
    """tab_web dict→None là hạ quyền → không vào 'them'."""
    d = diff_quyen(_manifest(TAB_WEB_THAT), _manifest(None))
    assert not any(m["ten"] == "tab_web" for m in d["them"]), \
        "tab_web dict→None là hạ quyền, không cần duyệt"


def test_tab_web_khong_doi_khong_bao():
    """Cùng có tab_web dict hoặc cùng không có thì không báo vào 'them'."""
    d = diff_quyen(_manifest(TAB_WEB_THAT), _manifest(TAB_WEB_THAT))
    assert not any(m["ten"] == "tab_web" for m in d["them"])
    d = diff_quyen(_manifest(None), _manifest(None))
    assert not any(m["ten"] == "tab_web" for m in d["them"])


def test_tab_web_thieu_mac_dinh_tat():
    """Thiếu tab_web coi như None (không có quyền), chuyển sang có dict tab_web phải vào 'them'."""
    d = diff_quyen({"id": "m1"}, _manifest(TAB_WEB_THAT))
    assert any(m["ten"] == "tab_web" for m in d["them"])
