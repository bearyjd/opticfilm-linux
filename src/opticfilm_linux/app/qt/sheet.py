"""Plain contact-sheet rows built exclusively from immutable app snapshots."""

from typing import Any

from opticfilm_linux.app.messages import slide_status_text
from opticfilm_linux.app.state import AppState, SlideCard, TrayRow
from opticfilm_linux.batch.model import BatchDefaults, SlideStatus, TrayStage

DEFAULTS = BatchDefaults()


def _tray_dict(row: TrayRow) -> dict[str, Any]:
    elapsed = "" if row.elapsed_s is None else f"{int(row.elapsed_s)} s"
    return {
        "number": row.number,
        "stage": row.stage.value,
        "stageText": row.stage_text,
        "progress": -1.0 if row.progress is None else row.progress,
        "elapsed": elapsed,
        "error": row.error or "",
        "needsLook": row.needs_look,
        "rawRemoved": row.raw_removed,
        "canRecoverRaw": row.can_recover_raw,
        "auto": row.auto,
        "autoFailed": ", ".join(map(str, row.auto_failed)),
        "autoError": row.auto_error,
        "autoFlagged": ", ".join(map(str, row.auto_flagged)),
    }


def _slide_dict(tray: int, card: SlideCard) -> dict[str, Any]:
    s = card.settings
    base = f"image://preview/{tray}/{card.position}"
    return {
        "tray": tray,
        "position": card.position,
        "status": card.status.value,
        "statusText": slide_status_text(card.status),
        "flags": list(card.flags),
        "rotation": s.rotation,
        "mirror": s.mirror,
        "brightness": s.brightness_ev,
        "color": DEFAULTS.color_strength if s.color_strength is None else s.color_strength,
        "grain": DEFAULTS.grain_strength if s.grain_strength is None else s.grain_strength,
        "sharpen": DEFAULTS.sharpen_amount if s.sharpen_amount is None else s.sharpen_amount,
        "ownerDecided": card.owner_decided,
        "exported": card.exported,
        "updating": card.updating,
        "previewToken": card.preview_token,
        "beforePreviewToken": card.before_preview_token,
        "afterUrl": f"{base}/after/{card.preview_token}" if card.preview_token else "",
        "beforeUrl": f"{base}/before/{card.before_preview_token}"
        if card.before_preview_token
        else "",
    }


def sheet_rows(state: AppState) -> list[dict]:
    busy = (TrayStage.SCANNING, TrayStage.SCANNED, TrayStage.ENHANCING)
    return [
        {
            **_tray_dict(tray),
            "slides": [
                {
                    **_slide_dict(tray.number, card),
                    "selected": (tray.number, card.position) in state.selection,
                    "focused": (tray.number, card.position) == state.focus,
                    "selectable": tray.stage not in busy,
                }
                for card in tray.slides
            ],
        }
        for tray in sorted(state.trays, key=lambda row: row.number, reverse=True)
    ]


def focused_slide(state: AppState) -> dict:
    if state.focus is None:
        return {}
    number, position = state.focus
    for tray in state.trays:
        if tray.number == number:
            for card in tray.slides:
                if card.position == position:
                    return {**_slide_dict(number, card), "rawRemoved": tray.raw_removed}
    return {}


def selection_summary(state: AppState) -> dict:
    summary = state.selection_summary
    return {
        "count": summary.count,
        "accepted": summary.accepted,
        "settings": dict(summary.settings),
        "mixed": list(summary.mixed),
        "trays": list(summary.trays),
    }


def filter_count(state: AppState, name: str) -> int:
    return sum(
        bool(card.flags) or card.status is SlideStatus.BLANK or card.position in tray.auto_flagged
        if name == "flagged"
        else card.position in tray.auto_failed
        or tray.stage is TrayStage.FAILED
        or (tray.auto == "failed" and not tray.auto_failed)
        for tray in state.trays
        for card in tray.slides
    )
