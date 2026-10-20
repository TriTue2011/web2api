"""Cho test của web2api import được gói `app` của RIÊNG web2api, và chặn mọi đường ra Internet thật.

Vì sao không đặt __init__.py ở thư mục này: repo gốc đã có gói `test` (517 file) và
thư viện chuẩn cũng có `test`; thêm __init__.py thì pytest đặt tên module thành
`test.…` và đụng tên. Không có nó, pytest chỉ thêm thư mục này vào sys.path.

Lưới chặn mạng: test chạy trên MÁY CHỦ THẬT, mà mặc định module gọi
generativelanguage.googleapis.com và vòng nền gọi api.github.com. Một test quên trỏ
về máy chủ giả sẽ tiêu hạn mức thật — nên mọi yêu cầu không phải 127.0.0.1 bị đẩy
vào proxy chết (cổng 9, discard) và hỏng ngay, còn máy chủ giả cục bộ đi thẳng nhờ NO_PROXY.
"""

import asyncio
import logging
import os
import sys
import warnings
from pathlib import Path

import pytest

GOC_WEB2API = Path(__file__).resolve().parents[1]
if str(GOC_WEB2API) not in sys.path:
    sys.path.insert(0, str(GOC_WEB2API))

#: Các mảnh từ (khi tách id module theo dấu «-») trùng với từ thông dụng của tiếng Việt
#: hoặc chuẩn mở (như «assistant» là vai tin nhắn của chuẩn OpenAI, «phong» trong «phong cách», «giải phóng»,
#: «phat» trong «phát lại», «phát hiện» — gặp ở mã combo/kênh vào; «lich» trong «lịch sử» của lõi — không phải nhà cung cấp nào).
#: «open» là tiền tố của chuẩn mở OpenAI (LoiOpenAI, opened_at…), lộ ra khi cắt «api» khỏi nhãn «openapi» của mang_ra.
#: «router» là từ kỹ thuật (APIRouter của FastAPI) — lộ ra từ nhãn đầu «router» của host router.project-osrm.org trong mang_ra.
#: «hoa_don» (tiền tố module hoa-don) là cụm từ tiếng Việt thông dụng, trùng đoạn của «khoa_dong_bo» trong lõi khi dò theo chuỗi con.
#: Tên đầy đủ của module («home-assistant», «van-phong», «youtube-phat», «lich-ics») và tiền tố vẫn bị quét bình thường.
MANH_TU_THONG_DUNG = frozenset({"assistant", "chay", "dich", "email", "giao", "hoa_don", "khoa", "lich", "ma", "nhan", "open", "phat", "phong", "router", "song", "tien", "vien"})

#: Biến môi trường dùng chung cho test trong tiến trình VÀ tiến trình uvicorn con (test_web2api_kiem_nghiem).
MOI_TRUONG_CHAN_MANG = {
    "HTTP_PROXY": "http://127.0.0.1:9",
    "HTTPS_PROXY": "http://127.0.0.1:9",
    "ALL_PROXY": "http://127.0.0.1:9",
    "NO_PROXY": "127.0.0.1,localhost",
    # Cổng 9 trên loopback không ai nghe → gọi GitHub "thật" hỏng ngay thay vì ra mạng.
    "WEB2API_GITHUB_API": "http://127.0.0.1:9",
}


@pytest.fixture(autouse=True)
def chan_mang_that(monkeypatch):
    for k, v in MOI_TRUONG_CHAN_MANG.items():
        monkeypatch.setenv(k, v)
    for k in ("http_proxy", "https_proxy", "all_proxy", "no_proxy"):
        monkeypatch.delenv(k, raising=False)
    for k in ("WEB2API_GIT_REPO", "WEB2API_GIT_NHANH", "WEB2API_GIT_THU_MUC", "WEB2API_GIT_TOKEN",
              "WEB2API_ADMIN_KEY", "WEB2API_TEST_CHO_PHEP_LOOPBACK", "WEB2API_MAY_RAM_MB", "WEB2API_MAY_CPU",
              "TZ"):  # TZ của máy chạy test không được đổi kết quả: test cần múi giờ thì tự đặt
        monkeypatch.delenv(k, raising=False)


def cai_git_hai_luot(client, id_: str, headers: dict | None = None, duong_dan: str | None = None):
    """Cài module từ git qua API theo luồng xác nhận hai lượt NV61:
    Lượt 1: POST /api/nguon-git/cai/{id_} → 409 can_xac_nhan_cai_moi + ma_xac_nhan.
    Lượt 2: POST kèm {"xac_nhan": True, "ma_xac_nhan": ma} → 200.
    Nếu lượt 1 trả 200 (module đã cài, là cập nhật), trả về luôn kết quả lượt 1.
    """
    url = duong_dan or f"/api/nguon-git/cai/{id_}"
    r1 = client.post(url, headers=headers)
    if r1.status_code == 200:
        return r1
    assert r1.status_code == 409, f"Lượt 1 cài git thất bại ({r1.status_code}): {r1.text}"
    dl = r1.json()
    ma = dl.get("ma_xac_nhan")
    assert ma, f"Lượt 1 không trả về ma_xac_nhan: {r1.text}"
    return client.post(url, json={"xac_nhan": True, "ma_xac_nhan": ma}, headers=headers)


