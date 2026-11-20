"""Kiểm tra hai đường HTTP NV305 trực tiếp trên service may-dang-nhap."""
from __future__ import annotations

import asyncio
import logging
import threading
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

import _gia_goi  # noqa: F401  (dữ liệu tạm + mô-đun giả)


KHOA_TEST = "khoa-gia-nv305-0123456789abcdef"
KHOA_SAI = "khoa-sai-nv305-0123456789abcdef"
HEADER_AUTH = {"Authorization": f"Bearer {KHOA_TEST}"}
MAT_KHAU_TEST = "Vnc!305x"
DUONG_DAT = "/v1/novnc/mat-khau"
DUONG_TRANG_THAI = f"{DUONG_DAT}/trang-thai"


@pytest.fixture
def api_vnc(tmp_path, monkeypatch):
    thu_muc_khoa = tmp_path / "khoa"
    thu_muc_khoa.mkdir()
    monkeypatch.setenv("CAPTCHA_SOLVER_API_KEY", KHOA_TEST)
    monkeypatch.setenv("CAPTCHA_SOLVER_TINH_NANG_BAT", "dang_nhap")
    monkeypatch.setenv("CAPTCHA_SOLVER_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CAPTCHA_SOLVER_KEY_DIR", str(thu_muc_khoa))
    monkeypatch.setenv("ACCOUNTS_DB", str(tmp_path / "accounts.db"))

    from src.main import app, mat_khau_vnc, settings

    monkeypatch.setattr(settings, "key_dir", thu_muc_khoa)
    nap_lai = Mock(return_value=True)
    monkeypatch.setattr(mat_khau_vnc, "_nap_lai_x11vnc", nap_lai)
    # Không chạy lifespan vì nó khởi động browser pool.
    return TestClient(app), nap_lai


@pytest.mark.parametrize("phuong_thuc,duong,noi_dung", [
    ("GET", DUONG_TRANG_THAI, {}),
    ("PUT", DUONG_DAT, {"json": {"mat_khau": MAT_KHAU_TEST}}),
], ids=["trang-thai", "dat-mat-khau"])
@pytest.mark.parametrize("headers", [None, {"Authorization": f"Bearer {KHOA_SAI}"}],
                         ids=["thieu-khoa", "sai-khoa"])
def test_api_vnc_yeu_cau_khoa_dich_vu(api_vnc, phuong_thuc, duong, noi_dung, headers, tmp_path):
    client, nap_lai = api_vnc

    r = client.request(phuong_thuc, duong, headers=headers, **noi_dung)

    assert r.status_code == 401
    assert list((tmp_path / "khoa").iterdir()) == []
    nap_lai.assert_not_called()


def test_api_vnc_tat_dang_nhap_tra_403(api_vnc, tmp_path, monkeypatch):
    client, nap_lai = api_vnc
    monkeypatch.setenv("CAPTCHA_SOLVER_TINH_NANG_BAT", "")

    trang_thai = client.get(DUONG_TRANG_THAI, headers=HEADER_AUTH)
    dat = client.put(DUONG_DAT, headers=HEADER_AUTH, json={"mat_khau": MAT_KHAU_TEST})

    assert trang_thai.status_code == dat.status_code == 403
    assert trang_thai.json()["ma_loi"] == dat.json()["ma_loi"] == "tinh_nang_tat"
    assert list((tmp_path / "khoa").iterdir()) == []
    nap_lai.assert_not_called()


def test_api_vnc_dat_va_doc_trang_thai_khong_lo_mat_khau(api_vnc, tmp_path, caplog):
    client, nap_lai = api_vnc

    with caplog.at_level(logging.DEBUG):
        truoc = client.get(DUONG_TRANG_THAI, headers=HEADER_AUTH)
        dat = client.put(DUONG_DAT, headers=HEADER_AUTH, json={"mat_khau": MAT_KHAU_TEST})
        sau = client.get(DUONG_TRANG_THAI, headers=HEADER_AUTH)

    assert truoc.status_code == dat.status_code == sau.status_code == 200
    assert truoc.json() == {"ok": True, "da_dat": False}
    assert dat.json() == {"ok": True, "da_nap_lai": True}
    assert sau.json() == {"ok": True, "da_dat": True}
    assert (tmp_path / "khoa" / "mat-khau-vnc").read_text(encoding="utf-8") == MAT_KHAU_TEST
    assert (tmp_path / "khoa" / "mat-khau-vnc-chu-dat").is_file()
    nap_lai.assert_called_once()
    assert MAT_KHAU_TEST not in caplog.text


