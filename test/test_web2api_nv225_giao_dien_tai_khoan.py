"""NV225 — Giao diện «Tài khoản»: nhóm → loại → đích dùng, hạn mức, «đang dùng #1».

ĐO (bước a, grep/đọc mã trước khi sửa):
- Backend: GET /api/tai-khoan/nhom-loai (NV218) gom nhóm → loại + cờ
  ``dang_dung`` từng tài khoản; /han-muc, /luong-dung có sẵn;
  POST /da-luu/{id}/tam-dung có sẵn (NV197b). NHƯNG chưa có đường nào ĐỔ
  BẢNG phân loại (NHOM_AI / LOAI_TAI_KHOAN_THEO_NHOM /
  DICH_DUNG_THEO_NHOM_LOAI) cho giao diện → ô chọn nhóm→loại→đích dùng
  của form thêm tài khoản không đổ từ API được.
- web/He-thong-AI.dc.html: mục «Tài khoản đã lưu» (NV144) là danh sách
  PHẲNG + lọc theo nhóm; hàng hiện lượng dùng nhưng KHÔNG có dấu
  «ĐANG DÙNG #1», hạn mức chỉ hiện khi đã có số liệu (không có «chưa rõ»);
  form thêm chưa có ô chọn nhóm→loại→đích dùng; dlLuu không gửi
  loai_tai_khoan/dich_dung.
- Ô «Đăng nhập bằng» thuộc NV223 (chưa nộp tại thời điểm làm NV225) —
  test này KHÔNG khẳng định gì về nó.

LÀM (b):
- GET /api/tai-khoan/bang-phan-loai: đổ đúng 3 bảng khai báo của kho
  (chỉ vai quản trị, như mọi đường /api/tai-khoan/*).
- Trang: danh sách gom theo 5 nhóm → loại (đủ 5 nhóm kể cả rỗng + xô
  «Chưa phân nhóm» cho tài khoản ngoài bảng), mỗi tài khoản hiện nhãn,
  gói, hạn mức («chưa rõ» khi khong_biet — không bịa số), dấu
  «ĐANG DÙNG #1» theo cờ dang_dung của /nhom-loai, nút tạm dừng/bật lại;
  form thêm: 3 ô chọn xếp tầng nhóm→loại→đích dùng đổ từ API,
  dlLuu gửi loai_tai_khoan/dich_dung.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

_KHOA_QT = "khoa-qt-nv225-do-0123456789abcdef"
_QT = {"Authorization": f"Bearer {_KHOA_QT}"}
TRANG = Path(__file__).resolve().parent.parent / "web" / "He-thong-AI.dc.html"


@pytest.fixture()
def moi_truong(tmp_path, monkeypatch):
    """Lõi THẬT: app đầy đủ + SoModule rỗng, dữ liệu cô lập trong tmp."""
    from fastapi.testclient import TestClient

    from app.main import app
    from app.so_module import SoModule

    data_dir = tmp_path / "data-loi"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", _KHOA_QT)
    monkeypatch.setenv("WEB2API_DANG_NHAP_GIAN_CACH_GIAY", "0")
    with TestClient(app) as client:
        so = SoModule()
        client.app.state.so = so
        yield client, so


def _cai_module(so, module_id, nhom_id=None):
    async def dang_nhap(dong):
        return SimpleNamespace(thanh_cong=True, can_nguoi=False, ma_loi="")

    man = {"id": module_id}
    if nhom_id is not None:
        man["nhom"] = {"id": nhom_id, "ten": nhom_id}
    so.catalog[module_id] = SimpleNamespace(tien_to=module_id, manifest=man)
    so.dang_chay[module_id] = SimpleNamespace(manifest=man, dang_nhap=dang_nhap)


def _them(client, email, **them):
    body = {"email": email, "mat_khau": "MatKhauGia123!"}
    body.update(them)
    r = client.post("/api/tai-khoan/da-luu", headers=_QT, json=body)
    assert r.status_code == 200, r.text
    return r.json()["tai_khoan"]


# ── (1) API đổ bảng phân loại ────────────────────────────────────────────────

def test_bang_phan_loai_do_dung_3_bang(moi_truong):
    """ĐỎ trước sửa: chưa có /bang-phan-loai (404). Sau sửa: 3 bảng khớp kho."""
    from app import kho_tai_khoan as kho

    client, _so = moi_truong
    r = client.get("/api/tai-khoan/bang-phan-loai", headers=_QT)
    assert r.status_code == 200, r.text
    j = r.json()
    assert list(j["nhom"]) == list(kho.NHOM_AI)
    assert set(j["nhom"]) == {"google", "chatgpt", "grok", "claude", "muse"}
    assert j["loai_theo_nhom"] == {
        n: list(ds) for n, ds in kho.LOAI_TAI_KHOAN_THEO_NHOM.items()
    }
    dich = {
        f"{nhom}|{loai}": list(ds)
        for (nhom, loai), ds in kho.DICH_DUNG_THEO_NHOM_LOAI.items()
    }
    assert j["dich_dung_theo_nhom_loai"] == dich
    # Ví dụ chốt theo thiết kế chủ máy: google/gmail → gemini + antigravity;
    # chatgpt/web → web + codex (Codex là ĐÍCH của loại web, không phải nhóm).
    assert j["dich_dung_theo_nhom_loai"]["google|gmail"] == ["gemini", "antigravity"]
    assert j["dich_dung_theo_nhom_loai"]["chatgpt|web"] == ["web", "codex"]
    # Tên hiển thị đi kèm bảng — giao diện lõi không chép tên nhà cung cấp
    # (luật độc lập mã lõi NV101): đủ tên cho mọi id trong 3 bảng.
    assert j["ten_nhom"] == kho.TEN_NHOM_AI
    assert set(j["ten_nhom"]) == set(kho.NHOM_AI)
    moi_loai = {l for ds in kho.LOAI_TAI_KHOAN_THEO_NHOM.values() for l in ds}
    assert moi_loai <= set(j["ten_loai"])
    moi_dich = {d for ds in kho.DICH_DUNG_THEO_NHOM_LOAI.values() for d in ds}
    assert moi_dich <= set(j["ten_dich_dung"])


def test_bang_phan_loai_can_quan_tri(moi_truong):
    """Không khoá quản trị → 401/403 (như mọi đường /api/tai-khoan/*)."""
    client, _so = moi_truong
    r = client.get("/api/tai-khoan/bang-phan-loai")
    assert r.status_code in (401, 403), r.status_code


def test_bang_phan_loai_khong_lo_bi_mat(moi_truong):
    """Bảng phân loại là dữ liệu khai báo — không chứa email/mật khẩu nào."""
    client, so = moi_truong
    _cai_module(so, "chatgpt-web", nhom_id="chatgpt")
    _them(client, "a225-bi-mat@example.com", module_id="chatgpt-web",
          loai_tai_khoan="web", dich_dung="codex")
    r = client.get("/api/tai-khoan/bang-phan-loai", headers=_QT)
    assert r.status_code == 200
    assert "a225-bi-mat" not in r.text
    assert "MatKhauGia123" not in r.text


# ── (2) Dữ liệu /nhom-loai đủ trường giao diện cần ───────────────────────────

def test_nhom_loai_du_truong_cho_giao_dien(moi_truong):
    """Mỗi tài khoản trong /nhom-loai mang: nhan, bac_goi, han_muc, dang_dung,
    trang_thai — đủ để trang vẽ nhãn/gói/hạn mức/dấu #1/tạm dừng."""
    client, so = moi_truong
    _cai_module(so, "chatgpt-web", nhom_id="chatgpt")
    a = _them(client, "a225-1@example.com", module_id="chatgpt-web",
              loai_tai_khoan="web", dich_dung="codex", bac_goi="plus",
              nhan="Chính")
    b = _them(client, "a225-2@example.com", module_id="chatgpt-web",
              loai_tai_khoan="web", dich_dung="web")
    r = client.get("/api/tai-khoan/nhom-loai", headers=_QT)
    assert r.status_code == 200
    muc = r.json()["nhom_loai"]["chatgpt"]["web"]
    assert muc["dang_dung"] in (a["id"], b["id"])
    for tk in muc["tai_khoan"]:
        for truong in ("id", "nhan", "bac_goi", "han_muc", "dang_dung",
                       "trang_thai", "email_che"):
            assert truong in tk, f"thiếu trường {truong}"
        # Chưa có số liệu nhà cung cấp → «khong_biet», giao diện hiện «chưa rõ».
        assert tk["han_muc"] == "khong_biet"
        # KHÔNG lộ bí mật qua đường giao diện dùng.
        assert "mat_khau" not in tk and "totp_secret" not in tk
    theo_id = {tk["id"]: tk for tk in muc["tai_khoan"]}
    assert theo_id[muc["dang_dung"]]["dang_dung"] is True
    assert theo_id[a["id"]]["bac_goi"] == "plus"
    assert theo_id[a["id"]]["nhan"] == "Chính"


def test_tam_dung_chuyen_dang_dung_cho_giao_dien(moi_truong):
    """Tạm dừng tài khoản #1 → /nhom-loai chuyển dang_dung sang tài khoản kế
    (trang dựa vào đây để dời dấu «ĐANG DÙNG #1»)."""
    client, so = moi_truong
    _cai_module(so, "gemini-web", nhom_id="google")
    a = _them(client, "a225-3@example.com", module_id="gemini-web",
              loai_tai_khoan="gmail", dich_dung="gemini")
    b = _them(client, "a225-4@example.com", module_id="gemini-web",
              loai_tai_khoan="gmail", dich_dung="gemini")
    r = client.get("/api/tai-khoan/nhom-loai", headers=_QT)
    dau = r.json()["nhom_loai"]["google"]["gmail"]["dang_dung"]
    assert dau in (a["id"], b["id"])
    con_lai = b["id"] if dau == a["id"] else a["id"]
    r = client.post(f"/api/tai-khoan/da-luu/{dau}/tam-dung", headers=_QT,
                    json={"tam_dung": True})
    assert r.status_code == 200, r.text
    r = client.get("/api/tai-khoan/nhom-loai", headers=_QT)
    muc = r.json()["nhom_loai"]["google"]["gmail"]
    assert muc["dang_dung"] == con_lai
    theo_id = {tk["id"]: tk for tk in muc["tai_khoan"]}
    assert theo_id[dau]["trang_thai"] == "tam_dung"
    assert theo_id[dau]["dang_dung"] is False


# ── (3) Cấu trúc trang ───────────────────────────────────────────────────────

def _noi_dung_trang() -> str:
    return TRANG.read_text(encoding="utf-8")


def test_trang_gom_theo_nhom_loai():
    """Trang có khối gom nhóm → loại: đủ 5 nhóm đổ từ bảng + xô chưa phân nhóm,
    dấu «ĐANG DÙNG #1» và hạn mức «chưa rõ» khi khong_biet."""
    s = _noi_dung_trang()
    assert "data-tk-nhom" in s, "thiếu khối gom theo nhóm trong template"
    assert "data-tk-loai" in s, "thiếu khối gom theo loại trong template"
    assert "ĐANG DÙNG #1" in s, "thiếu dấu «ĐANG DÙNG #1»"
    assert "Chưa phân nhóm" in s, "thiếu xô cho tài khoản ngoài 5 nhóm"
    assert "chưa rõ" in s, "hạn mức khong_biet phải hiện «chưa rõ», không bịa số"
    # Dữ liệu đổ từ API, không tự dựng bảng trong trang:
    assert "api/tai-khoan/nhom-loai" in s
    assert "api/tai-khoan/bang-phan-loai" in s
    # Tên hiển thị cũng lấy từ bảng API (ten_nhom/ten_loai/ten_dich_dung);
    # trang lõi không chứa tên nhà cung cấp (luật NV101 — trừ fonts).
    # NV235: MIỄN TRỪ khối «Đăng nhập bằng» (NV223) — nhãn «qua tài khoản
    # google đã lưu» là văn bản giao diện của tính năng do người dùng chọn,
    # không phải bảng phân loại cứng trong trang.
    assert "ten_nhom" in s and "ten_loai" in s and "ten_dich_dung" in s
    loc = re.sub(r"https?://fonts\.googleapis\.com[^\s\"']*", "", s, flags=re.I)
    loc = re.sub(r"https?://fonts\.gstatic\.com[^\s\"']*", "", loc, flags=re.I)
    loc = re.sub(r'<label[^>]*>Đăng nhập bằng.*?</sc-if>',
                 "", loc, flags=re.I | re.S)
    loc = re.sub(r'<select id="dl-dang-nhap-bang".*?</select>',
                 "", loc, flags=re.I | re.S)
    loc = re.sub(r'<select id="dl-tk-google".*?</select>',
                 "", loc, flags=re.I | re.S)
    loc = re.sub(r"«qua tài khoản google»[^<]*", "", loc, flags=re.I)
    loc = re.sub(r"tài khoản google dùng để đăng nhập[^<]*", "", loc, flags=re.I)
    loc = re.sub(r"codangnhapgoogle", "", loc, flags=re.I)
    loc = re.sub(r"tai_khoan_google_id", "", loc, flags=re.I)
    loc = re.sub(r"dltkgoogleid", "", loc, flags=re.I)
    loc = re.sub(r"dangnhapbang\s*===\s*'google'", "", loc, flags=re.I)
    loc = re.sub(r"===\s*'google'", "", loc)
    loc = re.sub(r"dstkgoogle", "", loc, flags=re.I)
    loc = re.sub(r"tkgoogleid", "", loc, flags=re.I)
    loc = re.sub(r"doitkgoogle", "", loc, flags=re.I)
    assert "google" not in loc.lower()


def test_trang_form_chon_xep_tang_tu_api():
    """Form thêm có 3 ô chọn xếp tầng (nhóm/loại/đích dùng) và dlLuu gửi
    loai_tai_khoan + dich_dung cho backend kiểm theo bảng."""
    s = _noi_dung_trang()
    for id_o in ('id="dl-nhom"', 'id="dl-loai-tk"', 'id="dl-dich-dung"'):
        assert id_o in s, f"thiếu ô chọn {id_o}"
    assert "loai_tai_khoan" in s and "dich_dung" in s
    # Ô đích dùng chỉ hiện khi (nhóm, loại) có đích trong bảng:
    assert "dlDichDung" in s


def test_trang_js_hop_le_node_check(tmp_path):
    """Mọi khối <script> của trang qua node --check (cú pháp JS hợp lệ)."""
    s = _noi_dung_trang()
    khoi = [k for k in re.findall(r"<script[^>]*>(.*?)</script>", s, flags=re.S)
            if k.strip()]
    assert khoi, "không tìm thấy khối script nào trong trang"
    for i, js in enumerate(khoi):
        tep = tmp_path / f"trang-{i}.js"
        tep.write_text(js, encoding="utf-8")
        r = subprocess.run(["node", "--check", str(tep)],
                           capture_output=True, text=True, timeout=30)
        assert r.returncode == 0, f"script {i}: {r.stderr}"
