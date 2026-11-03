"""Evidence binding an owner's empty-frame inspection to one classified raw region."""

from opticfilm_linux.batch.model import SlideRecord, SlideStatus, TrayRecord


def empty_frame_fingerprints(tray: TrayRecord) -> dict[int, str]:
    return {
        slide.position: slide.blank_fingerprint
        for slide in tray.slides
        if slide.status is SlideStatus.BLANK
    }


def is_empty_confirmed(tray: TrayRecord, slide: SlideRecord) -> bool:
    marker = slide.empty_confirmed
    return (
        slide.status is SlideStatus.BLANK
        and marker is not None
        and marker.sweep_version == tray.sweep_version
        and bool(slide.blank_fingerprint)
        and marker.fingerprint == slide.blank_fingerprint
    )
