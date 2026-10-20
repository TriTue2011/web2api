"""Dịch vụ chay-ma (NV171): chạy mã Python CHỈ trong container phụ dùng-một-lần cách ly.

Hợp đồng kiểm ở đây (theo spec NV171):
- Container dùng một lần, cách ly BẮT BUỘC: --network none, --read-only,
  tmpfs /tmp có trần, user không-root (65534), --cap-drop ALL,
  no-new-privileges, --cpus/--memory/--pids-limit, --pull never,
  không mount dữ liệu lõi, không docker.sock. Test ĐỌC LẠI cờ thực tế của
  lệnh mà dịch vụ sinh ra (qua docker GIẢ ghi argv), không chỉ đọc tài liệu.
- Hạng B, rủi ro nguy_hiem: cổng xác thực khoá dịch vụ riêng; mọi đường trừ
  /suc-khoe phải có Bearer; /suc-khoe không lộ trạng thái.
- Tệp đầu vào: tên kiểm cấu trúc, resolve() dưới thư mục tạm của lần chạy,
  trần số/kích thước, không đi theo symlink khi thu tệp ra.
- Môi trường container: whitelist tối thiểu — bí mật của tiến trình dịch vụ
  KHÔNG lọt vào env của docker client lẫn cờ --env của container.
- Test bằng docker GIẢ (ho_tro_test/docker_gia.py); chưa kiểm với Docker thật.
- Port từ c2a services/code_runner.py: boc_code_python, co_the_chay,
  kiem_tinh (soi tĩnh), chan_doan_loi, _RUNNER_BOOTSTRAP (rlimit CPU/RAM/
  FSIZE trong tiến trình con — test trực tiếp chạy bootstrap thật).

Docker thật không có trên máy test: mọi test chạy bằng docker_gia.
"""

from __future__ import annotations

import http.client
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

GOC = Path(__file__).resolve().parent
if str(GOC) not in sys.path:
    sys.path.insert(0, str(GOC))

import server  # noqa: E402  (chưa có server.py → test ĐỎ, đúng chu trình test-trước)

KHOA_GIA = "khoa-gia-chay-ma-0123456789abcdef"
KHOA_SAI = "khoa-sai-chay-ma-0123456789abcdef"


@pytest.fixture()
def may_chu(tmp_path, monkeypatch):
    """Dựng máy chủ thật trên 127.0.0.1 với docker GIẢ."""
    gia_bin = GOC / "ho_tro_test" / "docker_gia.py"
    gia_bin.chmod(0o755)
    monkeypatch.setenv("CHAYMA_DOCKER_BIN", str(gia_bin))
    monkeypatch.setenv("CHAYMA_KHOA", KHOA_GIA)
    monkeypatch.setenv("CHAYMA_THU_MUC_GOC", str(tmp_path / "lam_viec"))
    monkeypatch.setenv("CHAYMA_HOST", "127.0.0.1")
    monkeypatch.setenv("CHAYMA_PORT", "0")
    monkeypatch.setenv("CHAYMA_GIA_THU_MUC", str(tmp_path / "chup"))
    monkeypatch.setenv("CHAYMA_GIA_CHE_DO", "echo")
    srv, _ = server.tao_may_chu()
    cong = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{cong}", tmp_path, gia_bin
    srv.shutdown()
    t.join(timeout=5)


def _goi(co_so, duong_dan, than=None, khoa=KHOA_GIA, phuong_thuc="POST", tieu_de_them=None):
    cn = http.client.HTTPConnection("127.0.0.1", int(co_so.rsplit(":", 1)[1]), timeout=60)
    tieu_de = dict(tieu_de_them or {})
    if khoa is not None:
        tieu_de["Authorization"] = f"Bearer {khoa}"
    du_lieu = None
    if than is not None:
        if isinstance(than, (dict, list)):
            du_lieu = json.dumps(than).encode()
        else:
            du_lieu = than
        tieu_de["Content-Type"] = "application/json"
        tieu_de["Content-Length"] = str(len(du_lieu))
    cn.request(phuong_thuc, duong_dan, body=du_lieu, headers=tieu_de)
    tra = cn.getresponse()
    return tra.status, tra.read()


def _chay(co_so, than, khoa=KHOA_GIA):
    return _goi(co_so, "/chay", than, khoa=khoa)


def _doc_chup(tmp_path, ten):
    p = tmp_path / "chup" / ten
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


# ── Xác thực ──────────────────────────────────────────────────────────────

