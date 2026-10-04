# MHO984 Toolkit v0.1.0-beta.9 (Unofficial)

Windows向けの、RIGOL MHO984との互換性を目的とした**独立・非公式**の波形取得／解析ツールです。
取得エンジン r12.11e と Viewer r13.5 を、通常利用では1つのメイン画面から操作できるよう整理したBeta版です。

> **重要:** 本プロジェクトは RIGOL Technologies Co., Ltd. またはその関連会社による公式製品ではなく、提携・承認・保証を受けたものでもありません。RIGOLおよび製品名は各権利者に帰属します。名称は互換性の対象を識別するためにのみ使用しています。

## スクリーンショット

[![MHO984 Toolkit Viewer](docs/images/viewer-beta7.webp)](docs/images/viewer-beta7.webp)

*Viewer表示例。Analog波形、チャンネル選択、プロトコル帯、ズーム／パン／カーソル操作を1つの画面で扱えます。*

**最新版のダウンロード:** [GitHub Releases](https://github.com/ojimay0123-netizen/Unofficial-MHO984-Compatibility-Toolkit/releases)


## 対応状況

- **MHO984: 実機検証済み**
- MHO934 / MHO954: **未検証**。オンライン取得は安全のため `*IDN?` で `MHO984` を確認し、それ以外を拒否します。
- RG03内部構造の解析、LA lower16の復元、anonymous FTP取得は、公開SCPI仕様そのものではなく実機検証に基づく互換実装です。

詳細は [docs/SUPPORTED_MODELS.md](docs/SUPPORTED_MODELS.md) を参照してください。

## 初回導入と確認

1. [インストール・初回実行](docs/INSTALLATION.md)に従い、配布ZIPのチェックサム確認、Python準備、setup_venv.bat、起動、IP設定を行います。
2. [実機確認手順](docs/HARDWARE_VALIDATION.md)でAnalog・D0–D15・同期・SINGLEタイムアウト・beta.9大容量Memory BIN取得を確認します。
3. 問題があれば[障害切り分け](docs/TROUBLESHOOTING.md)を参照してください。

[既知の制限](docs/KNOWN_LIMITATIONS.md)には検証範囲と未評価項目を集約しています。SCPI既定ポートはTCP 5555、FTP制御はTCP 21です。Python/Tkinterと依存導入が必要で、正確な検証済みWindows/Python構成は追加確認中です。

mainの文書更新は公開済みタグ・Release ZIPに自動反映されません。

## 起動方法

通常は **`START_MHO984_Toolkit.bat` だけを使用してください。**

初回にNumPy / Matplotlibが未導入の場合は、メイン画面の `ツール → Python環境セットアップ` または `setup_venv.bat` を実行します。グローバルPythonを更新せず、このフォルダ内の `.venv` を使用します。

従来の直接起動BATは `advanced_launchers/` に移動しました。通常利用では不要です。

## メイン画面からできること

### 1. オシロから波形取得

`オシロから波形取得` を押すと、設定済みIPアドレスを使って次を実行します。

`SINGLE → STOP → scope C:/へMemory BIN保存 → anonymous FTP/21でPCへ1回転送 → Analog/Digitalデコード → Viewer`

取得前に安全確認ダイアログが表示されます。公開版の初期値は、強制トリガOFF、広範囲LAN探索OFF、ViewerのDigital時間軸Rawです。

### 2. 保存済みBINを開く（オフライン）

`保存済みBINを開く` から、MHO984本体で保存したMemory waveform BINを選択できます。

- オシロへのLAN接続は行いません。
- 選択したBINから新しい `mho984_offline_YYYYMMDD_HHMMSS` データセットを作成します。
- BINに含まれるAnalog CH1-CH4を復元します。
- LAレコードが含まれていればD0-D15も復元します。
- デコード完了後、Viewerを自動起動します。
- 保存時にBINへ含まれていなかったチャンネルを後から復元することはできません。

詳細は [docs/OFFLINE_BIN.md](docs/OFFLINE_BIN.md) を参照してください。

### 3. 既存データセットを開く

既にデコード済みの `mho984_*` または `mho984_offline_*` フォルダを選択し、そのままViewerで表示します。

### 4. プロトコル解析

メイン画面の `プロトコル解析` から、デコード済みデータセットに対して以下を解析できます。

- UART / RS-232 / RS-485
- I2C
- SPI
- LIN
- CAN Classic（11-bit / 29-bit ID）
- GPS NMEA / UBX / PPS

D0-D15を直接使用するほか、CHAN1-4をThreshold + Hysteresisで論理化して解析できます。RS-485では2本のAnalogを選択すると差動 `A-B` としてUART復号できます。解析結果一覧、論理波形表示、CSV/JSON出力に対応します。

CAN FDは本版では未対応です。プロトコル解析は適合性認証・安全評価・規格準拠試験を目的とした機能ではありません。詳細は [docs/PROTOCOL_DECODING.md](docs/PROTOCOL_DECODING.md) を参照してください。

### 5. IPアドレス・保存先設定

メニューの `設定 → 接続・保存先...` から変更します。

- オシロスコープIPv4アドレス
- PCデータ保存先（ローカルフォルダ / UNC共有フォルダ）

設定はユーザーのホームフォルダ内 `~/.mho984_toolkit_public_settings.json` に保存されます。Fresh installでは誤接続防止のためIPは空欄です。

## 主な機能

- 同一RG03 BINからCH1-CH4とD0-D15を復元
- Analog/Digitalを同一時間軸で表示
- 動的間引き、ズーム、パン、A-Zカーソル
- Digitalの周波数、周期、High/Low幅、Duty測定
- CH1↔D8 / CH2↔D0のエッジ差、時間ドリフト(ppm)
- 全範囲 / 表示範囲 / A-B間の自動測定
- Digital時間軸のRaw / 相対時間軸補正表示
- UART / RS-232 / RS-485 / I2C / SPI / LIN / CAN Classic / GPS NMEA・UBX・PPS解析

## Firmware更新・大容量メモリ対応（beta.9）

Firmware更新後、一部実機では `:SAVE:STATus?` の完了通知とFTP上のMemory BIN生成タイミングが一致せず、大容量RG03 BINがFTP上で長時間成長し続けることがあります。beta.9では固定60秒待機を廃止し、RG03ヘッダの規模と実測FTP SIZE成長速度から待機予算を動的に調整します。完了判定自体はヘッダ宣言サイズではなく、FTP SIZEの連続安定、ローカル/リモートサイズ一致、RG03デコーダ検証で行います。

2026-10-04に、**500 Mpts storage depth optionを搭載したRIGOL貸出デモMHO984** でbeta.9の実機取得成功が報告されました。オプション有無やFirmware差による挙動差は今後もIssueで収集します。

## SINGLE直後のSweep readback

一部の実機では `:SINGle` 直後に `:TRIGger:SWEep?` を問い合わせると `AUTO` が返ることがあります。本版では、この即時readbackを診断情報として保存しますが、それだけを理由に取得失敗とは判定しません。SINGLEコマンドのSCPI受理、トリガ状態監視、最終STOP確認で取得完了を判定します。

## Digital時間軸補正

Viewerの初期値は **Raw** です。

同梱 `digital_timebase_reference_profile_mho984_0034.json` の `+2.091228967 ppm` は、**1台のMHO984実機で、1台のファンクションジェネレータの10 MHz / 12 MHz信号をAnalogとDigitalへ分岐して求めた相対時間軸アライメントの参考値**です。

これはMHO984全個体に共通すると証明された値ではなく、国家標準等へトレーサブルな校正でもありません。安全・規制・認証用途の測定保証を与えません。詳しくは [docs/CALIBRATION.md](docs/CALIBRATION.md) を参照してください。

## オシロ側ストレージ

オンライン取得時、Memory BINをオシロの `C:/` に作成します。誤削除を避けるため本ツールは自動削除しません。長期間使用する場合はオシロ側の空き容量を確認し、不要なファイルをオシロ標準UI等から整理してください。

## セキュリティとデータ共有

- anonymous FTPは暗号化されません。所有・管理権限のある信頼済みLANでのみ使用してください。
- オフラインBIN表示ではLAN接続を使用しません。
- データセットにはIPアドレス、`*IDN?`文字列、PCパス等が含まれる可能性があります。
- Issue等へ共有する場合は `ツール → 診断情報ZIPを作成` または `export_diagnostics_for_issue.bat` を使用してください。

詳細は [SECURITY.md](SECURITY.md) と [PRIVACY.md](PRIVACY.md) を参照してください。

## 権利・免責

利用前に [LEGAL_NOTICE.md](LEGAL_NOTICE.md)、[SOURCE_PROVENANCE.md](SOURCE_PROVENANCE.md)、`LICENSE` を確認してください。

本ツールは無保証で提供され、計測器の校正証明、適合性、安全性評価を置き換えるものではありません。RIGOLのソフトウェア、Firmware、Manual、Logo、Font、Image等は本配布物に含めません。

## Release status

`v0.1.0-beta.9` は **MHO984 validated / community testing beta** です。Firmware更新後の遅延FTP公開と大容量Memory BINに対する容量依存の動的待機を追加し、500 Mpts storage depth option搭載のRIGOL貸出デモMHO984で実機取得成功が報告されています。


## beta.5: Viewerプロトコル帯

Protocol Analyzerでデコードした結果を、元のViewer波形へ直接重ねられます。デコード後の自動反映が既定でONです。Viewerの「プロトコル帯」タブから表示ON/OFF、透明度、ラベル密度、再読込、クリアを操作できます。帯はイベント種別ごとに色分けされ、デコードエラーは赤系で表示されます。波形BIN自体は変更しません。

## beta.6: Viewer GUI とマウス操作

- Viewer右ペインを「波形・表示 / カーソル / 自動測定 / プロトコル / 同期確認 / データ情報 / 診断」の順に整理しました。
- カーソル線の近く（約8px）へマウスを移動するとポインタが左右移動表示へ変わり、そのまま左ドラッグでA～Zカーソルを直接移動できます。
- 波形上の右ドラッグは時間軸パンです。ドラッグ中はパン用ポインタへ変わります。
- ホイールXズーム、Shift+ホイールパン、A～Zキー選択、←→サンプル移動など従来操作も維持しています。


### beta.9: 大容量Memory BINの容量依存動的待機

- FTP上にMemory BINが出現しても、ファイルが成長中なら固定60秒で失敗せず待機を継続します。
- RG03ヘッダの宣言サイズは初期待機予算の目安にのみ使い、完了条件にはしません。
- 実測FTP SIZEの成長速度をEMAで追跡し、必要に応じてsoft deadlineを延長します。
- hard upper boundは600秒です。
- 最終受理には、6回連続の安定SIZE、ローカル/リモートサイズ一致、RG03デコーダ検証が必要です。
- RIGOL貸出デモMHO984（500 Mpts storage depth option）で実機取得成功が報告されています。

### beta.7: SINGLE取得タイムアウト処理

`START_MHO984_Toolkit.bat` の統合GUIから取得した場合、SINGLEが設定時間内にSTOPへ到達しなくても直ちに例外終了せず、**今回だけForce Trigger / さらに待つ / 中止**を選択できます。CLI/ヘッドレス実行は従来どおり安全側で中止します。待機時間は「設定 → 接続・保存先」から変更できます。

