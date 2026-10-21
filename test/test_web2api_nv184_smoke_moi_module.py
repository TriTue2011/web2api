"""NV184 — smoke ĐỘNG qua LÕI THẬT cho MỌI module có manifest trong modules/*.

Bối cảnh: lớp lỗi đắt nhất đợt này là «test gọi thẳng hàm module thì xanh,
nhưng qua lõi thì module chết» (thuc_the đồng bộ, thiếu trang_thai, cung_cap
thiếu thiet_bi, mặc định bị lõi chặn lúc cài, manifest phương ngữ).
NV181 đã có test CẤU TRÚC (hợp đồng AST). File này là smoke ĐỘNG: cài từng
module vào lõi tạm bằng đúng API quản trị, rồi đi qua các tuyến HTTP thật.

Với MỖI thư mục modules/* có manifest.json:
  (a) cài vào lõi tạm (khuôn `moi_truong` của test_web2api_thiet_bi.py) với
      cấu hình TỐI THIỂU sinh tự động từ manifest (trường bắt buộc → dùng
      `mac_dinh` của manifest nếu có — giá trị hợp lệ theo định nghĩa, NV205;
      còn lại giá trị giả hợp lệ theo `kieu`; URL → máy chủ giả loopback,
      `WEB2API_TEST_CHO_PHEP_LOOPBACK=1`); module khai `can_module` thì cài
      module phụ thuộc trước bằng cùng cơ chế; module cần gói/dịch vụ ngoài
      → `skip` KÈM LÝ DO cụ thể (không skip trống);
  (b) sau cài: GET /api/modules và /api/modules/catalog không lỗi và thấy
      module, GET /api/modules/{id}/cau-hinh 200 — không lộ giá trị bí mật
      đã dùng lúc cài qua bất kỳ tuyến nào trong ba tuyến trên;
  (c) nếu module khai `thiet_bi`: GET /api/thiet-bi PHẢI thấy ≥ 1 thực thể
      của nó, mỗi thực thể có `rui_ro` hợp lệ và `cho_xac_nhan`, ít nhất một
      thực thể có `trang_thai` thật (module thiếu `trang_thai` → mọi thực
      thể đều None), GET /api/thiet-bi/{id} 200; một hành động ĐỌC an toàn
      (chọn theo `tinh_nang` có rủi ro `doc` trong chính dữ liệu thực thể —
      KHÔNG liệt kê tên hành động) không ném 500;
  (d) GET /api/modules/{id}/suc-khoe trả cấu trúc hợp lệ (dict có `ok`);
  (e) DELETE gỡ sạch (module biến khỏi catalog và danh sách), lõi vẫn khoẻ.

Case sinh theo `parametrize` từ thư mục — module mới tự được kiểm,
KHÔNG có danh sách tên module nào trong file này.

Module chết thật tìm được → test ĐỎ, ghi vào bảng báo cáo để điều phối giao;
KHÔNG tự sửa module của người khác.

Lệch hợp đồng đã biết trên lõi NEN179 (ghi trong báo cáo NV184): chưa có
route GET /api/modules/{id} trần — chi tiết từng module đi qua
/{id}/cau-hinh và /{id}/suc-khoe. Test vẫn thăm dò GET /{id} và chỉ đòi
không 500 (404 là chấp nhận được trên lõi này), để khi lõi có route thật
thì hành vi được khoá lại.

TestClient chạy với raise_server_exceptions=False để test QUAN SÁT được
HTTP 500 rồi phân loại (module chết / cần dịch vụ ngoài), thay vì để ngoại
lệ của route làm sập test giữa chừng.
"""

from __future__ import annotations

import json
import shutil
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import so_module
from app.main import app
from app.so_module import SoModule

KHOA_QT = "khoa-quan-tri-test-nv184-0123456789abcdef"
QT = {"Authorization": f"Bearer {KHOA_QT}"}

GOC = Path(__file__).resolve().parents[1]
THU_MUC_MODULES = GOC / "modules"

BI_MAT_GIA = "bi-mat-gia-smoke-nv184-0123456789abcdef"
KHOA_GIA = "khoa-gia-smoke-nv184-0123456789abcdef"
# Chuỗi cookie giả theo đúng dạng tài liệu manifest mô tả ('k1=v1; k2=v2'),
# kèm các tên cookie phổ biến mà module kiểm tra sự hiện diện (sso, PSID…).
COOKIE_GIA = ("sso=gia-tri-smoke-nv184; session=gia-tri-smoke-nv184; "
              "__Secure-1PSID=gia-tri-smoke-nv184; "
              "__Secure-1PSIDTS=gia-tri-smoke-nv184; "
              "sessionKey=gia-tri-smoke-nv184; auth_token=gia-tri-smoke-nv184")


# ───────────── máy chủ giả loopback ─────────────

