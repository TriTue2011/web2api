"""YOLO26 — nhận LOẠI vật thể và TOẠ ĐỘ trong ảnh camera, chạy ONNX trên CPU.

Port gần nguyên văn từ c2a `services/yolo_nha.py` (commit d879b03, bản sao
~/workspace/c2a-nv146/services/yolo_nha.py). Chỉ đổi: gói `services.igpu` →
thử CUDA rồi CPU (onnxruntime), không sửa thuật toán letterbox/giải mã đầu ra
end-to-end (1, 300, 6).

Model KHÔNG nằm trong ảnh — `tai_model.py` tải về volume `/model` và kiểm
sha256 lấy từ trường ``digest`` GitHub của release (đọc 15/09/2026, lưu trong
hằng `MODELS`).

Đo của c2a 16/09/2026 (Xeon E5-2630L v4, 255 khung thật): yolo26n 79 ms/khung.
Giấy phép trọng số: AGPL-3.0 (Ultralytics).
"""
from __future__ import annotations

import ast
import hashlib
import struct
import threading
from dataclasses import dataclass
from pathlib import Path


def kiem_sha256_tep(tep: Path, ky_vong: str) -> bool:
    """Băm một tệp theo khối — dùng khi tải (tai_model) và KHI NẠP (N5)."""
    h = hashlib.sha256()
    with Path(tep).open("rb") as f:
        for khoi in iter(lambda: f.read(1 << 20), b""):
            h.update(khoi)
    return h.hexdigest() == ky_vong

PHAT_HANH = "https://github.com/ultralytics/assets/releases/download/v8.4.0/"


@dataclass(frozen=True)
class ModelYolo:
    ma: str
    tep: str
    sha256: str
    mb: float
    mo_ta: str


#: Băm lấy từ trường ``digest`` GitHub trả cho từng tệp của release (đọc
#: 15/09/2026) — tải xong mà lệch là tệp hỏng hoặc bị thay, không dùng.
MODELS: tuple[ModelYolo, ...] = (
    ModelYolo("yolo26n", "yolo26n.onnx",
              "2e947b787d9e787b93a16772a5f55b1d4d8c4d86f53146149c5d6a642442d6f7",
              9.9, "nhanh nhất — 79 ms/khung, bắt 96/255 khung; hợp để quét liên tục"),
    ModelYolo("yolo26s", "yolo26s.onnx",
              "d26b65c432111eb95798cd2320603d4d75627605dbec6c6b7f98c499a80e7321",
              38.3, "244 ms/khung, bắt 129/255 — chậm gấp ba, bắt thêm một phần ba"),
    ModelYolo("yolo26m", "yolo26m.onnx",
              "5631854916f5d8418169580cde05647f3a1483b21a5026567f122c1fedab973d",
              82.0, "742 ms/khung, bắt 146/255 — giỏi nhất trong năm bản, nên dùng khi hỏi từng ảnh"),
    ModelYolo("yolo26l", "yolo26l.onnx",
              "d32e8d2b3e5c7c591865d372712e42a88ff5bbc518f3bd83ad3621668de89f7a",
              99.6, "943 ms/khung, bắt 143/255 — CHẬM HƠN yolo26m mà bắt được ít hơn"),
    ModelYolo("yolo26x", "yolo26x.onnx",
              "88568299de91d4967f239a062c9f1619f695ebd05de73cd66b8f589591aaeb0a",
              223.3, "2043 ms/khung, bắt 147/255 — hơn yolo26m một khung, tốn gấp 2,75 lần"),
)
MAC_DINH = "yolo26n"
CANH = 640


def get(ma: str) -> ModelYolo | None:
    return next((m for m in MODELS if m.ma == ma), None)


