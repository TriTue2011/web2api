"""NV30 — test TĨNH cho luật INPUT chặn container trình duyệt -> CHÍNH MÁY CHỦ.

Bổ sung NV27: NV27 chỉ đặt luật ở chuỗi DOCKER-USER (container -> mạng khác qua
FORWARD). Lưu lượng container -> CHÍNH MÁY CHỦ (cổng nghe 0.0.0.0, IP gateway)
đi qua chuỗi INPUT — script nay thêm luật `-i <cầu>` ở INPUT:
  1. ACCEPT ESTABLISHED,RELATED trước,
  2. ACCEPT DNS cổng 53 tới gateway,
  3. REJECT mọi còn lại từ cầu tới máy chủ.

Test không cần root, không chạm mạng/iptables thật: dựng `docker` và
`iptables` GIẢ trong thư mục tạm rồi đặt lên đầu PATH.

Các khẳng định:
- dry-run có luật INPUT gắn đúng tên cầu (mặc định br-<12 ký tự đầu của Id,
  hoặc Options "com.docker.network.bridge.name" khi có);
- luật INPUT dùng `-I` (chèn đầu), DOCKER-USER vẫn `-A`;
- ESTABLISHED đứng TRƯỚC DNS, DNS đứng TRƯỚC REJECT trong INPUT;
- mọi luật INPUT gắn nhãn và gắn đúng `-i <cầu>` (không chạm giao diện khác);
- tên cầu độc (chèn lệnh) hoặc Id rỗng -> thoát 3, không chạy iptables;
- idempotent: chạy hai lần không nhân đôi; --go gỡ cả luật INPUT có nhãn;
- --kiem bao thiếu luật INPUT;
- đột biến XÓA luật REJECT INPUT -> kiểm tra "có REJECT INPUT" ĐỎ;
- lưu lượng trong cầu (container<->container) không bị chặn: không spec INPUT
  nào khớp `-s SUBNET`, DOCKER-USER vẫn giữ ACCEPT nội mạng;
- IPv6 (khi mạng bật): có luật ip6tables INPUT tương ứng.
"""

import os
import stat
import subprocess
from pathlib import Path

import pytest

GOC = Path(__file__).resolve().parents[1]
SCRIPT_MAC_DINH = GOC / "scripts" / "chan-lan-trinh-duyet.sh"

NHAN = "web2api-trinh-duyet"
SUBNET_GIA = "172.21.0.0/16"
GATEWAY_GIA = "172.21.0.1"
ID_MANG_GIA = "abc123def456789012345678901234567890123456789012345678901234"
CAU_MAC_DINH_GIA = "br-abc123def456"
SUBNET_V6_GIA = "fd00:db8::/64"
GATEWAY_V6_GIA = "fd00:db8::1"

_DOCKER_GIA_MAU = """#!/usr/bin/env python3
# docker GIẢ cho NV30. Trả 7 trường đúng format của script:
# subnet|gateway|enableIPv6|id|ten_cau_options|subnet_v6|gateway_v6
# Điều khiển qua biến môi trường:
#   NV30_CAU_TUY_CHON : giá trị Options "com.docker.network.bridge.name"
#   NV30_CAU_DOC      : "1" -> tên cầu độc hại (kiểm tra chặn chèn lệnh)
#   NV30_ID_RONG      : "1" -> Id mạng rỗng
#   NV30_IPV6         : "1" -> bật IPv6 (kèm subnet/gateway v6)
import os, sys
if sys.argv[1:3] != ["network", "inspect"]:
    sys.exit(1)
if os.environ.get("NV30_DOCKER_THAT_BAI") == "1":
    sys.exit(1)
cau_tuy_chon = os.environ.get("NV30_CAU_TUY_CHON", "")
if os.environ.get("NV30_CAU_DOC") == "1":
    cau_tuy_chon = "br-x;reboot"
id_mang = "" if os.environ.get("NV30_ID_RONG") == "1" else "@@ID_MANG@@"
bat_v6 = os.environ.get("NV30_IPV6") == "1"
ipv6 = "true" if bat_v6 else "false"
subnet_v6 = "@@SUBNET_V6@@" if bat_v6 else ""
gateway_v6 = "@@GATEWAY_V6@@" if bat_v6 else ""
print("@@SUBNET@@|@@GATEWAY@@|" + ipv6 + "|" + id_mang + "|" + cau_tuy_chon
      + "|" + subnet_v6 + "|" + gateway_v6)
sys.exit(0)
"""
DOCKER_GIA = (_DOCKER_GIA_MAU
              .replace("@@SUBNET_V6@@", SUBNET_V6_GIA)
              .replace("@@GATEWAY_V6@@", GATEWAY_V6_GIA)
              .replace("@@ID_MANG@@", ID_MANG_GIA)
              .replace("@@SUBNET@@", SUBNET_GIA)
              .replace("@@GATEWAY@@", GATEWAY_GIA))

