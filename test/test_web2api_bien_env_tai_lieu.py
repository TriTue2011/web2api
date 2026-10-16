"""Kiểm tra tính nhất quán giữa biến môi trường trong mã và tài liệu THAM-CHIEU.md.

Lớp lỗi: mã thêm biến môi trường (WEB2API_*) mà tài liệu không theo kịp, hoặc
tài liệu giữ lại biến mà mã không còn dùng (tài liệu nói hơn mã).

Nguyên tắc:
1. Tập biến trong mã (app/ + modules/) ⊆ tập biến trong bảng tra cứu docs/THAM-CHIEU.md mục 8.
   Bất kỳ biến WEB2API_* mới nào được đưa vào mã lõi hoặc module đều phải được ghi
   vào bảng mục 8 (kiểu, mặc định, ý nghĩa, thời điểm đọc).
2. Tập biến trong bảng docs/THAM-CHIEU.md mục 8 ⊆ tập biến trong mã + danh sách ngoại lệ
   được kiểm soát chặt chẽ (các biến chỉ dùng bởi docker-compose hoặc kịch bản kiểm thử).
"""

import re
from pathlib import Path

GOC = Path(__file__).resolve().parents[1]
THAM_CHIEU_PATH = GOC / "docs" / "THAM-CHIEU.md"

#: Danh sách ngoại lệ: biến có trong bảng docs/THAM-CHIEU.md mục 8 nhưng không nằm
#: trong mã Python của app/ và modules/ (được đọc bởi docker-compose hoặc test/scripts).
#: Mỗi biến bắt buộc phải có chú thích lý do cụ thể.
NGOAI_LE_DOCS_NGOAI_MA = {
    "WEB2API_BIND": "Chỉ đọc bởi docker-compose.yml: địa chỉ IP máy chủ để container lắng nghe",
    "WEB2API_CHROME": "Chỉ đọc bởi test giao diện (test/ui_chung.py) và kịch bản scripts/chup-man-hinh.sh: đường tới Chrome",
    "WEB2API_DATA_DIR": "Chỉ đọc bởi docker-compose.yml: thư mục dữ liệu trên máy chủ gắn vào container",
    "WEB2API_DEMO_DIR": "Chỉ đọc bởi test dc-lite (test/test_web2api_dc_lite.py): thư mục HTML demo ngoài kho",
    "WEB2API_PORT": "Chỉ đọc bởi docker-compose.yml: cổng dịch vụ trên máy chủ",
}

#: Ngoại lệ biến trong mã nhưng không cần có trong docs (nếu có; hiện tại rỗng vì mọi biến đều phải có trong docs).
NGOAI_LE_MA_NGOAI_DOCS: dict[str, str] = {}


def _cac_bien_trong_ma() -> set[str]:
    """Quét động mọi biến WEB2API_* trong app/ và modules/."""
    bien = set()
    cac_tep = list((GOC / "app").rglob("*.py")) + list((GOC / "modules").rglob("*.py"))
    assert cac_tep, "Phải tìm thấy các tệp mã Python trong app/ hoặc modules/"
    for p in cac_tep:
        txt = p.read_text(encoding="utf-8")
        for b in re.findall(r"\bWEB2API_[A-Z0-9_]+\b", txt):
            # Bỏ qua các chuỗi tiền tố trong chú thích (kết thúc bằng dấu gạch dưới, vd WEB2API_RL_*)
            if not b.endswith("_"):
                bien.add(b)
    return bien


