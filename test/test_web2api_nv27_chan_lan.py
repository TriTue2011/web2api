"""NV27 — test TĨNH cho scripts/chan-lan-trinh-duyet.sh.

Script chặn container trình duyệt từ xa chạm mạng LAN bằng luật iptables trong
chuỗi DOCKER-USER. Test không cần root, không chạm mạng/iptables thật: dựng lệnh
`docker` và `iptables` GIẢ trong thư mục tạm rồi đặt lên đầu PATH.

Các khẳng định:
- mọi dải phải chặn (10/8, 172.16/12, 192.168/16, 169.254/16, 100.64/10,
  127/8) đều có luật REJECT;
- ngoại lệ DNS (cổng 53, ACCEPT) đứng TRƯỚC mọi luật REJECT;
- mọi luật đều gắn nhãn comment `web2api-trinh-duyet`;
- chạy cài hai lần không sinh luật trùng (giả lập `iptables -C`);
- `--go` chỉ gỡ luật có nhãn, không đụng luật khác;
- thiếu quyền (iptables -L hỏng) → thoát 2 với thông báo rõ ràng;
- docker inspect hỏng → thoát 3 với thông báo rõ ràng.
"""

import os
import stat
import subprocess
from pathlib import Path

import pytest

GOC = Path(__file__).resolve().parents[1]
SCRIPT_MAC_DINH = GOC / "scripts" / "chan-lan-trinh-duyet.sh"

NHAN = "web2api-trinh-duyet"
DAI_PHAI_CHAN = [
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "169.254.0.0/16",
    "100.64.0.0/10",
    "127.0.0.0/8",
]
SUBNET_GIA = "172.21.0.0/16"
GATEWAY_GIA = "172.21.0.1"

DOCKER_GIA = """#!/usr/bin/env python3
# docker GIẢ: chỉ phục vụ `docker network inspect <ten> -f ...`
import os, sys
that_bai = os.environ.get("NV27_DOCKER_THAT_BAI") == "1"
if sys.argv[1:3] == ["network", "inspect"] and not that_bai:
    print("%s|%s|false")
    sys.exit(0)
sys.exit(1)
""" % (SUBNET_GIA, GATEWAY_GIA)

IPTABLES_GIA = """#!/usr/bin/env python3
# iptables/ip6tables GIẢ: giữ "luật" trong tệp trạng thái, ghi mọi lệnh vào nhật ký.
# Hỗ trợ: -L CHAIN -n (0, hoặc 1 nếu NV27_IPTABLES_CAM=1),
#          -C CHAIN <spec...> (0 nếu spec đã có, 1 nếu chưa),
#          -A CHAIN <spec...> (thêm nếu chưa có),
#          -D CHAIN <spec...> (xóa; 1 nếu không có),
#          -S CHAIN (in các dòng `-A CHAIN <spec>`).
import os, sys

trang_thai = os.environ["NV27_IPTABLES_STATE"]
nhat_ky = os.environ["NV27_IPTABLES_LOG"]
with open(nhat_ky, "a", encoding="utf-8") as f:
    f.write(" ".join(sys.argv[1:]) + "\\n")

def doc():
    try:
        with open(trang_thai, encoding="utf-8") as f:
            return [d.rstrip("\\n") for d in f if d.strip()]
    except FileNotFoundError:
        return []

def ghi(dong):
    with open(trang_thai, "w", encoding="utf-8") as f:
        for d in dong:
            f.write(d + "\\n")

lenh = sys.argv[1]
if lenh == "-L":
    sys.exit(1 if os.environ.get("NV27_IPTABLES_CAM") == "1" else 0)
if lenh == "-S":
    for d in doc():
        print("-A", sys.argv[2], d)
    sys.exit(0)
spec = " ".join(sys.argv[3:])
ds = doc()
if lenh == "-C":
    sys.exit(0 if spec in ds else 1)
if lenh == "-A":
    if spec not in ds:
        ds.append(spec)
        ghi(ds)
    sys.exit(0)
if lenh == "-D":
    if spec not in ds:
        sys.exit(1)
    ds.remove(spec)
    ghi(ds)
    sys.exit(0)
sys.exit(2)
"""