def test_suc_khoe_mo_khong_can_khoa_va_khong_lo_gi(may_chu):
    co_so, _, _ = may_chu
    ma, than = _goi(co_so, "/suc-khoe", phuong_thuc="GET", khoa=None)
    assert ma == 200
    assert json.loads(than) == {"ok": True}
    assert KHOA_GIA not in than.decode("utf-8")


@pytest.mark.parametrize("duong", ["/chay", "/kiem-tinh"])
def test_khong_khoa_thi_401(may_chu, duong):
    co_so, _, _ = may_chu
    ma, _ = _goi(co_so, duong, {"ma": "print(1)"}, khoa=None)
    assert ma == 401


@pytest.mark.parametrize("duong", ["/chay", "/kiem-tinh"])
def test_sai_khoa_thi_401(may_chu, duong):
    co_so, _, _ = may_chu
    ma, _ = _goi(co_so, duong, {"ma": "print(1)"}, khoa=KHOA_SAI)
    assert ma == 401


def test_khoa_khong_ascii_khong_lam_sap_may_chu(may_chu):
    """NV153d D2: so sánh khoá bằng bytes — header không ASCII trả 401, không 500."""
    co_so, _, _ = may_chu
    than = b'{"ma":"print(1)"}'
    cn = http.client.HTTPConnection("127.0.0.1", int(co_so.rsplit(":", 1)[1]), timeout=30)
    cn.request("POST", "/chay", body=than,
               headers={"Authorization": "Bearer khoa-é-gia",
                        "Content-Type": "application/json", "Content-Length": str(len(than))})
    tra = cn.getresponse()
    assert tra.status == 401, f"header không ASCII phải 401, nhận {tra.status}"
    tra.read()
    # Máy chủ vẫn sống sau đó.
    ma, _ = _goi(co_so, "/suc-khoe", phuong_thuc="GET", khoa=None)
    assert ma == 200


def test_khoa_yeu_tu_choi_khoi_dong(monkeypatch, tmp_path):
    monkeypatch.setenv("CHAYMA_KHOA", "ngan")
    monkeypatch.setenv("CHAYMA_DOCKER_BIN", "/bin/true")
    monkeypatch.setenv("CHAYMA_THU_MUC_GOC", str(tmp_path))
    with pytest.raises(RuntimeError):
        server.tao_may_chu()


def test_khoa_don_dieu_tu_choi_khoi_dong(monkeypatch, tmp_path):
    monkeypatch.setenv("CHAYMA_KHOA", "aaaaaaaaaaaaaaaaaaaaaaaaaa")
    monkeypatch.setenv("CHAYMA_DOCKER_BIN", "/bin/true")
    monkeypatch.setenv("CHAYMA_THU_MUC_GOC", str(tmp_path))
    with pytest.raises(RuntimeError):
        server.tao_may_chu()


def test_thieu_docker_tu_choi_khoi_dong(monkeypatch, tmp_path):
    monkeypatch.setenv("CHAYMA_KHOA", KHOA_GIA)
    monkeypatch.setenv("CHAYMA_DOCKER_BIN", "/khong/co/docker-gia")
    monkeypatch.setenv("CHAYMA_THU_MUC_GOC", str(tmp_path))
    with pytest.raises(RuntimeError):
        server.tao_may_chu()


# ── Chạy vui vẻ + kiểm cờ cách ly THẬT (đọc lại argv mà dịch vụ sinh) ─────

CO_CACH_LY_BAT_BUOC = [
    ("--rm", None),
    ("--network", "none"),
    ("--read-only", None),
    ("--cap-drop", "ALL"),
    ("--user", "65534:65534"),
    ("--security-opt", "no-new-privileges:true"),
    ("--pids-limit", None),
    ("--memory", None),
    ("--cpus", None),
    ("--pull", "never"),
]

CO_CAM = ["--privileged", "docker.sock", "/var/run", "--device", "--network=host"]


def _khop_co(argv, co, gia_tri):
    for i, muc in enumerate(argv):
        if muc != co:
            continue
        if gia_tri is None:
            return True
        if i + 1 < len(argv) and argv[i + 1] == gia_tri:
            return True
    return False


