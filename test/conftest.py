"""Cho test của web2api import được gói `app` của RIÊNG web2api, và chặn mọi đường ra Internet thật.

Vì sao không đặt __init__.py ở thư mục này: repo gốc đã có gói `test` (517 file) và
thư viện chuẩn cũng có `test`; thêm __init__.py thì pytest đặt tên module thành
`test.…` và đụng tên. Không có nó, pytest chỉ thêm thư mục này vào sys.path.

Lưới chặn mạng: test chạy trên MÁY CHỦ THẬT, mà mặc định module gọi
generativelanguage.googleapis.com và vòng nền gọi api.github.com. Một test quên trỏ
về máy chủ giả sẽ tiêu hạn mức thật — nên mọi yêu cầu không phải 127.0.0.1 bị đẩy
vào proxy chết (cổng 9, discard) và hỏng ngay, còn máy chủ giả cục bộ đi thẳng nhờ NO_PROXY.
"""

import sys
from pathlib import Path

import pytest

GOC_WEB2API = Path(__file__).resolve().parents[1]
if str(GOC_WEB2API) not in sys.path:
    sys.path.insert(0, str(GOC_WEB2API))

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


