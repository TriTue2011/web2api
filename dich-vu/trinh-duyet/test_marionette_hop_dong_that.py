"""Kiểm thử hợp đồng Marionette bằng dữ liệu đo từ Firefox thật — NV100.

Nguồn sự thật là tệp du_lieu_that/marionette_that.json ghi lại phiên làm việc thật
với Firefox ESR 18/10/2026 trên trang công khai https://example.com/.
Máy chủ giả phát lại các khung đo thật và từ chối nghiêm ngặt các lệnh lạ/sai tham số.
"""

from __future__ import annotations

import ast
import json
import re
import socket
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

GOC_DICH_VU = Path(__file__).resolve().parent
if str(GOC_DICH_VU) not in sys.path:
    sys.path.insert(0, str(GOC_DICH_VU))

from marionette import KHOA_PHAN_TU, LoiMarionette, MarionetteClient

TEP_DU_LIEU = GOC_DICH_VU / "du_lieu_that" / "marionette_that.json"


def doc_du_lieu_that() -> dict:
    return json.loads(TEP_DU_LIEU.read_text(encoding="utf-8"))


def xu_ly_khung_tho(raw: str) -> tuple[int, int, str, bool]:
    """Phân tích khung thô dạng '<độ dài>:<thân>'. Dữ liệu chuẩn là khung ĐẦY ĐỦ nên tiền tố phải bằng số byte thật.

    Trả về: (khai_bao, byte_that, than, bi_cat) với bi_cat luôn False (giữ chữ ký cũ cho các chỗ gọi).
    """
    khai_str, _, than = raw.partition(":")
    khai = int(khai_str)
    byte_that = len(than.encode("utf-8"))
    assert khai == byte_that, f"khung trong dữ liệu chuẩn bị cắt: khai {khai}, thật {byte_that}"
    return khai, byte_that, than, False


