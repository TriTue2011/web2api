"""Che bí mật trong LOG HỆ THỐNG (stderr) ở gốc — NV13.

Sổ `nhat_ky.ghi` đã che bằng `che_chuoi`, nhưng các dòng `log.warning/exception(... %s, e)` rải khắp
`app/` ghi NGUYÊN lời nhắn lỗi; lỗi httpx hay chứa URL có token (vd `.../bot.../getUpdates`) ⇒ token
nằm nguyên trong stderr.

Nguyên tắc: MỘT bộ lọc ở gốc, dùng CHUNG `che_chuoi` (app/bien_loi.py) — không viết bộ che thứ hai,
không sửa từng dòng log. Lọc áp SAU khi định dạng (`getMessage()`), và cả `exc_info`/traceback.

LƯU Ý: Filter phải gắn vào HANDLER (không phải Logger) — Python chỉ kiểm filter của logger tạo
record, không kiểm filter của logger cha khi lan truyền. Gắn vào handler gốc thì mọi logger con
đều được lọc.

Đăng ký: `cai_dat()` (gọi một lần ở app/main.py) bọc nhà máy tạo bản ghi log của Python, nên MỌI bản ghi
đều được che bất kể handler nào in ra. Không gắn filter vào `logging.getLogger().handlers` vì dưới uvicorn
logger gốc KHÔNG có handler (dòng log rơi vào `lastResort`) — vòng lặp trên danh sách rỗng sẽ không che gì.
"""

from __future__ import annotations

import logging


def _che_chuoi(van_ban: str) -> str:
    """Lấy che_chuoi muộn để tránh import vòng (bien_loi ↔ nhat_ky)."""
    from app.bien_loi import che_chuoi
    return che_chuoi(van_ban)


def _che_doi_so(a):
    """Chuỗi và đối tượng lỗi (httpx… hay mang URL có token) được che; số/bool/None giữ nguyên để `%d` còn chạy."""
    if a is None or isinstance(a, (bool, int, float)):
        return a
    return _che_chuoi(a if isinstance(a, str) else str(a))


class CheBiMat(logging.Filter):
    """Filter che bí mật cho mọi log record đi qua logger được gắn.

    - Thông điệp và từng đối số của `%s` được che riêng (cấu trúc `args` được giữ).
    - Ngoại lệ (`exc_info`): thay giá trị ngoại lệ bằng bản có thông điệp đã che, giữ nguyên
      loại và traceback (vị trí mã vẫn đủ để tra, token không lọt).
    - Luôn trả True: chỉ che, không chặn dòng nào.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        # Che TỪNG phần, GIỮ cấu trúc `args`: formatter của uvicorn (access log) mở `record.args` thành bộ năm phần,
        # nên không được gộp thành một chuỗi rồi đặt args=None.
        try:
            if isinstance(record.msg, str):
                record.msg = _che_chuoi(record.msg)
            if isinstance(record.args, tuple):
                record.args = tuple(_che_doi_so(a) for a in record.args)
            elif isinstance(record.args, dict):
                record.args = {k: _che_doi_so(v) for k, v in record.args.items()}
        except Exception:
            pass
        if record.exc_info:
            exc_type, exc_val, exc_tb = record.exc_info
            if exc_val is not None:
                try:
                    masked = _che_chuoi(str(exc_val))
                except Exception:
                    masked = ""
                try:
                    new_exc = exc_type(masked)
                except Exception:
                    new_exc = Exception(masked)
                new_exc.__traceback__ = exc_tb
                # Giữ chuỗi nguyên nhân để tra, nhưng các thông điệp trong đó cũng đã qua che
                # ở lần ghi (không đệ quy sâu để tránh chậm đường nóng).
                record.exc_info = (exc_type, new_exc, exc_tb)
        return True


def cai_dat() -> None:
    """Che bí mật ở NƠI TẠO bản ghi (không phụ thuộc handler nào). Gọi nhiều lần vẫn chỉ bọc một lần."""
    cu = logging.getLogRecordFactory()
    if getattr(cu, "_che_bi_mat", False):
        return
    loc = CheBiMat()

    def nha_may(*args, **kwargs):
        record = cu(*args, **kwargs)
        loc.filter(record)
        return record

    nha_may._che_bi_mat = True
    logging.setLogRecordFactory(nha_may)
