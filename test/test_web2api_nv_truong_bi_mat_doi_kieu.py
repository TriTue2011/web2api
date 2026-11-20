"""NV — đổi kieu của trường cấu hình không được làm mất giá trị đã lưu."""

import json
from types import SimpleNamespace

import pytest

from app import kho, sao_luu


FINGERPRINT = "a" * 64
MAT_KHAU = "mat-khau-gia-cho-test"


@pytest.fixture()
def du_lieu(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    return tmp_path


def ghi_modules(goc, cau_hinh):
    (goc / "modules.json").write_text(json.dumps({
        "modules": [{
            "id": "router-test",
            "cai_luc": "2026-11-20T00:00:00+00:00",
            "cau_hinh_cong_khai": cau_hinh,
        }],
    }), encoding="utf-8")


def test_thuong_doi_sang_bi_mat_doc_va_di_tru_gia_tri(du_lieu):
    ghi_modules(du_lieu, {"van_tay_tls": FINGERPRINT, "mat_khau": "…"})
    kho.ghi_bi_mat("router-test", {"mat_khau": MAT_KHAU})
    truong = [
        {"khoa": "van_tay_tls", "kieu": "bi_mat"},
        {"khoa": "mat_khau", "kieu": "bi_mat"},
    ]

    cau_hinh = kho.doc_cau_hinh("router-test", truong, kho.doc_modules()[0][0])

    assert cau_hinh == {"van_tay_tls": FINGERPRINT, "mat_khau": MAT_KHAU}
    assert json.loads((du_lieu / "modules.json").read_text(encoding="utf-8"))["modules"][0][
        "cau_hinh_cong_khai"
    ]["van_tay_tls"] == "…aaaa"
    assert kho.doc_bi_mat("router-test") == {
        "van_tay_tls": FINGERPRINT,
        "mat_khau": MAT_KHAU,
    }


def test_bi_mat_doi_sang_thuong_doc_gia_tri_that_khong_doc_dang_che(du_lieu):
    ghi_modules(du_lieu, {"van_tay_tls": "…aaaa"})
    kho.ghi_bi_mat("router-test", {"van_tay_tls": FINGERPRINT})
    truong = [{"khoa": "van_tay_tls", "kieu": "chuoi"}]

    cau_hinh = kho.doc_cau_hinh("router-test", truong, kho.doc_modules()[0][0])

    assert cau_hinh == {"van_tay_tls": FINGERPRINT}
    assert kho.doc_bi_mat("router-test") == {"van_tay_tls": FINGERPRINT}
    assert json.loads((du_lieu / "modules.json").read_text(encoding="utf-8"))["modules"][0][
        "cau_hinh_cong_khai"
    ]["van_tay_tls"] == "…aaaa"


def test_bi_mat_da_luu_khong_doi_hanh_vi(du_lieu):
    ghi_modules(du_lieu, {"mat_khau": "…test"})
    kho.ghi_bi_mat("router-test", {"mat_khau": MAT_KHAU})
    truong = [{"khoa": "mat_khau", "kieu": "bi_mat"}]

    assert kho.doc_cau_hinh("router-test", truong, kho.doc_modules()[0][0]) == {
        "mat_khau": MAT_KHAU
    }


def test_bi_mat_that_co_hinh_dang_giong_che_van_duoc_nap(du_lieu):
    ghi_modules(du_lieu, {"bi_mat": "…"})
    kho.ghi_bi_mat("router-test", {"bi_mat": "…abcd"})

    assert kho.doc_cau_hinh(
        "router-test", [{"khoa": "bi_mat", "kieu": "bi_mat"}], kho.doc_modules()[0][0]
    ) == {"bi_mat": "…abcd"}


def test_dang_che_khong_lot_vao_cau_hinh_hoac_ghep(du_lieu):
    ghi_modules(du_lieu, {"mat_khau": "…test"})
    truong = [{"khoa": "mat_khau", "kieu": "bi_mat"}]
    muc = kho.doc_modules()[0][0]

    assert kho.doc_cau_hinh("router-test", truong, muc) == {}
    assert kho.ghep_cau_hinh(truong, {"mat_khau": MAT_KHAU}, {"mat_khau": "…test"}) == {
        "mat_khau": MAT_KHAU
    }


def test_chuoi_thuong_co_hinh_dang_giong_che_la_gia_tri_that():
    gia_tri = "…abcd"
    assert kho.ghep_cau_hinh(
        [{"khoa": "nhan", "kieu": "chuoi"}], {"nhan": "cu"}, {"nhan": gia_tri}
    ) == {"nhan": gia_tri}


def test_chuoi_thuong_giong_che_doc_lai_sau_khi_luu(du_lieu):
    muc = kho.luu_cai_dat("router-test", [{"khoa": "nhan", "kieu": "chuoi"}], {"nhan": "…abcd"})

    assert kho.doc_cau_hinh("router-test", [{"khoa": "nhan", "kieu": "chuoi"}], muc) == {
        "nhan": "…abcd"
    }


def test_danh_sach_bi_mat_khong_nhan_dang_che_la_khoa_moi():
    with pytest.raises(ValueError, match="dạng che"):
        kho.ghep_cau_hinh(
            [{"khoa": "khoa", "kieu": "danh_sach_bi_mat"}],
            {"khoa": ["gia-tri-that"]},
            {"khoa": {"them": ["…abcd"]}},
        )


def test_khoi_phuc_di_tru_truong_thuong_sang_bi_mat_truoc_khi_ghi():
    km = SimpleNamespace(truong_cau_hinh=[{"khoa": "van_tay_tls", "kieu": "bi_mat"}])
    so = SimpleNamespace(catalog={"router-test": km})
    sach = {"modules.json": {"modules": [{
        "id": "router-test",
        "cai_luc": "2026-11-20T00:00:00+00:00",
        "cau_hinh_cong_khai": {"van_tay_tls": FINGERPRINT},
    }]}}

    ke_hoach = sao_luu._doi_chieu({}, sach, so, None, "b" * 64)

    assert ke_hoach["ghi"]["modules.json"]["modules"][0]["cau_hinh_cong_khai"] == {
        "van_tay_tls": "…aaaa"
    }
    assert ke_hoach["ghi"]["bi_mat/router-test.json"] == {"van_tay_tls": FINGERPRINT}


def test_khoi_phuc_di_tru_khong_gia_bi_mat_trong_ban_sao_luu(du_lieu):
    kho.ghi_bi_mat("router-test", {"api_key": MAT_KHAU})
    km = SimpleNamespace(truong_cau_hinh=[
        {"khoa": "van_tay_tls", "kieu": "bi_mat"},
        {"khoa": "api_key", "kieu": "bi_mat"},
        {"khoa": "base_url", "kieu": "chuoi"},
    ])
    so = SimpleNamespace(catalog={"router-test": km})
    cu = {"id": "router-test", "cai_luc": "cu", "cau_hinh_cong_khai": {
        "van_tay_tls": "…aaaa", "api_key": "…test", "base_url": "https://cu.example/v1",
    }}
    sach = {"modules.json": {"modules": [{
        "id": "router-test",
        "cai_luc": "moi",
        "cau_hinh_cong_khai": {"van_tay_tls": FINGERPRINT, "base_url": "https://moi.example/v1"},
    }]}}
    (du_lieu / "modules.json").write_text(json.dumps({"modules": [cu]}), encoding="utf-8")

    ke_hoach = sao_luu._doi_chieu({}, sach, so, None, "c" * 64)

    assert any(x["tep"] == "modules.json" for x in ke_hoach["tu_choi"])
    assert ke_hoach["ghi"]["bi_mat/router-test.json"] == {
        "api_key": MAT_KHAU,
        "van_tay_tls": FINGERPRINT,
    }


def test_khoi_phuc_module_chua_co_manifest_che_cau_hinh_cong_khai(du_lieu):
    so = SimpleNamespace(catalog={})
    sach = {"modules.json": {"modules": [{
        "id": "router-test",
        "cai_luc": "2026-11-20T00:00:00+00:00",
        "cau_hinh_cong_khai": {"van_tay_tls": FINGERPRINT},
    }]}}

    ke_hoach = sao_luu._doi_chieu({}, sach, so, None, "d" * 64)

    assert ke_hoach["ghi"]["modules.json"]["modules"][0]["cau_hinh_cong_khai"] == {
        "van_tay_tls": "…aaaa"
    }
    assert ke_hoach["ghi"]["bi_mat/router-test.json"] == {"van_tay_tls": FINGERPRINT}


def test_khoi_phuc_danh_sach_tron_dang_che_khong_de_lo_gia_tri_that(du_lieu):
    so = SimpleNamespace(catalog={})
    sach = {"modules.json": {"modules": [{
        "id": "router-test",
        "cai_luc": "2026-11-20T00:00:00+00:00",
        "cau_hinh_cong_khai": {"khoa": ["gia-tri-that", "…abcd"]},
    }]}}

    ke_hoach = sao_luu._doi_chieu({}, sach, so, None, "e" * 64)

    assert ke_hoach["ghi"]["modules.json"]["modules"][0]["cau_hinh_cong_khai"] == {
        "khoa": ["…that", "…"]
    }
    assert "bi_mat/router-test.json" not in ke_hoach["ghi"]
