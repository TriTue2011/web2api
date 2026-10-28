"""Trường chỉ để HIỂN THỊ không được coi là quyền (28/10/2026: thêm `nhom_con` làm mọi module nhóm Tiện ích «xin thêm quyền» và
không cập nhật được). Và: mọi trường cấp trên cùng của manifest trong kho mà vẫn là «quyền» phải nằm trong danh sách ĐÃ XEM XÉT —
thêm một trường mới vào manifest mà chưa phân loại thì test này đỏ ngay ở CI, không chờ chủ máy gặp ở bảng duyệt."""

import glob
import json
from pathlib import Path

from app.quyen_module import _TRUONG_KHONG_QUYEN, diff_quyen, tap_quyen

GOC = Path(__file__).resolve().parents[1]

#: Trường cấp trên cùng mà manifest trong kho đang dùng và VẪN là quyền (đổi → phải duyệt), đã xem xét từng trường:
#: hang = hạng duyệt (tự hạ hạng là leo thang); dang_nhap_bang/webhook/quyen/thuc_the/cong_dung/duong/hoi_vong = khả năng hay tài nguyên;
#: model_tinh = danh sách model tĩnh module khai; tac_gia/nguon/giao_dien/nha_cung_cap = thông tin nguồn gốc (đổi đáng chú ý).
DA_XEM_XET_LA_QUYEN = frozenset({
    "hang", "dang_nhap_bang", "model_tinh", "quyen", "webhook", "duong", "hoi_vong", "cong_dung", "nha_cung_cap",
    "thuc_the", "tac_gia", "nguon", "giao_dien",
})


def _mf(**them):
    return {"id": "x", "ten": "X", "phien_ban": "1.0.0", "hang": "A", "loai": "ai", "cung_cap": ["chat"], **them}


def test_them_nhom_con_khong_la_quyen_moi():
    cu = _mf()
    moi = _mf(nhom_con={"id": "tro-ly", "ten": "Trợ lý", "thu_tu": 3})
    assert diff_quyen(cu, moi) == {"them": [], "bot": []}
    assert tap_quyen(cu) == tap_quyen(moi)


def test_doi_hang_van_la_quyen():
    """Hạng duyệt là khai báo bảo mật: không được lọt vào danh sách «không phải quyền»."""
    assert "hang" not in _TRUONG_KHONG_QUYEN
    assert diff_quyen(_mf(hang="B"), _mf(hang="A"))["them"], "hạ hạng B→A phải bị coi là thay đổi quyền"


def test_moi_truong_trong_kho_da_duoc_phan_loai():
    chua = {}
    for f in sorted(glob.glob(str(GOC / "modules" / "*" / "manifest.json"))):
        m = json.loads(Path(f).read_text(encoding="utf-8"))
        for khoa in m:
            if khoa not in _TRUONG_KHONG_QUYEN and khoa != "tab_web" and khoa not in DA_XEM_XET_LA_QUYEN:
                chua.setdefault(khoa, Path(f).parent.name)
    assert not chua, (f"trường manifest chưa phân loại (mới thêm?): {chua}. Trường chỉ để hiển thị → thêm vào "
                      "app/quyen_module._TRUONG_KHONG_QUYEN; trường cấp khả năng → thêm vào DA_XEM_XET_LA_QUYEN ở đây.")
