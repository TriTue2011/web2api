"""Test cầu nối Antigravity (cau_noi.py) với agy GIẢ (agy_gia.py) — không mạng, không tốn hạn mức.

Mỗi khẳng định về an toàn được CHỨNG MINH bằng nhật ký của agy giả (argv, tên biến môi trường, thư mục làm việc,
pid con/cháu) hoặc móc kiểm toán `open` của Python — không chỉ bằng mã trạng thái:
  xác thực bắt buộc · chuỗi người dùng chỉ vào đúng một tham số (kể cả nội dung mở đầu «-»/«--model») · id model lạ
  404 trước khi chạy gì · môi trường con không mang khoá cầu nối · không mở tệp nào dưới .gemini · che email ·
  /usage chịu dòng lạ/thiếu dòng · chọn theo hạn mức, xoay vòng, nghỉ tới mốc reset của cửa sổ CẠN · một yêu
  cầu/tài khoản · SSE đúng chuẩn · lỗi phân theo cấu trúc · hết giờ/khách ngắt giết cả con lẫn cháu · thư mục tạm
  dọn ở mọi đường · trần thân/prompt · không log prompt/HOME/email.
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient

GOC = Path(__file__).resolve().parent
if str(GOC) not in sys.path:
    sys.path.insert(0, str(GOC))

import cau_noi  # noqa: E402
from cau_noi import app, lay_be  # noqa: E402

KHOA = "sk-lan-0000000000000000000000000000xyzw"
AUTH = {"Authorization": f"Bearer {KHOA}"}
AGY_GIA = str(GOC / "agy_gia.py")

# Móc kiểm toán `open` (cả tiến trình, không gỡ được — nên chỉ ghi khi bật).
_MO: list[str] = []
_GHI_MO = [False]


def _moc_mo(su_kien, tham_so):
    if _GHI_MO[0] and su_kien == "open" and isinstance(tham_so[0], (str, bytes)):
        _MO.append(str(tham_so[0]))


sys.addaudithook(_moc_mo)


@pytest.fixture(autouse=True)
def moi_truong(tmp_path, monkeypatch):
    monkeypatch.setenv("AGY_CAU_NOI_KHOA", KHOA)
    monkeypatch.setenv("AGY_HAN_GIO_S", "20")
    monkeypatch.setenv("AGY_TTL_MODELS_S", "600")
    monkeypatch.setenv("AGY_TTL_USAGE_S", "600")
    monkeypatch.setenv("BI_MAT_KHAC_CUA_MAY", "khong-duoc-xuong-agy")
    monkeypatch.setattr(cau_noi, "_agy_bin", lambda: AGY_GIA)
    monkeypatch.setattr(cau_noi, "_dem_models", [])
    monkeypatch.setattr(cau_noi, "_dem_models_luc", 0.0)
    monkeypatch.setattr(cau_noi, "_khoa_models", asyncio.Lock())
    monkeypatch.setattr(cau_noi, "_models_doi_luc", None)
    monkeypatch.setattr(cau_noi, "_dem_phien_ban", None)
    monkeypatch.setattr(cau_noi, "_dem_phien_ban_luc", 0.0)
    monkeypatch.setattr(cau_noi, "_khoa_phien_ban", asyncio.Lock())
    monkeypatch.setattr(cau_noi, "be", None)
    tam = tmp_path / "tam"
    tam.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(tam))
    yield
    assert not list(tam.glob("agy_req_*")), "thư mục tạm của lượt agy còn sót"


def iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def bang(*dong: tuple[str, str, str, float]) -> str:
    return "\n".join(f"{h}\t{c}\t{p}\t{iso(t)}" for h, c, p, t in dong)


def tao_acc(tmp_path: Path, i: int, email: str | None = None, usage: str | None = None, **kw) -> Path:
    home = tmp_path / f"acc{i}"
    home.mkdir(parents=True, exist_ok=True)
    if usage is not None:
        kw["usage_table"] = usage
    (home / "_agy_gia.json").write_text(json.dumps(kw, ensure_ascii=False), encoding="utf-8")
    if email:
        (home / ".agy-cau-noi-email").write_text(email + "\n", encoding="utf-8")
    return home


def nhat_ky(home: Path) -> list[dict]:
    p = home / "_agy_gia_nhat_ky.jsonl"
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


def luot_chat(home: Path) -> list[dict]:
    """Các lần agy được gọi để CHAT (bỏ /usage, models và --version)."""
    return [m for m in nhat_ky(home) if "argv" in m and m["argv"][:1] not in (["models"], ["--version"]) and m["argv"][1:2] != ["/usage"]]


def con_song(pid: int) -> bool:
    try:
        trang_thai = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except OSError:
        return False
    return trang_thai != "Z"


def cho_den(dk, han: float = 10.0) -> bool:
    cuoi = time.monotonic() + han
    while time.monotonic() < cuoi:
        if dk():
            return True
        time.sleep(0.05)
    return False


def hoi(client, model="gemini-3.8-flash-high", noi_dung="alo", **than):
    return client.post("/v1/chat/completions", headers=AUTH,
                       json={"model": model, "messages": [{"role": "user", "content": noi_dung}], **than})


@contextmanager
def may_chu():
    sv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="off"))
    luong = threading.Thread(target=sv.run, daemon=True)
    luong.start()
    assert cho_den(lambda: sv.started)
    yield f"http://127.0.0.1:{sv.servers[0].sockets[0].getsockname()[1]}"
    sv.should_exit = True
    luong.join(10)


# ───────────── xác thực, health, models ─────────────


def test_xac_thuc_bat_buoc(tmp_path, monkeypatch):
    monkeypatch.setenv("AGY_HOMES", str(tao_acc(tmp_path, 0)))
    with TestClient(app) as c:
        for tieu_de in ({}, {"Authorization": "Bearer sai-khoa"}, {"Authorization": f"Basic {KHOA}"},
                        {"Authorization": "Bearer"}, {"Authorization": f"Bearer {KHOA}x"}):
            for duong in ("/v1/models", "/tai-khoan"):
                r = c.get(duong, headers=tieu_de)
                assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_api_key", (duong, tieu_de)
            assert c.post("/v1/chat/completions", headers=tieu_de, json={}).status_code == 401
        assert c.get("/v1/models", headers=AUTH).status_code == 200
    # Khoá chưa đặt → đóng cửa (500), không mở toang.
    monkeypatch.setenv("AGY_CAU_NOI_KHOA", "")
    with TestClient(app) as c:
        assert c.get("/v1/models", headers={"Authorization": "Bearer "}).status_code == 500
        assert c.get("/v1/models", headers=AUTH).json()["error"]["code"] == "server_misconfigured"


def test_health_va_models_bo_dong_la_va_hoi_tai_khoan_ke_khi_dau_hong(tmp_path, monkeypatch):
    h0 = tao_acc(tmp_path, 0, models_ma_thoat=3)
    h1 = tao_acc(tmp_path, 1, models=[{"id": "gemini-x", "nhan": "X"}, {"id": "-p", "nhan": "cờ giả"},
                                      {"id": "id có cách", "nhan": "y"}])
    monkeypatch.setenv("AGY_HOMES", f"{h0}:{h1}")
    with TestClient(app) as c:
        d_health = c.get("/health").json()
        assert d_health["status"] == "ok" and d_health["accounts"] == 2
        assert [m["id"] for m in c.get("/v1/models", headers=AUTH).json()["data"]] == ["gemini-x"]


# ───────────── /tai-khoan: che email, hạn mức, không mở tệp của agy ─────────────


def test_tai_khoan_che_email_va_han_muc_khong_mo_tep_cua_agy(tmp_path, monkeypatch):
    usage = ("Gemini Models\tWeekly Limit Remaining\t27%\t2026-10-19T04:15:23Z\n"
             "Gemini Models\tFive Hour Limit Remaining\t100%\t2026-10-12T09:42:59Z\n"
             "Claude and GPT models\tWeekly Limit Remaining\t100%\t2026-10-19T04:42:54Z")
    h0 = tao_acc(tmp_path, 0, "nguoidung@example.com", usage)
    (h0 / ".gemini" / "antigravity-cli").mkdir(parents=True)
    (h0 / ".gemini" / "antigravity-cli" / "user.json").write_text('{"email": "lo@example.com", "token": "TOKEN-GIA"}')
    monkeypatch.setenv("AGY_HOMES", str(h0))
    _MO.clear()
    _GHI_MO[0] = True
    try:
        with TestClient(app) as c:
            r = c.get("/tai-khoan", headers=AUTH)
            assert hoi(c).status_code == 200
    finally:
        _GHI_MO[0] = False
    assert not [p for p in _MO if ".gemini" in p], "cầu nối đã mở tệp trong .gemini (nơi agy giữ token)"
    acc = r.json()["tai_khoan"][0]
    assert acc["thu_tu"] == 0 and acc["email"] == "n…g@example.com" and acc["dang_ban"] is False
    assert acc["han_muc"]["Gemini Models"][0] == {"cua_so": "Weekly Limit Remaining", "phan_tram": 27,
                                                  "moc_reset": "2026-10-19T04:15:23Z"}
    assert str(h0) not in r.text and "TOKEN-GIA" not in r.text and "lo@" not in r.text


@pytest.mark.parametrize(("vao", "ra"), [
    ("nguoidung@example.com", "n…g@example.com"), ("ab@x.vn", "a…@x.vn"), ("a@x.vn", "a…@x.vn"),
    ("khong-co-a-cong", "…"), ("@x.vn", "…"), ("abc@", "…"), ("a@b@x.vn", "a…b@x.vn"),
])
def test_che_email(vao, ra):
    assert cau_noi._che_email(vao) == ra


def test_tai_khoan_khong_tep_email_thi_email_null(tmp_path, monkeypatch):
    monkeypatch.setenv("AGY_HOMES", str(tao_acc(tmp_path, 0)))
    with TestClient(app) as c:
        assert c.get("/tai-khoan", headers=AUTH).json()["tai_khoan"][0]["email"] is None


# ───────────── /usage: phân tích theo cấu trúc, chịu dòng lạ ─────────────


def test_phan_tich_usage_chiu_dong_la_va_thieu_dong():
    van_ban = ("Usage for this account:\n"                                   # tiêu đề tự do
               "Gemini Models\tWeekly Limit Remaining\t27%\t2026-10-19T04:15:23Z\r\n"
               "Gemini Models\tFive Hour Limit Remaining\t27.5%\t2026-10-12T09:42:59Z\n"  # % lẻ: bỏ
               "Gemini Models\tFive Hour Limit Remaining\tabc\t2026-10-12T09:42:59Z\n"    # không phải số: bỏ
               "Claude and GPT models\tWeekly Limit Remaining\t0%\tkhông-phải-giờ\n"      # mốc hỏng: giữ, moc_ts None
               "\t\t\t\n"
               "chỉ\thai cột\n"
               "Họ mới\tCửa sổ mới\t5%\t2026-10-20T00:00:00+07:00\tcột thừa\n")
    hm = cau_noi.phan_tich_usage(van_ban)
    assert set(hm) == {"Gemini Models", "Claude and GPT models", "Họ mới"}
    assert [w["phan_tram"] for w in hm["Gemini Models"]] == [27]             # thiếu «Five Hour» hợp lệ: vẫn chạy
    assert hm["Claude and GPT models"][0]["moc_ts"] is None
    assert hm["Họ mới"][0]["moc_ts"] == datetime(2026, 10, 19, 17, tzinfo=timezone.utc).timestamp()
    assert cau_noi.phan_tich_usage("") == {} and cau_noi.phan_tich_usage("\x00\t\x00") == {}


def test_usage_hong_thi_van_cho_thu_va_can_thieu_moc_thi_nghi_mot_gio(tmp_path, monkeypatch):
    h0 = tao_acc(tmp_path, 0, usage_ma_thoat=2)
    h1 = tao_acc(tmp_path, 1, usage="Gemini Models\tWeekly Limit Remaining\t0%\t\n")
    monkeypatch.setenv("AGY_HOMES", f"{h0}:{h1}")
    b = lay_be()
    asyncio.run(b.cap_nhat_tat_ca())
    assert b.tai_khoan[0].han_muc is None and b.tai_khoan[0].con("gemini-x") == 100
    assert b.tai_khoan[1].con("gemini-x") == 0
    assert 3590 <= b.tai_khoan[1].nghi_den("gemini-x") - time.time() <= 3600


# ───────────── chọn tài khoản ─────────────


def test_chon_tai_khoan_theo_han_muc_tung_ho(tmp_path, monkeypatch):
    t = time.time() + 86400
    h0 = tao_acc(tmp_path, 0, usage=bang(("Gemini Models", "Weekly", "20%", t), ("Claude and GPT models", "Weekly", "90%", t)))
    h1 = tao_acc(tmp_path, 1, usage=bang(("Gemini Models", "Weekly", "80%", t), ("Claude and GPT models", "Weekly", "30%", t)))
    monkeypatch.setenv("AGY_HOMES", f"{h0}:{h1}")
    b = lay_be()
    asyncio.run(b.cap_nhat_tat_ca())
    assert b.chon("gemini-3.8-flash-high")[0].thu_tu == 1
    assert b.chon("claude-sonnet-4-6")[0].thu_tu == 0
    assert b.chon("gpt-oss-120b-medium")[0].thu_tu == 0      # gpt-… thuộc họ «Claude and GPT models»
    # Họ «khác» (id không trùng từ nào của họ nào): theo cửa sổ CAO nhất bất kỳ → acc0 (90) và acc1 (80).
    assert b.chon("mau-la-1")[0].thu_tu == 0


def test_xoay_vong_khi_bang_han_muc(tmp_path, monkeypatch):
    u = bang(("Gemini Models", "Weekly", "50%", time.time() + 86400))
    homes = [tao_acc(tmp_path, i, usage=u) for i in range(3)]
    monkeypatch.setenv("AGY_HOMES", ":".join(map(str, homes)))
    b = lay_be()
    asyncio.run(b.cap_nhat_tat_ca())
    assert [b.chon("gemini-x")[0].thu_tu for _ in range(6)] == [0, 1, 2, 0, 1, 2]


def test_ho_khac_chi_can_mot_cua_so_con(tmp_path, monkeypatch):
    t = time.time() + 86400
    h0 = tao_acc(tmp_path, 0, usage=bang(("Gemini Models", "Weekly", "0%", t), ("Claude and GPT models", "Weekly", "0%", t)))
    h1 = tao_acc(tmp_path, 1, usage=bang(("Gemini Models", "Weekly", "0%", t), ("Claude and GPT models", "Weekly", "5%", t)))
    monkeypatch.setenv("AGY_HOMES", f"{h0}:{h1}")
    b = lay_be()
    asyncio.run(b.cap_nhat_tat_ca())
    assert b.chon("mau-la-1")[0].thu_tu == 1
    assert b.chon("gemini-x") == (None, "can")


# ───────────── cạn → 429 với Retry-After của cửa sổ CẠN; một tài khoản còn thì vẫn chạy ─────────────


def test_can_tra_429_retry_after_theo_cua_so_can_khong_theo_cua_so_som_nhat(tmp_path, monkeypatch):
    bay_gio = time.time()
    tuan, nam_gio = bay_gio + 3 * 86400, bay_gio + 3600
    u = bang(("Gemini Models", "Weekly Limit Remaining", "0%", tuan),
             ("Gemini Models", "Five Hour Limit Remaining", "100%", nam_gio))
    monkeypatch.setenv("AGY_HOMES", f"{tao_acc(tmp_path, 0, usage=u)}:{tao_acc(tmp_path, 1, usage=u)}")
    with TestClient(app) as c:
        r = hoi(c)
        assert r.status_code == 429 and r.json()["error"]["code"] == "rate_limit_exceeded"
        assert abs(int(r.headers["Retry-After"]) - (tuan - bay_gio)) < 30, r.headers["Retry-After"]
    assert not luot_chat(tmp_path / "acc0") and not luot_chat(tmp_path / "acc1")


def test_mot_tai_khoan_can_thi_dung_tai_khoan_kia(tmp_path, monkeypatch):
    t = time.time() + 86400
    h0 = tao_acc(tmp_path, 0, usage=bang(("Gemini Models", "Weekly", "0%", t)))
    h1 = tao_acc(tmp_path, 1, usage=bang(("Gemini Models", "Weekly", "3%", t)))
    monkeypatch.setenv("AGY_HOMES", f"{h0}:{h1}")
    with TestClient(app) as c:
        assert hoi(c).status_code == 200
    assert not luot_chat(h0) and len(luot_chat(h1)) == 1


def test_cua_so_can_qua_moc_reset_thi_doc_lai_usage_ngay(tmp_path, monkeypatch):
    h0 = tao_acc(tmp_path, 0, usage=bang(("Gemini Models", "Weekly", "0%", time.time() - 5)))
    monkeypatch.setenv("AGY_HOMES", str(h0))
    b = lay_be()
    asyncio.run(b.cap_nhat_tat_ca())
    (h0 / "_agy_gia.json").write_text(json.dumps({"usage_table": bang(("Gemini Models", "Weekly", "100%", time.time() + 9e4))}))
    asyncio.run(b.cap_nhat_tat_ca())   # TTL 600 s chưa hết nhưng mốc reset đã qua
    assert b.tai_khoan[0].con("gemini-x") == 100


def test_khong_co_tai_khoan_nao_khong_500(monkeypatch):
    monkeypatch.setenv("AGY_HOMES", "")
    with TestClient(app) as c:
        r = hoi(c)
    # Danh mục model rỗng → 404 trước; không bao giờ 500.
    assert r.status_code in (404, 503)


# ───────────── một yêu cầu / tài khoản ─────────────


def _song_song(url: str, n: int, **than) -> list[httpx.Response]:
    def mot(_):
        with httpx.Client(trust_env=False, timeout=30) as k:
            return k.post(f"{url}/v1/chat/completions", headers=AUTH,
                          json={"model": "gemini-3.8-flash-high", "messages": [{"role": "user", "content": "x"}], **than})
    with ThreadPoolExecutor(n) as ex:
        return list(ex.map(mot, range(n)))


def _khoang(home: Path) -> list[tuple[float, float]]:
    ds = nhat_ky(home)
    ket_thuc = {m["pid"]: m["ket_thuc"] for m in ds if "ket_thuc" in m}
    return sorted((m["bat_dau"], ket_thuc[m["pid"]]) for m in luot_chat(home))


def test_mot_yeu_cau_moi_tai_khoan_yeu_cau_sau_cho_toi_khi_ranh(tmp_path, monkeypatch):
    h0 = tao_acc(tmp_path, 0, tre=0.6)
    monkeypatch.setenv("AGY_HOMES", str(h0))
    with may_chu() as url:
        rs = _song_song(url, 3)
    assert [r.status_code for r in rs] == [200, 200, 200]
    k = _khoang(h0)
    assert len(k) == 3 and all(k[i][1] <= k[i + 1][0] for i in range(2)), f"hai lượt chồng nhau trên một HOME: {k}"


def test_tai_khoan_ban_het_qua_han_cho_thi_503_khong_429(tmp_path, monkeypatch):
    monkeypatch.setenv("AGY_CHO_TAI_KHOAN_S", "0")
    monkeypatch.setenv("AGY_HOMES", str(tao_acc(tmp_path, 0, tre=1.0)))
    with may_chu() as url:
        rs = _song_song(url, 2)
    ma = sorted(r.status_code for r in rs)
    assert ma == [200, 503], ma
    loi = next(r for r in rs if r.status_code == 503)
    assert loi.json()["error"]["code"] == "accounts_busy" and int(loi.headers["Retry-After"]) <= 10


def test_hai_tai_khoan_chay_song_song(tmp_path, monkeypatch):
    homes = [tao_acc(tmp_path, i, tre=0.6) for i in range(2)]
    monkeypatch.setenv("AGY_HOMES", ":".join(map(str, homes)))
    with may_chu() as url:
        assert [r.status_code for r in _song_song(url, 2)] == [200, 200]
    (a,), (b,) = _khoang(homes[0]), _khoang(homes[1])
    assert a[0] < b[1] and b[0] < a[1], "hai tài khoản rảnh phải được dùng cùng lúc"


def test_tran_dong_thoi_chung(tmp_path, monkeypatch):
    monkeypatch.setenv("AGY_TOI_DA_DONG_THOI", "1")
    homes = [tao_acc(tmp_path, i, tre=0.5) for i in range(2)]
    monkeypatch.setenv("AGY_HOMES", ":".join(map(str, homes)))
    with may_chu() as url:
        assert [r.status_code for r in _song_song(url, 2)] == [200, 200]
    k = sorted(_khoang(homes[0]) + _khoang(homes[1]))
    assert k[0][1] <= k[1][0], f"trần 1 mà hai lượt chạy cùng lúc: {k}"


def test_cho_trong_tran_chung_cung_co_han(tmp_path, monkeypatch):
    """Trần đầy thì yêu cầu không xếp hàng vô hạn: quá AGY_CHO_TAI_KHOAN_S → 503, agy không bị khởi động thêm."""
    monkeypatch.setenv("AGY_TOI_DA_DONG_THOI", "1")
    monkeypatch.setenv("AGY_CHO_TAI_KHOAN_S", "0")
    homes = [tao_acc(tmp_path, i, tre=1.0) for i in range(2)]
    monkeypatch.setenv("AGY_HOMES", ":".join(map(str, homes)))
    with may_chu() as url:
        rs = _song_song(url, 2)
    assert sorted(r.status_code for r in rs) == [200, 503]
    assert len(luot_chat(homes[0]) + luot_chat(homes[1])) == 1


# ───────────── chat: không stream, stream SSE ─────────────


def test_chat_khong_stream(tmp_path, monkeypatch):
    monkeypatch.setenv("AGY_HOMES", str(tao_acc(tmp_path, 0, response="Xin chào bạn!")))
    with TestClient(app) as c:
        r = c.post("/v1/chat/completions", headers=AUTH, json={"model": "gemini-3.8-flash-high", "messages": [
            {"role": "system", "content": "Bạn là trợ lý"},
            {"role": "user", "content": [{"type": "text", "text": "Hôm nay thế nào?"},
                                         {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]}]})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["object"] == "chat.completion" and j["choices"][0]["message"] == {"role": "assistant", "content": "Xin chào bạn!"}
    assert j["choices"][0]["finish_reason"] == "stop"
    assert j["usage"] == {"prompt_tokens": 13542, "completion_tokens": 1200, "total_tokens": 14742}
    assert r.headers["X-Cau-Noi-Bo-Khoi"] == "1"           # một khối ảnh bị bỏ — báo trong tiêu đề
    prompt = luot_chat(tmp_path / "acc0")[0]["argv"][1]
    assert prompt.startswith(cau_noi.LOI_DAN) and "[system]\nBạn là trợ lý" in prompt and "[user]\nHôm nay thế nào?" in prompt
    assert "base64" not in prompt


def _su_kien(r) -> list:
    ra = []
    for dong in r.iter_lines():
        if dong.startswith("data: "):
            ra.append("[DONE]" if dong == "data: [DONE]" else json.loads(dong[6:]))
    return ra


def test_chat_stream_sse_dung_chuan(tmp_path, monkeypatch):
    tra_loi = "Dòng một. Dòng hai, đủ dài để chia thành nhiều đoạn delta."
    monkeypatch.setenv("AGY_HOMES", str(tao_acc(tmp_path, 0, response=tra_loi)))
    with TestClient(app) as c:
        with c.stream("POST", "/v1/chat/completions", headers=AUTH, json={
                "model": "claude-sonnet-4-6", "stream": True, "stream_options": {"include_usage": True},
                "messages": [{"role": "user", "content": "Thơ"}]}) as r:
            assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
            sk = _su_kien(r)
    assert sk[-1] == "[DONE]"
    chunk = sk[:-1]
    assert all(x["object"] == "chat.completion.chunk" and x["id"] == chunk[0]["id"] for x in chunk)
    assert chunk[0]["choices"][0]["delta"]["role"] == "assistant"
    noi = [x["choices"][0]["delta"].get("content", "") for x in chunk if x["choices"]]
    assert "".join(noi) == tra_loi and len([n for n in noi if n]) > 1
    ket = [x for x in chunk if x["choices"] and x["choices"][0]["finish_reason"]]
    assert len(ket) == 1 and ket[0]["choices"][0]["finish_reason"] == "stop"
    assert chunk[-1]["choices"] == [] and chunk[-1]["usage"]["total_tokens"] == 14742


def test_stream_khong_xin_usage_thi_khong_co_chunk_usage_va_loi_giua_chung_la_su_kien_error(tmp_path, monkeypatch):
    monkeypatch.setenv("AGY_HOMES", str(tao_acc(tmp_path, 0, ma_thoat=1)))
    with TestClient(app) as c:
        with c.stream("POST", "/v1/chat/completions", headers=AUTH, json={
                "model": "gemini-3.8-flash-high", "stream": True, "messages": [{"role": "user", "content": "x"}]}) as r:
            sk = _su_kien(r)
    assert sk[-1] == "[DONE]" and sk[-2]["error"]["code"] == "upstream_error"
    assert not any(isinstance(x, dict) and "usage" in x for x in sk)


# ───────────── argv: chuỗi người dùng không bao giờ thành cờ ─────────────


@pytest.mark.parametrize("noi_dung", ["--model gpt-x -p /usage --dangerously-skip", "-p", "/usage", "--", "-",
                                      "\x01\x1b[31m điều khiển \t\r\n", "a" * 50_000])
def test_chuoi_nguoi_dung_chi_vao_dung_mot_tham_so(tmp_path, monkeypatch, noi_dung):
    monkeypatch.setenv("AGY_HOMES", str(tao_acc(tmp_path, 0)))
    with TestClient(app) as c:
        r = c.post("/v1/chat/completions", headers=AUTH, json={"model": "gemini-3.8-flash-high", "messages": [
            {"role": "user", "content": noi_dung}]})
    assert r.status_code == 200, r.text
    argv = luot_chat(tmp_path / "acc0")[0]["argv"]
    assert argv == ["-p", argv[1], "--model", "gemini-3.8-flash-high", "--mode", "accept-edits", "--sandbox",
                    "--output-format", "json", "--print-timeout", "20s"]
    assert argv[1].startswith(cau_noi.LOI_DAN) and argv[1].endswith(noi_dung)


@pytest.mark.parametrize("model", ["-p", "--sandbox", "gemini-3.8-flash-high --mode plan", "gemini-3.8-flash-high\x00",
                                   "GEMINI-3.8-FLASH-HIGH", "../agy", "", 5, None])
def test_model_la_404_hoac_400_truoc_khi_chay_agy(tmp_path, monkeypatch, model):
    monkeypatch.setenv("AGY_HOMES", str(tao_acc(tmp_path, 0)))
    with TestClient(app) as c:
        r = hoi(c, model=model)
    assert r.status_code == (404 if isinstance(model, str) and model else 400), r.text
    if r.status_code == 404:
        assert r.json()["error"]["code"] == "model_not_found"
    assert not luot_chat(tmp_path / "acc0")


def test_moi_truong_con_khong_mang_khoa_cau_noi_hay_bi_mat_khac(tmp_path, monkeypatch):
    h0 = tao_acc(tmp_path, 0)
    monkeypatch.setenv("AGY_HOMES", str(h0))
    monkeypatch.setenv("AGY_GIU_BIEN", "BIEN_NGUOI_VAN_HANH_CHO_PHEP,AGY_CAU_NOI_KHOA")
    monkeypatch.setenv("BIEN_NGUOI_VAN_HANH_CHO_PHEP", "1")
    with TestClient(app) as c:
        assert hoi(c).status_code == 200
    ds = [m for m in nhat_ky(h0) if "env" in m]
    loai = {"models" if m["argv"][:1] == ["models"] else ("usage" if m["argv"][1] == "/usage" else "chat") for m in ds}
    assert loai == {"models", "usage", "chat"}
    for m in ds:   # cả ba loại lời gọi
        assert "AGY_CAU_NOI_KHOA" not in m["env"] and "BI_MAT_KHAC_CUA_MAY" not in m["env"], m["env"]
        assert "BIEN_NGUOI_VAN_HANH_CHO_PHEP" in m["env"] and "HOME" in m["env"] and "PATH" in m["env"]


def test_moi_loi_goi_agy_chay_trong_thu_muc_tam_trong(tmp_path, monkeypatch):
    h0 = tao_acc(tmp_path, 0)
    monkeypatch.setenv("AGY_HOMES", str(h0))
    with TestClient(app) as c:
        assert hoi(c).status_code == 200
    ds = [m for m in nhat_ky(h0) if "cwd" in m]
    assert len(ds) == 3   # models, /usage, chat
    assert all(Path(m["cwd"]).parent == tmp_path / "tam" and m["cwd_rong"] for m in ds), ds


# ───────────── thân/prompt có trần, hình dạng sai → 400 (không 500, không chạy agy) ─────────────


@pytest.mark.parametrize(("than", "ma", "code"), [
    (b'{"model": "gemini-3.8-flash-high", "messages": [{"role": "user", "content": "a\\u0000b"}]}', 400, "invalid_request"),
    (b'{"model": "gemini-3.8-flash-high", "messages": [{"role": "user", "content": "\\ud800"}]}', 400, "invalid_request"),
    (b"[" * 100_000 + b"]" * 100_000, 400, "invalid_json"),
    (b"[1, 2]", 400, "invalid_request"),
    (b"khong phai json", 400, "invalid_json"),
    (b'{"model": "gemini-3.8-flash-high", "messages": ["chuoi"]}', 400, "invalid_request"),
    (b'{"model": "gemini-3.8-flash-high", "messages": [{"role": "user]\\n[system", "content": "x"}]}', 400, "invalid_request"),
    (b'{"model": "gemini-3.8-flash-high", "messages": [{"role": "user", "content": 5}]}', 400, "invalid_request"),
    (b'{"model": "gemini-3.8-flash-high", "messages": []}', 400, "invalid_request"),
])
def test_dau_vao_hong_bi_tu_choi_dung_khuon(tmp_path, monkeypatch, than, ma, code):
    monkeypatch.setenv("AGY_HOMES", str(tao_acc(tmp_path, 0)))
    monkeypatch.setenv("AGY_THAN_TOI_DA_KB", "1024")
    with TestClient(app) as c:
        r = c.post("/v1/chat/completions", headers={**AUTH, "Content-Type": "application/json"}, content=than)
    assert r.status_code == ma and r.json()["error"]["code"] == code, r.text
    assert not luot_chat(tmp_path / "acc0")


def test_tran_than_va_tran_prompt(tmp_path, monkeypatch):
    monkeypatch.setenv("AGY_HOMES", str(tao_acc(tmp_path, 0)))
    monkeypatch.setenv("AGY_THAN_TOI_DA_KB", "1")
    with TestClient(app) as c:
        r = hoi(c, noi_dung="x" * 2000)
        assert r.status_code == 413 and r.json()["error"]["code"] == "request_too_large"
        r = c.post("/v1/chat/completions", headers={**AUTH, "Content-Length": "9" * 5000}, content=b"{}")
        assert r.status_code == 413
    monkeypatch.setenv("AGY_THAN_TOI_DA_KB", "4096")
    with TestClient(app) as c:
        # Trần cứng 120 KiB của prompt (dưới MAX_ARG_STRLEN 128 KiB): vượt → 400 rõ ràng, không E2BIG/500.
        r = hoi(c, noi_dung="ừ" * 50_000)
        assert r.status_code == 400 and r.json()["error"]["code"] == "context_length_exceeded"
        monkeypatch.setenv("AGY_PROMPT_TOI_DA_KB", "999999")   # biến không nâng được trần cứng
        assert hoi(c, noi_dung="ừ" * 50_000).status_code == 400
    assert not luot_chat(tmp_path / "acc0")


# ───────────── phân loại lỗi theo cấu trúc ─────────────


@pytest.mark.parametrize(("kw", "ma", "code"), [
    ({"response": "", "denied_actions": [{"action": "command", "display_name": "RunCommand"}]}, 502, "agent_wanted_tool"),
    ({"ma_thoat": 1}, 502, "upstream_error"),
    ({"status": "ERROR"}, 502, "upstream_error"),
    ({"stdout_tho": "không phải json"}, 502, "upstream_error"),
    ({"stdout_tho": "[1]"}, 502, "upstream_error"),
    ({"response": 5}, 502, "upstream_error"),
])
def test_phan_loai_loi(tmp_path, monkeypatch, kw, ma, code):
    monkeypatch.setenv("AGY_HOMES", str(tao_acc(tmp_path, 0, **kw)))
    with TestClient(app) as c:
        r = hoi(c)
    assert r.status_code == ma and r.json()["error"]["code"] == code, r.text
    assert "agy lỗi giả" not in r.text and str(tmp_path) not in r.text   # stderr không lọt ra phản hồi


def test_bi_tu_choi_mot_cong_cu_nhung_van_co_cau_tra_loi_thi_200(tmp_path, monkeypatch):
    monkeypatch.setenv("AGY_HOMES", str(tao_acc(tmp_path, 0, response="Vẫn trả lời",
                                                denied_actions=[{"action": "command"}])))
    with TestClient(app) as c:
        r = hoi(c)
    assert r.status_code == 200 and r.json()["choices"][0]["message"]["content"] == "Vẫn trả lời"


# ───────────── hết giờ, khách ngắt: giết CON và CHÁU, dọn thư mục tạm ─────────────


def test_het_gio_504_giet_ca_con_lan_chau(tmp_path, monkeypatch):
    monkeypatch.setenv("AGY_HAN_GIO_S", "1")
    h0 = tao_acc(tmp_path, 0, treo=True)
    monkeypatch.setenv("AGY_HOMES", str(h0))
    with TestClient(app) as c:
        bat_dau = time.time()
        r = hoi(c)
        assert time.time() - bat_dau < 15
    assert r.status_code == 504 and r.json()["error"]["code"] == "timeout"
    m = luot_chat(h0)[0]
    assert cho_den(lambda: not con_song(m["pid"]) and not con_song(m["pid_chau"]), 5), "tiến trình con/cháu còn sống"


def test_agy_tu_het_gio_o_print_timeout_cung_la_504(tmp_path, monkeypatch):
    monkeypatch.setenv("AGY_HAN_GIO_S", "1")
    monkeypatch.setenv("AGY_HOMES", str(tao_acc(tmp_path, 0, tu_het_gio=True)))
    with TestClient(app) as c:
        r = hoi(c)
    assert r.status_code == 504 and r.json()["error"]["code"] == "timeout"


@pytest.mark.parametrize("stream", [False, True])
def test_khach_ngat_thi_giet_tien_trinh_va_tra_tai_khoan(tmp_path, monkeypatch, stream):
    h0 = tao_acc(tmp_path, 0, treo=True)
    monkeypatch.setenv("AGY_HOMES", str(h0))
    with may_chu() as url:
        cong = int(url.rsplit(":", 1)[1])
        than = json.dumps({"model": "gemini-3.8-flash-high", "stream": stream,
                           "messages": [{"role": "user", "content": "x"}]}).encode()
        s = socket.create_connection(("127.0.0.1", cong))
        s.sendall(b"POST /v1/chat/completions HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer " + KHOA.encode()
                  + b"\r\nContent-Type: application/json\r\nContent-Length: " + str(len(than)).encode() + b"\r\n\r\n" + than)
        assert cho_den(lambda: any("pid_chau" in m for m in luot_chat(h0))), "agy giả chưa chạy"
        s.close()
        m = luot_chat(h0)[0]
        assert cho_den(lambda: not con_song(m["pid"]) and not con_song(m["pid_chau"]), 8), "khách ngắt mà agy còn chạy"
        assert cho_den(lambda: not lay_be().tai_khoan[0].ban, 5), "tài khoản không được trả lại sau khi khách ngắt"


# ───────────── nhật ký: không prompt, không HOME, không email ─────────────


def test_khong_log_prompt_home_email(tmp_path, monkeypatch, caplog):
    h0 = tao_acc(tmp_path, 0, "nguoidung@example.com")
    h1 = tao_acc(tmp_path, 1, "khac@example.com", ma_thoat=1, usage_ma_thoat=1)
    monkeypatch.setenv("AGY_HOMES", f"{h0}:{h1}")
    bi_mat = "BI_MAT_NGUOI_DUNG_12345"
    with caplog.at_level(logging.DEBUG), TestClient(app) as c:
        for _ in range(3):
            hoi(c, noi_dung=bi_mat)
        c.get("/tai-khoan", headers=AUTH)
    assert "acc" in caplog.text
    for x in (bi_mat, str(h0), str(h1), "nguoidung@", "khac@", KHOA):
        assert x not in caplog.text, x


# ───────────── NV108: làm mới model & báo phiên bản ─────────────


def test_lam_moi_models_ep_doc_lai_va_cap_nhat_moi_khong_con(tmp_path, monkeypatch):
    h0 = tao_acc(tmp_path, 0, models=[
        {"id": "gemini-3.8-flash-high", "nhan": "Gemini 3.8"},
        {"id": "claude-sonnet-4-6", "nhan": "Claude Sonnet 4.6"},
    ])
    monkeypatch.setenv("AGY_HOMES", str(h0))
    with TestClient(app) as c:
        # Lần 1: nạp ban đầu qua GET /v1/models
        r1 = c.get("/v1/models", headers=AUTH)
        assert r1.status_code == 200
        assert [m["id"] for m in r1.json()["data"]] == ["gemini-3.8-flash-high", "claude-sonnet-4-6"]

        # Cập nhật danh sách model của agy giả: claude biến mất, gemini-3.9-pro mới xuất hiện
        (h0 / "_agy_gia.json").write_text(json.dumps({
            "models": [
                {"id": "gemini-3.8-flash-high", "nhan": "Gemini 3.8"},
                {"id": "gemini-3.9-pro", "nhan": "Gemini 3.9 Pro"},
            ]
        }, ensure_ascii=False), encoding="utf-8")

        # Nếu không gọi POST /v1/models/lam-moi thì GET /v1/models vẫn trả bản đệm cũ (TTL 600s)
        r_dem = c.get("/v1/models", headers=AUTH)
        assert [m["id"] for m in r_dem.json()["data"]] == ["gemini-3.8-flash-high", "claude-sonnet-4-6"]

        # Gọi POST /v1/models/lam-moi để ép làm mới
        r_lam_moi = c.post("/v1/models/lam-moi", headers=AUTH)
        assert r_lam_moi.status_code == 200
        body = r_lam_moi.json()
        assert body["ok"] is True
        assert body["so_model"] == 2
        assert body["moi"] == ["gemini-3.9-pro"]
        assert body["khong_con"] == ["claude-sonnet-4-6"]

        # Sau khi làm mới, GET /v1/models phải thấy danh mục mới
        r2 = c.get("/v1/models", headers=AUTH)
        assert [m["id"] for m in r2.json()["data"]] == ["gemini-3.8-flash-high", "gemini-3.9-pro"]


def test_lam_moi_models_xac_thuc_401(tmp_path, monkeypatch):
    h0 = tao_acc(tmp_path, 0)
    monkeypatch.setenv("AGY_HOMES", str(h0))
    with TestClient(app) as c:
        # Không có Authorization
        r_khong_khoa = c.post("/v1/models/lam-moi")
        assert r_khong_khoa.status_code == 401
        assert r_khong_khoa.json()["error"]["code"] == "invalid_api_key"

        # Sai khoá
        r_sai_khoa = c.post("/v1/models/lam-moi", headers={"Authorization": "Bearer sai-khoa"})
        assert r_sai_khoa.status_code == 401
        assert r_sai_khoa.json()["error"]["code"] == "invalid_api_key"


def test_lam_moi_models_dau_ra_ban_bi_loc_va_tran_500_model(tmp_path, monkeypatch):
    # Dòng chèn lệnh, ký tự điều khiển, cờ giả, id quá dài, trùng lặp
    models_ban = [
        {"id": "gemini-hop-le", "nhan": "Hợp lệ"},
        {"id": "; rm -rf /", "nhan": "Chèn lệnh"},
        {"id": "-model-co-gia", "nhan": "Cờ giả"},
        {"id": "id\x00nul", "nhan": "Ký tự NUL"},
        {"id": "a" * 150, "nhan": "Quá 100 ký tự"},
        {"id": "gemini-hop-le", "nhan": "Trùng lặp"},
    ]
    # Thêm 550 model hợp lệ để kiểm tra trần 500
    for i in range(550):
        models_ban.append({"id": f"model-extra-{i}", "nhan": f"Extra {i}"})

    h0 = tao_acc(tmp_path, 0, models=models_ban)
    monkeypatch.setenv("AGY_HOMES", str(h0))
    with TestClient(app) as c:
        r = c.post("/v1/models/lam-moi", headers=AUTH)
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        # Không vượt quá trần 500
        assert body["so_model"] <= 500
        assert len(body["moi"]) <= 500
        # Tất cả id trong moi đều khớp mẫu an toàn
        for mid in body["moi"]:
            assert cau_noi.MAU_ID_MODEL.fullmatch(mid)
            assert not mid.startswith("-")
            assert ";" not in mid
            assert "\x00" not in mid


def test_lam_moi_models_loi_hoac_het_gio_khong_lo_thong_tin(tmp_path, monkeypatch):
    # Lỗi thoát mã != 0
    h0 = tao_acc(tmp_path, 0, models_ma_thoat=2)
    monkeypatch.setenv("AGY_HOMES", str(h0))
    with TestClient(app) as c:
        r = c.post("/v1/models/lam-moi", headers=AUTH)
        assert r.status_code in (502, 504)
        err = r.json().get("error", {})
        assert err.get("code") in ("upstream_error", "timeout")
        # Không lộ đường dẫn hay thông tin nội bộ
        assert str(h0) not in err.get("message", "")


def test_health_bao_phien_ban_va_models_doi_luc(tmp_path, monkeypatch):
    h0 = tao_acc(tmp_path, 0, version="2.5.1-beta")
    monkeypatch.setenv("AGY_HOMES", str(h0))
    with TestClient(app) as c:
        # Trước khi gọi models, models_doi_luc là None
        r1 = c.get("/health")
        assert r1.status_code == 200
        d1 = r1.json()
        assert d1["status"] == "ok"
        assert d1["accounts"] == 1
        assert d1["agy_phien_ban"] == "2.5.1-beta"
        assert d1["models_doi_luc"] is None

        # Gọi GET /v1/models để nạp danh mục lần đầu
        c.get("/v1/models", headers=AUTH)
        r2 = c.get("/health")
        assert r2.status_code == 200
        d2 = r2.json()
        assert d2["models_doi_luc"] is not None
        # Định dạng ISO
        assert "T" in d2["models_doi_luc"]


def test_health_phien_ban_loi_va_het_gio_van_200(tmp_path, monkeypatch):
    # Khi agy --version bị lỗi hoặc đầu ra lạ (không khớp ^[0-9][0-9A-Za-z._+-]{0,31}$)
    h0 = tao_acc(tmp_path, 0, version_raw="invalid-version-not-starting-with-digit\n")
    monkeypatch.setenv("AGY_HOMES", str(h0))
    with TestClient(app) as c:
        r = c.get("/health")
        assert r.status_code == 200
        assert r.json()["agy_phien_ban"] is None

    # Khi agy --version thoát mã lỗi
    h1 = tao_acc(tmp_path, 1, version_ma_thoat=1)
    monkeypatch.setenv("AGY_HOMES", str(h1))
    monkeypatch.setattr(cau_noi, "_dem_phien_ban", None)
    monkeypatch.setattr(cau_noi, "_dem_phien_ban_luc", 0.0)
    with TestClient(app) as c:
        r = c.get("/health")
        assert r.status_code == 200
        assert r.json()["agy_phien_ban"] is None

