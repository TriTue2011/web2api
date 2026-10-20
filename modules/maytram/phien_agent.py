"""Sổ phiên + giao thức agent máy trạm của module maytram (NV170).

Nguồn: ~/workspace/c2a-nv146/services/device_agents.py @ d879b03 (256 dòng, CHỈ ĐỌC) —
CHÉP logic: phiên theo id yêu cầu, trần lệnh chờ song song, trần hết giờ mỗi lệnh,
dọn phiên cũ khi thiết bị nối lại (fail_all), cho phép ghi/nguồn/ghi-màn-hình
mặc định TẮT, hai lớp kiểm (lõi + agent), allowlist fail-closed.
KHÔNG port: resolve_token (hello token thô gửi thẳng = phát lại được), call_sync
(cầu thread của mô hình bot khác), list_devices, _registry/_app_loop (lệ thuộc
khung c2a), «exec»/«kill» (lệnh tuỳ ý — spec NV170 cấm).

Thay bằng bắt tay HMAC hai chiều (spec: «xác thực hai chiều lõi↔agent»,
«chống phát lại (nonce/thời gian)»):
  agent→lõi: {"loai":"chao","ten","nonce_agent","thoi_gian"}
  lõi→agent: {"loai":"thach_thuc","nonce_loi","xac_thuc_loi"}     ← lõi chứng minh BIẾT khoá
  agent→lõi: {"loai":"dap","xac_thuc_agent"}                     ← agent chứng minh BIẾT khoá
  lõi→agent: {"loai":"san_sang","duong_dan","quyen"}
Mọi khung lệnh/kết quả sau đó mang MAC = HMAC-SHA256(khoá-phiên, nội dung).
Khoá-phiên = HMAC-SHA256(khoa_agent, "phien:"+nonce_agent+":"+nonce_loi) —
khoa_agent không bao giờ đi trên đường truyền, ngay cả trong bắt tay.

Lớp truyền tải: hàm `nhan_agent(kenh)` nhận bất kỳ đối tượng «kenh» nào có
`async gui(obj)` / `async nhan() -> dict` (WebSocket thật của lõi ở tương lai,
hoặc kênh nối cặp của test). Module không tự mở cổng — đúng spec
«không cổng nào mở ra Internet; agent chỉ kết nối ra».
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import math
import re
import secrets
import time
import uuid

log = logging.getLogger("web2api.maytram")

# ── tập lệnh ĐÓNG (ánh xạ tên công cụ web2api → op trên agent) ───────────────
LENH_DOC = (
    "liet_ke",      # ls
    "doc_tep",      # read
    "thong_ke",     # stat
    "tim_tep",      # find
    "thong_tin",    # sysinfo   (lệnh CỐ ĐỊNH do agent tự chọn, không chèn chữ)
    "tai_nguyen",   # resources (idem)
    "tien_trinh",   # processes (idem)
    "dich_vu",      # services  (idem)
)
LENH_GHI = ("ghi_tep", "tao_thu_muc", "xoa", "noi_tep")            # cần co_quyen_ghi
LENH_NGUON = ("khoa_man_hinh", "ngu", "dang_xuat", "tat_may",
              "khoi_dong_lai", "mo_khoa")                          # cần co_quyen_nguon
LENH_GHI_MAN_HINH = ("chup_man_hinh", "chup_webcam")               # cần co_quyen_ghi_man_hinh
LENH_DONG = LENH_DOC + LENH_GHI + LENH_NGUON + LENH_GHI_MAN_HINH
#: Lệnh NỘI BỘ — chỉ đường quản lý «xoa» (go_tren_may) mới được gửi, không phơi
#: thành công cụ người dùng (theo c2a: «uninstall» không nằm trong tập gọi được).
LENH_NOI_BO = ("go_cai_dat",)

#: KHÔNG PORT từ c2a theo spec NV170 («nếu c2a có đường chạy lệnh tuỳ ý thì KHÔNG port»):
#: "exec" = chạy lệnh shell tuỳ ý, "kill" = tắt tiến trình tuỳ ý.
LENH_KHONG_PORT = ("exec", "kill")

#: Các trường đường dẫn phải qua allowlist ở LÕI trước khi gửi xuống agent.
TRUONG_DUONG_DAN = ("duong_dan", "nguon", "dich")

_HET_GIO_MAC_DINH = 60.0
_HET_GIO_BAT_TAY_MAC_DINH = 15.0  # bắt tay không treo nếu agent chết giữa chừng
_TRAN_LENH_CHO = 32            # trần lệnh chờ song song mỗi thiết bị (như c2a)
_TRAN_NONCE_NHO = 10_000       # cache nonce tối đa
_NONCE_TTL = 600.0             # nonce nhớ 10 phút (như cửa webhook của lõi)
_MAU_NONCE = re.compile(r"\A[0-9a-f]{32}\Z")


def _hmac_hex(khoa: bytes, *phan: bytes) -> str:
    m = hmac.new(khoa, digestmod=hashlib.sha256)
    for p in phan:
        m.update(p)
    return m.hexdigest()


def _json_chuan(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=True, sort_keys=True,
                      separators=(",", ":")).encode()


class BatTayLoi(Exception):
    """Bắt tay thất bại — câu lỗi KHÔNG chứa khoá, chỉ nêu lý do chung."""


def duong_dan_duoc_phep(duong_dan_cho_phep: list, duong_dan: str) -> bool:
    """Đường dẫn có nằm trong allowlist của thiết bị không.

    Chép logic c2a path_allowed: allowlist RỖNG = không cho gì cả (fail-closed);
    chặn «..» trước khi so; so theo ranh giới thư mục (không dính tiền tố na ná).
    Chặt hơn c2a ở hai chỗ: tiền tố gốc «/» phủ mọi đường (bản c2a so «//» nên
    KHÔNG khớp — lỗi phát hiện khi chạy đột biến M6 lượt NV170), và đầu vào
    tương đối bị từ chối (chỉ đường tuyệt đối).
    """
    p = str(duong_dan or "")
    tien_to = [str(x) for x in (duong_dan_cho_phep or []) if str(x).strip()]
    if not p or not tien_to:
        return False
    if ".." in p.replace("\\", "/").split("/"):
        return False
    chuan = p.replace("\\", "/").rstrip("/") or "/"
    if not chuan.startswith("/"):
        return False  # đường tương đối không bao giờ hợp lệ
    for tt in tien_to:
        ttc = tt.replace("\\", "/").rstrip("/") or "/"
        if ttc == "/" or chuan == ttc or chuan.startswith(ttc + "/"):
            return True
    return False


class PhienAgent:
    """Một máy trạm đang kết nối — ghép yêu cầu↔kết quả bằng id (chép c2a AgentSession)."""

    def __init__(self, ten: str, cfg: dict, khoa_phien: bytes, kenh,
                 het_gio: float = _HET_GIO_MAC_DINH, dau_van: str = ""):
        self.ten = ten
        self.cfg = cfg
        #: Dấu vân của khoá đã dùng để lập phiên này (NV170-SUA M1) — module
        #: đối chiếu với khoá HIỆN HÀNH ở mỗi lệnh: khoá xoay thì phiên chết.
        self.dau_van = dau_van
        self._khoa_phien = khoa_phien
        self._kenh = kenh
        self._het_gio = het_gio
        self.ket_noi_luc = time.time()
        self.thong_tin: dict = {}
        self.so_lenh = 0
        self._cho: dict[str, asyncio.Future] = {}
        self._da_dong = False

    # ── quyền (chép c2a can_*) ───────────────────────────────────────────────
    @property
    def co_quyen_ghi(self) -> bool:
        return bool(self.cfg.get("co_quyen_ghi", False))

    @property
    def co_quyen_nguon(self) -> bool:
        return bool(self.cfg.get("co_quyen_nguon", False))

    @property
    def co_quyen_ghi_man_hinh(self) -> bool:
        return bool(self.cfg.get("co_quyen_ghi_man_hinh", False))

    @property
    def duong_dan_cho_phep(self) -> list:
        dd = self.cfg.get("duong_dan")
        return [str(x) for x in dd if str(x).strip()] if isinstance(dd, list) else []

    def _mac_lenh(self, rid: str, lenh: str, doi_so: dict) -> str:
        return _hmac_hex(self._khoa_phien, rid.encode(), b":",
                         lenh.encode(), b":", _json_chuan(doi_so))

    def _mac_ket_qua(self, rid: str, tai: dict) -> str:
        return _hmac_hex(self._khoa_phien, rid.encode(), b":", _json_chuan(tai))

    async def gui_lenh(self, lenh: str, doi_so: dict) -> dict:
        """Gửi một lệnh xuống máy trạm và chờ kết quả (chép c2a call)."""
        if self._da_dong:
            return {"ok": False, "loi": f"máy «{self.ten}» đã ngắt kết nối"}
        if len(self._cho) >= _TRAN_LENH_CHO:
            return {"ok": False, "loi": "máy đang quá tải lệnh chờ"}
        rid = uuid.uuid4().hex[:12]
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._cho[rid] = fut
        khung = {"id": rid, "lenh": lenh, "doi_so": doi_so}
        khung["mac"] = self._mac_lenh(rid, lenh, doi_so)
        try:
            await self._kenh.gui(khung)
        except Exception as exc:
            self._cho.pop(rid, None)
            return {"ok": False, "loi": f"mất kết nối tới máy: {str(exc)[:80]}"}
        try:
            kq = await asyncio.wait_for(asyncio.shield(fut), timeout=self._het_gio)
        except asyncio.TimeoutError:
            return {"ok": False,
                    "loi": f"máy không trả lời trong {int(self._het_gio)}s"}
        finally:
            self._cho.pop(rid, None)
        self.so_lenh += 1
        if not isinstance(kq, dict):
            return {"ok": False, "loi": "phản hồi không hợp lệ"}
        if "ok" not in kq:
            # khung lỗi của agent ({loi: ...}) → chuẩn hoá về khuôn kết quả
            return {"ok": False,
                    "loi": str(kq.get("loi") or "phản hồi không hợp lệ")[:500]}
        return kq

    def nhan_khung(self, khung: dict) -> None:
        """Kết quả từ agent → đánh thức future tương ứng (chép c2a deliver).

        Khung lạ/không đúng MAC bị BỎ LẶNG (kẻ ở giữa không nhét được kết quả giả).
        """
        if not isinstance(khung, dict):
            return
        rid = str(khung.get("id") or "")
        fut = self._cho.get(rid)
        if fut is None or fut.done():
            return
        mac = str(khung.get("mac") or "")
        if "ket_qua" in khung:
            tai = khung["ket_qua"]
        elif "loi" in khung:
            tai = {"loi": str(khung.get("loi") or "")[:500]}
        else:
            return
        if not isinstance(tai, dict):
            return
        if not hmac.compare_digest(mac, self._mac_ket_qua(rid, tai)):
            log.warning({"su_kien": "maytram_mac_sai", "may": self.ten,
                         "id": rid[:8] + "…"})
            return
        fut.set_result(tai)

    def that_bai_tat_ca(self, ly_do: str) -> None:
        """Đứt kết nối → mọi lệnh đang chờ trả lỗi ngay, không treo (chép c2a)."""
        for fut in list(self._cho.values()):
            if not fut.done():
                fut.set_result({"ok": False, "loi": ly_do})
        self._cho.clear()

    async def dong(self, ly_do: str = "đóng kết nối") -> None:
        self._da_dong = True
        self.that_bai_tat_ca(ly_do)

    def cong_khai(self) -> dict:
        return {
            "ten": self.ten,
            "nhan": str(self.cfg.get("nhan") or self.ten),
            "dang_ket_noi": True,
            "ket_noi_luc": self.ket_noi_luc,
            "nen_tang": self.thong_tin.get("nen_tang", ""),
            "ten_may_tu_khai": str(self.thong_tin.get("ten_may") or "")[:40],
            "phien_ban_agent": self.thong_tin.get("phien_ban", ""),
            "duong_dan": self.duong_dan_cho_phep,
            "co_quyen_ghi": self.co_quyen_ghi,
            "co_quyen_nguon": self.co_quyen_nguon,
            "co_quyen_ghi_man_hinh": self.co_quyen_ghi_man_hinh,
            "so_lenh": self.so_lenh,
        }


class QuanLyPhien:
    """Sổ phiên các máy đang kết nối + bắt tay hai chiều (chép c2a register/unregister)."""

    def __init__(self, lay_thiet_bi, het_gio_lenh: float = _HET_GIO_MAC_DINH,
                 cua_so_giay: float = 120.0,
                 het_gio_bat_tay: float = _HET_GIO_BAT_TAY_MAC_DINH):
        #: lay_thiet_bi(ten) -> bản ghi đầy đủ (có khoá) hoặc None
        self._lay_thiet_bi = lay_thiet_bi
        self._het_gio_lenh = het_gio_lenh
        self._cua_so = cua_so_giay
        self._het_gio_bat_tay = het_gio_bat_tay
        self._phien: dict[str, PhienAgent] = {}
        self._nonce_da_dung: dict[str, float] = {}
        self._khoa = asyncio.Lock()

    async def _nhan_bat_tay(self, kenh):
        """Nhận một khung trong bắt tay, có trần chờ — agent chết thì dừng, không treo."""
        return await asyncio.wait_for(kenh.nhan(), timeout=self._het_gio_bat_tay)

    # ── chống phát lại ───────────────────────────────────────────────────────
    def _don_nonce_cu(self) -> None:
        gio = time.time()
        het = [n for n, luc in self._nonce_da_dung.items() if gio - luc > _NONCE_TTL]
        for n in het:
            self._nonce_da_dung.pop(n, None)
        while len(self._nonce_da_dung) > _TRAN_NONCE_NHO:
            self._nonce_da_dung.pop(next(iter(self._nonce_da_dung)))

    def _kiem_nonce(self, nonce: str) -> None:
        if not _MAU_NONCE.fullmatch(str(nonce or "")):
            raise BatTayLoi("nonce sai khuôn")
        self._don_nonce_cu()
        if nonce in self._nonce_da_dung:
            raise BatTayLoi("nonce đã dùng — nghi phát lại")
        self._nonce_da_dung[nonce] = time.time()

    # ── bắt tay ──────────────────────────────────────────────────────────────
    async def nhan_agent(self, kenh) -> PhienAgent:
        """Nhận MỘT kết nối agent mới: bắt tay 3 bước, đăng ký phiên.

        Ném BatTayLoi khi: khung chào sai, máy không tồn tại/bị tắt/thu hồi,
        thời gian lệch quá cửa sổ, nonce dùng lại, agent không chứng minh được
        biết khoá.
        """
        try:
            chao = await self._nhan_bat_tay(kenh)
        except asyncio.TimeoutError as exc:
            raise BatTayLoi("agent không chào trong thời gian chờ") from exc
        except Exception as exc:
            raise BatTayLoi("không đọc được khung chào") from exc
        if not isinstance(chao, dict) or chao.get("loai") != "chao":
            raise BatTayLoi("khung đầu phải là «chao»")
        ten = str(chao.get("ten") or "").strip()
        tb = self._lay_thiet_bi(ten)
        if (not isinstance(tb, dict) or not tb.get("bat", True)
                or tb.get("da_thu_hoi")):
            # Không nói rõ máy tồn tại hay không — như c2a, không giúp dò.
            raise BatTayLoi("máy không hợp lệ hoặc đã bị thu hồi")
        nonce_agent = str(chao.get("nonce_agent") or "")
        thoi_gian = chao.get("thoi_gian")
        # M4 (NV170-SUA): giờ phải là SỐ HỮU HẠN và không phải bool — NaN lọt
        # qua phép so cửa sổ vì mọi phép so với NaN đều False, vô cực cũng
        # không phải mốc giờ thật. Kiểm giờ TRƯỚC nonce: khung chào sai giờ
        # của kẻ không có khoá không được đốt nonce của agent thật.
        if (isinstance(thoi_gian, bool)
                or not isinstance(thoi_gian, (int, float))
                or not math.isfinite(float(thoi_gian))
                or abs(time.time() - float(thoi_gian)) > self._cua_so):
            raise BatTayLoi("thời gian lệch quá cửa sổ cho phép")
        self._kiem_nonce(nonce_agent)  # phát lại → dừng ngay tại đây
        try:
            khoa = bytes.fromhex(str(tb.get("khoa_agent") or ""))
        except ValueError:
            raise BatTayLoi("khoá máy hỏng trong sổ") from None
        if len(khoa) < 16:
            raise BatTayLoi("khoá máy hỏng trong sổ")

        # Bước 2 — LÕI chứng minh biết khoá TRƯỚC (xác thực hai chiều).
        nonce_loi = secrets.token_hex(16)
        xac_thuc_loi = _hmac_hex(khoa, b"loi:", nonce_agent.encode(), b":",
                                 nonce_loi.encode())
        try:
            await kenh.gui({"loai": "thach_thuc", "nonce_loi": nonce_loi,
                            "xac_thuc_loi": xac_thuc_loi})
            dap = await self._nhan_bat_tay(kenh)
        except asyncio.TimeoutError as exc:
            raise BatTayLoi("agent không đáp thách thức trong thời gian chờ") from exc
        except Exception as exc:
            raise BatTayLoi("đứt khi trao đổi thách thức") from exc
        mong = _hmac_hex(khoa, b"agent:", nonce_agent.encode(), b":",
                         nonce_loi.encode())
        if (not isinstance(dap, dict)
                or not hmac.compare_digest(str(dap.get("xac_thuc_agent") or ""),
                                           mong)):
            raise BatTayLoi("agent không chứng minh được biết khoá")

        khoa_phien = bytes.fromhex(_hmac_hex(
            khoa, b"phien:", nonce_agent.encode(), b":", nonce_loi.encode()))
        thong_tin = dap.get("thong_tin")
        async with self._khoa:
            # M1 (NV170-SUA): NẠP LẠI bản ghi TRONG khoá ngay trước khi lập
            # phiên. Bắt tay có thể bị giữ dở tới 60s (mỗi bước chờ 15s) —
            # trong lúc đó chủ máy có thể đã XOAY KHOÁ/THU HỒI/XOÁ máy để xử
            # lý sự cố; bản ghi `tb` nạp ở khung chào đã CŨ. Khoá hiện hành
            # khác khoá đã dùng bắt tay, hoặc máy đã tắt/thu hồi/mất khỏi sổ
            # → bắt tay CHẾT, không lập phiên cho kẻ giữ khoá cũ.
            tb2 = self._lay_thiet_bi(ten)
            if (not isinstance(tb2, dict) or not tb2.get("bat", True)
                    or tb2.get("da_thu_hoi")):
                raise BatTayLoi("máy không hợp lệ hoặc đã bị thu hồi")
            try:
                khoa_hien_hanh = bytes.fromhex(str(tb2.get("khoa_agent") or ""))
            except ValueError:
                raise BatTayLoi("khoá máy hỏng trong sổ") from None
            if not hmac.compare_digest(khoa_hien_hanh, khoa):
                raise BatTayLoi("khoá đã đổi trong lúc bắt tay")
            phien = PhienAgent(ten, tb2, khoa_phien, kenh,
                               het_gio=self._het_gio_lenh,
                               dau_van=str(tb2.get("dau_van") or ""))
            if isinstance(thong_tin, dict):
                phien.thong_tin = {k: str(v)[:80] for k, v in thong_tin.items()
                                   if k in ("nen_tang", "ten_may", "phien_ban")}
            cu = self._phien.get(ten)
            self._phien[ten] = phien
        if cu is not None:
            # Cùng máy nối lại — phiên cũ thành zombie, dọn ngay (chép c2a).
            await cu.dong("máy đã nối lại bằng phiên mới")
        await kenh.gui({"loai": "san_sang",
                        "duong_dan": phien.duong_dan_cho_phep,
                        "quyen": {"ghi": phien.co_quyen_ghi,
                                  "nguon": phien.co_quyen_nguon,
                                  "ghi_man_hinh": phien.co_quyen_ghi_man_hinh}})
        log.info({"su_kien": "maytram_ket_noi", "may": ten,
                  "dau_van": phien.dau_van})
        return phien

    async def chay_kenh(self, kenh) -> PhienAgent:
        """Phục vụ MỘT kết nối agent: bắt tay rồi chuyển mọi khung kết quả vào
        phiên cho tới khi kênh đóng. Đây là điểm lõi/gateway gọi cho từng kết
        nối tới (mỗi kết nối một tác vụ asyncio riêng)."""
        phien = await self.nhan_agent(kenh)
        try:
            while True:
                khung = await kenh.nhan()
                phien.nhan_khung(khung)
        except Exception:
            pass  # kênh đóng/lỗi — dọn phiên ở finally, không ném lên gateway
        finally:
            async with self._khoa:
                if self._phien.get(phien.ten) is phien:
                    self._phien.pop(phien.ten, None)
            await phien.dong("kênh đóng")
        return phien

    def lay(self, ten: str) -> "PhienAgent | None":
        return self._phien.get(str(ten or "").strip())

    async def ngat(self, ten: str, ly_do: str = "ngắt bởi quản lý") -> None:
        async with self._khoa:
            phien = self._phien.pop(str(ten or "").strip(), None)
        if phien is not None:
            await phien.dong(ly_do)
            log.info({"su_kien": "maytram_ngat", "may": ten})

    def danh_sach_phien(self) -> list[dict]:
        return [p.cong_khai() for p in self._phien.values()]
