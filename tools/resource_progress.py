"""Worker progress protocol and terminal GUI state.

Overall percentages allocate a fixed budget to each stage. They are not an
estimate of elapsed work or remaining time; the stage counters are real counts.
Only a successful process exit plus a committed patch report permits 100%.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
from typing import Any, Callable


PROGRESS_PREFIX = "RESOURCE_TRANSFER_PROGRESS "
STAGE_BUDGETS = {
    "scan_tw": (0, 10),
    "scan_jp": (10, 20),
    "copy": (20, 50),
    "replace": (50, 95),
    "finalize": (95, 99),
    "done": (99, 99),
}


@dataclass(frozen=True)
class ProgressEvent:
    stage: str
    current: int = 0
    total: int | None = None
    unit: str = ""
    complete: bool = False
    operation: str = ""


def format_progress(event: ProgressEvent) -> str:
    return PROGRESS_PREFIX + json.dumps({"format": 1, **asdict(event)}, ensure_ascii=True, separators=(",", ":"))


def emit_progress(stage: str, current: int = 0, total: int | None = None, *,
                  unit: str = "", complete: bool = False, operation: str = "",
                  enabled: bool = True) -> None:
    if enabled:
        print(format_progress(ProgressEvent(stage, current, total, unit, complete, operation)), flush=True)


def parse_progress_line(line: str) -> ProgressEvent | None:
    if not line.startswith(PROGRESS_PREFIX):
        return None
    try:
        value = json.loads(line[len(PROGRESS_PREFIX):])
        if not isinstance(value, dict) or value.get("format") != 1 or value.get("stage") not in STAGE_BUDGETS:
            return None
        current, total = value.get("current", 0), value.get("total")
        if type(current) is not int or current < 0:
            return None
        if total is not None and (type(total) is not int or total < 0 or current > total):
            return None
        unit, operation = value.get("unit", ""), value.get("operation", "")
        complete = value.get("complete", False)
        if not isinstance(unit, str) or not isinstance(operation, str) or type(complete) is not bool:
            return None
        return ProgressEvent(value["stage"], current, total, unit, complete, operation)
    except (ValueError, TypeError):
        return None


class ProgressParser:
    """Accept the protocol, with a fallback for older worker text output."""

    def __init__(self) -> None:
        self.structured = False
        self.replace_current = 0
        self.replace_total: int | None = None

    def parse(self, line: str) -> ProgressEvent | None:
        event = parse_progress_line(line)
        if event is not None:
            self.structured = True
            return event
        if self.structured or line.startswith(PROGRESS_PREFIX):
            return None
        match = re.search(r"(TW|JP)\s+(?:scan files\s+|\[)(\d+)/(\d+)", line)
        if match:
            return ProgressEvent("scan_tw" if match[1] == "TW" else "scan_jp", int(match[2]), int(match[3]), "files")
        if "Indexing TW voice targets" in line:
            return ProgressEvent("scan_tw", operation="index")
        if "Scanning matching JP resources" in line:
            return ProgressEvent("scan_jp")
        if "Loading bundled resource cache" in line or "Using cached JP scan" in line:
            return ProgressEvent("scan_jp", operation="cache")
        if "Selected " in line and "compatible clips" in line:
            return ProgressEvent("copy")
        match = re.search(r"Preparing (\d+) blob batches", line)
        if match:
            self.replace_total = int(match[1])
            self.replace_current = 0
            return ProgressEvent("replace", 0, self.replace_total, "blobs")
        match = re.search(r"Preparing blob (\d+)/(\d+):", line)
        if match:
            self.replace_current, self.replace_total = int(match[1]) - 1, int(match[2])
            return ProgressEvent("replace", self.replace_current, self.replace_total, "blobs", operation="rebuild")
        if "Patched and verified:" in line or "Prepared structure-preserving blob:" in line:
            self.replace_current += 1
            return ProgressEvent("replace", self.replace_current, self.replace_total, "blobs", operation="verified")
        if "Created patched copy" in line:
            return ProgressEvent("finalize")
        return None


class ProgressState:
    def __init__(self) -> None:
        self.token = 0
        self.active = False
        self.overall = 0.0
        self.event: ProgressEvent | None = None

    def begin(self) -> int:
        self.token += 1
        self.active = True
        self.overall = 0.0
        self.event = None
        return self.token

    def accepts(self, token: int) -> bool:
        return self.active and token == self.token

    def apply(self, event: ProgressEvent, token: int) -> bool:
        if not self.accepts(token) or event.stage not in STAGE_BUDGETS:
            return False
        if self.event is not None:
            order = list(STAGE_BUDGETS)
            if order.index(event.stage) < order.index(self.event.stage):
                return False
            if event.stage == self.event.stage and event.current < self.event.current:
                return False
        start, end = STAGE_BUDGETS[event.stage]
        fraction = 1.0 if event.complete else min(1.0, event.current / event.total) if event.total else 0.0
        self.overall = min(99.0, max(self.overall, start + (end - start) * fraction))
        self.event = event
        return True


@dataclass(frozen=True)
class CompletionResult:
    success: bool
    cancelled: bool = False
    reason: str = ""
    unchanged_source_count: int = 0
    skipped_count: int = 0


def load_completion_report(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def completion_result(code: int, cancelled: bool, report: dict[str, Any] | None) -> CompletionResult:
    if cancelled:
        return CompletionResult(False, True, "cancelled")
    if code != 0:
        return CompletionResult(False, reason="worker_failed")
    if not isinstance(report, dict):
        return CompletionResult(False, reason="missing_report")
    replacements = report.get("replacements")
    allowed = {"replaced", "already_identical", "metadata_adjusted", "skipped_quality"}
    if (report.get("transaction_status") not in {"committed", "committed_with_skips"}
            or report.get("failed") or not isinstance(replacements, list) or not replacements
            or any(not isinstance(item, dict) or item.get("status") not in allowed for item in replacements)):
        return CompletionResult(False, reason="report_failed")
    def count(key: str) -> int:
        value = report.get(key, 0)
        return value if type(value) is int and value >= 0 else 0
    return CompletionResult(True, unchanged_source_count=count("unchanged_source_count"),
                            skipped_count=count("skipped_quality_count"))


class GuiProgressController:
    """Keep all progress mutations on one run, and freeze every terminal state."""

    def __init__(self, bar: Any, progress_text: Any, status_text: Any,
                 labels: dict[str, str], messages: dict[str, str],
                 show_completion: Callable[[str, bool], None]) -> None:
        self.bar, self.progress_text, self.status_text = bar, progress_text, status_text
        self.labels, self.messages, self.show_completion = labels, messages, show_completion
        self.state = ProgressState()
        self.result: CompletionResult | None = None

    def begin(self) -> int:
        token = self.state.begin()
        self.result = None
        self.bar.stop()
        self.bar.configure(mode="determinate", maximum=100, value=0)
        self.progress_text.set(self.messages["progress_start"])
        return token

    def update(self, event: ProgressEvent, token: int) -> None:
        if not self.state.apply(event, token):
            return
        self.bar.stop()
        self.bar.configure(mode="determinate", maximum=100, value=self.state.overall)
        counter = f"{event.current}/{event.total if event.total is not None else '?'}" if event.unit or event.total is not None else ""
        if event.unit and self.messages.get("unit_" + event.unit):
            counter += " " + self.messages["unit_" + event.unit]
        phase = self.labels.get(event.stage + ":" + event.operation, self.labels.get(event.stage, event.stage))
        self.progress_text.set(self.messages["progress"].format(overall=int(self.state.overall), phase=phase, counter=counter))

    def finish(self, code: int, cancelled: bool, report: dict[str, Any] | None, token: int) -> CompletionResult | None:
        if not self.state.accepts(token):
            return self.result if token == self.state.token else None
        self.result = completion_result(code, cancelled, report)
        self.state.active = False
        self.bar.stop()
        self.bar.configure(mode="determinate", maximum=100, value=100 if self.result.success else self.state.overall)
        if self.result.success:
            self.state.overall = 100
            text = self.messages["done"]
            warning = False
            if self.result.unchanged_source_count:
                warning = True
                text += "\n" + self.messages["warning"].format(count=self.result.unchanged_source_count)
            if self.result.skipped_count:
                warning = True
                text += "\n" + self.messages.get("skipped_warning", "Kept original resources: {count}").format(count=self.result.skipped_count)
            self.status_text.set(text)
            self.progress_text.set(self.messages["progress_done"])
            self.show_completion(text, warning)
        elif self.result.cancelled:
            self.status_text.set(self.messages["stopped"])
            self.progress_text.set(self.messages.get("progress_stopped", self.messages["stopped"]))
        else:
            reason = self.messages.get("reason_" + self.result.reason, self.result.reason)
            self.status_text.set(self.messages.get("failure_detail", self.messages["failed"]).format(reason=reason))
            self.progress_text.set(self.messages["failed"])
        return self.result