#: Tên tiếng Việt cho 80 lớp COCO — khoá là tên tiếng Anh model nhúng sẵn
#: trong metadata ONNX (``names``), nên đổi thứ tự lớp cũng không lệch tên.
TEN_VIET: dict[str, str] = {
    "person": "người", "bicycle": "xe đạp", "car": "ô tô", "motorcycle": "xe máy",
    "airplane": "máy bay", "bus": "xe buýt", "train": "tàu hoả", "truck": "xe tải",
    "boat": "thuyền", "traffic light": "đèn giao thông", "fire hydrant": "trụ cứu hoả",
    "stop sign": "biển dừng", "parking meter": "cột thu phí đỗ xe", "bench": "ghế dài",
    "bird": "chim", "cat": "mèo", "dog": "chó", "horse": "ngựa", "sheep": "cừu",
    "cow": "bò", "elephant": "voi", "bear": "gấu", "zebra": "ngựa vằn",
    "giraffe": "hươu cao cổ", "backpack": "ba lô", "umbrella": "ô", "handbag": "túi xách",
    "tie": "cà vạt", "suitcase": "vali", "frisbee": "đĩa ném", "skis": "ván trượt tuyết",
    "snowboard": "ván trượt tuyết đơn", "sports ball": "quả bóng", "kite": "diều",
    "baseball bat": "gậy bóng chày", "baseball glove": "găng bóng chày",
    "skateboard": "ván trượt", "surfboard": "ván lướt sóng", "tennis racket": "vợt tennis",
    "bottle": "chai", "wine glass": "ly rượu", "cup": "cốc", "fork": "dĩa", "knife": "dao",
    "spoon": "thìa", "bowl": "bát", "banana": "chuối", "apple": "táo",
    "sandwich": "bánh mì kẹp", "orange": "cam", "broccoli": "súp lơ xanh",
    "carrot": "cà rốt", "hot dog": "xúc xích kẹp", "pizza": "pizza", "donut": "bánh donut",
    "cake": "bánh ngọt", "chair": "ghế", "couch": "sofa", "potted plant": "chậu cây",
    "bed": "giường", "dining table": "bàn ăn", "toilet": "bồn cầu", "tv": "tivi",
    "laptop": "laptop", "mouse": "chuột máy tính", "remote": "điều khiển",
    "keyboard": "bàn phím", "cell phone": "điện thoại", "microwave": "lò vi sóng",
    "oven": "lò nướng", "toaster": "máy nướng bánh mì", "sink": "bồn rửa",
    "refrigerator": "tủ lạnh", "book": "sách", "clock": "đồng hồ", "vase": "bình hoa",
    "scissors": "kéo", "teddy bear": "gấu bông", "hair drier": "máy sấy tóc",
    "toothbrush": "bàn chải đánh răng",
}
TEN_VIET["bowl"] = "bát"


@dataclass(frozen=True)
class VatThe:
    """Một vật thể. ``hop`` tính bằng pixel của ẢNH GỐC: ``(x1, y1, x2, y2)``."""

    nhan: str
    diem: float
    hop: tuple[int, int, int, int]

    @property
    def ten(self) -> str:
        return TEN_VIET.get(self.nhan, self.nhan)

    def as_dict(self, rong: int, cao: int) -> dict:
        x1, y1, x2, y2 = self.hop
        return {"nhan": self.nhan, "ten": self.ten, "diem": round(self.diem, 3),
                "hop": [x1, y1, x2, y2],
                "hop_ti_le": [round(x1 / rong, 4), round(y1 / cao, 4),
                              round(x2 / rong, 4), round(y2 / cao, 4)],
                "vi_tri": vi_tri(self.hop, rong, cao)}


