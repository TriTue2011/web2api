"""NV115 — bổ sung của người soát (Claude, 18/10): kẻ lạ KHÔNG được làm tê liệt chủ máy, không làm phình bộ nhớ, không dội tin báo.

Bot nhắn tin công khai thì AI cũng nhắn được. Ba điểm yếu của bản agy (đo bằng test dưới đây):
1. Hạn dòng THEO MODULE chạy TRƯỚC phân quyền → kẻ lạ nhắn dồn dập làm cạn hạn dòng của module, lệnh của CHÍNH CHỦ bị từ chối.
2. Bộ nhớ theo dõi theo người gửi (hạn dòng theo người, tập «đã báo người lạ») không có trần → phình theo số tài khoản lạ.
3. Mỗi người lạ báo MỘT lần nhưng không có trần tổng → 10.000 tài khoản lạ = 10.000 tin báo vào kênh của chủ máy.
"""
import asyncio

from test_web2api_nv115_kenh_vao import (  # noqa: F401  (fixture + hằng của bộ test chính)
    MOD_KENH_A, kenh_vao, moi_truong, so_thiet_bi)


def _tin(i, nguoi, van_ban="/ping"):
    return {"id_tin": f"t-{i}", "nguoi_gui": nguoi, "noi_gui": "chat-a1", "la_nhom": False,
            "ten_tu_khai": f"Lạ {i}", "van_ban": van_ban, "luc": None}


def _bat_bao(chu_may_dich=True):
    so = so_thiet_bi.doc_so()
    so["cau_hinh_bao"] = {"bat": True, "dich": [{"module": MOD_KENH_A, "kenh_id": "chat-a1"}], "bao_mac_ngau_nhien": "gop"}
    so_thiet_bi.ghi_so(so)


def test_nguoi_la_dong_dac_khong_lam_chu_may_bi_tu_choi(moi_truong, monkeypatch):
    monkeypatch.setenv("WEB2API_KENH_VAO_MODULE_PHUT", "20")
    app_obj = moi_truong["client"].app

    async def _chay():
        await kenh_vao.cap_quyen(MOD_KENH_A, "chu-may", "quan_tri", "Chủ máy")

        async def ping(ngu_canh, doi_so):
            return "pong"
        kenh_vao.dang_ky_lenh("ping", ping, "Thử", "quan_tri")
        for i in range(200):                      # 200 tài khoản lạ khác nhau nhắn dồn trong cùng phút
            await kenh_vao.nhan_tin(app_obj, MOD_KENH_A, _tin(i, f"la-{i}"))
        kq = await kenh_vao.nhan_tin(app_obj, MOD_KENH_A, _tin("chu", "chu-may"))
        assert kq.get("ok") is True and kq.get("tra_loi") == "pong", f"chủ máy bị chặn bởi kẻ lạ: {kq}"
    asyncio.run(_chay())


def test_bo_nho_theo_doi_nguoi_gui_co_tran(moi_truong):
    app_obj = moi_truong["client"].app

    async def _chay():
        for i in range(3000):
            await kenh_vao.nhan_tin(app_obj, MOD_KENH_A, _tin(i, f"la-{i}"))
    asyncio.run(_chay())
    assert len(kenh_vao._NGUOI_LA) <= kenh_vao.TRAN_NGUOI_LA
    assert len(kenh_vao._DA_BAO_NGUOI_LA) <= kenh_vao.TRAN_NGUOI_LA, "tập «đã báo người lạ» phình không trần"
    assert len(kenh_vao._HAN_DONG_NGUOI) <= kenh_vao.TRAN_NGUOI_LA, "hạn dòng theo người phình theo số tài khoản lạ"


def test_bao_nguoi_la_co_tran_tong(moi_truong):
    _bat_bao()
    app_obj = moi_truong["client"].app
    mod_ka = moi_truong["mod_ka"]

    async def _chay():
        for i in range(60):
            await kenh_vao.nhan_tin(app_obj, MOD_KENH_A, _tin(i, f"la-{i}"))
    asyncio.run(_chay())
    so_tin_bao = len(mod_ka.da_gui)
    assert 1 <= so_tin_bao <= kenh_vao.TRAN_BAO_NGUOI_LA, f"60 người lạ gây {so_tin_bao} tin báo vào kênh của chủ máy"
