# Protocol decoding (beta)

The protocol analyzer works only on already captured waveforms. It does not send any protocol traffic and does not change oscilloscope configuration.

## Supported in v0.1.0-beta.9

- UART: 5-9 data bits, None/Even/Odd parity, 1/1.5/2 stop bits
- RS-232: UART decoder with polarity inversion support
- RS-485: UART payload decoding; when two Analog channels are selected, the analyzer forms a differential signal `A-B`
- I2C: START/STOP, 7-bit address + R/W, ACK/NACK, data bytes
- SPI: Mode 0-3, 1-32 bits/word, MSB/LSB first, optional CS, MOSI/MISO
- LIN: BREAK, SYNC, protected ID check, data, classic/enhanced checksum check
- CAN Classic: standard 11-bit and extended 29-bit identifiers, RTR, DLC/data, bit stuffing, CRC-15, ACK and EOF checks
- GPS NMEA: UART extraction, checksum validation, basic RMC/GGA field parsing
- GPS UBX: sync/class/id/length/payload/checksum extraction
- GPS PPS: rising-edge list, period/error, and nearest NMEA sentence timing observation

CAN FD is not implemented in this beta.

## Analog input

CHAN1-CHAN4 can be used as decoder sources. Analog signals are converted to logic using a threshold and optional hysteresis. `Auto` threshold uses the midpoint of robust low/high estimates in the selected analysis range.

For RS-485, selecting two Analog channels uses their difference (`Source1 - Source2`) before thresholding. The user is responsible for selecting the correct A/B order and electrical probing method.

## Digital input

D0-D15 are decoded directly from the dataset's Digital event bus. The analyzer can use either Raw Digital time or the optional relative timebase-aligned view. Raw remains the default.

## Accuracy and limitations

Protocol decoding is a convenience analysis feature, not protocol conformance testing. Results depend on sample rate, threshold, noise, baud/bitrate setting, probe setup, and the correctness of the captured waveform. Frames marked `OK` only mean that the checks implemented by this decoder passed; they do not certify compliance with a protocol standard.

No protocol specification text, certification mark, logo, or third-party software is bundled. Protocol names are used descriptively for interoperability.


## Viewer overlay (beta.5)

After a successful decode, Protocol Analyzer can publish the decoded event list into the current dataset as `protocol_overlays/active_protocol_overlay.json`. With auto-reflect enabled (default), this happens automatically. The Viewer polls this annotation file and draws the visible events as translucent colored bands on the original waveform axes.

The overlay file contains decoded annotations and decoder settings only; it does not rewrite waveform BIN files. Dense event sets are display-limited for responsiveness, while the stored event list remains intact.