async def cai_ng_hai_luot(ng, id_: str, nid: str | None = None, **kwargs):
    """Cài module từ NguonGit theo luồng xác nhận hai lượt NV61:
    Lượt 1: nếu ném LoiCanXacNhanCaiMoi thì lấy ma_xac_nhan.
    Lượt 2: gọi lại kèm xac_nhan=True, ma_xac_nhan=ma.
    Nếu là cập nhật (không ném LoiCanXacNhanCaiMoi), trả kết quả bình thường.
    """
    from app.nguon_git import LoiCanXacNhanCaiMoi
    try:
        if nid is not None:
            return await ng.cai_tu_nguon(nid, id_, **kwargs)
        return await ng.cai(id_, **kwargs)
    except LoiCanXacNhanCaiMoi as e:
        if nid is not None:
            return await ng.cai_tu_nguon(nid, id_, xac_nhan=True, ma_xac_nhan=e.ma_xac_nhan, **kwargs)
        return await ng.cai(id_, xac_nhan=True, ma_xac_nhan=e.ma_xac_nhan, **kwargs)


# ── NV183: bộ test không được phụ thuộc thứ tự chạy ─────────────────────────
# Đo trên nền NEN179 cho thấy test đỏ theo thứ tự thuộc ba lớp trạng thái
# toàn cục rò rỉ giữa các test:
#  (a) Bộ lọc/handler logging: một test gọi POST /api/modules/nap-lai (vd
#      test_web2api_nv129_doc_viet_tat) khiến lõi nạp lại MỌI module; mỗi
#      module có `_cai_bo_loc_log()` gỡ bộ lọc của bản nạp trước trên các
#      logger HTTP (httpx/httpcore/...) và cài bộ lọc mới có sổ token RỖNG
#      ⇒ test token-khong-vao-log của facebook/telegram đỏ tuỳ thứ tự.
#  (b) Event loop của MainThread: test dùng `asyncio.run()` kết thúc bằng
#      `set_event_loop(None)` ⇒ test sau gọi `asyncio.get_event_loop()`
#      (vd test_web2api_nv165_tien_ich) ném RuntimeError «no current event
#      loop» ở mọi thứ tự chạy chung.
#  (c) Biến môi trường / cwd / sys.path / warnings: test đặt trực tiếp (không
#      qua monkeypatch) thì rò sang test sau.
# Fixture dưới đây chụp các lớp đó trước mỗi test và khôi phục sau mỗi test,
# nên trạng thái ở đầu mỗi test luôn bằng trạng thái ở đầu test trước — kết
# quả hết phụ thuộc thứ tự. `sys.modules` chỉ khôi phục mục bị XOÁ/THAY (giữ
# mục nạp mới, tránh ép nạp lại ngoài ý muốn).


def _tat_ca_logger():
    """Root + mọi logger thật trong manager (bỏ qua PlaceHolder)."""
    yield logging.getLogger()
    for lg in logging.Logger.manager.loggerDict.values():
        if isinstance(lg, logging.Logger):
            yield lg


def _bao_dam_event_loop():
    """MainThread phải luôn có một event loop dùng được sau mỗi test."""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = None
    if loop is None or loop.is_closed():
        asyncio.set_event_loop(asyncio.new_event_loop())


def _chup_trang_thai_toan_cuc():
    return {
        "loggers": {
            lg.name: (list(lg.filters), list(lg.handlers), lg.level,
                      lg.propagate, lg.disabled)
            for lg in _tat_ca_logger()
        },
        "env": dict(os.environ),
        "cwd": os.getcwd(),
        "sys_path": list(sys.path),
        "warnings": list(warnings.filters),
        "modules": dict(sys.modules),
    }


def _khoi_phuc_trang_thai_toan_cuc(ban_chup):
    for ten, (filters, handlers, level, propagate, disabled) in ban_chup["loggers"].items():
        lg = logging.getLogger(ten)
        lg.filters[:] = filters
        lg.handlers[:] = handlers
        lg.level = level
        lg.propagate = propagate
        lg.disabled = disabled
    for k in list(os.environ):
        if k not in ban_chup["env"]:
            del os.environ[k]
    os.environ.update(ban_chup["env"])
    try:
        os.chdir(ban_chup["cwd"])
    except OSError:
        pass
    sys.path[:] = ban_chup["sys_path"]
    warnings.filters[:] = ban_chup["warnings"]
    for k, v in ban_chup["modules"].items():
        if sys.modules.get(k) is not v:
            sys.modules[k] = v
    _bao_dam_event_loop()


@pytest.fixture(autouse=True)
def on_dinh_trang_thai_toan_cuc():
    """NV183: chụp/khôi phục trạng thái toàn cục quanh MỌI test.

    Khai báo SAU `chan_mang_that` để setup chạy sau (bản chụp đã gồm env do
    monkeypatch đặt) và teardown chạy trước (trả env về đúng bản chụp rồi
    monkeypatch mới khôi phục tiếp).
    """
    _bao_dam_event_loop()
    ban_chup = _chup_trang_thai_toan_cuc()
    yield
    _khoi_phuc_trang_thai_toan_cuc(ban_chup)