class _TayGia(BaseHTTPRequestHandler):
    """Máy chủ giả tối thiểu cho smoke: trả lời đủ để module kiểm tra
    «địa chỉ có nghe» lúc cài đặt, không giả làm dịch vụ thật."""

    def _gui(self, ma: int, obj: dict | None = None) -> None:
        than = json.dumps(obj or {}, ensure_ascii=False).encode("utf-8")
        self.send_response(ma)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(than)))
        self.end_headers()
        self.wfile.write(than)

    def do_GET(self) -> None:  # noqa: N802
        duong = self.path.split("?")[0]
        if duong.endswith(".ics"):
            from datetime import date
            hom_nay = date.today().strftime("%Y%m%d")
            ics = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//smoke-nv184//VN\r\n"
                   "BEGIN:VEVENT\r\nUID:smoke-nv184@gia\r\n"
                   f"DTSTART:{hom_nay}T010000Z\r\nDTEND:{hom_nay}T020000Z\r\n"
                   "RRULE:FREQ=DAILY\r\nSUMMARY:Su kien smoke NV184\r\n"
                   "END:VEVENT\r\nEND:VCALENDAR\r\n").encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/calendar; charset=utf-8")
            self.send_header("Content-Length", str(len(ics)))
            self.end_headers()
            self.wfile.write(ics)
        elif duong == "/health":
            self._gui(200, {"trang_thai": "ok"})
        elif duong == "/v1/models":
            self._gui(200, {"data": []})
        elif duong == "/models":
            self._gui(200, {"models": []})
        elif duong == "/ho-so":
            self._gui(200, {})
        else:
            self._gui(404, {"loi": "khong co trong may chu gia"})

    def do_POST(self) -> None:  # noqa: N802
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self._gui(200, {})

    def log_message(self, *args) -> None:  # noqa: ANN002, ANN202
        pass


@pytest.fixture
def may_chu_gia():
    """URL gốc http://127.0.0.1:<cổng> của máy chủ giả (loopback cho test)."""
    srv = HTTPServer(("127.0.0.1", 0), _TayGia)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        t.join(timeout=5)


# ───────────── môi trường lõi tạm ─────────────