class MayChuMarionetteHopDongThat:
    """Máy chủ Marionette giả dựa trên dữ liệu đo thật.

    - Nguồn sự thật: `du_lieu_that/marionette_that.json`.
    - Kiểm tra nghiêm ngặt định dạng khung yêu cầu từ máy khách.
    - Từ chối lệnh lạ với mã lỗi `unknown command`.
    - Kiểm tra tham số theo quy tắc tập con; sai tham số trả `invalid argument`.
    """

    def __init__(self, doi_id: bool = True, ghi_de_phan_hoi: dict[str, Any] | None = None):
        self.doi_id = doi_id
        self.ghi_de_phan_hoi = ghi_de_phan_hoi or {}
        self.da_nhan: list[list] = []
        self.vi_pham: list[str] = []

        du_lieu = doc_du_lieu_that()
        self.khung_chao_tho = ""
        self.cac_khung_lenh: list[dict] = []

        for kh in du_lieu.get("khung", []):
            if "thô" in kh:
                self.khung_chao_tho = kh["thô"]
            elif "lệnh" in kh:
                khai, that, than_chuan, bi_cat = xu_ly_khung_tho(kh["phản_hồi_thô"])
                self.cac_khung_lenh.append({
                    "lệnh": kh["lệnh"],
                    "tham_số": kh["tham_số"],
                    "phản_hồi_thô": kh["phản_hồi_thô"],
                    "than_chuan": than_chuan,
                    "bi_cat": bi_cat,
                })

        self._sk = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sk.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sk.bind(("127.0.0.1", 0))
        self._sk.listen(1)
        self.port = self._sk.getsockname()[1]
        self._luong = threading.Thread(target=self._phuc_vu, daemon=True)

    def __enter__(self):
        self._luong.start()
        return self

    def __exit__(self, *args):
        try:
            self._sk.close()
        except OSError:
            pass
        self._luong.join(timeout=5)

    def _phuc_vu(self):
        try:
            conn, _ = self._sk.accept()
        except OSError:
            return
        with conn:
            conn.settimeout(10.0)
            # Gửi khung chào ban đầu
            chao_bytes = self.khung_chao_tho.encode("utf-8")
            try:
                conn.sendall(chao_bytes)
            except OSError:
                return

            while True:
                try:
                    yeu_cau = self._doc_khung_nghiem(conn)
                except (ConnectionError, OSError, ValueError):
                    break
                self.da_nhan.append(yeu_cau)
                loai, msg_id, ten_lenh, tham_so = yeu_cau

                phan_hoi_bytes = self._xu_ly_lenh(msg_id, ten_lenh, tham_so)
                try:
                    conn.sendall(phan_hoi_bytes)
                except OSError:
                    break

    def _doc_khung_nghiem(self, conn: socket.socket) -> list:
        chu_so = bytearray()
        while True:
            b = conn.recv(1)
            if not b:
                raise ConnectionError("máy khách đóng nối khi đang đọc độ dài")
            if b == b":":
                break
            if not b.isdigit() or len(chu_so) >= 10:
                self.vi_pham.append(f"tiền tố độ dài sai định dạng: {chu_so!r} + {b!r}")
                raise ValueError("khung_sai_dinh_dang")
            chu_so += b

        if not chu_so:
            self.vi_pham.append("tiền tố độ dài rỗng")
            raise ValueError("khung_sai_dinh_dang")

        dai = int(chu_so)
        du_lieu = bytearray()
        while len(du_lieu) < dai:
            khuc = conn.recv(min(65536, dai - len(du_lieu)))
            if not khuc:
                self.vi_pham.append(f"thiếu byte: cần {dai}, có {len(du_lieu)}")
                raise ConnectionError("đóng giữa chừng")
            du_lieu += khuc

        try:
            obj = json.loads(du_lieu.decode("utf-8"))
        except Exception as e:
            self.vi_pham.append(f"JSON sai cú pháp: {e}")
            raise ValueError("json_sai") from e

        if (
            not isinstance(obj, list)
            or len(obj) != 4
            or obj[0] != 0
            or not isinstance(obj[1], int)
            or not isinstance(obj[2], str)
            or not isinstance(obj[3], dict)
        ):
            self.vi_pham.append(f"khung yêu cầu sai cấu trúc [0, id, ten, tham_so]: {obj!r}")
            raise ValueError("yeu_cau_sai_cau_truc")

        return obj

    def _xu_ly_lenh(self, msg_id: int, ten_lenh: str, tham_so_khach: dict) -> bytes:
        if not self.doi_id:
            # Phát lại NGUYÊN PHIÊN: yêu cầu thứ n ↔ khung thứ n đã ghi (có lệnh lặp như GetCookies/Navigate), kiểm đúng lệnh và tham số.
            idx = len(self.da_nhan) - 1
            if idx < len(self.cac_khung_lenh):
                k = self.cac_khung_lenh[idx]
                if k["lệnh"] == ten_lenh and k["tham_số"] == tham_so_khach:
                    return k["phản_hồi_thô"].encode("utf-8")
            self.vi_pham.append(f"yêu cầu thứ {idx + 1} ({ten_lenh}) không khớp khung ghi tương ứng")
            loi = {"error": "invalid argument", "message": "invalid argument", "stacktrace": ""}
            than_bytes = json.dumps([1, msg_id, loi, None]).encode("utf-8")
            return str(len(than_bytes)).encode("ascii") + b":" + than_bytes
        if ten_lenh in self.ghi_de_phan_hoi:
            obj = self.ghi_de_phan_hoi[ten_lenh]
            if self.doi_id and isinstance(obj, list) and len(obj) >= 2:
                obj = [obj[0], msg_id, *obj[2:]]
            than_bytes = json.dumps(obj).encode("utf-8")
            return str(len(than_bytes)).encode("ascii") + b":" + than_bytes

        khung_cung_ten = [k for k in self.cac_khung_lenh if k["lệnh"] == ten_lenh]
        if not khung_cung_ten:
            loi = {"error": "unknown command", "message": f"WebDriver command not found: {ten_lenh}", "stacktrace": ""}
            obj = [1, msg_id if self.doi_id else 1, loi, None]
            than_bytes = json.dumps(obj).encode("utf-8")
            return str(len(than_bytes)).encode("ascii") + b":" + than_bytes

        khung_khop = None
        for k in khung_cung_ten:
            ghi_ts = k["tham_số"]
            khop = True
            for g_k, g_v in ghi_ts.items():
                if g_k not in tham_so_khach or tham_so_khach[g_k] != g_v:
                    khop = False
                    break
            if khop:
                khung_khop = k
                break

        if khung_khop is None:
            self.vi_pham.append(f"tham số của {ten_lenh} không khớp bất kỳ khung ghi nào: {tham_so_khach}")
            loi = {"error": "invalid argument", "message": "invalid argument", "stacktrace": ""}
            obj = [1, msg_id if self.doi_id else 1, loi, None]
            than_bytes = json.dumps(obj).encode("utf-8")
            return str(len(than_bytes)).encode("ascii") + b":" + than_bytes

        than = khung_khop["than_chuan"]
        if self.doi_id:
            than_doi = re.sub(r"^\[1,\s*\d+,", f"[1,{msg_id},", than, count=1)
            b = than_doi.encode("utf-8")
            return str(len(b)).encode("ascii") + b":" + b
        else:
            if not khung_khop["bi_cat"]:
                return khung_khop["phản_hồi_thô"].encode("utf-8")
            b = than.encode("utf-8")
            return str(len(b)).encode("ascii") + b":" + b


