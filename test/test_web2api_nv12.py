# NV12: model tuỳ ý của khách làm phình _khong_co/_model_duoc không giới hạn
#
# Test bằng thượng nguồn giả (httpx.MockTransport): model lạ → 404 → phân loại
# "khong_co_o_khoa" (đúng hợp đồng module) → ghi vào _khong_co.
import asyncio

import httpx
import pytest

from app.chuyen_tiep import ChuyenTiepOpenAI, KHONG_CO_TOI_DA, MODEL_DUOC_TOI_DA, MODEL_KHOA_TOI_DA


def _ct_mock(khoa=("k1",), phan_loai=None):
    """ChuyenTiepOpenAI với transport giả: luôn trả 404."""
    def _tra_loi(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": {"message": "no such model"}})

    ct = ChuyenTiepOpenAI(
        ten="X", tien_to="x", base_url="http://mock/v1", khoa=list(khoa),
        phan_loai_loi=phan_loai or (lambda ma, loi, bc: "khong_co_o_khoa" if ma == 404 else None),
    )
    # Chèn MockTransport thay cho TransportGhimIp
    ct._khach = httpx.AsyncClient(transport=httpx.MockTransport(_tra_loi))  # noqa: SLF001
    ct._client = lambda: ct._khach  # noqa: SLF001
    return ct


def test_model_qua_dai_bi_tu_choi_truoc_khi_lam_khoa():
    """Model dài hơn trần → 400 ngay, không đi vào dict làm khoá."""
    ct = _ct_mock()

    async def chay():
        try:
            return await ct.chat({"model": "x/" + "a" * (MODEL_KHOA_TOI_DA + 1)}, stream=False)
        finally:
            await ct.dung()

    ma, _, _ = asyncio.run(chay())
    assert ma == 400
    assert len(ct._khong_co) == 0  # noqa: SLF001 — không ghi gì


def test_khong_co_khong_vuot_tran_50000_model_la():
    """Model lạ vượt trần → số mục _khong_co dừng ở đúng trần và dọn mục cũ nhất."""
    ct = _ct_mock()

    async def chay():
        try:
            for i in range(1050):
                await ct.chat({"model": f"x/model-la-{i:05d}"}, stream=False)
            return ct._khong_co  # noqa: SLF001
        finally:
            await ct.dung()

    khong_co = asyncio.run(chay())
    assert len(khong_co) == KHONG_CO_TOI_DA, f"Phải chạm trần {KHONG_CO_TOI_DA}, hiện tại {len(khong_co)}"
    # Mục cũ đầu tiên đã bị pop khỏi dict (FIFO khi đầy)
    assert ("k1", "model-la-00000") not in khong_co
    assert ("k1", "model-la-00049") not in khong_co
    # Các mục mới nhất còn nguyên
    assert ("k1", "model-la-01049") in khong_co


def test_khong_co_don_muc_het_han_khi_ghi():
    """Mục _khong_co hết hạn bị dọn khi ghi mục mới (không nằm vĩnh viễn)."""
    ct = _ct_mock()

    async def chay():
        try:
            await ct.chat({"model": "x/cu"}, stream=False)
            assert len(ct._khong_co) == 1  # noqa: SLF001
            for k in list(ct._khong_co):  # noqa: SLF001 — giả lập hết hạn
                ct._khong_co[k] = 0.0  # noqa: SLF001
            await ct.chat({"model": "x/moi"}, stream=False)
            return len(ct._khong_co)  # noqa: SLF001
        finally:
            await ct.dung()

    assert asyncio.run(chay()) == 1


def test_model_duoc_khong_vuot_tran():
    """_model_duoc có trần số mục, chạm trần và dọn mục cũ nhất."""
    ct = _ct_mock()
    for i in range(MODEL_DUOC_TOI_DA + 500):
        ct._ghi_model_duoc("k1", f"m-{i}")  # noqa: SLF001
    assert len(ct._model_duoc) == MODEL_DUOC_TOI_DA  # noqa: SLF001
    assert ("k1", "m-0") not in ct._model_duoc  # noqa: SLF001
    assert ("k1", "m-499") not in ct._model_duoc  # noqa: SLF001
    assert ("k1", f"m-{MODEL_DUOC_TOI_DA + 499}") in ct._model_duoc  # noqa: SLF001


def test_log_cat_model_dai():
    """Log 'không có model' cắt tên model bằng nhac_lai, không ghi nguyên chuỗi MB."""
    import logging

    ct = _ct_mock()
    ghi = []
    h = logging.Handler()
    h.emit = lambda r: ghi.append(r.getMessage())  # noqa: ARG005
    ct.log.addHandler(h)
    ct.log.setLevel(logging.WARNING)

    async def chay():
        try:
            # 200 ký tự: qua được trần 256 nhưng dài hơn TRAN_TOKEN=120 của nhac_lai
            await ct.chat({"model": "x/" + "M" * 200}, stream=False)
        finally:
            await ct.dung()
            ct.log.removeHandler(h)

    asyncio.run(chay())
    assert ghi, "phải có dòng log cảnh báo"
    assert "M" * 150 not in ghi[0], "log ghi nguyên model dài"
    assert len(ghi[0]) < 1000


def test_hanh_vi_cu_giu_nguyen_thu_khoa_ke_khi_404():
    """Hành vi cũ không đổi: khoá 404 'khong_co_o_khoa' thì thử khoá kế tiếp."""
    goi = []

    def _tra_loi(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("authorization", "")
        goi.append(auth)
        if "k1" in auth:
            return httpx.Response(404, json={"error": {"message": "no"}})
        return httpx.Response(200, json={"choices": []})

    ct = ChuyenTiepOpenAI(
        ten="X", tien_to="x", base_url="http://mock/v1", khoa=["k1", "k2"],
        phan_loai_loi=lambda ma, loi, bc: "khong_co_o_khoa" if ma == 404 else None,
    )
    ct._khach = httpx.AsyncClient(transport=httpx.MockTransport(_tra_loi))  # noqa: SLF001
    ct._client = lambda: ct._khach  # noqa: SLF001

    async def chay():
        try:
            ma, _, _ = await ct.chat({"model": "x/m"}, stream=False)
            return ma
        finally:
            await ct.dung()

    assert asyncio.run(chay()) == 200
    assert any("k2" in g for g in goi), "phải thử khoá k2 sau khi k1 404"
    assert ("k1", "m") in ct._khong_co  # noqa: SLF001 — k1 bị ghi sổ
