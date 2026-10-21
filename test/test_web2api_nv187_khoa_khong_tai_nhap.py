"""NV187 — kiểm toán «giữ khoá không tái nhập rồi gọi mã ngoài» (lớp tự khoá chết).

Bối cảnh (đo thật, NV185): bộ test đầy đủ TREO vô hạn (futex_wait). Ngăn xếp:
``ham_khoa.lay_khoa_chinh`` giữ ``_KHOA_LUONG`` (``threading.Lock``, không tái nhập) rồi gọi
``nhat_ky.ghi`` → ``bien_loi.che_chuoi`` → ``xac_thuc.khoa_quan_tri()`` →
``lay_khoa_chinh()`` → chờ chính khoá đang giữ. Điều phối đã vá riêng chỗ này
(commit 95381c9); test này bảo đảm KHÔNG còn chỗ cùng lớp trong ``app/``,
``modules/*``, ``dich-vu/`` và bắt lỗi tương tự ở module MỚI sau này.

Nguyên tắc: một khoá KHÔNG tái nhập (``threading.Lock``/``asyncio.Lock``, không phải
``RLock``) mà thân ``with`` gọi mã ngoài có thể quay lại ĐÚNG khoá đó thì luồng/
task tự treo. Mọi ``log.*`` đều đáng ngờ vì bộ lọc che bí mật toàn cục
(``che_log``) chạy ``che_chuoi`` → ``khoa_quan_tri`` → ``lay_khoa_chinh`` →
giữ ``ham_khoa._KHOA_LUONG``.
"""

from __future__ import annotations

import ast
from pathlib import Path

GOC = Path(__file__).resolve().parent.parent
THU_MUC_QUET = ("app", "modules", "dich-vu")

# (đường dẫn tương đối, tên hàm): lý do đã kiểm chứng thủ công (đọc chuỗi gọi),
# vì sao gọi nhat_ky.ghi/log.* trong with khoá không tái nhập mà KHÔNG quay lại
# đúng khoá đang giữ. Thêm mục mới vào đây PHẢI kèm lý do kiểm chứng, không thêm
# bừa cho qua test.
NGOAI_LE_DA_DUYET: dict[tuple[str, str], str] = {
    ("app/combo.py", "chiem"):
        "self._lock là khoá riêng từng BoCauChi; nhat_ky.ghi → che_chuoi → "
        "khoa_quan_tri → lay_khoa_chinh chỉ giữ ham_khoa._KHOA_LUONG (khoá module "
        "khác), không chạm tới self._lock → không quay lại cùng khoá.",
    ("app/combo.py", "record_success"):
        "Như chiem: nhat_ky.ghi chạm khoá khác (ham_khoa._KHOA_LUONG), không quay lại self._lock.",
    ("app/combo.py", "_ghi_loi"):
        "che_chuoi/nhat_ky.ghi chạm khoá khác (ham_khoa._KHOA_LUONG qua chuỗi "
        "khoa_quan_tri → lay_khoa_chinh), không quay lại self._lock.",
    ("app/gioi_han.py", "kiem_tra"):
        "self._khoa_luong là khoá riêng từng BoGioiHan; nhat_ky.ghi chỉ giữ "
        "ham_khoa._KHOA_LUONG (khoá khác) → không quay lại cùng khoá.",
    ("app/gioi_han.py", "ghi_loi"):
        "Như kiem_tra: nhat_ky.ghi chạm khoá khác, không quay lại self._khoa_luong.",
    ("app/nguon_git.py", "chay_nen"):
        "log.warning qua filter → khoá khác (ham_khoa._KHOA_LUONG); các hàm được gọi "
        "(_dong_bo/_ghi_loi_nguon/_tu_dong/_luu_tt_nguon) không giữ self._khoa "
        "(mọi `async with self._khoa` đều ở phương thức công khai gọi trực tiếp) → "
        "không quay lại cùng khoá.",
    ("app/so_module.py", "go"):
        "nhat_ky.ghi → che_chuoi → khoa_quan_tri → lay_khoa_chinh chỉ giữ "
        "ham_khoa._KHOA_LUONG (khoá khác); kho.* dùng kho._KHOA (RLock); helper "
        "_nap_lai/_dong_bo_tu_xa ghi rõ «Gọi khi ĐANG giữ self.khoa» nên không tự "
        "giữ lại → không quay lại cùng khoá.",
    ("app/tu_lam_moi.py", "bao_loi_phien"):
        "log.* qua bộ lọc che_log toàn cục → che_chuoi → khoa_quan_tri → "
        "lay_khoa_chinh chỉ giữ ham_khoa._KHOA_LUONG (khoá module khác), không quay "
        "lại _khoa_luong của tu_lam_moi.",
    ("app/tuyen.py", "thuc_hien_thiet_lap"):
        "nhat_ky_mod.ghi chạm khoá khác (ham_khoa._KHOA_LUONG); _KHOA_THIET_LAP là "
        "khoá module của tuyen, chỉ dùng tại đây, các hàm được gọi ở module khác "
        "không chạm tới → không quay lại cùng khoá.",
    ("dich-vu/antigravity-cau-noi/cau_noi.py", "_lay_phien_ban"):
        "log.warning qua filter → khoá khác (ham_khoa._KHOA_LUONG); lay_be/ "
        "phan_tich_phien_ban không giữ _khoa_phien_ban → không quay lại cùng khoá.",
    ("dich-vu/antigravity-cau-noi/cau_noi.py", "_lay_models"):
        "Như _lay_phien_ban: log.warning chạm khoá khác, không quay lại _khoa_models.",
    ("dich-vu/antigravity-cau-noi/cau_noi.py", "v1_models_lam_moi"):
        "Như _lay_models: log.warning chạm khoá khác, không quay lại _khoa_models.",
    ("dich-vu/antigravity-cau-noi/cau_noi.py", "cap_nhat_han_muc"):
        "log.warning qua filter → khoá khác (ham_khoa._KHOA_LUONG); _chay là tiến "
        "trình con, phan_tich_usage thuần túy → không quay lại self._dang_doc.",
    ("dich-vu/trinh-duyet/server.py", "gianh_dieu_khien"):
        "log.info qua filter → khoá khác (ham_khoa._KHOA_LUONG), không quay lại self.khoa.",
    ("dich-vu/trinh-duyet/server.py", "nha_dieu_khien"):
        "Như gianh_dieu_khien: log.info chạm khoá khác, không quay lại self.khoa.",
    ("dich-vu/trinh-duyet/server.py", "mo"):
        "log.info/log.warning qua filter → khoá khác, không quay lại self.khoa.",
    ("dich-vu/trinh-duyet/server.py", "dong"):
        "Như mo: log.info chạm khoá khác, không quay lại self.khoa.",
    ("dich-vu/trinh-duyet/server.py", "don_idle"):
        "Như mo: log.info chạm khoá khác, không quay lại self.khoa.",
    ("modules/mqtt/mqtt_lop.py", "start"):
        "log.info qua filter → khoá khác (ham_khoa._KHOA_LUONG), không quay lại self._khoa.",
    ("modules/zte-e2633/module.py", "cai"):
        "log.warning qua filter → khoá khác, không quay lại self._khoa.",
    ("modules/zte-e2633/module.py", "_lay_thuc_the"):
        "log.warning qua filter → khoá khác, không quay lại self._khoa.",
}

