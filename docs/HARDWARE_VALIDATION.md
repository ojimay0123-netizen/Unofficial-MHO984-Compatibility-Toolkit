# 実機確認手順 / Hardware validation

これは利用者が実施する確認手順です。この文書の追加自体は新たな実機検証の完了を意味しません。

## 検証記録

実施日、コミット/配布版、Windows、Python/依存版、型番、フルFirmware（本体画面）、信号源、配線、Analogプローブ倍率、LAしきい値、サンプルレート、メモリ長、トリガ設定を記録します。*IDN?の00.01.00だけでフルFirmwareビルドを特定しないでください。

入力範囲とGND条件を確認し、最初は小さな記録で既知信号を取得します。配線・電圧条件は使用するプローブと機器に合わせます。判定許容差は信号源精度、サンプル間隔、プローブ条件から事前に決め、数値を記録します。

## チェック表

| 項目 | 手順 | 合格条件 |
|---|---|---|
| 取得 | IP設定後に取得し全進捗を確認 | STOP、本体保存、FTPサイズ検証、デコードが完了。Viewer表示だけで判定しない |
| Analog | 必要CHを表示し、既知の周波数・振幅を入力 | CH番号、周期、振幅、時間範囲が本体・信号源と許容差内で整合 |
| D0–D15 | 各線を1本ずつ切替。他線を既知の静的レベルにする | 番号の入替なし、静的線に偽エッジなし。LAレコードあり |
| 記録範囲 | 取得点数、サンプル間隔、先頭・末尾時間を比較 | 記録の切詰めや欠落を説明できる。表示間引きを元データ欠落と混同しない |
| 同期 | 同じ既知信号をAnalog/Digitalへ適切に分岐。Rawで前半と後半を比較 | 固定遅延と時間ドリフトを分離して記録。複数周波数・記録で再現性を確認 |
| オフライン | 本体でMemory waveform BINを保存・PCへコピー。LANなしで開く | 元BINのCH、点数、時間範囲を復元。LA未記録ならDigitalなしが正しい |
| 再現性 | 同条件を複数回、メモリ長やサンプルレートを変えて実施 | 同じ判定が再現。条件依存の不一致は未確認として報告 |

## beta.7タイムアウトの3分岐

既知信号を使用し、トリガ条件が成立しない状態で短い待機時間を設定します。各分岐は別取得として試します。

1. **さらに待つ**: 自動Forceせず次の待機区間へ進むことを確認。その後条件を成立させてSTOPと正常取得を確認。
2. **中止**: 未完了を成功扱いしないことを確認。途中フォルダと本体状態を確認。
3. **今回だけForce**: 明示操作で1回だけForceを行い、STOP確認後に処理が進むことを確認。結果を「強制トリガ」と記録。STOPへ到達しない場合は成功としない。

CLI/ヘッドレスは明示的Force指定なしでタイムアウト中止することを別途確認します。タイムアウトのGUI選択と、メイン画面の処理中止ボタンは別経路です。

## プロトコルの最小確認例

以下は設定例であり、実機合格済みデータを同梱したものではありません。

| 対象 | 信号と設定例 | 確認 |
|---|---|---|
| UART | 既知バイト列0x55, 0xA5、9600 baud、8N1。TXを選択し実信号に合う極性 | 送信列と復号値、フレーム時間が一致 |
| I2C | 既知の7-bitアドレスへの書込。SCL/SDAをそれぞれ指定 | START、アドレス/RW、ACK/NACK、データ、STOPが一致 |
| SPI | 既知ワード、Mode 0、8bit、MSB first。SCLK/MOSI/必要ならCSを指定 | クロック、CS極性、ワード値が一致 |

Analog源ではしきい値・ヒステリシスを設定し、論理化波形も確認します。CSV/JSONへ出力して値を照合し、Viewer帯の位置も確認します。OK表示は実装したチェックの通過を示すだけです。

## 公開済み証拠の範囲

PUBLIC_RELEASE_AUDIT.mdには実機RG03の保存データ回帰（CH1/CH2/LA、D0–D15 12,500,000点）、合成プロトコル信号、モック取得、仮想表示GUI試験が記載されています。元の実機キャプチャは非同梱です。PUBLIC_RELEASE_TEST_REPORT.jsonのbeta.7 PASSは試験方式の内訳を十分に記載していないため、Force/Wait/Abortの実機完了をこの文書から推定しないでください。正確なWindows/Python構成、フルFirmware、全機能の実機検証表は追加確認が必要です。

## English

Record the exact environment, full firmware, probes, thresholds, sample rate, record length and trigger conditions. Test analog amplitudes/periods, each digital bit, static channels, full time coverage, raw analog/digital alignment and offline replay. Define tolerances before testing. Exercise Wait, Abort and explicit Force in separate runs, and distinguish forced captures from normal triggers. Existing audits mix saved hardware captures, synthetic tests, mocks and virtual-display checks; they do not establish fresh hardware validation for every beta.7 branch.