# ── CÁC BÀI KIỂM THỬ ────────────────────────────────────────────────────────


def test_du_lieu_chuan_day_du_khong_cat_va_da_che_truong_nhay_cam():
    """Mọi khung có tiền tố = số byte thật (không bị cắt) và không còn đường dẫn/phiên bản của máy chủ (kho PUBLIC)."""
    du_lieu = doc_du_lieu_that()
    for kh in du_lieu["khung"]:
        raw = kh.get("phản_hồi_thô") or kh.get("thô")
        khai_str, _, than = raw.partition(":")
        assert int(khai_str) == len(than.encode("utf-8")), kh.get("lệnh") or "lời chào"
    tho = TEP_DU_LIEU.read_text(encoding="utf-8")
    assert "pve" not in tho                                   # phiên bản nhân của máy chủ
    assert not re.search(r"/hoso/[0-9a-f]{8}", tho)           # đường dẫn hồ sơ thật
    cookie = next(k for k in reversed(du_lieu["khung"]) if k.get("lệnh") == "WebDriver:GetCookies")
    obj = json.loads(cookie["phản_hồi_thô"].partition(":")[2])
    assert obj[3] and all(c["value"] == "<đã-che>" for c in obj[3])
    assert du_lieu["đã_che"], "phải ghi rõ trường nào đã che"


def test_tai_hien_nguyen_phien_ghi():
    """Tái hiện nguyên vẹn mọi lệnh đã ghi theo đúng thứ tự và id, phát lại nguyên byte."""
    du_lieu = doc_du_lieu_that()
    cac_khung_lenh = [k for k in du_lieu["khung"] if "lệnh" in k]

    with MayChuMarionetteHopDongThat(doi_id=False) as may_chu:
        kh = MarionetteClient(port=may_chu.port)
        kh.ket_noi()
        try:
            for k in cac_khung_lenh:
                ten_lenh = k["lệnh"]
                tham_so = k["tham_số"]
                _, _, than_chuan, _ = xu_ly_khung_tho(k["phản_hồi_thô"])
                mong_doi = json.loads(than_chuan)
                loi_mong_doi = mong_doi[2]

                if loi_mong_doi:
                    with pytest.raises(LoiMarionette) as exc:
                        kh.lenh(ten_lenh, tham_so)
                    assert exc.value.ma_loi == loi_mong_doi["error"]
                else:
                    ket_qua = kh.lenh(ten_lenh, tham_so)
                    assert ket_qua == mong_doi[3]

            assert len(may_chu.da_nhan) == len(cac_khung_lenh)
            for idx, yeu_cau in enumerate(may_chu.da_nhan):
                id_ghi = json.loads(xu_ly_khung_tho(cac_khung_lenh[idx]["phản_hồi_thô"])[2])[1]
                assert yeu_cau[1] == id_ghi
            assert may_chu.vi_pham == []
        finally:
            kh.dong()


def test_loi_chao_doc_duoc():
    """Khung chào ban đầu 50:{...} đọc được và đặt da_noi = True."""
    with MayChuMarionetteHopDongThat() as may_chu:
        kh = MarionetteClient(port=may_chu.port)
        kh.ket_noi()
        try:
            assert kh.da_noi is True
            assert may_chu.vi_pham == []
        finally:
            kh.dong()
        assert kh.da_noi is False


