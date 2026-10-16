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

NV30 — bổ sung: chặn cả đường container -> CHÍNH MÁY CHỦ bằng luật chuỗi INPUT
gắn theo giao diện cầu (-i <cầu>), không hard-code tên cầu (lấy từ
`docker network inspect`: Options "com.docker.network.bridge.name", nếu không có
thì br-<12 ký tự đầu của Id>). Các khẳng định thêm:
- có luật INPUT -i <cầu>: ESTABLISHED,RELATED ACCEPT đứng TRƯỚC REJECT bắt tất cả;
- ngoại lệ trong-mạng (lõi web2api nối vào cùng mạng) vẫn đứng trước mọi REJECT;
- cài hai lần không nhân đôi luật INPUT (giả lập `iptables -I` chèn đầu);
- `--go` gỡ cả luật INPUT có nhãn, không đụng luật khác;
- không có tên cầu trong Options → dùng br-<12 ký tự đầu của Id>;
- mạng bật IPv6 → có luật INPUT v6 tương ứng;
- đột biến xóa luật REJECT của INPUT → kiểm tra phát hiện (đỏ).
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
CAU_GIA = "br-gia-nv30"
ID_MANG_GIA = "a1b2c3d4e5f60718293a4b5c6d7e8f901234567890abcdef1234567890abcdef"
CAU_FALLBACK = "br-" + ID_MANG_GIA[:12]
SUBNET_V6_GIA = "fd00:dead:beef::/64"
GATEWAY_V6_GIA = "fd00:dead:beef::1"

DOCKER_GIA = """#!/usr/bin/env python3
# docker GIẢ: chỉ phục vụ `docker network inspect <ten> -f ...`
# In 7 trường: subnet|gateway|enableIPv6|id|bridge.name|subnet6|gateway6
# NV30_DOCKER_KHONG_CO_TEN_CAU=1  -> trường bridge.name rỗng (kiểm fallback)
# NV30_DOCKER_IPV6=1              -> bật IPv6, có subnet/gateway v6
import os, sys
that_bai = os.environ.get("NV27_DOCKER_THAT_BAI") == "1"
if sys.argv[1:3] == ["network", "inspect"] and not that_bai:
    khong_ten_cau = os.environ.get("NV30_DOCKER_KHONG_CO_TEN_CAU") == "1"
    ipv6 = os.environ.get("NV30_DOCKER_IPV6") == "1"
    print("|".join([
        "%(subnet)s", "%(gateway)s", "true" if ipv6 else "false",
        "%(id)s", "" if khong_ten_cau else "%(cau)s",
        "%(subnet6)s" if ipv6 else "<no value>",
        "%(gateway6)s" if ipv6 else "<no value>",
    ]))
    sys.exit(0)
sys.exit(1)
""" % {
    "subnet": SUBNET_GIA, "gateway": GATEWAY_GIA, "id": ID_MANG_GIA,
    "cau": CAU_GIA, "subnet6": SUBNET_V6_GIA, "gateway6": GATEWAY_V6_GIA,
}