@pytest.fixture
def moi_truong(tmp_path, monkeypatch):
    """Lõi tạm sạch theo khuôn `moi_truong` của test_web2api_thiet_bi.py,
    cộng cờ cho phép loopback trong test."""
    data_dir = tmp_path / "data"
    (data_dir / "modules").mkdir(parents=True)
    anh_dir = tmp_path / "anh"
    anh_dir.mkdir(parents=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    monkeypatch.setattr(so_module, "THU_MUC_ANH", anh_dir)
    with TestClient(app, raise_server_exceptions=False) as client:
        client.app.state.so = SoModule()
        yield client, data_dir


def ds_module_dir() -> list[str]:
    """Mọi thư mục modules/* có manifest.json — nguồn duy nhất của parametrize."""
    return sorted(
        d.name for d in THU_MUC_MODULES.iterdir()
        if d.is_dir() and (d / "manifest.json").is_file()
    )


def doc_manifest(dir_name: str) -> dict:
    return json.loads((THU_MUC_MODULES / dir_name / "manifest.json").read_text(encoding="utf-8"))


# ───────────── sinh cấu hình tối thiểu ─────────────

def _la_o_url(khoa: str) -> bool:
    k = khoa.lower()
    return any(t in k for t in ("url", "dia_chi", "co_so", "base", "may_chu", "endpoint"))


def _truong_dang_url(t: dict) -> bool:
    """Trường chuỗi này nhận URL hay host trần — quyết theo CHÍNH manifest:
    tên có «url», mặc định bắt đầu bằng http, hoặc mô tả nói rõ URL/https.
    (Cùng tên `dia_chi`: module này mô tả «URL gốc», module khác «host[:cổng]».)"""
    khoa = str(t.get("khoa") or "").lower()
    if "url" in khoa:
        return True
    md = t.get("mac_dinh")
    if isinstance(md, str) and md.startswith("http"):
        return True
    mo_ta = str(t.get("mo_ta") or "")
    return "URL" in mo_ta or "https://" in mo_ta or "http://" in mo_ta


def _vi_du_json(mo_ta: str) -> str | None:
    """Trích ví dụ JSON (mảng/đối tượng) nguyên văn trong mo_ta của manifest —
    manifest tự tài liệu hoá định dạng trường, dùng lại làm giá trị giả."""
    for dau, cuoi in (("[", "]"), ("{", "}")):
        bat_dau = mo_ta.find(dau)
        if bat_dau < 0:
            continue
        sau, trong_chuoi, thoat = 0, False, False
        for i in range(bat_dau, len(mo_ta)):
            c = mo_ta[i]
            if trong_chuoi:
                if thoat:
                    thoat = False
                elif c == "\\":
                    thoat = True
                elif c == '"':
                    trong_chuoi = False
                continue
            if c == '"':
                trong_chuoi = True
            elif c == dau:
                sau += 1
            elif c == cuoi:
                sau -= 1
                if sau == 0:
                    doan = mo_ta[bat_dau:i + 1]
                    try:
                        return json.dumps(json.loads(doan), ensure_ascii=False)
                    except Exception:
                        break
    return None


def _mang_json_tu_mo_ta(mo_ta: str, url_goc: str) -> str:
    """Dựng mảng JSON hợp lệ từ mô tả văn xuôi khi mo_ta KHÔNG có ví dụ JSON
    nguyên văn (vd cameranha: «Mảng JSON, mỗi phần tử: name (tên, vd 'Cửa'),
    kind ('go2rtc' hoặc 'rtsp'), với kind=go2rtc: base (vd http://10.0.0.6:1984)
    + src (tên luồng)...»). Ưu tiên kind=go2rtc (base http cho loopback lúc
    cài) hơn rtsp (đòi IP riêng, cấm loopback)."""
    import re
    trich = re.findall(r"'([^'\n]{1,60})'", mo_ta)
    mo_ta_l = mo_ta.lower()
    bo_qua = {"go2rtc", "rtsp", "json", "mảng json"}
    ten = next((x for x in trich if x.strip().lower() not in bo_qua), "Camera")
    muc: dict = {"name": ten}
    if "rtsp" in mo_ta_l and "go2rtc" not in mo_ta_l:
        # rtsp-only: host phải là IP riêng (cấm loopback) — dùng IP riêng giả.
        muc["kind"] = "rtsp"
        host = url_goc.split("://", 1)[-1].split(":", 1)[0]
        if host in ("127.0.0.1", "localhost", "::1"):
            host = "192.168.77.5"
        muc["url"] = f"rtsp://smoke:smoke@{host}/stream"
    else:
        muc["kind"] = "go2rtc"
        muc["base"] = url_goc  # base http; loopback được phép lúc cài
        muc["src"] = "smoke"
    return json.dumps([muc], ensure_ascii=False)


def _gia_tri_truong(t: dict, url_goc: str):
    """Giá trị giả hợp lệ cho MỘT trường, theo kieu + định dạng manifest tự mô tả
    (hex/base64 cho khoá thiết bị, chuỗi cookie 'k1=v1; k2=v2', URL hay host
    trần, ví dụ JSON trong mo_ta)."""
    khoa, kieu = str(t.get("khoa") or ""), t.get("kieu")
    mo_ta = str(t.get("mo_ta") or "")
    tuy_chon = t.get("tuy_chon") or t.get("gia_tri_cho_phep") or t.get("cac_gia_tri")
    if isinstance(tuy_chon, list) and tuy_chon:
        return tuy_chon[0]
    if kieu == "danh_sach_bi_mat":
        if "k1=v1" in mo_ta:  # trường cookie: manifest mô tả dạng 'k1=v1; k2=v2'
            return [COOKIE_GIA]
        return [f"{url_goc}/gia.ics"] if _la_o_url(khoa) else [KHOA_GIA]
    if kieu == "danh_sach":
        return [f"{url_goc}/gia.ics"] if _la_o_url(khoa) else ["gia-tri-smoke-nv184"]
    mo_ta_l = mo_ta.lower()
    muon_json = ("json" in mo_ta_l
                 and ("mảng" in mo_ta_l or "dạng json" in mo_ta_l
                      or "dang json" in mo_ta_l or "array" in mo_ta_l))
    if muon_json and kieu in ("chuoi", "bi_mat"):
        # Trường nhận CHUỖI JSON (vd cameranha.danh_sach_camera: kieu bi_mat,
        # mo_ta «Mảng JSON...»): BI_MAT_GIA không phải JSON hợp lệ nên cài luôn
        # đỏ — sinh mảng JSON đúng khuôn theo mô tả (NV189, lớp A).
        vd = _vi_du_json(mo_ta)
        if vd is not None:
            return vd
        return _mang_json_tu_mo_ta(mo_ta, url_goc)
    if kieu == "bi_mat":
        if "hex" in mo_ta_l:  # khoá dạng hex (vd 32 bytes = 64 ký tự)
            return "cd" * 32
        if "base64" in mo_ta_l:
            import base64
            return base64.b64encode(b"s" * 32).decode()
        return BI_MAT_GIA
    if _la_o_url(khoa):
        if _truong_dang_url(t):
            return url_goc
        return url_goc.split("://", 1)[-1].split(":", 1)[0]  # host trần (vd 127.0.0.1)
    if "email" in khoa.lower() or "thu_dien_tu" in khoa.lower():
        return "smoke-nv184@example.invalid"
    if kieu == "chuoi":
        # Nguyên tắc NV205: trường chuỗi CÓ mac_dinh thì dùng đúng mặc định
        # của manifest (giá trị hợp lệ theo định nghĩa của tác giả module) —
        # smoke sinh chuỗi giả cho mọi trường chuoi có thể bị module từ chối
        # lúc cài (vd anh-ai.nha_cung_cap mac_dinh "gemini" bị 400 khi nhận
        # "gia-tri-smoke-nv184"). Không thêm danh sách ngoại lệ cho từng module.
        if t.get("mac_dinh") is not None:
            return t["mac_dinh"]
        return "gia-tri-smoke-nv184"
    if kieu in ("so", "so_nguyen", "so_thuc"):
        return 1
    if kieu in ("dung_sai", "boolean"):
        return True
    if t.get("mac_dinh") is not None:
        return t["mac_dinh"]
    return "gia-tri-smoke-nv184"


def cau_hinh_gia(manifest: dict, url_goc: str) -> tuple[dict, list[str]]:
    """Cấu hình TỐI THIỂU: chỉ trường bắt buộc. Trả (cấu hình, [giá trị bí mật])
    — danh sách bí mật để assert không lộ qua API công khai."""
    ch: dict = {}
    bi_mat: list[str] = []
    for t in manifest.get("truong_cau_hinh") or []:
        if not t.get("bat_buoc"):
            continue
        gt = _gia_tri_truong(t, url_goc)
        ch[t["khoa"]] = gt
        if t.get("kieu") in ("bi_mat", "danh_sach_bi_mat") or t.get("bi_mat"):
            bi_mat += [x for x in (gt if isinstance(gt, list) else [gt]) if isinstance(x, str)]
    return ch, bi_mat


# ───────────── phân loại: chết thật (fail) / cần dịch vụ ngoài (skip) ─────────────

# Dấu hiệu CHẾT THẬT qua lõi — kiểm TRƯỚC mọi lý do skip: lỗi cấu trúc/hợp
# đồng không bao giờ được ngụy trang thành «cần dịch vụ ngoài».
_LOI_CAU_TRUC = (
    "coroutine", "was never awaited", "not awaitable",
    "has no attribute", "object has no attribute", "'str' object", "'dict' object",
    "'nonetype' object", "is not callable", "not callable",
    "positional argument", "unexpected keyword argument",
    "can't be used in 'await'", "thiếu phương thức", "không đúng chuẩn",
    "bị chặn", "chan ssrf", "ssrf", "loopback", "địa chỉ bị cấm",
    "không thuộc mạng riêng", "khong thuoc mang rieng",
)

# Dấu hiệu CẦN DỊCH VỤ/GÓI NGOÀI thật (kết nối, gói chưa cài, xác thực với
# dịch vụ ngoài bằng thông tin thật) → skip KÈM lý do nguyên văn.
_DICH_VU_NGOAI = (
    "connection", "connecterror", "refused", "unreachable", "timed out", "timeout",
    "quá thời gian", "hết giờ", "không tới được", "không kết nối", "không phân giải",
    "name or service not known", "dns", "proxy", "certificate", "ssl",
    "no module named", "modulenotfounderror", "importerror", "cannot import",
    "thiếu gói", "chưa cài", "401", "403", "404", "unauthorized", "forbidden",
    "đăng nhập thất bại", "đăng nhập không", "sign invalid", "invalid token",
    "token không hợp lệ", "hết hạn phiên", "cookie không", "không đăng nhập được",
    "không thấy dịch vụ", "dịch vụ không chạy", "dịch vụ chưa chạy",
    "không liên lạc được", "không gọi được dịch vụ", "lỗi mạng",
    "không tài khoản nào dùng được", "máy chủ trả http",
    "access id", "user token", "chưa nhập cookie", "chưa có cấu hình nào dùng được",
)


def phan_loai_loi(van_ban: str) -> str:
    """'chet' = lỗi cấu trúc/hợp đồng qua lõi (test phải ĐỎ);
    'ngoai' = cần dịch vụ/gói ngoài thật (skip có lý do);
    'khac' = không rõ (mặc định coi là chết — không skip lặng lẽ)."""
    gop = van_ban.lower()
    if any(t in gop for t in _LOI_CAU_TRUC):
        return "chet"
    if any(t in gop for t in _DICH_VU_NGOAI):
        return "ngoai"
    return "khac"


def _van_ban_loi(r) -> str:
    """Gộp mọi văn bản lỗi trong phản hồi (kể cả lỗi từng khoá của lõi)."""
    try:
        than = r.json()
    except Exception:
        return r.text[:1000]
    cac = [r.text[:1000]]
    if isinstance(than, dict):
        cac.append(str(than.get("loi") or ""))
        err = than.get("error")
        if isinstance(err, dict):
            cac.append(str(err.get("message") or ""))
        for m in than.get("ket_qua_tung_khoa") or []:
            if isinstance(m, dict):
                cac.append(str(m.get("loi") or ""))
                cac.append(str(m.get("ly_do") or ""))
    return " | ".join(x for x in cac if x)


# ───────────── cài module (kèm phụ thuộc can_module) ─────────────

def cai_mot_module(client: TestClient, data_dir: Path, dir_name: str,
                   url_goc: str) -> tuple[dict, dict, list[str]]:
    """Chép module vào lõi tạm, nạp lại, cài với cấu hình giả tối thiểu.
    Trả (manifest, cau_hinh, [bí mật]). Raise skip/fail đã phân loại."""
    manifest = doc_manifest(dir_name)
    mod_id = manifest.get("id") or dir_name
    dich = data_dir / "modules" / dir_name
    if dich.exists():
        shutil.rmtree(dich)
    shutil.copytree(THU_MUC_MODULES / dir_name, dich)
    r = client.post("/api/modules/nap-lai", headers=QT)
    assert r.status_code == 200, f"[{mod_id}] nap-lai vỡ: HTTP {r.status_code} {r.text[:300]}"
    cau_hinh, bi_mat = cau_hinh_gia(manifest, url_goc)
    r = client.post(f"/api/modules/{mod_id}/cai", headers=QT, json={"cau_hinh": cau_hinh})
    if r.status_code != 200 or not r.json().get("ok"):
        # Thử lại MỘT lần: lỗi cài nêu đích danh một trường manifest chưa được
        # điền (trường «tuỳ chọn» trên giấy nhưng module đòi lúc cài — vd token
        # người dùng, danh sách đích) → điền trường đó theo đúng định dạng
        # manifest mô tả rồi cài lại. Không nêu trường nào → không thử lại.
        van = _van_ban_loi(r).lower()
        da_dien = False
        for t in manifest.get("truong_cau_hinh") or []:
            khoa = str(t.get("khoa") or "")
            if not khoa or khoa in cau_hinh or "tự lưu" in str(t.get("mo_ta") or "").lower():
                continue
            nhan = str(t.get("nhan") or "").split("(")[0].strip().lower()
            if khoa.lower() in van or (nhan and nhan in van):
                gt = _gia_tri_truong(t, url_goc)
                cau_hinh[khoa] = gt
                if t.get("kieu") in ("bi_mat", "danh_sach_bi_mat") or t.get("bi_mat"):
                    bi_mat += [x for x in (gt if isinstance(gt, list) else [gt])
                               if isinstance(x, str)]
                da_dien = True
        if da_dien:
            r = client.post(f"/api/modules/{mod_id}/cai", headers=QT,
                            json={"cau_hinh": cau_hinh})
    if r.status_code != 200:
        van = _van_ban_loi(r)
        loai = phan_loai_loi(van)
        if loai == "ngoai":
            pytest.skip(f"[{mod_id}] cần dịch vụ/gói ngoài: {van[:300]}")
        pytest.fail(f"[{mod_id}] lõi từ chối cài đặt (HTTP {r.status_code}): {van[:400]}")
    kq = r.json()
    if not kq.get("ok"):
        van = _van_ban_loi(r)
        loai = phan_loai_loi(van)
        if loai == "ngoai":
            pytest.skip(f"[{mod_id}] cần dịch vụ/gói ngoài: {van[:300]}")
        pytest.fail(f"[{mod_id}] cài thất bại qua lõi: {van[:400]}")
    return manifest, cau_hinh, bi_mat


# ───────────── kiểm tra qua lõi ─────────────

def _ten_va_rui_ro(tinh_nang_mot: object, rui_ro_mac_dinh: str | None) -> tuple[str, str]:
    if isinstance(tinh_nang_mot, dict):
        ten = str(tinh_nang_mot.get("hanh_dong") or tinh_nang_mot.get("ten")
                  or tinh_nang_mot.get("name") or "").strip()
        return ten, str(tinh_nang_mot.get("rui_ro") or rui_ro_mac_dinh or "")
    return str(tinh_nang_mot or "").strip(), str(rui_ro_mac_dinh or "")


def _than_thiet_bi(client: TestClient, mod_id: str) -> dict:
    r = client.get("/api/thiet-bi", headers=QT)
    assert r.status_code == 200, f"[{mod_id}] GET /api/thiet-bi vỡ: HTTP {r.status_code}"
    return r.json()


def _khoa_bat_mac_dinh_tat(manifest: dict) -> str | None:
    """Tên công tắc «bat» TẮT theo mặc định (module fail-closed: tắt là đúng)."""
    for t in manifest.get("truong_cau_hinh") or []:
        if (str(t.get("khoa") or "").lower() == "bat"
                and str(t.get("mac_dinh") or "").strip().lower() in ("false", "0", "")):
            return str(t.get("khoa"))
    return None


def _hanh_dong_doc_hoac_skip(cua: list[dict], mod_id: str) -> tuple[str, str]:
    """Chọn một hành động ĐỌC an toàn từ CHÍNH dữ liệu thực thể (không liệt
    kê tên hành động cụ thể). Module không công bố hành động «doc» nào →
    pytest.skip (không thử ghi/nguy_hiem). Lớp A (NV189, openaicodex)."""
    chon = None
    rui_ro_thay: set[str] = set()
    for e in cua:
        for tn in e.get("tinh_nang") or []:
            ten, rr = _ten_va_rui_ro(tn, e.get("rui_ro_mac_dinh"))
            if ten:
                rui_ro_thay.add(rr)
            if ten and rr == "doc":
                chon = (e["id"], ten)
                break
        if chon:
            break
    if not chon:
        pytest.skip(
            f"[{mod_id}] không có hành động rủi ro «doc» trong dữ liệu thực "
            f"thể (rui_ro thực tế tìm được: {sorted(rui_ro_thay)}) — "
            f"không thử ghi/nguy_hiem")
    return chon


def kiem_tra_thiet_bi_qua_loi(client: TestClient, mod_id: str, manifest: dict,
                              cau_hinh: dict) -> None:
    """Bước (c): thực thể + hành động đọc an toàn qua tuyến HTTP thật."""
    than = _than_thiet_bi(client, mod_id)
    cua = [e for e in than.get("thuc_the", []) if e.get("module") == mod_id]
    if not cua:
        # Module TẮT theo thiết kế (fail-closed, vd maytram bat=false mặc
        # định): thuc_the=[] là ĐÚNG, không phải module chết — bật công tắc
        # lên rồi đọc lại để bước (c) thật sự chạy được (NV189, lớp A).
        # Cài lại thất bại → rơi xuống đường fail/skip như cũ, không che lỗi.
        khoa_bat = _khoa_bat_mac_dinh_tat(manifest)
        if (khoa_bat and str(cau_hinh.get(khoa_bat, "")).strip().lower()
                not in ("true", "1", "bat", "on")):
            r2 = client.post(f"/api/modules/{mod_id}/cai", headers=QT,
                             json={"cau_hinh": {**cau_hinh, khoa_bat: "true"}})
            if r2.status_code == 200 and r2.json().get("ok"):
                than = _than_thiet_bi(client, mod_id)
                cua = [e for e in than.get("thuc_the", [])
                       if e.get("module") == mod_id]
    if not cua:
        loi_cua = [x for x in (than.get("loi_module") or [])
                   if isinstance(x, dict) and x.get("module") == mod_id]
        van_loi = " | ".join(str(x.get("loi") or x) for x in loi_cua)[:500]
        if van_loi and phan_loai_loi(van_loi) == "ngoai":
            pytest.skip(f"[{mod_id}] thuc_the cần dịch vụ ngoài: {van_loi[:300]}")
        if not van_loi and any(
                t.get("bat_buoc") and _la_o_url(str(t.get("khoa") or ""))
                for t in manifest.get("truong_cau_hinh") or []):
            # Module sống dựa vào MỘT dịch vụ ngoài (có trường địa chỉ bắt
            # buộc): thực thể sinh từ dữ liệu của dịch vụ đó — máy chủ giả
            # không có dữ liệu thật nên 0 thực thể không chứng minh được
            # module chết. Module thuần cục bộ thì KHÔNG có đường thoát này.
            pytest.skip(
                f"[{mod_id}] 0 thực thể và không lỗi — thực thể sinh từ dịch vụ "
                f"ngoài tại địa chỉ đã khai, máy chủ giả không có dữ liệu thật")
        pytest.fail(
            f"[{mod_id}] khai cung_cap «thiet_bi» nhưng lõi không thấy thực thể nào"
            f" (thuc_the đồng bộ / lỗi là nguyên nhân điển hình). loi_module: {van_loi or '-'}")
    for e in cua:
        assert "rui_ro" in e and e.get("rui_ro") in ("doc", "ghi", "nguy_hiem"), \
            f"[{mod_id}] thực thể {e.get('id')} thiếu rui_ro hợp lệ: {e.get('rui_ro')!r}"
        assert "cho_xac_nhan" in e and isinstance(e.get("cho_xac_nhan"), bool), \
            f"[{mod_id}] thực thể {e.get('id')} thiếu cho_xac_nhan (bool)"
        r1 = client.get(f"/api/thiet-bi/{e['id']}", headers=QT)
        assert r1.status_code == 200, \
            f"[{mod_id}] GET /api/thiet-bi/{e['id']}: HTTP {r1.status_code} {r1.text[:200]}"
    assert any(e.get("trang_thai") is not None for e in cua), \
        (f"[{mod_id}] MỌI thực thể đều trang_thai=None — module thiếu/không chạy "
         f"được trang_thai qua lõi (lớp lỗi «bỏ trang_thai»)")
    # Một hành động ĐỌC an toàn: chọn theo rủi ro «doc» trong CHÍNH dữ liệu
    # thực thể, không liệt kê tên hành động cụ thể nào.
    eid, hd = _hanh_dong_doc_hoac_skip(cua, mod_id)
    r = client.post(f"/api/thiet-bi/{eid}/hanh-dong", headers=QT,
                    json={"hanh_dong": hd, "tham_so": {}, "xac_nhan": True})
    assert r.status_code != 500, \
        f"[{mod_id}] hành động đọc «{hd}» ném 500: {r.text[:300]}"


def smoke_qua_loi(client: TestClient, data_dir: Path, dir_name: str,
                  url_goc: str) -> None:
    """Chạy toàn bộ bước (a)→(e) cho một module. AssertionError = module
    chết thật qua lõi; pytest.skip = cần dịch vụ/gói ngoài (có lý do)."""
    manifest = doc_manifest(dir_name)
    mod_id = manifest.get("id") or dir_name

    # (a0) cài phụ thuộc can_module trước (cùng cơ chế, cấu hình giả tối thiểu)
    phu_thuoc: list[str] = []
    for dep_dir in ds_module_dir():
        dep_mf = doc_manifest(dep_dir)
        if (dep_mf.get("id") or dep_dir) in (manifest.get("can_module") or []):
            cai_mot_module(client, data_dir, dep_dir, url_goc)
            phu_thuoc.append(dep_mf.get("id") or dep_dir)

    # (a) cài module chính
    manifest, cau_hinh, bi_mat = cai_mot_module(client, data_dir, dir_name, url_goc)

    # (b) danh sách + catalog + cấu hình từng module: không lỗi, không lộ bí mật
    van_bi_mat: dict[str, str] = {}
    r = client.get("/api/modules", headers=QT)
    assert r.status_code == 200, f"[{mod_id}] GET /api/modules: HTTP {r.status_code}"
    van_bi_mat["GET /api/modules"] = r.text
    assert any(m.get("id") == mod_id for m in r.json().get("modules", [])), \
        f"[{mod_id}] cài xong nhưng vắng mặt trong GET /api/modules"
    r = client.get("/api/modules/catalog", headers=QT)
    assert r.status_code == 200, f"[{mod_id}] GET /api/modules/catalog: HTTP {r.status_code}"
    van_bi_mat["GET /api/modules/catalog"] = r.text
    assert any(m.get("id") == mod_id for m in r.json().get("modules", [])), \
        f"[{mod_id}] cài xong nhưng vắng mặt trong catalog"
    r = client.get(f"/api/modules/{mod_id}/cau-hinh", headers=QT)
    assert r.status_code == 200, \
        f"[{mod_id}] GET /api/modules/{mod_id}/cau-hinh: HTTP {r.status_code} {r.text[:200]}"
    van_bi_mat["GET cau-hinh"] = r.text
    # Thăm dò route trần của spec: lõi NEN179 chưa có — chỉ đòi không 500.
    r = client.get(f"/api/modules/{mod_id}", headers=QT)
    assert r.status_code != 500, f"[{mod_id}] GET /api/modules/{mod_id} ném 500"
    for bm in bi_mat:
        for ten_truyen, van in van_bi_mat.items():
            assert bm not in van, f"[{mod_id}] LỘ bí mật qua {ten_truyen}"

    # (c) thiết bị — chỉ khi module khai cung_cap thiet_bi
    if "thiet_bi" in (manifest.get("cung_cap") or []):
        kiem_tra_thiet_bi_qua_loi(client, mod_id, manifest, cau_hinh)

    # (d) sức khoẻ có cấu trúc
    r = client.get(f"/api/modules/{mod_id}/suc-khoe", headers=QT)
    if r.status_code != 200:
        van = _van_ban_loi(r)
        if r.status_code >= 500 and phan_loai_loi(van) == "ngoai":
            pytest.skip(f"[{mod_id}] suc_khoe cần dịch vụ ngoài: {van[:300]}")
        pytest.fail(f"[{mod_id}] suc-khoe: HTTP {r.status_code} {van[:300]}")
    sk = r.json()
    assert isinstance(sk, dict) and "ok" in sk, \
        f"[{mod_id}] suc-khoe không trả cấu trúc hợp lệ: {str(sk)[:200]}"
    # Mâu thuẫn chết người: cai vừa báo ok mà suc_khoe tự nhận «chưa cấu hình»
    # → cấu hình không tới được thể hiện phục vụ (lớp lỗi `cau_hinh_luu`:
    # lõi chỉ lưu/nạp lại những gì cai trả trong cau_hinh_luu).
    tu_nhan = f"{sk.get('trang_thai') or ''} {sk.get('chi_tiet') or ''} {sk.get('loi') or ''}"
    if "chưa cấu hình" in tu_nhan or "chua cau hinh" in tu_nhan.lower():
        pytest.fail(
            f"[{mod_id}] cai báo ok nhưng suc_khoe tự nhận chưa cấu hình — "
            f"cấu hình không tới thể hiện phục vụ (cau_hinh_luu?): {str(sk)[:250]}")

    # (e) gỡ module chính rồi phụ thuộc; lõi vẫn khoẻ, module biến mất
    for can_go in [mod_id, *reversed(phu_thuoc)]:
        r = client.delete(f"/api/modules/{can_go}", headers=QT)
        assert r.status_code == 200, \
            f"[{mod_id}] gỡ «{can_go}»: HTTP {r.status_code} {r.text[:300]}"
    r = client.get("/api/modules/catalog", headers=QT)
    assert r.status_code == 200, f"[{mod_id}] lõi yếu sau khi gỡ module: HTTP {r.status_code}"
    assert all(m.get("id") != mod_id for m in r.json().get("modules", [])), \
        f"[{mod_id}] gỡ xong nhưng vẫn còn trong catalog"
    r = client.get("/api/modules", headers=QT)
    assert r.status_code == 200
    assert all(m.get("id") != mod_id for m in r.json().get("modules", [])), \
        f"[{mod_id}] gỡ xong nhưng vẫn còn trong GET /api/modules"


@pytest.mark.parametrize("dir_name", ds_module_dir())
def test_nv184_smoke_moi_module(moi_truong, may_chu_gia, dir_name):
    """NV184: mỗi module có manifest phải sống qua lõi thật (a)→(e)."""
    client, data_dir = moi_truong
    smoke_qua_loi(client, data_dir, dir_name, may_chu_gia)


# ───────────── kiểm tra chéo khai ↔ mã, và mặc định loopback ─────────────

import re

_CO_THUC_THE = re.compile(r"^\s*(?:async\s+)?def thuc_the\b", re.M)


@pytest.mark.parametrize("dir_name", ds_module_dir())
def test_nv184_khai_thiet_bi_khop_ma(dir_name):
    """Khai ↔ mã phải khớp hai chiều cho khả năng thiết bị.

    Mã có `thuc_the` (phương thức hợp đồng thiết bị) mà manifest KHÔNG khai
    «thiet_bi» trong `cung_cap` → lõi không bao giờ hiện thực thể — module
    chết LẶNG (lớp lỗi NV181: module có thực thể mà người dùng không thấy).
    Khai «thiet_bi» mà mã không có `thuc_the` → lõi vỡ khi hỏi thực thể.
    Đột biến «bỏ thiet_bi khỏi cung_cap» bị test này bắt.
    """
    manifest = doc_manifest(dir_name)
    ma = (THU_MUC_MODULES / dir_name / "module.py").read_text(
        encoding="utf-8", errors="ignore")
    co_ma = bool(_CO_THUC_THE.search(ma))
    co_khai = "thiet_bi" in (manifest.get("cung_cap") or [])
    assert co_ma == co_khai, (
        f"[{manifest.get('id') or dir_name}] khai↔mã lệch cho «thiet_bi»: "
        f"mã có thuc_the={co_ma}, manifest khai thiet_bi={co_khai} — "
        + ("module có thực thể nhưng lõi không hiện vì cung_cap thiếu «thiet_bi»"
           if co_ma else "manifest khai «thiet_bi» nhưng mã không có thuc_the"))


def ds_module_mac_dinh_loopback() -> list[str]:
    """Module có trường URL TUỲ CHỌN mang mặc định loopback (127.0.0.1/
    localhost/::1) — sinh từ manifest, không danh sách tên."""
    ra = []
    for dir_name in ds_module_dir():
        m = doc_manifest(dir_name)
        for t in m.get("truong_cau_hinh") or []:
            md = t.get("mac_dinh")
            if (not t.get("bat_buoc") and isinstance(md, str)
                    and ("127.0.0.1" in md or "localhost" in md or "::1" in md)
                    and _la_o_url(str(t.get("khoa") or ""))):
                ra.append(dir_name)
                break
    return ra


@pytest.fixture
def moi_truong_that(tmp_path, monkeypatch):
    """Như `moi_truong` nhưng KHÔNG bật cờ cho phép loopback — tái hiện đúng
    môi trường thật, nơi lõi chặn URL loopback lúc cài (lớp lỗi NV181)."""
    data_dir = tmp_path / "data"
    (data_dir / "modules").mkdir(parents=True)
    anh_dir = tmp_path / "anh"
    anh_dir.mkdir(parents=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.delenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", raising=False)
    monkeypatch.setattr(so_module, "THU_MUC_ANH", anh_dir)
    with TestClient(app, raise_server_exceptions=False) as client:
        client.app.state.so = SoModule()
        yield client, data_dir


@pytest.mark.parametrize("dir_name", ds_module_mac_dinh_loopback())
def test_nv184_mac_dinh_loopback_van_cai_duoc(moi_truong_that, may_chu_gia, dir_name):
    """Mặc định loopback của trường tuỳ chọn không được làm cài đặt chết
    ngoài môi trường test: cài với trường đó để trống (mặc định tự áp),
    các trường URL bắt buộc trỏ vào địa chỉ LAN riêng (lõi cho qua).
    Lõi chặn chính mặc định đó → test ĐỎ (đột biến «mặc định loopback»)."""
    client, data_dir = moi_truong_that
    cong = may_chu_gia.rsplit(":", 1)[-1]
    url_lan = f"http://192.168.77.5:{cong}"
    manifest, _ch, _bm = cai_mot_module(client, data_dir, dir_name, url_lan)
    mod_id = manifest.get("id") or dir_name
    r = client.delete(f"/api/modules/{mod_id}", headers=QT)
    assert r.status_code == 200, \
        f"[{mod_id}] gỡ sau kiểm mặc định loopback: HTTP {r.status_code} {r.text[:200]}"