@pytest.fixture()
def moi_truong_gia(tmp_path, monkeypatch):
    """Dựng docker/iptables giả trong tmp, trả về dict môi trường chạy script."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "docker").write_text(DOCKER_GIA, encoding="utf-8")
    (bin_dir / "iptables").write_text(IPTABLES_GIA, encoding="utf-8")
    (bin_dir / "ip6tables").write_text(IPTABLES_GIA, encoding="utf-8")
    for ten in ("docker", "iptables", "ip6tables"):
        p = bin_dir / ten
        p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    trang_thai = tmp_path / "iptables.state"
    nhat_ky = tmp_path / "iptables.log"
    trang_thai.write_text("", encoding="utf-8")
    nhat_ky.write_text("", encoding="utf-8")
    env = dict(os.environ)
    env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
    env["NV27_IPTABLES_STATE"] = str(trang_thai)
    env["NV27_IPTABLES_LOG"] = str(nhat_ky)
    env.pop("NV27_DOCKER_THAT_BAI", None)
    env.pop("NV27_IPTABLES_CAM", None)
    script = os.environ.get("NV27_SCRIPT", str(SCRIPT_MAC_DINH))
    return {
        "env": env,
        "script": script,
        "trang_thai": trang_thai,
        "nhat_ky": nhat_ky,
        "bin_dir": bin_dir,
    }


def chay_script(moi_truong_gia, *tham_so):
    return subprocess.run(
        ["bash", moi_truong_gia["script"], *tham_so],
        env=moi_truong_gia["env"],
        capture_output=True,
        text=True,
        timeout=60,
    )


def luat_trong_trang_thai(moi_truong_gia):
    return [
        d for d in moi_truong_gia["trang_thai"].read_text(encoding="utf-8").splitlines()
        if d.strip()
    ]


def dong_nhat_ky(moi_truong_gia):
    return moi_truong_gia["nhat_ky"].read_text(encoding="utf-8").splitlines()


def test_dry_run_co_du_luat_reject_cho_moi_dai(moi_truong_gia):
    kq = chay_script(moi_truong_gia, "--dry-run")
    assert kq.returncode == 0, kq.stderr
    dong_luat = [d for d in kq.stdout.splitlines() if d and not d.startswith("#")]
    assert dong_luat, "dry-run phải in ra các lệnh"
    for dai in DAI_PHAI_CHAN:
        khop = [d for d in dong_luat if dai in d and "REJECT" in d]
        assert khop, f"thiếu luật REJECT cho dải {dai}"


def test_moi_luat_deu_gan_nhan(moi_truong_gia):
    kq = chay_script(moi_truong_gia, "--dry-run")
    assert kq.returncode == 0, kq.stderr
    dong_luat = [d for d in kq.stdout.splitlines() if d and not d.startswith("#")]
    assert dong_luat
    for d in dong_luat:
        assert NHAN in d, f"luật thiếu nhãn `{NHAN}`: {d}"


def test_ngoai_le_dns_dung_truoc_luat_reject(moi_truong_gia):
    kq = chay_script(moi_truong_gia, "--dry-run")
    assert kq.returncode == 0, kq.stderr
    dong_luat = [d for d in kq.stdout.splitlines() if d and not d.startswith("#")]
    vi_tri_dns = [i for i, d in enumerate(dong_luat) if "--dport 53" in d]
    vi_tri_reject = [i for i, d in enumerate(dong_luat) if "REJECT" in d]
    assert vi_tri_dns, "thiếu ngoại lệ DNS cổng 53"
    assert vi_tri_reject, "thiếu luật REJECT"
    assert max(vi_tri_dns) < min(vi_tri_reject), \
        "ngoại lệ DNS phải đứng TRƯỚC mọi luật REJECT"
    for i in vi_tri_dns:
        assert "-j ACCEPT" in dong_luat[i], f"ngoại lệ DNS phải ACCEPT: {dong_luat[i]}"
        assert GATEWAY_GIA in dong_luat[i], \
            f"ngoại lệ DNS phải nhắm tới gateway Docker {GATEWAY_GIA}: {dong_luat[i]}"


def test_chay_hai_lan_khong_nhan_doi_luat(moi_truong_gia):
    lan1 = chay_script(moi_truong_gia)
    assert lan1.returncode == 0, lan1.stderr
    so_luat_lan1 = len(luat_trong_trang_thai(moi_truong_gia))
    assert so_luat_lan1 > 0
    lan2 = chay_script(moi_truong_gia)
    assert lan2.returncode == 0, lan2.stderr
    ds = luat_trong_trang_thai(moi_truong_gia)
    assert len(ds) == so_luat_lan1, "chạy lần 2 sinh thêm luật trùng"
    assert len(set(ds)) == len(ds), "có luật trùng lặp trong trạng thái"
    # Lần 2 không được gọi -A thêm lần nào (mọi luật đã có, -C đều trúng).
    so_lan_append = sum(1 for d in dong_nhat_ky(moi_truong_gia) if d.startswith("-A "))
    assert so_lan_append == so_luat_lan1, \
        f"lần 2 vẫn gọi -A (tổng số lần -A trong nhật ký: {so_lan_append})"


def test_go_chi_go_luat_co_nhan(moi_truong_gia):
    cai = chay_script(moi_truong_gia)
    assert cai.returncode == 0, cai.stderr
    luat_la = "-s 9.9.9.9 -j RETURN"
    with moi_truong_gia["trang_thai"].open("a", encoding="utf-8") as f:
        f.write(luat_la + "\n")
    go = chay_script(moi_truong_gia, "--go")
    assert go.returncode == 0, go.stderr
    ds = luat_trong_trang_thai(moi_truong_gia)
    assert ds == [luat_la], f"--go đã đụng luật không có nhãn: {ds}"
    # Gỡ lần nữa vẫn êm (idempotent), luật lạ còn nguyên.
    go2 = chay_script(moi_truong_gia, "--go")
    assert go2.returncode == 0, go2.stderr
    assert luat_trong_trang_thai(moi_truong_gia) == [luat_la]


def test_kiem_bao_thieu_roi_bao_du(moi_truong_gia):
    truoc = chay_script(moi_truong_gia, "--kiem")
    assert truoc.returncode == 1, "--kiem phải thoát 1 khi thiếu luật"
    assert "THIEU" in truoc.stdout
    cai = chay_script(moi_truong_gia)
    assert cai.returncode == 0, cai.stderr
    sau = chay_script(moi_truong_gia, "--kiem")
    assert sau.returncode == 0, sau.stdout
    assert "THIEU" not in sau.stdout


def test_thieu_quyen_thi_loi_ro_rang(moi_truong_gia):
    moi_truong_gia["env"]["NV27_IPTABLES_CAM"] = "1"  # iptables -L hỏng = không có quyền
    kq = chay_script(moi_truong_gia)
    assert kq.returncode == 2, f"phải thoát 2 khi thiếu quyền, được {kq.returncode}"
    assert "root" in kq.stderr or "quyền" in kq.stderr, kq.stderr


def test_docker_hong_thi_loi_ro_rang(moi_truong_gia):
    moi_truong_gia["env"]["NV27_DOCKER_THAT_BAI"] = "1"
    kq = chay_script(moi_truong_gia, "--dry-run")
    assert kq.returncode == 3, f"phải thoát 3 khi docker hỏng, được {kq.returncode}"
    assert "web2api-trinh-duyet" in kq.stderr, kq.stderr
