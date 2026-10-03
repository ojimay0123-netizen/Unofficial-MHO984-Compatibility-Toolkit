# Supported Models / 検証状況

| Model | Status | Notes |
|---|---|---|
| MHO984 | **Validated Beta** | 実機でAnalog + D0-D15、RG03 BIN、FTP取得、Viewer、相対時間軸解析を検証 |
| MHO954 | Not validated | 同シリーズでもRG03配置・LA構造・FTP挙動が同一とは未確認 |
| MHO934 | Not validated | 同上 |

## Model guard

取得プログラムは `*IDN?` を読み、`MHO984` を含まない場合は停止します。これは未検証モデルでの誤操作を避けるための意図的な制限です。

Viewerは既存データセットを読むだけなので、将来別モデルの互換データが提供された場合に解析検証へ利用できる可能性がありますが、現時点で対応を保証しません。

## Firmware

検証データセットの `*IDN?` ソフトウェア欄は `00.01.00` でした。ただしRIGOLの配布ページで表示されるフルFirmwareバージョンと `*IDN?` の粒度が同一とは限りません。このBeta版は特定のフルビルド番号を保証対象としていません。

Firmware更新後は必ず小さな既知信号でAnalog/Digitalの一致を再確認してください。
