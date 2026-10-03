# Security / ネットワーク運用上の注意

## 推奨環境

本ツールは、PCとオシロが接続された**信頼できるローカル実験室LAN**での利用を想定しています。

- オシロをインターネットへ直接公開しないでください。
- 公衆Wi-Fi、共有ネットワーク、不特定多数がアクセスできるセグメントでの使用を避けてください。
- 組織ネットワークでは、ネットワーク管理者のポリシーに従ってください。

## 通信

通常動作では主に次を使用します。

- TCP/5555: SCPI
- TCP/21: anonymous FTPによるMemory BIN取得

FTPは平文であり、暗号化・サーバ認証を提供しません。機密ネットワークを越えて使用しないでください。

## 公開版の安全側初期値

FTP取得が失敗しても、公開版は既定でHTTP/HTTPS/SMB等の互換探索を行いません。

広範囲のフォールバック探索は、所有・管理権限のある信頼済み実験室LANでのみ、明示的に:

```bat
run_acquisition.bat --allow-fallback-discovery
```

として有効にしてください。このオプションを有効にすると、互換性調査のため複数ポート/サービスへ接続を試みることがあります。

## 強制トリガ

公開版ではSINGLEのタイムアウト時に自動 `:TFORce` を実行しません。必要な場合のみ:

```bat
run_acquisition.bat --force-trigger-on-timeout
```

を明示してください。

## オシロ側ファイル

Memory BINはオシロの `C:/` に作成されます。誤削除防止のため本公開版では自動削除しません。空き容量を定期的に確認してください。

## 脆弱性報告

公開リポジトリで運用する場合は、認証情報、シリアル番号、IPアドレス、未加工データセットを公開Issueへ貼らず、まず再現手順と匿名化済み診断情報を共有してください。


## Offline BIN mode

`Open saved BIN` operates only on a user-selected local/network file and does not initiate an oscilloscope connection, SCPI traffic, FTP, or network discovery. This is the preferred workflow in environments where instrument-network access is restricted.