# GHI CHÚ phân loại (không cần ngoại lệ): modules/register/module.py dùng
# threading.RLock cho self._khoa (tái nhập được) nên with-block gọi log.* là an
# toàn theo phân loại ở mục 1 spec — không đưa vào NGOAI_LE_DA_DUYET.


def _ban_khoa_khong_tai_nhap(tree: ast.AST) -> dict[str, str]:
    """Tên khoá -> 'Lock' (không tái nhập) / 'RLock'.

    Quét gán ở thân module (``_KHOA = threading.Lock()``) và gán thuộc tính
    (``self._khoa = threading.Lock()`` — thường trong ``__init__``), tra cứu theo
    tên thuộc tính. Ngoài ra nhận diện hai mẫu gián tiếp:
    (a) ``X[...] = threading.Lock()/asyncio.Lock()`` → vật chứa X giữ khoá, gán
        ``ten = X[...]`` cũng là khoá;
    (b) hàm nhà máy ``def f(...) -> asyncio.Lock:`` / ``-> threading.Lock`` →
        gán ``ten = f(...)`` cũng là khoá (vd zte-e2633: ``self._khoa =
        _lay_khoa_tien_to(self.tien_to)``).
    Tên vừa Lock vừa RLock trong cùng file → coi là Lock
    (fail-safe: kiểm tra chặt hơn).
    """
    ban: dict[str, str] = {}

    def _ghi(ten: str, loai: str) -> None:
        if ban.get(ten) == "RLock" and loai == "Lock":
            ban[ten] = "Lock"  # fail-safe
        else:
            ban.setdefault(ten, loai)

    chua_khoa: set[str] = set()  # tên vật chứa X trong X[...] = Lock()
    nha_may: dict[str, str] = {}  # tên hàm nhà máy -> 'Lock'/'RLock'

    for nut in ast.walk(tree):
        if isinstance(nut, (ast.FunctionDef, ast.AsyncFunctionDef)) and nut.returns:
            chu = ast.unparse(nut.returns)
            if chu in ("asyncio.Lock", "threading.Lock"):
                nha_may[nut.name] = "Lock"
            elif chu == "threading.RLock":
                nha_may[nut.name] = "RLock"
        if not (isinstance(nut, ast.Assign) and len(nut.targets) == 1):
            continue
        muc_tieu, gia_tri = nut.targets[0], nut.value
        # (a) X[...] = Lock()
        if isinstance(muc_tieu, ast.Subscript) and isinstance(muc_tieu.value, ast.Name) \
                and isinstance(gia_tri, ast.Call) and isinstance(gia_tri.func, ast.Attribute):
            ham = gia_tri.func
            goc = ham.value
            goc_ten = goc.id if isinstance(goc, ast.Name) else ""
            if (goc_ten == "threading" and ham.attr in ("Lock", "RLock")) \
                    or (goc_ten == "asyncio" and ham.attr == "Lock"):
                chua_khoa.add(muc_tieu.value.id)
            continue
        if not (isinstance(gia_tri, ast.Call)):
            continue
        ham = gia_tri.func
        loai = None
        ten_muc_tieu = None
        if isinstance(muc_tieu, ast.Name):
            ten_muc_tieu = muc_tieu.id
        elif isinstance(muc_tieu, ast.Attribute) and isinstance(muc_tieu.value, ast.Name) \
                and muc_tieu.value.id == "self":
            ten_muc_tieu = muc_tieu.attr
        if ten_muc_tieu is None:
            continue
        if isinstance(ham, ast.Attribute):
            goc = ham.value
            goc_ten = goc.id if isinstance(goc, ast.Name) else ""
            if goc_ten == "threading" and ham.attr in ("Lock", "RLock"):
                loai = ham.attr
            elif goc_ten == "asyncio" and ham.attr == "Lock":
                loai = "Lock"
        elif isinstance(ham, ast.Name) and ham.id in nha_may:
            # (b) ten = nha_may(...)
            loai = nha_may[ham.id]
        if loai is None and isinstance(gia_tri, ast.Subscript) \
                and isinstance(gia_tri.value, ast.Name) \
                and gia_tri.value.id in chua_khoa:
            loai = "Lock"  # vật chứa chỉ giữ khoá không tái nhập
        if loai is None:
            continue
        _ghi(ten_muc_tieu, loai)
    return ban


