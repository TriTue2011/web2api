#!/usr/bin/env python3
"""Nhị phân OfficeCLI GIẢ cho kiểm thử (không phải nhị phân thật).

Ghi argv nhận được vào doi_so_cuoi.json, đánh dấu da_goi.log, rồi trả theo
OFFICE_GIA_CHE_DO: "echo" (mặc định), "ngu" (treo để kiểm hết giờ), "dai"
(100.000 ký tự để kiểm cắt đầu ra).
"""

import json
import os
import sys
import time
from pathlib import Path

goc = Path(__file__).resolve().parent
(goc / "doi_so_cuoi.json").write_text(json.dumps(sys.argv), encoding="utf-8")
(goc / "da_goi.log").write_text("goi", encoding="utf-8")

che_do = os.environ.get("OFFICE_GIA_CHE_DO", "echo")
if che_do == "ngu":
    time.sleep(120)
elif che_do == "dai":
    sys.stdout.write("x" * 100000)
else:
    sys.stdout.write("OK " + " ".join(sys.argv[1:]))
