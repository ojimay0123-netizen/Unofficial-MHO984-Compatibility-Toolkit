# Offline Memory BIN workflow

The main window command **保存済みBINを開く / Open saved BIN** decodes an MHO984 Memory waveform BIN entirely on the PC. It does not connect to the oscilloscope and does not use FTP.

## Flow

1. Save a Memory waveform BIN on the MHO984.
2. Copy the BIN to the Windows PC using a method you trust (USB storage, file share, etc.).
3. Launch `START_MHO984_Toolkit.bat`.
4. Choose `保存済みBINを開く`.
5. Select the BIN.
6. The toolkit creates a new `mho984_offline_YYYYMMDD_HHMMSS` dataset under the configured PC storage folder.
7. The RG03 decoder reconstructs Analog channels and, if an LA record is present, D0-D15.
8. Viewer opens automatically.

## Limits

- Only data physically present in the BIN can be reconstructed.
- If the logic analyzer was disabled/not captured when the BIN was saved, D0-D15 will not be available.
- The decoder is validated against MHO984 RG03 files observed during development. Firmware changes may alter the format.
- Viewer starts in Raw digital timebase mode. Any relative timebase alignment remains optional and non-traceable.
