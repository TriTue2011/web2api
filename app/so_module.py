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
import logging
import os
import secrets
import shutil
import sys
from pathlib import Path

from app import he_thong, kho, nhat_ky
from app.kham_pha import (ID_HOP_LE, LoiNap, ModuleKhamPha, doc_manifest, nap_ma, quet, van_tay)

log = logging.getLogger("web2api.so_module")

#: Nguồn module đi kèm ảnh. Thuộc tính mô-đun (đọc lúc gọi) để test trỏ sang thư mục tạm.
THU_MUC_ANH = Path(__file__).resolve().parent / "modules"


class LoiSo(Exception):
    """Lỗi nghiệp vụ trả về cho quản trị: `ma` là mã HTTP gợi ý."""

    def __init__(self, ma: int, thong_diep: str):
        super().__init__(thong_diep)
        self.ma = ma


class SoModule:
    def __init__(self):
        self.catalog: dict[str, ModuleKhamPha] = {}
        self.tu_xa: dict[str, dict] = {}
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
            modules.append({"id": id_, "ten": man["ten"], "loai": man["loai"], "cung_cap": cc,
                            "tien_to": man["tien_to"], "tu_xa": tu_xa, "da_lien_ket": lk})
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
                "tab": tab, "muc": muc, "mui_gio": self.mui_gio}

    # ───────────── khởi động / tắt ─────────────

    async def khoi_dong(self) -> dict:
        from app.tu_xa import ModuleTuXa  # nhập muộn: tu_xa nhập so_module để lấy LoiSo

        async with self.khoa:
            try:
                ds = kho.doc_tu_xa()
            except kho.KhoHong as e:
                log.warning("%s", e)
                self.loi.append({"thu_muc": str(kho.thu_muc_du_lieu() / "tu_xa.json"), "ly_do": str(e)})
                ds = []
            for ban_ghi in ds:
                try:
                    api_key = kho.doc_bi_mat(ban_ghi["id"]).get("api_key")
                    self.tu_xa[ban_ghi["id"]] = ban_ghi
                    self.dang_chay[ban_ghi["id"]] = ModuleTuXa(ban_ghi, api_key)
                except (KeyError, ValueError, kho.KhoHong) as e:
                    log.warning("Bỏ qua module từ xa hỏng %r: %s", ban_ghi, e)
            return await self._nap_lai()

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
                        sys.modules.pop(ten, None)
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
                    sys.modules.pop(truoc.ten_ma, None)  # dọn bản cũ: không giữ mã cũ trong bộ nhớ

        go = [i for i in cu if i not in moi]
        for id_ in go:
            sys.modules.pop(cu[id_].ten_ma, None)
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
        the.nap(sach)
        cu = self.dang_chay.get(km.id)
        self.dang_chay[km.id] = the
        if cu is not None:
            await self._dung_an_toan(km.id, cu)
        return cong_khai

    async def go(self, id_: str) -> dict:
        async with self.khoa:
            if id_ in self.tu_xa:
                m = self.dang_chay.pop(id_, None)
                kho.xoa_tu_xa(id_)
                kho.xoa_cai_dat_model_module(id_)
                del self.tu_xa[id_]
                if m is not None:
                    await self._dung_an_toan(id_, m)
                return {"ok": True, "id": id_, "loai": "tu_xa"}
            km = self.catalog.get(id_)
            if km is None:
                raise LoiSo(404, f"Không có module {id_!r}")
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
                raise LoiSo(500, str(e)) from e
            if km.nguon == "anh":
                return {"ok": True, "id": id_, "ghi_chu": "đã gỡ cấu hình; mã đi kèm ảnh vẫn còn"}
            # Đổi tên trước rồi mới xoá: thư mục biến mất khỏi khám phá NGAY, xoá dở cũng không nạp lại nửa module.
            rac = km.thu_muc.parent / f".xoa-{id_}-{secrets.token_hex(4)}"
            os.rename(km.thu_muc, rac)
            shutil.rmtree(rac, ignore_errors=True)
            kq = await self._nap_lai()
            return {"ok": True, "id": id_, "nap_lai": kq}

    # ───────────── module từ xa ─────────────

    async def them_tu_xa(self, than: dict) -> dict:
        from app.kham_pha import ID_DANH_RIENG, TIEN_TO_HOP_LE
        from app.tu_xa import ModuleTuXa, kiem_url_lan

        id_, ten, tien_to = than.get("id"), than.get("ten"), than.get("tien_to")
        api_key = than.get("api_key") or None
        if not isinstance(id_, str) or not ID_HOP_LE.match(id_) or id_ in ID_DANH_RIENG:
            raise LoiSo(400, "id không hợp lệ (cần ^[a-z0-9][a-z0-9_-]{1,40}$, không trùng tên tuyến)")
        if not isinstance(ten, str) or not ten.strip():
            raise LoiSo(400, "thiếu «ten»")
        if not isinstance(tien_to, str) or not TIEN_TO_HOP_LE.match(tien_to):
            raise LoiSo(400, "tien_to không hợp lệ (chữ, số, . _ -; không có «/»)")
        if api_key is not None and not isinstance(api_key, str):
            raise LoiSo(400, "api_key phải là chuỗi")
        try:
            base = await kiem_url_lan(than.get("base_url"))
        except ValueError as e:
            raise LoiSo(400, str(e)) from None
        async with self.khoa:
            if id_ in self.catalog or id_ in self.tu_xa:
                raise LoiSo(409, f"id {id_!r} đã có")
            if self.tim_theo_tien_to(tien_to):
                raise LoiSo(409, f"tiền tố {tien_to!r} đã thuộc module {self.tim_theo_tien_to(tien_to)!r}")
            ban_ghi = {"id": id_, "ten": ten.strip(), "tien_to": tien_to, "base_url": base,
                       "cai_luc": kho.bay_gio_iso()}
            kho.luu_tu_xa(ban_ghi, api_key)
            self.tu_xa[id_] = ban_ghi
            m = ModuleTuXa(ban_ghi, api_key)
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

    async def cai_thu_muc(self, id_: str, tam: Path) -> dict:
        """Đổi tên NGUYÊN TỬ `tam` → `<WEB2API_DATA>/modules/<id>/`, nạp lại; bản mới nạp hỏng →
        hoàn về bản cũ (bản cũ không ngừng phục vụ lúc nào) và LoiSo. Module chưa cần cấu hình
        bắt buộc thì tự cài cấu hình rỗng để dùng được ngay."""
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
            cai = None
            if not km.can_cau_hinh and id_ not in self.dang_chay:
                cai = await self._cai(km, {})
            return {"nap_lai": kq, "cai": cai}