IPTABLES_GIA = """#!/usr/bin/env python3
# iptables/ip6tables GIẢ: giữ "luật" trong tệp trạng thái, ghi mọi lệnh vào nhật ký.
# Hỗ trợ: -L CHAIN -n, -C CHAIN <spec>, -A/-I CHAIN <spec>, -D CHAIN <spec>, -S CHAIN.
import os, sys

trang_thai = os.environ["NV30_IPTABLES_STATE"]
nhat_ky = os.environ["NV30_IPTABLES_LOG"]
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
    sys.exit(1 if os.environ.get("NV30_IPTABLES_CAM") == "1" else 0)
if lenh == "-S":
    for d in doc():
        print("-A", sys.argv[2], d)
    sys.exit(0)
spec = " ".join(sys.argv[3:])
ds = doc()
if lenh == "-C":
    sys.exit(0 if spec in ds else 1)
if lenh in ("-A", "-I"):
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
    env["NV30_IPTABLES_STATE"] = str(trang_thai)
    env["NV30_IPTABLES_LOG"] = str(nhat_ky)
    for bien in ("NV30_DOCKER_THAT_BAI", "NV30_IPTABLES_CAM", "NV30_CAU_TUY_CHON",
                 "NV30_CAU_DOC", "NV30_ID_RONG", "NV30_IPV6"):
        env.pop(bien, None)
    script = os.environ.get("NV30_SCRIPT", str(SCRIPT_MAC_DINH))
    return {
        "env": env,
        "script": script,
        "trang_thai": trang_thai,
        "nhat_ky": nhat_ky,
        "bin_dir": bin_dir,
        "tmp_path": tmp_path,
    }


def chay_script(moi_truong_gia, *tham_so, env=None):
    return subprocess.run(
        ["bash", moi_truong_gia["script"], *tham_so],
        env=env if env is not None else moi_truong_gia["env"],
        capture_output=True,
        text=True,
        timeout=60,
    )


def dong_dry_run(moi_truong_gia):
    kq = chay_script(moi_truong_gia, "--dry-run")
    assert kq.returncode == 0, kq.stderr
    return [d for d in kq.stdout.splitlines() if d and not d.startswith("#")]


def dong_input(dong):
    return [d for d in dong if " INPUT " in d]


def co_luat_reject_input(dong):
    return any("REJECT" in d for d in dong_input(dong))


def test_dry_run_co_luat_input_dung_cau_mac_dinh(moi_truong_gia):
    dong = dong_dry_run(moi_truong_gia)
    ds = dong_input(dong)
    assert ds, "dry-run phải có luật chuỗi INPUT"
    for d in ds:
        assert f"-i {CAU_MAC_DINH_GIA}" in d, f"luật INPUT sai tên cầu: {d}"


def test_cau_lay_tu_options_khi_co(moi_truong_gia):
    moi_truong_gia["env"]["NV30_CAU_TUY_CHON"] = "br-tuychon"
    dong = dong_dry_run(moi_truong_gia)
    ds = dong_input(dong)
    assert ds
    for d in ds:
        assert "-i br-tuychon" in d, f"phải dùng tên cầu từ Options: {d}"
    assert not any(CAU_MAC_DINH_GIA in d for d in ds)


def test_input_dung_i_chen_dau_docker_user_dung_a(moi_truong_gia):
    dong = dong_dry_run(moi_truong_gia)
    for d in dong_input(dong):
        assert d.startswith("iptables -I INPUT ") or d.startswith("ip6tables -I INPUT "), \
            f"luật INPUT phải chèn đầu bằng -I: {d}"
    for d in dong:
        if " DOCKER-USER " in d:
            assert " -A DOCKER-USER " in d, f"DOCKER-USER phải dùng -A: {d}"


def test_thu_tu_input_established_dns_reject(moi_truong_gia):
    dong = dong_dry_run(moi_truong_gia)
    ds = [d for d in dong_input(dong) if d.startswith("iptables ")]
    vi_tri_est = [i for i, d in enumerate(ds) if "ESTABLISHED,RELATED" in d]
    vi_tri_dns = [i for i, d in enumerate(ds) if "--dport 53" in d]
    vi_tri_reject = [i for i, d in enumerate(ds) if "REJECT" in d]
    assert vi_tri_est, "thiếu ACCEPT ESTABLISHED,RELATED trong INPUT"
    assert vi_tri_dns, "thiếu ngoại lệ DNS trong INPUT"
    assert vi_tri_reject, "thiếu REJECT trong INPUT"
    assert max(vi_tri_est) < min(vi_tri_dns), "ESTABLISHED phải đứng TRƯỚC DNS"
    assert max(vi_tri_dns) < min(vi_tri_reject), "DNS phải đứng TRƯỚC REJECT"
    assert "-j ACCEPT" in ds[vi_tri_est[0]]
    assert GATEWAY_GIA in ds[vi_tri_dns[0]], "ngoại lệ DNS phải nhắm tới gateway"


def test_moi_luat_input_gan_nhan(moi_truong_gia):
    dong = dong_dry_run(moi_truong_gia)
    ds = dong_input(dong)
    assert ds
    for d in ds:
        assert NHAN in d, f"luật INPUT thiếu nhãn `{NHAN}`: {d}"


def test_cau_doc_hai_thi_loi_khong_chay_iptables(moi_truong_gia):
    moi_truong_gia["env"]["NV30_CAU_DOC"] = "1"
    kq = chay_script(moi_truong_gia, "--dry-run")
    assert kq.returncode == 3, f"tên cầu độc phải thoát 3, được {kq.returncode}"
    assert "cầu" in kq.stderr, kq.stderr
    # Không được chạm iptables: nhật ký chỉ có lệnh -L kiểm quyền (nếu có).
    nhat_ky = moi_truong_gia["nhat_ky"].read_text(encoding="utf-8")
    assert "-A " not in nhat_ky and "-I " not in nhat_ky, \
        f"tên cầu độc nhưng vẫn chạy iptables: {nhat_ky}"


def test_id_mang_rong_thi_loi(moi_truong_gia):
    moi_truong_gia["env"]["NV30_ID_RONG"] = "1"
    kq = chay_script(moi_truong_gia, "--dry-run")
    assert kq.returncode == 3, f"Id rỗng phải thoát 3, được {kq.returncode}"


def test_idempotent_input_hai_lan(moi_truong_gia):
    lan1 = chay_script(moi_truong_gia)
    assert lan1.returncode == 0, lan1.stderr
    ds1 = [d for d in moi_truong_gia["trang_thai"].read_text(encoding="utf-8").splitlines()
           if d.strip() and "-i " in d]
    assert len(ds1) == 4, f"phải có 4 luật INPUT v4, được {len(ds1)}: {ds1}"
    lan2 = chay_script(moi_truong_gia)
    assert lan2.returncode == 0, lan2.stderr
    ds2 = [d for d in moi_truong_gia["trang_thai"].read_text(encoding="utf-8").splitlines()
           if d.strip() and "-i " in d]
    assert ds2 == ds1, "chạy lần 2 làm thay đổi luật INPUT"
    # Lần 2 không gọi -I thêm.
    so_lan_i = sum(1 for d in moi_truong_gia["nhat_ky"].read_text(encoding="utf-8").splitlines()
                   if d.startswith("-I "))
    assert so_lan_i == 4, f"lần 2 vẫn gọi -I (tổng: {so_lan_i})"


def test_go_go_ca_luat_input(moi_truong_gia):
    cai = chay_script(moi_truong_gia)
    assert cai.returncode == 0, cai.stderr
    assert moi_truong_gia["trang_thai"].read_text(encoding="utf-8").strip()
    go = chay_script(moi_truong_gia, "--go")
    assert go.returncode == 0, go.stderr
    assert moi_truong_gia["trang_thai"].read_text(encoding="utf-8").strip() == "", \
        "--go phải gỡ cả luật INPUT có nhãn"
    assert "INPUT" in go.stdout, "--go phải in ra luật INPUT đã gỡ"


def test_kiem_bao_thieu_input_roi_du(moi_truong_gia):
    truoc = chay_script(moi_truong_gia, "--kiem")
    assert truoc.returncode == 1
    assert any("THIEU" in d and "INPUT" in d for d in truoc.stdout.splitlines()), \
        "--kiem phải báo THIEU luật INPUT khi chưa cài"
    cai = chay_script(moi_truong_gia)
    assert cai.returncode == 0, cai.stderr
    sau = chay_script(moi_truong_gia, "--kiem")
    assert sau.returncode == 0, sau.stdout
    assert "THIEU" not in sau.stdout


def test_luu_luong_trong_cau_khong_bi_chan(moi_truong_gia):
    # Container<->container trong cùng cầu đi ở tầng L2, không vào INPUT của
    # máy chủ; luật INPUT chỉ gắn `-i <cầu>` nên không thể khớp lưu lượng nội
    # mạng theo dải nguồn. DOCKER-USER vẫn giữ ngoại lệ ACCEPT nội mạng.
    dong = dong_dry_run(moi_truong_gia)
    for d in dong_input(dong):
        assert f"-s {SUBNET_GIA}" not in d, \
            f"luật INPUT không được khớp theo dải nguồn (chỉ -i cầu): {d}"
        assert "-i eth0" not in d and "-i lo" not in d, \
            f"luật INPUT không được chạm giao diện khác: {d}"
    noi_mang = [d for d in dong
                if "DOCKER-USER" in d and f"-s {SUBNET_GIA}" in d
                and f"-d {SUBNET_GIA}" in d and "-j ACCEPT" in d]
    assert noi_mang, "DOCKER-USER phải giữ ngoại lệ ACCEPT lưu lượng trong mạng"


def test_dot_bien_xoa_reject_input_thi_do(moi_truong_gia, tmp_path):
    # Đột biến: xóa dòng sinh luật REJECT trong sinh_luat_input_v4/v6.
    # Khẳng định "có luật REJECT trong INPUT" phải XANH trên script gốc...
    assert co_luat_reject_input(dong_dry_run(moi_truong_gia)), \
        "script gốc phải có REJECT trong INPUT"
    # ...và ĐỎ trên bản đột biến.
    ban_dot_bien = tmp_path / "script-dot-bien.sh"
    goc = Path(moi_truong_gia["script"]).read_text(encoding="utf-8")
    dot_bien = "\n".join(
        d for d in goc.splitlines()
        if not ("-i %s -j REJECT" in d and "REJECT" in d)
    )
    # Đảm bảo đột biến thật sự đã xóa (cả v4 và v6).
    assert dot_bien.count("printf -- '-i %s -j REJECT") == 0, "đột biến chưa xóa dòng REJECT"
    assert goc.count("printf -- '-i %s -j REJECT") == 2, "giả định về script gốc sai"
    ban_dot_bien.write_text(dot_bien, encoding="utf-8")
    moi_truong_gia["script"] = str(ban_dot_bien)
    assert not co_luat_reject_input(dong_dry_run(moi_truong_gia)), \
        "ĐỎ: đột biến xóa REJECT INPUT phải làm mất luật REJECT"


def test_ipv6_input(moi_truong_gia):
    moi_truong_gia["env"]["NV30_IPV6"] = "1"
    dong = dong_dry_run(moi_truong_gia)
    ds = [d for d in dong if d.startswith("ip6tables -I INPUT ")]
    assert ds, "mạng bật IPv6 phải có luật ip6tables INPUT"
    assert any("ESTABLISHED,RELATED" in d for d in ds)
    assert any("REJECT" in d for d in ds)
    assert any("--dport 53" in d and GATEWAY_V6_GIA in d for d in ds), \
        "thiếu ngoại lệ DNS v6 tới gateway"
    for d in ds:
        assert NHAN in d, f"luật ip6tables INPUT thiếu nhãn: {d}"
        assert f"-i {CAU_MAC_DINH_GIA}" in d
