"""Test NV85: công cụ scripts.kiem_module cho tác giả module.

Chạy công cụ qua subprocess (nhanh, không nhập mã module). Module mẫu được
dựng trong tmp_path.
"""

import json
import subprocess
import sys
from pathlib import Path

GOC_KHO = Path(__file__).resolve().parents[1]

MANIFEST_TOT = {
    "id": "mau-tot",
    "ten": "Mẫu tốt",
    "mo_ta": "Module mẫu tốt cho test.",
    "loai": "ai",
    "giao_thuc": "mau-tot-thu-cong",
    "tien_to": "mau-tot",
    "hang": "A",
    "phien_ban": "0.1.0",
    "cung_cap": ["chat"],
    "truong_cau_hinh": [],
    "tai_nguyen": {"mang_ra": ["vi-du.example"]},
    "yeu_cau_goi": [],
    "muc": [],
}


def dung_module_mau(noi: Path, manifest: dict, ma: dict[str, str]) -> Path:
    noi.mkdir(parents=True, exist_ok=True)
    (noi / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    for ten, nd in ma.items():
        (noi / ten).write_text(nd, encoding="utf-8")
    return noi


def chay(thu_muc: Path, *tham_so: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "scripts.kiem_module", str(thu_muc), *tham_so],
        cwd=GOC_KHO, capture_output=True, text=True, timeout=60)


def test_mau_tot_dat(tmp_path):
    noi = dung_module_mau(tmp_path / "mau-tot", MANIFEST_TOT, {
        "client.py": 'import httpx\n\nURL = "https://vi-du.example/api"\n',
        "module.py": "MO_TA = 'mau'\n",
    })
    kq = chay(noi)
    assert kq.returncode == 0, kq.stdout + kq.stderr
    assert "KẾT LUẬN" not in kq.stdout or "đạt" in kq.stdout.splitlines()[0]


def test_thieu_manifest_exit_2(tmp_path):
    noi = tmp_path / "trong"
    noi.mkdir()
    kq = chay(noi)
    assert kq.returncode == 2


def test_thu_muc_khong_co_exit_2(tmp_path):
    kq = chay(tmp_path / "khong-co")
    assert kq.returncode == 2


def test_manifest_hong_exit_2(tmp_path):
    noi = tmp_path / "hong"
    noi.mkdir()
    (noi / "manifest.json").write_text("{khong phai json", encoding="utf-8")
    kq = chay(noi)
    assert kq.returncode == 2


def test_manifest_sai_truong_exit_1(tmp_path):
    mf = dict(MANIFEST_TOT)
    mf.pop("hang")  # thiếu trường bắt buộc
    noi = dung_module_mau(tmp_path / "sai", mf, {"module.py": "X = 1\n"})
    kq = chay(noi)
    assert kq.returncode == 1
    assert "hang" in kq.stdout


def test_goi_mang_mien_chua_khai(tmp_path):
    noi = dung_module_mau(tmp_path / "mien-la", MANIFEST_TOT, {
        "client.py": 'import httpx\n\nr = httpx.get("https://la.example/api")\n',
    })
    kq = chay(noi)
    assert kq.returncode == 1
    assert "la.example" in kq.stdout


def test_goi_la_khong_khai(tmp_path):
    noi = dung_module_mau(tmp_path / "goi-la", MANIFEST_TOT, {
        "client.py": "import goi_la_xyz\n\ngoi_la_xyz.goi()\n",
    })
    kq = chay(noi)
    assert kq.returncode == 1
    assert "goi_la_xyz" in kq.stdout


def test_tien_trinh_con(tmp_path):
    noi = dung_module_mau(tmp_path / "ttc", MANIFEST_TOT, {
        "x.py": "import subprocess\n\nsubprocess.run(['ls'])\n",
    })
    kq = chay(noi)
    assert kq.returncode == 1
    assert "tiến trình con" in kq.stdout


def test_eval_va_pickle(tmp_path):
    noi = dung_module_mau(tmp_path / "eval", MANIFEST_TOT, {
        "x.py": "import pickle\n\ndata = eval('1+1')\nobj = pickle.loads(b'x')\n",
    })
    kq = chay(noi)
    assert kq.returncode == 1
    assert "nguy hiểm" in kq.stdout


def test_tat_tls(tmp_path):
    noi = dung_module_mau(tmp_path / "tls", MANIFEST_TOT, {
        "x.py": 'import httpx\n\nhttpx.get("https://vi-du.example", verify=False)\n',
    })
    kq = chay(noi)
    assert kq.returncode == 1
    assert "TLS" in kq.stdout


def test_ro_bi_mat_canh_bao(tmp_path):
    noi = dung_module_mau(tmp_path / "ro", MANIFEST_TOT, {
        "x.py": 'API_KEY = "sk-ant-rat-dai-1234567890abcdef"\n',
    })
    kq = chay(noi)
    # rò bí mật là cảnh báo, không tính lỗi → vẫn đạt nếu không có vi phạm
    assert "[cảnh báo]" in kq.stdout


def test_che_do_json(tmp_path):
    noi = dung_module_mau(tmp_path / "mau-tot", MANIFEST_TOT, {
        "client.py": 'import httpx\n\nURL = "https://vi-du.example/api"\n',
    })
    kq = chay(noi, "--json")
    assert kq.returncode == 0, kq.stdout + kq.stderr
    dk = json.loads(kq.stdout)
    assert dk["module"] == "mau-tot"
    assert dk["ma_thoat"] == 0
    assert "vi-du.example" in dk["mien"]
    # chuỗi trong JSON bị cắt ngắn
    for chuoi_list in (dk["nhap"], dk["mien"]):
        for s in chuoi_list:
            assert len(s) <= 210


def test_json_vi_pham_co_cau_truc(tmp_path):
    noi = dung_module_mau(tmp_path / "mien-la", MANIFEST_TOT, {
        "client.py": 'import httpx\n\nr = httpx.get("https://la.example/api")\n',
    })
    kq = chay(noi, "--json")
    assert kq.returncode == 1
    dk = json.loads(kq.stdout)
    assert any("la.example" in v for v in dk["vi_pham"])


def test_4_module_web_khong_bao_loi_manifest():
    for ten in ("gemini-web", "grok-web", "claude-web", "chatgpt-web"):
        kq = chay(GOC_KHO / "modules" / ten)
        assert kq.returncode == 0, f"{ten}: {kq.stdout}"
        assert "[lỗi]" not in kq.stdout


def test_4_module_web_mien_khop_mang_ra():
    for ten in ("gemini-web", "grok-web", "claude-web", "chatgpt-web"):
        kq = chay(GOC_KHO / "modules" / ten, "--json")
        assert kq.returncode == 0, f"{ten}: {kq.stdout}"
        dk = json.loads(kq.stdout)
        mf = json.loads((GOC_KHO / "modules" / ten / "manifest.json")
                        .read_text(encoding="utf-8"))
        mang_ra = mf["tai_nguyen"]["mang_ra"]
        for mien in dk["mien"]:
            assert any(mien == k or mien.endswith("." + k) for k in mang_ra), \
                f"{ten}: miền {mien} không khớp {mang_ra}"
