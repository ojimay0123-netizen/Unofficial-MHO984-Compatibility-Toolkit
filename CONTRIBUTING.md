# Contributing

Contributions are welcome, especially validation data from additional MHO984 units and other MHO900-series models.

## Rights and provenance

By submitting a contribution, you represent that you have the right to submit it and that it may be distributed under the project's MIT License unless clearly stated otherwise and accepted by the maintainers.

Do **not** submit or attach:

- RIGOL firmware images
- RIGOL proprietary software binaries
- copied RIGOL manuals/PDFs, logos, website images, fonts, or other vendor Materials
- third-party code that you do not have the right to redistribute
- data from instruments or networks you are not authorized to use

Small quotations or factual references to public documentation should be limited to what is needed for interoperability discussion and should identify the source.

## Test captures

If you share an oscilloscope dataset for interoperability testing:

- confirm you own the data or have permission to share it;
- remove confidential signal content;
- review IP addresses, serial numbers, timestamps, usernames and filesystem paths;
- prefer `export_diagnostics_for_issue.bat` for ordinary bug reports;
- only share raw RG03/BIN data when it is genuinely needed and you are comfortable making the waveform public to the intended recipients.

## Compatibility claims

Do not mark a model or firmware as supported based on a single successful launch. At minimum, validate:

- model identity (`*IDN?`)
- Analog record parsing
- D0-D15 bit mapping
- time-axis coverage
- multiple known frequencies
- absence of false transitions on static digital channels
- Analog/Digital timing behavior

Experimental results should be labeled as such.