def _loai_khoa(ban: dict[str, str], bieu_thuc: str) -> str | None:
    txt = bieu_thuc.strip()
    if txt in ban:
        return ban[txt]
    # self._khoa / self.a._khoa -> tra theo tên thuộc tính cuối
    if "." in txt:
        cuoi = txt.split(".")[-1]
        if cuoi in ban:
            return ban[cuoi]
    return None


def _ban_bi_danh(cay: ast.AST) -> dict[str, str]:
    """Tên bí danh import trong file -> đường dẫn đầy đủ.

    Vd ``from app import nhat_ky as nk`` → {"nk": "app.nhat_ky"}; chống né kiểm
    tra bằng bí danh (đột biến M1: ``_nk_tam.ghi`` phải bị bắt như ``nhat_ky.ghi``).
    """
    ban: dict[str, str] = {}
    for nut in ast.walk(cay):
        if isinstance(nut, ast.Import):
            for bi in nut.names:
                ban[bi.asname or bi.name.split(".")[0]] = bi.name
        elif isinstance(nut, ast.ImportFrom) and nut.module:
            for bi in nut.names:
                if bi.name == "*":
                    continue
                ban[bi.asname or bi.name] = f"{nut.module}.{bi.name}"
    return ban


def _giai_quyet_bi_danh(van_ban: str, bi_danh: dict[str, str]) -> str:
    phan = van_ban.split(".")
    if phan[0] in bi_danh:
        phan[0] = bi_danh[phan[0]]
    return ".".join(phan)


def _goi_nguy_hiem(nut_goi: ast.Call, bi_danh: dict[str, str] | None = None) -> str | None:
    """Trả về tên gọi nguy hiểm nếu call chạm chuỗi che bí mật / nhật ký.

    Giải quyết bí danh import (``from app import nhat_ky as nk`` → ``nk.ghi``
    vẫn bị bắt) và gọi qua module logging (``logging.warning``).
    """
    ham = nut_goi.func
    van_ban = ast.unparse(ham)
    day_du = _giai_quyet_bi_danh(van_ban, bi_danh or {})
    if isinstance(ham, ast.Attribute) and isinstance(ham.value, ast.Name) \
            and ham.value.id == "log":
        return f"log.{ham.attr}"
    if day_du.split(".")[-2:] == ["logging", "warning"] or \
            (day_du.startswith("logging.") and day_du.split(".")[-1]
             in ("debug", "info", "warning", "error", "critical", "exception")):
        return day_du
    if isinstance(ham, ast.Attribute) and ham.attr == "ghi" and "nhat_ky" in day_du:
        return day_du  # nhat_ky.ghi / nhat_ky_mod.ghi / nk.ghi (bí danh)
    ten = day_du.split(".")[-1]
    if ten in ("che_chuoi", "khoa_quan_tri"):
        return day_du
    return None


