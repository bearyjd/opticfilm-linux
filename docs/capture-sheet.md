# Recording the vendor app's USB traffic

Roadmap step 3. The goal is one short, labelled recording per user action, so each can later be compared with what the native driver sends for the same action.

The recording itself is read-only and happens on the Linux host. The vendor app still moves the scanner, so every capture needs you at the scanner. Follow `hardware-checklist.md`, with the exceptions below for the two captures that start without a seated holder.

## Safety rules for every capture

- **Anomaly rule.** If you hear or see grinding, stalled movement, a holder that is not moving while the motor runs, or a cancel that does not stop the carriage: stop waiting for the app. Power the scanner off at once (off until the lights go out), stop the recording, and note it in the action log. Do not retry.
- **Start state.** Each action below names its start state. Confirm it physically first. If the state is not what the step says, skip the step.
- **Checklist exceptions.** Captures 01 (retained holder), 02 (no holder) and 03 (load) deliberately start without a freshly front-loaded holder, so the checklist rule "no commands until the holder is seated" does not apply to them. For 03, put the holder in by hand the way the app's load prompt tells you, then press Load. All other checklist rules still apply to every capture.
- Do not unplug the scanner or force the holder during a capture.
- Nothing from `ofscan` may talk to the scanner during a capture. The Windows VM owns it.

## Before you start

1. **Prepare while the scanner is powered on and attached to the VM.** Find its USB bus: `grep -l 1436 /sys/bus/usb/devices/*/idProduct`; the folder name starts with the bus number (for example `3-2` is bus 3). Note the bus. Do this before the cold-start captures, because a powered-off scanner has no entry. The bus number stays the same across power cycles unless the VM changes the port; check again before each capture that you started from a power-off.
2. **Make sure usbmon works.** If `/dev/usbmon<bus>` does not exist, run `sudo modprobe usbmon`.
3. **Storage.** Use a folder outside the repository so nothing is committed or published by accident. Create it first: `mkdir -p ~/captures/2026-10-28/`. Check free space: `df -h ~/captures/2026-10-28/`. Keep at least 20 GB free. Captures are truncated per packet (below), so they are much smaller than the scans, but a long session still adds up.
4. Captures contain traffic from every device on the same bus. They stay on your machine.

## One capture

1. Start recording, replacing `3` with the bus number. The truncation keeps control and table writes and shortens image data:
   `sudo timeout 3600 tcpdump -i usbmon3 -s 1024 -w ~/captures/2026-10-28/capture-NN-name.pcap`
2. Wait for tcpdump to print `listening on usbmon3`. If it prints an error instead, fix that before touching the scanner.
3. Do exactly one action from the list below in the Windows app.
4. Wait until the app is idle and the scanner has stopped moving, then stop the recording with Ctrl-C. (If the anomaly rule applies, power off first.)
5. Add a line to `actions.log` in the same folder: capture file name, local time started, what you did, anything unusual (an app message, a noise, a holder that did not move).

Keep the action log honest: write what happened, not what was planned.

For the cold-start captures (01 and 02), start tcpdump before powering the scanner on. The scanner appears on the bus after power-on and the VM re-attaches it; leave the recording running through that.

## Actions, in this order

Do the short ones first. Skip any step if the start state is not what it says.

| # | Name | Start state | Action |
|---|------|-------------|--------|
| 01 | cold-retained | scanner off, holder left in | start recording, power on, wait for the lights to settle, open the app |
| 02 | cold-empty | scanner off, no holder | start recording, power on, open the app, do nothing else |
| 03 | load | app open, holder out | load the holder from the app |
| 04 | eject | holder loaded | eject from the app |
| 05 | preview | holder loaded, four slides | preview the tray |
| 06 | scan-600 | holder loaded | scan slides 1 to 4 at 600 dpi, IR on |
| 07 | scan-3600 | holder loaded | scan slides 1 to 4 at 3600 dpi, IR on |
| 08 | cancel | holder loaded | start a 600 dpi scan, press cancel about halfway |
| 09a | slide1-7200 | holder loaded | scan slide 1 at 7200 dpi, IR on, positive |
| 09b | slide1-noir | holder loaded | scan slide 1 at 600 dpi, IR off, positive |
| 09c | slide1-neg | holder loaded | scan slide 1 at 600 dpi, IR on, negative |
| 10 | idle | app open | leave it alone for 6 minutes, then stop (`timeout 420` instead of 3600) |

## Getting to each start state

The captures are not a continuous run. Between them, prepare the start state without recording, following the checklist (power off until the lights go out; remove a holder only if it is confirmed released, never by force):

- After 01, to reach 02: power off, and take the holder out only if the app showed it released. If it will not come out, stop and get advice.
- After 04 (eject), to reach 05 onward: load the holder again by hand and through the app, without recording, or record it again as 03.
- After 08 (cancel), power-cycle before the next scan capture.
- Before 09a to 09c, confirm the holder is loaded and the same four slides are in it.

Notes:

- 01 and 02 are the most valuable. Do them first, one capture each, from a real power-on.
- 07 is long and heavy. Only do it if 06 went cleanly.
- 08 shows what the vendor does after a cut-off scan. Leave the scanner alone afterwards until the capture is stopped, unless the anomaly rule applies.
- 09a to 09c change exactly one setting each against 06, so the differences are interpretable.

## After recording

- Keep the `.pcap` files and `actions.log` together, one folder per session.
- Tell me the folder. I will decode each capture into a trace with named register fields and compare it with the native driver's traffic for the same action.
