"""Kiểm thử đường POST /v1/responses cho công cụ image_generation.

Chuẩn OpenAI Responses API: lõi dịch sang hợp đồng module.anh/anh_sua và trả đúng hình dạng Responses.

Chứng minh thêm:
- Phạm vi theo CHỨC NĂNG THẬT được gọi: có input_image là sửa ảnh → cần «anh_sua» như /v1/images/edits; không có
  ảnh là tạo ảnh → cần «anh» như /v1/images/generations. Khoá hẹp bị chặn TRƯỚC khi lõi tra model.
- `n` kiểm ở biên bằng MỘT hàm dùng chung cho /v1/responses, /v1/images/generations và /v1/images/edits.
- Ánh xạ trung thực: model đầy đủ như đường /v1/images/*, lỗi module chuyển nguyên (cả Retry-After), mục output
  chỉ nêu thuộc tính lấy từ KẾT QUẢ (phản hồi module hoặc byte ảnh), không lặp tham số khách.
"""

from __future__ import annotations

import base64
import json

import pytest
from fastapi.testclient import TestClient

from app import tuyen
from app.combo import bo_cau_chi, bo_dinh_phien, bo_han_muc
from app.main import app
from app.so_module import SoModule
from app.yeu_cau_da_phan import YeuCauDaPhan

KHOA_QT = "khoa-quan-tri-test-0123456789abcdef"
QT = {"Authorization": f"Bearer {KHOA_QT}"}
CHUOI_BI_MAT_GIA = "sk-khoa-tu-xa-RIENG-7777"

# 1x1 PNG trong suốt mẫu
PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4"
    b"\x00\x00\x00\rIDATx\x9cc`\x00\x00\x00\x02\x00\x01H\xaf\xa4q\x00\x00\x00\x00IEND\xaeB`\x82"
)
B64_PNG = base64.b64encode(PNG_1X1).decode("ascii")
DATA_URL_PNG = f"data:image/png;base64,{B64_PNG}"
DATA_URL_JPEG = f"data:image/jpeg;base64,{B64_PNG}"
DATA_URL_WEBP = f"data:image/webp;base64,{B64_PNG}"


class ModuleGia:
    """Module giả hỗ trợ cả tạo ảnh (anh) và sửa ảnh (anh_sua)."""

    def __init__(self, tien_to: str = "gia"):
        self.tien_to = tien_to
        self.manifest = {"id": "gia", "ten": "Module Giả", "cung_cap": ["anh", "anh_sua"]}
        self.da_nhan_anh: list[dict] = []
        self.da_nhan_sua: list[YeuCauDaPhan] = []
        self.loi_nem: Exception | None = None
        self.loi_tra_ma: tuple[int, dict, bytes] | None = None
        self.tra_url: bool = False
        #: Trường cấp đầu thêm vào phản hồi thành công (như gpt-image trả size/quality/output_format/background).
        self.tra_them: dict = {}

    async def models(self) -> list[dict]:
        return [
            {"id": f"{self.tien_to}/anh-model", "object": "model", "kha_nang": ["anh", "anh_sua"]},
        ]

    async def anh(self, body: dict) -> tuple[int, dict, bytes]:
        self.da_nhan_anh.append(body)
        if self.loi_nem:
            raise self.loi_nem
        if self.loi_tra_ma:
            return self.loi_tra_ma
        if self.tra_url:
            return 200, {"content-type": "application/json"}, json.dumps({
                "created": 1728860000,
                "data": [{"url": "http://127.0.0.1/anh.png"}]
            }).encode()

        n = int(body.get("n") or 1)
        cac_anh = []
        for i in range(n):
            cac_anh.append({
                "b64_json": B64_PNG,
                "revised_prompt": f"Ảnh đã sửa {i + 1}" if i == 0 else None,
            })
        return 200, {"content-type": "application/json", "x-module-rieng": "co"}, json.dumps({
            "created": 1728860000,
            "data": cac_anh,
            **self.tra_them,
        }).encode()

    async def anh_sua(self, yc: YeuCauDaPhan) -> tuple[int, dict, bytes]:
        self.da_nhan_sua.append(yc)
        if self.loi_nem:
            raise self.loi_nem
        if self.loi_tra_ma:
            return self.loi_tra_ma
        return 200, {"content-type": "application/json"}, json.dumps({
            "created": 1728860000,
            "data": [{"b64_json": B64_PNG, "revised_prompt": "Ảnh sửa hoàn tất"}],
            **self.tra_them,
        }).encode()


class ModuleKhongAnh:
    """Module không có phương thức anh hay anh_sua."""

    def __init__(self, tien_to: str = "khonganh"):
        self.tien_to = tien_to
        self.manifest = {"id": "khonganh", "ten": "Không Ảnh", "cung_cap": ["chat"]}

    async def models(self) -> list[dict]:
        return [{"id": f"{self.tien_to}/chat-model", "object": "model", "kha_nang": ["chat"]}]