IPTABLES_GIA = """#!/usr/bin/env python3
# iptables/ip6tables GIẢ: giữ "luật" trong tệp trạng thái, ghi mọi lệnh vào nhật ký.
# Hỗ trợ: -L CHAIN -n (0, hoặc 1 nếu NV27_IPTABLES_CAM=1),
#          -C CHAIN <spec...> (0 nếu spec đã có, 1 nếu chưa),
#          -A CHAIN <spec...> (thêm vào cuối nếu chưa có),
#          -I CHAIN <spec...> (chèn vào đầu nếu chưa có),
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
if lenh == "-I":
    if spec not in ds:
        ds.insert(0, spec)
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
    env.pop("NV30_DOCKER_KHONG_CO_TEN_CAU", None)
    env.pop("NV30_DOCKER_IPV6", None)
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


def dong_dry_run(kq):
    return [d for d in kq.stdout.splitlines() if d and not d.startswith("#")]


def dong_input(dong_luat):
    return [d for d in dong_luat if "INPUT" in d]


def dong_docker_user(dong_luat):
    return [d for d in dong_luat if "DOCKER-USER" in d]


# ---------- NV27: các test cũ, giữ nguyên hành vi ----------

def test_dry_run_co_du_luat_reject_cho_moi_dai(moi_truong_gia):
    kq = chay_script(moi_truong_gia, "--dry-run")
    assert kq.returncode == 0, kq.stderr
    dong_luat = dong_dry_run(kq)
    assert dong_luat, "dry-run phải in ra các lệnh"
    for dai in DAI_PHAI_CHAN:
        khop = [d for d in dong_docker_user(dong_luat) if dai in d and "REJECT" in d]
        assert khop, f"thiếu luật REJECT cho dải {dai}"


def test_moi_luat_deu_gan_nhan(moi_truong_gia):
    kq = chay_script(moi_truong_gia, "--dry-run")
    assert kq.returncode == 0, kq.stderr
    dong_luat = dong_dry_run(kq)
    assert dong_luat
    for d in dong_luat:
        assert NHAN in d, f"luật thiếu nhãn `{NHAN}`: {d}"


def test_ngoai_le_dns_dung_truoc_luat_reject(moi_truong_gia):
    kq = chay_script(moi_truong_gia, "--dry-run")
    assert kq.returncode == 0, kq.stderr
    dong_luat = dong_docker_user(dong_dry_run(kq))
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
    # Lần 2 không được gọi -A/-I thêm lần nào (mọi luật đã có, -C đều trúng).
    so_lan_them = sum(
        1 for d in dong_nhat_ky(moi_truong_gia)
        if d.startswith("-A ") or d.startswith("-I ")
    )
    assert so_lan_them == so_luat_lan1, \
        f"lần 2 vẫn gọi -A/-I (tổng số lần thêm trong nhật ký: {so_lan_them})"


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


# ---------- NV30: luật INPUT theo giao diện cầu ----------

def kiem_input_co_reject(dong_luat):
    """Helper dùng chung: chuỗi INPUT phải có luật REJECT gắn theo giao diện cầu."""
    khop = [d for d in dong_input(dong_luat) if "REJECT" in d]
    assert khop, "thiếu luật REJECT cho chuỗi INPUT"
    assert any("-i " in d for d in khop), "luật REJECT INPUT phải gắn theo giao diện cầu (-i)"


def test_input_luat_gan_theo_giao_dien_cau(moi_truong_gia):
    kq = chay_script(moi_truong_gia, "--dry-run")
    assert kq.returncode == 0, kq.stderr
    dong = dong_input(dong_dry_run(kq))
    assert dong, "dry-run phải in các lệnh cho chuỗi INPUT"
    for d in dong:
        assert f"-i {CAU_GIA}" in d, f"luật INPUT phải gắn theo cầu {CAU_GIA}: {d}"
        assert NHAN in d, f"luật INPUT thiếu nhãn `{NHAN}`: {d}"
        assert "-I INPUT" in d, f"luật INPUT phải cài bằng -I (đầu chuỗi): {d}"
    kiem_input_co_reject(dong_dry_run(kq))


def test_input_established_dung_truoc_reject(moi_truong_gia):
    kq = chay_script(moi_truong_gia, "--dry-run")
    assert kq.returncode == 0, kq.stderr
    dong = dong_input(dong_dry_run(kq))
    vi_tri_est = [i for i, d in enumerate(dong) if "ESTABLISHED,RELATED" in d]
    vi_tri_reject = [i for i, d in enumerate(dong) if "REJECT" in d]
    assert vi_tri_est, "thiếu luật ACCEPT ESTABLISHED,RELATED cho INPUT"
    assert vi_tri_reject, "thiếu luật REJECT cho INPUT"
    assert max(vi_tri_est) < min(vi_tri_reject), \
        "ESTABLISHED,RELATED phải đứng TRƯỚC REJECT trong chuỗi INPUT"
    for i in vi_tri_est:
        assert "-j ACCEPT" in dong[i]


def test_ngoai_le_trong_mang_van_dung_truoc(moi_truong_gia):
    # Lõi web2api nối vào cùng mạng -> lưu lượng trong cầu đi qua FORWARD,
    # được ngoại lệ "trong mạng" của DOCKER-USER chấp nhận trước mọi luật chặn.
    kq = chay_script(moi_truong_gia, "--dry-run")
    assert kq.returncode == 0, kq.stderr
    dong = dong_docker_user(dong_dry_run(kq))
    ngoai_le = [d for d in dong
                if f"-s {SUBNET_GIA}" in d and f"-d {SUBNET_GIA}" in d and "-j ACCEPT" in d]
    assert ngoai_le, "thiếu ngoại lệ trong-mạng cho DOCKER-USER"
    vi_tri_reject = [i for i, d in enumerate(dong) if "REJECT" in d]
    assert vi_tri_reject, "thiếu luật REJECT"
    assert dong.index(ngoai_le[0]) < min(vi_tri_reject), \
        "ngoại lệ trong-mạng phải đứng TRƯỚC mọi luật REJECT"


def test_input_idempotent_chen_dau_dung_thu_tu(moi_truong_gia):
    lan1 = chay_script(moi_truong_gia)
    assert lan1.returncode == 0, lan1.stderr
    ds = luat_trong_trang_thai(moi_truong_gia)
    luat_input = [d for d in ds if f"-i {CAU_GIA}" in d]
    assert len(luat_input) == 4, f"phải có 4 luật INPUT v4, được {len(luat_input)}"
    # Thứ tự trong trạng thái: -I chèn vào đầu theo thứ tự ngược -> cuối cùng
    # ESTABLISHED đứng đầu, REJECT đứng cuối trong nhóm INPUT.
    assert "ESTABLISHED,RELATED" in luat_input[0], luat_input
    assert "REJECT" in luat_input[-1], luat_input
    lan2 = chay_script(moi_truong_gia)
    assert lan2.returncode == 0, lan2.stderr
    ds2 = luat_trong_trang_thai(moi_truong_gia)
    assert len(ds2) == len(ds), "chạy lần 2 sinh thêm luật INPUT trùng"
    assert len(set(ds2)) == len(ds2)
    so_lan_chen = sum(1 for d in dong_nhat_ky(moi_truong_gia) if d.startswith("-I "))
    assert so_lan_chen == len(luat_input), \
        f"lần 2 vẫn gọi -I (tổng số lần -I trong nhật ký: {so_lan_chen})"


def test_go_go_ca_luat_input_co_nhan(moi_truong_gia):
    cai = chay_script(moi_truong_gia)
    assert cai.returncode == 0, cai.stderr
    assert any(f"-i {CAU_GIA}" in d for d in luat_trong_trang_thai(moi_truong_gia))
    luat_la = "-i eth0 -j ACCEPT"
    with moi_truong_gia["trang_thai"].open("a", encoding="utf-8") as f:
        f.write(luat_la + "\n")
    go = chay_script(moi_truong_gia, "--go")
    assert go.returncode == 0, go.stderr
    ds = luat_trong_trang_thai(moi_truong_gia)
    assert ds == [luat_la], f"--go chưa gỡ hết luật INPUT có nhãn hoặc đụng luật lạ: {ds}"


def test_ten_cau_fallback_br_id(moi_truong_gia):
    moi_truong_gia["env"]["NV30_DOCKER_KHONG_CO_TEN_CAU"] = "1"
    kq = chay_script(moi_truong_gia, "--dry-run")
    assert kq.returncode == 0, kq.stderr
    dong = dong_input(dong_dry_run(kq))
    assert dong, "thiếu luật INPUT khi không có tên cầu trong Options"
    for d in dong:
        assert f"-i {CAU_FALLBACK}" in d, \
            f"phải fallback sang br-<12 ký tự đầu của Id> ({CAU_FALLBACK}): {d}"


def test_ipv6_co_luat_input(moi_truong_gia):
    moi_truong_gia["env"]["NV30_DOCKER_IPV6"] = "1"
    kq = chay_script(moi_truong_gia, "--dry-run")
    assert kq.returncode == 0, kq.stderr
    dong = [d for d in dong_dry_run(kq) if d.startswith("ip6tables")]
    assert dong, "mạng bật IPv6 phải sinh luật ip6tables"
    dong_in6 = [d for d in dong if "INPUT" in d]
    assert dong_in6, "thiếu luật INPUT cho IPv6"
    for d in dong_in6:
        assert f"-i {CAU_GIA}" in d and NHAN in d, f"luật INPUT v6 thiếu cầu/nhãn: {d}"
    vi_tri_est = [i for i, d in enumerate(dong_in6) if "ESTABLISHED,RELATED" in d]
    vi_tri_reject = [i for i, d in enumerate(dong_in6) if "REJECT" in d]
    assert vi_tri_est and vi_tri_reject
    assert max(vi_tri_est) < min(vi_tri_reject), \
        "ESTABLISHED,RELATED v6 phải đứng TRƯỚC REJECT v6"


def test_dot_bien_xoa_reject_input_phai_do(moi_truong_gia, tmp_path):
    # Đột biến: xóa dòng sinh luật REJECT của INPUT (v4) khỏi bản sao script.
    # Kiểm tra phải PHÁT HIỆN (đỏ) — tức helper khẳng định phải raise AssertionError.
    ban_goc = Path(moi_truong_gia["script"]).read_text(encoding="utf-8")
    ban_dot_bien = tmp_path / "chan-lan-dot-bien.sh"
    giu = []
    trong_vao_v4 = False
    for d in ban_goc.splitlines(keepends=True):
        if "sinh_luat_vao_v4() {" in d:
            trong_vao_v4 = True
        elif trong_vao_v4 and d.startswith("}"):
            trong_vao_v4 = False
        if trong_vao_v4 and "-j REJECT" in d:
            continue  # đột biến: bỏ luật REJECT của INPUT
        giu.append(d)
    ban_dot_bien.write_text("".join(giu), encoding="utf-8")
    ban_dot_bien.chmod(ban_dot_bien.stat().st_mode | stat.S_IXUSR)
    moi_truong_gia["script"] = str(ban_dot_bien)
    kq = chay_script(moi_truong_gia, "--dry-run")
    assert kq.returncode == 0, kq.stderr  # script đột biến vẫn chạy êm
    with pytest.raises(AssertionError):
        kiem_input_co_reject(dong_dry_run(kq))
