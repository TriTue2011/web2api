# Nguồn: c2a-nv146/services/image_tags_service.py (commit d879b03)
# Port sang module web2api `anh-ai`:
# - Nguồn là hàm module dùng đường dẫn cố định (DATA_DIR / "image_tags.json").
#   Bản này bọc thành class KhoNhan nhận đường dẫn tệp JSON qua __init__
#   (module web2api lưu ở <WEB2API_DATA>/du_lieu/anh-ai/).
# - Thêm chmod: tệp ghi với quyền 0600, thư mục cha 0700.
# - Giữ nguyên semantics: JSON UTF-8, ensure_ascii=False, indent 2, làm sạch
#   trùng/rỗng như set_tags, xoa_nhan trả số ảnh bị ảnh hưởng.
"""Kho nhãn (tag) ảnh — một dict {đường-dẫn-tương-đối-ảnh: [nhãn...]} lưu JSON."""
from __future__ import annotations

import json
import os
from pathlib import Path


class KhoNhan:
    """Đọc/ghi nhãn ảnh trên một tệp JSON do caller chỉ định."""

    def __init__(self, tep_json: Path) -> None:
        self.tep_json = Path(tep_json)
        self._dam_bao_tep()

    def _dam_bao_tep(self) -> None:
        """Tạo thư mục cha (0700) và tệp rỗng (0600) nếu chưa có."""
        self.tep_json.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.tep_json.parent, 0o700)
        if not self.tep_json.exists():
            self.tep_json.write_text("{}", encoding="utf-8")
            os.chmod(self.tep_json, 0o600)

    def tai(self) -> dict[str, list[str]]:
        """Đọc toàn bộ nhãn. File hỏng/không đúng dạng → {}."""
        self._dam_bao_tep()
        try:
            data = json.loads(self.tep_json.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def luu(self, du_lieu: dict) -> None:
        """Ghi toàn bộ nhãn (JSON UTF-8, ensure_ascii=False, indent 2), chmod 0600."""
        self._dam_bao_tep()
        self.tep_json.write_text(
            json.dumps(du_lieu, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.chmod(self.tep_json, 0o600)

    def lay(self, anh_rel: str) -> list[str]:
        """Nhãn của một ảnh. Chưa có → []."""
        return self.tai().get(anh_rel, [])

    def dat(self, anh_rel: str, nhan: list[str]) -> list[str]:
        """Đặt nhãn cho ảnh: bỏ rỗng, bỏ trùng (giữ thứ tự), lưu rồi trả về
        danh sách đã làm sạch. Nhãn rỗng hết thì xoá mục ảnh."""
        data = self.tai()
        cleaned = list(dict.fromkeys(t.strip() for t in nhan if t.strip()))
        if cleaned:
            data[anh_rel] = cleaned
        else:
            data.pop(anh_rel, None)
        self.luu(data)
        return cleaned

    def xoa(self, anh_rel: str) -> None:
        """Xoá toàn bộ nhãn của một ảnh (chỉ ghi file khi có mục bị xoá)."""
        data = self.tai()
        if data.pop(anh_rel, None) is not None:
            self.luu(data)

    def xoa_nhan(self, nhan: str) -> int:
        """Xoá một nhãn khỏi mọi ảnh, trả số ảnh bị ảnh hưởng."""
        data = self.tai()
        count = 0
        for rel in list(data):
            if nhan in data[rel]:
                data[rel] = [t for t in data[rel] if t != nhan]
                if not data[rel]:
                    del data[rel]
                count += 1
        if count > 0:
            self.luu(data)
        return count

    def tat_ca_nhan(self) -> list[str]:
        """Tất cả nhãn đã dùng, không trùng, giữ thứ tự xuất hiện đầu tiên."""
        data = self.tai()
        seen: set[str] = set()
        result: list[str] = []
        for nhan_list in data.values():
            for t in nhan_list:
                if t not in seen:
                    seen.add(t)
                    result.append(t)
        return result
