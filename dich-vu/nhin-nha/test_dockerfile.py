"""Test tĩnh Dockerfile + requirements của dich-vu/nhin-nha."""

from __future__ import annotations

import re
from pathlib import Path

DICH_VU = Path(__file__).resolve().parent


def test_dockerfile_ghim_digest():
    df = (DICH_VU / "Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"^FROM \S+@sha256:[0-9a-f]{64}$", df, flags=re.M), df


def test_dockerfile_khong_tai_model():
    df = (DICH_VU / "Dockerfile").read_text(encoding="utf-8")
    for dong in df.splitlines():
        d = dong.strip()
        assert not (d.startswith("RUN") and ("tai_model" in d or "curl" in d or "wget" in d)), dong
        assert not d.startswith("ENTRYPOINT") or "tai_model" not in d, dong


def test_requirements_co_hash():
    req = (DICH_VU / "requirements.txt").read_text(encoding="utf-8")
    for goi in ("numpy", "onnxruntime", "opencv-python-headless", "pillow"):
        assert f"{goi}==" in req
    assert req.count("--hash=sha256:") >= 4


def test_model_sha256_that():
    """Hằng sha256 trong yolo.py/khuon_mat.py phải là digest đầy đủ 64 hex."""
    yolo = (DICH_VU / "yolo.py").read_text(encoding="utf-8")
    mat = (DICH_VU / "khuon_mat.py").read_text(encoding="utf-8")
    import re as _re
    for ten, noi_dung, so in (("yolo", yolo, 5), ("mat", mat, 4)):
        cac = _re.findall(r'"[0-9a-f]{64}"', noi_dung)
        assert len(cac) >= so, (ten, cac)
    assert "2e947b787d9e787b93a16772a5f55b1d4d8c4d86f53146149c5d6a642442d6f7" in yolo
    assert "5e4447f50245bbd7966bd6c0fa52938c61474a04ec7def48753668a9d8b4ea3a" in mat
    assert "9cc6e4a75f0e2bf0b1aed94578f144d15175f357bdc05e815e5c4a02b319eb4f" in mat
