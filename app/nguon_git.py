"""Nguồn module là GIT của dự án: đẩy module lên git → web2api tự thấy, báo «có module mới», cài khi được lệnh.

Không cần dựng lại ảnh, không khởi động lại: mã tải về <WEB2API_DATA>/modules/<id>/ rồi nạp nóng.

Đường đi (chỉ API GitHub, host CỐ ĐỊNH — người dùng không đưa URL tuỳ ý vào được, nên không có SSRF):
  1. Mỗi `chu_ky_giay`: GET /repos/{repo}/commits/{nhanh} kèm If-None-Match (ETag). Không đổi → 304:
     một vòng thăm dò bình thường tốn ĐÚNG 1 yêu cầu. Tài liệu GitHub nói 304 có điều kiện không trừ
     hạn mức — CHƯA kiểm điều đó cho truy cập ẩn danh; dù có bị trừ, chu kỳ 300 s chỉ tốn 12 yêu cầu/giờ.
  2. Commit đổi → GET /git/trees/{cây}?recursive=1, mỗi thư mục con của `thu_muc` có manifest.json là
     một module; manifest đọc qua /git/blobs (chỉ cho module có sha thư mục ĐỔI so với lần trước).
  3. Cài: chốt theo ĐÚNG cây của commit đã thấy (không lấy «mới nhất» ngầm), tải từng blob, KIỂM
     BĂM git (sha1 của "blob <len>\\0"+nội dung) khớp sha trong cây — lệch một tệp là từ chối cả module.

Kho đang PUBLIC (chủ máy mở 10/10/2026) → đọc ẨN DANH, không gửi Authorization khi không có token.
Ẩn danh chỉ 60 yêu cầu/giờ/IP: đọc x-ratelimit-remaining/x-ratelimit-reset mỗi phản hồi; còn <10 thì
giãn nhịp tới giờ reset (giữ nguyên ETag), hết hạn mức (403/429) thì chờ tới reset — không đốt vòng lặp.

Rủi ro (nói rõ trong README): mã module chạy TRONG tiến trình lõi — ai đẩy được lên nhánh đó là chạy
được mã trong lõi. Tự cài mặc định TẮT; bật `yeu_cau_chu_ky` để chỉ tự cài commit đã xác minh chữ ký.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import posixpath
import re
import secrets
import shutil
import time
import unicodedata
from urllib.parse import quote

import httpx

from app import kho
from app.chuyen_tiep import iso
from app.kham_pha import ID_HOP_LE, doc_manifest, kiem_manifest
from app.so_module import LoiSo, SoModule

log = logging.getLogger("web2api.nguon_git")

API_MAC_DINH = "https://api.github.com"
REPO_HOP_LE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
DUONG_HOP_LE = re.compile(r"^[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*$")
SHA_HOP_LE = re.compile(r"^[0-9a-f]{40}$")
#: Thư mục chứa module trong kho git của dự án (TriTue2011/web2api: modules/<id>/ ở gốc kho).
THU_MUC_MAC_DINH = "modules"
TRAN_TEP = 200
TRAN_TONG = 50 * 1024 * 1024
TRAN_BLOB = 5 * 1024 * 1024
HAN_MUC_THAP = 10
TEN_BI_MAT = "_nguon_git"
TEP_CAU_HINH = "nguon_git.json"
TEP_TRANG_THAI = "nguon_git_trang_thai.json"


def api_github() -> str:
    return os.environ.get("WEB2API_GITHUB_API", API_MAC_DINH).rstrip("/")


def bam_blob(du_lieu: bytes) -> str:
    """Mã đối tượng git của một tệp. SHA-1 vì đó là ĐỊNH DẠNG của git/GitHub (sha trong cây là SHA-1),
    không phải ta chọn: phải băm đúng thuật toán đó mới so được với sha trong cây."""
    return hashlib.sha1(b"blob %d\0" % len(du_lieu) + du_lieu).hexdigest()  # nosec B324 — định dạng git


class LoiGit(Exception):
    def __init__(self, thong_diep: str, ma: int = 502, cho_den: float | None = None):
        super().__init__(thong_diep)
        self.ma = ma
        self.cho_den = cho_den  # thời điểm (epoch) nên thử lại — khi GitHub báo hết hạn mức


def _duong_an_toan(ten: str, ten_tham_so: str) -> str:
    if not isinstance(ten, str) or len(ten) > 200 or not DUONG_HOP_LE.match(ten) or any(
            p in (".", "..") for p in ten.split("/")):
        raise ValueError(f"{ten_tham_so} chỉ gồm chữ, số, . _ - và «/» giữa các phần (không «..»)")
    return ten


def kiem_repo(repo) -> str:
    # «..» làm chủ/kho thì /repos/../.. trỏ sang đường KHÁC của API GitHub — và mang theo token.
    if not isinstance(repo, str) or not REPO_HOP_LE.match(repo) or any(p in (".", "..") for p in repo.split("/")):
        raise ValueError("repo phải dạng chủ/kho, ví dụ TriTue2011/web2api")
    return repo


class NguonGit:
    #: Nhân thời gian chờ của vòng nền — test đặt nhỏ để không phải ngủ thật.
    HE_SO_CHO = 1.0
    TRAN_LUI_GIAY = 1800.0

    def __init__(self, so: SoModule):
        self.so = so
        try:
            tt = kho.doc_json(kho.thu_muc_du_lieu() / TEP_TRANG_THAI)
        except kho.KhoHong as e:
            log.warning("%s — bắt đầu lại trạng thái đồng bộ", e)
            tt = None
        self.trang_thai: dict = tt if isinstance(tt, dict) else {}
        self._danh_thuc = asyncio.Event()
        self._khoa = asyncio.Lock()  # một lần đồng bộ/cài từ git tại một thời điểm
        self._khach: httpx.AsyncClient | None = None
        self._tac_vu: asyncio.Task | None = None
        self._lan_loi = 0
        self._ghi_chu: dict[str, tuple[str, str]] = {}
        self._tu_dong_hong: dict[str, str] = {}
        self._lan_dau = False  # lần đồng bộ vừa xong là lần ĐẦU của nguồn này → _tu_dong cập nhật module đang có
        if api_github() != API_MAC_DINH:
            log.warning("WEB2API_GITHUB_API=%s khác mặc định %s — chỉ dùng cho test", api_github(), API_MAC_DINH)

    # ───────────── cấu hình ─────────────

    def cau_hinh(self) -> dict | None:
        try:
            cfg = kho.doc_json(kho.thu_muc_du_lieu() / TEP_CAU_HINH)
        except kho.KhoHong as e:
            log.warning("%s — coi như chưa cấu hình nguồn git", e)
            return None
        if isinstance(cfg, dict) and cfg.get("repo"):
            return cfg
        repo = os.environ.get("WEB2API_GIT_REPO", "").strip()
        if not repo:
            return None
        try:
            return {"repo": kiem_repo(repo),
                    "nhanh": _duong_an_toan(os.environ.get("WEB2API_GIT_NHANH") or "main", "WEB2API_GIT_NHANH"),
                    "thu_muc": _duong_an_toan(os.environ.get("WEB2API_GIT_THU_MUC") or THU_MUC_MAC_DINH,
                                              "WEB2API_GIT_THU_MUC"),
                    "chu_ky_giay": 300, "tu_dong": False, "yeu_cau_chu_ky": False, "nguon": "bien_moi_truong"}
        except ValueError as e:
            log.warning("Biến môi trường nguồn git sai, bỏ qua: %s", e)
            return None

    def token(self) -> str | None:
        try:
            tk = kho.doc_bi_mat(TEN_BI_MAT).get("token")
        except kho.KhoHong:
            tk = None
        return tk or os.environ.get("WEB2API_GIT_TOKEN", "").strip() or None

    def luu_cau_hinh(self, than: dict) -> dict:
        cu = self.cau_hinh() or {}
        repo = kiem_repo(than.get("repo", cu.get("repo")))
        nhanh = _duong_an_toan(than.get("nhanh", cu.get("nhanh", "main")), "nhanh")
        thu_muc = _duong_an_toan(than.get("thu_muc", cu.get("thu_muc", THU_MUC_MAC_DINH)), "thu_muc")
        chu_ky = than.get("chu_ky_giay", cu.get("chu_ky_giay", 300))
        if not isinstance(chu_ky, int) or isinstance(chu_ky, bool) or not 1 <= chu_ky <= 86400:
            raise ValueError("chu_ky_giay phải là số nguyên 1..86400")
        co = {}
        for k in ("tu_dong", "yeu_cau_chu_ky"):
            v = than.get(k, cu.get(k, False))
            if not isinstance(v, bool):
                raise ValueError(f"{k} phải là true/false")
            co[k] = v
        token = than.get("token", None)
        if token is not None and not isinstance(token, str):
            raise ValueError("token phải là chuỗi")
        cfg = {"repo": repo, "nhanh": nhanh, "thu_muc": thu_muc, "chu_ky_giay": chu_ky, **co}
        kho.ghi_json_cong_khai(TEP_CAU_HINH, cfg)
        if "token" in than:
            if token:
                kho.ghi_bi_mat(TEN_BI_MAT, {"token": token.strip()})
            else:
                kho.xoa_bi_mat(TEN_BI_MAT)
        self._lan_loi = 0
        self.danh_thuc()
        return self.cong_khai()

    def cong_khai(self) -> dict:
        cfg = self.cau_hinh()
        tk = self.token()
        tt = self.trang_thai
        return {
            "cau_hinh": None if cfg is None else {**cfg, "token": kho.che(tk) if tk else None},
            "trang_thai": {k: tt.get(k) for k in ("kiem_luc", "loi", "canh_bao", "han_muc_con", "reset_luc",
                                                  "thu_lai_luc")}
                          | {"commit": tt.get("commit"), "so_module": len(tt.get("modules") or [])},
        }

    # ───────────── HTTP tới GitHub ─────────────

    def _client(self) -> httpx.AsyncClient:
        if self._khach is None:
            # follow_redirects=False: chuyển hướng (repo đổi tên…) KHÔNG được mang token sang chỗ khác.
            self._khach = httpx.AsyncClient(follow_redirects=False)
        return self._khach

    def _che(self, van_ban: str) -> str:
        tk = self.token()
        return van_ban.replace(tk, kho.che(tk)) if tk else van_ban

    def _doc_han_muc(self, r: httpx.Response) -> None:
        con = r.headers.get("x-ratelimit-remaining")
        reset = r.headers.get("x-ratelimit-reset")
        if con is not None and con.isdigit():
            self.trang_thai["han_muc_con"] = int(con)
        if reset is not None and reset.isdigit():
            self.trang_thai["reset_luc"] = iso(int(reset))
            self.trang_thai["_reset_epoch"] = int(reset)

    async def _goi(self, duong: str, etag: str | None = None) -> httpx.Response:
        hd = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "web2api"}
        tk = self.token()
        if tk:
            hd["Authorization"] = f"Bearer {tk}"
        if etag:
            hd["If-None-Match"] = etag
        try:
            r = await self._client().get(api_github() + duong, headers=hd,
                                         timeout=httpx.Timeout(30.0, connect=10.0))
        except httpx.HTTPError as e:
            raise LoiGit(f"lỗi mạng tới GitHub: {type(e).__name__}") from None
        self._doc_han_muc(r)
        if r.status_code == 429 or (r.status_code == 403 and r.headers.get("x-ratelimit-remaining") == "0"):
            cho_den = self.trang_thai.get("_reset_epoch")
            ra = r.headers.get("retry-after")
            if ra and ra.isdigit():
                cho_den = max(cho_den or 0, time.time() + int(ra))
            raise LoiGit(f"hết hạn mức API GitHub ({r.status_code}"
                         + (", ẩn danh 60 yêu cầu/giờ/IP — thêm token để nâng" if not tk else "")
                         + f"); thử lại lúc {self.trang_thai.get('reset_luc') or 'sau'}", 429, cho_den)
        return r

    def _loi_http(self, r: httpx.Response, viec: str) -> LoiGit:
        try:
            tin = str(r.json().get("message") or "")
        except (ValueError, AttributeError):
            tin = ""
        goi_y = ""
        if r.status_code == 404:
            goi_y = " — không thấy repo/nhánh (kho riêng tư thì cần token có quyền đọc)"
        elif r.status_code == 401:
            goi_y = " — token sai hoặc hết hạn"
        elif r.status_code in (301, 302, 307, 308):
            goi_y = " — repo đã đổi tên/chuyển chỗ; sửa «repo» trong cấu hình"
        return LoiGit(self._che(f"GitHub trả HTTP {r.status_code} khi {viec}: {tin[:200]}{goi_y}"), 502)

    async def _doc_cay(self, repo: str, cay: str) -> list[dict]:
        if not SHA_HOP_LE.match(cay):
            raise LoiGit(f"sha cây không hợp lệ: {cay!r}")
        r = await self._goi(f"/repos/{repo}/git/trees/{cay}?recursive=1")
        if r.status_code != 200:
            raise self._loi_http(r, "đọc cây thư mục")
        j = r.json()
        if j.get("truncated"):
            raise LoiGit("cây thư mục quá lớn, GitHub cắt bớt (truncated) — không đọc được đầy đủ")
        return j.get("tree") or []

    async def _doc_blob(self, repo: str, sha: str) -> bytes:
        if not isinstance(sha, str) or not SHA_HOP_LE.match(sha):
            raise LoiGit(f"sha tệp không hợp lệ: {sha!r}")
        r = await self._goi(f"/repos/{repo}/git/blobs/{sha}")
        if r.status_code != 200:
            raise self._loi_http(r, f"tải tệp {sha[:12]}")
        j = r.json()
        if j.get("encoding") != "base64" or not isinstance(j.get("content"), str):
            raise LoiGit(f"tệp {sha[:12]} không ở dạng base64")
        du_lieu = base64.b64decode(j["content"])
        if len(du_lieu) > TRAN_BLOB:
            raise LoiGit(f"tệp {sha[:12]} lớn {len(du_lieu)} byte, vượt trần {TRAN_BLOB}")
        if bam_blob(du_lieu) != sha:
            raise LoiGit(f"tệp {sha[:12]} SAI BĂM — nội dung nhận được không khớp sha trong cây git; "
                         "từ chối cả module", 422)
        return du_lieu

    # ───────────── thăm dò ─────────────

    def _luu_trang_thai(self) -> None:
        tt = dict(self.trang_thai)
        tt["danh_sach"] = self.danh_sach()
        kho.ghi_json_cong_khai(TEP_TRANG_THAI, tt)

    async def mot_lan(self) -> None:
        """Một vòng thăm dò. LoiGit nếu GitHub lỗi (người gọi lùi nhịp)."""
        cfg = self.cau_hinh()
        if not cfg:
            return
        repo, nhanh, thu_muc = cfg["repo"], cfg["nhanh"], cfg["thu_muc"]
        tt = self.trang_thai
        if tt.get("dinh_danh") != [repo, nhanh, thu_muc]:
            tt.clear()
            tt["dinh_danh"] = [repo, nhanh, thu_muc]
        # «Lần đầu» = trạng thái chưa từng có commit của ĐÚNG nguồn này (đổi repo/nhánh/thư mục thì tt vừa bị
        # xoá ở trên). Dấu chính là commit đã lưu trong nguon_git_trang_thai.json: máy đã đồng bộ từ bản cũ
        # thì có sẵn commit → nâng cấp lõi KHÔNG tự cập nhật module lúc không ai bấm; khởi động lại cũng vậy.
        lan_dau = not tt.get("commit")
        r = await self._goi(f"/repos/{repo}/commits/{quote(nhanh, safe='/')}", etag=tt.get("etag"))
        tt["kiem_luc"] = kho.bay_gio_iso()
        if r.status_code == 304:
            tt["loi"] = None
            return
        if r.status_code == 409:  # kho rỗng: không phải lỗi, chỉ là chưa có module nào
            tt.update(etag=None, commit=None, modules=[], loi=None)
            return
        if r.status_code != 200:
            raise self._loi_http(r, "đọc commit mới nhất")
        j = r.json()
        c = j.get("commit") or {}
        commit = {"sha": j.get("sha"), "tac_gia": (c.get("author") or {}).get("name"),
                  "thong_diep": str(c.get("message") or "")[:300],
                  "da_xac_minh": bool((c.get("verification") or {}).get("verified")),
                  "cay": (c.get("tree") or {}).get("sha")}
        cay = await self._doc_cay(repo, str(commit["cay"]))
        modules = await self._doc_modules(repo, cay, thu_muc, tt.get("modules") or [])
        # ETag chỉ lưu khi đã đọc xong: hỏng giữa chừng thì lần sau không bị 304 che mất commit này.
        tt.update(etag=r.headers.get("etag"), commit=commit, modules=modules, loi=None)
        self._lan_dau = lan_dau

    async def _doc_modules(self, repo: str, cay: list[dict], thu_muc: str, cu: list[dict]) -> list[dict]:
        tien = thu_muc + "/"
        thu_muc_con = {e["path"][len(tien):]: e["sha"] for e in cay
                       if e.get("type") == "tree" and e["path"].startswith(tien) and "/" not in e["path"][len(tien):]}
        manifest = {e["path"][len(tien):-len("/manifest.json")]: e["sha"] for e in cay
                    if e.get("type") == "blob" and e["path"].startswith(tien) and e["path"].endswith("/manifest.json")}
        cu_theo_id = {m["id"]: m for m in cu if isinstance(m, dict)}
        # Dung lượng tải = cộng `size` mọi blob trong thư mục module của CÂY đã đọc (số thật của git, không tải gì thêm).
        dung_luong: dict[str, int] = {}
        for e in cay:
            if e.get("type") == "blob" and e["path"].startswith(tien) and "/" in e["path"][len(tien):]:
                ten_tm = e["path"][len(tien):].split("/", 1)[0]
                dung_luong[ten_tm] = dung_luong.get(ten_tm, 0) + int(e.get("size") or 0)
        ra = []
        for ten, sha_tm in sorted(thu_muc_con.items()):
            if ten not in manifest:
                continue  # thư mục con không có manifest.json ở gốc thì không phải module
            truoc = cu_theo_id.get(ten)
            # Thư mục không đổi → khỏi tải lại manifest (tiết kiệm hạn mức). Bản tóm tắt do lõi CŨ ghi (chưa có
            # `loai`) thì đọc lại một lần: thiếu `loai` thì trang Module không xếp được ô «Chưa cài» vào khu nào.
            if truoc and truoc.get("tree_sha") == sha_tm and (truoc.get("loi") or "loai" in truoc):
                ra.append({**truoc, "dung_luong": dung_luong.get(ten, 0)})
                continue
            muc = {"id": ten, "tree_sha": sha_tm, "loi": None, "dung_luong": dung_luong.get(ten, 0)}
            try:
                m = json.loads(await self._doc_blob(repo, manifest[ten]))
                ly_do = kiem_manifest(m)
                if ly_do is None and m["id"] != ten:
                    ly_do = f"id trong manifest ({m['id']!r}) khác tên thư mục ({ten!r})"
            except LoiGit:
                raise
            except ValueError as e:
                ly_do, m = f"manifest.json không phải JSON: {e}", {}
            if ly_do:
                muc["loi"] = ly_do
            else:
                muc.update(ten=m["ten"], mo_ta=m["mo_ta"], phien_ban=m.get("phien_ban"),
                           yeu_cau_goi=m.get("yeu_cau_goi", []), mang_ra=m["tai_nguyen"].get("mang_ra", []),
                           tai_nguyen=m["tai_nguyen"], loai=m["loai"], loai_ten=m.get("loai_ten"),
                           loai_thu_tu=m.get("loai_thu_tu"), cung_cap=m.get("cung_cap", []))
            ra.append(muc)
        return ra

    # ───────────── trạng thái & thông báo ─────────────

    def _nguon_da_cai(self, id_: str) -> dict | None:
        km = self.so.catalog.get(id_)
        if km is None or km.nguon != "du_lieu":
            return None
        try:
            return json.loads((km.thu_muc / ".nguon.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def danh_sach(self) -> list[dict]:
        """Trạng thái từng module trên git so với bản đã cài — tính LÚC ĐỌC (người dùng cài/gỡ
        không cần đợi vòng thăm dò sau mới thấy đúng). Bản đã cài khớp khi sha THƯ MỤC module
        trong git bằng nhau: so nội dung thật, chặt hơn so số phiên bản (quên tăng phiên bản vẫn thấy)."""
        commit = self.trang_thai.get("commit") or {}
        ra = []
        for m in self.trang_thai.get("modules") or []:
            km = self.so.catalog.get(m["id"])
            nguon = self._nguon_da_cai(m["id"])
            ly_do = m.get("loi")
            if ly_do:
                tt = "loi_manifest"
            elif km is not None and km.nguon == "anh":
                tt, ly_do = "loi_manifest", "trùng id với module đi kèm ảnh"
            elif km is None:
                tt = "moi"
            elif nguon and nguon.get("tree_sha") == m["tree_sha"]:
                tt = "da_cai_dung"
            else:
                tt = "co_ban_moi"
            ghi_chu = self._ghi_chu.get(m["id"])
            ra.append({
                "id": m["id"], "ten": m.get("ten"), "trang_thai": tt,
                "phien_ban_cu": km.manifest.get("phien_ban") if km else None,
                "phien_ban_moi": m.get("phien_ban"),
                # Tóm tắt manifest trên git: trang Module xếp ô «Chưa cài» vào khu theo `loai` và hiện «Cần gì để chạy».
                **{k: m.get(k) for k in ("mo_ta", "loai", "loai_ten", "loai_thu_tu", "cung_cap", "tai_nguyen",
                                         "yeu_cau_goi", "dung_luong")},
                "commit": {k: commit.get(k) for k in ("sha", "tac_gia", "thong_diep")},
                "ly_do": ly_do,
                "ghi_chu": ghi_chu[1] if ghi_chu and ghi_chu[0] == m["tree_sha"] else None,
            })
        return ra

    def thong_bao(self) -> dict:
        tat_ca = self.danh_sach()
        ds = [x for x in tat_ca if x["trang_thai"] != "da_cai_dung"]
        tt = self.trang_thai
        # `moi_nhat`: module đã cài ĐÚNG bản trên git — giao diện ghi «Đã là bản mới nhất» (không suy từ chỗ vắng mặt:
        # vắng mặt còn có thể là module không có trên git).
        return {"so_moi": sum(x["trang_thai"] in ("moi", "co_ban_moi") for x in ds), "danh_sach": ds,
                "moi_nhat": [x["id"] for x in tat_ca if x["trang_thai"] == "da_cai_dung"],
                "dong_bo": {k: tt.get(k) for k in ("kiem_luc", "loi", "canh_bao", "han_muc_con", "reset_luc")}}

    def so_thong_bao(self) -> int:
        return sum(x["trang_thai"] in ("moi", "co_ban_moi") for x in self.danh_sach())

    # ───────────── cài từ git ─────────────

    def viec(self, id_: str) -> str:
        """Quy tắc từ ngữ, suy từ ĐĨA chứ không từ danh sách: module chưa có mã trên máy thì là «cài»;
        đã cài rồi (có trong catalog, kể cả chưa liên kết hay đặt tay) thì mọi thay đổi bản là «cập nhật»."""
        return "cập nhật" if id_ in self.so.catalog else "cài"

    async def cai(self, id_: str) -> dict:
        async with self._khoa:
            return await self._cai(id_)

    async def _cai(self, id_: str) -> dict:
        cfg = self.cau_hinh()
        if not cfg:
            raise LoiGit("chưa cấu hình nguồn git (PUT /api/nguon-git)", 400)
        if not isinstance(id_, str) or not ID_HOP_LE.match(id_):
            raise LoiGit(f"id {id_!r} không hợp lệ", 400)
        commit = self.trang_thai.get("commit") or {}
        m = next((x for x in self.trang_thai.get("modules") or [] if x["id"] == id_), None)
        if m is None or not commit.get("cay"):
            raise LoiGit(f"module {id_!r} không có trong lần đồng bộ gần nhất — chờ vòng thăm dò sau", 404)
        if m.get("loi"):
            raise LoiGit(f"manifest của {id_!r} lỗi: {m['loi']}", 422)
        repo, thu_muc = cfg["repo"], cfg["thu_muc"]
        cay = await self._doc_cay(repo, commit["cay"])  # chốt theo ĐÚNG cây đã thấy
        tien = f"{thu_muc}/{id_}/"
        sha_tm = next((e["sha"] for e in cay if e["path"] == f"{thu_muc}/{id_}" and e.get("type") == "tree"), None)
        if sha_tm != m["tree_sha"]:
            raise LoiGit("cây trên GitHub không khớp lần đồng bộ (sha thư mục khác) — từ chối", 422)
        tep = self._kiem_danh_sach_tep([e for e in cay if e["path"].startswith(tien)], tien)
        tam = kho.thu_muc_modules() / f".tam-{id_}-{secrets.token_hex(4)}"
        try:
            tam.mkdir(parents=True)
            tong = 0
            # Tải và kiểm băm NGOÀI khoá của sổ module: GitHub chậm không làm nghẽn /v1 hay nạp lại.
            for rel, e in tep:
                du_lieu = await self._doc_blob(repo, e["sha"])
                tong += len(du_lieu)
                if tong > TRAN_TONG:
                    raise LoiGit(f"tổng dung lượng vượt trần {TRAN_TONG} byte", 422)
                p = tam / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(du_lieu)
                os.chmod(p, 0o644)
            manifest, ly_do = doc_manifest(tam)
            if ly_do or manifest["id"] != id_:
                raise LoiGit(f"manifest tải về không hợp lệ: {ly_do or 'id khác tên thư mục'}", 422)
            nguon = {"repo": repo, "nhanh": cfg["nhanh"], "sha_commit": commit["sha"], "tree_sha": m["tree_sha"],
                     "phien_ban": manifest.get("phien_ban"), "cai_luc": kho.bay_gio_iso()}
            (tam / ".nguon.json").write_text(json.dumps(nguon, ensure_ascii=False, indent=2), encoding="utf-8")
            viec = self.viec(id_)  # đọc TRƯỚC khi cài: cài xong thì module đã nằm trong catalog
            kq = await self.so.cai_thu_muc(id_, tam)
        except BaseException:
            if tam.exists():
                shutil.rmtree(tam, ignore_errors=True)
            raise
        self._ghi_chu.pop(id_, None)
        self._tu_dong_hong.pop(id_, None)
        (self.trang_thai.get("da_go") or {}).pop(id_, None)
        log.warning("Đã %s module %s phiên bản %s từ git (commit %s)", viec, id_, manifest.get("phien_ban"),
                    str(commit["sha"])[:12])
        return {"ok": True, "id": id_, "phien_ban": manifest.get("phien_ban"), "sha_commit": commit["sha"], **kq}

    @staticmethod
    def _kiem_danh_sach_tep(muc: list[dict], tien: str) -> list[tuple[str, dict]]:
        """Chỉ tệp thường. Symlink (120000) có thể trỏ ra ngoài thư mục module; submodule (160000)
        kéo mã từ kho khác không qua kiểm băm; đường thoát/«..»/tên trùng sau chuẩn hoá là cách
        đè tệp ngoài ý muốn — tất cả từ chối CẢ module."""
        ra = []
        da_thay = set()
        tong = 0
        for e in muc:
            rel = e["path"][len(tien):]
            if (not rel or rel.startswith("/") or "\\" in rel or "\0" in rel
                    or any(p in ("", ".", "..") for p in rel.split("/")) or posixpath.normpath(rel) != rel):
                raise LoiGit(f"đường dẫn không an toàn trong module: {rel!r}", 422)
            khoa = unicodedata.normalize("NFC", rel).casefold()
            if khoa in da_thay:
                raise LoiGit(f"tên tệp trùng nhau sau chuẩn hoá: {rel!r}", 422)
            da_thay.add(khoa)
            if e.get("mode") == "120000":
                raise LoiGit(f"module chứa symlink {rel!r} — từ chối", 422)
            if e.get("mode") == "160000" or e.get("type") == "commit":
                raise LoiGit(f"module chứa submodule {rel!r} — từ chối", 422)
            if e.get("type") == "tree":
                continue
            if e.get("type") != "blob" or e.get("mode") not in ("100644", "100755"):
                raise LoiGit(f"loại mục lạ trong module: {rel!r} ({e.get('mode')})", 422)
            co = int(e.get("size") or 0)
            if co > TRAN_BLOB:
                raise LoiGit(f"tệp {rel!r} lớn {co} byte, vượt trần {TRAN_BLOB}", 422)
            tong += co
            ra.append((rel, e))
        if len(ra) > TRAN_TEP:
            raise LoiGit(f"module có {len(ra)} tệp, vượt trần {TRAN_TEP}", 422)
        if tong > TRAN_TONG:
            raise LoiGit(f"tổng dung lượng {tong} byte vượt trần {TRAN_TONG}", 422)
        ten = {r for r, _ in ra}
        if "manifest.json" not in ten or "module.py" not in ten:
            raise LoiGit("module phải có manifest.json và module.py ở gốc thư mục", 422)
        return ra

    def da_go(self, id_: str) -> None:
        """Quản trị vừa gỡ module: nhớ (lưu đĩa) sha thư mục của bản trên git để vòng tự cài KHÔNG cài
        lại đúng bản đó ở lần thăm dò sau — không thì gỡ xong một chu kỳ là module quay về."""
        m = next((x for x in self.trang_thai.get("modules") or [] if x["id"] == id_), None)
        if m is None:
            return
        self.trang_thai.setdefault("da_go", {})[id_] = m["tree_sha"]
        try:
            self._luu_trang_thai()
        except OSError as e:
            log.warning("Không ghi được %s: %s", TEP_TRANG_THAI, e)

    # ───────────── tự cài ─────────────

    async def _tu_dong(self) -> None:
        """Tự cài sau một vòng thăm dò, hai trường hợp, cùng mọi chốt bên dưới (chữ ký, xin thêm quyền,
        bản vừa gỡ, bản đã tự cài hỏng):
          - `tu_dong` bật: module mới và bản mới;
          - lần đồng bộ ĐẦU của nguồn git (`_lan_dau`, KE-HOACH-CAI-MODULE.md 3.4): MỘT lần, cập nhật module
            đã có mã mà git có bản khác (`co_ban_moi`, kể cả module đặt tay không có .nguon.json) dù
            `tu_dong` tắt — không cài module mới, việc đó chủ máy bấm."""
        cfg = self.cau_hinh()
        lan_dau, self._lan_dau = self._lan_dau, False
        if not cfg:
            return
        duoc = {"moi", "co_ban_moi"} if cfg.get("tu_dong") else {"co_ban_moi"} if lan_dau else set()
        if not duoc:
            return
        commit = self.trang_thai.get("commit") or {}
        snap = {m["id"]: m for m in self.trang_thai.get("modules") or []}
        for x in self.danh_sach():
            if x["trang_thai"] not in duoc:
                continue
            m = snap[x["id"]]
            viec = self.viec(x["id"])
            if self._tu_dong_hong.get(x["id"]) == m["tree_sha"]:
                continue  # bản này đã tự cài hỏng một lần — chờ bản mới hoặc quản trị
            if (self.trang_thai.get("da_go") or {}).get(x["id"]) == m["tree_sha"]:
                self._ghi_chu[x["id"]] = (m["tree_sha"], f"quản trị đã gỡ bản này — không tự {viec} lại; "
                                                          f"bản mới trên git hoặc {viec} tay thì {viec}")
                continue
            ly_do = None
            if cfg.get("yeu_cau_chu_ky") and not commit.get("da_xac_minh"):
                ly_do = f"commit chưa được GitHub xác minh chữ ký (yeu_cau_chu_ky bật) — không tự {viec}, cần quản trị {viec} tay"
            elif x["trang_thai"] == "co_ban_moi":
                km = self.so.catalog.get(x["id"])
                goi = sorted(set(m.get("yeu_cau_goi") or []) - set(km.manifest.get("yeu_cau_goi", [])))
                mang = sorted(set(m.get("mang_ra") or []) - set(km.manifest["tai_nguyen"].get("mang_ra", [])))
                if goi or mang:
                    ly_do = ("bản mới XIN THÊM QUYỀN (" + "; ".join(
                        ([f"gói: {', '.join(goi)}"] if goi else []) + ([f"mạng ra: {', '.join(mang)}"] if mang else []))
                        + f") — không tự {viec}, cần quản trị duyệt và {viec} tay")
            if ly_do:
                self._ghi_chu[x["id"]] = (m["tree_sha"], ly_do)
                continue
            try:
                await self._cai(x["id"])
            except (LoiGit, LoiSo) as e:
                self._tu_dong_hong[x["id"]] = m["tree_sha"]
                self._ghi_chu[x["id"]] = (m["tree_sha"], f"tự {viec} hỏng: {e}")
                log.warning("Tự %s module %s hỏng: %s", viec, x["id"], e)

    # ───────────── vòng nền ─────────────

    def nhip_ke_tiep(self, chu_ky: float, bay_gio: float | None = None) -> float:
        """Giây tới lần thăm dò sau. Hạn mức còn < 10 → giãn tới giờ reset (vẫn giữ ETag)."""
        bay_gio = time.time() if bay_gio is None else bay_gio
        con = self.trang_thai.get("han_muc_con")
        reset = self.trang_thai.get("_reset_epoch")
        if con is not None and con < HAN_MUC_THAP and reset and reset > bay_gio:
            self.trang_thai["canh_bao"] = (f"hạn mức API GitHub còn {con} yêu cầu — giãn nhịp thăm dò tới "
                                           f"{self.trang_thai.get('reset_luc')}")
            return max(chu_ky, reset - bay_gio + 1)
        self.trang_thai["canh_bao"] = None
        return chu_ky

    def danh_thuc(self) -> None:
        self._danh_thuc.set()

    async def _cho(self, giay: float | None) -> None:
        try:
            if giay is None:
                await self._danh_thuc.wait()
            else:
                await asyncio.wait_for(self._danh_thuc.wait(), timeout=giay * self.HE_SO_CHO)
        except TimeoutError:
            pass
        self._danh_thuc.clear()

    async def chay_nen(self) -> None:
        while True:
            cfg = self.cau_hinh()
            if not cfg:
                await self._cho(None)  # chưa cấu hình: ngủ tới khi PUT /api/nguon-git đánh thức
                continue
            chu_ky = float(cfg["chu_ky_giay"])
            # Lời đánh thức tới TRƯỚC vòng này đã được chính vòng này phục vụ — xoá để lần chờ sau
            # không bật lại ngay (đo được: PUT cấu hình rồi khởi động → 2 yêu cầu liền nhau lúc hết hạn mức).
            self._danh_thuc.clear()
            try:
                async with self._khoa:
                    await self.mot_lan()
                    await self._tu_dong()
                self._lan_loi = 0
                cho = self.nhip_ke_tiep(chu_ky)
                self.trang_thai["thu_lai_luc"] = None
            except asyncio.CancelledError:
                raise
            except Exception as e:  # vòng nền KHÔNG được chết vì một lần đồng bộ hỏng
                self._lan_loi += 1
                cho = min(chu_ky * 2 ** self._lan_loi, self.TRAN_LUI_GIAY)
                if isinstance(e, LoiGit):
                    tin = str(e)
                    if e.cho_den:
                        cho = max(cho, e.cho_den - time.time() + 1)
                else:
                    log.exception("Lỗi nội bộ khi đồng bộ nguồn git")
                    tin = f"lỗi nội bộ: {type(e).__name__}"
                self.trang_thai["loi"] = self._che(tin)
                self.trang_thai["thu_lai_luc"] = iso(time.time() + cho)
                log.warning("Đồng bộ nguồn git hỏng (lần %d liên tiếp), thử lại sau %.0f s: %s",
                            self._lan_loi, cho, self._che(tin))
            try:
                self._luu_trang_thai()
            except OSError as e:
                log.warning("Không ghi được %s: %s", TEP_TRANG_THAI, e)
            await self._cho(cho)

    def bat_dau(self) -> None:
        self._tac_vu = asyncio.create_task(self.chay_nen(), name="web2api-nguon-git")

    async def dung(self) -> None:
        if self._tac_vu is not None:
            self._tac_vu.cancel()
            try:
                await self._tac_vu
            except asyncio.CancelledError:
                pass
            self._tac_vu = None
        if self._khach is not None:
            await self._khach.aclose()
            self._khach = None