def test_phien_moi():
    """phien_moi() trả đúng sessionId và ghi nhận moz:profile từ khung ghi đã vá."""
    du_lieu = doc_du_lieu_that()
    khung_ns = next(k for k in du_lieu["khung"] if k.get("lệnh") == "WebDriver:NewSession")
    _, _, than_chuan, _ = xu_ly_khung_tho(khung_ns["phản_hồi_thô"])
    ns_obj = json.loads(than_chuan)[3]
    sid_mong_doi = ns_obj["sessionId"]
    profile_mong_doi = ns_obj["capabilities"]["moz:profile"]

    with MayChuMarionetteHopDongThat() as may_chu:
        kh = MarionetteClient(port=may_chu.port)
        kh.ket_noi()
        try:
            phien = kh.phien_moi()
            assert phien == sid_mong_doi
            assert kh.moz_profile == profile_mong_doi
            assert may_chu.vi_pham == []
        finally:
            kh.dong()


def test_dieu_huong_va_doc_trang():
    """dieu_huong, url_hien_tai, tieu_de và nguon_trang khớp giá trị khung ghi."""
    du_lieu = doc_du_lieu_that()
    url_mong_doi = next(
        json.loads(xu_ly_khung_tho(k["phản_hồi_thô"])[2])[3]["value"]
        for k in du_lieu["khung"]
        if k.get("lệnh") == "WebDriver:GetCurrentURL"
    )
    tieu_de_mong_doi = next(
        json.loads(xu_ly_khung_tho(k["phản_hồi_thô"])[2])[3]["value"]
        for k in du_lieu["khung"]
        if k.get("lệnh") == "WebDriver:GetTitle"
    )
    nguon_trang_mong_doi = next(
        json.loads(xu_ly_khung_tho(k["phản_hồi_thô"])[2])[3]["value"]
        for k in du_lieu["khung"]
        if k.get("lệnh") == "WebDriver:GetPageSource"
    )

    with MayChuMarionetteHopDongThat() as may_chu:
        kh = MarionetteClient(port=may_chu.port)
        kh.ket_noi()
        try:
            kh.phien_moi()
            kh.dieu_huong(url_mong_doi)
            assert kh.url_hien_tai() == url_mong_doi
            assert kh.tieu_de() == tieu_de_mong_doi
            assert kh.nguon_trang().startswith(nguon_trang_mong_doi[:100])
            assert may_chu.vi_pham == []
        finally:
            kh.dong()


def test_cookie_khung_ghi_that():
    """Khung ghi GetCookies trả mảng rỗng trực tiếp [1,id,null,[]] -> cookie() trả []."""
    du_lieu = doc_du_lieu_that()
    khung_ck = next(k for k in du_lieu["khung"] if k.get("lệnh") == "WebDriver:GetCookies")
    _, _, than_chuan, _ = xu_ly_khung_tho(khung_ck["phản_hồi_thô"])
    ck_obj = json.loads(than_chuan)
    assert isinstance(ck_obj[3], list)
    assert ck_obj[3] == []

    with MayChuMarionetteHopDongThat() as may_chu:
        kh = MarionetteClient(port=may_chu.port)
        kh.ket_noi()
        try:
            kh.phien_moi()
            assert kh.cookie() == []
            assert may_chu.vi_pham == []
        finally:
            kh.dong()


def test_cookie_khung_that_co_cookie():
    """Khung GetCookies ĐO THẬT trên trang có cookie (google.com, giá trị đã che): danh sách trực tiếp các dict có name/value/path/domain."""
    du_lieu = doc_du_lieu_that()
    khung_ck = [k for k in du_lieu["khung"] if k.get("lệnh") == "WebDriver:GetCookies"][-1]
    obj = json.loads(khung_ck["phản_hồi_thô"].partition(":")[2])
    assert isinstance(obj[3], list) and len(obj[3]) >= 1
    ghi_de = {"WebDriver:GetCookies": obj}

    with MayChuMarionetteHopDongThat(ghi_de_phan_hoi=ghi_de) as may_chu:
        kh = MarionetteClient(port=may_chu.port)
        kh.ket_noi()
        try:
            kh.phien_moi()
            ra = kh.cookie()
            assert ra == obj[3]
            assert all({"name", "value", "path", "domain"} <= set(c) for c in ra)
            assert may_chu.vi_pham == []
        finally:
            kh.dong()


def test_thuc_thi():
    """thuc_thi() gửi đúng script, args và nhận value từ khung ghi."""
    du_lieu = doc_du_lieu_that()
    khung_ex = next(k for k in du_lieu["khung"] if k.get("lệnh") == "WebDriver:ExecuteScript")
    script = khung_ex["tham_số"]["script"]
    args = khung_ex["tham_số"]["args"]
    kq_mong_doi = json.loads(xu_ly_khung_tho(khung_ex["phản_hồi_thô"])[2])[3]["value"]

    with MayChuMarionetteHopDongThat() as may_chu:
        kh = MarionetteClient(port=may_chu.port)
        kh.ket_noi()
        try:
            kh.phien_moi()
            assert kh.thuc_thi(script, args) == kq_mong_doi
            assert may_chu.vi_pham == []
        finally:
            kh.dong()


