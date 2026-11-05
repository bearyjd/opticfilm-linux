"""Empty holder openings: detected clear windows bound to the owner's inspection of one raw
region, and openings skipped on purpose, which have no raw data at all."""

from collections.abc import Mapping

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


def is_known_empty(tray: TrayRecord, slide: SlideRecord) -> bool:
    """Skipped on purpose, or a clear window the owner confirmed for the current scan."""
    return slide.status is SlideStatus.SKIPPED or is_empty_confirmed(tray, slide)


def scanned_slides(tray: TrayRecord) -> tuple[SlideRecord, ...]:
    """The slide records whose holder opening is in the raw sweep (all but skipped ones)."""
    return tuple(s for s in tray.slides if s.status is not SlideStatus.SKIPPED)


def check_raw_coverage(tray: TrayRecord, regions: Mapping[int, object]) -> None:
    """Every raw region has exactly one scanned record, and no skipped one hides in the raw."""
    positions = [slide.position for slide in scanned_slides(tray)]
    if (
        any(type(position) is not int for position in positions)
        or len(positions) != len(regions)
        or set(positions) != set(regions)
        # not implied by the above: a hand-edited manifest could repeat a scanned position
        or any(s.status is SlideStatus.SKIPPED and s.position in regions for s in tray.slides)
    ):
        raise ValueError("manifest positions must cover every raw position exactly once")