def _cac_bien_trong_bang_docs() -> set[str]:
    """Trích xuất danh sách biến môi trường từ cột 1 của bảng mục 8 trong docs/THAM-CHIEU.md."""
    assert THAM_CHIEU_PATH.is_file(), "docs/THAM-CHIEU.md phải tồn tại"
    tc = THAM_CHIEU_PATH.read_text(encoding="utf-8")

    m = re.search(r"## 8\. Tra cứu: API và biến môi trường.*?(?=## 9\.|\Z)", tc, re.S)
    assert m, "Không tìm thấy mục 8 trong docs/THAM-CHIEU.md"
    muc8 = m.group(0)

    bang_m = re.search(r"\| Biến môi trường \| Mặc định \| Ý nghĩa \|.*?(?=\n\n|\n##|\Z)", muc8, re.S)
    assert bang_m, "Không tìm thấy bảng biến môi trường trong mục 8 docs/THAM-CHIEU.md"
    bang = bang_m.group(0)

    bien_bang = set()
    for dong in bang.splitlines()[2:]:
        dong = dong.strip()
        if not dong.startswith("|"):
            continue
        cot1 = dong.split("|")[1]
        for b in re.findall(r"`([A-Z0-9_]+)`", cot1):
            if b.startswith("WEB2API_"):
                bien_bang.add(b)

    assert bien_bang, "Bảng biến môi trường mục 8 không được rỗng"
    return bien_bang


def test_moi_bien_trong_ma_phai_co_trong_docs_muc_8():
    """Tập biến trong mã phải là tập con của tập biến trong bảng docs mục 8."""
    bien_ma = _cac_bien_trong_ma()
    bien_docs = _cac_bien_trong_bang_docs()

    assert "WEB2API_DATA" in bien_ma
    assert "WEB2API_ADMIN_KEY" in bien_ma

    thieu = sorted((bien_ma - set(NGOAI_LE_MA_NGOAI_DOCS)) - bien_docs)
    assert not thieu, (
        f"Lớp lỗi: mã thêm biến mà tài liệu không theo kịp!\n"
        f"Các biến sau có trong mã nhưng thiếu trong bảng mục 8 docs/THAM-CHIEU.md:\n"
        f"{thieu}\n"
        f"Hãy thêm từng biến vào bảng mục 8 (kiểu, mặc định đọc từ mã, ý nghĩa, đọc lúc nào: "
        f"mỗi lượt gọi hay lúc khởi động)."
    )


def test_docs_muc_8_khong_chua_bien_thua_khong_con_dung():
    """Tài liệu không nói hơn mã: biến trong docs mục 8 phải có trong mã hoặc ngoại lệ được duyệt."""
    bien_ma = _cac_bien_trong_ma()
    bien_docs = _cac_bien_trong_bang_docs()

    thua = sorted((bien_docs - bien_ma) - set(NGOAI_LE_DOCS_NGOAI_MA))
    assert not thua, (
        f"Lớp lỗi: tài liệu nói hơn mã!\n"
        f"Các biến sau có trong bảng mục 8 docs/THAM-CHIEU.md nhưng không còn được mã sử dụng:\n"
        f"{thua}\n"
        f"Nếu biến đã bị xoá khỏi mã, hãy xoá khỏi docs. Nếu là biến dùng ngoài app/modules, "
        f"hãy khai vào NGOAI_LE_DOCS_NGOAI_MA kèm lý do."
    )


def test_ngoai_le_docs_duoc_dong_bo_chinh_xac():
    """Danh sách ngoại lệ không chứa mục mồ côi và không chứa biến mà mã đã dùng thật."""
    bien_ma = _cac_bien_trong_ma()
    bien_docs = _cac_bien_trong_bang_docs()

    # Ngoại lệ phải thực sự xuất hiện trong docs
    thua_trong_ngoai_le = sorted(set(NGOAI_LE_DOCS_NGOAI_MA) - bien_docs)
    assert not thua_trong_ngoai_le, (
        f"Ngoại lệ chứa biến không có trong bảng docs mục 8: {thua_trong_ngoai_le}"
    )

    # Nếu một biến trong ngoại lệ được đưa vào app/ hoặc modules/, phải gỡ khỏi ngoại lệ
    da_co_trong_ma = sorted(set(NGOAI_LE_DOCS_NGOAI_MA) & bien_ma)
    assert not da_co_trong_ma, (
        f"Biến trong ngoại lệ nay đã được dùng trong mã app/modules, hãy xoá khỏi ngoại lệ: {da_co_trong_ma}"
    )
