---
name: python-testing
description: Cách viết test cho web2api — pytest + fixture + mock, máy chủ ngoài đều giả, test đỏ→xanh và xét mã thoát. Dùng khi viết/sửa test trong thư mục test/.
---

# Viết test cho web2api

## Quy ước cứng của kho (đọc `test/conftest.py` trước khi viết)

- **Máy chủ Google/GitHub/OpenAI đều GIẢ và chạy cục bộ** (127.0.0.1); mọi yêu cầu
  không phải 127.0.0.1 bị `conftest.py` đẩy vào proxy chết (cổng 9) và hỏng ngay.
  Test không bao giờ gọi mạng thật — một test quên trỏ về máy chủ giả sẽ hỏng,
  đó là chủ đích.
- Lệnh chạy đúng:
  ```bash
  .venv/bin/python -m pytest test -q -p no:cacheprovider    # xét MÃ THOÁT
  ```
- **Xét MÃ THOÁT, không xét cảm giác**: `echo $?` — 0 là xanh, khác 0 là đỏ.
- `test/test_web2api_readme.py` bắt lệch giữa tài liệu và mã: thêm đường API hay
  biến `WEB2API_*` thì phải có trong `docs/THAM-CHIEU.md`, không test sẽ đỏ.
- Lỗi do khớp chuỗi bắt nhầm hay đọc sai kho dữ liệu phải sửa ở **gốc** rồi tìm
  mọi chỗ cùng lớp bằng `grep`, không vá triệu chứng.

## Test-first: đỏ → xanh → refactor

1. **Đỏ**: viết test mô tả đúng hành vi mong muốn, chạy và xem nó **thất bại**
   vì đúng lý do (không phải vì lỗi cú pháp).
2. **Xanh**: viết mã tối thiểu cho test qua.
3. **Refactor**: làm gọn mã mà vẫn giữ xanh.

Một test mới chưa từng đỏ thì chưa chứng minh được gì.

## Fixture

```python
import pytest

@pytest.fixture
def may_chu_gia():
    """Máy chủ OpenAI giả cho test — mọi test dùng chung chỗ này."""
    ...

def test_chat_khong_khoa(may_chu_gia):
    resp = goi("/v1/chat/completions", khoa="")
    assert resp.status_code == 401
```

- Đặt fixture dùng chung ở `test/conftest.py`, không sao chép máy chủ giả vào
  từng test.
- Fixture mặc định phạm vi `function` (mỗi test một bộ); chỉ nâng `scope` khi
  dựng đắt đỏ (máy chủ giả, thư mục dữ liệu) và chắc chắn test không dùng chung
  trạng thái bẩn.
- `@pytest.fixture(autouse=True)` chỉ cho quy tắc chung (như chặn mạng ở
  conftest), không cho trạng thái nghiệp vụ.

## Mock — giả cái ngoài, thật cái mình kiểm

```python
from unittest.mock import patch

def test_loi_nha_cung_cap():
    with patch("app.chuyen_tiep.khach_http") as khach:
        khach.send.side_effect = TimeoutError()
        resp = goi("/v1/chat/completions")
    assert resp.status_code == 502
```

- Giả: gọi ra ngoài (HTTP tới nhà cung cấp), giờ, ngẫu nhiên, tệp hệ thống.
- Không giả: chính mã kho đang kiểm — mock cả mã kho thì test chỉ kiểm mock.
- Dùng `autospec=True` khi sợ mock đặt sai chữ ký hàm thật.

## Parametrize và kiểm ngoại lệ

```python
import pytest

@pytest.mark.parametrize("ma,du_doan", [(401, "invalid_api_key"), (403, "forbidden")])
def test_loi_xac_thuc(ma, du_doan):
    ...

def test_khoa_sai_nhieu_lan_bi_khoa():
    with pytest.raises(TimeoutError):
        goi_quá_gioi_han()
```

- `@pytest.mark.parametrize`: một test chạy nhiều bộ dữ liệu, đặt `ids` dễ đọc.
- Ngoại lệ mong muốn dùng `pytest.raises(...)`; test không bao giờ `try/except`
  để nuốt lỗi.

## Nên và tránh

- **Nên**: mỗi test kiểm MỘT hành vi; tên test nói rõ mong muốn
  (`test_chat_khong_khoa_tra_401`); kiểm biên (đầu vào rỗng, tham số sai).
- **Tránh**: test phụ thuộc thứ tự; trạng thái dùng chung giữa các test;
  `print` thay cho `assert`; bỏ qua test hỏng — hỏng là dừng.