def test_chay_hanh_phuc_va_lenh_cach_ly_day_du(may_chu, monkeypatch):
    """Lệnh docker mà dịch vụ sinh phải mang đủ cờ cách ly — đọc lại từ argv
    mà docker_gia ghi được, không tin tài liệu."""
    co_so, tmp_path, _ = may_chu
    monkeypatch.setenv("BI_MAT_GIA_LOI", "bi-mat-tuyet-mat-gia-xyz")
    ma, than = _chay(co_so, {"ma": "print(1+1)"})
    assert ma == 200, than[:200]
    kq = json.loads(than)
    assert kq["ok"] is True and kq["da_chay"] is True
    assert "stdout-gia" in kq["stdout"]

    argv = _doc_chup(tmp_path, "argv.json")
    assert argv and argv[0] == "run", f"docker_gia ghi argv lạ: {argv[:4] if argv else None}"
    gop = "\n".join(argv)
    for co, gia_tri in CO_CACH_LY_BAT_BUOC:
        assert _khop_co(argv, co, gia_tri), f"thiếu cờ cách ly {co} {gia_tri or ''} trong {argv}"
    # tmpfs /tmp có trần kích thước
    tmpfs = [m for m in argv if m.startswith("/tmp")]
    assert tmpfs, "thiếu tmpfs /tmp"
    assert "size=" in tmpfs[0], f"tmpfs /tmp thiếu trần size: {tmpfs}"
    for xau in CO_CAM:
        assert xau not in gop, f"lệnh chứa thứ bị cấm {xau!r}"
    # Chỉ MỘT mount -v, đích là /lam-viec, nguồn là thư mục lần chạy trong THU_MUC_GOC
    mounts = [argv[i + 1] for i, m in enumerate(argv) if m == "-v" and i + 1 < len(argv)]
    assert len(mounts) == 1, f"số mount -v phải đúng 1, nhận {mounts}"
    nguon, dich, che_do = mounts[0].split(":")
    assert dich == "/lam-viec"
    assert nguon.startswith(str(tmp_path / "lam_viec")), "mount phải từ thư mục tạm của lần chạy"
    # Môi trường container: đúng whitelist, không thêm gì
    envs = {}
    for i, m in enumerate(argv):
        if m == "--env" and i + 1 < len(argv):
            k, _, v = argv[i + 1].partition("=")
            envs[k] = v
    assert set(envs) == {"PATH", "HOME", "LANG", "LC_ALL", "PYTHONIOENCODING", "PYTHONDONTWRITEBYTECODE"}, \
        f"env container lạ: {sorted(envs)}"
    # Bí mật của tiến trình dịch vụ KHÔNG lọt vào argv của container
    assert KHOA_GIA not in gop
    # Môi trường của tiến trình docker client cũng tối thiểu, không chứa khoá
    env_client = _doc_chup(tmp_path, "env.json") or {}
    assert KHOA_GIA not in json.dumps(env_client, ensure_ascii=False)
    assert "BI_MAT_GIA_LOI" not in env_client, "biến môi trường lõi lọt vào env của docker client"


def test_xay_dung_lenh_don_vi_kiem_du_co(may_chu):
    """Kiểm pure-function xay_dung_lenh (mục tiêu rẻ cho đột biến)."""
    co_so, tmp_path, _ = may_chu
    lenh = server.xay_dung_lenh("docker", "anh-gia@sha256:abc", "id123",
                                tmp_path / "lam_viec" / "id123",
                                han_giay=7, ram_mb=256, cpu="0.5", pids=32)
    for co, gia_tri in CO_CACH_LY_BAT_BUOC:
        assert _khop_co(lenh, co, gia_tri), f"thiếu {co} {gia_tri or ''}"
    gop = "\n".join(lenh)
    for xau in CO_CAM:
        assert xau not in gop
    assert _khop_co(lenh, "--pids-limit", "32")
    assert _khop_co(lenh, "--cpus", "0.5")
    assert _khop_co(lenh, "--memory", "256m")
    # --name dùng id chạy, không lọt ký tự lạ
    i = lenh.index("--name")
    assert lenh[i + 1] == "chayma-id123"
    # bootstrap + giới hạn đặt TRONG container, sau exec
    assert "python3" in lenh and "-I" in lenh and "-c" in lenh


# ── Cổng mã nguy hiểm (tĩnh, không bao giờ chạm docker) ───────────────────

MA_DOC_HAI = [
    ("import socket\ns = socket.socket()", "socket"),
    ("import os\nos.system('ls')", "os.system"),
    ("import os\nos.fork()", "os.fork"),
    ("print(open('/etc/passwd').read())", "open(\"/"),
    ("while True:\n    pass", "while true:"),
    ("x = input('nhap:')", "input("),
    ("import subprocess\nsubprocess.run(['ls'])", "subprocess"),
    ("__import__('os').system('id')", "__import__"),
]