def _quet_hit_tho() -> list[tuple[tuple[str, str], int, str, list[str]]]:
    """Mọi hit thô: ((đường dẫn, hàm), dòng with, biểu thức khoá, [gọi nguy hiểm])."""
    hit: list[tuple[tuple[str, str], int, str, list[str]]] = []
    for thu_muc in THU_MUC_QUET:
        for duong in sorted((GOC / thu_muc).rglob("*.py")):
            if "test" in duong.parts or duong.name.startswith("test_"):
                continue
            try:
                cay = ast.parse(duong.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            ban = _ban_khoa_khong_tai_nhap(cay)
            if not ban:
                continue
            bi_danh = _ban_bi_danh(cay)
            tuong_doi = str(duong.relative_to(GOC))
            for nut in ast.walk(cay):
                if not isinstance(nut, (ast.With, ast.AsyncWith)):
                    continue
                for muc in nut.items:
                    if _loai_khoa(ban, ast.unparse(muc.context_expr)) != "Lock":
                        continue
                    goi = sorted({
                        ten for nd in ast.walk(nut)
                        if isinstance(nd, ast.Call) and (ten := _goi_nguy_hiem(nd, bi_danh))
                    })
                    if not goi:
                        continue
                    ham_chua = None
                    for m in ast.walk(cay):
                        if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                                and m.lineno <= nut.lineno <= m.end_lineno:
                            if ham_chua is None or m.lineno >= ham_chua.lineno:
                                ham_chua = m
                    hit.append(((tuong_doi, ham_chua.name if ham_chua else "?"),
                                nut.lineno, ast.unparse(muc.context_expr).strip(), goi))
    return hit


def _quet_vi_pham() -> list[str]:
    vi_pham: list[str] = []
    for khoa, dong, ctx, goi in _quet_hit_tho():
        if khoa not in NGOAI_LE_DA_DUYET:
            vi_pham.append(
                f"{khoa[0]}:{dong} ({khoa[1]}) with {ctx} gọi {', '.join(goi)}"
            )
    return vi_pham


def test_khong_goi_nhat_ky_log_trong_with_khoa_khong_tai_nhap():
    """Mục 3 spec: thân ``with <khoá không tái nhập>:`` không gọi nhat_ky.ghi/log.*
    trừ danh sách ngoại lệ đã duyệt có lý do (NGOAI_LE_DA_DUYET)."""
    vi_pham = _quet_vi_pham()
    assert not vi_pham, (
        "Phát hiện gọi mã ngoài nguy hiểm trong with khoá KHÔNG tái nhập "
        "(lớp tự khoá chết NV185) — hoặc vá (nhả khoá trước khi gọi ngoài), hoặc "
        "kiểm chứng thủ công rồi thêm vào NGOAI_LE_DA_DUYET kèm lý do:\n"
        + "\n".join(vi_pham)
    )


def test_ngoai_le_deu_con_dung_duoc():
    """Mọi ngoại lệ trong NGOAI_LE_DA_DUYET phải còn khớp mã nguồn (chống mục chết)."""
    tap_hit_tho = {khoa for khoa, _, _, _ in _quet_hit_tho()}
    chet = [k for k in NGOAI_LE_DA_DUYET if k not in tap_hit_tho]
    assert not chet, (
        "Ngoại lệ không còn khớp mã nguồn (đã sửa/xoá mà quên dọn danh sách): "
        + ", ".join(f"{f}:{h}" for f, h in chet)
    )


def test_ham_khoa_khong_goi_nhat_ky_khi_giu_khoa_luong():
    """Ghim vá NV185: ``with _KHOA_LUONG:`` trong app/ham_khoa.py không gọi mã ngoài
    (nhat_ky.ghi/log.*/che_chuoi/khoa_quan_tri) — gọi là tự khoá chết như đã đo."""
    cay = ast.parse((GOC / "app" / "ham_khoa.py").read_text(encoding="utf-8"))
    bi_danh = _ban_bi_danh(cay)
    for nut in ast.walk(cay):
        if not isinstance(nut, (ast.With, ast.AsyncWith)):
            continue
        for muc in nut.items:
            if ast.unparse(muc.context_expr).strip() != "_KHOA_LUONG":
                continue
            goi = sorted({
                ten for nd in ast.walk(nut)
                if isinstance(nd, ast.Call) and (ten := _goi_nguy_hiem(nd, bi_danh))
            })
            assert not goi, (
                f"app/ham_khoa.py:{nut.lineno}: with _KHOA_LUONG gọi {', '.join(goi)} — "
                "tái hiện lớp tự khoá chết NV185 (treo vô hạn khi chưa cấu hình khoá chính)"
            )