@pytest.mark.parametrize("mat_khau", ["Vnc!305xx", "Vnc 305x", "Vnc-é305"],
                         ids=["qua-dai", "khoang-trang", "ngoai-ascii"])
def test_api_vnc_mat_khau_sai_tra_400_khong_echo(api_vnc, tmp_path, caplog, mat_khau):
    client, nap_lai = api_vnc

    with caplog.at_level(logging.DEBUG):
        r = client.put(DUONG_DAT, headers=HEADER_AUTH, json={"mat_khau": mat_khau})

    assert r.status_code == 400
    assert mat_khau not in str(r.json())
    assert mat_khau not in caplog.text
    assert list((tmp_path / "khoa").iterdir()) == []
    nap_lai.assert_not_called()


def test_api_vnc_thieu_truong_mat_khau_tra_422(api_vnc, tmp_path):
    client, nap_lai = api_vnc

    r = client.put(DUONG_DAT, headers=HEADER_AUTH, json={})

    assert r.status_code == 422
    assert [(loi["loc"], loi["type"]) for loi in r.json()["detail"]] == [
        (["body", "mat_khau"], "missing"),
    ]
    assert list((tmp_path / "khoa").iterdir()) == []
    nap_lai.assert_not_called()


@pytest.mark.parametrize("than", [
    {"mat_khau": ["Q7!p"]},
    {"mat_khau": {"bi_mat": "Q7!p"}},
    {"truong_khac": "Q7!p"},
    ["Q7!p"],
    "Q7!p",
])
def test_api_vnc_validation_khong_phan_chieu_bi_mat(api_vnc, tmp_path, caplog, than):
    client, nap_lai = api_vnc
    with caplog.at_level(logging.DEBUG):
        r = client.put(DUONG_DAT, headers=HEADER_AUTH, json=than)
    assert r.status_code == 422
    assert "Q7!p" not in r.text and "Q7!p" not in caplog.text
    assert all(set(loi) <= {"loc", "msg", "type"} for loi in r.json()["detail"])
    assert list((tmp_path / "khoa").iterdir()) == []
    nap_lai.assert_not_called()


def test_api_vnc_json_hong_khong_phan_chieu_bi_mat(api_vnc, caplog):
    client, nap_lai = api_vnc
    with caplog.at_level(logging.DEBUG):
        r = client.put(DUONG_DAT, headers={**HEADER_AUTH, "Content-Type": "application/json"},
                       content='{"mat_khau":"Q7!p"')
    assert r.status_code == 422
    assert "Q7!p" not in r.text and "Q7!p" not in caplog.text
    assert all(set(loi) <= {"loc", "msg", "type"} for loi in r.json()["detail"])
    nap_lai.assert_not_called()


def test_api_vnc_ghi_va_restart_khong_giu_event_loop(api_vnc, monkeypatch):
    from src import main

    luong_event_loop = threading.get_ident()
    da_ghi = []

    def ghi_gia(mat_khau, thu_muc_khoa):
        da_ghi.append(threading.get_ident())
        return {"ok": True, "da_nap_lai": True}

    monkeypatch.setattr(main.mat_khau_vnc, "ghi_mat_khau_vnc", ghi_gia)
    kq = asyncio.run(main.api_dat_mat_khau_vnc(main.MatKhauVncReq(mat_khau=MAT_KHAU_TEST)))
    assert kq == {"ok": True, "da_nap_lai": True}
    assert len(da_ghi) == 1 and da_ghi[0] != luong_event_loop