def letterbox(anh, canh: int = CANH):
    """Thu ảnh BGR vào khung vuông, viền xám 114 — đúng cách Ultralytics.

    Trả ``(blob NCHW float32 0..1 RGB, tỉ lệ, lề trái, lề trên)``.
    """
    import cv2
    import numpy as np

    cao, rong = anh.shape[:2]
    ti_le = min(canh / cao, canh / rong)
    moi_r, moi_c = round(rong * ti_le), round(cao * ti_le)
    le_trai, le_tren = (canh - moi_r) // 2, (canh - moi_c) // 2
    khung = np.full((canh, canh, 3), 114, np.uint8)
    if (moi_r, moi_c) != (rong, cao):
        anh = cv2.resize(anh, (moi_r, moi_c), interpolation=cv2.INTER_LINEAR)
    khung[le_tren:le_tren + moi_c, le_trai:le_trai + moi_r] = anh
    blob = khung[:, :, ::-1].transpose(2, 0, 1)[None].astype(np.float32) / 255.0
    return np.ascontiguousarray(blob), ti_le, le_trai, le_tren


def giai_ma(dau_ra, ten_lop: dict[int, str], ti_le: float, le_trai: int, le_tren: int,
            rong: int, cao: int, nguong: float,
            chi_nhan: set[str] | None = None) -> list[VatThe]:
    """Đầu ra end-to-end ``(300, 6)`` → vật thể trong toạ độ ảnh gốc, điểm cao trước."""
    ra: list[VatThe] = []
    for x1, y1, x2, y2, diem, lop in dau_ra:
        if diem < nguong:
            continue
        nhan = ten_lop.get(int(lop), str(int(lop)))
        if chi_nhan and nhan not in chi_nhan:
            continue
        hop = (
            int(max(0, min(rong, round((x1 - le_trai) / ti_le)))),
            int(max(0, min(cao, round((y1 - le_tren) / ti_le)))),
            int(max(0, min(rong, round((x2 - le_trai) / ti_le)))),
            int(max(0, min(cao, round((y2 - le_tren) / ti_le)))),
        )
        if hop[2] <= hop[0] or hop[3] <= hop[1]:
            continue
        ra.append(VatThe(nhan, float(diem), hop))
    ra.sort(key=lambda v: -v.diem)
    return ra


_VI_TRI = (("góc trên bên trái", "phía trên", "góc trên bên phải"),
           ("bên trái", "giữa khung", "bên phải"),
           ("góc dưới bên trái", "phía dưới", "góc dưới bên phải"))


def vi_tri(hop: tuple[int, int, int, int], rong: int, cao: int) -> str:
    """Chỗ của tâm hộp trong lưới 3×3 — để bot nói bằng lời thay cho con số."""
    cx = (hop[0] + hop[2]) / 2 / max(rong, 1)
    cy = (hop[1] + hop[3]) / 2 / max(cao, 1)
    return _VI_TRI[min(2, int(cy * 3))][min(2, int(cx * 3))]


#: Trần điểm ảnh TRƯỚC khi giải mã (N5): PNG nén cực mạnh vài chục byte
#: cũng khai được hàng trăm triệu điểm ảnh — trần base64 (14 MB) không
#: chặn nổi bom giải nén. ~25 triệu điểm ≈ ảnh 5000×5000, dư cho camera nhà.
TRAN_DIEM_ANH = 25_000_000


def _kich_thuoc_anh(du_lieu: bytes) -> tuple[int, int] | None:
    """(rộng, cao) đọc từ TIÊU ĐỀ PNG/JPEG — không giải mã điểm ảnh.

    Không nhận dạng được khuôn thì trả None (để bước giải mã xử lý tiếp,
    vẫn còn lớp chặn sau giải mã trong `doc_anh`)."""
    if du_lieu[:8] == b"\x89PNG\r\n\x1a\n" and len(du_lieu) >= 24:
        rong, cao = struct.unpack(">II", du_lieu[16:24])
        return int(rong), int(cao)
    if du_lieu[:2] == b"\xff\xd8":  # JPEG: duyệt các đoạn tới SOF0/1/2
        i = 2
        while i + 9 < len(du_lieu):
            if du_lieu[i] != 0xFF:
                i += 1
                continue
            dau = du_lieu[i + 1]
            if dau in (0xC0, 0xC1, 0xC2):
                cao, rong = struct.unpack(">HH", du_lieu[i + 5:i + 9])
                return int(rong), int(cao)
            if dau in (0xD8, 0xD9) or 0xD0 <= dau <= 0xD7 or dau == 0x01:
                i += 2
                continue
            do_dai = int.from_bytes(du_lieu[i + 2:i + 4], "big")
            if do_dai < 2:
                return None
            i += 2 + do_dai
        return None
    return None


