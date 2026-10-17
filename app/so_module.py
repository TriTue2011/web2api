"""Sổ module đang sống trong lõi: khám phá hai nguồn, nạp lại nóng, cài/gỡ, định tuyến theo tiền tố.

Ba loại "đã biết":
  catalog    — module ĐÃ CÀI: có mã trên đĩa (ảnh + volume), có tab trên web, gỡ được — chưa chắc đã
               liên kết (có khoá/cấu hình);
  tu_xa      — module TỪ XA: chỉ URL nói chuẩn OpenAI, không nạp mã (module nặng/GPU/máy khác);
  dang_chay  — ĐÃ LIÊN KẾT: thể hiện đang phục vụ /v1 (module có mã đã có cấu hình hợp lệ + mọi module từ xa).

Nguyên tắc cô lập: một module hỏng (manifest sai, lỗi cú pháp, thiếu gói, lỗi __init__/nap)
chỉ làm hỏng CHÍNH nó — ghi vào danh sách lỗi kèm thư mục và lý do; lõi và module khác
chạy tiếp. Module ĐANG chạy mà bản mới trên đĩa nạp hỏng thì GIỮ bản cũ trong bộ nhớ.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import secrets
import shutil
import sys
from pathlib import Path

from app import he_thong, kho, nhat_ky, phien
from app.kham_pha import (ID_HOP_LE, LoiNap, ModuleKhamPha, doc_manifest, go_ma, nap_ma, quet, van_tay)
from app.quyen_module import bam_diff, cac_muc_chuan, diff_quyen

log = logging.getLogger("web2api.so_module")

#: Nguồn module đi kèm ảnh. Thuộc tính mô-đun (đọc lúc gọi) để test trỏ sang thư mục tạm.
THU_MUC_ANH = Path(__file__).resolve().parent / "modules"


class LoiSo(Exception):
    """Lỗi nghiệp vụ trả về cho quản trị: `ma` là mã HTTP gợi ý."""

    def __init__(self, ma: int, thong_diep: str, code: str | None = None):
        super().__init__(thong_diep)
        self.ma = ma
        self.code = code


def _van_tay_tu_xa(ban_ghi: dict, api_key: str | None) -> tuple:
    """Dấu để biết thể hiện từ xa đang chạy còn khớp đĩa không — băm khoá, không giữ thêm bản khoá thô."""
    return (json.dumps(ban_ghi, sort_keys=True, ensure_ascii=False),
            hashlib.sha256(api_key.encode("utf-8")).hexdigest() if api_key else None)


async def kiem_ban_ghi_tu_xa(than: dict) -> dict:
    """Kiểm THUẦN (không ghi) một bản ghi module từ xa — MỘT bộ kiểm cho đăng ký (POST /api/modules/tu-xa) và cho
    khôi phục tu_xa.json: id/tên/tiền tố đúng quy tắc, base_url qua `kiem_url_lan` (chặn SSRF). Trả
    {id, ten, tien_to, base_url} đã chuẩn hoá; sai → LoiSo(400)."""
    from app.kham_pha import ID_DANH_RIENG, TIEN_TO_HOP_LE
    from app.tu_xa import kiem_url_lan

    if not isinstance(than, dict):
        raise LoiSo(400, "bản ghi module từ xa phải là đối tượng")
    id_, ten, tien_to = than.get("id"), than.get("ten"), than.get("tien_to")
    if not isinstance(id_, str) or not ID_HOP_LE.match(id_) or id_ in ID_DANH_RIENG:
        raise LoiSo(400, "id không hợp lệ (cần ^[a-z0-9][a-z0-9_-]{1,40}$, không trùng tên tuyến)")
    if not isinstance(ten, str) or not ten.strip():
        raise LoiSo(400, "thiếu «ten»")
    if not isinstance(tien_to, str) or not TIEN_TO_HOP_LE.match(tien_to):
        raise LoiSo(400, "tien_to không hợp lệ (chữ, số, . _ -; không có «/»)")
    try:
        base = await kiem_url_lan(than.get("base_url"))
    except ValueError as e:
        raise LoiSo(400, str(e)) from None
    return {"id": id_, "ten": ten.strip(), "tien_to": tien_to, "base_url": base}


class LoiCanDuyetQuyen(LoiSo):
    """409 khi bản mới của module đã cài xin thêm quyền, hoặc vòng tự cài tải về bản đổi mã (NV49 R-1):
    chưa kích hoạt, chờ quản trị duyệt.

    Thuộc tính: id_, diff {"them": [...], "bot": [...]}, sha_commit, tree_sha của bản mới.
    """

    def __init__(self, id_: str, diff: dict, sha_commit: str | None, tree_sha: str | None):
        viec = (f"xin thêm {len(diff['them'])} quyền" if diff.get("them")
                else "đổi mã (không xin thêm quyền), do vòng tự cài tải về")
        super().__init__(409, f"Module {id_!r} có bản mới {viec} — cần quản trị duyệt trước khi kích hoạt")
        self.id_ = id_
        self.diff = diff
        self.sha_commit = sha_commit
        self.tree_sha = tree_sha


class LoiModuleDangDuocCan(LoiSo):
    """409 khi gỡ module đang được module khác cần (`can_module`): chưa gỡ gì cả.

    Thuộc tính: id_, dung — danh sách id các module đã cài đang khai cần module này.
    """

    def __init__(self, id_: str, dung: list[str]):
        super().__init__(409, f"Module {id_!r} đang được {len(dung)} module cần tới: "
                              f"{', '.join(dung)} — gỡ các module đó trước",
                         code="module_dang_duoc_can")
        self.id_ = id_
        self.dung = list(dung)


class SoModule:
    def __init__(self):
        self.catalog: dict[str, ModuleKhamPha] = {}
        self.tu_xa: dict[str, dict] = {}
        self._vt_tu_xa: dict[str, tuple] = {}  # (bản ghi, băm khoá) mà thể hiện từ xa đang chạy được dựng từ đó
        self.dang_chay: dict[str, object] = {}
        self.loi: list[dict] = []  # lỗi của lần quét/nạp gần nhất: [{thu_muc, ly_do}]
        # Tạo trong vòng sự kiện của lifespan (asyncio.Lock gắn vòng lặp ở lần chờ đầu).
        self.khoa = asyncio.Lock()
        # Múi giờ khai bằng TZ: đọc MỘT lần mỗi lần khởi động (sai thì cảnh báo một lần, không theo từng yêu cầu).
        self.mui_gio = he_thong.mui_gio()

    # ───────────── tra cứu ─────────────

    def tien_to_cua(self, id_: str) -> str | None:
        if id_ in self.catalog:
            return self.catalog[id_].tien_to
        if id_ in self.tu_xa:
            return self.tu_xa[id_]["tien_to"]
        return None

    def tim_theo_tien_to(self, tien_to: str) -> str | None:
        for id_, km in self.catalog.items():
            if km.tien_to == tien_to:
                return id_
        for id_, ban_ghi in self.tu_xa.items():
            if ban_ghi["tien_to"] == tien_to:
                return id_
        return None

    def tien_to_dang_chay(self) -> list[str]:
        return sorted(f"{self.tien_to_cua(i)}/" for i in self.dang_chay)

    # ───────────── phụ thuộc module (`can_module`, NV73) ─────────────

    def dang_duoc_can_boi(self, id_: str) -> list[str]:
        """Id các module ĐÃ CÀI khai `can_module` chứa `id_` — đếm tham chiếu SUY TỪ manifest,
        không cần kho riêng nên tự đúng sau mọi lần nạp lại (không lệch bộ đếm)."""
        return sorted(km.id for km in self.catalog.values()
                      if id_ in (km.manifest.get("can_module") or []))

    def can_module_thieu(self, id_: str) -> list[str]:
        """Các id trong `can_module` của module `id_` mà chưa cài — để đề nghị cài, không tự cài lén."""
        km = self.catalog.get(id_)
        if km is None:
            return []
        cm = km.manifest.get("can_module") or []
        return [d for d in cm if isinstance(d, str) and d not in self.catalog]

    def tinh_nang(self) -> dict:
        """«Tính năng đang có» cho khung giao diện (GET /api/tinh-nang) — suy từ manifest module ĐÃ CÀI và trạng thái
        liên kết, không từ danh sách tên: mỗi module góp mã `loai` của nó và các mã `cung_cap` vào tập khả năng
        (đếm số module, tách đã cài / đã liên kết), kèm `mui_gio` (tên IANA khai bằng TZ, hoặc null) để Studio báo giờ
        theo múi giờ của nhà. Không bí mật, không cấu hình module. Đánh giá điều kiện (`can`) là
        việc của giao diện — một hàm duy nhất cho cả điều kiện của khung lẫn `muc` của module."""
        modules, loai, da_cai, lien_ket, tab, muc = [], {}, {}, {}, [], []

        def ghi(id_: str, man: dict, tu_xa: bool) -> None:
            lk = id_ in self.dang_chay
            cc = list(man.get("cung_cap", []))
            # `dang_nhap_tu_dong`: suy từ CẤU TRÚC (module đang chạy khai hàm `dang_nhap`), không từ tên nhà cung cấp —
            # giao diện chỉ hiện chế độ «Tự đăng nhập» cho module thật sự làm được.
            dn = lk and callable(getattr(self.dang_chay.get(id_), "dang_nhap", None))
            modules.append({"id": id_, "ten": man["ten"], "loai": man["loai"], "cung_cap": cc,
                            "tien_to": man["tien_to"], "tu_xa": tu_xa, "da_lien_ket": lk,
                            "dang_nhap_tu_dong": bool(dn)})
            l = loai.setdefault(man["loai"], {"ma": man["loai"], "ten": None, "thu_tu": None,
                                              "so_da_cai": 0, "so_da_lien_ket": 0})
            l["so_da_cai"] += 1
            l["so_da_lien_ket"] += lk
            # Hai module khai khác nhau cho cùng một loại: module đứng trước theo id thắng (xác định, không ngẫu nhiên).
            if l["ten"] is None and man.get("loai_ten"):
                l["ten"] = man["loai_ten"]
            if l["thu_tu"] is None and man.get("loai_thu_tu") is not None:
                l["thu_tu"] = man["loai_thu_tu"]
            for ma in dict.fromkeys([man["loai"], *cc]):
                da_cai[ma] = da_cai.get(ma, 0) + 1
                if lk:
                    lien_ket[ma] = lien_ket.get(ma, 0) + 1

        for id_, km in sorted(self.catalog.items()):
            man = km.manifest
            ghi(id_, man, False)
            t = man.get("tab_web")
            if t and id_ in self.dang_chay:
                # Đường TƯƠNG ĐỐI: web2api sau proxy ở thư mục con vẫn mở đúng.
                tab.append({"module": id_, "nhan": t["nhan"], "duong": f"m/{id_}/{t['trang'].removeprefix('web/')}"})
            muc += [{"module": id_, "id": x["id"], "nhan": x["nhan"], "can": list(x["can"])} for x in man.get("muc", [])]
        for id_ in sorted(self.tu_xa):
            m = self.dang_chay.get(id_)
            if m is not None:  # module từ xa: lõi tự dựng manifest (ModuleTuXa) — dịch vụ nói chuẩn OpenAI = chat
                ghi(id_, m.manifest, True)
        thu_tu = sorted(loai.values(), key=lambda x: (x["thu_tu"] is None, x["thu_tu"] or 0, x["ma"]))
        return {"modules": modules, "loai": thu_tu, "kha_nang_da_cai": da_cai, "kha_nang_lien_ket": lien_ket,
                "tab": tab, "muc": muc, "mui_gio": self.mui_gio, "phien": phien.thong_ke()}

    # ───────────── khởi động / tắt ─────────────

    async def khoi_dong(self) -> dict:
        from app.tu_xa import ModuleTuXa  # nhập muộn: tu_xa nhập so_module để lấy LoiSo

        async with self.khoa:
            loi = await self._dong_bo_tu_xa()
            kq = await self._nap_lai()
            self.loi = loi + self.loi
            return kq

    async def _dong_bo_tu_xa(self) -> list[dict]:
        """Cho sổ module từ xa khớp tu_xa.json + bi_mat/<id>.json trên đĩa: bản ghi mới/đổi (hoặc khoá đổi) → dựng
        thể hiện mới rồi dừng bản cũ; bản ghi không còn → dừng. MỘT đường cho khởi động và cho nạp lại sau khôi phục.
        Gọi khi ĐANG giữ self.khoa. Trả danh sách lỗi {thu_muc, ly_do}."""
        from app.tu_xa import ModuleTuXa  # nhập muộn: tu_xa nhập so_module để lấy LoiSo

        loi: list[dict] = []
        try:
            ds = kho.doc_tu_xa()
        except kho.KhoHong as e:
            log.warning("%s", e)
            return [{"thu_muc": str(kho.thu_muc_du_lieu() / "tu_xa.json"), "ly_do": str(e)}]
        moi: dict[str, tuple[dict, str | None]] = {}
        for ban_ghi in ds:
            try:
                moi[ban_ghi["id"]] = (ban_ghi, kho.doc_bi_mat(ban_ghi["id"]).get("api_key"))
            except (KeyError, TypeError, ValueError, kho.KhoHong) as e:
                log.warning("Bỏ qua module từ xa hỏng: %s", e)
        for id_ in [i for i in self.tu_xa if i not in moi]:
            del self.tu_xa[id_]
            self._vt_tu_xa.pop(id_, None)
            m = self.dang_chay.pop(id_, None)
            if m is not None:
                await self._dung_an_toan(id_, m)
        for id_, (ban_ghi, api_key) in moi.items():
            vt = _van_tay_tu_xa(ban_ghi, api_key)
            cu = self.dang_chay.get(id_) if id_ in self.tu_xa else None
            if cu is not None and self._vt_tu_xa.get(id_) == vt:
                continue
            try:
                the = ModuleTuXa(ban_ghi, api_key)
            except (KeyError, TypeError, ValueError) as e:
                log.warning("Bỏ qua module từ xa hỏng %s: %s", id_, e)
                loi.append({"thu_muc": str(kho.thu_muc_du_lieu() / "tu_xa.json"), "ly_do": f"{id_}: {e}"})
                continue
            self._vt_tu_xa[id_] = vt
            self.tu_xa[id_] = ban_ghi
            self.dang_chay[id_] = the
            if cu is not None:
                await self._dung_an_toan(id_, cu)
        return loi

    async def dung_het(self) -> None:
        for id_, m in list(self.dang_chay.items()):
            await self._dung_an_toan(id_, m)
        self.dang_chay.clear()

    @staticmethod
    async def _dung_an_toan(id_: str, m) -> None:
        try:
            await m.dung()
        except Exception:
            log.exception("Module %s lỗi khi dừng (bỏ qua, lõi chạy tiếp)", id_)

    # ───────────── nạp lại nóng ─────────────

    async def nap_lai(self) -> dict:
        async with self.khoa:
            return await self._nap_lai()

    async def nap_lai_tu_kho(self, doi_cau_hinh: set[str]) -> dict:
        """Nạp lại SAU KHI tệp cấu hình bị thay từ ngoài (khôi phục): module từ xa theo tu_xa.json; module có mã ĐANG
        chạy mà cấu hình/bí mật đổi (`doi_cau_hinh`) hoặc không còn trong modules.json thì dựng lại từ kho (hỏng →
        dừng và báo lỗi, không để mã chạy với cấu hình cũ trong khi đĩa đã khác); rồi quét như `nap_lai`."""
        async with self.khoa:
            loi = await self._dong_bo_tu_xa()
            con = {m.get("id") for m in kho.doc_modules()[0] if isinstance(m, dict)}
            for id_ in [i for i in self.dang_chay if i in self.catalog and (i in doi_cau_hinh or i not in con)]:
                cu = self.dang_chay.pop(id_)
                km = self.catalog[id_]
                try:
                    the = self._tao_tu_kho(km)
                except Exception as e:  # mã module: lỗi của nó, không phải của lõi
                    the = None
                    loi.append({"thu_muc": str(km.thu_muc),
                                "ly_do": f"không nạp được cấu hình đã khôi phục: {type(e).__name__}: {e}"})
                if the is not None:
                    self.dang_chay[id_] = the
                await self._dung_an_toan(id_, cu)
            kq = await self._nap_lai()
            kq["loi"] = loi + kq["loi"]
            self.loi = kq["loi"]
            return kq

    async def _nap_lai(self) -> dict:
        """Quét lại hai nguồn. Trả {them, doi, go, loi}. Gọi khi ĐANG giữ self.khoa."""
        cu = self.catalog
        moi: dict[str, ModuleKhamPha] = {}
        loi: list[dict] = []
        them, doi = [], []

        def bao_loi(thu_muc: Path, ly_do: str):
            log.warning("Module ở %s không dùng được: %s", thu_muc, ly_do)
            loi.append({"thu_muc": str(thu_muc), "ly_do": ly_do})
            nhat_ky.ghi("he_thong", "loi", f"Lỗi nạp module ở {thu_muc.name}: {ly_do}",
                        thu_muc=str(thu_muc), ly_do=str(ly_do))

        for nguon, goc in (("anh", THU_MUC_ANH), ("du_lieu", kho.thu_muc_modules())):
            for thu_muc in quet(goc):
                manifest, ly_do = doc_manifest(thu_muc)
                if ly_do:
                    truoc = cu.get(thu_muc.name) or next((m for m in cu.values() if m.thu_muc == thu_muc), None)
                    if truoc and truoc.thu_muc == thu_muc and van_tay(thu_muc) == truoc.van_tay:
                        moi[truoc.id] = truoc
                        continue
                    bao_loi(thu_muc, ly_do)
                    continue
                id_ = manifest["id"]
                if id_ in moi:
                    bao_loi(thu_muc, f"trùng id {id_!r} với {moi[id_].thu_muc} — từ chối module sau"
                            + (" (module đi kèm ảnh được ưu tiên, không bị đè)" if moi[id_].nguon == "anh" else ""))
                    continue
                trung = next((k for k in moi.values() if k.tien_to == manifest["tien_to"]), None)
                if trung:
                    bao_loi(thu_muc, f"trùng tiền tố {manifest['tien_to']!r} với {trung.thu_muc} — từ chối module sau")
                    continue
                if id_ in self.tu_xa or any(b["tien_to"] == manifest["tien_to"] for b in self.tu_xa.values()):
                    bao_loi(thu_muc, f"trùng id/tiền tố với module từ xa đã đăng ký — từ chối")
                    continue
                vt = van_tay(thu_muc)
                truoc = cu.get(id_)
                if truoc and truoc.van_tay == vt and truoc.thu_muc == thu_muc:
                    moi[id_] = truoc  # không đổi → không chạy lại mã
                    if truoc.chuan_loi:
                        for kn, thieu in truoc.chuan_loi.items():
                            loi.append({"thu_muc": str(thu_muc),
                                        "ly_do": f"không đúng chuẩn: khả năng «{kn}» thiếu phương thức: {', '.join(thieu)}"})
                    continue
                try:
                    lop, ten = nap_ma(thu_muc, manifest, vt)
                except LoiNap as e:
                    bao_loi(thu_muc, str(e))
                    if truoc and truoc.thu_muc == thu_muc:
                        moi[id_] = truoc  # bản mới hỏng → giữ bản cũ đang chạy
                    continue
                km = ModuleKhamPha(id=id_, nguon=nguon, thu_muc=thu_muc, manifest=manifest, lop=lop,
                                   van_tay=vt, ten_ma=ten)
                if km.chuan_loi:
                    for kn, thieu in km.chuan_loi.items():
                        thong_diep = f"không đúng chuẩn: khả năng «{kn}» thiếu phương thức: {', '.join(thieu)}"
                        log.warning("Module %s %s", id_, thong_diep)
                        loi.append({"thu_muc": str(thu_muc), "ly_do": thong_diep})
                        nhat_ky.ghi("he_thong", "canh_bao", f"Module {id_} {thong_diep}",
                                    module=id_, kha_nang=kn, thieu=thieu)
                if truoc and id_ in self.dang_chay:
                    # Đổi mã của module đang phục vụ: dựng thể hiện MỚI với cấu hình đã lưu trước,
                    # hỏng thì giữ nguyên bản cũ; xong mới dừng bản cũ.
                    try:
                        the_moi = self._tao_tu_kho(km)
                    except Exception as e:
                        bao_loi(thu_muc, f"bản mới không nạp được cấu hình đã lưu: {type(e).__name__}: {e}")
                        go_ma(ten)
                        moi[id_] = truoc
                        continue
                    cu_chay = self.dang_chay[id_]
                    if the_moi is not None:
                        self.dang_chay[id_] = the_moi
                    else:
                        # Bản mới cần trường bắt buộc mà cấu hình đã lưu không có: ĐÃ CÀI, CHƯA LIÊN KẾT —
                        # không để mã cũ phục vụ tiếp dưới phiên bản mới.
                        del self.dang_chay[id_]
                    await self._dung_an_toan(id_, cu_chay)
                moi[id_] = km
                (doi if truoc else them).append(id_)
                if truoc and truoc.ten_ma != km.ten_ma:
                    go_ma(truoc.ten_ma)  # dọn bản cũ: không giữ mã cũ trong bộ nhớ

        go = [i for i in cu if i not in moi]
        for id_ in go:
            go_ma(cu[id_].ten_ma)
            m = self.dang_chay.pop(id_, None)
            if m is not None:
                await self._dung_an_toan(id_, m)
        self.catalog = moi
        loi += await self._kich_hoat_da_cai()
        self.loi = loi
        if them or doi or go:
            log.warning("Nạp lại module: thêm %s, đổi %s, gỡ %s", them, doi, go)
            if them:
                nhat_ky.ghi("he_thong", "thong_tin", f"Nạp module: thêm {', '.join(sorted(them))}", modules=sorted(them))
            if doi:
                nhat_ky.ghi("he_thong", "thong_tin", f"Nạp module: đổi {', '.join(sorted(doi))}", modules=sorted(doi))
            if go:
                nhat_ky.ghi("he_thong", "thong_tin", f"Nạp module: gỡ {', '.join(sorted(go))}", modules=sorted(go))
        return {"them": them, "doi": doi, "go": go, "loi": loi}

    @staticmethod
    def muc_da_luu(id_: str) -> dict | None:
        """Mục của module trong modules.json (cấu hình công khai, bí mật đã che) — None nếu chưa có."""
        return next((m for m in kho.doc_modules()[0] if isinstance(m, dict) and m.get("id") == id_), None)

    def _tao_tu_kho(self, km: ModuleKhamPha):
        """Thể hiện đã `nap` cấu hình đã lưu; None nếu module chưa có cấu hình, hoặc cấu hình đã lưu thiếu
        trường `bat_buoc` (vd tệp bí mật bị mất). Chặn ở LÕI theo manifest, không trông vào mã module tự
        vỡ: module `nap({})` có thể không vỡ mà dựng thể hiện với 0 khoá rồi hỏng lúc gọi."""
        muc = self.muc_da_luu(km.id)
        if muc is None:
            return None
        cau_hinh = kho.doc_cau_hinh(km.id, km.truong_cau_hinh, muc)
        thieu = [t["khoa"] for t in km.truong_cau_hinh if t.get("bat_buoc") and not cau_hinh.get(t["khoa"])]
        if thieu:
            log.warning("Module %s: cấu hình đã lưu thiếu trường bắt buộc %s — coi như chưa liên kết", km.id, thieu)
            return None
        the = km.tao()
        the.kho = kho.kho_rieng(km.id)
        the.nap(cau_hinh)
        return the

    async def _kich_hoat_da_cai(self) -> list[dict]:
        """Module có cấu hình trong modules.json mà chưa chạy (khởi động, hoặc mã vừa xuất hiện) → nạp."""
        loi = []
        ds, canh_bao = kho.doc_modules()
        if canh_bao:
            log.warning("%s", canh_bao)
        for muc in ds:
            id_ = muc.get("id") if isinstance(muc, dict) else None
            if id_ in self.dang_chay or id_ not in self.catalog:
                continue
            km = self.catalog[id_]
            try:
                the = self._tao_tu_kho(km)
            except Exception as e:  # mã module: lỗi của nó, không phải của lõi
                log.warning("Module %s không nạp được cấu hình đã lưu: %s", id_, e)
                loi.append({"thu_muc": str(km.thu_muc), "ly_do": f"không nạp được cấu hình đã lưu: {type(e).__name__}: {e}"})
                nhat_ky.ghi("he_thong", "loi", f"Module {id_} không nạp được cấu hình đã lưu: {e}",
                            module=id_, ly_do=str(e))
                continue
            if the is not None:
                self.dang_chay[id_] = the
        return loi

    # ───────────── cài cấu hình / gỡ ─────────────

    async def cai(self, id_: str, cau_hinh: dict) -> dict:
        """Kiểm rồi lưu cấu hình. Trường vắng mặt giữ giá trị đã lưu; `danh_sach_bi_mat` nhận dạng sửa
        {"giu", "them"} (kho.ghep_cau_hinh) — đọc-ghép-lưu trong khoá sổ để hai lời gọi không đè nhau."""
        km = self.catalog.get(id_)
        if km is None:
            raise LoiSo(404, f"Không có module {id_!r} (xem GET /api/modules/catalog)")
        async with self.khoa:
            muc = self.muc_da_luu(id_)
            try:
                da_luu = kho.doc_cau_hinh(id_, km.truong_cau_hinh, muc) if muc else {}
                day_du = kho.ghep_cau_hinh(km.truong_cau_hinh, da_luu, cau_hinh)
            except kho.KhoHong as e:
                raise LoiSo(500, f"Không đọc được cấu hình đã lưu: {e}") from None
            except ValueError as e:
                raise LoiSo(400, str(e)) from None
            return await self._cai(km, day_du)

    async def _cai(self, km: ModuleKhamPha, cau_hinh: dict) -> dict:
        moi = km.tao()
        moi.kho = kho.kho_rieng(km.id)
        try:
            kq = await moi.cai(cau_hinh)
        finally:
            await self._dung_an_toan(km.id, moi)  # thể hiện dùng để kiểm; bản phục vụ dựng lại bằng nap
        cong_khai = {"ok": bool(kq.get("ok")), "ket_qua_tung_khoa": kq.get("ket_qua_tung_khoa") or [],
                     "loi": kq.get("loi")}
        if not cong_khai["ok"]:
            return cong_khai
        sach = kq.get("cau_hinh_luu") or {}
        try:
            kho.luu_cai_dat(km.id, km.truong_cau_hinh, sach)
        except kho.KhoHong as e:
            raise LoiSo(500, f"Không lưu được cấu hình: {e}") from e
        the = km.tao()
        the.kho = kho.kho_rieng(km.id)
        the.nap(sach)
        cu = self.dang_chay.get(km.id)
        self.dang_chay[km.id] = the
        if cu is not None:
            await self._dung_an_toan(km.id, cu)
        return cong_khai

    async def _don_dep_sau_go(self, id_: str) -> None:
        """Dọn sau khi gỡ module (NV91): huỷ tác vụ tự khôi phục/làm mới còn chạy
        (không để tác vụ nền mồ côi) và gỡ «gắn» danh tính của module (không để
        chặn xoá danh tính sau này). Không ném: dọn dẹp không được làm hỏng bước gỡ."""
        try:
            from app import tu_lam_moi
            await tu_lam_moi.huy_cua_module(id_)
            tu_lam_moi.quan_ly().go_vong(id_)
        except Exception as e:
            log.warning("go: dọn tác vụ tự làm mới của %s lỗi: %s", id_, type(e).__name__)
        try:
            from app import danh_tinh
            danh_tinh.go_theo_module(id_)
        except Exception as e:
            log.warning("go: gỡ gắn danh tính của %s lỗi: %s", id_, type(e).__name__)

    async def go(self, id_: str, nguoi_thuc_hien: str | None = None) -> dict:
        async with self.khoa:
            nguoi = nguoi_thuc_hien
            if nguoi is None:
                from app.xac_thuc import dinh_danh_nguoi_thuc_hien
                nguoi = dinh_danh_nguoi_thuc_hien()
            # NV73: module đang được module khác cần (can_module) thì không cho gỡ — kể cả
            # module từ xa (id của nó cũng có thể được khai trong can_module).
            dung = self.dang_duoc_can_boi(id_)
            if dung:
                raise LoiModuleDangDuocCan(id_, dung)
            if id_ in self.tu_xa:
                m = self.dang_chay.pop(id_, None)
                try:
                    kho.xoa_tu_xa(id_)
                    kho.xoa_cai_dat_model_module(id_)
                except kho.KhoHong as e:
                    raise LoiSo(500, f"Không xoá được module từ xa: {e} — khôi phục hoặc xoá tệp tu_xa.json hỏng",
                                code="store_unreadable") from e
                del self.tu_xa[id_]
                self._vt_tu_xa.pop(id_, None)
                if m is not None:
                    await self._dung_an_toan(id_, m)
                await self._don_dep_sau_go(id_)  # NV91: không tác vụ mồ côi, gỡ «gắn» danh tính
                nhat_ky.ghi(
                    "quan_tri",
                    "thong_tin",
                    f"Gỡ module «{id_}» (từ xa)",
                    id_module=id_,
                    nguoi_thuc_hien=nguoi,
                )
                return {"ok": True, "id": id_, "loai": "tu_xa"}
            km = self.catalog.get(id_)
            if km is None:
                raise LoiSo(404, f"Không có module {id_!r}")
            mf_cu = km.manifest  # giữ để tính module chung bị «mồ côi» sau khi gỡ
            co_cau_hinh = id_ in self.dang_chay or self.muc_da_luu(id_) is not None
            if km.nguon == "anh" and not co_cau_hinh:
                raise LoiSo(400, f"Module {id_!r} đi kèm ảnh: mã nằm trong ảnh nên không xoá được, "
                                 "và hiện chưa cài cấu hình nào để gỡ")
            m = self.dang_chay.pop(id_, None)
            if m is not None:
                await self._dung_an_toan(id_, m)
            try:
                kho.xoa_cai_dat(id_)
                kho.xoa_cai_dat_model_module(id_)
            except kho.KhoHong as e:
                raise LoiSo(500, str(e), code="store_unreadable") from e
            if km.nguon == "anh":
                await self._don_dep_sau_go(id_)  # NV91: không tác vụ mồ côi, gỡ «gắn» danh tính
                nhat_ky.ghi(
                    "quan_tri",
                    "thong_tin",
                    f"Gỡ module «{id_}» (gỡ cấu hình)",
                    id_module=id_,
                    nguoi_thuc_hien=nguoi,
                )
                return {"ok": True, "id": id_, "ghi_chu": "đã gỡ cấu hình; mã đi kèm ảnh vẫn còn"}
            # Đổi tên trước rồi mới xoá: thư mục biến mất khỏi khám phá NGAY, xoá dở cũng không nạp lại nửa module.
            rac = km.thu_muc.parent / f".xoa-{id_}-{secrets.token_hex(4)}"
            os.rename(km.thu_muc, rac)
            shutil.rmtree(rac, ignore_errors=True)
            await self._don_dep_sau_go(id_)  # NV91: không tác vụ mồ côi, gỡ «gắn» danh tính
            kq = await self._nap_lai()
            # NV73: module chung mà module vừa gỡ cần, nay không còn ai cần nữa → KHÔNG tự gỡ lén,
            # trả cờ để giao diện hỏi quản trị «giữ hay gỡ theo».
            mo_coi = sorted(d for d in (mf_cu.get("can_module") or [])
                            if isinstance(d, str) and d in self.catalog and not self.dang_duoc_can_boi(d))
            nhat_ky.ghi(
                "quan_tri",
                "thong_tin",
                f"Gỡ module «{id_}»",
                id_module=id_,
                nguoi_thuc_hien=nguoi,
            )
            return {"ok": True, "id": id_, "nap_lai": kq, "hoi_giu_phu_thuoc": mo_coi}

    # ───────────── module từ xa ─────────────

    async def them_tu_xa(self, than: dict) -> dict:
        from app.tu_xa import ModuleTuXa

        api_key = than.get("api_key") or None
        if api_key is not None and not isinstance(api_key, str):
            raise LoiSo(400, "api_key phải là chuỗi")
        sach = await kiem_ban_ghi_tu_xa(than)
        id_, ten, tien_to, base = sach["id"], sach["ten"], sach["tien_to"], sach["base_url"]
        async with self.khoa:
            if id_ in self.catalog or id_ in self.tu_xa:
                raise LoiSo(409, f"id {id_!r} đã có")
            if self.tim_theo_tien_to(tien_to):
                raise LoiSo(409, f"tiền tố {tien_to!r} đã thuộc module {self.tim_theo_tien_to(tien_to)!r}")
            ban_ghi = {"id": id_, "ten": ten, "tien_to": tien_to, "base_url": base, "cai_luc": kho.bay_gio_iso()}
            try:
                kho.luu_tu_xa(ban_ghi, api_key)
            except kho.KhoHong as e:
                raise LoiSo(500, f"Không lưu được module từ xa: {e} — khôi phục hoặc xoá tệp tu_xa.json hỏng",
                            code="store_unreadable") from e
            self.tu_xa[id_] = ban_ghi
            m = ModuleTuXa(ban_ghi, api_key)
            self._vt_tu_xa[id_] = _van_tay_tu_xa(ban_ghi, api_key)
            self.dang_chay[id_] = m
        return {"ok": True, "module": self.cong_khai_tu_xa(id_), "suc_khoe": await m.suc_khoe()}

    def cong_khai_tu_xa(self, id_: str) -> dict:
        b = self.tu_xa[id_]
        co_khoa = bool(kho.doc_bi_mat(id_).get("api_key"))
        return {"id": id_, "ten": b["ten"], "loai": "tu_xa", "tien_to": b["tien_to"], "cai_luc": b["cai_luc"],
                "cau_hinh_cong_khai": {"base_url": b["base_url"],
                                       "api_key": kho.che(kho.doc_bi_mat(id_)["api_key"]) if co_khoa else None},
                "da_cau_hinh": True}  # module từ xa không có bước liên kết: đăng ký xong là phục vụ

    # ───────────── cài thư mục mã (dùng bởi nguồn git) ─────────────

    @staticmethod
    def _nguon_cua_tam(tam: Path) -> dict:
        """Đọc .nguon.json mà nguồn git đã ghi vào thư mục tạm (sha_commit, tree_sha)."""
        try:
            du_lieu = json.loads((tam / ".nguon.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return du_lieu if isinstance(du_lieu, dict) else {}

    @staticmethod
    def thu_muc_cho_duyet(id_: str) -> Path:
        """Thư mục giữ bản chờ duyệt của module `id_` — suy từ id (đã kiểm mẫu ở nơi gọi), cố định dưới thư mục modules."""
        return kho.thu_muc_modules() / f".cho-duyet-{id_}"

    def _cho_duyet_quyen(self, id_: str, tam: Path, mf_moi: dict, nguon: dict, diff: dict) -> None:
        """Bản mới xin thêm quyền: dời `tam` vào nơi chờ duyệt, lưu yêu cầu (nguyên tử, 0600)."""
        cho = self.thu_muc_cho_duyet(id_)  # suy từ id, KHÔNG tin đường dẫn lưu trong tệp JSON
        if cho.exists():
            shutil.rmtree(cho, ignore_errors=True)
        os.rename(tam, cho)
        try:
            kho.luu_cho_duyet_quyen(id_, {
                "id": id_, "sha_commit": nguon.get("sha_commit"), "tree_sha": nguon.get("tree_sha"),
                "diff": diff, "bam_diff": bam_diff(diff), "thu_muc": str(cho),
                "phien_ban": mf_moi.get("phien_ban"), "luc": kho.bay_gio_iso(),
            })
        except kho.KhoHong as e:
            if cho.exists():
                shutil.rmtree(cho, ignore_errors=True)
            raise LoiSo(500, f"Không lưu được yêu cầu chờ duyệt quyền: {e} — khôi phục hoặc xoá tệp cho_duyet_quyen.json hỏng",
                        code="store_unreadable") from e
        viec = f"xin thêm {len(diff['them'])} quyền" if diff["them"] else "đổi mã (không xin thêm quyền)"
        nhat_ky.ghi("he_thong", "canh_bao",
                    f"Module {id_} có bản mới (commit {str(nguon.get('sha_commit'))[:12]}) "
                    f"{viec} — chưa kích hoạt, chờ quản trị duyệt",
                    module=id_)

    @staticmethod
    def _kiem_duyet_quyen(id_: str, nguon: dict, diff: dict, chap_nhan: list | None) -> None:
        """Duyệt phải KHỚP CHÍNH XÁC: cùng SHA đã xem + cùng băm diff + đúng danh sách «them»."""
        pending = kho.doc_cho_duyet_quyen(id_)
        if pending is None:
            raise LoiSo(409, f"Module {id_!r} không có yêu cầu chờ duyệt quyền — cài thường để tạo yêu cầu")
        sha = nguon.get("sha_commit")
        if not sha or sha != pending.get("sha_commit"):
            raise LoiSo(409, "SHA commit đã đổi sau khi xem diff — yêu cầu duyệt cũ mất hiệu lực, "
                             "cài lại để xem diff mới")
        if bam_diff(diff) != pending.get("bam_diff"):
            raise LoiSo(409, "Diff quyền đã đổi — yêu cầu duyệt cũ mất hiệu lực, cài lại để xem diff mới")
        if cac_muc_chuan(chap_nhan) != cac_muc_chuan((pending.get("diff") or {}).get("them")):
            raise LoiSo(409, "Danh sách chấp nhận phải khớp chính xác phần «them» đã xem")

    async def cai_thu_muc(self, id_: str, tam: Path, chap_nhan_quyen_them: list | None = None,
                          tu_dong: bool = False) -> dict:
        """Đổi tên NGUYÊN TỬ `tam` → `<WEB2API_DATA>/modules/<id>/`, nạp lại; bản mới nạp hỏng →
        hoàn về bản cũ (bản cũ không ngừng phục vụ lúc nào) và LoiSo. Module chưa cần cấu hình
        bắt buộc thì tự cài cấu hình rỗng để dùng được ngay.

        Chống rug-pull (cẩm nang §23): module ĐÃ cài mà bản mới xin thêm quyền (so manifest)
        thì KHÔNG kích hoạt — `chap_nhan_quyen_them=None` → lưu yêu cầu chờ duyệt và ném
        LoiCanDuyetQuyen(409); duyệt thì truyền đúng danh sách «them» đã xem (kèm SHA khớp).
        Cài mới (chưa có bản cũ) không bị chặn; phản hồi nêu rõ quyền module xin.

        NV49 R-1: `tu_dong=True` (vòng tự cài, không người bấm) mà module ĐÃ cài → mọi bản mới (đổi mã dù quyền
        giữ nguyên) cũng giữ lại chờ duyệt như trên; chỉ quản trị cài/duyệt tay mới kích hoạt được.

        NV73: phản hồi luôn có `can_module_thieu` — các id trong `can_module` của bản vừa cài mà chưa có
        trên máy (để đề nghị cài, không tự cài lén).
        """
        if not ID_HOP_LE.match(id_):
            raise LoiSo(400, f"id {id_!r} không hợp lệ")
        async with self.khoa:
            km_cu = self.catalog.get(id_)
            dich = kho.thu_muc_modules() / id_
            if km_cu is not None and km_cu.nguon == "anh":
                raise LoiSo(409, f"Module {id_!r} đã đi kèm ảnh — không cập nhật đè từ nguồn ngoài")
            if km_cu is not None and km_cu.thu_muc != dich:
                raise LoiSo(409, f"Id {id_!r} đã có ở {km_cu.thu_muc} — gỡ bản đó trước")
            if id_ in self.tu_xa:
                raise LoiSo(409, f"Id {id_!r} đang là module từ xa")
            # ── Cổng duyệt quyền (chỉ khi đã có bản cũ; cài mới không chặn) ──
            mf_moi, loi_mf = doc_manifest(tam)
            if loi_mf is not None:
                raise LoiSo(422, f"Bản mới nạp hỏng: {loi_mf}")
            diff = diff_quyen(km_cu.manifest if km_cu else {}, mf_moi)
            if km_cu is not None and diff and diff["them"]:
                nguon = self._nguon_cua_tam(tam)
                if chap_nhan_quyen_them is None:
                    self._cho_duyet_quyen(id_, tam, mf_moi, nguon, diff)
                    raise LoiCanDuyetQuyen(id_, diff, nguon.get("sha_commit"), nguon.get("tree_sha"))
                self._kiem_duyet_quyen(id_, nguon, diff, chap_nhan_quyen_them)
            elif km_cu is not None and tu_dong:
                # Không người bấm mà đổi mã module đang được tin → giữ chờ duyệt (diff rỗng hoặc chỉ bớt quyền).
                nguon = self._nguon_cua_tam(tam)
                self._cho_duyet_quyen(id_, tam, mf_moi, nguon, diff or {"them": [], "bot": []})
                raise LoiCanDuyetQuyen(id_, diff or {"them": [], "bot": []}, nguon.get("sha_commit"),
                                       nguon.get("tree_sha"))
            sao_luu = None
            if dich.exists():
                sao_luu = dich.parent / f".cu-{id_}-{secrets.token_hex(4)}"
                os.rename(dich, sao_luu)
            os.rename(tam, dich)
            kq = await self._nap_lai()
            km = self.catalog.get(id_)
            ly_do = [x["ly_do"] for x in kq["loi"] if x["thu_muc"] == str(dich)]
            if km is None or ly_do or km.van_tay != van_tay(dich):
                hong = dich.parent / f".hong-{id_}-{secrets.token_hex(4)}"
                os.rename(dich, hong)
                shutil.rmtree(hong, ignore_errors=True)
                if sao_luu is not None:
                    os.rename(sao_luu, dich)
                await self._nap_lai()
                raise LoiSo(422, "Bản mới nạp hỏng" + (", đã hoàn về bản cũ" if sao_luu else "")
                            + ": " + ("; ".join(ly_do) or "module không được nạp"))
            if sao_luu is not None:
                shutil.rmtree(sao_luu, ignore_errors=True)
            # Đã kích hoạt (duyệt, hoặc quản trị cập nhật tay) → yêu cầu chờ duyệt nào còn lại đều hết hiệu lực.
            kho.xoa_cho_duyet_quyen(id_)
            shutil.rmtree(self.thu_muc_cho_duyet(id_), ignore_errors=True)
            cai = None
            if not km.can_cau_hinh and id_ not in self.dang_chay:
                cai = await self._cai(km, {})
            # NV73: module mới cài cần module khác mà chưa có → ĐỀ NGHỊ cài (không tự cài lén);
            # giao diện dùng danh sách này để hiện nút cài từng cái thiếu.
            return {"nap_lai": kq, "cai": cai, "diff_quyen": diff or {"them": [], "bot": []},
                    "can_module_thieu": self.can_module_thieu(id_)}
