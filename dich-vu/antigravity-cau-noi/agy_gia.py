#!/usr/bin/env python3
"""``agy`` GIẢ — mô phỏng đầu ra thật của ``agy`` để kiểm cầu nối KHÔNG CẦN mạng, KHÔNG tốn hạn mức.

Đặt ``AGY_BIN`` trỏ tới script này. Hành vi đọc từ ``$HOME/_agy_gia.json`` (tệp điều khiển của BẢN GIẢ — cầu nối
không bao giờ đọc nó). Mỗi lần chạy ghi MỘT dòng vào ``$HOME/_agy_gia_nhat_ky.jsonl``: argv, TÊN các biến môi
trường nhận được, thư mục làm việc, pid, mốc bắt đầu/kết thúc — để test chứng minh cầu nối truyền gì xuống.

Hỗ trợ:
- ``agy models``                          → «Fetching available models...» rồi ``<id>\\t<nhãn>`` mỗi dòng
- ``agy -p "/usage" --output-format json`` → JSON có bảng hạn mức (tách bằng tab) trong ``response``
- ``agy -p "…" --output-format json …``   → JSON kết quả như agy thật

Khoá điều khiển: ma_thoat, status, response, denied_actions, usage, models, models_ma_thoat, usage_table,
usage_ma_thoat, stdout_tho (in nguyên chuỗi này thay JSON), tre (giây ngủ trước khi trả lời), treo (ngủ 1 giờ và
sinh một tiến trình CHÁU cũng ngủ — ghi pid cháu vào nhật ký), tu_het_gio (ngủ theo --print-timeout rồi thoát 1).
"""

import json
import os
import subprocess
import sys
import time

HOME = os.environ.get("HOME", "")

_MAC_DINH = {
    "ma_thoat": 0,
    "status": "SUCCESS",
    "response": "Đây là câu trả lời giả từ agy.",
    "denied_actions": [],
    "usage": {"input_tokens": 13542, "output_tokens": 1200, "thinking_tokens": 1123,
              "cache_read_tokens": 0, "total_tokens": 14742},
    "models": [
        {"id": "gemini-3.8-flash-high", "nhan": "Gemini 3.8 Flash (High)"},
        {"id": "gemini-3.7-flash-medium", "nhan": "Gemini 3.7 Flash (Medium)"},
        {"id": "claude-sonnet-4-6", "nhan": "Claude Sonnet 4.6"},
        {"id": "gpt-oss-120b-medium", "nhan": "GPT OSS 120B (Medium)"},
        {"id": "mau-la-1", "nhan": "Model họ lạ"},
    ],
    "models_ma_thoat": 0,
    "usage_table": (
        "Gemini Models\tWeekly Limit Remaining\t100%\t2026-10-19T04:15:23Z\n"
        "Gemini Models\tFive Hour Limit Remaining\t100%\t2026-10-12T09:42:59Z\n"
        "Claude and GPT models\tWeekly Limit Remaining\t100%\t2026-10-19T04:42:54Z\n"
        "Claude and GPT models\tFive Hour Limit Remaining\t100%\t2026-10-12T09:42:59Z"
    ),
    "usage_ma_thoat": 0,
    "tre": 0,
    "treo": False,
    "tu_het_gio": False,
}


def _cau_hinh() -> dict:
    try:
        with open(os.path.join(HOME, "_agy_gia.json"), encoding="utf-8") as f:
            return {**_MAC_DINH, **json.load(f)}
    except (FileNotFoundError, ValueError):
        return dict(_MAC_DINH)


def _ghi(muc: dict) -> None:
    with open(os.path.join(HOME, "_agy_gia_nhat_ky.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(muc, ensure_ascii=False) + "\n")


def main():
    ch = _cau_hinh()
    args = sys.argv[1:]
    muc = {"argv": args, "env": sorted(os.environ), "cwd": os.getcwd(), "cwd_rong": not os.listdir("."),
           "pid": os.getpid(), "bat_dau": time.time()}

    if args[:1] == ["models"]:
        _ghi(muc)
        if ch["models_ma_thoat"]:
            sys.exit(ch["models_ma_thoat"])
        print("Fetching available models...")  # noqa: T201
        for m in ch["models"]:
            print(f"{m['id']}\t{m['nhan']}")  # noqa: T201
        return

    prompt = args[args.index("-p") + 1] if "-p" in args and args.index("-p") + 1 < len(args) else ""
    if prompt == "/usage":
        _ghi(muc)
        if ch["usage_ma_thoat"]:
            sys.exit(ch["usage_ma_thoat"])
        sys.stdout.write(json.dumps({"conversation_id": "gia-usage", "status": "SUCCESS",
                                     "response": ch["usage_table"], "denied_actions": []}, ensure_ascii=False))
        return

    if ch["treo"]:
        chau = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(3600)"])
        muc["pid_chau"] = chau.pid
        _ghi(muc)
        time.sleep(3600)
        sys.exit(1)
    _ghi(muc)
    if ch["tu_het_gio"]:
        han = args[args.index("--print-timeout") + 1].rstrip("s")
        time.sleep(float(han))
        sys.exit(1)
    if ch["tre"]:
        time.sleep(float(ch["tre"]))
    _ghi({"pid": os.getpid(), "ket_thuc": time.time()})

    if ch["ma_thoat"]:
        sys.stderr.write(f"agy lỗi giả (mã {ch['ma_thoat']}) tại {HOME}\n")
        sys.exit(ch["ma_thoat"])
    if "stdout_tho" in ch:
        sys.stdout.write(ch["stdout_tho"])
        return
    sys.stdout.write(json.dumps({
        "conversation_id": f"gia-{os.getpid()}", "status": ch["status"], "response": ch["response"],
        "duration_seconds": 0.3, "num_turns": 1, "usage": ch["usage"], "denied_actions": ch["denied_actions"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
