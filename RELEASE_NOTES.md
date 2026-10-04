# MHO984 Compatibility Toolkit v0.1.0-beta.9

This release improves compatibility with newer MHO984 firmware behavior and large/deep-memory waveform captures.

> This project is independent and unofficial. It is not affiliated with, endorsed by, or sponsored by RIGOL Technologies.

## Highlights

### Capacity-aware dynamic FTP wait

Large Memory BIN files may become visible through FTP while they are still being written by the oscilloscope.

Previous releases used a fixed 60-second stable-size timeout. This could fail on deep-memory captures even though the file was still growing normally.

beta.9 now:

- waits for the expected Memory BIN to appear in the FTP namespace;
- monitors the remote FTP SIZE while the file is growing;
- estimates the required wait budget from the RG03 header size;
- tracks the observed FTP growth rate with an exponential moving average;
- dynamically extends the soft deadline when the file is still growing;
- uses a 600-second hard upper limit to prevent infinite waiting;
- requires six consecutive stable SIZE checks before downloading;
- verifies that the downloaded local file size exactly matches the stable remote FTP size;
- performs RG03 validation before accepting the acquisition.

The RG03 header-declared total size is used only as a timing hint. It is not used as the final completion criterion.

### Firmware save compatibility

Firmware updates can change the timing between:

`SAVE command -> SAVE status -> FTP file appearance -> file write completion`

beta.9 includes the compatibility work introduced during beta.8 development:

- scope-side Memory BIN names are now limited to 26 characters;
- generated names use the form:

  `mho984_YYYYMMDD_HHMMSS.bin`

- numeric filename variants appended by the oscilloscope are accepted automatically;
- `:SAVE:STATus?` polling has been extended;
- an inconclusive SAVE status with no SCPI error can be deferred to downstream FTP/RG03 verification in the integrated GUI workflow;
- FTP file appearance is polled instead of failing after a single directory lookup;
- uncertain or incomplete files are never accepted as successful acquisitions.

## Hardware validation

Successful beta.9 acquisition was reported on 2026-10-04 using a RIGOL loaner/demo MHO984 equipped with the:

**500 Mpts storage depth option**

Testing included the firmware-update behavior that originally caused the Toolkit to fail during large Memory BIN retrieval.

During beta.8 testing, a Memory BIN with an RG03 declared size of approximately 1.5 GB was still only about 980 MB after roughly 60 seconds, confirming that the previous fixed timeout was insufficient.

beta.9's dynamic waiting mechanism successfully handled this condition.

## Viewer and protocol analysis

Existing Viewer and analysis functionality from beta.7 is preserved, including:

- Analog CH1-CH4 display
- Digital D0-D15 display when LA data are present
- offline Memory BIN decoding
- mouse-wheel X-axis zoom
- right-drag time-axis panning
- direct mouse dragging of A-Z measurement cursors
- automatic measurements
- Raw / relative-aligned digital timebase display
- protocol overlays on the original Viewer waveform

Supported protocol decoding includes:

- UART
- RS-232
- RS-485
- I2C
- SPI
- LIN
- Classic CAN
- GPS NMEA
- GPS UBX
- GPS PPS timing analysis

CAN FD is not currently implemented.

## Documentation and GitHub improvements

Since beta.7, the repository also gained:

- Viewer screenshot in the Japanese and English README
- installation guide
- troubleshooting guide
- hardware-validation guide
- known-limitations documentation
- updated protocol-decoding documentation
- public release audit
- expanded public test report
- GitHub bug-report template
- GitHub feature-request template
- privacy reminder for diagnostic logs and datasets

## Safety behavior retained

The public-release safety policy remains unchanged:

- only MHO984 is accepted by the online acquisition model guard;
- Force Trigger is not automatic;
- CLI/headless mode remains fail-safe;
- broad LAN fallback discovery remains disabled by default;
- anonymous FTP is used only on the configured instrument;
- the Toolkit does not automatically delete Memory BIN files from the oscilloscope;
- Viewer digital timebase correction remains optional and Raw is the default.

## Validation

The beta.9 public test report records PASS results for:

- Python syntax compilation
- protocol decoder self-tests
- protocol overlay self-tests
- firmware-save compatibility self-test
- 26-character scope filename handling
- delayed FTP file appearance
- synthetic 1.5 GB-class dynamic growth beyond 60 seconds
- header-size / final-size mismatch handling
- dynamic soft-deadline extension
- 600-second absolute wait limit
- deferred SAVE-status handling

In addition, beta.9 was successfully exercised on the RIGOL loaner/demo MHO984 described above.

## Important notes

- Primarily validated with the RIGOL MHO984.
- MHO934 and MHO954 remain unvalidated.
- RG03 internal format handling and anonymous FTP behavior are interoperability mechanisms derived from observed instrument behavior and may change with future firmware.
- Logic Analyzer / POD must be enabled on the oscilloscope if D0-D15 data are required.
- Digital timebase correction is experimental and instrument-dependent.
- This software is not intended for safety-critical or traceable calibration use.

## Upgrade from beta.7

A fresh extraction of the beta.9 ZIP is recommended rather than overwriting an existing beta.7 folder.

Run:

`setup_venv.bat`

if the local virtual environment has not already been prepared, then start the Toolkit with:

`START_MHO984_Toolkit.bat`

## Integrity

Release package:

`Unofficial_MHO984_Compatibility_Toolkit_v0.1.0-beta.9.zip`

SHA-256:

`9e14e2e38b6d9bf288f1a6531b7c6eab5d026c24a9bbfd4202246200e5ba0f34`

## Full Changelog

https://github.com/ojimay0123-netizen/Unofficial-MHO984-Compatibility-Toolkit/compare/v0.1.0-beta.7...v0.1.0-beta.9
