#!/usr/bin/env python3
"""Interactive Windows launcher; user paths never pass through cmd parsing."""

from __future__ import annotations

import argparse
import multiprocessing
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import unicodedata
from typing import TextIO
import webbrowser

from portable_runtime import install_source_dependencies, package_root, self_check, terminate_worker_tree, worker_command


def start_gui_worker(command: list[str], root: Path, environment: dict[str, str],
                     process_holder: dict, stop_requested: dict) -> subprocess.Popen:
    """Publish the worker, then honor a Stop click that raced with its launch."""
    process = subprocess.Popen(
        command, cwd=root, env=environment, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
        bufsize=1, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    process_holder["proc"] = process
    if stop_requested["value"]:
        terminate_worker_tree(process)
    return process


MESSAGES = {
    "zh-Hans": {
        "language": "语言 / 語言 / Language：1 简体中文  2 繁體中文  3 English（回车：自动选择）",
        "invalid_language": "请输入 1、2 或 3；直接回车将按系统语言选择。",
        "jp_optional": "日服目录可选：直接回车使用包内日服语音缓存。",
        "jp_prompt": "日服客户端目录（可选，回车使用包内缓存）： ",
        "bundled": "将使用包内日服语音缓存。",
        "jp_missing": "包内语音缓存不存在，请重新解压工具，或提供日服客户端目录。",
        "tw_prompt": "台服客户端目录（选择包含 appdata 的目录，也可直接选择 appdata）： ",
        "output_prompt": "输出目录（新建目录，不能与原台服目录相同）： ",
        "prefix_prompt": "语音范围（回车：阿蘭雯全部语音；输入 ALL：全部语音）： ",
        "limit_prompt": "最多处理数量（回车或 0：该范围内全部）： ",
        "limit_error": "替换数量不能小于 0。",
        "mode": "模式：完整重建，保留日服原始音频并更新受影响 .blob 的结束偏移表。",
        "scope": "目标范围：{scope}；数量：{count}。",
        "limit": "处理上限：{value}。",
        "start": "开始处理。详细进度和错误会保存到：{path}",
        "hint": "提示：重建或验证资源包时可能暂时没有新日志，但状态行会固定在同一位置刷新。",
        "done": "已完成（退出码 {code}）。日志：{path}",
        "failed": "失败（退出码 {code}）。日志：{path}",
        "cancelled": "已取消。",
        "error": "错误：{error}",
        "py_error": "需要 Python 3.11 或更新版本。",
    },
    "zh-Hant": {
        "language": "語言 / Language：1 简体中文  2 繁體中文  3 English（按 Enter：自動選擇）",
        "invalid_language": "請輸入 1、2 或 3；直接按 Enter 將依系統語言選擇。",
        "jp_optional": "日服目錄可選：直接按 Enter 使用套件內日服語音快取。",
        "jp_prompt": "日服客戶端目錄（可選，按 Enter 使用套件內快取）： ",
        "bundled": "將使用套件內日服語音快取。",
        "jp_missing": "套件內語音快取不存在，請重新解壓工具，或提供日服客戶端目錄。",
        "tw_prompt": "台服客戶端目錄（選擇包含 appdata 的目錄，也可直接選 appdata）： ",
        "output_prompt": "輸出目錄（建立新目錄，不可與原台服目錄相同）： ",
        "prefix_prompt": "語音範圍（按 Enter：阿蘭雯全部語音；輸入 ALL：全部語音）： ",
        "limit_prompt": "最多處理數量（按 Enter 或 0：該範圍內全部）： ",
        "limit_error": "替換數量不能小於 0。",
        "mode": "模式：完整重建，保留日服原始音訊並更新受影響 .blob 的結束偏移表。",
        "scope": "目標範圍：{scope}；數量：{count}。",
        "limit": "處理上限：{value}。",
        "start": "開始處理。詳細進度和錯誤會儲存到：{path}",
        "hint": "提示：重建或驗證資源包時可能暫時沒有新日誌，但狀態列會固定在同一位置更新。",
        "done": "已完成（退出碼 {code}）。日誌：{path}",
        "failed": "失敗（退出碼 {code}）。日誌：{path}",
        "cancelled": "已取消。",
        "error": "錯誤：{error}",
        "py_error": "需要 Python 3.11 或更新版本。",
    },
    "en": {
        "language": "Language / 语言 / 語言: 1 简体中文  2 繁體中文  3 English (Enter: auto-detect)",
        "invalid_language": "Enter 1, 2, or 3. Press Enter to auto-detect.",
        "jp_optional": "The JP directory is optional. Press Enter to use the bundled JP voice cache.",
        "jp_prompt": "JP client directory (optional, press Enter for bundled cache): ",
        "bundled": "Using the bundled JP voice cache.",
        "jp_missing": "Bundled voice cache is missing. Re-extract the package or provide a JP client directory.",
        "tw_prompt": "TW client directory (folder containing appdata; appdata itself is also accepted): ",
        "output_prompt": "Output directory (new directory; do not use the original TW directory): ",
        "prefix_prompt": "Voice scope (Enter: all Vo_NPC_Aranwen_ clips; type ALL: all voice clips): ",
        "limit_prompt": "Maximum clips (Enter or 0: all clips in this scope): ",
        "limit_error": "The replacement count cannot be negative.",
        "mode": "Mode: full rebuild. Original JP audio is retained and affected .blob end tables are updated.",
        "scope": "Target scope: {scope}; clips: {count}.",
        "limit": "Processing limit: {value}.",
        "start": "Starting. Detailed progress and errors will be saved to: {path}",
        "hint": "Note: rebuilding or validating a bundle may be quiet for a while; the status line refreshes in place.",
        "done": "Completed (exit code {code}). Log: {path}",
        "failed": "Failed (exit code {code}). Log: {path}",
        "cancelled": "Cancelled.",
        "error": "ERROR: {error}",
        "py_error": "Python 3.11 or newer is required.",
    },
}


def detect_language() -> str:
    """Return simplified Chinese, traditional Chinese, or English from the OS locale."""
    raw = os.environ.get("LC_ALL") or os.environ.get("LANG") or os.environ.get("LANGUAGE") or ""
    if not raw:
        try:
            import ctypes
            buffer = ctypes.create_unicode_buffer(85)
            if ctypes.windll.kernel32.GetUserDefaultLocaleName(buffer, len(buffer)):
                raw = buffer.value
        except (AttributeError, OSError):
            pass
    raw = raw.lower().replace("_", "-")
    if any(tag in raw for tag in ("zh-tw", "zh-hk", "zh-mo", "zh-hant")):
        return "zh-Hant"
    if raw.startswith("zh"):
        return "zh-Hans"
    return "en"


def choose_language() -> str:
    print(MESSAGES["en"]["language"], flush=True)
    value = input("> ").strip()
    if value == "1":
        return "zh-Hans"
    if value == "2":
        return "zh-Hant"
    if value == "3":
        return "en"
    if value:
        print(MESSAGES["en"]["invalid_language"], flush=True)
    return detect_language()


def msg(language: str, key: str, **kwargs: object) -> str:
    return MESSAGES[language][key].format(**kwargs)


def run_gui(root_dir: Path, language: str = "zh-Hans") -> int:
    """Launch the beginner-friendly Tk interface. CLI remains available with --cli."""
    from resource_progress import GuiProgressController, ProgressParser, load_completion_report
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk
    except Exception as exc:
        print(f"GUI unavailable: {exc}. Use --cli to continue.", flush=True)
        return 2
    tr = {
        "zh-Hans": {"title":"Mabinogi Mobile 日服语音资源替换", "jp":"日服目录（可选）", "tw":"台服目录（包含 appdata）", "out":"输出目录（新目录）", "browse":"浏览", "start":"开始替换", "help":"使用说明", "risk":"风险提示", "scope":"语音范围", "aranwen":"阿蘭雯（默认）", "all":"全部语音（ALL）", "limit":"最多数量（0=全部）", "ready":"请选择目录后开始。原始客户端只读，输出会写入新目录。", "running":"处理中，请勿关闭窗口…", "done":"处理完成，详情请查看 patch-report.json。", "failed":"处理失败，请查看日志和输出目录。", "sponsor":"支持作者", "jp_hint":"留空将使用包内日服语音缓存。", "required":"台服和输出目录不能为空。", "integer":"最多数量必须是整数。", "existing":"输出目录已存在且不为空，请选择空文件夹或新目录。", "close_running":"替换仍在进行，请等待完成后再关闭窗口。", "risk_text":"完整重建会改变 .blob 文件大小、偏移和哈希。请使用可丢弃副本和测试账号；启动器可能修复或拒绝修改文件。官方 manifest 不会被修改。"},
        "zh-Hant": {"title":"Mabinogi Mobile 日服語音資源替換", "jp":"日服目錄（可選）", "tw":"台服目錄（包含 appdata）", "out":"輸出目錄（新目錄）", "browse":"瀏覽", "start":"開始替換", "help":"使用說明", "risk":"風險提示", "scope":"語音範圍", "aranwen":"阿蘭雯（預設）", "all":"全部語音（ALL）", "limit":"最多數量（0=全部）", "ready":"請選擇目錄後開始。原始客戶端唯讀，輸出會寫入新目錄。", "running":"處理中，請勿關閉視窗…", "done":"處理完成，詳情請查看 patch-report.json。", "failed":"處理失敗，請查看日誌和輸出目錄。", "sponsor":"支持作者", "jp_hint":"留空將使用套件內日服語音快取。", "required":"台服和輸出目錄不能為空。", "integer":"最多數量必須是整數。", "existing":"輸出目錄已存在且不為空，請選擇空資料夾或新目錄。", "close_running":"替換仍在進行，請完成後再關閉視窗。", "risk_text":"完整重建會改變 .blob 檔案大小、偏移和雜湊。請使用可丟棄副本和測試帳號；啟動器可能修復或拒絕修改檔案。官方 manifest 不會被修改。"},
        "en": {"title":"Mabinogi Mobile JP Voice Resource Transfer", "jp":"JP client directory (optional)", "tw":"TW client directory (contains appdata)", "out":"Output directory (new folder)", "browse":"Browse", "start":"Start transfer", "help":"Instructions", "risk":"Risk notice", "scope":"Voice scope", "aranwen":"Aranwen (default)", "all":"All voices (ALL)", "limit":"Maximum clips (0=all)", "ready":"Choose the directories and start. Source clients are read-only; output is written to a new folder.", "running":"Working; please keep this window open…", "done":"Completed. See patch-report.json for details.", "failed":"The transfer failed. Check the log and output folder.", "sponsor":"Support the author", "jp_hint":"Leave blank to use the bundled JP voice cache.", "required":"TW and output directories are required.", "integer":"Maximum clips must be an integer.", "existing":"The output folder already exists and is not empty. Choose an empty or new folder.", "close_running":"The transfer is still running. Wait for it to finish before closing this window.", "risk_text":"Full rebuild changes .blob sizes, offsets, and hashes. Use a disposable copy and test account; the launcher may repair or reject modified files. The official manifest is not changed."},
    }[language]
    bundled_cache_available = all(
        (root_dir / name).is_file() for name in ("resource_cache.bin", "resource_cache.json")
    )
    documents = {
        "zh-Hans": {
            "help": (
                "使用说明\n\n"
                "1. 准备\n"
                "• 关闭游戏、平台启动器和更新器。\n"
                "• 建议使用可随时删除的台服客户端副本，并预留足够磁盘空间。\n\n"
                "2. 选择目录\n"
                "• 日服目录：可以留空，使用工具内置的日服语音缓存；填写后会读取你选择的日服客户端。\n"
                "• 台服目录：选择包含 appdata 的游戏根目录，也可以直接选择 appdata。\n"
                "• 输出目录：选择一个空目录。可以是已经存在的空文件夹；非空文件夹会被拒绝。不要选择原台服目录，也不要放在原台服目录里面。\n\n"
                "3. 选择范围\n"
                "• 默认范围是阿蘭雯（Vo_NPC_Aranwen_）的全部语音，适合先做小批量验证。\n"
                "• 选择“全部语音（ALL）”才会处理全量目标。\n"
                "• 最多数量填 0 表示处理所选范围内的全部语音；填写正整数可限制数量。\n\n"
                "4. 开始与检查\n"
                "• 点击“开始替换”后请保持窗口打开。第一次扫描日服可能较慢，后续会复用缓存。\n"
                "• 完成后查看输出目录\\appdata\\patch-report.json。transaction_status 应为 committed，failed 应为空。\n"
                "• 只有报告通过时，才把输出副本用于游戏测试。详细过程保存在工具目录的 run_transfer.log。"
            ),
            "risk": (
                "风险提示\n\n"
                "• 本工具采用完整资源包重建，会改变受影响 .blob 文件的大小、偏移和哈希；原台服和日服目录保持只读。\n"
                "• 官方 manifest 不会被修改。启动器、完整性检查或反作弊系统仍可能重新下载、修复、拒绝或阻止修改后的客户端；可能出现地图加载失败、语音缺失、强制下线或账号风险，无法保证在线使用安全。\n"
                "• 请使用可丢弃的客户端副本和测试账号，不要覆盖正在使用的原客户端。客户端更新后请重新扫描并生成新的输出，不要继续使用旧副本或旧缓存。\n"
                "• 出现失败、格式错误或校验错误时，不要使用输出副本；先查看 run_transfer.log 和 patch-report.json。\n"
                "• 包内语音资源仅供学习、研究和个人离线测试使用。下载后 24 小时内请自觉删除，不要上传或再次分发。"
            ),
            "sponsor_intro": "感谢你对工具开发的支持。赞助完全自愿，不影响工具的下载、安装和使用。请选择赞助渠道：",
            "close": "关闭",
            "sponsor_button": "赞助作者",
            "afdian": "爱发电",
            "patreon": "Patreon",
        },
        "zh-Hant": {
            "help": (
                "使用說明\n\n"
                "1. 準備\n"
                "• 關閉遊戲、平台啟動器和更新器。\n"
                "• 建議使用可隨時刪除的台服客戶端副本，並預留足夠磁碟空間。\n\n"
                "2. 選擇目錄\n"
                "• 日服目錄：可以留空，使用工具內建的日服語音快取；填寫後會讀取你選擇的日服客戶端。\n"
                "• 台服目錄：選擇包含 appdata 的遊戲根目錄，也可以直接選擇 appdata。\n"
                "• 輸出目錄：選擇一個空目錄。可以是已存在的空資料夾；非空資料夾會被拒絕。不要選擇原台服目錄，也不要放在原台服目錄裡面。\n\n"
                "3. 選擇範圍\n"
                "• 預設範圍是阿蘭雯（Vo_NPC_Aranwen_）的全部語音，適合先做小批量驗證。\n"
                "• 選擇「全部語音（ALL）」才會處理全量目標。\n"
                "• 最多數量填 0 表示處理所選範圍內的全部語音；填寫正整數可限制數量。\n\n"
                "4. 開始與檢查\n"
                "• 點擊「開始替換」後請保持視窗開啟。第一次掃描日服可能較慢，後續會重用快取。\n"
                "• 完成後查看輸出目錄\\appdata\\patch-report.json。transaction_status 應為 committed，failed 應為空。\n"
                "• 只有報告通過時，才把輸出副本用於遊戲測試。詳細過程保存在工具目錄的 run_transfer.log。"
            ),
            "risk": (
                "風險提示\n\n"
                "• 本工具採用完整資源包重建，會改變受影響 .blob 檔案的大小、偏移和雜湊；原台服和日服目錄保持唯讀。\n"
                "• 官方 manifest 不會被修改。啟動器、完整性檢查或反作弊系統仍可能重新下載、修復、拒絕或阻止修改後的客戶端；可能出現地圖載入失敗、語音缺失、強制登出或帳號風險，無法保證線上使用安全。\n"
                "• 請使用可丟棄的客戶端副本和測試帳號，不要覆蓋正在使用的原客戶端。客戶端更新後請重新掃描並生成新的輸出，不要繼續使用舊副本或舊快取。\n"
                "• 出現失敗、格式錯誤或校驗錯誤時，不要使用輸出副本；先查看 run_transfer.log 和 patch-report.json。\n"
                "• 套件內語音資源僅供學習、研究和個人離線測試使用。下載後 24 小時內請自覺刪除，不要上傳或再次分發。"
            ),
            "sponsor_intro": "感謝你對工具開發的支持。贊助完全自願，不影響工具的下載、安裝和使用。請選擇贊助渠道：",
            "close": "關閉",
            "sponsor_button": "贊助作者",
            "afdian": "愛發電",
            "patreon": "Patreon",
        },
        "en": {
            "help": (
                "Instructions\n\n"
                "1. Before you start\n"
                "• Close the game, platform launcher, and updater.\n"
                "• Use a disposable TW client copy and make sure you have enough free disk space.\n\n"
                "2. Choose folders\n"
                "• JP folder: leave it blank to use the bundled JP voice cache, or choose your JP client folder.\n"
                "• TW folder: choose the game folder containing appdata; selecting appdata itself is also supported.\n"
                "• Output folder: choose an empty folder. An existing empty folder is allowed; a non-empty folder is rejected. Do not choose the original TW folder or a folder inside it.\n\n"
                "3. Choose the scope\n"
                "• The default scope is all Vo_NPC_Aranwen_ clips for a small first test.\n"
                "• Select All voices (ALL) only when you want the full target set.\n"
                "• Maximum clips 0 means all clips in the selected scope; a positive number limits the run.\n\n"
                "4. Run and verify\n"
                "• Click Start transfer and keep the window open. The first JP scan can be slow; later runs reuse the cache.\n"
                "• When finished, inspect output\\appdata\\patch-report.json. transaction_status should be committed and failed should be empty.\n"
                "• Use the output copy for game testing only after the report passes. Full details are saved in run_transfer.log."
            ),
            "risk": (
                "Risk notice\n\n"
                "• This tool rebuilds affected resource bundles. .blob sizes, offsets, and hashes will change; the original JP and TW folders remain read-only.\n"
                "• The official manifest is not modified. The launcher, integrity checks, or anti-cheat may still redownload, repair, reject, or block the modified client. Map loading failures, missing voices, forced logout, or account risk are possible; online safety is not guaranteed.\n"
                "• Use a disposable client copy and test account. Do not overwrite the client you normally use. After a client update, scan again and create a new output instead of reusing an old copy or cache.\n"
                "• If validation fails, do not use the output copy. Check run_transfer.log and patch-report.json first.\n"
                "• Bundled voice resources are for learning, research, and personal offline testing only. Delete them yourself within 24 hours of downloading; do not upload or redistribute them."
            ),
            "sponsor_intro": "Thank you for supporting the tool. Sponsorship is completely voluntary and does not affect downloading, installing, or using the tool. Choose a channel:",
            "close": "Close",
            "sponsor_button": "Support the author",
            "afdian": "Afdian",
            "patreon": "Patreon",
        },
    }[language]
    if not bundled_cache_available:
        no_cache_help = {
            "zh-Hans": "• 日服目录：必须选择日服客户端根目录；本发布包不含日服语音缓存。首次扫描可能较慢，之后会复用工具目录中的缓存。",
            "zh-Hant": "• 日服目錄：必須選擇日服客戶端根目錄；本發布套件不含日服語音快取。首次掃描可能較慢，之後會重用工具目錄中的快取。",
            "en": "• JP folder: you must choose the JP client root; this release does not include a JP voice cache. The first scan can be slow; later scans reuse the cache in the tool folder.",
        }[language]
        old_help = {
            "zh-Hans": "• 日服目录：可以留空，使用工具内置的日服语音缓存；填写后会读取你选择的日服客户端。",
            "zh-Hant": "• 日服目錄：可以留空，使用工具內建的日服語音快取；填寫後會讀取你選擇的日服客戶端。",
            "en": "• JP folder: leave it blank to use the bundled JP voice cache, or choose your JP client folder.",
        }[language]
        documents["help"] = documents["help"].replace(old_help, no_cache_help)
    ui_text = {
        "zh-Hans": {
            "progress_idle": "进度：等待开始",
            "progress_start": "进度：正在启动",
            "progress": "进度：{phase} {current}/{total}",
            "phase": "进度：{phase}",
            "progress_done": "进度：已完成",
            "progress_stopped": "进度：已停止",
            "tw_scan": "扫描台服资源",
            "jp_scan": "扫描日服资源",
            "prepare": "准备资源包",
            "rebuild": "重建资源包",
            "matched": "匹配完成",
            "index": "检查台服语音目标",
            "matching": "匹配日服资源",
            "cache": "读取日服资源缓存",
            "stop": "停止",
            "stopping": "正在停止，请稍候…",
            "stopped": "已停止。输出副本不应视为完成品，请查看日志。",
            "jp_label": "日服目录（必选）",
            "jp_hint_required": "此版本不含日服缓存，请选择日服客户端根目录。",
            "jp_required": "此版本不含日服语音缓存，请选择日服客户端目录。",
        },
        "zh-Hant": {
            "progress_idle": "進度：等待開始",
            "progress_start": "進度：正在啟動",
            "progress": "進度：{phase} {current}/{total}",
            "phase": "進度：{phase}",
            "progress_done": "進度：已完成",
            "progress_stopped": "進度：已停止",
            "tw_scan": "掃描台服資源",
            "jp_scan": "掃描日服資源",
            "prepare": "準備資源包",
            "rebuild": "重建資源包",
            "matched": "匹配完成",
            "index": "檢查台服語音目標",
            "matching": "匹配日服資源",
            "cache": "讀取日服資源快取",
            "stop": "停止",
            "stopping": "正在停止，請稍候…",
            "stopped": "已停止。輸出副本不應視為完成品，請查看日誌。",
            "jp_label": "日服目錄（必選）",
            "jp_hint_required": "此版本不含日服快取，請選擇日服客戶端根目錄。",
            "jp_required": "此版本不含日服語音快取，請選擇日服客戶端目錄。",
        },
        "en": {
            "progress_idle": "Progress: waiting",
            "progress_start": "Progress: starting",
            "progress": "Progress: {phase} {current}/{total}",
            "phase": "Progress: {phase}",
            "progress_done": "Progress: complete",
            "progress_stopped": "Progress: stopped",
            "tw_scan": "scanning TW resources",
            "jp_scan": "scanning JP resources",
            "prepare": "preparing bundles",
            "rebuild": "rebuilding bundles",
            "matched": "matching complete",
            "index": "checking TW voice targets",
            "matching": "matching JP resources",
            "cache": "loading JP resource cache",
            "stop": "Stop",
            "stopping": "Stopping; please wait…",
            "stopped": "Stopped. Do not treat the output copy as complete; check the log.",
            "jp_label": "JP client directory (required)",
            "jp_hint_required": "This release has no JP cache; choose the JP client root.",
            "jp_required": "This release has no JP voice cache. Choose the JP client directory.",
        },
    }[language]
    stage_text = {
        "zh-Hans": {"copy": "复制台服客户端", "replace": "替换并验证资源包", "finalize": "写入报告与收尾", "budget": "整体百分比按阶段预算计算，不代表剩余时间；扫描、复制和替换分别计数。", "warning": "有 {count} 项日服来源音频与原台服相同，未产生音频变化。", "skipped_warning": "有 {count} 项因质量或容量限制保留原台服音频。", "failure_detail": "处理失败：{reason}。请查看日志和输出报告。", "reason_worker_failed": "资源处理进程失败", "reason_missing_report": "缺少有效的完成报告", "reason_report_failed": "报告未确认全部目标处理完成"},
        "zh-Hant": {"copy": "複製台服客戶端", "replace": "替換並驗證資源包", "finalize": "寫入報告與收尾", "budget": "整體百分比依階段預算計算，不代表剩餘時間；掃描、複製和替換分別計數。", "warning": "有 {count} 項日服來源音訊與原台服相同，未產生音訊變化。", "skipped_warning": "有 {count} 項因品質或容量限制保留原台服音訊。", "failure_detail": "處理失敗：{reason}。請查看日誌和輸出報告。", "reason_worker_failed": "資源處理程序失敗", "reason_missing_report": "缺少有效的完成報告", "reason_report_failed": "報告未確認全部目標處理完成"},
        "en": {"copy": "copying TW client", "replace": "replacing and verifying bundles", "finalize": "writing reports and finishing", "budget": "Overall percentage uses stage budgets, not estimated time remaining. Scanning, copying and replacement have separate counts.", "warning": "{count} JP source audio resources match the original TW audio; their audio did not change.", "skipped_warning": "{count} original TW audio resources were kept due to quality or capacity limits.", "failure_detail": "Transfer failed: {reason}. Check the log and output report.", "reason_worker_failed": "the resource worker failed", "reason_missing_report": "a valid completion report is missing", "reason_report_failed": "the report did not confirm completion of all targets"},
    }[language]
    ui_text.update(stage_text)
    ui_text.update({
        "zh-Hans": {"unit_files": "文件", "unit_clips": "语音", "unit_blobs": "资源包", "unit_steps": "步骤", "unit_index": "索引", "cache": "读取日服语音缓存"},
        "zh-Hant": {"unit_files": "檔案", "unit_clips": "語音", "unit_blobs": "資源包", "unit_steps": "步驟", "unit_index": "索引", "cache": "讀取日服語音快取"},
        "en": {"unit_files": "files", "unit_clips": "clips", "unit_blobs": "bundles", "unit_steps": "steps", "unit_index": "index", "cache": "loading JP voice cache"},
    }[language])
    ui_text["progress"] = {"zh-Hans": "整体 {overall}% | {phase} {counter}", "zh-Hant": "整體 {overall}% | {phase} {counter}", "en": "Overall {overall}% | {phase} {counter}"}[language]
    result_code = 0
    restart_code: dict[str, int | None] = {"code": None}
    running_state = {"running": False}
    process_holder: dict[str, subprocess.Popen[str] | None] = {"proc": None}
    stop_requested = {"value": False}
    try:
        win = tk.Tk()
    except Exception as exc:
        print(f"GUI unavailable: {exc}. Use --cli to continue.", flush=True)
        return 2
    win.title(tr["title"]); win.geometry("760x600"); win.minsize(680, 500)
    frame = ttk.Frame(win, padding=14); frame.pack(fill="both", expand=True)
    language_labels = {"zh-Hans": "简体中文", "zh-Hant": "繁體中文", "en": "English"}
    language_values = {value: key for key, value in language_labels.items()}
    lang_var = tk.StringVar(value=language_labels.get(language, "English"))
    ttk.Label(frame, text="Language / 语言 / 語言").grid(row=0, column=0, sticky="w")
    lang_box = ttk.Combobox(frame, textvariable=lang_var, values=list(language_values), state="readonly", width=12); lang_box.grid(row=0, column=1, sticky="w", pady=(0, 10))
    def change_language(_event: object = None) -> None:
        selected = language_values.get(lang_var.get())
        if selected in MESSAGES:
            win.destroy()
            restart_code["code"] = run_gui(root_dir, selected)
    lang_box.bind("<<ComboboxSelected>>", change_language)
    vars_ = {k: tk.StringVar() for k in ("jp", "tw", "out")}
    for row, key in enumerate(("jp", "tw", "out"), 1):
        label_text = ui_text["jp_label"] if key == "jp" and not bundled_cache_available else tr[key]
        ttk.Label(frame, text=label_text).grid(row=row, column=0, sticky="w", pady=5)
        ttk.Entry(frame, textvariable=vars_[key]).grid(row=row, column=1, columnspan=2, sticky="ew", pady=5)
        ttk.Button(frame, text=tr["browse"], command=lambda k=key: vars_[k].set(filedialog.askdirectory() or vars_[k].get())).grid(row=row, column=3, padx=5)
        if key == "jp":
            jp_hint = ui_text["jp_hint_required"] if not bundled_cache_available else tr["jp_hint"]
            ttk.Label(frame, text=jp_hint).grid(row=row, column=4, sticky="w")
    ttk.Label(frame, text=tr["scope"]).grid(row=4, column=0, sticky="w", pady=5)
    scope = tk.StringVar(value="aranwen")
    ttk.Radiobutton(frame, text=tr["aranwen"], variable=scope, value="aranwen").grid(row=4, column=1, sticky="w")
    ttk.Radiobutton(frame, text=tr["all"], variable=scope, value="all").grid(row=4, column=2, sticky="w")
    ttk.Label(frame, text=tr["limit"]).grid(row=5, column=0, sticky="w", pady=5)
    limit_var = tk.StringVar(value="0"); ttk.Entry(frame, textvariable=limit_var, width=10).grid(row=5, column=1, sticky="w")
    status = tk.StringVar(value=tr["ready"]); ttk.Label(frame, textvariable=status, wraplength=700).grid(row=6, column=0, columnspan=5, sticky="w", pady=10)
    progress_text = tk.StringVar(value=ui_text["progress_idle"])
    ttk.Label(frame, textvariable=progress_text, wraplength=700).grid(row=7, column=0, columnspan=5, sticky="w")
    progress_widget = ttk.Progressbar(frame, orient="horizontal", mode="determinate", maximum=100, value=0)
    progress_widget.grid(row=8, column=0, columnspan=5, sticky="ew", pady=4)
    ttk.Label(frame, text=ui_text["budget"], wraplength=700).grid(row=9, column=0, columnspan=5, sticky="w", pady=(0, 4))
    log_box = tk.Text(frame, height=16, state="disabled", wrap="none"); log_box.grid(row=10, column=0, columnspan=5, sticky="nsew")
    frame.columnconfigure(1, weight=1); frame.columnconfigure(2, weight=1); frame.rowconfigure(10, weight=1)
    button_row = ttk.Frame(frame); button_row.grid(row=11, column=0, columnspan=5, sticky="ew", pady=10)
    def add_log(text: str) -> None:
        log_box.configure(state="normal"); log_box.insert("end", text); log_box.see("end"); log_box.configure(state="disabled")
    def show_document(title: str, content: str) -> None:
        dialog = tk.Toplevel(win)
        dialog.title(title)
        dialog.geometry("720x600")
        dialog.minsize(600, 420)
        dialog.transient(win)
        dialog_frame = ttk.Frame(dialog, padding=12)
        dialog_frame.pack(fill="both", expand=True)
        text = tk.Text(dialog_frame, wrap="word", height=24, width=80)
        scrollbar = ttk.Scrollbar(dialog_frame, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scrollbar.set)
        text.insert("1.0", content)
        text.configure(state="disabled")
        text.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        ttk.Button(dialog_frame, text=documents["close"], command=dialog.destroy).grid(row=1, column=0, columnspan=2, pady=(10, 0))
        dialog_frame.columnconfigure(0, weight=1)
        dialog_frame.rowconfigure(0, weight=1)
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
        dialog.grab_set()
        dialog.focus_set()

    def show_help() -> None:
        show_document(tr["help"], documents["help"])

    def show_risk() -> None:
        show_document(tr["risk"], documents["risk"])

    def show_sponsor() -> None:
        dialog = tk.Toplevel(win)
        dialog.title(documents["sponsor_button"])
        dialog.geometry("520x300")
        dialog.minsize(460, 260)
        dialog.transient(win)
        dialog_frame = ttk.Frame(dialog, padding=18)
        dialog_frame.pack(fill="both", expand=True)
        ttk.Label(dialog_frame, text=documents["sponsor_intro"], wraplength=470, justify="left").pack(anchor="w", pady=(0, 16))
        for label, url in ((documents["afdian"], "https://afdian.com/a/minamichiwa"), (documents["patreon"], "https://www.patreon.com/cw/chiwaminami")):
            row = ttk.Frame(dialog_frame)
            row.pack(fill="x", pady=5)
            ttk.Button(row, text=label, command=lambda target=url: webbrowser.open(target)).pack(side="left")
            ttk.Label(row, text=url).pack(side="left", padx=10)
        ttk.Button(dialog_frame, text=documents["close"], command=dialog.destroy).pack(pady=(14, 0))
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
        dialog.grab_set()
        dialog.focus_set()
    ttk.Button(button_row, text=tr["help"], command=show_help).pack(side="left")
    ttk.Button(button_row, text=tr["risk"], command=show_risk).pack(side="left", padx=6)
    ttk.Button(button_row, text=documents["sponsor_button"], command=show_sponsor).pack(side="right")
    stop_btn = ttk.Button(button_row, text=ui_text["stop"], state="disabled")
    stop_btn.pack(side="right", padx=6)
    start_btn = ttk.Button(button_row, text=tr["start"]); start_btn.pack(side="right", padx=6)

    def show_completion(text: str, warning: bool) -> None:
        dialog = messagebox.showwarning if warning else messagebox.showinfo
        dialog(tr["title"], text, parent=win)

    progress_controller = GuiProgressController(
        progress_widget, progress_text, status,
        {"scan_tw": ui_text["tw_scan"], "scan_jp": ui_text["jp_scan"], "copy": ui_text["copy"],
         "replace": ui_text["replace"], "finalize": ui_text["finalize"], "done": ui_text["finalize"],
         "scan_jp:cache": ui_text["cache"],
         **{
             "zh-Hans": {"replace:rebuild": "重建资源包", "replace:verify": "校验重建资源包", "replace:commit": "写入已校验资源包", "finalize:commit": "提交已校验资源包"},
             "zh-Hant": {"replace:rebuild": "重建資源包", "replace:verify": "驗證重建資源包", "replace:commit": "寫入已驗證資源包", "finalize:commit": "提交已驗證資源包"},
             "en": {"replace:rebuild": "rebuilding bundles", "replace:verify": "verifying rebuilt bundles", "replace:commit": "writing verified bundles", "finalize:commit": "committing verified bundles"},
         }[language]},
        {**tr, **ui_text}, show_completion,
    )

    def finish_ui(code: int, was_stopped: bool, token: int, output_path: Path) -> None:
        nonlocal result_code
        if not progress_controller.state.accepts(token):
            return
        running_state["running"] = False
        process_holder["proc"] = None
        stop_btn.configure(state="disabled")
        start_btn.configure(state="normal")
        lang_box.configure(state="readonly")
        report_path = output_path / "appdata" / "patch-report.json"
        if not report_path.is_file():
            report_path = output_path / "patch-report.json"
        result = progress_controller.finish(code, was_stopped, load_completion_report(report_path), token)
        result_code = 0 if result and result.success else 130 if result and result.cancelled else code or 2

    def stop_transfer() -> None:
        if not running_state["running"]:
            return
        stop_requested["value"] = True
        stop_btn.configure(state="disabled")
        status.set(ui_text["stopping"])
        proc = process_holder.get("proc")
        if proc is not None and proc.poll() is None:
            terminate_worker_tree(proc)
            target = proc
            def force_kill() -> None:
                if process_holder.get("proc") is target and target.poll() is None:
                    terminate_worker_tree(target)
            win.after(3000, force_kill)
    stop_btn.configure(command=stop_transfer)

    def start() -> None:
        tw, out, jp = vars_["tw"].get().strip(), vars_["out"].get().strip(), vars_["jp"].get().strip()
        if not tw or not out:
            messagebox.showerror(tr["risk"], tr["required"]); return
        if not jp and not bundled_cache_available:
            messagebox.showerror(tr["risk"], ui_text["jp_required"]); return
        out_path = Path(out).expanduser()
        if out_path.exists():
            try:
                output_has_files = any(out_path.iterdir())
            except (OSError, NotADirectoryError):
                output_has_files = True
            if output_has_files:
                messagebox.showerror(tr["risk"], tr["existing"]); return
        try: limit = int(limit_var.get() or "0")
        except ValueError: messagebox.showerror(tr["risk"], tr["integer"]); return
        if limit < 0:
            messagebox.showerror(tr["risk"], msg(language, "limit_error")); return
        prefix = "" if scope.get() == "all" else "Vo_NPC_Aranwen_"
        command = worker_command(root_dir, ["--tw-root", tw, "--max-clips", str(limit), "--full-rebuild", "--output-copy", out])
        if prefix: command.extend(["--include", "^" + re.escape(prefix)])
        if jp: command.extend(["--jp-root", jp])
        else: command.extend(["--cache-dir", str(root_dir)])
        stop_requested["value"] = False
        start_btn.configure(state="disabled"); stop_btn.configure(state="normal"); lang_box.configure(state="disabled"); running_state["running"] = True
        token = progress_controller.begin()
        status.set(tr["running"]); log_box.configure(state="normal"); log_box.delete("1.0", "end"); log_box.configure(state="disabled")
        log_path = root_dir / "run_transfer.log"
        parser = ProgressParser()
        def deliver_line(line: str) -> None:
            if not progress_controller.state.accepts(token):
                return
            parsed = parser.parse(line)
            if parsed is not None:
                progress_controller.update(parsed, token)
            if not line.startswith("RESOURCE_TRANSFER_PROGRESS "):
                add_log(line)
        def worker() -> None:
            nonlocal result_code
            try:
                env = os.environ.copy(); env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
                proc = start_gui_worker(command, root_dir, env, process_holder, stop_requested)
                with log_path.open("w", encoding="utf-8") as log:
                    for line in proc.stdout or []:
                        log.write(line); log.flush(); win.after(0, deliver_line, line)
                code = proc.wait(); result_code = code
                was_stopped = stop_requested["value"]
                win.after(0, finish_ui, code, was_stopped, token, out_path)
            except Exception as exc:
                result_code = 2
                message = f"ERROR: {exc}\n"
                try:
                    log_path.write_text(message, encoding="utf-8")
                except OSError:
                    pass
                win.after(0, deliver_line, message)
                win.after(0, finish_ui, 2, stop_requested["value"], token, out_path)
        threading.Thread(target=worker, daemon=True).start()
    start_btn.configure(command=start)
    def close_window() -> None:
        if running_state["running"]:
            messagebox.showwarning(tr["risk"], tr["close_running"])
            return
        win.destroy()
    win.protocol("WM_DELETE_WINDOW", close_window)
    win.mainloop(); return result_code if restart_code["code"] is None else restart_code["code"]


def ask_path(message: str, required: bool = True, empty_message: str = "A path cannot be empty. Please enter it again.") -> str:
    while True:
        value = input(message).strip().strip('"').strip()
        if value or not required:
            return value
        print(empty_message, flush=True)


def elapsed_text(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def progress_bar(current: int, total: int, width: int = 24) -> str:
    if total <= 0:
        return "[------------------------]"
    ratio = max(0.0, min(1.0, current / total))
    filled = int(ratio * width)
    return "[" + "#" * filled + "-" * (width - filled) + f"] {ratio * 100:5.1f}%"


def console_width(text: str) -> int:
    return sum(
        0 if unicodedata.combining(char) else 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
        for char in text
    )


def fit_console_line(text: str, columns: int) -> str:
    if console_width(text) <= columns:
        return text
    suffix = "." * min(3, columns)
    available = max(0, columns - len(suffix))
    result: list[str] = []
    used = 0
    for char in text:
        width = console_width(char)
        if used + width > available:
            break
        result.append(char)
        used += width
    return "".join(result) + suffix


class ConsoleStatus:
    """Keep one status row without requiring ANSI support in Windows cmd."""

    def __init__(self, stream: TextIO | None = None) -> None:
        self.stream = sys.stdout if stream is None else stream
        self.interactive = self.stream.isatty()
        self.drawn_width = 0

    def __enter__(self) -> ConsoleStatus:
        return self

    def __exit__(self, *args: object) -> None:
        self.clear()

    def columns(self) -> int:
        try:
            width = os.get_terminal_size(self.stream.fileno()).columns
        except (AttributeError, OSError, ValueError):
            width = shutil.get_terminal_size(fallback=(80, 24)).columns
        # Leave the last cell empty so the console does not wrap to another row.
        return max(1, width - 1)

    def clear(self) -> None:
        if self.interactive and self.drawn_width:
            self.stream.write("\r" + " " * min(self.drawn_width, self.columns()) + "\r")
            self.stream.flush()
            self.drawn_width = 0

    def update(self, text: str) -> None:
        if not self.interactive:
            self.stream.write(text + "\n")
        else:
            columns = self.columns()
            line = fit_console_line(text, columns)
            width = console_width(line)
            padding = max(0, min(self.drawn_width, columns) - width)
            self.stream.write("\r" + line + " " * padding)
            self.drawn_width = width
        self.stream.flush()

    def write_output(self, text: str) -> None:
        self.clear()
        self.stream.write(text)
        if not text.endswith("\n"):
            self.stream.write("\n")
        self.stream.flush()


def run_logged_process(
    command: list[str],
    root: Path,
    log_path: Path,
    environment: dict[str, str],
    header: str,
    language: str = "zh-Hans",
) -> int:
    """Run the worker while showing progress even when it is quiet.

    Rebuilding a large UnityFS bundle can spend several seconds without writing
    a line. A reader thread and a one-second status refresh keep the batch
    window visibly alive. Original output and five-second heartbeats remain in
    the log.
    """

    from resource_progress import PROGRESS_PREFIX, ProgressParser, ProgressState

    progress_parser = ProgressParser()
    progress_state = ProgressState()
    progress_token = progress_state.begin()
    stage_names = {
        "zh-Hans": {"scan_tw": "正在扫描台服资源", "scan_jp": "正在扫描日服资源", "copy": "正在复制台服客户端", "replace": "正在替换并验证资源包", "finalize": "正在写入报告与收尾", "done": "报告已写入，等待进程结束"},
        "zh-Hant": {"scan_tw": "正在掃描台服資源", "scan_jp": "正在掃描日服資源", "copy": "正在複製台服客戶端", "replace": "正在替換並驗證資源包", "finalize": "正在寫入報告與收尾", "done": "報告已寫入，等待程序結束"},
        "en": {"scan_tw": "Scanning TW resources", "scan_jp": "Scanning JP resources", "copy": "Copying TW client", "replace": "Replacing and verifying bundles", "finalize": "Writing reports and finishing", "done": "Report written; waiting for worker exit"},
    }[language]
    output_queue: queue.Queue[tuple[str, str | None]] = queue.Queue()
    started = time.monotonic()
    phase_names = {
        "zh-Hans": {"start": "正在启动替换进程", "tw": "正在扫描台服资源", "jp": "正在扫描日服资源", "targets": "正在检查台服语音目标", "matching": "正在扫描日服匹配资源", "cache": "正在读取包内日服资源缓存", "prepare": "正在准备资源包", "rebuild": "正在重建并验证资源包", "matched": "已完成匹配，准备重建输出副本", "finish": "正在写入报告并完成收尾", "label": "状态"},
        "zh-Hant": {"start": "正在啟動替換程序", "tw": "正在掃描台服資源", "jp": "正在掃描日服資源", "targets": "正在檢查台服語音目標", "matching": "正在掃描日服匹配資源", "cache": "正在讀取套件內日服資源快取", "prepare": "正在準備資源包", "rebuild": "正在重建並驗證資源包", "matched": "已完成匹配，準備重建輸出副本", "finish": "正在寫入報告並完成收尾", "label": "狀態"},
        "en": {"start": "Starting replacement process", "tw": "Scanning TW resources", "jp": "Scanning JP resources", "targets": "Indexing TW voice targets", "matching": "Scanning matching JP resources", "cache": "Loading bundled JP resource cache", "prepare": "Preparing resource bundles", "rebuild": "Rebuilding and validating resource bundles", "matched": "Matching complete; preparing output copy", "finish": "Writing reports and finishing", "label": "Status"},
    }[language]
    phase = phase_names["start"]
    scan_current = 0
    scan_total = 0
    blob_current = 0
    blob_total = 0
    spinner = "|/-\\"
    spinner_index = 0
    process = subprocess.Popen(
        command,
        cwd=root,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )

    def pump_output() -> None:
        assert process.stdout is not None
        try:
            for line in process.stdout:
                output_queue.put(("line", line))
        finally:
            process.stdout.close()
            output_queue.put(("eof", None))

    reader = threading.Thread(target=pump_output, name="voice-transfer-output", daemon=True)
    reader.start()
    console = ConsoleStatus()
    eof = False
    last_heartbeat = started - 5.0
    last_refresh = started - 1.0
    with log_path.open("w", encoding="utf-8") as log, console:
        log.write(header)
        log.flush()
        while True:
            try:
                kind, value = output_queue.get(timeout=1.0)
            except queue.Empty:
                kind, value = "", None
            if kind == "line" and value is not None:
                line = value
                log.write(line)
                log.flush()
                progress_only = False
                event = progress_parser.parse(line)
                if event is not None and progress_state.apply(event, progress_token):
                    phase = stage_names[event.stage]
                match = re.search(r"(?:TW|JP) (?:scan files |\[)(\d+)/(\d+)", line)
                if match:
                    progress_only = True
                    scan_current, scan_total = int(match.group(1)), int(match.group(2))
                    side = "台服" if line.startswith("TW") else "日服"
                    phase = phase_names["tw"] if side == "台服" else phase_names["jp"]
                else:
                    blob_match = re.search(r"Preparing blob (\d+)/(\d+):", line)
                    batch_match = re.search(r"Preparing (\d+) blob batches", line)
                    if batch_match:
                        blob_current, blob_total = 0, int(batch_match.group(1))
                        phase = phase_names["prepare"]
                    elif blob_match:
                        progress_only = True
                        blob_current, blob_total = int(blob_match.group(1)), int(blob_match.group(2))
                        phase = phase_names["rebuild"]
                if "Indexing TW voice targets" in line:
                    phase = phase_names["targets"]
                elif "Scanning matching JP resources" in line:
                    phase = phase_names["matching"]
                elif "Loading bundled resource cache" in line:
                    phase = phase_names["cache"]
                elif "Selected " in line and "compatible clips" in line:
                    phase = phase_names["matched"]
                    scan_current = scan_total = 0
                    blob_current = blob_total = 0
                elif "Prepared structure-preserving blob" in line:
                    progress_only = True
                    phase = phase_names["rebuild"]
                elif line.startswith("JP target names found after "):
                    progress_only = True
                elif "Created patched copy" in line:
                    phase = phase_names["finish"]
                if progress_parser.structured and progress_state.event is not None:
                    phase = stage_names[progress_state.event.stage]
                if not line.startswith(PROGRESS_PREFIX) and not (console.interactive and progress_only):
                    console.write_output(line)
            elif kind == "eof":
                eof = True

            now = time.monotonic()
            if process.poll() is None and (
                now - last_refresh >= 1.0 or (console.interactive and kind == "line")
            ):
                spinner_char = spinner[spinner_index % len(spinner)]
                spinner_index += 1
                if progress_parser.structured and progress_state.event is not None:
                    event = progress_state.event
                    counter = f"{event.current}/{event.total if event.total is not None else '?'}" if event.unit or event.total is not None else f"[{spinner_char}]"
                    progress = f"{int(progress_state.overall)}% {counter}"
                elif scan_total:
                    progress = progress_bar(scan_current, scan_total)
                elif blob_total:
                    progress = progress_bar(blob_current, blob_total)
                else:
                    progress = f"[{spinner_char}]"
                status = f"[{phase_names['label']}] {phase} {progress} | {elapsed_text(now - started)}"
                if console.interactive or now - last_heartbeat >= 5.0:
                    console.update(status)
                if now - last_heartbeat >= 5.0:
                    log.write(status + "\n")
                    log.flush()
                    last_heartbeat = now
                last_refresh = now

            if eof and process.poll() is not None and output_queue.empty():
                break
        code = process.wait()
        final_labels = {"zh-Hans": "状态", "zh-Hant": "狀態", "en": "Status"}
        final_words = {"zh-Hans": "子进程已结束，退出码", "zh-Hant": "子程序已結束，退出碼", "en": "Worker finished, exit code"}
        final = f"[{final_labels.get(language, 'Status')}] {final_words.get(language, 'Worker finished, exit code')} {code}, " \
            f"elapsed {elapsed_text(time.monotonic() - started)}"
        console.write_output(final + "\n")
        log.write(final + "\n")
    reader.join(timeout=2)
    return code


def main() -> int:
    # Frozen multiprocessing invokes this executable with private bootstrap args.
    multiprocessing.freeze_support()
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    if sys.argv[1:2] == ["--worker"]:
        from transfer_jp_voice_resources import main as transfer_main
        sys.argv = [sys.argv[0], *sys.argv[2:]]
        return transfer_main()
    if sys.argv[1:] == ["--self-check"]:
        return self_check()
    if len(sys.argv) == 1:
        sys.argv.extend(["transfer", "--gui"])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("transfer", "scan", "dependencies"))
    parser.add_argument("--gui", action="store_true", help="Use the Tk graphical interface")
    parser.add_argument("--cli", action="store_true", help="Use the text interface")
    args = parser.parse_args()
    root = package_root()
    log_path = root / ("install_dependencies.log" if args.mode == "dependencies" else f"run_{args.mode}.log")
    language = detect_language()
    try:
        if sys.version_info < (3, 11):
            raise RuntimeError(msg(language, "py_error"))
        if args.gui and args.mode == "transfer":
            return run_gui(root, language)
        if args.mode == "dependencies":
            return self_check() if getattr(sys, "frozen", False) else install_source_dependencies(root)
        else:
            language = choose_language()
            bundled_cache_available = all(
                (root / name).is_file() for name in ("resource_cache.bin", "resource_cache.json")
            )
            if bundled_cache_available:
                print(msg(language, "jp_optional"), flush=True)
                jp = ask_path(msg(language, "jp_prompt"), required=False)
            else:
                required_prompt = {
                    "zh-Hans": "本版本不含日服语音缓存，请输入日服客户端目录： ",
                    "zh-Hant": "本版本不含日服語音快取，請輸入日服客戶端目錄： ",
                    "en": "This release has no JP voice cache. Enter the JP client directory: ",
                }[language]
                jp = ask_path(required_prompt, required=True)
            if not jp:
                print(msg(language, "bundled"), flush=True)
            empty_path = {"zh-Hans": "路径不能为空，请重新输入。", "zh-Hant": "路徑不能為空，請重新輸入。", "en": "A path cannot be empty. Please enter it again."}[language]
            tw = ask_path(msg(language, "tw_prompt"), empty_message=empty_path)
            if args.mode == "transfer":
                output = ask_path(msg(language, "output_prompt"), empty_message=empty_path)
                prefix = input(msg(language, "prefix_prompt")).strip()
                if not prefix:
                    prefix = "Vo_NPC_Aranwen_"
                limit_text = input(msg(language, "limit_prompt")).strip() or "0"
                limit = int(limit_text)
                if limit < 0:
                    raise ValueError(msg(language, "limit_error"))
                if prefix.upper() == "ALL":
                    prefix = ""
                print(msg(language, "mode"), flush=True)
                print(msg(language, "scope", scope=prefix or "all voice clips", count="all matching" if limit == 0 else limit), flush=True)
            else:
                output = ""
                limit = 5
            print(msg(language, "limit", value="all matching clips" if limit == 0 else str(limit) + " clips maximum"), flush=True)
            command = worker_command(root, ["--tw-root", tw, "--max-clips", str(limit)])
            if args.mode == "transfer" and prefix:
                import re
                command.extend(["--include", "^" + re.escape(prefix)])
            if jp:
                command.extend(["--jp-root", jp])
            else:
                command.extend(["--cache-dir", str(root)])
            if args.mode == "transfer":
                command.append("--full-rebuild")
            if args.mode == "transfer":
                command.extend(["--output-copy", output])
            else:
                command.extend(["--dry-run", "--report-dir", str(root / "scan-reports")])
        environment = os.environ.copy()
        environment["PYTHONUTF8"] = "1"
        environment["PYTHONIOENCODING"] = "utf-8"
        print(msg(language, "start", path=log_path), flush=True)
        print(msg(language, "hint"), flush=True)
        header = f"mode: {args.mode}\npython: {sys.executable}\n"
        if args.mode != "dependencies":
            header += f"audio source: {'local-jp' if jp else 'bundled-cache'}\n"
        code = run_logged_process(command, root, log_path, environment, header, language)
        with log_path.open("a", encoding="utf-8") as log:
            log.write(f"\nexit code: {code}\n")
        print(msg(language, "done" if code == 0 else "failed", code=code, path=log_path), flush=True)
        return code
    except (EOFError, KeyboardInterrupt):
        print(msg(language, "cancelled"), flush=True)
        return 130
    except Exception as exc:
        message = msg(language, "error", error=exc)
        print(message, flush=True)
        try:
            log_path.write_text(message + "\n", encoding="utf-8")
        except OSError:
            pass
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