class ModuleChiTaoAnh:
    """Module chỉ có anh, không có anh_sua."""

    def __init__(self, tien_to: str = "chitao"):
        self.tien_to = tien_to
        self.manifest = {"id": "chitao", "ten": "Chỉ Tạo Ảnh", "cung_cap": ["anh"]}

    async def models(self) -> list[dict]:
        return [{"id": f"{self.tien_to}/anh-model", "object": "model", "kha_nang": ["anh"]}]

    async def anh(self, body: dict) -> tuple[int, dict, bytes]:
        return 200, {"content-type": "application/json"}, json.dumps({
            "data": [{"b64_json": B64_PNG}]
        }).encode()


def _dat_lai_combo() -> None:
    bo_cau_chi._states.clear()
    bo_han_muc._nghi.clear()
    bo_dinh_phien._map.clear()


@pytest.fixture
def moi_truong(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setenv("WEB2API_RL_LOI_XAC_THUC", "0")
    _dat_lai_combo()
    so = SoModule()
    mod_gia = ModuleGia("gia")
    mod_khong_anh = ModuleKhongAnh("khonganh")
    mod_chi_tao = ModuleChiTaoAnh("chitao")

    so.dang_chay["gia"] = mod_gia
    so.dang_chay["khonganh"] = mod_khong_anh
    so.dang_chay["chitao"] = mod_chi_tao

    so.tu_xa["gia"] = {"tien_to": "gia", "url": "http://mock"}
    so.tu_xa["khonganh"] = {"tien_to": "khonganh", "url": "http://mock"}
    so.tu_xa["chitao"] = {"tien_to": "chitao", "url": "http://mock"}

    with TestClient(app) as client:
        client.app.state.so = so
        yield client, mod_gia, mod_khong_anh, mod_chi_tao
    _dat_lai_combo()


def _khoa_khach(client, pham_vi: list[str]) -> dict:
    r = client.post("/api/khoa-khach", headers=QT, json={"nhan": "Khách thử", "pham_vi": pham_vi})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['khoa']['khoa']}"}


def _than_sua(model: str = "gia/anh-model", **tool) -> dict:
    return {
        "model": model,
        "input": [{"role": "user", "content": [
            {"type": "input_text", "text": "Thêm chiếc mũ đỏ"},
            {"type": "input_image", "image_url": DATA_URL_PNG},
        ]}],
        "tools": [{"type": "image_generation", **tool}],
    }


def _than_tao(model: str = "gia/anh-model", **tool) -> dict:
    return {"model": model, "input": "vẽ con mèo", "tools": [{"type": "image_generation", **tool}]}


def _doc_sse(text: str) -> list[tuple[str, dict]]:
    ra = []
    for khoi in text.strip().split("\n\n"):
        ten, du_lieu = None, None
        for d in khoi.split("\n"):
            if d.startswith("event: "):
                ten = d[len("event: "):].strip()
            elif d.startswith("data: "):
                du_lieu = json.loads(d[len("data: "):].strip())
        if ten and du_lieu is not None:
            ra.append((ten, du_lieu))
    return ra


# ───────────────────────────── Xác thực & Leo thang quyền ─────────────────────────────

def test_responses_anh_khong_khoa_401(moi_truong):
    """Không có khoá xác thực gọi POST /v1/responses → 401 invalid_api_key (cả nhánh tạo lẫn nhánh sửa)."""
    client, mod_gia, _, _ = moi_truong
    for than in (_than_tao(), _than_sua()):
        r = client.post("/v1/responses", json=than)
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "invalid_api_key"
    assert mod_gia.da_nhan_anh == [] and mod_gia.da_nhan_sua == []


def test_responses_anh_khoa_khach_thieu_pham_vi_403(moi_truong):
    """Khoá khách không có phạm vi ảnh nào (chỉ có 'chat') gọi /v1/responses → 403 insufficient_scope."""
    client, _, _, _ = moi_truong
    r = client.post("/v1/responses", headers=_khoa_khach(client, ["chat"]), json=_than_tao())
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "insufficient_scope"


def test_responses_anh_khoa_khach_co_pham_vi_200(moi_truong):
    """Khoá khách có phạm vi 'anh' gọi /v1/responses (không ảnh đầu vào) → 200 thành công."""
    client, _, _, _ = moi_truong
    r = client.post("/v1/responses", headers=_khoa_khach(client, ["anh"]), json=_than_tao())
    assert r.status_code == 200
    assert r.json()["status"] == "completed"


