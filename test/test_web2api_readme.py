"""README không được lệch mã — cơ chế «bổ sung dần»: thêm module/đường/biến mà quên tài liệu là đỏ.

Mọi thứ đối chiếu được LẤY TỪ MÃ (app.routes, manifest trên đĩa, grep WEB2API_*), không từ một
danh sách gõ tay trong test — danh sách gõ tay sẽ lệch y như tài liệu. Kiểm tra trên gộp cả README.md
(hướng dẫn gọn) và docs/THAM-CHIEU.md (tài liệu tham chiếu chi tiết).
"""

import re
from pathlib import Path

from starlette.routing import Mount

from app.main import app

GOC = Path(__file__).resolve().parents[1]  # gốc kho web2api — mọi đường trong README tính từ đây
README = (GOC / "README.md").read_text(encoding="utf-8")
THAM_CHIEU = (GOC / "docs" / "THAM-CHIEU.md").read_text(encoding="utf-8")
NOI_DUNG = README + "\n" + THAM_CHIEU


def _khoi_ma(ngon_ngu: str | None = None) -> list[str]:
    ds = re.findall(r"```(\w*)\n(.*?)```", NOI_DUNG, flags=re.S)
    return [noi_dung for nn, noi_dung in ds if ngon_ngu is None or nn == ngon_ngu]


def _mau_duong(path: str) -> re.Pattern:
    """Đường của route → regex khớp cách README viết tham số: {id}, <id>, <tệp>…"""
    phan = re.split(r"\{[^}]+\}", path)
    return re.compile(r"[{<][^}>\s]+[}>]".join(re.escape(p) for p in phan) + r"(?![\w-])")


def _mau_goi(path: str) -> re.Pattern:
    """Đường của route → regex khớp một lời gọi THẬT (tham số đã điền giá trị, vd /api/modules/gemini-api)."""
    ra = re.escape(path)
    ra = re.sub(r"\\\{[^}]*:path\\\}", ".+", ra)
    ra = re.sub(r"\\\{[^}]*\\\}", "[^/]+", ra)
    return re.compile(ra)


def _cac_duong(routes=None, tien_to: str = "") -> list[str]:
    """Mọi đường của app (trừ Mount). FastAPI ≥ 0.140 không còn gộp phẳng route của router con vào
    `app.routes` mà bọc chúng trong một mục có `original_router` — phải đi vào trong mới thấy."""
    ra = []
    for r in app.routes if routes is None else routes:
        if isinstance(r, Mount):
            continue
        if getattr(r, "path", None):
            ra.append(tien_to + r.path)
        elif hasattr(r, "original_router"):
            ra += _cac_duong(r.original_router.routes, tien_to + r.include_context.prefix)
    assert ra, "không lấy được đường nào từ app.routes"
    return ra


def test_anh_nhung_ton_tai_trong_docs_anh():
    anh_readme = [(duong, GOC / duong, README) for duong in re.findall(r"!\[[^\]]*\]\(([^)\s]+)\)", README)]
    anh_tc = [(duong, GOC / "docs" / duong, THAM_CHIEU) for duong in re.findall(r"!\[[^\]]*\]\(([^)\s]+)\)", THAM_CHIEU)]
    tat_ca = anh_readme + anh_tc
    assert tat_ca, "phải có ảnh màn hình trong tài liệu"
    for duong, p, nguon in tat_ca:
        p_res = p.resolve()
        assert p_res.is_file(), f"ảnh {duong} không tồn tại"
        assert p_res.parent == (GOC / "docs" / "anh").resolve(), f"ảnh {duong} phải nằm trong docs/anh/"
        assert p_res.stat().st_size <= 250 * 1024, f"ảnh {duong} vượt 250 KB"
        # Mỗi ảnh có chú thích (dòng nghiêng) ngay dưới.
        sau = nguon.split(f"]({duong})", 1)[1].lstrip("\n")
        assert sau.startswith("*"), f"ảnh {duong} thiếu chú thích"


