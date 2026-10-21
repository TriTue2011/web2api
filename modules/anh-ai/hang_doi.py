# -*- coding: utf-8 -*-
"""
Hàng đợi tác vụ ảnh cho module web2api `anh-ai`.

Nguồn: c2a services/image_task_service.py, commit d879b03 (lớp ImageTaskService).

Những gì đã đổi so với bản c2a:
- Đổi tên lớp: ImageTaskService → HangDoiTacVu; mọi tên phụ trợ sang tiếng Việt.
- Đổi trạng thái theo spec NV192: "success"/"error" → "done"/"failed";
  trạng thái còn lại giữ nguyên: "queued", "running".
  TERMINAL = {"done", "failed"}.
- Constructor mới: nhận đường dẫn file JSON + 2 hàm xử lý đồng bộ
  (xu_ly_tao / xu_ly_sua, nhận payload dict, trả dict có "data" là list);
  giới hạn đồng thời toàn hệ thống (dong_thoi_toi_da, mặc định 6) và theo
  từng danh tính (dong_thoi_moi_chu, mặc định 2) cấu hình qua constructor
  thay vì hằng số toàn cục.
- Phục hồi sau khởi động lại: queued/running → "failed", error
  "Dịch vụ đã khởi động lại — tác vụ ảnh chưa xong bị gián đoạn" (như c2a).
- Thêm phương thức public huy() theo spec NV192 (c2a không có):
  queued → đánh dấu "failed", error "Đã huỷ bởi người dùng";
  thread nếu đã chạy thì _chay_task kiểm tra cờ huỷ trước khi gọi handler.
- Thêm phương thức public don_dep() theo số ngày giữ (c2a chỉ tự dọn ngầm).
- BỎ toàn bộ phụ thuộc c2a: không import services.config, services.log_service,
  services.content_filter, services.protocol; không dùng DATA_DIR global;
  logging bằng stdlib (không log prompt dài quá 120 ký tự, không log khoá).
- Mode dùng tiếng Việt khớp module: "tao"/"sua" (thay cho "generate"/"edit").
- Validate ma (1..128 ký tự) và prompt (1..8000 ký tự) → ValueError.
- Idempotency theo cặp (owner, ma): đã có thì trả public task hiện tại.
- Mọi thao tác chỉ thấy tác vụ của đúng owner (chống IDOR).

Logic giữ nguyên từ c2a: semaphore giới hạn đồng thời + đếm theo chủ sở hữu,
thread daemon chạy tác vụ, nhả slot trong finally SAU khi việc thật xong,
lưu JSON nguyên tử (ghi .tmp rồi replace), RLock cho thread-safety.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

TRANG_THAI_XEP_HANG = "queued"
TRANG_THAI_DANG_CHAY = "running"
TRANG_THAI_XONG = "done"
TRANG_THAI_LOI = "failed"
TERMINAL = {TRANG_THAI_XONG, TRANG_THAI_LOI}
CHUA_XONG = {TRANG_THAI_XEP_HANG, TRANG_THAI_DANG_CHAY}

MODE_TAO = "tao"
MODE_SUA = "sua"

GIU_NGAY_MAC_DINH = 30


class TaskQueueFull(RuntimeError):
    """Hết chỗ chạy tác vụ ảnh — người gọi trả 429, KHÔNG xếp hàng vô hạn."""

    def __init__(self, ly_do: str) -> None:
        self.ly_do = ly_do
        super().__init__(ly_do)


def _gio_hien_tai_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _dau_thoi_gian(value: object) -> float:
    if not isinstance(value, str) or not value.strip():
        return 0.0
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value[:26], fmt).timestamp()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def _lam_sach(value: object, default: str = "") -> str:
    return str(value or default).strip()


def _owner(danh_tinh: dict[str, object] | str) -> str:
    if isinstance(danh_tinh, str):
        return _lam_sach(danh_tinh) or "anonymous"
    if isinstance(danh_tinh, dict):
        return _lam_sach(danh_tinh.get("id")) or "anonymous"
    return "anonymous"


def _khoa_tac_vu(owner: str, ma: str) -> str:
    return f"{owner}:{ma}"


def _rut_gon_prompt(prompt: str, gioi_han: int = 120) -> str:
    text = _lam_sach(prompt)
    if len(text) > gioi_han:
        return text[:gioi_han] + "…"
    return text


def _cong_khai(task: dict[str, Any]) -> dict[str, Any]:
    item = {
        "id": task.get("id"),
        "status": task.get("status"),
        "mode": task.get("mode"),
        "model": task.get("model"),
        "size": task.get("size"),
        "created_at": task.get("created_at"),
        "updated_at": task.get("updated_at"),
    }
    if task.get("data") is not None:
        item["data"] = task.get("data")
    if task.get("error"):
        item["error"] = task.get("error")
    return item


class HangDoiTacVu:
    """Hàng đợi tác vụ tạo/sửa ảnh cho module anh-ai."""

    def __init__(
        self,
        duong: Path,
        *,
        xu_ly_tao: Callable[[dict], dict],
        xu_ly_sua: Callable[[dict], dict],
        dong_thoi_toi_da: int = 6,
        dong_thoi_moi_chu: int = 2,
        giu_ngay_getter: Callable[[], int] | None = None,
    ):
        self.duong = duong
        self.xu_ly_tao = xu_ly_tao
        self.xu_ly_sua = xu_ly_sua
        self.dong_thoi_toi_da = max(1, int(dong_thoi_toi_da))
        self.dong_thoi_moi_chu = max(1, int(dong_thoi_moi_chu))
        self.giu_ngay_getter = giu_ngay_getter or (lambda: GIU_NGAY_MAC_DINH)
        self._khoa = threading.RLock()
        self._tac_vu: dict[str, dict[str, Any]] = {}
        # Trần đồng thời: một semaphore chung + đếm theo chủ sở hữu. Giữ slot tới
        # khi tác vụ THỰC SỰ xong (nhả trong finally của _chay_task), nếu không
        # thì trần chỉ giới hạn tốc độ tạo thread chứ không giới hạn số thread.
        self._khe = threading.BoundedSemaphore(self.dong_thoi_toi_da)
        self._dang_chay: dict[str, int] = {}
        self.duong.parent.mkdir(parents=True, exist_ok=True)
        with self._khoa:
            self._tac_vu = self._tai_khoa()
            doi = self._phuc_hoi_khoa()
            doi = self._don_dep_khoa(self._giu_ngay()) or doi
            if doi:
                self._luu_khoa()

    # ---------- API public ----------

    def gui_tao(
        self,
        danh_tinh: dict[str, object] | str,
        *,
        ma: str,
        prompt: str,
        model: str,
        size: str | None = None,
    ) -> dict:
        self._validate(ma, prompt)
        payload = {
            "prompt": prompt,
            "model": model,
            "size": size,
        }
        return self._gui(_owner(danh_tinh), ma=ma, mode=MODE_TAO, payload=payload)

    def gui_sua(
        self,
        danh_tinh: dict[str, object] | str,
        *,
        ma: str,
        prompt: str,
        model: str,
        size: str | None = None,
        anh: list[tuple[bytes, str, str]] | None = None,
    ) -> dict:
        self._validate(ma, prompt)
        payload = {
            "prompt": prompt,
            "model": model,
            "size": size,
            "anh": list(anh) if anh else [],
        }
        return self._gui(_owner(danh_tinh), ma=ma, mode=MODE_SUA, payload=payload)

    def lay(self, danh_tinh: dict[str, object] | str, ma: str) -> dict | None:
        owner = _owner(danh_tinh)
        key = _khoa_tac_vu(owner, _lam_sach(ma))
        with self._khoa:
            task = self._tac_vu.get(key)
            return _cong_khai(task) if task is not None else None

    def liet_ke(self, danh_tinh: dict[str, object] | str, gioi_han: int = 50) -> list[dict]:
        owner = _owner(danh_tinh)
        try:
            gioi_han = max(1, int(gioi_han))
        except (TypeError, ValueError):
            gioi_han = 50
        with self._khoa:
            if self._don_dep_khoa(self._giu_ngay()):
                self._luu_khoa()
            items = [
                _cong_khai(task)
                for task in self._tac_vu.values()
                if task.get("owner_id") == owner
            ]
        items.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        return items[:gioi_han]

    def huy(self, danh_tinh: dict[str, object] | str, ma: str) -> dict:
        """Huỷ một tác vụ. Chỉ huỷ được khi còn ở trạng thái queued."""
        owner = _owner(danh_tinh)
        key = _khoa_tac_vu(owner, _lam_sach(ma))
        with self._khoa:
            task = self._tac_vu.get(key)
            if task is None:
                return {"ok": False, "ly_do": "không thấy tác vụ"}
            trang_thai = task.get("status")
            if trang_thai == TRANG_THAI_XEP_HANG:
                task["da_huy"] = True
                task["status"] = TRANG_THAI_LOI
                task["error"] = "Đã huỷ bởi người dùng"
                task["updated_at"] = _gio_hien_tai_iso()
                self._luu_khoa()
                log.info("huy tac vu anh: owner=%s ma=%s", owner, task.get("id"))
                return {"ok": True}
            if trang_thai == TRANG_THAI_DANG_CHAY:
                return {"ok": False, "ly_do": "đang chạy, không huỷ được"}
            return {"ok": False, "ly_do": "tác vụ đã xong"}

    def don_dep(self, giu_ngay: int | None = None) -> dict:
        """Xoá tác vụ đã ở trạng thái terminal cũ hơn số ngày giữ."""
        if giu_ngay is None:
            giu_ngay = self._giu_ngay()
        with self._khoa:
            da_xoa = self._don_dep_khoa(giu_ngay)
            if da_xoa:
                self._luu_khoa()
        log.info("don dep tac vu anh: da_xoa=%d giu_ngay=%s", da_xoa, giu_ngay)
        return {"ok": True, "da_xoa": da_xoa}

    def dong(self) -> None:
        """Giữ chỗ cho tương thích vòng đời module — các thread là daemon
        nên không cần dọn gì ở đây."""

    # ---------- nội bộ ----------

    def _validate(self, ma: str, prompt: str) -> None:
        ma_sach = _lam_sach(ma)
        if not (1 <= len(ma_sach) <= 128):
            raise ValueError("ma phải từ 1 đến 128 ký tự")
        prompt_sach = _lam_sach(prompt)
        if not (1 <= len(prompt_sach) <= 8000):
            raise ValueError("prompt phải từ 1 đến 8000 ký tự")

    def _gui(self, owner: str, *, ma: str, mode: str, payload: dict[str, Any]) -> dict:
        ma_sach = _lam_sach(ma)
        key = _khoa_tac_vu(owner, ma_sach)
        now = _gio_hien_tai_iso()
        nen_bat_dau = False
        with self._khoa:
            da_don = self._don_dep_khoa(self._giu_ngay())
            task = self._tac_vu.get(key)
            if task is not None:
                if da_don:
                    self._luu_khoa()
                return _cong_khai(task)
            task = {
                "id": ma_sach,
                "owner_id": owner,
                "status": TRANG_THAI_XEP_HANG,
                "mode": mode,
                "model": _lam_sach(payload.get("model"), "gpt-image-2"),
                "size": _lam_sach(payload.get("size")),
                "created_at": now,
                "updated_at": now,
            }
            self._tac_vu[key] = task
            self._luu_khoa()
            nen_bat_dau = True

        if nen_bat_dau:
            try:
                self._giu_khe(owner)
            except TaskQueueFull:
                # Bỏ tác vụ vừa ghi: để lại trạng thái "queued" mà không có gì
                # chạy thì nó treo mãi và người dùng không gửi lại được (khoá
                # idempotency đã bị chiếm).
                with self._khoa:
                    self._tac_vu.pop(key, None)
                    self._luu_khoa()
                raise
            thread = threading.Thread(
                target=self._chay_xong,
                args=(key, mode, payload, owner),
                name=f"anh-ai-{ma_sach[:16]}",
                daemon=True,
            )
            thread.start()
            log.info(
                "gui tac vu anh: owner=%s ma=%s mode=%s model=%s prompt=%r",
                owner,
                ma_sach,
                mode,
                task.get("model"),
                _rut_gon_prompt(str(payload.get("prompt", ""))),
            )
        return _cong_khai(task)

    def _giu_khe(self, owner: str) -> None:
        """Chiếm một slot chạy. Hết chỗ → TaskQueueFull (người gọi trả 429)."""
        with self._khoa:
            dang = self._dang_chay.get(owner, 0)
            if dang >= self.dong_thoi_moi_chu:
                raise TaskQueueFull(
                    f"Bạn đang có {dang} tác vụ ảnh chạy dở (trần {self.dong_thoi_moi_chu}). "
                    "Chờ xong một tác vụ rồi gửi tiếp."
                )
        if not self._khe.acquire(blocking=False):
            raise TaskQueueFull(
                f"Hệ thống đang chạy đủ {self.dong_thoi_toi_da} tác vụ ảnh. Thử lại sau ít phút."
            )
        with self._khoa:
            self._dang_chay[owner] = self._dang_chay.get(owner, 0) + 1

    def _nha_khe(self, owner: str) -> None:
        with self._khoa:
            con = self._dang_chay.get(owner, 0) - 1
            if con > 0:
                self._dang_chay[owner] = con
            else:
                self._dang_chay.pop(owner, None)
        try:
            self._khe.release()
        except ValueError:
            pass

    def _chay_xong(self, key: str, mode: str, payload: dict[str, Any], owner: str) -> None:
        try:
            self._chay_task(key, mode, payload)
        finally:
            # Nhả slot ở ĐÂY, không phải ngay sau khi start thread: giữ tới lúc
            # việc thật xong mới là trần đồng thời, nhả sớm là vô tác dụng.
            if owner:
                self._nha_khe(owner)

    def _chay_task(self, key: str, mode: str, payload: dict[str, Any]) -> None:
        bat_dau = time.time()
        with self._khoa:
            task = self._tac_vu.get(key)
            if task is None:
                return
            # Người dùng đã huỷ khi tác vụ còn queued → bỏ qua, không gọi handler.
            if task.get("da_huy"):
                return
            task["status"] = TRANG_THAI_DANG_CHAY
            task["error"] = ""
            task["updated_at"] = _gio_hien_tai_iso()
            model = task.get("model")
            owner = task.get("owner_id")
            ma = task.get("id")
            self._luu_khoa()
        try:
            xu_ly = self.xu_ly_sua if mode == MODE_SUA else self.xu_ly_tao
            ket_qua = xu_ly(payload)
            if not isinstance(ket_qua, dict):
                raise RuntimeError("hàm xử lý ảnh trả về kết quả không hợp lệ")
            data = ket_qua.get("data")
            if not isinstance(data, list) or not data:
                thong_bao = _lam_sach(ket_qua.get("message")) or "không nhận được dữ liệu ảnh"
                raise RuntimeError(thong_bao)
            self._cap_nhat(key, status=TRANG_THAI_XONG, data=data, error="")
            log.info(
                "tac vu anh xong: owner=%s ma=%s mode=%s model=%s %dms",
                owner,
                ma,
                mode,
                model,
                int((time.time() - bat_dau) * 1000),
            )
        except Exception as exc:
            thong_bao = str(exc) or "tác vụ ảnh thất bại"
            self._cap_nhat(key, status=TRANG_THAI_LOI, error=thong_bao, data=[])
            log.warning(
                "tac vu anh loi: owner=%s ma=%s mode=%s model=%s: %s",
                owner,
                ma,
                mode,
                model,
                thong_bao[:200],
            )

    def _cap_nhat(self, key: str, **cap_nhat: Any) -> None:
        with self._khoa:
            task = self._tac_vu.get(key)
            if task is None:
                return
            task.update(cap_nhat)
            task["updated_at"] = _gio_hien_tai_iso()
            self._luu_khoa()

    def _tai_khoa(self) -> dict[str, dict[str, Any]]:
        if not self.duong.exists():
            return {}
        try:
            raw = json.loads(self.duong.read_text(encoding="utf-8"))
        except Exception:
            return {}
        danh_sach = raw.get("tasks") if isinstance(raw, dict) else raw
        if not isinstance(danh_sach, list):
            return {}
        tac_vu: dict[str, dict[str, Any]] = {}
        for item in danh_sach:
            if not isinstance(item, dict):
                continue
            ma = _lam_sach(item.get("id"))
            owner = _lam_sach(item.get("owner_id"))
            if not ma or not owner:
                continue
            trang_thai = _lam_sach(item.get("status"))
            if trang_thai not in (TRANG_THAI_XEP_HANG, TRANG_THAI_DANG_CHAY, TRANG_THAI_XONG, TRANG_THAI_LOI):
                trang_thai = TRANG_THAI_LOI
            task = {
                "id": ma,
                "owner_id": owner,
                "status": trang_thai,
                "mode": MODE_SUA if item.get("mode") == MODE_SUA else MODE_TAO,
                "model": _lam_sach(item.get("model"), "gpt-image-2"),
                "size": _lam_sach(item.get("size")),
                "created_at": _lam_sach(item.get("created_at"), _gio_hien_tai_iso()),
                "updated_at": _lam_sach(item.get("updated_at"), _lam_sach(item.get("created_at"), _gio_hien_tai_iso())),
            }
            data = item.get("data")
            if isinstance(data, list):
                task["data"] = data
            loi = _lam_sach(item.get("error"))
            if loi:
                task["error"] = loi
            tac_vu[_khoa_tac_vu(owner, ma)] = task
        return tac_vu

    def _luu_khoa(self) -> None:
        danh_sach = sorted(
            self._tac_vu.values(),
            key=lambda item: str(item.get("updated_at") or ""),
            reverse=True,
        )
        duong_tmp = self.duong.with_suffix(self.duong.suffix + ".tmp")
        duong_tmp.write_text(
            json.dumps({"tasks": danh_sach}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        duong_tmp.replace(self.duong)

    def _phuc_hoi_khoa(self) -> bool:
        doi = False
        for task in self._tac_vu.values():
            if task.get("status") in CHUA_XONG:
                task["status"] = TRANG_THAI_LOI
                task["error"] = "Dịch vụ đã khởi động lại — tác vụ ảnh chưa xong bị gián đoạn"
                task["updated_at"] = _gio_hien_tai_iso()
                doi = True
        return doi

    def _giu_ngay(self) -> int:
        try:
            return max(1, int(self.giu_ngay_getter()))
        except Exception:
            return GIU_NGAY_MAC_DINH

    def _don_dep_khoa(self, giu_ngay: int) -> int:
        try:
            giu_ngay = max(1, int(giu_ngay))
        except (TypeError, ValueError):
            giu_ngay = GIU_NGAY_MAC_DINH
        han = time.time() - giu_ngay * 86400
        cac_khoa_xoa = [
            key
            for key, task in self._tac_vu.items()
            if task.get("status") in TERMINAL and _dau_thoi_gian(task.get("updated_at")) < han
        ]
        for key in cac_khoa_xoa:
            self._tac_vu.pop(key, None)
        return len(cac_khoa_xoa)
