"""Khám phá module từ CẤU TRÚC thư mục — không từ danh sách gõ tay (KE-HOACH-MODULE.md mục 8).

Thêm module = có một thư mục `<id>/` chứa `manifest.json` + `module.py` ở MỘT trong hai nguồn:
  - `app/modules/*/`             đi kèm ảnh, chỉ-đọc (hiện rỗng — lõi nhẹ);
  - `<WEB2API_DATA>/modules/*/`  cài lúc chạy (từ git), ghi được.
Không sửa dòng nào của lõi. Bảng khai riêng cho từng module là "thêm một danh sách nữa" —
hỏng đúng kiểu `_PREFETCH_MAP` của c2a (biết 3/109 công cụ).

MODULE NHIỀU TỆP: lõi nạp `module.py` như MỘT GÓI (tên gói duy nhất theo id + vân tay). Tệp anh em trong thư mục module
import bằng IMPORT TƯƠNG ĐỐI — `from .client import X` — KHÔNG `from modules.<id>.client import X` (đường đó chỉ có khi chạy
trong kho, lúc chạy thật module nằm ở <WEB2API_DATA>/modules/<id>/ nên vỡ «No module named 'modules'»).

HỢP ĐỒNG lớp `Module` trong module.py:
    Module(manifest: dict)                 — KHÔNG mở kết nối, không đọc mạng (được tạo thử khi nạp)
    nap(cau_hinh: dict) -> None            — nạp cấu hình ĐÃ KIỂM (khởi động/cài xong), không gọi mạng
    async cai(cau_hinh) -> dict            — kiểm cấu hình bằng cách gọi thật:
                                             {ok, ket_qua_tung_khoa: [...], loi, cau_hinh_luu (chỉ khi ok)}
                                             `cau_hinh_luu` là cấu hình sạch để lưu — lõi KHÔNG trả nó ra API
    async chat(body: dict, stream: bool)   -> (mã HTTP, headers, bytes | async-iterator[bytes])
                                             — bắt buộc khi `cung_cap` có "chat"; tuỳ chọn nếu không (thiếu thì
                                             POST /v1/chat/completions trả 501 capability_not_supported)
    async models() -> list[dict]           — id dạng "<tien_to>/<tên>"; bắt buộc khi `cung_cap` có khả năng định
                                             tuyến theo `model` (KHA_NANG_CO_MODEL), tuỳ chọn nếu không
    async suc_khoe() -> dict               — không tốn token, không lộ khoá; không được đổi bộ đếm dùng
                                             {ok, base_url?, khoa: [mục mỗi khoá, THEO THỨ TỰ ĐÃ LƯU]}; mục mỗi khoá:
                                             khoa (dạng che …abcd), ok, do_tre_ms, so_model, loi, dang_nghi_den, và
                                             TUỲ CHỌN (thiếu thì giao diện ẩn phần đó, không lỗi):
                                               kha_nang  {mã: {trang_thai, ma?, thu_lai_luc?}}
                                               thu_tu    số thứ tự trong danh sách khoá (từ 1)
                                               dang_dung true ở khoá phục vụ yêu cầu GẦN NHẤT
                                               dung_cuoi / lan_429_cuoi  ISO hoặc null; so_429 số lần 429
                                               han_muc   [{ten: mã việc ("chat", "anh"…), da_dung: số lượt thành công,
                                                           toi_da: số hoặc null, hoi_luc: ISO hoặc null,
                                                           trang_thai: "dung_duoc" | "het_han_muc" | "bi_tu_choi" |
                                                                       "chua_ro", ma: mã HTTP hoặc null}]
                                             `toi_da` CHỈ điền khi nhà cung cấp cho biết trần thật, và khi ấy `da_dung` phải
                                             là mức đã dùng CÙNG KỲ với trần đó (cũng do nhà cung cấp báo) — giao diện vẽ thanh
                                             «còn toi_da − da_dung» khi có nó, không có thì chỉ hiện «đã dùng N lượt» + trạng
                                             thái; không bịa trần. Bộ chuyển tiếp dùng chung (app/chuyen_tiep.py) điền sẵn mọi
                                             trường này từ bộ đếm trong bộ nhớ (kể từ lần khởi động / lưu cấu hình gần nhất),
                                             `toi_da` luôn null.
    async dung() -> None                   — đóng kết nối

PHƯƠNG THỨC TUỲ CHỌN — có thì lõi mở đường /v1 tương ứng; không có thì lõi trả 501 (không vỡ):
    async anh(body: dict)                  -> (mã, headers, bytes)   POST /v1/images/generations (thân OpenAI)
    async video_tao(body: dict)            -> (mã, headers, bytes)   POST /v1/videos — JSON video OpenAI, "id" là
                                              mã CỦA MODULE (chỉ [A-Za-z0-9._-], không «:» «/»); lõi trả ra ngoài
                                              dạng "<tien_to>:<mã>" để biết module nào xử lý lượt hỏi sau
    async video_trang_thai(ma: str)        -> (mã, headers, bytes)   GET /v1/videos/{id} (lõi đã bỏ "<tien_to>:")
    async video_noi_dung(ma: str)          -> (mã, headers, bytes | async-iterator[bytes])
                                              GET /v1/videos/{id}/content — tệp lớn thì trả iterator (stream)
    Module có video phải có đủ cả ba phương thức video_*. Mã do khách gửi là ĐẦU VÀO KHÔNG TIN ĐƯỢC: module
    phải kiểm nó trước khi dùng để gọi dịch vụ kèm khoá.
    POST /v1/videos nhận cả multipart/form-data (SDK openai gửi videos.create như vậy): lõi đổi các trường chữ
    thành thân JSON cho video_tao (giá trị vẫn là CHUỖI, module tự ép kiểu); có phần TỆP (vd input_reference) thì
    lõi trả 415 — chưa có hợp đồng đưa tệp cho video_tao.
    async noi(body: dict)                  -> (mã, headers, bytes | async-iterator[bytes])
                                              POST /v1/audio/speech — thân JSON OpenAI (model, input, voice,
                                              response_format, speed, stream…); trả âm thanh nhị phân, module đặt
                                              content-type; iterator thì lõi stream từng khúc, không gom
    async nghe(yc: YeuCauDaPhan)           -> (mã, headers, bytes)   POST /v1/audio/transcriptions (multipart);
                                              lõi bảo đảm có đúng MỘT tệp "file"
    async anh_sua(yc: YeuCauDaPhan)        -> (mã, headers, bytes)   POST /v1/images/edits (multipart); lõi bảo
                                              đảm có ≥ 1 tệp "image" (khách gửi "image" hay "image[]" đều thành
                                              "image"), ≤ 1 tệp "mask", "prompt" là chuỗi khác rỗng
    Phương thức cho khả năng "thiet_bi":
      async thuc_the(loc: dict | None) -> list[dict]
        mỗi thực thể: {id: "<tien_to>.<tên>", ten: str, loai: str (mã MỞ: den, cong_tac, cam_bien, camera, may_chu,
        cong_mang… mã lạ hiện nguyên mã), khu: str (tuỳ chọn), thuoc_tinh: dict (tự do), tinh_nang: list[str] (list mã
        hành động hỗ trợ, vd ["bat","tat","do_sang"]), rui_ro_mac_dinh: "doc" | "ghi" | "nguy_hiem"}.
        Lõi kiểm hình dạng: id sai tiền tố / trùng → bỏ thực thể đó và ghi log (không vỡ cả danh sách).
      async trang_thai(ids: list[str]) -> dict[str, dict]
        {id: {gia_tri, don_vi?, luc (ISO), hop_le: bool}}
      async thuc_hien(id: str, hanh_dong: str, tham_so: dict) -> dict
        {ok: bool, trang_thai_moi?, loi?}; hanh_dong phải nằm trong tinh_nang của thực thể (lõi kiểm trước khi gọi
        module → 400 nếu không).
      async su_kien() -> async iterator[dict] (tuỳ chọn):
        mỗi sự kiện {id: str, loai: "trang_thai" | …(mã mở), gia_tri, luc: str}
      Định tuyến: id thực thể bắt đầu bằng "<tien_to>." → module có tiền tố đó.
    Phương thức cho khả năng "nhung":
      async nhung(body: dict) -> (mã, headers, bytes)   POST /v1/embeddings — thân OpenAI (model, input chuỗi
                                                        hoặc list) → data[].embedding; kiểm phạm vi "nhung".
    Phương thức cho khả năng "xep_hang":
      async xep_hang(body: dict) -> (mã, headers, bytes) POST /v1/rerank — thân chuẩn xếp hạng (model, query,
                                                        documents list chuỗi hoặc {text}, top_n) → results[];
                                                        kiểm phạm vi "xep_hang".
    Phương thức cho khả năng "tim_kiem":
      async tim_kiem(yc: dict) -> dict                  GET /api/tim-kiem/nguon và POST /api/tim-kiem;
                                                        yc = {chuoi?, loc?, bat_tat?, cay?, muc_luc?, chi_muc?, so_luong, ngu_canh?};
                                                        kết quả {muc: [{id, diem, doan, tai_lieu?, vi_tri?, truong?}], tong?};
                                                        mọi trường là mã mở; kiểm phạm vi "tim_kiem".
      async muc_luc(yc: dict) -> dict                   POST /api/tim-kiem/muc-luc (tuỳ chọn);
                                                        yc = {tai_lieu, do_sau?};
                                                        kết quả {tai_lieu, tieu_de?, muc: [{id, tieu_de, cap, vi_tri?, con: [...]}]};
                                                        cây đề mục: chương → mục → tiểu mục; trần độ sâu 6, trần số nút 2000;
                                                        bỏ nút sai không vỡ cây; kiểm phạm vi "tim_kiem".
    Phương thức cho khả năng "trinh_duyet":
      async trinh_duyet_mo(ho_so: str, loai: str) -> dict
        {ho_so: str, dich: "http://[w2a:<bí-mật>@]host:port", loai: str} — mở/khôi phục hồ sơ, trả địa chỉ nội bộ
        noVNC; lõi tách userinfo thành Basic auth, kiểm SSRF theo hostname, ghim IP lúc mở, giữ dich chỉ trong RAM;
        mở lại mà dich đổi máy → 502. Lõi phục vụ noVNC ở /novnc/s/<phien>/ với CSP sandbox (nội dung không tin được).
      async trinh_duyet_dong(ho_so: str, giu_phien: bool) -> dict
        {ok: bool} — đóng; giu_phien=True (mặc định khi quản trị không nói) giữ thư mục hồ sơ, False thì xoá.
        Lõi thu hồi vé/phiên/WebSocket TRƯỚC khi gọi; module ném lỗi → 502 kèm da_thu_hoi, không nhúng nguyên văn lỗi.
      async trinh_duyet_ds() -> list[dict]
        list[{ho_so: str, ten?: str, loai: str, trang_thai: str, dang_mo?: bool, thu_cong?: bool, luc_mo?: str}] —
        liệt kê workspace; lõi chỉ trả đúng các trường này (trường khác như `dich` bị bỏ).
      trinh_duyet_mo nhận thêm `url` (tuỳ chọn, từ khoá) — trang đầu khi trình duyệt khởi động; lõi đã kiểm http(s),
        có tên máy, không userinfo, ≤ 2048 ký tự, và CHỈ truyền khi quản trị gửi (module cũ (ho_so, loai) vẫn chạy).
      Phương thức TUỲ CHỌN của khả năng "trinh_duyet" (thiếu → lõi trả 501, không tính là sai chuẩn):
      async trinh_duyet_tao(ten: str) -> dict
        {ho_so: str, ten: str} — tạo workspace: tên hiển thị (lõi đã kiểm 1..100 ký tự in được), thư mục hồ sơ ID
        NGẪU NHIÊN do dịch vụ sinh (không suy từ tên/email). Không mở trình duyệt. POST /api/trinh-duyet/{module}.
      Trường NHẬN cookie do module ĐÍCH tự khai trong manifest: phần tử `truong_cau_hinh` kiểu bí mật thêm
        "nhan_cookie": {"mien": "<tên miền chặt, ≥ 2 nhãn>"}. Lõi chỉ ghi cookie vào trường có khai báo này và chỉ với
        đúng tên miền đã khai (người gọi không chọn tên miền, không ghi được vào khoá API của module khác).
      async trinh_duyet_cookie(ho_so: str, mien: str) -> dict
        {cookie: str} — chuỗi «tên=giá trị; …» các cookie CÒN HẠN của tên miền `mien` (và tên miền con) trong hồ sơ.
        Lõi đã kiểm `mien` (chữ thường, ≥ 2 nhãn). Dùng ở POST /api/trinh-duyet/{module}/{ho_so}/cookie-sang-module:
        lõi đưa chuỗi vào trường BÍ MẬT của module đích qua đường cài có sẵn (cai() của module đích kiểm trước khi
        lưu). Module KHÔNG log giá trị cookie. Lỗi có cấu trúc: ném ngoại lệ có thuộc tính `ma` (int) — 400 (yêu cầu
        sai), 404 (hồ sơ không có/chưa có kho cookie), 409 (hồ sơ đang mở), 501 (loại hồ sơ không hỗ trợ) được lõi chuyển tiếp kèm câu
        của LÕI; mọi lỗi khác → 502 chỉ nêu tên kiểu lỗi (không nhúng nguyên văn — có thể chứa cookie).
    YeuCauDaPhan (app/yeu_cau_da_phan.py) do lõi đọc từ multipart — module KHÔNG tự phân tích thân:
      .truong: dict[str, str | list[str]]  trường chữ, tên bỏ hậu tố «[]»; gửi một lần và không có «[]» → str,
                                           có «[]» hoặc lặp → list[str] theo thứ tự gửi (đúng hình thân JSON
                                           của cùng tham số). "model" vẫn giữ "<tien_to>/<tên>" như chat.
      .tep: list[TepTai]                   TepTai(ten_truong, ten_tep, content_type, noi_dung: bytes);
      .tep_ten(ten) -> list[TepTai]        các tệp của một trường.
    Trần TỔNG thân multipart: WEB2API_TEP_TOI_DA_MB (mặc định 25) — vượt là 413, module không được gọi. Tệp tạm
    lõi đã đóng trước khi gọi module. Mọi thứ trong YeuCauDaPhan (kể cả ten_tep, content_type) là KHÁCH KHAI,
    KHÔNG TIN ĐƯỢC: không dùng ten_tep làm đường dẫn, tự kiểm định dạng trước khi gửi đi kèm khoá.
    Header trả về do module chọn và lõi chuyển nguyên (riêng content-length của thân bytes thì lõi tự tính):
    đừng chép nguyên header của nhà cung cấp — content-encoding/content-length sai khi httpx đã giải nén.
    phan_loai_loi(ma: int, loi: dict, boi_canh: dict) -> str | None
        — phân loại lỗi từ nhà cung cấp thượng nguồn về một trong các chuẩn loại lỗi trung lập (mã mở):
            "khoa_hong"        — khoá sai, hết hạn, bị thu hồi; lõi cho khoá nghỉ (1 giờ) và thử khoá kế.
            "het_han_muc"      — hết hạn mức / rate limit; lõi cho khoá nghỉ theo Retry-After và thử khoá kế.
            "khong_co_o_khoa"  — model hoặc khả năng không được cấp cho khoá này; chat: ghi sổ 6 giờ cho
                                 (khoá, model) và thử khoá kế; khả năng: nghỉ 24 giờ và thử khoá kế.
            "yeu_cau_sai"      — yêu cầu của khách không hợp lệ; lõi trả ngay, không đổi khoá, không phạt khoá.
            "may_chu_loi"      — lỗi phía máy chủ nhà cung cấp hoặc mạng (5xx); lõi thử thêm đúng một khoá.
            "chan_vung"        — máy chủ bị chặn vùng địa lý; lỗi của hạ tầng, lõi trả ngay, không phạt khoá.
            "khong_ro"         — chưa rõ lỗi do khoá hay do yêu cầu; lõi dùng so sánh giữa các khoá để phân xử.
        Mặc định khi module không cài hook hoặc trả về None:
            429           -> "het_han_muc"
            401, 403      -> "khoa_hong"
            500..599      -> "may_chu_loi"
            còn lại       -> "khong_ro"
        Mã lạ hoặc hook ném ngoại lệ -> coi là "khong_ro", không vỡ tiến trình.
        `loi` là từ điển lỗi chuẩn hoá {"error": {"message": ..., "type": ..., "code": ...}}; module được phép
        bổ sung gợi ý vào loi["error"]["message"] (lõi gọi hook ngay khi chuẩn hoá lỗi, trước khi trả cho khách).
        `boi_canh` chứa {"viec", "khoa", ...}. Lời giải thích khi khoá bị từ chối một khả năng tốn tiền thì module
        truyền cho bộ chuyển tiếp bằng tham số `goi_y_tu_choi_kha_nang` (một chuỗi), không qua hook.
    async phien_con_song(du_lieu: dict) -> bool
        — kiểm RẺ, không tốn token/lượt dùng (tuỳ chọn; thiếu thì lõi coi phiên là «chua_ro» và không kiểm).
    async lam_moi_phien(du_lieu: dict) -> dict | None
        — trả `du_lieu` mới (lõi lưu đè) hoặc None nếu không làm mới được (tuỳ chọn).
    Module lấy bí mật/phiên qua tay cầm do lõi trao (self.kho.phien_lay(tai_khoan), self.kho.doc(ten)), không tự mở tệp hay gọi qua id khác.

Manifest khai khả năng ở trường MỞ `cung_cap` (tuỳ chọn): danh sách mã như ["chat", "anh", "video"]; quy ước
cho các phương thức trên: "noi", "nghe", "anh_sua" (cùng tên phương thức). Lõi chỉ
kiểm MẪU (MA_KHA_NANG), không giữ danh sách mã hợp lệ. Định tuyến /v1 dựa vào phương thức CÓ THẬT trên thể
hiện, không dựa vào lời khai; lời khai dùng cho giao diện (tab/chế độ nào hiện — GET /api/tinh-nang).

`loai` cũng là mã MỞ cùng mẫu MA_KHA_NANG (mục `can` trộn hai loại mã: "ai" là loại, "chat" là khả năng).
Tuỳ chọn cho giao diện: `loai_ten` (tên hiển thị của loại, vd "AI"), `loai_thu_tu` (số nguyên, nhỏ đứng
trước), `muc` — tab con/mục của module, mỗi mục {"id", "nhan", "can": [mã…]}: mục chỉ hiện khi module đã
liên kết VÀ mọi mã trong `can` có ở ít nhất một module đã liên kết (rỗng = luôn hiện khi đã liên kết).

Mã module chạy TRONG tiến trình lõi (quyền như lõi). Được import `app.*` (vd app.chuyen_tiep,
app.dia_chi, app.nhat_ky để ghi kênh riêng qua `nhat_ky.ghi(kenh, muc, tom_tat, ...)`) và gói có sẵn trong ảnh;
gói thiếu khai ở `yeu_cau_goi` để lõi từ chối sớm, rõ ràng.
Module cần địa chỉ gọi lại (OAuth/webhook) gọi `app.dia_chi.dia_chi_hien_tai(request)`: trả về URL gốc
công khai (ưu tiên địa chỉ đã đặt, biến WEB2API_PUBLIC_URL, hoặc suy ra từ Host). Địa chỉ suy ra do KHÁCH gửi:
gửi cho bên thứ ba thì dùng `lay_thong_tin_dia_chi(request)` và từ chối khi `dang_doan` là true.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("web2api.kham_pha")

ID_HOP_LE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,40}\Z")
#: Tiền tố model: đứng trước «/» trong tên model nên không được chứa «/».
TIEN_TO_HOP_LE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,40}\Z")
#: Id trùng chữ của tuyến /api/modules/<chữ> thì `DELETE /api/modules/tu-xa` thành mơ hồ.
ID_DANH_RIENG = {"catalog", "thong-bao", "nap-lai", "tu-xa"}
KIEU_TRUONG = {"chuoi", "bi_mat", "danh_sach_bi_mat"}
KIEU_BI_MAT_TRUONG = {"bi_mat", "danh_sach_bi_mat"}
#: Tên miền chặt: nhãn [a-z0-9-] không «-» đầu/cuối (≤ 63), ÍT NHẤT hai nhãn, tổng ≤ 253, chữ thường. Một nhãn đơn là cả
#: một đuôi tên miền (cookie của mọi trang dưới nó). Dùng cho `nhan_cookie.mien` và đường cookie của lõi.
MAU_MIEN = re.compile(r"(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
TRUONG_CHUOI_BAT_BUOC = ("id", "ten", "mo_ta", "loai", "giao_thuc", "tien_to")
#: Phương thức BẮT BUỘC chung: chỉ vòng đời. Khả năng (chat, ảnh, …) theo `cung_cap` — xem HOP_DONG_KHA_NANG.
PHUONG_THUC = ("nap", "cai", "suc_khoe", "dung")
_TEN_GOI = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\Z")
#: Mẫu mã khả năng trong `cung_cap`, mã loại `loai` và mã trong `can` — kiểm dạng, KHÔNG liệt kê mã hợp lệ
#: (loại/khả năng mới không phải sửa lõi).
MA_KHA_NANG = re.compile(r"^[a-z][a-z0-9_]{0,30}\Z")
#: Id của một mục (tab con) trong `muc` — đi vào id phần tử/đường dẫn của giao diện.
ID_MUC = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}\Z")

#: Chuẩn phân loại lỗi trung lập cho module (mã mở):
LOAI_LOI_CHUAN = {
    "khoa_hong",
    "het_han_muc",
    "khong_co_o_khoa",
    "yeu_cau_sai",
    "may_chu_loi",
    "chan_vung",
    "khong_ro",
}

#: Bảng hợp đồng chuẩn cho từng khả năng đã biết (mã mở: khả năng lạ không nằm trong bảng không bị chặn):
HOP_DONG_KHA_NANG: dict[str, tuple[str, ...]] = {
    "chat": ("chat",),
    "anh": ("anh",),
    "video": ("video_tao", "video_trang_thai", "video_noi_dung"),
    "noi": ("noi",),
    "nghe": ("nghe",),
    "anh_sua": ("anh_sua",),
    "thiet_bi": ("thuc_the", "trang_thai", "thuc_hien"),
    "nhung": ("nhung",),
    "xep_hang": ("xep_hang",),
    "tim_kiem": ("tim_kiem",),
    "trinh_duyet": ("trinh_duyet_mo", "trinh_duyet_dong", "trinh_duyet_ds"),
    #: Gửi ra đích danh (NV32): module liệt kê đích qua kenh_ra() và gửi qua gui_tin().
    "kenh_ra": ("gui_tin", "kenh_ra"),
}


#: Khả năng mà đường /v1 của LÕI chọn module theo trường `model` của khách (tuyen._module_theo_model) — khai một
#: trong số này thì phải có `models()` để model hiện ở GET /v1/models. Không suy được từ HOP_DONG_KHA_NANG: bảng đó
#: có cả khả năng không đi theo model (thiet_bi, tim_kiem, trinh_duyet). Đây là thuộc tính của các đường chuẩn OpenAI
#: lõi đang có, không phải danh sách tên nhà cung cấp; khả năng lạ không nằm đây thì không bị bắt `models` (mã mở).
KHA_NANG_CO_MODEL = frozenset({"chat", "anh", "anh_sua", "video", "noi", "nghe", "nhung", "xep_hang"})


def kiem_chuan_module(cung_cap: list[str], lop: type) -> dict[str, list[str]]:
    """Kiểm tra các khả năng đã khai trong cung_cap so với bảng HOP_DONG_KHA_NANG.

    Trả về từ điển các khả năng bị lỗi chuẩn: {khả_năng: [phương_thức_còn_thiếu]}.
    Khả năng lạ không có trong HOP_DONG_KHA_NANG: coi là hợp lệ (mã mở).
    """
    loi = {}
    for kn in cung_cap:
        bat_buoc = HOP_DONG_KHA_NANG.get(kn)
        if not bat_buoc:
            continue
        thieu = [pt for pt in bat_buoc if not callable(getattr(lop, pt, None))]
        if thieu:
            loi[kn] = thieu
    return loi


def phan_loai_loi_mac_dinh(ma: int) -> str:
    """Phân loại lỗi mặc định theo mã trạng thái HTTP chuẩn khi module không tự phân loại."""
    if ma == 429:
        return "het_han_muc"
    if ma in (401, 403):
        return "khoa_hong"
    if 500 <= ma < 600:
        return "may_chu_loi"
    return "khong_ro"


class LoiNap(Exception):
    pass


@dataclass
class ModuleKhamPha:
    id: str
    nguon: str  # "anh" | "du_lieu"
    thu_muc: Path
    manifest: dict
    lop: type
    van_tay: str
    ten_ma: str
    chuan_loi: dict[str, list[str]] = field(default_factory=dict)
    tien_to: str = field(init=False)

    def __post_init__(self):
        self.tien_to = self.manifest["tien_to"]
        if not self.chuan_loi and self.lop:
            self.chuan_loi = kiem_chuan_module(self.manifest.get("cung_cap", []), self.lop)

    @property
    def dung_chuan(self) -> bool:
        return len(self.chuan_loi) == 0

    @property
    def kha_nang_chuan(self) -> list[str]:
        return [k for k in self.manifest.get("cung_cap", []) if k not in self.chuan_loi]

    @property
    def truong_cau_hinh(self) -> list[dict]:
        return self.manifest["truong_cau_hinh"]

    @property
    def can_cau_hinh(self) -> bool:
        return any(t.get("bat_buoc") for t in self.truong_cau_hinh)

    def tao(self):
        the = self.lop(self.manifest)
        if not hasattr(the, "manifest") or not isinstance(getattr(the, "manifest", None), dict):
            try:
                the.manifest = self.manifest
            except Exception:
                pass
        return the

    def duong_tab(self) -> dict:
        """Tab của module trên thanh trên. Manifest khai `tab_web` thì dùng giao diện riêng ở /m/<id>/;
        không khai thì trỏ tới ô của module trên trang Hệ thống → Module — nơi form dựng từ `truong_cau_hinh`
        để thêm khoá. Module nào đã cài cũng có tab, không cần bảng tên module nào. Đường tương đối: chạy
        được cả khi web2api nằm sau proxy ở một thư mục con."""
        tab = self.manifest.get("tab_web")
        if not tab:
            return {"nhan": self.manifest["ten"], "duong": f"He-thong.dc.html#module-{self.id}"}
        return {"nhan": tab["nhan"], "duong": f"/m/{self.id}/{tab['trang'].removeprefix('web/')}"}


def kiem_manifest(m) -> str | None:
    """Lý do manifest không dùng được, hoặc None. Kiểu trường lạ là LỖI chứ không bỏ qua:
    kho dựa vào `kieu` để quyết bí mật — gõ nhầm «bi_mat » mà coi là chuỗi thường là lộ khoá."""
    if not isinstance(m, dict):
        return "manifest phải là đối tượng JSON"
    for k in TRUONG_CHUOI_BAT_BUOC:
        if not isinstance(m.get(k), str) or not m[k].strip():
            return f"thiếu hoặc sai trường «{k}» (cần chuỗi khác rỗng)"
    if not ID_HOP_LE.match(m["id"]) or m["id"] in ID_DANH_RIENG:
        return f"id {m['id']!r} không hợp lệ (cần ^[a-z0-9][a-z0-9_-]{{1,40}}$, không trùng tên tuyến)"
    if not MA_KHA_NANG.match(m["loai"]):
        return f"loai {m['loai']!r} không hợp lệ (mã: chữ thường, số, _; ví dụ \"ai\", \"app\", \"nha\")"
    if "loai_ten" in m and (not isinstance(m["loai_ten"], str) or not m["loai_ten"].strip()):
        return "loai_ten phải là chuỗi khác rỗng (tên hiển thị của loại)"
    if "loai_thu_tu" in m and (type(m["loai_thu_tu"]) is not int):
        return "loai_thu_tu phải là số nguyên (nhỏ đứng trước)"
    if not TIEN_TO_HOP_LE.match(m["tien_to"]):
        return f"tien_to {m['tien_to']!r} không hợp lệ (chữ, số, . _ -; không có «/»)"
    truong = m.get("truong_cau_hinh")
    if not isinstance(truong, list):
        return "thiếu «truong_cau_hinh» (danh sách, có thể rỗng)"
    khoa_da_co = set()
    for i, t in enumerate(truong):
        if not isinstance(t, dict) or not isinstance(t.get("khoa"), str) or not isinstance(t.get("nhan"), str):
            return f"truong_cau_hinh[{i}] cần có «khoa» và «nhan» là chuỗi"
        if t.get("kieu") not in KIEU_TRUONG:
            return f"truong_cau_hinh[{i}] kiểu {t.get('kieu')!r} không hợp lệ (chỉ {sorted(KIEU_TRUONG)})"
        if t["khoa"] in khoa_da_co:
            return f"truong_cau_hinh trùng khoá {t['khoa']!r}"
        if "nhan_cookie" in t:
            nc = t["nhan_cookie"]
            if t["kieu"] not in KIEU_BI_MAT_TRUONG:
                return f"truong_cau_hinh[{i}] khai nhan_cookie phải là trường bí mật (bi_mat hoặc danh_sach_bi_mat)"
            if (not isinstance(nc, dict) or set(nc) != {"mien"} or not isinstance(nc["mien"], str)
                    or not MAU_MIEN.fullmatch(nc["mien"])):
                return (f'truong_cau_hinh[{i}].nhan_cookie cần {{"mien": tên miền chữ thường, ít nhất hai nhãn}} '
                        '(ví dụ {"mien": "vi-du.example"})')
        khoa_da_co.add(t["khoa"])
    if not isinstance(m.get("tai_nguyen"), dict):
        return "thiếu «tai_nguyen» (đối tượng)"
    mang = m["tai_nguyen"].get("mang_ra", [])
    if not isinstance(mang, list) or not all(isinstance(x, str) for x in mang):
        return "tai_nguyen.mang_ra phải là danh sách chuỗi"
    if "phien_ban" in m and not isinstance(m["phien_ban"], str):
        return "phien_ban phải là chuỗi"
    goi = m.get("yeu_cau_goi", [])
    if not isinstance(goi, list) or not all(isinstance(x, str) and _TEN_GOI.match(x) for x in goi):
        return "yeu_cau_goi phải là danh sách tên gói Python (chữ, số, _; không chấm)"
    cung_cap = m.get("cung_cap", [])
    if (not isinstance(cung_cap, list) or not all(isinstance(x, str) and MA_KHA_NANG.match(x) for x in cung_cap)
            or len(set(cung_cap)) != len(cung_cap)):
        return 'cung_cap phải là danh sách mã khả năng không trùng (chữ thường, số, _; ví dụ ["chat", "anh"])'
    muc = m.get("muc", [])
    if not isinstance(muc, list):
        return 'muc phải là danh sách [{"id", "nhan", "can": [mã…]}]'
    id_muc = set()
    for i, x in enumerate(muc):
        if (not isinstance(x, dict) or not isinstance(x.get("id"), str) or not ID_MUC.match(x["id"])
                or not isinstance(x.get("nhan"), str) or not x["nhan"].strip() or not isinstance(x.get("can"), list)
                or not all(isinstance(c, str) and MA_KHA_NANG.match(c) for c in x["can"])):
            return (f'muc[{i}] cần {{"id": mã (chữ thường, số, - _), "nhan": chuỗi, "can": [mã khả năng/loại…]}} '
                    '(can rỗng = luôn hiện khi module đã liên kết)')
        if x["id"] in id_muc:
            return f"muc trùng id {x['id']!r}"
        id_muc.add(x["id"])
    tab = m.get("tab_web")
    if tab is not None:
        trang = tab.get("trang") if isinstance(tab, dict) else None
        if (not isinstance(tab, dict) or not isinstance(tab.get("nhan"), str) or not isinstance(trang, str)
                or not trang.startswith("web/") or ".." in trang.split("/") or "\\" in trang):
            return "tab_web cần {\"nhan\": chuỗi, \"trang\": \"web/<tệp>\"} (không «..»)"
    wh = m.get("webhook")
    if wh is not None:
        # Cửa webhook chung của lõi (app/webhook.py): khai báo là quyền (xem diff_quyen).
        if not isinstance(wh, dict):
            return "webhook phải là đối tượng"
        bm = wh.get("bi_mat")
        if not isinstance(bm, str) or not bm:
            return "webhook.bi_mat phải là tên khoá cấu hình (chuỗi khác rỗng)"
        truong_bm = {t["khoa"] for t in truong if t.get("kieu") == "bi_mat"}
        if bm not in truong_bm:
            return (f"webhook.bi_mat {bm!r} phải là khoá trong truong_cau_hinh có kieu «bi_mat» "
                    "(bí mật webhook lưu ở kho 0600, không nằm trong manifest)")
        tb = wh.get("tran_byte", 65536)
        if type(tb) is not int or tb <= 0:
            return "webhook.tran_byte phải là số nguyên dương (byte)"
        isk = wh.get("id_su_kien", "su_kien_id")
        if not isinstance(isk, str) or not isk:
            return "webhook.id_su_kien phải là tên phương thức trích id sự kiện (chuỗi khác rỗng)"
        ck = wh.get("chu_ky")
        if ck is not None and (not isinstance(ck, str) or not ck.startswith("header:")
                               or not ck[7:].strip()):
            return "webhook.chu_ky phải có dạng «header:TÊN» (tên header mang chữ ký HMAC)"
        if wh.get("thuat_toan", "hmac-sha256") != "hmac-sha256":
            return "webhook.thuat_toan chỉ hỗ trợ «hmac-sha256»"
    return None


def doc_manifest(thu_muc: Path) -> tuple[dict | None, str | None]:
    try:
        m = json.loads((thu_muc / "manifest.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, "thiếu manifest.json"
    except (OSError, ValueError) as e:
        return None, f"manifest.json không đọc được: {e}"
    ly_do = kiem_manifest(m)
    return (None, ly_do) if ly_do else (m, None)


def goi_thieu(manifest: dict) -> list[str]:
    thieu = []
    for ten in manifest.get("yeu_cau_goi", []):
        try:
            if importlib.util.find_spec(ten) is None:
                thieu.append(ten)
        except (ImportError, ValueError):
            thieu.append(ten)
    return thieu


def van_tay(thu_muc: Path) -> str:
    """Băm NỘI DUNG mọi tệp (bỏ __pycache__): sửa một ký tự là đổi vân tay → nạp lại."""
    h = hashlib.sha256()
    for p in sorted(x for x in thu_muc.rglob("*") if x.is_file() and "__pycache__" not in x.parts):
        h.update(str(p.relative_to(thu_muc)).encode() + b"\0")
        h.update(p.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def ten_ma(id_: str, vt: str) -> str:
    return f"web2api_module_{id_.replace('-', '_')}_{vt[:16]}"


def go_ma(ten: str) -> None:
    """Gỡ mô-đun module khỏi bộ nhớ cùng MỌI tệp anh em đã import (ten.<tệp>): không giữ mã cũ."""
    for k in [k for k in sys.modules if k == ten or k.startswith(ten + ".")]:
        sys.modules.pop(k, None)


def nap_ma(thu_muc: Path, manifest: dict, vt: str) -> tuple[type, str]:
    """Thực thi module.py dưới tên mô-đun DUY NHẤT theo (id + vân tay) → (lớp Module, tên mô-đun).

    Biên dịch thẳng từ MÃ NGUỒN, không qua __pycache__: .pyc được kiểm theo mtime (giây) + cỡ tệp,
    nên cài đè bản mới cùng cỡ trong cùng một giây sẽ chạy nhầm bytecode CŨ.
    Mọi lỗi của mã module (cú pháp, import, __init__) → LoiNap; lõi không sập.
    """
    thieu = goi_thieu(manifest)
    if thieu:
        raise LoiNap(f"thiếu gói Python: {', '.join(thieu)} — lõi không tự pip install lúc chạy; "
                     "hãy dùng module dạng từ xa (container riêng có đủ gói, đăng ký bằng /api/modules/tu-xa)")
    tep = thu_muc / "module.py"
    ten = ten_ma(manifest["id"], vt)
    # Nạp như một GÓI: các tệp anh em trong thư mục module import được bằng `from .tên import …` (tên gói duy nhất theo
    # id + vân tay nên hai module cùng có `client.py` không đụng nhau).
    spec = importlib.util.spec_from_file_location(ten, tep, submodule_search_locations=[str(thu_muc)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[ten] = mod
    try:
        ma = compile(tep.read_bytes(), str(tep), "exec")
        exec(ma, mod.__dict__)
        lop = getattr(mod, "Module", None)
        if not isinstance(lop, type):
            raise LoiNap("module.py không có lớp «Module»")
        bat_buoc = list(PHUONG_THUC)
        if KHA_NANG_CO_MODEL.intersection(manifest.get("cung_cap", [])):
            bat_buoc.append("models")
        thieu_pt = [p for p in bat_buoc if not callable(getattr(lop, p, None))]
        if thieu_pt:
            raise LoiNap(f"lớp Module thiếu phương thức: {', '.join(thieu_pt)}")
        lop(manifest)  # tạo thử: lỗi trong __init__ lộ NGAY lúc nạp, không đợi lúc cài/khởi động
    except LoiNap:
        go_ma(ten)
        raise
    except FileNotFoundError:
        go_ma(ten)
        raise LoiNap("thiếu module.py") from None
    except BaseException as e:  # mã bên thứ ba: mọi lỗi (kể cả SystemExit) chỉ hỏng module này
        go_ma(ten)
        if isinstance(e, (KeyboardInterrupt,)):
            raise
        raise LoiNap(f"lỗi khi nạp module.py: {type(e).__name__}: {e}") from e
    return lop, ten


def quet(goc: Path) -> list[Path]:
    """Thư mục ứng viên trong một nguồn, xếp theo tên (để «module sau» khi trùng là xác định).
    Bỏ thư mục bắt đầu bằng «.» (thư mục tạm khi cài) hoặc «_» (__pycache__)."""
    if not goc.is_dir():
        return []
    return sorted(p for p in goc.iterdir() if p.is_dir() and not p.name.startswith((".", "_")))