@pytest.mark.parametrize("ma, dau_hieu", MA_DOC_HAI)
def test_cong_ma_doc_hai_bo_qua_khong_cham_docker(may_chu, tmp_path, ma, dau_hieu):
    co_so, _, _ = may_chu
    chup = tmp_path / "chup" / "argv.json"
    if chup.exists():
        chup.unlink()
    m, than = _chay(co_so, {"ma": ma})
    assert m == 200
    kq = json.loads(than)
    assert kq["da_chay"] is False, f"mã độc {dau_hieu!r} phải bị bỏ qua"
    assert kq["ly_do_bo_qua"], "phải có lý do bỏ qua"
    assert not chup.exists(), "mã độc đã chạm tới docker — phải chặn TRƯỚC khi gọi"


def test_ma_rong_va_ma_qua_dai_bi_tu_choi(may_chu):
    co_so, _, _ = may_chu
    m, than = _chay(co_so, {"ma": ""})
    assert json.loads(than)["da_chay"] is False
    m, than = _chay(co_so, {"ma": "x = 1\n" * (server.TRAN_MA // 6 + 1)})
    assert json.loads(than)["da_chay"] is False


def test_boc_code_tu_khoi_fence():
    tra_loi = "đây là code:\n```python\nprint(1)\n```\nvà tiếp:\n```\nx = 2\n```"
    ma = server.boc_code_python(tra_loi)
    assert "print(1)" in ma and "x = 2" in ma
    # Cổng c2a giữ nguyên: mã trần chỉ được nhận khi bắt đầu bằng def/class/
    # import; văn bản thường không phải code → "".
    assert server.boc_code_python("import os\nprint(1)") == "import os\nprint(1)"
    assert server.boc_code_python("chỉ là văn bản thường") == ""


# ── Soi tĩnh /kiem-tinh (không chạy) ───────────────────────────────────────

def test_kiem_tinh_bat_ten_chua_dinh_nghia(may_chu):
    co_so, _, _ = may_chu
    m, than = _goi(co_so, "/kiem-tinh", {"ma": "print(bien_chua_khai)\n"})
    kq = json.loads(than)
    assert m == 200 and kq["ok"] is True
    assert "bien_chua_khai" in kq["kiem_tinh"]


def test_kiem_tinh_bat_loi_cu_phap(may_chu):
    co_so, _, _ = may_chu
    m, than = _goi(co_so, "/kiem-tinh", {"ma": "def f(:\n  pass\n"})
    assert "CÚ PHÁP" in json.loads(than)["kiem_tinh"]


def test_kiem_tinh_sach_cho_ma_tot(may_chu):
    co_so, _, _ = may_chu
    m, than = _goi(co_so, "/kiem-tinh", {"ma": "def cong(a, b):\n    return a + b\n\nprint(cong(1, 2))\n"})
    kq = json.loads(than)
    # NV171-SUA C3: trường đổi tên «goi_y» — soi tĩnh chỉ là GỢI Ý SỚM,
    # không phải lời hứa an toàn; ranh giới an toàn là container cách ly.
    assert kq["kiem_tinh"] == "" and kq["goi_y"] is True


# ── Hết giờ / đầu ra tràn / lỗi chạy ────────────────────────────────────────

def test_het_gio_diet_nhom_tien_trinh_som(may_chu, monkeypatch):
    co_so, _, _ = may_chu
    monkeypatch.setenv("CHAYMA_GIA_CHE_DO", "ngu")
    bat_dau = time.monotonic()
    m, than = _chay(co_so, {"ma": "print(1)", "han_giay": 1})
    keo_dai = time.monotonic() - bat_dau
    kq = json.loads(than)
    assert kq["het_gio"] is True and kq["ok"] is False
    assert keo_dai < 20, f"hết giờ phải diệt sớm, đã kéo {keo_dai:.1f}s"


def test_dau_ra_tran_bi_cat_va_diet_som(may_chu, monkeypatch):
    co_so, _, _ = may_chu
    monkeypatch.setenv("CHAYMA_GIA_CHE_DO", "phun")
    bat_dau = time.monotonic()
    m, than = _chay(co_so, {"ma": "print(1)"})
    keo_dai = time.monotonic() - bat_dau
    kq = json.loads(than)
    assert kq["bi_cat"] is True
    assert len(kq["stdout"].encode("utf-8")) <= server.TRAN_DAU_RA_BYTE + 100
    assert "…[cat]" in kq["stdout"]
    assert keo_dai < 20, f"phải diệt sớm khi phun đầu ra, đã kéo {keo_dai:.1f}s"


def test_loi_chay_co_chan_doan(may_chu, monkeypatch):
    co_so, _, _ = may_chu
    monkeypatch.setenv("CHAYMA_GIA_CHE_DO", "loi")
    m, than = _chay(co_so, {"ma": "x = 1\nraise RuntimeError('loi gia')\n"})
    kq = json.loads(than)
    assert kq["ok"] is False and kq["ma_thoat"] == 3
    assert "RuntimeError" in kq["chan_doan"] and "dòng 2" in kq["chan_doan"]


# ── Tệp đầu vào / tệp ra ───────────────────────────────────────────────────

def test_tep_dau_vao_hop_le_vao_dung_thu_muc(may_chu):
    co_so, tmp_path, _ = may_chu
    m, than = _chay(co_so, {"ma": "print(open('du-lieu.txt').read())",
                            "tep_dau_vao": [{"ten": "du-lieu.txt", "noi_dung": "xin chao"}]})
    kq = json.loads(than)
    assert kq["ok"] is True
    chup = _doc_chup(tmp_path, "thu_muc_chay.json")
    assert chup and chup.get("du-lieu.txt", {}).get("noi_dung") == "xin chao"


@pytest.mark.parametrize("ten_xau", ["../thoat.txt", "/etc/passwd", "a/b.txt", "..\\thoat.txt",
                                     ".an.txt", "co dau cach.txt", "", "a" * 70 + ".txt"])
def test_tep_dau_vao_ten_xau_bi_tu_choi(may_chu, tmp_path, ten_xau):
    co_so, _, _ = may_chu
    chup = tmp_path / "chup" / "argv.json"
    if chup.exists():
        chup.unlink()
    m, _ = _chay(co_so, {"ma": "print(1)", "tep_dau_vao": [{"ten": ten_xau, "noi_dung": "x"}]})
    assert m == 400, f"tên tệp {ten_xau!r} phải bị từ chối"
    assert not chup.exists(), "tên xấu đã chạm tới docker"


def test_tep_dau_vao_gioi_han_so_luong_va_kich_thuoc(may_chu):
    co_so, _, _ = may_chu
    nhieu = [{"ten": f"f{i}.txt", "noi_dung": "x"} for i in range(server.TRAN_SO_TEP + 1)]
    m, _ = _chay(co_so, {"ma": "print(1)", "tep_dau_vao": nhieu})
    assert m == 400
    m, _ = _chay(co_so, {"ma": "print(1)",
                         "tep_dau_vao": [{"ten": "lon.txt", "noi_dung": "x" * (server.TRAN_TEP_BYTE + 1)}]})
    assert m == 400


def test_tep_ra_khong_di_theo_symlink(may_chu, monkeypatch):
    """Thoát hộp qua symlink: tệp ra là symlink ra ngoài thì BỎ QUA, không đọc."""
    co_so, _, _ = may_chu
    monkeypatch.setenv("CHAYMA_GIA_CHE_DO", "symlink")
    m, than = _chay(co_so, {"ma": "print(1)"})
    kq = json.loads(than)
    ten_ra = [t["ten"] for t in kq["tep_ra"]]
    assert "thoat.txt" not in ten_ra, "symlink ra ngoài đã bị đọc theo"
    assert "ket-qua.txt" in ten_ra
    assert "root" not in than.decode("utf-8", "replace"), "nội dung /etc/hostname lọt ra"


def test_tep_ra_gioi_han(may_chu):
    co_so, _, _ = may_chu
    m, than = _chay(co_so, {"ma": "print(1)"})
    kq = json.loads(than)
    tep = next(t for t in kq["tep_ra"] if t["ten"] == "ket-qua.txt")
    assert tep["noi_dung"] == "42\n"
    assert tep["co_byte"] == 3


# ── Bootstrap rlimit (chạy THẬT tiến trình python con, giới hạn nhỏ) ────────

def _chay_bootstrap(script: str, cpu: int, ram_byte: int, fsize: int, tmp_path) -> tuple:
    tep = tmp_path / "ma_bootstrap.py"
    tep.write_text(script, encoding="utf-8")
    p = subprocess.run(
        [sys.executable, "-I", "-c", server.BOOTSTRAP, str(tep), str(cpu), str(ram_byte), str(fsize)],
        capture_output=True, text=True, timeout=60,
    )
    return p.returncode, p.stdout, p.stderr


def test_bootstrap_chan_ram(tmp_path):
    rc, out, err = _chay_bootstrap("x = [0] * (10 ** 8)\nprint('khong-nen-thay')\n",
                                   10, 64 * 1024 * 1024, 64 * 1024 * 1024, tmp_path)
    assert rc != 0, "cấp phát vượt trần RAM phải thất bại"
    assert "khong-nen-thay" not in out


def test_bootstrap_cho_phep_ma_binh_thuong(tmp_path):
    rc, out, _ = _chay_bootstrap("print('bootstrap-ok')\n",
                                 10, 512 * 1024 * 1024, 64 * 1024 * 1024, tmp_path)
    assert rc == 0 and "bootstrap-ok" in out


def test_bootstrap_chan_cpu(tmp_path):
    bat_dau = time.monotonic()
    rc, _, _ = _chay_bootstrap("while True:\n    pass\n", 1, 512 * 1024 * 1024, 64 * 1024 * 1024, tmp_path)
    keo_dai = time.monotonic() - bat_dau
    assert rc != 0, "vòng lặp vô hạn phải bị giết bởi RLIMIT_CPU"
    assert keo_dai < 15, f"phải giết sớm, đã kéo {keo_dai:.1f}s"


# ── NV171-SUA C2: container mồ côi — MỌI đường thoát đều kill + rm ───────

def _doc_tat_ca_lenh(tmp_path):
    p = tmp_path / "chup" / "argv_all.jsonl"
    if not p.exists():
        return []
    return [json.loads(d) for d in p.read_text(encoding="utf-8").splitlines() if d.strip()]


def _ten_container_tu_run(argv_run):
    i = argv_run.index("--name")
    return argv_run[i + 1]


@pytest.mark.parametrize("che_do", ["echo", "loi", "ngu", "phun"])
def test_c2_moi_duong_thoat_deu_kill_va_rm_container(may_chu, monkeypatch, che_do):
    """C2: hết giờ, tràn đầu ra, lỗi chạy, chạy xong bình thường — đường nào
    cũng phải có `docker kill <tên>` và `docker rm … <tên>` SAU lệnh run,
    nếu không container thành mồ côi giữ 512MB/64 tiến trình."""
    co_so, tmp_path, _ = may_chu
    monkeypatch.setenv("CHAYMA_GIA_CHE_DO", che_do)
    m, than = _chay(co_so, {"ma": "print(1)", "han_giay": 1})
    assert m == 200, than[:200]
    cac_lenh = _doc_tat_ca_lenh(tmp_path)
    chay = [a for a in cac_lenh if a and a[0] == "run"]
    assert len(chay) == 1, f"phải đúng 1 lệnh run, nhận {cac_lenh}"
    ten = _ten_container_tu_run(chay[0])
    assert ten.startswith("chayma-")
    idx_run = cac_lenh.index(chay[0])
    sau = cac_lenh[idx_run + 1:]
    kills = [a for a in sau if a and a[0] == "kill" and ten in a]
    rms = [a for a in sau if a and a[0] == "rm" and ten in a]
    assert kills, f"chế độ {che_do}: thiếu docker kill {ten} sau run — {sau}"
    assert rms, f"chế độ {che_do}: thiếu docker rm {ten} sau run — {sau}"


def test_c2_lenh_co_log_driver_none_va_gio_tuong_trong_container(may_chu):
    """C2: --log-driver none (daemon không ghi đĩa host) + giờ TƯỜNG chạy
    TRONG container qua `timeout` (RLIMIT_CPU không tính thời gian ngủ)."""
    co_so, tmp_path, _ = may_chu
    lenh = server.xay_dung_lenh("docker", "anh-gia@sha256:abc", "idx",
                                tmp_path / "lam_viec" / "idx",
                                han_giay=7, ram_mb=256, cpu="0.5", pids=32)
    assert _khop_co(lenh, "--log-driver", "none"), \
        f"thiếu --log-driver none trong {lenh}"
    # Lệnh TRONG container (sau tên ảnh): timeout -s KILL 7 python3 …
    i_anh = lenh.index("anh-gia@sha256:abc")
    trong = lenh[i_anh + 1:]
    assert trong[0] == "timeout", f"lệnh trong container phải bắt đầu bằng timeout: {trong[:4]}"
    assert "7" in trong[:4], f"timeout phải mang hạn giây 7: {trong[:4]}"
    assert "python3" in trong


# ── NV171-SUA C4: thư mục lần chạy thuộc uid 65534, KHÔNG 777 ─────────────

def test_c4_thu_muc_chay_thuoc_uid_65534_va_quyen_chat(may_chu):
    """C4: thư mục lần chạy phải THUỘC uid 65534 (container ghi được tệp ra)
    với quyền chặt — bản cũ 0o755 thuộc uid dịch vụ nên «tệp ra» không bao
    giờ ghi được thật; cấm chữa bằng chmod 777 (mở đường đầy đĩa)."""
    if os.geteuid() != 0:
        pytest.skip("đổi chủ thư mục cần quyền root (môi trường triển khai chạy root/65534)")
    co_so, tmp_path, _ = may_chu
    m, than = _chay(co_so, {"ma": "print(1)"})
    assert m == 200, than[:200]
    chup = _doc_chup(tmp_path, "thu_muc_chay.json")
    assert chup is not None
    assert chup["_mount_uid"] == 65534, \
        f"thư mục lần chạy phải thuộc uid 65534, nhận {chup['_mount_uid']}"
    mode = int(chup["_mount_mode"], 8)
    assert mode & 0o022 == 0 and mode & 0o005 == 0, \
        f"thư mục lần chạy không được cho nhóm/người lạ ghi-đọc bừa: {oct(mode)}"
    assert mode != 0o777


# ── NV171-SUA C5: xác thực TRƯỚC khi đọc thân; socket có hết giờ ─────────

def test_c5_401_tra_ngay_khong_doi_doc_than(may_chu):
    """C5: khoá sai → 401 NGAY, không đọc thân (bản cũ đọc ≤1MB rồi mới 401,
    trái docstring). Gửi header hứa thân 1MB nhưng KHÔNG gửi thân: bản cũ
    treo chờ thân, bản sửa trả 401 ngay và đóng kết nối."""
    import socket as socket_mod
    co_so, _, _ = may_chu
    cong = int(co_so.rsplit(":", 1)[1])
    s = socket_mod.create_connection(("127.0.0.1", cong), timeout=5)
    try:
        s.sendall(b"POST /chay HTTP/1.1\r\nHost: x\r\n"
                  b"Authorization: Bearer khoa-sai\r\n"
                  b"Content-Type: application/json\r\n"
                  b"Content-Length: 1000000\r\n\r\n")
        s.settimeout(5)
        tra = b""
        bat_dau = time.monotonic()
        while b"\r\n\r\n" not in tra:
            mieng = s.recv(4096)
            assert mieng, "máy chủ đóng kết nối mà không trả lời"
            tra += mieng
        assert time.monotonic() - bat_dau < 4, "401 phải trả ngay, không chờ thân"
        assert b" 401 " in tra.split(b"\r\n", 1)[0], tra[:100]
    finally:
        s.close()


def test_c5_socket_co_het_gio_va_co_tran_ket_noi(may_chu):
    """C5: handler có timeout (slowloris không giữ luồng vô hạn) và máy chủ
    có trần kết nối đồng thời — kiểm ở mức cấu trúc của lớp máy chủ."""
    assert server.XuLy.timeout and server.XuLy.timeout > 0, \
        "XuLy phải đặt timeout cho socket (BaseHTTPRequestHandler.timeout)"
    co_so, _, _ = may_chu
    # Máy chủ đang chạy trong fixture dùng lớp có trần kết nối:
    assert server.MayChuChayMa._TRAN_KET_NOI >= 1


def test_c5_ket_noi_treo_bi_cat_sau_het_gio_socket(tmp_path, monkeypatch):
    """C5: kết nối gửi dở request rồi treo phải bị cắt sau hết giờ socket
    (CHAYMA_HET_GIO_SOCKET=1 trong test này), không giữ luồng mãi."""
    import socket as socket_mod
    gia_bin = GOC / "ho_tro_test" / "docker_gia.py"
    gia_bin.chmod(0o755)
    monkeypatch.setenv("CHAYMA_DOCKER_BIN", str(gia_bin))
    monkeypatch.setenv("CHAYMA_KHOA", KHOA_GIA)
    monkeypatch.setenv("CHAYMA_THU_MUC_GOC", str(tmp_path / "lam_viec"))
    monkeypatch.setenv("CHAYMA_HOST", "127.0.0.1")
    monkeypatch.setenv("CHAYMA_PORT", "0")
    monkeypatch.setenv("CHAYMA_HET_GIO_SOCKET", "1")
    srv, _ = server.tao_may_chu()
    cong = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    t.start()
    try:
        s = socket_mod.create_connection(("127.0.0.1", cong), timeout=5)
        try:
            s.sendall(b"POST /chay HTTP/1.1\r\nHost: x\r\n")  # dở dang rồi treo
            s.settimeout(6)
            bat_dau = time.monotonic()
            dong = False
            try:
                while True:
                    mieng = s.recv(4096)
                    if not mieng:
                        dong = True
                        break
            except (ConnectionResetError, socket_mod.timeout, TimeoutError):
                dong = True
            assert dong, "kết nối treo không bị cắt"
            assert time.monotonic() - bat_dau < 6, "phải cắt sau ~1s hết giờ socket"
        finally:
            s.close()
    finally:
        srv.shutdown()
        t.join(timeout=5)


# ── NV171-SUA C6: đầu vào lạ → 400 sạch, không cắt kết nối ────────────────

def test_c6_han_giay_vo_han_va_nan_400_sach(may_chu):
    co_so, _, _ = may_chu
    for han in ("1e999", "NaN", "Infinity"):
        than = ('{"ma": "print(1)", "han_giay": ' + han + "}").encode()
        ma, _ = _goi(co_so, "/chay", than)
        assert ma == 400, f"han_giay={han} phải 400 sạch, nhận {ma} (bản cũ cắt kết nối)"
    ma, _ = _goi(co_so, "/suc-khoe", phuong_thuc="GET", khoa=None)
    assert ma == 200, "máy chủ phải còn sống sau đầu vào lạ"


def test_c6_ma_chua_surrogate_400_sach(may_chu):
    co_so, _, _ = may_chu
    than = json.dumps({"ma": "print('\ud800')"}).encode()
    ma, _ = _goi(co_so, "/chay", than)
    assert ma == 400, f"mã chứa surrogate phải 400 sạch, nhận {ma} (bản cũ cắt kết nối)"
    than = json.dumps({"ma": "print(1)",
                       "tep_dau_vao": [{"ten": "a.txt", "noi_dung": "\udfff"}]}).encode()
    ma, _ = _goi(co_so, "/chay", than)
    assert ma == 400, f"nội dung tệp chứa surrogate phải 400 sạch, nhận {ma}"
    ma, _ = _goi(co_so, "/suc-khoe", phuong_thuc="GET", khoa=None)
    assert ma == 200


# ── Rào khác ────────────────────────────────────────────────────────────────

def test_than_qua_lon_413(may_chu):
    co_so, _, _ = may_chu
    cn = http.client.HTTPConnection("127.0.0.1", int(co_so.rsplit(":", 1)[1]), timeout=30)
    du_lieu = b'{"ma":"' + b"x" * (server.TRAN_THAN + 100) + b'"}'
    cn.request("POST", "/chay", body=du_lieu,
               headers={"Authorization": f"Bearer {KHOA_GIA}",
                        "Content-Type": "application/json", "Content-Length": str(len(du_lieu))})
    assert cn.getresponse().status == 413


def test_ngon_ngu_khong_ho_tro_400(may_chu):
    co_so, _, _ = may_chu
    m, _ = _chay(co_so, {"ma": "console.log(1)", "ngon_ngu": "javascript"})
    assert m == 400


def test_tep_bi_mat_khong_hien_trong_log_khi_ghi(tmp_path, monkeypatch, caplog):
    """Môi trường chạy (log) không bao giờ chứa khoá dịch vụ."""
    import logging
    gia_bin = GOC / "ho_tro_test" / "docker_gia.py"
    monkeypatch.setenv("CHAYMA_DOCKER_BIN", str(gia_bin))
    monkeypatch.setenv("CHAYMA_KHOA", KHOA_GIA)
    monkeypatch.setenv("CHAYMA_THU_MUC_GOC", str(tmp_path / "lam_viec"))
    monkeypatch.setenv("CHAYMA_HOST", "127.0.0.1")
    monkeypatch.setenv("CHAYMA_PORT", "0")
    monkeypatch.setenv("CHAYMA_GIA_THU_MUC", str(tmp_path / "chup"))
    with caplog.at_level(logging.DEBUG, logger="dich_vu.chay_ma"):
        srv, _ = server.tao_may_chu()
    assert KHOA_GIA not in caplog.text
    srv.server_close()
