# Digital Relative Timebase Alignment

## 用語

本プロジェクトでは、DigitalとAnalogの時間軸差を補正する機能を**相対時間軸アライメント (relative timebase alignment)** と呼びます。

これは計量トレーサブルな「校正証明」ではありません。

## Reference profile

同梱の参考プロファイル:

`digital_timebase_reference_profile_mho984_0034.json`

には、1台のMHO984で得た:

`+2.091228967 ppm`

が保存されています。

測定条件は、1台のファンクションジェネレータから10 MHzと12 MHzを出力し、それぞれをAnalog入力とDigital入力へ分岐して、Analog/Digitalのエッジドリフトを比較したものです。

補正式:

`t_corrected = t_ref + (t_raw - t_ref) * (1 + ppm * 1e-6)`

Referenceはtrigger `t_ref = 0 s` です。

## 公開版の初期値

**ViewerはRawを初期値にします。**

参考プロファイルは利用者が明示的に「補正後」を選んだときだけ表示時間軸へ適用します。デコーダが作るRaw eventは保持されます。

## なぜ全個体へ自動適用しないのか

次の要因が未評価です。

- 個体差
- 温度差
- Firmware差
- LA option / probe / threshold条件
- record length / sample rate条件
- 他のMHO900モデル

したがって、`+2.091228967 ppm` を全MHO984の固有定数として扱ってはいけません。

## 個体ごとの確認を推奨

時間精度が重要な場合は、同一信号源をAnalogとDigitalへ分岐し、複数周波数・複数recordでRaw driftを測定してください。2系統以上で同程度の線形ppm差が再現する場合にのみ、個体用の相対補正値として検討してください。

補正後も、固定遅延、Comparator/threshold、ケーブル、Analog補間、Digital 1 ns量子化等の影響は残ります。
