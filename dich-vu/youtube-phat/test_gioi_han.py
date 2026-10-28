"""Trần tài nguyên của add-on (app/gioi_han.py): số trích xuất đồng thời và số mục bộ nhớ đệm."""
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gioi_han  # noqa: E402


class GioiHanTests(unittest.TestCase):
    def test_khong_qua_tran_trich_xuat_dong_thoi(self):
        dang, toi_da, loi, lock = 0, 0, [], threading.Lock()

        def viec():
            nonlocal dang, toi_da
            try:
                with gioi_han.chiem_cho(5, RuntimeError("bận")):
                    with lock:
                        dang += 1
                        toi_da = max(toi_da, dang)
                    time.sleep(0.05)
                    with lock:
                        dang -= 1
            except RuntimeError as e:
                loi.append(e)

        luong = [threading.Thread(target=viec) for _ in range(12)]
        for t in luong: t.start()
        for t in luong: t.join()
        self.assertLessEqual(toi_da, gioi_han.TRICH_XUAT_DONG_THOI)
        self.assertEqual(loi, [])                         # ai cũng được phục vụ, chỉ xếp hàng

    def test_het_cho_qua_thoi_gian_thi_bao_ban_khong_treo(self):
        giu = []
        for _ in range(gioi_han.TRICH_XUAT_DONG_THOI):
            cm = gioi_han.chiem_cho(1, RuntimeError("x"))
            cm.__enter__()
            giu.append(cm)
        try:
            t0 = time.monotonic()
            with self.assertRaises(RuntimeError) as ctx:
                with gioi_han.chiem_cho(0.5, RuntimeError("busy")):
                    pass
            self.assertEqual(str(ctx.exception), "busy")
            self.assertLess(time.monotonic() - t0, 3)
        finally:
            for cm in giu:
                cm.__exit__(None, None, None)

    def test_bo_nho_dem_co_tran_so_muc_bo_muc_sap_het_han(self):
        dem = {f"k{i}": (float(i), {}) for i in range(10)}
        kq = gioi_han.cat_bo_nho_dem(dem, 4, het_han=lambda v: v[0])
        self.assertEqual(sorted(kq), ["k6", "k7", "k8", "k9"])       # giữ 4 mục còn hạn LÂU nhất
        nho = {"a": 1.0}
        self.assertIs(gioi_han.cat_bo_nho_dem(nho, 4), nho)           # chưa tới trần: không đổi


if __name__ == "__main__":
    unittest.main()