def test_tim_phan_tu():
    """tim_phan_tu thành công trả KHOA_PHAN_TU; không thấy ném LoiMarionette('no such element')."""
    du_lieu = doc_du_lieu_that()
    khung_body = next(
        k for k in du_lieu["khung"]
        if k.get("lệnh") == "WebDriver:FindElement" and k["tham_số"].get("value") == "body"
    )
    id_body_mong_doi = json.loads(xu_ly_khung_tho(khung_body["phản_hồi_thô"])[2])[3]["value"][KHOA_PHAN_TU]

    khung_khong_thay = next(
        k for k in du_lieu["khung"]
        if k.get("lệnh") == "WebDriver:FindElement" and k["tham_số"].get("value") == "div.khong-co-that"
    )
    ma_loi_mong_doi = json.loads(xu_ly_khung_tho(khung_khong_thay["phản_hồi_thô"])[2])[2]["error"]

    with MayChuMarionetteHopDongThat() as may_chu:
        kh = MarionetteClient(port=may_chu.port)
        kh.ket_noi()
        try:
            kh.phien_moi()
            pt = kh.tim_phan_tu("body")
            assert pt[KHOA_PHAN_TU] == id_body_mong_doi

            with pytest.raises(LoiMarionette) as exc:
                kh.tim_phan_tu("div.khong-co-that", timeout=0)
            assert exc.value.ma_loi == ma_loi_mong_doi
            assert may_chu.vi_pham == []
        finally:
            kh.dong()


def test_bam():
    """bam() gửi WebDriver:ElementClick với id chuỗi uuid; vi_pham == []."""
    du_lieu = doc_du_lieu_that()
    khung_body = next(
        k for k in du_lieu["khung"]
        if k.get("lệnh") == "WebDriver:FindElement" and k["tham_số"].get("value") == "body"
    )
    id_body = json.loads(xu_ly_khung_tho(khung_body["phản_hồi_thô"])[2])[3]["value"][KHOA_PHAN_TU]

    with MayChuMarionetteHopDongThat() as may_chu:
        kh = MarionetteClient(port=may_chu.port)
        kh.ket_noi()
        try:
            kh.phien_moi()
            kh.bam("body")
            bam_lenh = [y for y in may_chu.da_nhan if y[2] == "WebDriver:ElementClick"]
            assert len(bam_lenh) == 1
            assert bam_lenh[0][3] == {"id": id_body}
            assert may_chu.vi_pham == []
        finally:
            kh.dong()


def test_dien():
    """dien() bỏ qua lỗi invalid element state của ElementClear và gửi ElementSendKeys."""
    du_lieu = doc_du_lieu_that()
    khung_body = next(
        k for k in du_lieu["khung"]
        if k.get("lệnh") == "WebDriver:FindElement" and k["tham_số"].get("value") == "body"
    )
    id_body = json.loads(xu_ly_khung_tho(khung_body["phản_hồi_thô"])[2])[3]["value"][KHOA_PHAN_TU]

    with MayChuMarionetteHopDongThat() as may_chu:
        kh = MarionetteClient(port=may_chu.port)
        kh.ket_noi()
        try:
            kh.phien_moi()
            kh.dien("body", "x")
            gui_lenh = [y for y in may_chu.da_nhan if y[2] == "WebDriver:ElementSendKeys"]
            assert len(gui_lenh) == 1
            assert gui_lenh[0][3] == {"id": id_body, "text": "x"}
            assert may_chu.vi_pham == []

            # Ca xoa_truoc=False không gửi ElementClear
            may_chu.da_nhan.clear()
            kh.dien("body", "x", xoa_truoc=False)
            clear_lenh = [y for y in may_chu.da_nhan if y[2] == "WebDriver:ElementClear"]
            assert len(clear_lenh) == 0
            assert may_chu.vi_pham == []
        finally:
            kh.dong()


