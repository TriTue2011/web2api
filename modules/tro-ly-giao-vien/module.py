"""Module tro-ly-giao-vien — trợ lý giáo viên (đợt 1: kiểm kê + phần thuần;
đợt 2: chấm tự luận bằng mô hình).

Nội dung port từ c2a (~/workspace/c2a-nv146/services/agent/) theo LUẬT PORT —
mỗi tệp ghi rõ nguồn ở đầu: tieng_anh.py (teacher_english.py nguyên văn),
cham.py (teacher_assess.py), giao_an.py (teacher.py), mon_hoc.py
(teacher_workspace.py), lop.py (teacher_path.py), muc_luc.py
(teacher_lecture.py + detect_volume), cham_model.py (teacher_assess
._llm_grade — đợt 2).

Dữ liệu học sinh là NHẠY CẢM:
- `student_answer`/bài làm là DỮ LIỆU KHÔNG TIN: chấm thuần ở lớp heuristic,
  chấm model thì được rào trong prompt như dữ liệu (xem cham_model.py); không
  log, không đưa vào phản hồi sau khi strip, KHÔNG lưu vào kết quả chấm.
- Kho đề: <WEB2API_DATA>/du_lieu/tro-ly-giao-vien/<sha256(danh_tinh_id)[:16]>/de/,
  0600, thư mục 0700, cách ly theo danh tính (IDOR giữa các giáo viên bị chặn),
  ghi nguyên tử. Thực thể kho_de có rủi ro `ghi`.
- Đợt 2 — kết quả chấm model nằm ở ../ket_qua/ cùng khuôn 0600/0700; điểm
  model chỉ là ĐỀ XUẤT, giáo viên `xac_nhan_diem` mới thành điểm chính thức.
  Model của lõi được gọi qua loopback /v1/chat/completions bằng khoá khách
  phạm vi hẹp (mẫu NV135/NV163); chưa cấu hình thì hành động chấm model tắt.
"""

from __future__ import annotations

import hashlib
import logging
import random
import re
import secrets
import time
from typing import Any

from app import kho, ngu_canh

from . import cham, cham_model, giao_an, ket_qua_cham, kho_de, lop, mon_hoc, muc_luc, tieng_anh

log = logging.getLogger("web2api.tro_ly_giao_vien")

TRAN_BAI_HS = 4000
TRAN_VAN_BAN = 20000
TRAN_MARKDOWN = 500000
TRAN_MUC_LUC = 500000

DIA_CHI_MAC_DINH = "http://127.0.0.1:3031"
HET_GIO_MODEL = 90.0

_MO_HINH_HOP_LE = re.compile(r"^[^/\\\s]+/[^\s]+$")
_MAU_DIA_CHI = re.compile(r"^https?://(\[[0-9a-fA-F:]+\]|[^/:?#\s]+)(?::\d+)?(?:[/?#]|$)")


def _la_loopback(dia_chi: str) -> bool:
    """Địa chỉ lõi có phải loopback không (chống SSRF). Mẫu NV135/NV163."""
    m = _MAU_DIA_CHI.match(str(dia_chi or "").strip())
    if not m:
        return False
    return m.group(1).strip("[]").lower() in ("127.0.0.1", "localhost", "::1")