def test_responses_sua_anh_khoa_chi_anh_403_nhu_images_edits(moi_truong):
    """Khoá «anh» kèm input_image là SỬA ảnh → 403 như /v1/images/edits; module không bị gọi."""
    client, mod_gia, _, _ = moi_truong
    h = _khoa_khach(client, ["anh"])
    r = client.post("/v1/responses", headers=h, json=_than_sua())
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "insufficient_scope"
    assert "anh_sua" in r.json()["error"]["message"]
    assert mod_gia.da_nhan_sua == [] and mod_gia.da_nhan_anh == []
    # Đối chứng: cùng khoá gọi đường chuẩn cũng bị 403 — hai đường cùng một luật.
    r_chuan = client.post("/v1/images/edits", headers=h, data={"model": "gia/anh-model", "prompt": "x"},
                          files={"image": ("a.png", PNG_1X1, "image/png")})
    assert r_chuan.status_code == 403


def test_responses_sua_anh_khoa_chi_anh_403_truoc_khi_tra_model(moi_truong):
    """Khoá hẹp không dò được model nào tồn tại: model lạ vẫn là 403 (không phải 404)."""
    client, _, _, _ = moi_truong
    r = client.post("/v1/responses", headers=_khoa_khach(client, ["anh"]), json=_than_sua("khong-co/x"))
    assert r.status_code == 403


def test_responses_sua_anh_khoa_anh_sua_200(moi_truong):
    """Khoá «anh_sua» kèm input_image → 200, module.anh_sua được gọi."""
    client, mod_gia, _, _ = moi_truong
    r = client.post("/v1/responses", headers=_khoa_khach(client, ["anh_sua"]), json=_than_sua())
    assert r.status_code == 200, r.text
    assert len(mod_gia.da_nhan_sua) == 1 and mod_gia.da_nhan_anh == []


def test_responses_tao_anh_khoa_chi_anh_sua_403(moi_truong):
    """Khoá chỉ «anh_sua», không ảnh đầu vào là TẠO ảnh → 403 như /v1/images/generations."""
    client, mod_gia, _, _ = moi_truong
    h = _khoa_khach(client, ["anh_sua"])
    r = client.post("/v1/responses", headers=h, json=_than_tao())
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "insufficient_scope"
    assert mod_gia.da_nhan_anh == []
    assert client.post("/v1/images/generations", headers=h, json=_than_tao()).status_code == 403


# ───────────────────────────── Tạo ảnh không stream ─────────────────────────────

