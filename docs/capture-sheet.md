# Recording the vendor app's USB traffic

Roadmap step 3. The goal is one short, labelled recording per user action, so each can later be compared with what the native driver sends for the same action.

This is a read-only recording of USB traffic made on the Linux host. The vendor app still moves the scanner, though, so every capture needs you at the scanner with the physical state confirmed (see `hardware-checklist.md`). Stop at the first anomaly and never retry automatically.

## Before you start

- The scanner is claimed by the Windows VM, and the native driver is not running. Do not run any `ofscan` command that talks to the scanner during a capture.
- Find the USB bus the scanner is on: `grep -l 1436 /sys/bus/usb/devices/*/idProduct`, then read the bus number from the folder name (for example `3-2` is bus 3). Note it down. The bus number can change if the VM re-attaches the device, so check it again before each capture.
- Free disk: scans are large. A 3600 dpi scan capture can be hundreds of MB. Use a folder on a big disk.
- Captures contain image data and traffic from other devices on the same bus. They stay on your machine and are never published.

## One capture

1. Open a terminal and start recording, replacing `3` with the bus number:
   `sudo tcpdump -i usbmon3 -s 0 -w capture-NN-name.pcap`
2. Do exactly one action from the list below in the Windows app.
3. Wait until the app is idle and the scanner has stopped moving, then stop the recording with Ctrl-C.
4. Add a line to `actions.log` in the same folder: capture file name, local time you started, what you did, anything unusual (a message from the app, a noise, the holder not moving).

Keep the action log honest: write what happened, not what was planned.

## Actions, in this order

Do the safe, short ones first. Skip any step if the state is not what the step says.

| # | Name | Start state | Action |
|---|------|-------------|--------|
| 01 | cold-retained | scanner off, holder left in | power on, wait for the lights to settle, open the app |
| 02 | cold-empty | scanner off, no holder | power on, open the app, do nothing else |
| 03 | load | app open, holder out | load the holder from the app |
| 04 | eject | holder loaded | eject from the app |
| 05 | preview | holder loaded, four slides | preview the tray |
| 06 | scan-600 | holder loaded | scan slides 1 to 4 at 600 dpi, IR on |
| 07 | scan-3600 | holder loaded | scan slides 1 to 4 at 3600 dpi, IR on |
| 08 | cancel | holder loaded | start a 600 dpi scan, press cancel about halfway |
| 09 | settings | holder loaded | scan slide 1 at 7200 dpi, then once with IR off, then once as negative |
| 10 | idle | app open | leave it alone for 6 minutes |

Notes:

- 01 and 02 are the most valuable. Do them first, one capture each, from a real power-on.
- 07 is long. Only do it if 06 went cleanly.
- 08 shows what the vendor does after a cut-off scan. Leave the scanner alone afterwards until the capture is stopped.
- Do not unplug the scanner or force the holder during any capture.

## After recording

- Keep the `.pcap` files and `actions.log` together in one folder per session, for example `captures/2026-10-28/`.
- Tell me the folder. I will decode each capture into a trace with named register fields and compare it with the native driver's traffic for the same action.
