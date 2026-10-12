"""Cơ chế quy trình nối tầng (phễu tìm kiếm và xử lý dữ liệu dùng chung).

Nguyên tắc:
- Lõi không chứa tên công nghệ cụ thể; chỉ biết các toán tử dữ liệu thuần và khả năng của module.
- Quy trình gồm danh sách tầng có thứ tự: [{ten, kha_nang, nguon?, tham_so?}].
- Tầng sau nhận kết quả tầng trước; đo từng tầng (số mục vào/ra, ms, lỗi).
- Tầng hỏng dừng ngay và nêu đúng tầng hỏng, báo cáo luôn trả về được.
- Trần số tầng: 8; trần thời gian tổng: WEB2API_QUY_TRINH_GIAY (mặc định 60s) -> 504.
- Không phải ngôn ngữ lập trình: không biểu thức, không eval, không vòng lặp.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any

from app.loi_openai import LoiOpenAI
from app.so_module import SoModule

log = logging.getLogger("web2api.quy_trinh")

TRAN_SO_TANG = 8
MAC_DINH_THOI_GIAN_GIAY = 60.0


class LoiQuyTrinh(LoiOpenAI):
    """Lỗi xảy ra trong quá trình chạy một tầng của quy trình."""

    def __init__(
        self,
        ma: int,
        thong_diep: str,
        bao_cao: list[dict],
        tang_loi: str,
        loai: str | None = None,
        code=None,
    ):
        super().__init__(ma, thong_diep, loai=loai, code=code or "step_failed")
        self.bao_cao = bao_cao
        self.tang_loi = tang_loi
        self.than["error"]["tang_loi"] = tang_loi
        self.than["error"]["bao_cao"] = bao_cao
        self.than["bao_cao"] = bao_cao


# ───────────── Các toán tử dữ liệu thuần của lõi ─────────────


def toan_tu_gop(du_lieu_vao: dict, k: int = 60) -> dict:
    """Hợp nhất danh sách kết quả theo giải thuật Reciprocal Rank Fusion (RRF).

    Công thức: điểm = Σ 1 / (k + hạng), với hạng tính từ 1.
    """
    if not isinstance(du_lieu_vao, dict):
        return {"muc": []}

    danh_sach_nguon: list[list[dict]] = []

    # 1. Đầu vào từ danh_sach (danh sách các danh sách)
    if isinstance(du_lieu_vao.get("danh_sach"), list):
        for sub in du_lieu_vao["danh_sach"]:
            if isinstance(sub, list):
                danh_sach_nguon.append([x for x in sub if isinstance(x, dict)])

    # 2. Hoặc trường muc là danh sách các danh sách
    elif isinstance(du_lieu_vao.get("muc"), list) and du_lieu_vao["muc"] and isinstance(du_lieu_vao["muc"][0], list):
        for sub in du_lieu_vao["muc"]:
            if isinstance(sub, list):
                danh_sach_nguon.append([x for x in sub if isinstance(x, dict)])

    # 3. Hoặc trường muc là danh sách phẳng chứa các mục đã có trường nguon
    elif isinstance(du_lieu_vao.get("muc"), list):
        nhom_theo_nguon: dict[str, list[dict]] = {}
        for item in du_lieu_vao["muc"]:
            if not isinstance(item, dict):
                continue
            nguon_id = str(item.get("nguon") or "mac_dinh")
            nhom_theo_nguon.setdefault(nguon_id, []).append(item)
        danh_sach_nguon = list(nhom_theo_nguon.values())

    if not danh_sach_nguon:
        muc_cu = du_lieu_vao.get("muc", [])
        return {**du_lieu_vao, "muc": muc_cu if isinstance(muc_cu, list) else []}

    # Tính điểm RRF
    bang_diem: dict[str, float] = {}
    ban_ghi: dict[str, dict] = {}

    for ds in danh_sach_nguon:
        for hang, item in enumerate(ds, start=1):
            m_id = str(item.get("id") or f"id_{id(item)}")
            diem_them = 1.0 / (float(k) + float(hang))
            bang_diem[m_id] = bang_diem.get(m_id, 0.0) + diem_them
            if m_id not in ban_ghi:
                ban_ghi[m_id] = dict(item)

    ket_qua_muc: list[dict] = []
    for m_id, tong_diem in sorted(bang_diem.items(), key=lambda x: x[1], reverse=True):
        muc = dict(ban_ghi[m_id])
        muc["diem"] = round(tong_diem, 8)
        muc["nguon_ngoai"] = True
        ket_qua_muc.append(muc)

    return {**du_lieu_vao, "muc": ket_qua_muc}


def toan_tu_loc(du_lieu_vao: dict, dieu_kien: dict) -> dict:
    """Lọc các mục theo từ điển trường=giá trị trên trường 'truong' của từng mục."""
    if not isinstance(du_lieu_vao, dict):
        return {"muc": []}
    muc_in = du_lieu_vao.get("muc")
    if not isinstance(muc_in, list):
        return {**du_lieu_vao, "muc": []}
    if not isinstance(dieu_kien, dict) or not dieu_kien:
        return du_lieu_vao

    ket_qua = []
    for item in muc_in:
        if not isinstance(item, dict):
            continue
        truong_item = item.get("truong") or {}
        if not isinstance(truong_item, dict):
            truong_item = {}
        khop = True
        for k, v in dieu_kien.items():
            gia_tri_that = truong_item.get(k)
            if isinstance(v, list):
                if gia_tri_that not in v:
                    khop = False
                    break
            else:
                if gia_tri_that != v:
                    khop = False
                    break
        if khop:
            ket_qua.append(item)
    return {**du_lieu_vao, "muc": ket_qua}


def toan_tu_cat(du_lieu_vao: dict, so_luong: int) -> dict:
    """Lấy top N mục đầu tiên từ danh sách kết quả."""
    if not isinstance(du_lieu_vao, dict):
        return {"muc": []}
    muc_in = du_lieu_vao.get("muc")
    if not isinstance(muc_in, list):
        return {**du_lieu_vao, "muc": []}
    n = max(0, int(so_luong))
    return {**du_lieu_vao, "muc": muc_in[:n]}


def toan_tu_gop_ngu_canh(du_lieu_vao: dict, tran_ky_tu: int) -> dict:
    """Cắt danh sách sao cho tổng độ dài các đoạn text (trường 'doan') <= trần ký tự."""
    if not isinstance(du_lieu_vao, dict):
        return {"muc": []}
    muc_in = du_lieu_vao.get("muc")
    if not isinstance(muc_in, list):
        return {**du_lieu_vao, "muc": []}

    tran = max(0, int(tran_ky_tu))
    tong_do_dai = 0
    ket_qua = []
    for item in muc_in:
        if not isinstance(item, dict):
            continue
        doan = str(item.get("doan") or "")
        do_dai = len(doan)
        if tong_do_dai + do_dai <= tran:
            ket_qua.append(item)
            tong_do_dai += do_dai
        else:
            break
    return {**du_lieu_vao, "muc": ket_qua}


def toan_tu_cay(du_lieu_vao: dict, cay_cfg: dict) -> dict:
    """Lọc theo đường dẫn phân cấp trên trường 'truong' của mục (tiền tố gốc, độ sâu tối đa)."""
    if not isinstance(du_lieu_vao, dict):
        return {"muc": []}
    muc_in = du_lieu_vao.get("muc")
    if not isinstance(muc_in, list):
        return {**du_lieu_vao, "muc": []}
    if not isinstance(cay_cfg, dict) or not cay_cfg:
        return du_lieu_vao

    truong_ten = str(cay_cfg.get("truong") or "duong_dan")
    goc_tho = str(cay_cfg.get("goc") or "").strip()
    goc = goc_tho.strip("/")
    do_sau = cay_cfg.get("do_sau")
    do_sau_int = int(do_sau) if do_sau is not None else None

    ket_qua = []
    for item in muc_in:
        if not isinstance(item, dict):
            continue
        # Nếu module đã tự áp dụng lọc cây, bỏ qua bước lọc lại của lõi
        if item.get("cay_da_ap") is True:
            ket_qua.append(item)
            continue

        truong_item = item.get("truong") or {}
        if not isinstance(truong_item, dict):
            continue
        path_tho = str(truong_item.get(truong_ten) or "").strip()
        path = path_tho.strip("/")

        # Kiểm tra tiền tố gốc
        if goc:
            if path != goc and not path.startswith(f"{goc}/"):
                continue
            if do_sau_int is not None:
                con = path[len(goc):].strip("/")
                phan_doan = [p for p in con.split("/") if p]
                if len(phan_doan) > do_sau_int:
                    continue
        else:
            if do_sau_int is not None:
                phan_doan = [p for p in path.split("/") if p]
                if len(phan_doan) > do_sau_int:
                    continue

        ket_qua.append(item)

    return {**du_lieu_vao, "muc": ket_qua}


# ───────────── Thực thi từng tầng ─────────────


async def _thuc_hien_mot_tang(
    kha_nang: str,
    nguon: str | None,
    tham_so: dict,
    du_lieu_vao: dict,
    so: SoModule,
) -> dict:
    """Thực thi một tầng đơn lẻ của quy trình."""
    # 1. Toán tử gộp RRF
    if kha_nang == "gop":
        k = int(tham_so.get("k", 60))
        return toan_tu_gop(du_lieu_vao, k=k)

    # 2. Toán tử lọc
    if kha_nang == "loc":
        dieu_kien = tham_so.get("dieu_kien") if "dieu_kien" in tham_so else tham_so
        return toan_tu_loc(du_lieu_vao, dieu_kien)

    # 3. Toán tử cắt (top N)
    if kha_nang == "cat":
        n = tham_so.get("n") or tham_so.get("so_luong") or tham_so.get("top_n") or du_lieu_vao.get("so_luong", 10)
        return toan_tu_cat(du_lieu_vao, int(n))

    # 4. Toán tử gộp ngữ cảnh
    if kha_nang == "gop_ngu_canh":
        tran = tham_so.get("tran_ky_tu") or tham_so.get("tran") or tham_so.get("max_chars")
        if tran is None and isinstance(du_lieu_vao.get("ngu_canh"), dict):
            tran = du_lieu_vao["ngu_canh"].get("tran_ky_tu")
        return toan_tu_gop_ngu_canh(du_lieu_vao, int(tran if tran is not None else 2000))

    # 5. Toán tử cây
    if kha_nang == "cay":
        cay_cfg = tham_so if ("truong" in tham_so or "goc" in tham_so) else du_lieu_vao.get("cay", {})
        return toan_tu_cay(du_lieu_vao, cay_cfg)

    # 6. Khả năng tìm kiếm (tim_kiem)
    if kha_nang == "tim_kiem":
        yc = {**du_lieu_vao, **tham_so}
        if nguon:
            m = so.dang_chay.get(nguon)
            km = so.catalog.get(nguon)
            if not m or not callable(getattr(m, "tim_kiem", None)):
                raise LoiOpenAI(404, f"Module {nguon!r} không sẵn sàng hoặc không hỗ trợ tim_kiem", code="module_not_ready")
            if km and "tim_kiem" in getattr(km, "chuan_loi", {}):
                raise LoiOpenAI(501, f"Khả năng «tim_kiem» của module {nguon!r} không đúng chuẩn", code="capability_not_supported")
            kq = await m.tim_kiem(yc)
            if not isinstance(kq, dict):
                kq = {"muc": []}
            muc = kq.get("muc", [])
            for x in muc:
                if isinstance(x, dict):
                    x["nguon"] = nguon
                    x["nguon_ngoai"] = True
            return {**du_lieu_vao, **kq, "muc": muc}

        # Gọi mọi module có khai tim_kiem song song
        ds_m = []
        for mid, m in list(so.dang_chay.items()):
            km = so.catalog.get(mid)
            if km and "tim_kiem" in km.manifest.get("cung_cap", []) and "tim_kiem" not in getattr(km, "chuan_loi", {}):
                if callable(getattr(m, "tim_kiem", None)):
                    ds_m.append((mid, m))

        loc_nguon = du_lieu_vao.get("nguon")
        if isinstance(loc_nguon, str) and loc_nguon:
            loc_nguon = [loc_nguon]
        if isinstance(loc_nguon, list):
            ds_m = [(mid, m) for mid, m in ds_m if mid in loc_nguon]

        if not ds_m:
            return {**du_lieu_vao, "muc": [], "tong": 0}

        cac_task = [m.tim_kiem(yc) for _, m in ds_m]
        cac_kq = await asyncio.gather(*cac_task, return_exceptions=True)

        danh_sach_cac_nguon = []
        tat_ca_muc = []
        for (mid, _), res in zip(ds_m, cac_kq):
            if isinstance(res, Exception):
                log.warning("Module %s lỗi khi gọi tim_kiem: %s", mid, res)
                continue
            if isinstance(res, dict) and isinstance(res.get("muc"), list):
                sub_list = []
                for item in res["muc"]:
                    if isinstance(item, dict):
                        item_moi = dict(item)
                        item_moi["nguon"] = mid
                        item_moi["nguon_ngoai"] = True
                        sub_list.append(item_moi)
                        tat_ca_muc.append(item_moi)
                danh_sach_cac_nguon.append(sub_list)

        return {**du_lieu_vao, "danh_sach": danh_sach_cac_nguon, "muc": tat_ca_muc}

    # 7. Khả năng xếp hạng lại (xep_hang)
    if kha_nang == "xep_hang":
        muc_in = du_lieu_vao.get("muc", [])
        if not isinstance(muc_in, list) or not muc_in:
            return du_lieu_vao

        m = None
        km = None
        if nguon:
            m = so.dang_chay.get(nguon)
            km = so.catalog.get(nguon)
            if not m or not callable(getattr(m, "xep_hang", None)):
                raise LoiOpenAI(404, f"Module {nguon!r} không sẵn sàng hoặc không hỗ trợ xep_hang", code="module_not_ready")
            if km and "xep_hang" in getattr(km, "chuan_loi", {}):
                raise LoiOpenAI(501, f"Khả năng «xep_hang» của module {nguon!r} không đúng chuẩn", code="capability_not_supported")
        else:
            for mid, mod in list(so.dang_chay.items()):
                c_km = so.catalog.get(mid)
                if c_km and "xep_hang" in c_km.manifest.get("cung_cap", []) and "xep_hang" not in getattr(c_km, "chuan_loi", {}):
                    if callable(getattr(mod, "xep_hang", None)):
                        m = mod
                        km = c_km
                        break

        if not m:
            raise LoiOpenAI(400, "Không có module nào hỗ trợ khả năng «xep_hang»", code="unknown_capability")

        query = str(tham_so.get("query") or tham_so.get("chuoi") or du_lieu_vao.get("chuoi") or "")
        docs = [str(item.get("doan") or item.get("text") or "") for item in muc_in if isinstance(item, dict)]
        model = str(tham_so.get("model") or (f"{km.tien_to}/rerank" if km else "rerank"))
        top_n = tham_so.get("top_n") or tham_so.get("n") or len(docs)
        top_n = min(int(top_n), len(docs)) if docs else 0

        kq_raw = await m.xep_hang({"model": model, "query": query, "documents": docs, "top_n": top_n})
        if isinstance(kq_raw, tuple) and len(kq_raw) == 3:
            ma, _, noi_dung = kq_raw
            if ma >= 400:
                raise LoiOpenAI(ma, f"Module xếp hạng trả mã lỗi {ma}")
            if isinstance(noi_dung, (bytes, bytearray)):
                noi_dung = json.loads(noi_dung)
            kq_json = noi_dung
        elif isinstance(kq_raw, dict):
            kq_json = kq_raw
        else:
            raise LoiOpenAI(502, "Kết quả từ module xep_hang không đúng định dạng", code="upstream_error")

        results = kq_json.get("results", [])
        if not isinstance(results, list):
            results = []

        muc_sap_xep = []
        for r in results:
            if not isinstance(r, dict):
                continue
            idx = r.get("index")
            diem = r.get("relevance_score", 0.0)
            if isinstance(idx, int) and 0 <= idx < len(muc_in):
                muc_item = dict(muc_in[idx])
                muc_item["diem"] = diem
                muc_item["nguon_ngoai"] = True
                muc_sap_xep.append(muc_item)

        if top_n and top_n > 0:
            muc_sap_xep = muc_sap_xep[:top_n]

        return {**du_lieu_vao, "muc": muc_sap_xep}

    # 8. Khả năng khác do module khai
    if nguon:
        m = so.dang_chay.get(nguon)
        if m and callable(getattr(m, kha_nang, None)):
            yc = {**du_lieu_vao, **tham_so}
            kq = await getattr(m, kha_nang)(yc)
            return {**du_lieu_vao, **kq} if isinstance(kq, dict) else du_lieu_vao

    for mid, m in list(so.dang_chay.items()):
        km = so.catalog.get(mid)
        if km and kha_nang in km.manifest.get("cung_cap", []) and callable(getattr(m, kha_nang, None)):
            yc = {**du_lieu_vao, **tham_so}
            kq = await getattr(m, kha_nang)(yc)
            return {**du_lieu_vao, **kq} if isinstance(kq, dict) else du_lieu_vao

    # Khả năng lạ không thuộc toán tử và không có module khai
    raise LoiOpenAI(400, f"Khả năng «{kha_nang}» không hợp lệ hoặc không có module nào hỗ trợ", code="unknown_capability")


# ───────────── Hàm điều phối quy trình chính ─────────────


async def chay(tang: list, du_lieu_vao: dict, so: SoModule) -> dict:
    """Chạy quy trình nối tầng với danh sách tầng có thứ tự.

    Trả về: {"ket_qua": ..., "bao_cao": [{ten, vao, ra, ms, loi?}]}
    """
    if not isinstance(tang, list):
        raise LoiOpenAI(400, "Danh sách tầng «pheu» phải là mảng", code="invalid_pipeline")

    if len(tang) > TRAN_SO_TANG:
        raise LoiOpenAI(400, f"Vượt quá trần số tầng quy trình (tối đa {TRAN_SO_TANG}, nhận {len(tang)})", code="too_many_steps")

    try:
        gioi_han_giay = float(os.environ.get("WEB2API_QUY_TRINH_GIAY", MAC_DINH_THOI_GIAN_GIAY))
    except (ValueError, TypeError):
        gioi_han_giay = MAC_DINH_THOI_GIAN_GIAY

    thoi_gian_bat_dau = time.monotonic()
    du_lieu_hien_tai = dict(du_lieu_vao) if isinstance(du_lieu_vao, dict) else {}
    bao_cao: list[dict] = []

    for i, t in enumerate(tang):
        if not isinstance(t, dict):
            raise LoiOpenAI(400, f"Tầng thứ {i+1} phải là đối tượng JSON", code="invalid_step")

        kha_nang = str(t.get("kha_nang") or "").strip()
        if not kha_nang:
            raise LoiOpenAI(400, f"Tầng thứ {i+1} thiếu «kha_nang»", code="missing_capability")

        ten = str(t.get("ten") or f"tang_{i+1}_{kha_nang}")
        nguon = str(t.get("nguon")).strip() if t.get("nguon") else None
        tham_so = t.get("tham_so") or {}
        if not isinstance(tham_so, dict):
            tham_so = {}

        # Đo số lượng mục vào
        if isinstance(du_lieu_hien_tai.get("muc"), list):
            vao = len(du_lieu_hien_tai["muc"])
        elif isinstance(du_lieu_hien_tai.get("danh_sach"), list):
            vao = sum(len(sub) for sub in du_lieu_hien_tai["danh_sach"] if isinstance(sub, list))
        else:
            vao = 0

        # Kiểm tra thời gian còn lại
        da_chay = time.monotonic() - thoi_gian_bat_dau
        con_lai = gioi_han_giay - da_chay
        if con_lai <= 0:
            bao_cao.append({"ten": ten, "vao": vao, "ra": 0, "ms": 0.0, "loi": f"Quá hạn thời gian quy trình ({gioi_han_giay}s)"})
            raise LoiQuyTrinh(504, f"Quá hạn thời gian quy trình ({gioi_han_giay}s) tại tầng {ten!r}", bao_cao=bao_cao, tang_loi=ten, code="gateway_timeout")

        t0 = time.monotonic()
        try:
            du_lieu_moi = await asyncio.wait_for(
                _thuc_hien_mot_tang(kha_nang, nguon, tham_so, du_lieu_hien_tai, so),
                timeout=con_lai,
            )
        except asyncio.TimeoutError:
            ms = round((time.monotonic() - t0) * 1000, 2)
            bao_cao.append({"ten": ten, "vao": vao, "ra": 0, "ms": ms, "loi": f"Quá hạn thời gian quy trình ({gioi_han_giay}s)"})
            raise LoiQuyTrinh(504, f"Quá hạn thời gian quy trình ({gioi_han_giay}s) tại tầng {ten!r}", bao_cao=bao_cao, tang_loi=ten, code="gateway_timeout")
        except Exception as e:
            ms = round((time.monotonic() - t0) * 1000, 2)
            # Lỗi chuẩn của lõi (LoiOpenAI) đã che khoá → giữ nguyên văn; lỗi lạ từ mã module có thể chứa dữ liệu nội bộ
            # → chỉ ghi log, khách nhận thông báo chung.
            if isinstance(e, LoiOpenAI):
                mo_ta = str(e)
            else:
                log.warning("Quy trình: tầng %r lỗi %s: %s", ten, type(e).__name__, e)
                mo_ta = f"tầng gặp lỗi nội bộ ({type(e).__name__})"
            bao_cao.append({"ten": ten, "vao": vao, "ra": 0, "ms": ms, "loi": mo_ta})
            ma_loi = getattr(e, "ma", 500) if isinstance(e, LoiOpenAI) else 500
            code_loi = getattr(e, "than", {}).get("error", {}).get("code", "step_failed") if isinstance(e, LoiOpenAI) else "step_failed"
            raise LoiQuyTrinh(ma_loi, f"Lỗi tại tầng {ten!r}: {mo_ta}", bao_cao=bao_cao, tang_loi=ten, code=code_loi) from e

        ms = round((time.monotonic() - t0) * 1000, 2)
        if isinstance(du_lieu_moi, dict) and isinstance(du_lieu_moi.get("muc"), list):
            ra = len(du_lieu_moi["muc"])
        elif isinstance(du_lieu_moi, dict) and isinstance(du_lieu_moi.get("danh_sach"), list):
            ra = sum(len(sub) for sub in du_lieu_moi["danh_sach"] if isinstance(sub, list))
        else:
            ra = vao

        bao_cao.append({"ten": ten, "vao": vao, "ra": ra, "ms": ms})
        du_lieu_hien_tai = du_lieu_moi

    return {"ket_qua": du_lieu_hien_tai, "bao_cao": bao_cao}