def test_lenh_la():
    """ClickElement và tên lệnh chưa từng đo đều bị máy chủ từ chối với unknown command."""
    du_lieu = doc_du_lieu_that()
    khung_body = next(
        k for k in du_lieu["khung"]
        if k.get("lệnh") == "WebDriver:FindElement" and k["tham_số"].get("value") == "body"
    )
    id_body = json.loads(xu_ly_khung_tho(khung_body["phản_hồi_thô"])[2])[3]["value"][KHOA_PHAN_TU]

    with MayChuMarionetteHopDongThat() as may_chu:
        kh = MarionetteClient(port=may_chu.port)
        kh.ket_noi()
        try:
            kh.phien_moi()
            with pytest.raises(LoiMarionette) as exc1:
                kh.lenh("WebDriver:ClickElement", {"id": id_body})
            assert exc1.value.ma_loi == "unknown command"

            with pytest.raises(LoiMarionette) as exc2:
                kh.lenh("WebDriver:TenLenhChuaTungDo")
            assert exc2.value.ma_loi == "unknown command"
            assert may_chu.vi_pham == []
        finally:
            kh.dong()


def test_xoa_phien():
    """xoa_phien() gửi DeleteSession; gọi lần hai không gửi gì."""
    with MayChuMarionetteHopDongThat() as may_chu:
        kh = MarionetteClient(port=may_chu.port)
        kh.ket_noi()
        try:
            kh.phien_moi()
            kh.xoa_phien()
            xoa1 = [y for y in may_chu.da_nhan if y[2] == "WebDriver:DeleteSession"]
            assert len(xoa1) == 1

            kh.xoa_phien()
            xoa2 = [y for y in may_chu.da_nhan if y[2] == "WebDriver:DeleteSession"]
            assert len(xoa2) == 1
            assert may_chu.vi_pham == []
        finally:
            kh.dong()


def test_moi_lenh_marionette_py_da_do_that():
    """Mọi hằng chuỗi WebDriver:[A-Za-z]+ trong marionette.py phải thuộc tập lệnh đo thật hợp lệ."""
    tep_marionette = GOC_DICH_VU / "marionette.py"
    cay = ast.parse(tep_marionette.read_text(encoding="utf-8"))
    lenh_re = re.compile(r"^WebDriver:[A-Za-z]+$")
    cac_lenh_ma_nguon = set()
    for nut in ast.walk(cay):
        if isinstance(nut, ast.Constant) and isinstance(nut.value, str):
            if lenh_re.match(nut.value):
                cac_lenh_ma_nguon.add(nut.value)

    assert len(cac_lenh_ma_nguon) > 0
    assert "WebDriver:NewSession" in cac_lenh_ma_nguon

    du_lieu = doc_du_lieu_that()
    lenh_hop_le_da_do = set()
    for k in du_lieu.get("khung", []):
        if "lệnh" in k:
            _, _, than_chuan, _ = xu_ly_khung_tho(k["phản_hồi_thô"])
            obj = json.loads(than_chuan)
            loi = obj[2]
            if not (loi and isinstance(loi, dict) and loi.get("error") == "unknown command"):
                lenh_hop_le_da_do.add(k["lệnh"])

    chua_do = cac_lenh_ma_nguon - lenh_hop_le_da_do
    assert not chua_do, f"lệnh {chua_do} chưa có trong dữ liệu đo thật — đo lại bằng Firefox thật rồi thêm vào du_lieu_that/marionette_that.json"


def test_toan_ven_du_lieu_ghi():
    """Kiểm tra toàn vẹn tệp dữ liệu: tiền tố khớp số byte thật, cấu trúc phản hồi chuẩn, id tăng dần."""
    du_lieu = doc_du_lieu_that()
    cac_khung = du_lieu.get("khung", [])
    assert len(cac_khung) >= 14

    id_truoc = 0
    for kh in cac_khung:
        if "thô" in kh:
            raw = kh["thô"]
            khai_str, _, than = raw.partition(":")
            assert int(khai_str) == len(than.encode("utf-8"))
        elif "phản_hồi_thô" in kh:
            raw = kh["phản_hồi_thô"]
            khai_str, _, than = raw.partition(":")
            khai = int(khai_str)
            that = len(than.encode("utf-8"))
            kh, that, than_chuan, bi_cat = xu_ly_khung_tho(raw)
            if not bi_cat:
                assert khai == that
            obj = json.loads(than_chuan)
            assert isinstance(obj, list) and len(obj) == 4
            assert obj[0] == 1
            msg_id = obj[1]
            assert isinstance(msg_id, int) and msg_id > id_truoc
            id_truoc = msg_id
