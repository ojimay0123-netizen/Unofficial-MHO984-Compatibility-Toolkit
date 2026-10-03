# インストール・初回実行 / Installation

## 前提条件

Windows向けPythonソース配布です。Python runtimeは同梱しません。Python 3、Tkinter、venv、pipが必要です。依存範囲は `numpy>=2.0,<3`、`matplotlib>=3.8,<4` です。大容量データには64bit Pythonを推奨しますが、これはWindows/Pythonの特定構成の検証済み宣言ではありません。公開テスト報告には正確なWindows/Python/依存版の組合せが未記載です。必要RAM、最大BINサイズ、処理時間の上限も未評価です。

## 導入

1. [beta.7 Release](https://github.com/ojimay0123-netizen/Unofficial-MHO984-Compatibility-Toolkit/releases/tag/v0.1.0-beta.7)から `Unofficial_MHO984_Compatibility_Toolkit_v0.1.0-beta.7.zip` と対応する `.zip.sha256` を取得します。GitHub自動生成のSource code ZIPと配布ZIPは区別してください。
2. PowerShellで下記を実行し、結果をダウンロードしたsha256ファイルの値と比較します。不一致なら使用せず再取得してください。
3. ZIPを、書き込み可能なローカルフォルダへ完全に展開します。ZIP内から直接BATを起動しないでください。
4. python.orgのPython 3を用意します。Python launcherとTcl/Tkを含む構成を使用し、下記のTk確認で小窓が表示されることを確認します。
5. `setup_venv.bat` を実行し、`Setup complete.` を確認します。初回pip導入にはパッケージ取得のインターネット接続が必要です。
6. `START_MHO984_Toolkit.bat` を起動します。
7. `設定 → 接続・保存先...` で本体IPv4、書き込み可能なPC保存先、SINGLE待機時間を設定します。初期IPは空欄、保存先の既定値は `C:\Temp`、待機は30秒です。
8. オフラインBINを開くか、下記LAN準備を済ませて取得します。初回の合否確認は [HARDWARE_VALIDATION.md](HARDWARE_VALIDATION.md) を使用してください。

```powershell
Get-FileHash .\Unofficial_MHO984_Compatibility_Toolkit_v0.1.0-beta.7.zip -Algorithm SHA256
py -3 --version
py -3 -m tkinter
```

setupは `py -3` を優先し、なければPATH上の `python` を使います。複数版がある場合、導入先を確認してください。特定のインタープリタを選ぶ場合は展開先で次のように明示できます（既存venvの置換は行わず、新しい展開先を使用）。

```powershell
& "C:\path\to\python.exe" -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe --version
.\.venv\Scripts\python.exe -m pip freeze
```

## LAN準備と取得

本体のLAN設定画面でIPv4を確認します。PCから到達できる信頼済みLANを使用し、必要なAnalogチャンネル、LA/POD、しきい値、トリガ条件を本体で準備します。

取得コードのSCPI既定ポートはTCP **5555**、anonymous FTP制御ポートはTCP **21**です。PowerShellで下記の例示IPを自分の本体IPへ変更してください。

```powershell
Test-NetConnection -ComputerName 192.168.1.100 -Port 5555
Test-NetConnection -ComputerName 192.168.1.100 -Port 21
```

成功はTCP接続の確認のみです。機種識別やFTPデータ転送の成功を意味しません。FTPは別のデータ接続も使うため、21番が成功しても転送を遮断するネットワーク設定があり得ます。

メイン画面で「オシロから波形取得」を押し、確認ダイアログを読んで進めます。SINGLE → STOP → 本体C:/保存 → FTP → デコード → Viewerの進捗を確認します。本体は取得後STOP状態に残ります。

## SINGLEタイムアウト

設定可能範囲は1～3600秒、既定30秒です。統合GUIの選択肢は次の意味です。

| 選択 | 動作・確認 |
|---|---|
| 今回だけForce Trigger | 明示操作で1回だけ強制トリガ。その後STOPを確認。元のトリガ条件が成立した証明にはならない |
| さらに待つ | 設定した待機区間を延長。必要なら信号・トリガ条件を確認 |
| 中止 | 未完了を成功扱いにしない。途中フォルダが残る場合がある |

CLI/ヘッドレスの既定はタイムアウト中止です。自動Forceを有効にする設定とGUIでの明示選択は区別してください。

## 配布版との関係

mainの文書更新は公開済みタグやRelease ZIPへ自動反映されません。beta.7配布物の識別にはタグ・ファイル名・チェックサムを使い、この文書はmainで追加された補足手順として参照してください。

## English quick start

Install Python 3 with Tkinter, venv and pip; exact validated Windows/Python/package versions are not yet recorded. Prefer 64-bit Python for large captures. Download the named beta.7 ZIP and matching checksum from Releases, compare SHA-256, fully extract to a writable local folder, run setup_venv.bat until “Setup complete”, then START_MHO984_Toolkit.bat. Setup needs package-download connectivity and prefers py -3. Configure scope IPv4 and PC storage. SCPI defaults to TCP 5555; FTP uses TCP 21 plus a data connection. The scope remains stopped after acquisition. Timeout defaults to 30 seconds (range 1–3600); Force is explicit and does not prove the original trigger condition occurred. Main documentation updates do not replace the published tag or ZIP.
