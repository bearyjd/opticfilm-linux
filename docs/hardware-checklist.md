# Hardware checklist (before any real scan)

Run through this before the first scanner command of a session, and again after any failed or
interrupted scan. Evidence behind it comes from tests on a single unit; treat it as cautious
practice, not a guarantee.

1. **Confirm the physical state.** Look at the scanner: is a holder inside, partly inside, or
   out? Do not assume from the last command's output; the word "ejected" is not proof of release.
2. **Power-cycle.** Switch the scanner off and leave it off until its lights go out, then switch
   it on and wait for the steady light.
3. **Clear the tray.** Only if the holder is confirmed released, remove it and check nothing is
   left in the slot. If it seems retained, do not force it.
4. **Front-load a fresh holder.** Insert the holder from the front without force, and confirm by
   hand that it is seated and latched.
5. **No commands until the holder is seated.** Send nothing to the scanner (no load, scan, eject,
   or status command) until step 4 is done.

If anything looks wrong while the motor runs (grinding, holder not moving, unexpected noise),
send nothing further and switch the scanner off. Do not force the holder; if it will not come out
by hand, stop and get advice. Then start again from step 1.

This sequence is not a validated recovery procedure. Earlier successful runs also used the
driver's full `load` step (jog, reinsert, hand-confirm the latch) after seating the holder; follow
the driver's own prompts for that step.
