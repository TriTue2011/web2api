"""Sổ khuôn mặt — logic thuần (không mạng, không numpy, không trạng thái).

Port từ c2a `services/so_mat_nha.py` (commit d879b03, bản sao
~/workspace/c2a-nv146/services/so_mat_nha.py) + hằng thẩm định từ
`services/khuon_mat_nha.py`. Chỉ đổi lớp lưu trữ/kết nối: so khớp dùng phép
chấm thuần Python trên vector float32 (512 chiều) thay vì ma trận numpy, và
hàm ở đây KHÔNG giữ trạng thái — sổ đọc/ghi ở `module.py` qua kho riêng của
lõi (`app.ham_khoa.KhoRieng`, mã hoá khi nghỉ nếu có khoá chính, không thì
0600).

Cách nhận giống IRIS (anhnvme/facedetect): mỗi người giữ vài vector tham
chiếu, mặt mới so cosine với TỪNG vector, lấy người giống nhất. Từ ngưỡng
«có thể là» trở lên là ứng viên, từ ngưỡng «chắc» là nhận luôn — dưới đó là
người lạ. Ngưỡng «có thể» TỰ CHỈNH theo dữ liệu của chính nhà (c2a, chủ máy
24/09/2026): nằm TRÊN mọi cặp khác người đã biết một biên an toàn 6 điểm,
không vượt cấu hình, không dưới sàn 30. Đo trên 17 ảnh mẫu camera nhà (c2a):
hai ảnh CÙNG người giống 14–57, hai người KHÁC nhau cao nhất 29.
"""
from __future__ import annotations

import math
import re
import struct
import unicodedata

#: Độ nét vùng mặt đã căn dưới ngần này là mảng phẳng, không phải mặt.
#: Port `khuon_mat_nha.NET_TOI_THIEU` — đo trên 573 lượt camera thật (c2a):
#: 0/122 mặt từng nhận «quen» có độ nét dưới 10.
NET_TOI_THIEU = 10.0
#: Mặt nhỏ hơn ngần này (px, cạnh ngắn của hộp) thì vector kém tin.
MAT_NHO_NHAT = 40
#: Hai mẫu của cùng một người giống nhau từ mức này (0–100) thì là một mặt
#: chụp lại, không giữ cả hai.
NGUONG_TRUNG = 92.0
#: Biên an toàn trên cặp khác người giống nhất đã biết.
_BIEN_AN_TOAN = 6.0
#: Sàn của ngưỡng «có thể» tự chỉnh.
_SAN_CO_THE = 30.0
#: Trần sự kiện giữ trong sổ (giữ mới nhất).
TRAN_SU_KIEN = 500
_CHIEU = 512


def vec_tu_bytes(b: bytes):
    if len(b) != _CHIEU * 4:
        return None
    return struct.unpack(f"<{_CHIEU}f", b)


def do_giong(a: tuple, b: tuple) -> float:
    """Độ giống kiểu IRIS: cosine của hai vector đã chuẩn hoá, kẹp ≥ 0, thang 0–100."""
    if a is None or b is None:
        return 0.0
    return max(0.0, sum(x * y for x, y in zip(a, b))) * 100.0


def gon_ten(ten: str) -> str:
    """Tên để so trùng: bỏ dấu, hạ chữ, gộp khoảng trắng (port `_gon_ten`)."""
    s = unicodedata.normalize("NFD", str(ten or ""))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.replace("đ", "d").replace("Đ", "D").lower()
    return " ".join(re.split(r"\s+", s.strip()))


def sach_ten(ten: str) -> str:
    """Tên hiển thị: bỏ ký tự điều khiển, gộp khoảng trắng (dữ liệu không tin)."""
    s = "".join(c for c in str(ten or "") if c.isprintable())
    return " ".join(s.split())


def kiem_ten(ten: str) -> str | None:
    """Tên đã làm sạch, hoặc None khi không hợp lệ (rỗng / > 60 ký tự)."""
    s = sach_ten(ten)
    if not s or len(s) > 60:
        return None
    return s


def chat_luong_ok(net: float) -> bool:
    """Mặt có dùng được không — thước độ nét (port `khong_phai_mat` phần nét).

    Phần độ lớn vector của c2a (`chuan_toi_thieu`) thuộc BỘ model: bộ mặc định
    buffalo_s để 0 (không lọc), nên tại đây chỉ áp thước độ nét.
    """
    return float(net) >= NET_TOI_THIEU


def tinh_tran_khac(mau: list[dict]) -> float | None:
    """Độ giống cao nhất giữa hai mẫu của HAI NGƯỜI KHÁC NHAU (thang 0–100).

    `mau`: [{"nguoi_id": ..., "vec": tuple512}] — port phần `tran_khac` của
    `so_mat_nha._nap_bang`.
    """
    if len({m["nguoi_id"] for m in mau}) < 2:
        return None
    tran = 0.0
    for i in range(len(mau)):
        for j in range(i + 1, len(mau)):
            if mau[i]["nguoi_id"] != mau[j]["nguoi_id"]:
                tran = max(tran, do_giong(mau[i]["vec"], mau[j]["vec"]))
    return tran


def nguong_hieu_luc(co_the_cfg: float, chac_cfg: float,
                    tran_khac: float | None) -> tuple[float, float]:
    """``(có thể là, chắc chắn)`` dùng thật — port `so_mat_nha.nguong_hieu_luc`."""
    co_the = float(co_the_cfg)
    chac = float(chac_cfg)
    if tran_khac is not None:
        co_the = min(co_the, max(_SAN_CO_THE, tran_khac + _BIEN_AN_TOAN))
    return co_the, max(co_the, chac)


def khop(mau: list[dict], vector: tuple, co_the: float, chac: float) -> dict:
    """So một vector với sổ mẫu. Trả {nguoi_id, ten, do_giong, loai}.

    `loai`: `quen` (≥ ngưỡng chắc), `co_the` (≥ ngưỡng có thể), `la`. Người lạ
    vẫn trả độ giống của người giống nhất để hiển thị — port `so_mat_nha.khop`.
    """
    if not mau:
        return {"nguoi_id": None, "ten": None, "do_giong": 0.0, "loai": "la"}
    tot = None
    for m in mau:
        d = do_giong(vector, m["vec"])
        if tot is None or d > tot[0]:
            tot = (d, m)
    d = float(tot[0])
    if d < co_the:
        return {"nguoi_id": None, "ten": None, "do_giong": round(d, 1), "loai": "la"}
    return {"nguoi_id": tot[1]["nguoi_id"], "ten": tot[1]["ten"],
            "do_giong": round(d, 1),
            "loai": "quen" if d >= chac else "co_the"}