def doc_anh(du_lieu: bytes):
    """JPEG/PNG → mảng BGR. Không đọc được thì ném ``ValueError``."""
    kt = _kich_thuoc_anh(du_lieu)
    if kt is not None and kt[0] * kt[1] > TRAN_DIEM_ANH:
        raise ValueError(
            f"ảnh quá nhiều điểm ảnh ({kt[0]}x{kt[1]} > {TRAN_DIEM_ANH})")
    import cv2
    import numpy as np

    anh = cv2.imdecode(np.frombuffer(du_lieu, np.uint8), cv2.IMREAD_COLOR)
    if anh is None:
        raise ValueError("không đọc được ảnh")
    if anh.shape[0] * anh.shape[1] > TRAN_DIEM_ANH:
        # Khuôn không đọc được tiêu đề (WebP/BMP…) vẫn bị chặn sau giải mã.
        raise ValueError("ảnh quá nhiều điểm ảnh sau giải mã")
    return anh


class BoPhatHien:
    """Một phiên ONNX cho một model. Dùng chung giữa các luồng — có khoá."""

    def __init__(self, duong_model: Path, luong: int = 2) -> None:
        # Kiểm LẠI sha256 lúc nạp (N5): volume /model bị thay tệp sau khi
        # tai_model đã kiểm thì dịch vụ cũng không được nạp. Chỉ nhận đúng
        # các model đã ghim trong MODELS.
        mh = next((m for m in MODELS if m.tep == Path(duong_model).name), None)
        if mh is None:
            raise ValueError(f"model {Path(duong_model).name} không nằm trong "
                             "danh sách đã ghim sha256 (MODELS)")
        if not kiem_sha256_tep(Path(duong_model), mh.sha256):
            raise ValueError(f"lệch sha256 khi nạp model {mh.tep} — không dùng")
        import onnxruntime as ort

        tuy_chon = ort.SessionOptions()
        # Đặt số luồng TƯỜNG MINH: để mặc định thì onnxruntime chiếm mọi nhân
        # và cố ghim luồng — trong container bị chặn nên in lỗi
        # `pthread_setaffinity_np failed` mỗi lần nạp (đo c2a 15/09/2026).
        tuy_chon.intra_op_num_threads = max(1, int(luong))
        tuy_chon.inter_op_num_threads = 1
        try:
            _nha = ["CUDAExecutionProvider", "CPUExecutionProvider"]
            self._phien = ort.InferenceSession(str(duong_model), tuy_chon, providers=_nha)
        except Exception:
            self._phien = ort.InferenceSession(str(duong_model), tuy_chon,
                                               providers=["CPUExecutionProvider"])
        self._vao = self._phien.get_inputs()[0].name
        meta = self._phien.get_modelmeta().custom_metadata_map
        # `names` là repr của dict Python — literal_eval chỉ đọc hằng, không chạy mã.
        self.ten_lop: dict[int, str] = {int(k): str(v) for k, v in
                                        ast.literal_eval(meta.get("names") or "{}").items()}
        self.canh = CANH
        self._khoa = threading.Lock()

    def phat_hien(self, anh, nguong: float = 0.35,
                  chi_nhan: set[str] | None = None) -> list[VatThe]:
        """Ảnh BGR → danh sách vật thể, điểm cao trước."""
        cao, rong = anh.shape[:2]
        blob, ti_le, le_trai, le_tren = letterbox(anh, self.canh)
        with self._khoa:
            dau_ra = self._phien.run(None, {self._vao: blob})[0]
        return giai_ma(dau_ra[0], self.ten_lop, ti_le, le_trai, le_tren,
                       rong, cao, nguong, chi_nhan)