def test_responses_anh_khong_stream_dung_hinh_dang(moi_truong):
    """Phản hồi không stream trả đúng cấu trúc chuẩn Responses API; thuộc tính ảnh lấy từ KẾT QUẢ của module."""
    client, mod_gia, _, _ = moi_truong
    mod_gia.tra_them = {"size": "1024x1024", "quality": "high", "output_format": "png", "background": "opaque"}
    payload = {
        "model": "gia/anh-model",
        "input": "Vẽ chú cá voi xanh",
        "tools": [{
            "type": "image_generation",
            "size": "1024x1024",
            "quality": "hd",
            "output_format": "png",
            "background": "transparent",
        }],
    }
    r = client.post("/v1/responses", headers=QT, json=payload)
    assert r.status_code == 200, r.text
    assert r.headers.get("x-module-rieng") == "co"  # tiêu đề của module không bị bỏ
    data = r.json()

    assert data["id"].startswith("resp_")
    assert data["object"] == "response"
    assert isinstance(data["created_at"], int)
    assert data["status"] == "completed"
    assert data["model"] == "gia/anh-model"
    assert data["usage"] == {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    assert len(data["output"]) == 1
    item = data["output"][0]
    assert item["id"].startswith("ig_")
    assert item["type"] == "image_generation_call"
    assert item["status"] == "completed"
    assert item["result"] == B64_PNG
    assert item["revised_prompt"] == "Ảnh đã sửa 1"
    # Lấy từ phản hồi module, KHÔNG lặp tham số khách ("hd", "transparent").
    assert item["size"] == "1024x1024"
    assert item["quality"] == "high"
    assert item["output_format"] == "png"
    assert item["background"] == "opaque"

    # Thân gửi tới module: hình /v1/images/generations — model ĐẦY ĐỦ như đường chuẩn (module tự bỏ tiền tố).
    assert len(mod_gia.da_nhan_anh) == 1
    gui = mod_gia.da_nhan_anh[0]
    assert gui["model"] == "gia/anh-model"
    assert gui["prompt"] == "Vẽ chú cá voi xanh"
    assert gui["response_format"] == "b64_json"
    assert "n" not in gui  # khách không nêu n → module dùng mặc định, lõi không tự đặt
    assert gui["size"] == "1024x1024"
    assert gui["quality"] == "hd"
    assert gui["output_format"] == "png"
    assert gui["background"] == "transparent"


def test_responses_output_format_khong_lap_tham_so_khach(moi_truong):
    """Khách xin webp, module không nêu định dạng mà trả PNG → output_format suy từ byte ảnh («png»), không «webp»;
    size/quality khách gửi không được lặp lại như thể là kết quả."""
    client, _, _, _ = moi_truong
    r = client.post("/v1/responses", headers=QT, json=_than_tao(output_format="webp", size="512x512", quality="low"))
    assert r.status_code == 200, r.text
    item = r.json()["output"][0]
    assert item["output_format"] == "png"
    assert "size" not in item and "quality" not in item


def test_responses_anh_n_bang_2_hai_output(moi_truong):
    """Khi tool chỉ định n=2 → phản hồi có 2 phần tử trong output."""
    client, mod_gia, _, _ = moi_truong
    r = client.post("/v1/responses", headers=QT, json=_than_tao(n=2))
    assert r.status_code == 200
    data = r.json()
    assert len(data["output"]) == 2
    assert data["output"][0]["id"] != data["output"][1]["id"]
    assert data["output"][0]["result"] == B64_PNG
    assert data["output"][1]["result"] == B64_PNG
    assert mod_gia.da_nhan_anh[0]["n"] == 2


def test_responses_anh_ghep_prompt_input_text_va_instructions(moi_truong):
    """Prompt ghép từ instructions ở đầu và mọi input_text của lượt người dùng."""
    client, mod_gia, _, _ = moi_truong
    payload = {
        "model": "gia/anh-model",
        "instructions": "Vẽ theo phong cách màu nước cổ điển",
        "input": [
            {"role": "user", "content": [{"type": "input_text", "text": "Một ngôi nhà nhỏ ven sông"}]},
            {"role": "assistant", "content": "Tôi hiểu rồi, tôi sẽ vẽ ngôi nhà."},
            {"role": "user", "content": [{"type": "input_text", "text": "Khói lam chiều từ mái bếp"}]},
        ],
        "tools": [{"type": "image_generation"}],
    }
    r = client.post("/v1/responses", headers=QT, json=payload)
    assert r.status_code == 200

    prompt_that = mod_gia.da_nhan_anh[-1]["prompt"]
    assert "Vẽ theo phong cách màu nước cổ điển" in prompt_that
    assert "Một ngôi nhà nhỏ ven sông" in prompt_that
    assert "Khói lam chiều từ mái bếp" in prompt_that
    # Lời của assistant không được lọt vào prompt
    assert "Tôi hiểu rồi" not in prompt_that
    # Instructions phải ở đầu
    assert prompt_that.startswith("Vẽ theo phong cách màu nước cổ điển")


def test_responses_vai_developer_system_vao_phan_chi_dan(moi_truong):
    """Tin vai developer/system là CHỈ DẪN (như `instructions`): ghép vào phần chỉ dẫn ở đầu prompt, theo thứ tự,
    trước lời người dùng — không bị bỏ im lặng."""
    client, mod_gia, _, _ = moi_truong
    payload = {
        "model": "gia/anh-model",
        "instructions": "CHI-DAN-1",
        "input": [
            {"role": "user", "content": "LOI-NGUOI-DUNG"},
            {"role": "developer", "content": [{"type": "input_text", "text": "CHI-DAN-2"}]},
            {"role": "system", "content": "CHI-DAN-3"},
        ],
        "tools": [{"type": "image_generation"}],
    }
    r = client.post("/v1/responses", headers=QT, json=payload)
    assert r.status_code == 200, r.text
    assert mod_gia.da_nhan_anh[-1]["prompt"] == "CHI-DAN-1\n\nCHI-DAN-2\n\nCHI-DAN-3\n\nLOI-NGUOI-DUNG"


def test_responses_vai_la_400_khong_phan_chieu(moi_truong):
    """Vai không có trong chuẩn Responses → 400, không đoán nghĩa, không phản chiếu chuỗi khách."""
    client, mod_gia, _, _ = moi_truong
    vai_la = "vai-la-" + "z" * 300
    r = client.post("/v1/responses", headers=QT, json={
        "model": "gia/anh-model",
        "input": [{"role": vai_la, "content": "x"}, {"role": "user", "content": "vẽ"}],
        "tools": [{"type": "image_generation"}],
    })
    assert r.status_code == 400
    assert r.json()["error"]["type"] == "invalid_request_error"
    assert "z" * 200 not in r.text
    assert mod_gia.da_nhan_anh == []


# ───────────────────────────── Kiểm `n` / `size` ở biên ─────────────────────────────

#: `n` sai kiểu hoặc ngoài [1, N_ANH_TOI_DA]. Chuỗi "2" sai trong thân JSON (đặc tả OpenAI: số nguyên).
N_SAI = ["abc", [1], {"n": 1}, True, False, 0, -1, 11, 1000000, 1.5, "2"]


@pytest.mark.parametrize("n", N_SAI, ids=repr)
def test_responses_n_sai_400(moi_truong, n):
    client, mod_gia, _, _ = moi_truong
    for than in (_than_tao(n=n), _than_sua(n=n)):
        r = client.post("/v1/responses", headers=QT, json=than)
        assert r.status_code == 400, (n, r.status_code, r.text)
        assert r.json()["error"]["type"] == "invalid_request_error"
    assert mod_gia.da_nhan_anh == [] and mod_gia.da_nhan_sua == []


@pytest.mark.parametrize("n", N_SAI, ids=repr)
def test_images_generations_n_sai_400_cung_mot_ham_kiem(moi_truong, n):
    """/v1/images/generations cùng lớp (trước đây chuyển `n` nguyên văn, không trần) → cùng hàm kiểm, cùng 400."""
    client, mod_gia, _, _ = moi_truong
    r = client.post("/v1/images/generations", headers=QT, json={"model": "gia/anh-model", "prompt": "x", "n": n})
    assert r.status_code == 400, (n, r.status_code, r.text)
    assert r.json()["error"]["type"] == "invalid_request_error"
    assert mod_gia.da_nhan_anh == []


@pytest.mark.parametrize("n", ["abc", "0", "11", "-1", "1.5", "²"])
def test_images_edits_n_sai_400(moi_truong, n):
    """/v1/images/edits: `n` là trường form (luôn chuỗi) → nhận chuỗi chữ số ASCII trong trần, còn lại 400."""
    client, mod_gia, _, _ = moi_truong
    r = client.post("/v1/images/edits", headers=QT, data={"model": "gia/anh-model", "prompt": "x", "n": n},
                    files={"image": ("a.png", PNG_1X1, "image/png")})
    assert r.status_code == 400, (n, r.text)
    assert mod_gia.da_nhan_sua == []


def test_n_hop_le_khong_doi_hanh_vi(moi_truong):
    """Giá trị hợp lệ đi qua như cũ: n=1..10 (số nguyên), n=null/thiếu → module dùng mặc định; form n="2" giữ chuỗi."""
    client, mod_gia, _, _ = moi_truong
    for n in (1, 10):
        assert client.post("/v1/images/generations", headers=QT,
                           json={"model": "gia/anh-model", "prompt": "x", "n": n}).status_code == 200
        assert mod_gia.da_nhan_anh[-1]["n"] == n
        assert client.post("/v1/responses", headers=QT, json=_than_tao(n=n)).status_code == 200
        assert mod_gia.da_nhan_anh[-1]["n"] == n
    assert client.post("/v1/images/generations", headers=QT,
                       json={"model": "gia/anh-model", "prompt": "x", "n": None}).status_code == 200
    assert mod_gia.da_nhan_anh[-1]["n"] is None  # thân chuẩn chuyển nguyên văn như trước
    assert client.post("/v1/responses", headers=QT, json=_than_tao(n=None)).status_code == 200
    assert "n" not in mod_gia.da_nhan_anh[-1]
    r = client.post("/v1/images/edits", headers=QT, data={"model": "gia/anh-model", "prompt": "x", "n": "2"},
                    files={"image": ("a.png", PNG_1X1, "image/png")})
    assert r.status_code == 200, r.text
    assert mod_gia.da_nhan_sua[-1].truong["n"] == "2"
    assert client.post("/v1/responses", headers=QT, json=_than_sua(n=3)).status_code == 200
    assert mod_gia.da_nhan_sua[-1].truong["n"] == "3"


@pytest.mark.parametrize("truong", ["size", "quality", "background", "output_format"])
@pytest.mark.parametrize("gia_tri", [{"w": 1}, 123, ["1024x1024"], True])
def test_responses_tham_so_chuoi_sai_kieu_400(moi_truong, truong, gia_tri):
    """Tham số chuyển sang module phải là chuỗi — đối tượng/số/danh sách không được `str()` nguyên sang module."""
    client, mod_gia, _, _ = moi_truong
    for than in (_than_tao(**{truong: gia_tri}), _than_sua(**{truong: gia_tri})):
        r = client.post("/v1/responses", headers=QT, json=than)
        assert r.status_code == 400, r.text
        assert r.json()["error"]["type"] == "invalid_request_error"
    assert mod_gia.da_nhan_anh == [] and mod_gia.da_nhan_sua == []


# ───────────────────────────── Có input_image (gọi anh_sua) ─────────────────────────────

def test_responses_anh_co_input_image_goi_anh_sua(moi_truong):
    """Có input_image (data URL base64) → lõi gọi module.anh_sua(yc) với đúng byte đã giải mã và đủ tham số."""
    client, mod_gia, _, _ = moi_truong
    r = client.post("/v1/responses", headers=QT, json=_than_sua(
        size="512x512", quality="high", background="transparent", output_format="webp"))
    assert r.status_code == 200
    assert len(mod_gia.da_nhan_sua) == 1
    assert len(mod_gia.da_nhan_anh) == 0

    yc = mod_gia.da_nhan_sua[0]
    assert isinstance(yc, YeuCauDaPhan)
    assert yc.truong["model"] == "gia/anh-model"  # như /v1/images/edits: model đầy đủ
    assert yc.truong["prompt"] == "Thêm chiếc mũ đỏ"
    assert yc.truong["size"] == "512x512"
    assert yc.truong["quality"] == "high"
    assert yc.truong["background"] == "transparent"
    assert yc.truong["output_format"] == "webp"
    tep_anh = yc.tep_ten("image")
    assert len(tep_anh) == 1
    assert tep_anh[0].content_type == "image/png"
    assert tep_anh[0].noi_dung == PNG_1X1


def test_responses_anh_input_image_dinh_dang_jpeg_va_webp(moi_truong):
    """Hỗ trợ ảnh image/jpeg và image/webp qua data URL."""
    client, mod_gia, _, _ = moi_truong
    for data_url, mime in ((DATA_URL_JPEG, "image/jpeg"), (DATA_URL_WEBP, "image/webp")):
        r = client.post("/v1/responses", headers=QT, json={
            "model": "gia/anh-model",
            "input": [
                {"role": "user", "content": [
                    {"type": "input_text", "text": "Sửa màu"},
                    {"type": "input_image", "image_url": data_url},
                ]}
            ],
            "tools": [{"type": "image_generation"}],
        })
        assert r.status_code == 200
        yc = mod_gia.da_nhan_sua[-1]
        assert yc.tep_ten("image")[0].content_type == mime


def test_responses_anh_url_ngoai_400_chong_ssrf(moi_truong):
    """URL ảnh ngoài (http/https) bị từ chối 400 unsupported_image_url để chống SSRF."""
    client, _, _, _ = moi_truong
    for url in ("http://example.com/anh.png", "https://api.domain.internal/image.jpg"):
        r = client.post("/v1/responses", headers=QT, json={
            "model": "gia/anh-model",
            "input": [
                {"role": "user", "content": [
                    {"type": "input_text", "text": "Sửa ảnh"},
                    {"type": "input_image", "image_url": url},
                ]}
            ],
            "tools": [{"type": "image_generation"}],
        })
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "unsupported_image_url"


def test_responses_anh_dinh_dang_anh_khong_hop_le_400(moi_truong):
    """Data URL với định dạng không hỗ trợ (gif, svg) hoặc base64 hỏng → 400 unsupported_image_url."""
    client, _, _, _ = moi_truong
    # Mime không hỗ trợ
    r = client.post("/v1/responses", headers=QT, json={
        "model": "gia/anh-model",
        "input": [
            {"role": "user", "content": [
                {"type": "input_text", "text": "Sửa"},
                {"type": "input_image", "image_url": "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"},
            ]}
        ],
        "tools": [{"type": "image_generation"}],
    })
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "unsupported_image_url"

    # Base64 hỏng
    r_hong = client.post("/v1/responses", headers=QT, json={
        "model": "gia/anh-model",
        "input": [
            {"role": "user", "content": [
                {"type": "input_text", "text": "Sửa"},
                {"type": "input_image", "image_url": "data:image/png;base64,!!!hong!!!"},
            ]}
        ],
        "tools": [{"type": "image_generation"}],
    })
    assert r_hong.status_code == 400
    assert r_hong.json()["error"]["code"] == "unsupported_image_url"


def test_responses_mime_rat_dai_khong_phan_chieu(moi_truong):
    """MIME do khách gửi dài hàng trăm KB → 400 ngắn gọn, không phản chiếu chuỗi đó."""
    client, _, _, _ = moi_truong
    mime_dai = "image/" + "x" * 300_000
    r = client.post("/v1/responses", headers=QT, json={
        "model": "gia/anh-model",
        "input": [{"role": "user", "content": [
            {"type": "input_text", "text": "Sửa"},
            {"type": "input_image", "image_url": f"data:{mime_dai};base64,{B64_PNG}"},
        ]}],
        "tools": [{"type": "image_generation"}],
    })
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "unsupported_image_url"
    assert len(r.content) < 2000
    assert "x" * 200 not in r.text


# ───────────────────────────── Kiểm tra công cụ & Khả năng module ─────────────────────────────

def test_responses_anh_cong_cu_khac_hoac_thieu_cong_cu_400(moi_truong):
    """Không có công cụ image_generation hoặc có công cụ khác → 400 unsupported_tool, không phản chiếu input."""
    client, _, _, _ = moi_truong
    cac_tools_sai = [
        [],
        [{"type": "code_interpreter"}],
        [{"type": "web_search"}],
        [{"type": "image_generation"}, {"type": "code_interpreter"}],
    ]
    for tools in cac_tools_sai:
        r = client.post("/v1/responses", headers=QT, json={
            "model": "gia/anh-model",
            "input": "vẽ ảnh",
            "tools": tools,
        })
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "unsupported_tool"
        assert r.json()["error"]["type"] == "invalid_request_error"
        # Không phản chiếu giá trị đầu vào của khách
        assert "code_interpreter" not in r.text
        assert "web_search" not in r.text

    # Không có trường tools
    r_thieu = client.post("/v1/responses", headers=QT, json={
        "model": "gia/anh-model",
        "input": "vẽ ảnh",
    })
    assert r_thieu.status_code == 400
    assert r_thieu.json()["error"]["code"] == "unsupported_tool"


def test_responses_anh_module_khong_co_anh_501(moi_truong):
    """Module không có phương thức anh → 501 capability_not_supported."""
    client, _, _, _ = moi_truong
    r = client.post("/v1/responses", headers=QT, json=_than_tao("khonganh/chat-model"))
    assert r.status_code == 501
    assert r.json()["error"]["code"] == "capability_not_supported"


def test_responses_anh_module_khong_co_anh_sua_501(moi_truong):
    """Module không có phương thức anh_sua khi có input_image → 501 capability_not_supported."""
    client, _, _, _ = moi_truong
    r = client.post("/v1/responses", headers=QT, json=_than_sua("chitao/anh-model"))
    assert r.status_code == 501
    assert r.json()["error"]["code"] == "capability_not_supported"


def test_responses_anh_module_tra_url_502(moi_truong):
    """Module trả URL thay vì b64_json → 502 upstream_error."""
    client, mod_gia, _, _ = moi_truong
    mod_gia.tra_url = True
    r = client.post("/v1/responses", headers=QT, json=_than_tao())
    assert r.status_code == 502
    assert r.json()["error"]["code"] == "upstream_error"
    assert "URL" in r.json()["error"]["message"]


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("co_anh", [False, True])
def test_responses_loi_module_chuyen_nguyen_ca_retry_after(moi_truong, stream, co_anh):
    """Lỗi của module (429 kèm Retry-After) chuyển NGUYÊN như /v1/images/*: mã, thân, tiêu đề — không thay bằng
    câu chung, không mất Retry-After."""
    client, mod_gia, _, _ = moi_truong
    than_loi = {"error": {"message": "Hết hạn mức, thử lại sau", "type": "rate_limit_error", "code": "het_han_muc"}}
    mod_gia.loi_tra_ma = (429, {"content-type": "application/json", "retry-after": "17"},
                          json.dumps(than_loi).encode())
    than = _than_sua() if co_anh else _than_tao()
    r = client.post("/v1/responses", headers=QT, json={**than, "stream": stream})
    assert r.status_code == 429
    assert r.headers.get("retry-after") == "17"
    assert r.json() == than_loi
    duong = "/v1/images/edits" if co_anh else "/v1/images/generations"
    if co_anh:
        r_chuan = client.post(duong, headers=QT, data={"model": "gia/anh-model", "prompt": "x"},
                              files={"image": ("a.png", PNG_1X1, "image/png")})
    else:
        r_chuan = client.post(duong, headers=QT, json={"model": "gia/anh-model", "prompt": "x"})
    assert (r_chuan.status_code, r_chuan.headers.get("retry-after"), r_chuan.json()) == (429, "17", than_loi)


# ───────────────────────────── Combo ─────────────────────────────

def test_responses_qua_combo(moi_truong):
    """model `combo/<tên>` đi qua đường này bằng đúng cơ chế combo của /v1/images/generations: bước nhận model
    đầy đủ của bước, tiêu đề định tuyến của combo không bị bỏ; combo không có sửa ảnh → 501."""
    client, mod_gia, _, _ = moi_truong
    r_put = client.put("/api/combo/anh-du-phong", headers=QT,
                       json={"buoc": [{"model": "gia/anh-model", "bat": True}]})
    assert r_put.status_code == 200, r_put.text

    r = client.post("/v1/responses", headers=QT, json=_than_tao("combo/anh-du-phong"))
    assert r.status_code == 200, r.text
    assert r.json()["model"] == "combo/anh-du-phong"
    assert r.json()["output"][0]["result"] == B64_PNG
    assert mod_gia.da_nhan_anh[-1]["model"] == "gia/anh-model"
    assert r.headers.get("x-web2api-route") == "gia/anh-model"
    assert r.headers.get("x-web2api-combo") == "anh-du-phong"

    r_sua = client.post("/v1/responses", headers=QT, json=_than_sua("combo/anh-du-phong"))
    assert r_sua.status_code == 501
    assert r_sua.json()["error"]["code"] == "capability_not_supported"


# ───────────────────────────── Stream SSE ─────────────────────────────

def test_responses_anh_stream_thu_tu_su_kien_va_sequence_number(moi_truong):
    """stream: true phát đúng chuỗi sự kiện SSE, sequence_number tăng dần, không có [DONE]."""
    client, _, _, _ = moi_truong
    r = client.post("/v1/responses", headers=QT, json={**_than_tao(), "stream": True})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert "[DONE]" not in r.text  # Chuẩn Responses KHÔNG gửi [DONE]

    su_kien = _doc_sse(r.text)
    thu_tu_mong_doi = [
        "response.created",
        "response.in_progress",
        "response.output_item.added",
        "response.image_generation_call.in_progress",
        "response.image_generation_call.generating",
        "response.output_item.done",
        "response.completed",
    ]
    assert [t for t, _ in su_kien] == thu_tu_mong_doi
    assert [d["sequence_number"] for _, d in su_kien] == list(range(len(thu_tu_mong_doi)))


def test_responses_anh_stream_loi_giua_chung_phat_su_kien_error(moi_truong, monkeypatch):
    """Lỗi GIỮA stream (sau khi đã phát 200) → sự kiện `error` đúng khuôn Responses (type, sequence_number, code,
    message), không lộ chữ của ngoại lệ, rồi dừng — không NameError, không đứt ngang."""
    client, _, _, _ = moi_truong

    class JsonHongLanBa:
        loads = staticmethod(json.loads)

        def __init__(self):
            self.dem = 0

        def dumps(self, *a, **k):
            self.dem += 1
            if self.dem == 3:
                raise ValueError("chuoi-ngoai-le-noi-bo")
            return json.dumps(*a, **k)

    monkeypatch.setattr(tuyen, "json", JsonHongLanBa())
    r = client.post("/v1/responses", headers=QT, json={**_than_tao(), "stream": True})
    assert r.status_code == 200
    su_kien = _doc_sse(r.text)
    assert [t for t, _ in su_kien] == ["response.created", "response.in_progress", "error"]
    loi = su_kien[-1][1]
    assert loi["type"] == "error"
    assert loi["sequence_number"] == 2
    assert loi["code"] == "stream_error"
    assert isinstance(loi["message"], str) and loi["message"]
    assert "chuoi-ngoai-le-noi-bo" not in r.text


def test_responses_anh_stream_loi_truoc_byte_dau_la_ma_that(moi_truong):
    """Module lỗi trước byte đầu (vd 401/404) → trả mã HTTP lỗi thật, không phải 200 rỗng."""
    client, mod_gia, _, _ = moi_truong
    mod_gia.loi_tra_ma = (404, {"content-type": "application/json"}, b'{"error": {"code": "model_not_found"}}')
    r = client.post("/v1/responses", headers=QT, json={**_than_tao(), "stream": True})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "model_not_found"


# ───────────────────────────── Trần thân & Bảo mật thông tin ─────────────────────────────

def test_responses_anh_tran_than_413(moi_truong, monkeypatch):
    """Thân JSON vượt trần → 413 request_too_large."""
    client, _, _, _ = moi_truong
    monkeypatch.setenv("WEB2API_THAN_TOI_DA_MB", "0.001")  # ~1 KB
    payload = {
        "model": "gia/anh-model",
        "input": "A" * 2000,
        "tools": [{"type": "image_generation"}],
    }
    r = client.post("/v1/responses", headers=QT, json=payload)
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "request_too_large"


def test_responses_anh_tran_tep_giai_ma_413(moi_truong, monkeypatch):
    """Ảnh base64 giải mã vượt trần WEB2API_TEP_TOI_DA_MB → 413 request_too_large."""
    client, _, _, _ = moi_truong
    monkeypatch.setenv("WEB2API_TEP_TOI_DA_MB", "0.001")  # ~1 KB
    du_lieu_lon = base64.b64encode(b"X" * 3000).decode("ascii")
    payload = {
        "model": "gia/anh-model",
        "input": [
            {"role": "user", "content": [
                {"type": "input_text", "text": "Sửa"},
                {"type": "input_image", "image_url": f"data:image/png;base64,{du_lieu_lon}"},
            ]}
        ],
        "tools": [{"type": "image_generation"}],
    }
    r = client.post("/v1/responses", headers=QT, json=payload)
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "request_too_large"


def test_responses_anh_loi_module_khong_lot_chuoi_bi_mat(moi_truong):
    """Ngoại lệ của module chứa chuỗi bí mật → phản hồi không lọt chuỗi bí mật."""
    client, mod_gia, _, _ = moi_truong
    mod_gia.loi_nem = RuntimeError(f"Lỗi bí mật nội bộ: {CHUOI_BI_MAT_GIA}")
    r = client.post("/v1/responses", headers=QT, json=_than_tao())
    assert r.status_code == 502
    assert r.json()["error"]["code"] == "upstream_error"
    # Quét toàn bộ phản hồi: không được chứa chuỗi bí mật
    assert CHUOI_BI_MAT_GIA not in r.text
    for ten, gtri in r.headers.items():
        assert CHUOI_BI_MAT_GIA not in ten and CHUOI_BI_MAT_GIA not in gtri