def test_moi_module_co_tieu_muc_va_dong_trang_thai():
    manifests = list((GOC / "app" / "modules").glob("*/manifest.json")) + list((GOC / "modules").glob("*/manifest.json"))
    assert manifests, "phải có ít nhất module gemini-api trong modules/"
    import json

    for p in manifests:
        id_ = json.loads(p.read_text(encoding="utf-8"))["id"]
        assert re.search(rf"^### .*\b{re.escape(id_)}\b", NOI_DUNG, flags=re.M), f"thiếu tiểu mục cho module {id_}"
        assert re.search(rf"^\|.*`{re.escape(id_)}`.*\|", NOI_DUNG, flags=re.M), f"thiếu dòng trạng thái cho {id_}"


def test_moi_duong_api_co_trong_readme():
    thieu = [d for d in _cac_duong() if not _mau_duong(d).search(NOI_DUNG)]
    assert not thieu, f"tài liệu chưa nhắc các đường: {thieu}"


def test_moi_bien_moi_truong_co_trong_readme():
    bien = set()
    for p in list((GOC / "app").rglob("*.py")) + list((GOC / "modules").rglob("*.py")) + [f for f in (GOC / "scripts").glob("*") if f.is_file()]:
        bien |= set(re.findall(r"WEB2API_[A-Z0-9_]+", p.read_text(encoding="utf-8")))
    assert bien >= {"WEB2API_DATA", "WEB2API_ADMIN_KEY"}
    thieu = sorted(b for b in bien if b not in NOI_DUNG)
    assert not thieu, f"tài liệu chưa nhắc biến: {thieu}"


def test_lenh_mau_khong_tro_toi_tep_hay_duong_khong_co():
    mau = [_mau_goi(d) for d in _cac_duong()]
    khoi = _khoi_ma("bash")
    assert khoi
    for noi_dung in khoi:
        lenh = noi_dung.replace("\\\n", " ")
        for dong in lenh.splitlines():
            if not re.search(r"\b(docker|curl|pip|pytest)\b", dong) and "chup-man-hinh" not in dong:
                continue
            # Đường dẫn tệp trong kho (tính từ gốc kho), kể cả tệp ở gốc như requirements-dev.txt.
            for tep in re.findall(r"(?<![\w/.$-])((?:app|web|modules|scripts|docs|deploy|test)/[\w./-]*\w"
                                  r"|[\w-]+\.(?:txt|yml|sh))(?![\w/])", dong):
                assert (GOC / tep).exists(), f"lệnh trỏ tới {tep} không tồn tại: {dong.strip()}"
            # Đường gọi WEB2API (sau $W hoặc …:3031) phải là route thật. URL nằm trong thân JSON
            # (vd base_url của module từ xa) là địa chỉ dịch vụ khác, không kiểm.
            for duong in re.findall(r"(?:\$W|https?://[^/\s'\"]*:3031)(/[^\s'\"]*)", dong):
                duong = duong.split("?")[0]
                assert any(m.fullmatch(duong) for m in mau), f"lệnh gọi đường không có: {duong} ({dong.strip()})"
    # Thư mục được docker build/compose dùng phải có thật.
    assert (GOC / "app").is_dir() and (GOC / "web").is_dir()


def test_mau_tieu_muc_va_script_chup_ton_tai():
    assert (GOC / "docs" / "THAM-CHIEU.md").is_file()
    assert (GOC / "docs" / "MAU-TIEU-MUC-MODULE.md").is_file()
    sc = GOC / "scripts" / "chup-man-hinh.sh"
    assert sc.is_file() and sc.stat().st_mode & 0o111


def test_readme_gon_gioi_han_dong():
    dong = README.splitlines()
    assert len(dong) <= 400, f"README.md có {len(dong)} dòng, vượt trần 400 dòng"
    dong_en = (GOC / "README.en.md").read_text(encoding="utf-8").splitlines()
    assert len(dong_en) <= 400, f"README.en.md có {len(dong_en)} dòng, vượt trần 400 dòng"
