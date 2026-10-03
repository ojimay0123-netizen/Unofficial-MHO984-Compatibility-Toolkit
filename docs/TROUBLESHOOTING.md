# 障害切り分け / Troubleshooting

まずメイン画面の進捗ログで、どの段階まで完了したかを確認します。失敗したデータセットは成功結果と混同しないよう保存し、次の取得で新しい結果を確認してください。

| 症状・段階 | 確認・対処 |
|---|---|
| BATから画面が開かない | setup_venv.batの完了、Python/Tkinterを確認。下記コンソール起動で例外全文を確認 |
| pip/setup失敗 | エラー全文、Python版、パッケージ取得接続、保存先権限を確認。成功表示前に進まない |
| 接続・機種識別失敗 | 本体IPとTCP 5555への到達性を確認。MHO984以外は意図的に拒否 |
| SINGLE待機 | 信号・トリガ条件を確認。統合GUIで待機延長／今回だけForce／中止を選択。Force結果は通常トリガ取得と区別 |
| 本体Memory BIN保存失敗 | 本体C:/空き容量とログを確認。ファイルを自動削除しない |
| Automatic LAN retrieval failed | 作業データセットのlan_retrieval_r12.jsonを確認。TCP 21、FTPデータ接続、対象BIN名・サイズ安定・転送検証を切り分け |
| デコード失敗 | MHO984 Memory waveform BINか、コピーが完了しているかを確認。Firmware、ファイルサイズ、デコード例外を記録 |
| Digitalがない | 元BINのLAレコードと取得時LA/POD設定を確認。未記録チャンネルは復元できない |
| Viewerが開かない | 下記依存import確認、メインログのViewer例外、対象データセットを確認 |
| 時間軸が合わない | Rawで既知信号を比較し、固定遅延とドリフトを分ける。参考ppmを全個体へ適用しない |

展開先でPowerShellを開き、コンソール付きで起動します。通常BATはpythonwを優先するため起動例外が見えない場合があります。

```powershell
.\.venv\Scripts\python.exe -c "import tkinter, numpy, matplotlib; print(numpy.__version__, matplotlib.__version__)"
.\.venv\Scripts\python.exe .\mho984_toolkit_main.py
```

setupはpyを利用できますが、通常起動BATはグローバルのpyを探索しません。先にsetupでローカルvenvを作ってください。

## Issueへ添付する情報

- beta版番号とコミット、Windows/Python/NumPy/Matplotlib版
- 型番、フルFirmware版（シリアルは伏せる）
- Analog/LA設定、メモリ長、サンプルレート、トリガ設定、通常／Forceの別
- 操作手順、期待結果、実際の結果、失敗した段階と例外全文
- 「ツール → 診断情報ZIPを作成」またはexport_diagnostics_for_issue.batの出力

診断ZIPは共有前に内容を確認してください。BIN波形、IP、シリアル、ユーザー名、機密信号を無検討で添付しないでください。lan_retrieval_r12.jsonはFTP段階、performance_r12.jsonは成功時の処理時間確認に使います。失敗時には後者が存在しない場合があります。

中止で途中データが残る場合があります。本体の状態を本体画面で確認し、不要なPC作業フォルダは内容を確認して整理します。本体C:/のBINは標準UIで別途管理します。

## English

Identify the last completed stage in the main log. Run the local venv Python in a console to expose startup errors. Check SCPI TCP 5555 separately from FTP TCP 21 and its data connection. Use lan_retrieval_r12.json for retrieval failures; performance_r12.json may not exist after failure. Check Memory BIN format and LA presence for decode/missing-channel problems. Report reproducible steps and sanitized diagnostics, not confidential raw waveforms. Interrupted datasets may remain; inspect scope state before retrying.