class Module:
    """Module trợ lý giáo viên."""

    def __init__(self, manifest: dict[str, Any]):
        self.manifest = manifest
        self.tien_to = manifest.get("tien_to", "tro_ly_giao_vien")
        self._cau_hinh: dict[str, Any] = {}

    def nap(self, cau_hinh: dict[str, Any]) -> None:
        """Nạp cấu hình đã lưu (không gọi mạng)."""
        self._cau_hinh = dict(cau_hinh or {})

    async def cai(self, cau_hinh: dict[str, Any]) -> dict[str, Any]:
        """Kiểm tra cấu hình chấm bằng model (mẫu NV163).

        Rỗng = hợp lệ (chấm model tắt, các hành động thuần vẫn chạy).
        Đã khai model_cham thì cần khoa_khach; dia_chi_v1 chỉ chấp nhận loopback.
        """
        cau_hinh = dict(cau_hinh or {})
        ket_qua: list[dict[str, Any]] = []

        def ghi(khoa: str, hop_le: bool, loi: str | None = None) -> None:
            muc: dict[str, Any] = {"khoa": khoa, "hop_le": hop_le}
            if loi:
                muc["loi"] = loi
            ket_qua.append(muc)

        dia_chi = str(cau_hinh.get("dia_chi_v1") or DIA_CHI_MAC_DINH).strip()
        if _la_loopback(dia_chi):
            ghi("dia_chi_v1", True)
        else:
            ghi("dia_chi_v1", False,
                "chỉ chấp nhận loopback (127.0.0.1/localhost/::1) — chống SSRF")

        model = str(cau_hinh.get("model_cham") or "").strip()
        khoa = str(cau_hinh.get("khoa_khach") or "").strip()
        if model:
            if _MO_HINH_HOP_LE.match(model):
                ghi("model_cham", True)
            else:
                ghi("model_cham", False,
                    "đúng dạng trong GET /v1/models, ví dụ tien_to/ten-model")
            if khoa:
                ghi("khoa_khach", True)
            else:
                ghi("khoa_khach", False,
                    "đã khai model_cham thì cần khoá khách để gọi model")
        else:
            ghi("model_cham", True)
            ghi("khoa_khach", True)  # không chấm model thì không cần khoá

        ok = all(m["hop_le"] for m in ket_qua)
        luu = {k: cau_hinh[k] for k in ("model_cham", "khoa_khach", "dia_chi_v1")
               if k in cau_hinh} if ok else {}
        return {
            "ok": ok,
            "ket_qua_tung_khoa": ket_qua,
            "loi": None if ok else "cấu hình chưa hợp lệ",
            "cau_hinh_luu": luu,
        }

    async def suc_khoe(self) -> dict[str, Any]:
        return {"ok": True, "base_url": None, "khoa": [],
                "model_cham": self._model_kha_dung()}

    def _model_kha_dung(self) -> bool:
        ch = self._cau_hinh
        return bool(str(ch.get("model_cham") or "").strip()
                    and str(ch.get("khoa_khach") or "").strip()
                    and _la_loopback(str(ch.get("dia_chi_v1") or DIA_CHI_MAC_DINH)))

    async def _goi_model(self, tin_nhan: list[dict[str, str]]) -> str:
        """Gọi /v1/chat/completions của chính lõi qua loopback. (mẫu NV135/NV163)

        Test monkeypatch phương thức này bằng model GIẢ (không mạng).
        """
        ch = self._cau_hinh
        dia_chi = str(ch.get("dia_chi_v1") or DIA_CHI_MAC_DINH).strip()
        if not _la_loopback(dia_chi):
            raise cham_model.LoiChamModel(
                "dia_chi_v1 không phải loopback — chặn trước khi gọi (chống SSRF)")
        model = str(ch.get("model_cham") or "").strip()
        khoa = str(ch.get("khoa_khach") or "").strip()
        if not model or not khoa:
            raise cham_model.LoiChamModel(
                "chưa cấu hình model_cham / khoa_khach — cài ở Hệ thống → Module")
        import httpx
        try:
            async with httpx.AsyncClient(timeout=HET_GIO_MODEL) as client:
                r = await client.post(
                    f"{dia_chi.rstrip('/')}/v1/chat/completions",
                    headers={"Authorization": f"Bearer {khoa}"},
                    json={"model": model, "messages": tin_nhan, "stream": False},
                )
        except Exception as exc:
            raise cham_model.LoiChamModel(
                f"không gọi được model của lõi: {type(exc).__name__}") from exc
        if r.status_code >= 400:
            # Không đưa body lỗi vào thông điệp (có thể lẫn nội dung nhạy cảm),
            # càng không bao giờ kèm khoá.
            raise cham_model.LoiChamModel(f"model của lõi trả HTTP {r.status_code}")
        try:
            data = r.json()
            noi_dung = str((data.get("choices") or [{}])[0]
                           .get("message", {}).get("content") or "")
        except Exception as exc:
            raise cham_model.LoiChamModel("phản hồi model không đúng dạng") from exc
        return noi_dung

    async def dung(self) -> None:
        return None

    # ── Chuẩn thiet_bi ─────────────────────────────────────────────────────

    async def thuc_the(self, loc: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        thu_vien = f"{self.tien_to}.thu_vien"
        kho_id = f"{self.tien_to}.kho_de"
        ra = []
        if not loc or thu_vien in loc or loc.get("id") in (None, thu_vien):
            ra.append(
                {
                    "id": thu_vien,
                    "ten": "Thư viện công cụ giáo viên",
                    "loai": "cong_cu",
                    "rui_ro_mac_dinh": "doc",
                    "tinh_nang": [
                        self._tn("ra_de", "Ra một đề tập luyện (không lưu vào kho)",
                                 {"lop": ("integer", "Lớp (1-12)", True),
                                  "mon": ("string", "Mã môn (toan, tviet, van, anh, su, dia, ly, hoa, sinh, sudia)", True),
                                  "chu_de": ("string", "Chủ đề (vd: cộng trong phạm vi 100)", False),
                                  "so_cau": ("integer", "Số câu (1-10)", False),
                                  "do_kho": ("string", "Độ khó: easy|medium|hard", False)}),
                        self._tn("ra_bai_anh", "Ra một câu tiếng Anh theo kỹ năng",
                                 {"lop": ("integer", "Lớp (1-12)", True),
                                  "ky_nang": ("string", "Kỹ năng (vd: present, past, be, vocab:animals)", True)}),
                        self._tn("cham_mot_cau", "Chấm một câu bài làm (thuần, không gọi mô hình)",
                                 {"lop": ("integer", "Lớp (1-12)", False),
                                  "mon": ("string", "Mã môn", False),
                                  "cau_hoi": ("string", "Đề bài (tối đa 4000 ký tự)", True),
                                  "bai_hs": ("string", "Bài làm học sinh (tối đa 4000 ký tự; dữ liệu không tin)", True),
                                  "goi_y_dap_an": ("string", "Gợi ý đáp án (không lộ vào feedback)", False)}),
                        self._tn("cham_de", "Chấm một đề đã lưu trong kho (cần danh tính)",
                                 {"quiz_id": ("string", "Mã đề đã luu_de", True),
                                  "answers": ("object", "Ánh xạ q1..qn → bài làm", True)}),
                        self._tn("band_diem", "Tra band điểm + tiêu chí cho một số điểm",
                                 {"mon": ("string", "Mã môn", False),
                                  "diem_0_10": ("integer", "Điểm 0-10", True),
                                  "lop": ("integer", "Lớp", False)}),
                        self._tn("rubric", "Bảng rubric đầy đủ của một môn (markdown)",
                                 {"mon": ("string", "Mã môn", False),
                                  "lop": ("integer", "Lớp", False)}),
                        self._tn("goi_y", "Gợi ý giải theo bậc (cấp càng cao càng lộ đáp án)",
                                 {"cau_hoi": ("string", "Đề bài", True),
                                  "goi_y_dap_an": ("string", "Gợi ý đáp án", False),
                                  "mon": ("string", "Mã môn", False),
                                  "lop": ("integer", "Lớp", False),
                                  "cap": ("integer", "Bậc 1-3", False)}),
                        self._tn("giao_an_mau", "Mẫu giáo án 6 pha",
                                 {"lop": ("integer", "Lớp (1-12)", True),
                                  "mon": ("string", "Mã môn", True),
                                  "chu_de": ("string", "Chủ đề bài học", True),
                                  "muc_tieu": ("string", "Mục tiêu", False)}),
                        self._tn("mon_theo_lop", "Danh sách môn của một lớp",
                                 {"lop": ("integer", "Lớp (1-12)", True)}),
                        self._tn("xac_dinh_lop", "Suy lớp từ hồ sơ (năm sinh ghi đè bởi lớp khai tay)",
                                 {"ho_so": ("object", "{nam_sinh?: number, lop?: number}", True)}),
                        self._tn("lo_trinh_mach", "Mạch kiến thức của một môn ở một lớp",
                                 {"mon": ("string", "Mã môn", True),
                                  "lop": ("integer", "Lớp (1-12)", True)}),
                        self._tn("muc_luc_markdown", "Rút mục lục từ markdown (markdown là dữ liệu không tin)",
                                 {"markdown": ("string", "Markdown sách (tối đa 500.000 ký tự)", True)}),
                        self._tn("doan_tap", "Đoán tập sách từ tên tệp/tiêu đề",
                                 {"ten": ("string", "Tên tệp hoặc tiêu đề", True)}),
                        self._tn("kich_nang_anh", "Bảng kỹ năng tiếng Anh của một lớp",
                                 {"lop": ("integer", "Lớp (1-12)", True)}),
                        self._tn("doc_tts", "Viết lại công thức/ký hiệu để TTS đọc đúng tiếng Việt",
                                 {"van_ban": ("string", "Văn bản (tối đa 20.000 ký tự)", True)}),
                    ],
                }
            )
        if not loc or kho_id in loc or loc.get("id") in (None, kho_id):
            ra.append(
                {
                    "id": kho_id,
                    "ten": "Kho đề theo giáo viên",
                    "loai": "cong_cu",
                    "rui_ro_mac_dinh": "ghi",
                    "tinh_nang": [
                        self._tn("luu_de", "Lưu một đề vào kho riêng của danh tính (0600)",
                                 {"quiz_id": ("string", "Mã đề (A-Za-z0-9_-)", True),
                                  "de": ("object", "Đề (tối thiểu khóa cau_hoi)", True)}),
                        self._tn("doc_de", "Đọc một đề của chính danh tính",
                                 {"quiz_id": ("string", "Mã đề", True)}),
                        self._tn("liet_ke_de", "Liệt kê mã đề của chính danh tính", {}),
                        self._tn("xoa_de", "Xoá một đề của chính danh tính",
                                 {"quiz_id": ("string", "Mã đề", True)}),
                    ],
                }
            )
        kq_id = f"{self.tien_to}.ket_qua_cham"
        if not loc or kq_id in loc or loc.get("id") in (None, kq_id):
            ra.append(
                {
                    "id": kq_id,
                    "ten": "Chấm tự luận bằng model + kết quả (đợt 2)",
                    "loai": "cong_cu",
                    "rui_ro_mac_dinh": "ghi",
                    "tinh_nang": [
                        self._tn("cham_tu_luan_model",
                                 "Chấm một bài tự luận bằng model của lõi (điểm là ĐỀ XUẤT; cần model_cham + khoa_khach)",
                                 {"de_bai": ("string", "Đề bài (tối đa 2000 ký tự đưa vào prompt)", True),
                                  "bai_hs": ("string", "Bài làm học sinh (tối đa 8000 ký tự; dữ liệu không tin, không lưu)", True),
                                  "barem": ("array", "Danh sách [{tieu_chi, diem_toi_da}] của giáo viên (bỏ trống = 1 tiêu chí «Tổng thể» 10 điểm)", False),
                                  "goi_y_dap_an": ("string", "Gợi ý đáp án (tối đa 1000 ký tự đưa vào prompt)", False),
                                  "mon": ("string", "Mã môn", False),
                                  "lop": ("integer", "Lớp (1-12)", False)}),
                        self._tn("cham_nhieu_bai_model",
                                 "Chấm nhiều bài tự luận trong một lượt (tối đa 5 bài)",
                                 {"de_bai": ("string", "Đề bài chung", True),
                                  "danh_sach_bai": ("array", "Danh sách [{bai_hs}] (1-5 bài)", True),
                                  "barem": ("array", "Danh sách [{tieu_chi, diem_toi_da}]", False),
                                  "goi_y_dap_an": ("string", "Gợi ý đáp án", False),
                                  "mon": ("string", "Mã môn", False),
                                  "lop": ("integer", "Lớp (1-12)", False)}),
                        self._tn("doc_ket_qua", "Đọc một kết quả chấm của chính danh tính",
                                 {"ket_qua_id": ("string", "Mã kết quả", True)}),
                        self._tn("liet_ke_ket_qua", "Liệt kê kết quả chấm của chính danh tính (tóm tắt)", {}),
                        self._tn("xac_nhan_diem",
                                 "Xác nhận điểm cho một kết quả (điểm model là đề xuất; thành điểm chính thức khi xác nhận)",
                                 {"ket_qua_id": ("string", "Mã kết quả", True),
                                  "diem_chinh_thuc": ("number", "Điểm chính thức (bỏ trống = dùng điểm đề xuất của model)", False)}),
                        self._tn("xoa_ket_qua", "Xoá một kết quả chấm của chính danh tính",
                                 {"ket_qua_id": ("string", "Mã kết quả", True)}),
                    ],
                }
            )
        return ra

    def _tn(self, hanh_dong: str, mo_ta: str, ts: dict[str, tuple[str, str, bool]]) -> dict[str, Any]:
        props = {k: {"type": t, "description": m} for k, (t, m, _) in ts.items()}
        req = [k for k, (_, _, r) in ts.items() if r]
        return {
            "hanh_dong": hanh_dong,
            "mo_ta": mo_ta,
            "tham_so": {"type": "object", "required": req,
                        "additionalProperties": False, "properties": props},
        }

    async def trang_thai(self, ids: list[str]) -> dict[str, dict[str, Any]]:
        ra = {}
        for tid in (f"{self.tien_to}.thu_vien", f"{self.tien_to}.kho_de",
                    f"{self.tien_to}.ket_qua_cham"):
            if not ids or tid in ids:
                ra[tid] = {
                    "gia_tri": "san_sang", "don_vi": None,
                    "luc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "hop_le": True,
                }
        return ra

    # ── Thực thi ───────────────────────────────────────────────────────────

    async def thuc_hien(self, id: str, hanh_dong: str, tham_so: dict[str, Any] | None) -> dict[str, Any]:
        if id == f"{self.tien_to}.thu_vien":
            return self._thuc_hien_thu_vien(hanh_dong, tham_so or {})
        if id == f"{self.tien_to}.kho_de":
            return self._thuc_hien_kho_de(hanh_dong, tham_so or {})
        if id == f"{self.tien_to}.ket_qua_cham":
            return await self._thuc_hien_ket_qua(hanh_dong, tham_so or {})
        return {"ok": False, "loi": f"Không tìm thấy thực thể {id!r}"}

    # –– thu_vien (chỉ đọc) ––

    def _thuc_hien_thu_vien(self, hanh_dong: str, ts: dict[str, Any]) -> dict[str, Any]:
        try:
            if hanh_dong == "ra_de":
                lop = self._lop(ts.get("lop"))
                mon = mon_hoc.chuan_mon(str(ts.get("mon") or "")) or "toan"
                so_cau = max(1, min(int(ts.get("so_cau") or 5), 10))
                do_kho = str(ts.get("do_kho") or "medium")
                de = cham.ra_de(lop=lop, mon=mon, chu_de=str(ts.get("chu_de") or ""),
                                so_cau=so_cau, do_kho=do_kho)
                return {"ok": True, "quiz_id": de["quiz_id"],
                        "van_ban": cham.format_quiz_for_student(de)}
            if hanh_dong == "ra_bai_anh":
                lop = self._lop(ts.get("lop"))
                ky_nang = str(ts.get("ky_nang") or "").strip()
                bang = tieng_anh.english_skill_map(lop)
                hop_le = {s["id"] for s in bang["skills"]}
                if ky_nang not in hop_le:
                    return {"ok": False, "loi": f"kỹ năng không hợp lệ, chọn trong: {sorted(hop_le)}"}
                cau = tieng_anh.make_english_item(lop, ky_nang, random.Random())
                return {"ok": True, "cau": cau}
            if hanh_dong == "cham_mot_cau":
                lop = self._lop(ts.get("lop") or 5)
                mon = mon_hoc.chuan_mon(str(ts.get("mon") or "")) or "toan"
                cau_hoi = str(ts.get("cau_hoi") or "")[:TRAN_BAI_HS]
                bai_hs = str(ts.get("bai_hs") or "")[:TRAN_BAI_HS]
                if not cau_hoi or not bai_hs:
                    return {"ok": False, "loi": "thiếu cau_hoi hoặc bai_hs"}
                kq = cham.cham_mot_cau(cau_hoi, bai_hs,
                                       str(ts.get("goi_y_dap_an") or "")[:TRAN_BAI_HS],
                                       mon=mon, lop=lop)
                kq.pop("student_answer", None)  # bài học sinh: không phản hồi lại
                kq.pop("answer_hint", None)
                return {"ok": True, "ket_qua": kq}
            if hanh_dong == "cham_de":
                khoa, loi = self._khoa_danh_tinh()
                if loi:
                    return {"ok": False, "loi": loi, "ma_loi": "missing_context"}
                quiz_id = str(ts.get("quiz_id") or "")
                d = kho_de.doc_de(self._goc_de(), khoa, quiz_id)
                if not d["ok"]:
                    return {"ok": False, "loi": d["loi"]}
                answers = {str(k): str(v)[:TRAN_BAI_HS]
                           for k, v in (ts.get("answers") or {}).items()}
                kq = cham.grade_quiz(d["de"], answers)
                for r in kq.get("details") or []:
                    r.pop("student_answer", None)
                    r.pop("answer_hint", None)
                return {"ok": True, "ket_qua": kq}
            if hanh_dong == "band_diem":
                mon = mon_hoc.chuan_mon(str(ts.get("mon") or "")) or "toan"
                diem = int(ts.get("diem_0_10") or 0)
                return {"ok": True, "ket_qua": cham.band_diem(diem, mon=mon)}
            if hanh_dong == "rubric":
                mon = mon_hoc.chuan_mon(str(ts.get("mon") or "")) or "toan"
                return {"ok": True, "van_ban": cham.bang_rubric(mon=mon)}
            if hanh_dong == "goi_y":
                cap = max(1, min(int(ts.get("cap") or 1), 3))
                mon = mon_hoc.chuan_mon(str(ts.get("mon") or "")) or "toan"
                g = cham.goi_y_bac_thang(str(ts.get("cau_hoi") or "")[:TRAN_BAI_HS],
                                         str(ts.get("goi_y_dap_an") or "")[:TRAN_BAI_HS],
                                         mon=mon, lop=self._lop(ts.get("lop") or 5), cap=cap)
                return {"ok": True, "ket_qua": g}
            if hanh_dong == "giao_an_mau":
                md = giao_an.mau_giao_an(lop=self._lop(ts.get("lop")),
                                         mon=mon_hoc.chuan_mon(str(ts.get("mon") or "")) or "toan",
                                         chu_de=str(ts.get("chu_de") or ""),
                                         muc_tieu=str(ts.get("muc_tieu") or ""))
                return {"ok": True, "van_ban": md}
            if hanh_dong == "mon_theo_lop":
                lop_n = self._lop(ts.get("lop"))
                return {"ok": True, "mon": [{"ma": m, "nhan": mon_hoc.nhan(m)}
                                            for m in mon_hoc.subjects_for(lop_n)]}
            if hanh_dong == "xac_dinh_lop":
                hs = ts.get("ho_so") or {}
                if not isinstance(hs, dict):
                    return {"ok": False, "loi": "ho_so phải là object"}
                return {"ok": True, "ket_qua": lop.xac_dinh_lop(
                    {"nam_sinh": hs.get("nam_sinh"), "lop": hs.get("lop")})}
            if hanh_dong == "lo_trinh_mach":
                mon = mon_hoc.chuan_mon(str(ts.get("mon") or "")) or "toan"
                return {"ok": True, "mach": lop.mach_kien_thuc(mon, self._lop(ts.get("lop")))}
            if hanh_dong == "muc_luc_markdown":
                md = str(ts.get("markdown") or "")[:TRAN_MUC_LUC]
                return {"ok": True, "muc_luc": muc_luc.muc_luc_tu_markdown(md)}
            if hanh_dong == "doan_tap":
                return {"ok": True, "tap": muc_luc.doan_tap(str(ts.get("ten") or ""))}
            if hanh_dong == "kich_nang_anh":
                return {"ok": True, "ket_qua": tieng_anh.english_skill_map(self._lop(ts.get("lop")))}
            if hanh_dong == "doc_tts":
                vb = str(ts.get("van_ban") or "")[:TRAN_VAN_BAN]
                if not vb:
                    return {"ok": False, "loi": "thiếu van_ban"}
                return {"ok": True, "van_ban": giao_an.verbalize_cho_tts(vb)}
            return {"ok": False, "loi": f"Hành động «{hanh_dong}» không hỗ trợ"}
        except ValueError as e:
            return {"ok": False, "loi": str(e)}
        except Exception as e:
            log.warning("Lỗi hành động %s: %s", hanh_dong, type(e).__name__)
            return {"ok": False, "loi": "lỗi nội bộ khi thực hiện"}

    # –– kho_de (ghi — cần danh tính, cách ly danh tính) ––

    def _khoa_danh_tinh(self) -> tuple[str | None, str | None]:
        nc = ngu_canh.lay()
        if nc is None:
            return None, "thiếu ngữ cảnh gọi (missing_context)"
        did = nc.danh_tinh.get("id") if isinstance(nc.danh_tinh, dict) else None
        if not did:
            return None, "ngữ cảnh không có danh tính"
        return hashlib.sha256(str(did).encode("utf-8")).hexdigest()[:16], None

    def _goc_de(self):
        return kho.thu_muc_du_lieu() / "du_lieu"

    def _thuc_hien_kho_de(self, hanh_dong: str, ts: dict[str, Any]) -> dict[str, Any]:
        khoa, loi = self._khoa_danh_tinh()
        if loi:
            return {"ok": False, "loi": loi, "ma_loi": "missing_context"}
        goc = self._goc_de()
        try:
            if hanh_dong == "luu_de":
                quiz_id = str(ts.get("quiz_id") or "")
                de = ts.get("de")
                if not isinstance(de, dict) or not isinstance(de.get("cau_hoi"), list):
                    return {"ok": False, "loi": "de phải có khóa cau_hoi là danh sách"}
                r = kho_de.luu_de(goc, khoa, quiz_id, de)
                if not r["ok"]:
                    return {"ok": False, "loi": r["loi"]}
                return {"ok": True, "quiz_id": quiz_id}
            if hanh_dong == "doc_de":
                return kho_de.doc_de(goc, khoa, str(ts.get("quiz_id") or ""))
            if hanh_dong == "liet_ke_de":
                return kho_de.liet_ke_de(goc, khoa)
            if hanh_dong == "xoa_de":
                return kho_de.xoa_de(goc, khoa, str(ts.get("quiz_id") or ""))
            return {"ok": False, "loi": f"Hành động «{hanh_dong}» không hỗ trợ"}
        except Exception as e:
            log.warning("Lỗi kho đề %s: %s", hanh_dong, type(e).__name__)
            return {"ok": False, "loi": "lỗi nội bộ khi thao tác kho đề"}

    # –– ket_qua_cham (ghi — chấm model + kết quả, cần danh tính) ––

    async def _cham_mot_bai(self, de_bai: str, bai_hs: str,
                            barem: list[dict[str, Any]], mon: str, lop: int,
                            goi_y: str) -> dict[str, Any]:
        """Chấm 1 bài bằng model. Bài là DỮ LIỆU KHÔNG TIN (xem cham_model).

        Trả dict đã chuẩn hoá (diem_de_xuat tự cộng từ barem). Lỗi nào cũng
        ném LoiChamModel — không bao giờ bịa điểm thay thế.
        """
        bai = str(bai_hs or "")
        if not bai.strip():
            raise cham_model.LoiChamModel("thiếu bài làm học sinh")
        if len(bai) > cham_model.TRAN_BAI_MODEL:
            raise cham_model.LoiChamModel(
                f"bài làm vượt trần {cham_model.TRAN_BAI_MODEL} ký tự — "
                "chia nhỏ hoặc rút gọn bài rồi chấm lại")
        if not de_bai.strip():
            raise cham_model.LoiChamModel("thiếu đề bài")
        tin_nhan = cham_model.dung_tin_nhan(
            de_bai, goi_y, bai, barem, mon, lop)
        tra_ve = await self._goi_model(tin_nhan)
        data = cham_model.boc_json(tra_ve)
        if data is None:
            raise cham_model.LoiChamModel(
                "model trả về không phải JSON hợp lệ — không chấm được")
        return cham_model.chuan_hoa_ket_qua(data, barem)

    async def _thuc_hien_ket_qua(self, hanh_dong: str, ts: dict[str, Any]) -> dict[str, Any]:
        khoa, loi = self._khoa_danh_tinh()
        if loi:
            return {"ok": False, "loi": loi, "ma_loi": "missing_context"}
        goc = self._goc_de()
        try:
            if hanh_dong == "cham_tu_luan_model":
                mon = mon_hoc.chuan_mon(str(ts.get("mon") or "")) or "van"
                lop = self._lop(ts.get("lop") or 5)
                barem = cham_model.chuan_barem(ts.get("barem"))
                kq = await self._cham_mot_bai(
                    str(ts.get("de_bai") or ""), ts.get("bai_hs") or "",
                    barem, mon, lop, str(ts.get("goi_y_dap_an") or ""))
                ban_ghi = self._ban_ghi_ket_qua(ts, barem, mon, lop, kq)
                luu = ket_qua_cham.luu_ket_qua(goc, khoa, ban_ghi["id"], ban_ghi)
                if not luu["ok"]:
                    return {"ok": False, "loi": luu["loi"]}
                return {"ok": True, "ket_qua_id": ban_ghi["id"], "ket_qua": ban_ghi}
            if hanh_dong == "cham_nhieu_bai_model":
                ds = ts.get("danh_sach_bai")
                if not isinstance(ds, list) or not ds:
                    return {"ok": False, "loi": "danh_sach_bai phải là danh sách 1-5 bài"}
                if len(ds) > cham_model.TRAN_SO_BAI_LUOT:
                    return {"ok": False,
                            "loi": f"vượt trần {cham_model.TRAN_SO_BAI_LUOT} bài/lượt"}
                mon = mon_hoc.chuan_mon(str(ts.get("mon") or "")) or "van"
                lop = self._lop(ts.get("lop") or 5)
                barem = cham_model.chuan_barem(ts.get("barem"))
                ket_qua_ds: list[dict[str, Any]] = []
                for muc in ds:
                    bai = (muc or {}).get("bai_hs") if isinstance(muc, dict) else None
                    try:
                        kq = await self._cham_mot_bai(
                            str(ts.get("de_bai") or ""), bai or "",
                            barem, mon, lop, str(ts.get("goi_y_dap_an") or ""))
                        ban_ghi = self._ban_ghi_ket_qua(ts, barem, mon, lop, kq)
                        luu = ket_qua_cham.luu_ket_qua(goc, khoa, ban_ghi["id"], ban_ghi)
                        if not luu["ok"]:
                            ket_qua_ds.append({"ok": False, "loi": luu["loi"]})
                        else:
                            ket_qua_ds.append({"ok": True, "ket_qua_id": ban_ghi["id"],
                                               "ket_qua": ban_ghi})
                    except cham_model.LoiChamModel as e:
                        ket_qua_ds.append({"ok": False, "loi": str(e)})
                return {"ok": any(m["ok"] for m in ket_qua_ds), "ket_qua": ket_qua_ds}
            if hanh_dong == "doc_ket_qua":
                return ket_qua_cham.doc_ket_qua(goc, khoa, str(ts.get("ket_qua_id") or ""))
            if hanh_dong == "liet_ke_ket_qua":
                return ket_qua_cham.liet_ke_ket_qua(goc, khoa)
            if hanh_dong == "xac_nhan_diem":
                doc = ket_qua_cham.doc_ket_qua(goc, khoa, str(ts.get("ket_qua_id") or ""))
                if not doc["ok"]:
                    return {"ok": False, "loi": doc["loi"]}
                bg = doc["ket_qua"]
                tong = sum(float(m["diem_toi_da"]) for m in bg.get("barem") or [])
                diem_in = ts.get("diem_chinh_thuc")
                if diem_in is None:
                    diem = float(bg["diem_de_xuat"])
                elif isinstance(diem_in, bool) or not isinstance(diem_in, (int, float)):
                    return {"ok": False, "loi": "diem_chinh_thuc phải là số"}
                else:
                    diem = float(diem_in)
                if not (0 <= diem <= tong):
                    return {"ok": False,
                            "loi": f"diem_chinh_thuc phải trong [0, {tong:g}] (tổng barem)"}
                bg["diem_chinh_thuc"] = round(diem, 2)
                bg["trang_thai"] = "da_xac_nhan"
                bg["luc_xac_nhan"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                luu = ket_qua_cham.luu_ket_qua(goc, khoa, bg["id"], bg)
                if not luu["ok"]:
                    return {"ok": False, "loi": luu["loi"]}
                return {"ok": True, "ket_qua_id": bg["id"], "ket_qua": bg}
            if hanh_dong == "xoa_ket_qua":
                return ket_qua_cham.xoa_ket_qua(goc, khoa, str(ts.get("ket_qua_id") or ""))
            return {"ok": False, "loi": f"Hành động «{hanh_dong}» không hỗ trợ"}
        except cham_model.LoiChamModel as e:
            return {"ok": False, "loi": str(e)}
        except ValueError as e:
            return {"ok": False, "loi": str(e)}
        except Exception as e:
            log.warning("Lỗi kết quả chấm %s: %s", hanh_dong, type(e).__name__)
            return {"ok": False, "loi": "lỗi nội bộ khi chấm/lưu kết quả"}

    def _ban_ghi_ket_qua(self, ts: dict[str, Any], barem: list[dict[str, Any]],
                         mon: str, lop: int, kq: dict[str, Any]) -> dict[str, Any]:
        """Bản ghi lưu đĩa: KHÔNG chứa bài làm học sinh, không chứa khoá."""
        return {
            "id": f"kq-{secrets.token_hex(4)}",
            "de_bai": str(ts.get("de_bai") or "")[:500],
            "mon": mon,
            "lop": lop,
            "barem": barem,
            "tieu_chi": kq["tieu_chi"],
            "diem_de_xuat": kq["diem_de_xuat"],
            "nhan_xet_chung": kq["nhan_xet_chung"],
            "da_kep_diem": kq["da_kep_diem"],
            "trang_thai": "de_xuat",
            "diem_chinh_thuc": None,
            "model": str(self._cau_hinh.get("model_cham") or ""),
            "luc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

    # –– helper ––

    @staticmethod
    def _lop(v: Any) -> int:
        try:
            n = int(v)
        except (TypeError, ValueError):
            raise ValueError("lop phải là số 1-12")
        if n not in mon_hoc.GRADES:
            raise ValueError("lop phải là số 1-12")
        return n
