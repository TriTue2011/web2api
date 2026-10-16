"""NV20 — lớp lỗi «đọc hỏng rồi ghi đè»: kho hỏng không được coi như rỗng rồi ghi đè.

Mỗi test: tạo tệp kho HỎNG → gọi hàm lưu → phải ném KhoHong và tệp giữ nguyên.
Trên mã cũ (chưa vá): tệp bị ghi đè → test ĐỎ. Trên mã đã vá: XANH.
"""

import pytest

from app import kho


@pytest.fixture()
def du_lieu(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    return tmp_path


def _ghi_hong(p):
    p.write_text('{"du_lieu_cu": true}{hong', encoding="utf-8")
    return p.read_text(encoding="utf-8")


def test_model_json_hong_khong_bi_ghi_de(du_lieu):
    p = du_lieu / "model.json"
    truoc = _ghi_hong(p)
    with pytest.raises(kho.KhoHong):
        kho.luu_cai_dat_model_module("mod_moi", [], {})
    assert p.read_text(encoding="utf-8") == truoc  # không mất, không ghi đè


def test_combo_json_hong_khong_bi_ghi_de(du_lieu):
    p = du_lieu / "combo.json"
    truoc = _ghi_hong(p)
    with pytest.raises(kho.KhoHong):
        kho.luu_mot_combo("combo_moi", {"mo_ta": "x", "kha_nang": [], "buoc": []})
    assert p.read_text(encoding="utf-8") == truoc


def test_cho_duyet_quyen_sai_dang_khong_bi_ghi_de(du_lieu):
    p = du_lieu / "cho_duyet_quyen.json"
    p.write_text('["khong-phai-doi-tuong"]', encoding="utf-8")
    with pytest.raises(kho.KhoHong):
        kho.luu_cho_duyet_quyen("mod", {"diff": {}})
    assert p.read_text(encoding="utf-8") == '["khong-phai-doi-tuong"]'


def test_model_json_hop_le_van_luu_duoc(du_lieu):
    kho.luu_cai_dat_model_module("m1", ["x"], {"q": "y"})
    kho.luu_cai_dat_model_module("m2", [], {})
    tat_ca = kho.doc_cai_dat_model()
    assert tat_ca["m1"] == {"tat": ["x"], "mac_dinh": {"q": "y"}}
    assert tat_ca["m2"] == {"tat": [], "mac_dinh": {}}


def test_combo_hop_le_van_luu_duoc(du_lieu):
    kho.luu_mot_combo("c1", {"mo_ta": "m", "kha_nang": [], "buoc": []})
    kho.luu_mot_combo("c2", {"mo_ta": "n", "kha_nang": [], "buoc": []})
    assert set(kho.doc_cai_dat_combo()) == {"c1", "c2"}


def test_nguon_git_trang_thai_hong_duoc_giu_lai(du_lieu):
    """nguon_git_trang_thai.json hỏng: tệp hỏng được đổi tên giữ lại (.hong),
    không lặng lẽ ghi đè; trạng thái mới bắt đầu rỗng."""
    from app import nguon_git

    p = du_lieu / nguon_git.TEP_TRANG_THAI
    noi_dung_hong = '{"dong_bo": [}{hong'
    p.write_text(noi_dung_hong, encoding="utf-8")

    class SoGia:
        pass

    ng = nguon_git.NguonGit(SoGia())
    assert ng.trang_thai == {}
    assert not p.exists()  # tệp hỏng đã được dời đi, không còn ở chỗ cũ
    ban_giu = sorted(du_lieu.glob(nguon_git.TEP_TRANG_THAI + ".hong*"))
    assert len(ban_giu) == 1
    assert ban_giu[0].read_text(encoding="utf-8") == noi_dung_hong


def test_nguon_git_trang_thai_oserror_khong_doi_ten_tep(du_lieu, monkeypatch):
    """Khi gặp OSError tạm thời (không phải lỗi cú pháp JSON), không dời tệp sang .hong."""
    from app import nguon_git

    p = du_lieu / nguon_git.TEP_TRANG_THAI
    noi_dung_lanh = '{"dong_bo": "ok"}'
    p.write_text(noi_dung_lanh, encoding="utf-8")

    class SoGia:
        pass

    # Giả lập doc_json ném KhoHong với __cause__ là OSError (ví dụ lỗi I/O)
    def fake_doc_json(_):
        err = OSError("Lỗi phân quyền tạm thời")
        kh = kho.KhoHong(f"{p} hỏng: {err}")
        kh.__cause__ = err
        raise kh

    monkeypatch.setattr(kho, "doc_json", fake_doc_json)

    ng = nguon_git.NguonGit(SoGia())
    assert ng.trang_thai == {}
    assert p.exists()  # Tệp lành vẫn giữ nguyên, KHÔNG bị đổi tên sang .hong
    ban_giu = list(du_lieu.glob(nguon_git.TEP_TRANG_THAI + ".hong*"))
    assert len(ban_giu) == 0
