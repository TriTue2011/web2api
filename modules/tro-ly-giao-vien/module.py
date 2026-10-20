"""Module tro-ly-giao-vien — trợ lý giáo viên (đợt 1: kiểm kê + phần thuần).

Nội dung port từ c2a (~/workspace/c2a-nv146/services/agent/) theo LUẬT PORT —
mỗi tệp ghi rõ nguồn ở đầu: tieng_anh.py (teacher_english.py nguyên văn),
cham.py (teacher_assess.py), giao_an.py (teacher.py), mon_hoc.py
(teacher_workspace.py), lop.py (teacher_path.py), muc_luc.py
(teacher_lecture.py + detect_volume).

Dữ liệu học sinh là NHẠY CẢM:
- `student_answer`/bài làm là DỮ LIỆU KHÔNG TIN: chỉ đối chiếu đáp án ở lớp thuần
  (không gọi mô hình đợt này), không log, không đưa vào phản hồi sau khi strip.
- Kho đề: <WEB2API_DATA>/du_lieu/tro-ly-giao-vien/<sha256(danh_tinh_id)[:16]>/de/,
  0600, thư mục 0700, cách ly theo danh tính (IDOR giữa các giáo viên bị chặn),
  ghi nguyên tử. Thực thể kho_de có rủi ro `ghi`.
"""

from __future__ import annotations

import hashlib
import logging
import random
import time
from typing import Any

from app import kho, ngu_canh

from . import cham, giao_an, kho_de, lop, mon_hoc, muc_luc, tieng_anh

log = logging.getLogger("web2api.tro_ly_giao_vien")

TRAN_BAI_HS = 4000
TRAN_VAN_BAN = 20000
TRAN_MARKDOWN = 500000
TRAN_MUC_LUC = 500000


class Module:
    """Module trợ lý giáo viên."""

    def __init__(self, manifest: dict[str, Any]):
        self.manifest = manifest
        self.tien_to = manifest.get("tien_to", "tro_ly_giao_vien")
        self._cau_hinh: dict[str, Any] = {}

    def nap(self, cau_hinh: dict[str, Any]) -> None:
        self._cau_hinh = cau_hinh or {}

    async def cai(self, cau_hinh: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True, "ket_qua_tung_khoa": [], "cau_hinh_luu": {}}

    async def suc_khoe(self) -> dict[str, Any]:
        return {"ok": True, "base_url": None, "khoa": []}

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
        for tid in (f"{self.tien_to}.thu_vien", f"{self.tien_to}.kho_de"):
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
