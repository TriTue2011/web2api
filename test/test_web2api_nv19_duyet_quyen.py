"""NV19 — lấp lỗ hổng NV14#7: kiểm SHA khi duyệt quyền ở CẢ HAI tầng (CHỈ THÊM TEST).

- app/so_module.py::_kiem_duyet_quyen: diff đúng + SHA sai → 409.
- app/nguon_git.py::_cai_duyet: diff đúng + SHA sai → 409.

Mỗi tầng một test; test phân biệt đúng 409 «SHA không khớp» (dùng match=),
không chỉ assert mã 409 chung chung.
"""

from __future__ import annotations

import asyncio

import pytest

from app import kho
from app.nguon_git import LoiGit, NguonGit
from app.quyen_module import bam_diff
from app.so_module import LoiSo, SoModule

ID_MOD = "mod-nv19"
SHA_DUNG = "a" * 40
SHA_SAI = "b" * 40
DIFF = {"them": [{"loai": "mang_ra", "ten": "https://vi-du.example/api"}], "bot": []}


@pytest.fixture
def moi_truong(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", "khoa-quan-tri-nv19-duyet-0123456789abcdef")
    kho.luu_cho_duyet_quyen(ID_MOD, {
        "id": ID_MOD, "sha_commit": SHA_DUNG, "tree_sha": "c" * 40,
        "diff": DIFF, "bam_diff": bam_diff(DIFF), "thu_muc": "/khong-ton-tai",
        "phien_ban": "0.2.0", "luc": "2026-10-15T00:00:00+00:00",
    })
    yield tmp_path
    kho.xoa_cho_duyet_quyen(ID_MOD)


def test_so_module_kiem_duyet_quyen_sha_sai_409(moi_truong):
    """NV14#7 (tầng so_module): diff đúng + SHA sai → 409 «SHA commit đã đổi»."""
    with pytest.raises(LoiSo, match="SHA commit đã đổi"):
        SoModule._kiem_duyet_quyen(
            ID_MOD,
            {"sha_commit": SHA_SAI},
            DIFF,
            [dict(m) for m in DIFF["them"]],  # danh sách chấp nhận khớp đúng phần «them» đã xem
        )


def test_nguon_git_cai_duyet_sha_sai_409(moi_truong):
    """NV14#7 (tầng nguon_git): diff đúng + SHA sai → 409 «SHA commit không khớp»."""
    ng = NguonGit(None)  # SHA sai bị chặn trước khi chạm tới SoModule

    async def goi():
        await ng._cai_duyet(ID_MOD, [dict(m) for m in DIFF["them"]], SHA_SAI)

    with pytest.raises(LoiGit, match="SHA commit không khớp"):
        asyncio.run(goi())
