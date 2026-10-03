# Legal Notice / 権利・免責に関する注意

**この文書は法的助言ではありません。** 公開・再配布・商用利用、または所属組織のコンプライアンス上の判断が必要な場合は、必要に応じて専門家へ確認してください。

## 1. 独立した非公式プロジェクト

MHO984 Toolkitは独立した互換性プロジェクトです。RIGOL Technologies Co., Ltd.、その子会社、関連会社、販売代理店等による公式製品ではなく、提携、承認、認証、保証を受けたものでもありません。

`RIGOL`、`MHO900`、`MHO984`、その他の製品名・商標・サービスマークは各権利者に帰属します。本プロジェクトでは対象機器を識別する目的で文字による名称を使用します。RIGOLのロゴや公式ブランド素材は本配布物に含めません。

## 2. RIGOL由来のMaterialsを再配布しない方針

本配布物には、RIGOLのFirmware、公式Software、Manual/PDF、Webサイト画像、ロゴ、BIOS、ドライバ等を同梱しません。

RIGOL公式サイトのTerms and Conditionsは、同社サイト上のsoftware/firmware/manual等のMaterialsおよびMarksについて権利を留保し、無断の複製・再配布・一定の利用等を制限しています。利用者・再配布者は最新のRIGOL Terms、製品ライセンス、および適用法令を自身で確認してください。

Reference: https://www.rigol.com/intl/legal/terms.html

## 3. 本ツールの実装範囲

取得には、MHO900 Programming Guideに掲載されているSCPI機能を含みます。特に `:SAVE:MEMory:WAVeform <path>` は公式Programming Guideに記載されているコマンドです。

一方で、次は本プロジェクトの実機互換性検証に基づく部分であり、RIGOLが公開フォーマットとして保証しているものとは扱いません。

- 保存されたRG03 BINの内部レコード解釈
- large-record LAでlower16をD0-D15として扱う互換処理
- 実機で観測されたanonymous FTPによるBIN取得
- Analog/Digital間の経験的な相対時間軸補正

本プロジェクトはこれらを「RIGOL公式仕様」と主張しません。Firmwareやモデル差により変更される可能性があります。

Official Programming Guide reference:
https://www.rigol.com/dam/global/downloads/brochures/en/program-guide/oscilloscopes/MHO900-ProgrammingGuide.pdf

## 4. 所有・管理権限のある機器でのみ使用

利用者は、自身が所有する機器、または明示的に操作権限を与えられた機器・ネットワークでのみ本ツールを使用してください。アクセス制御の回避、他者の機器やネットワークへの無断アクセスを目的とした使用は想定していません。

## 5. 測定・安全上の免責

本ソフトウェアはBeta版であり、MIT Licenseの条件に従い**AS IS**で提供されます。

本ツールの表示値、デコード、エッジ時刻、周波数、Duty、ppm、相対時間軸補正について、正確性、完全性、特定目的適合性、計量トレーサビリティ、規格適合性を保証しません。

以下の用途では、本ツールだけを根拠に判断しないでください。

- 人命・医療・安全保護に関わる判断
- 高エネルギー、高電圧、機械安全等の保護動作判定
- 法令・認証・規制適合性の証明
- 校正証明、検査成績、トレーサブル測定の代替
- 故障時に重大な財産損害が生じる用途

必要な場合は、適切に校正された機器・標準手順・独立した検証を併用してください。

## 6. 相対時間軸補正は「校正証明」ではない

同梱の約 `+2.091229 ppm` は、1台のMHO984で同一信号源をAnalog/Digitalへ分岐して得た経験的な相対アライメント値です。全個体・全Firmware・他モデルへ一般化できることは確認されていません。

公開版のViewerはRawを初期値とします。詳しくは `docs/CALIBRATION.md` を参照してください。

## 7. ソース来歴の確認

本Beta版は既存のユーザー提供スクリプトを基礎に改良された経緯があります。本パッケージ作成時点でRIGOL由来のfirmware/manual/software binary等は意図的に含めていませんが、公開者は過去に持ち込まれたソース全体について再配布権限を確認してください。`SOURCE_PROVENANCE.md` を参照してください。

## 8. 第三者ソフトウェア

NumPy、Matplotlib、Python等は別プロジェクトであり、それぞれのライセンスが適用されます。本ZIPではそれらの実行バイナリを同梱せず、利用者の環境へ別途インストールします。詳細は `THIRD_PARTY_NOTICES.md` を参照してください。

## 9. 再配布時

この公開版を再配布・改変する場合は、少なくとも次を維持することを推奨します。

- `LICENSE`
- 本 `LEGAL_NOTICE.md`
- 非公式・非提携であることの明示
- RIGOL公式Materialsを追加同梱しないこと
- 互換性・検証済みモデルを過大表示しないこと
- Digital時間軸補正をトレーサブルな校正値として表示しないこと


## Protocol names and specifications

Names such as UART, RS-232, RS-485, I2C, SPI, LIN, CAN, GPS, NMEA and UBX are used only to describe interoperability/analysis targets. Any associated trademarks remain with their respective owners. This distribution does not bundle official protocol standards, certification marks, logos, or proprietary vendor protocol documentation. Decoder output is not a statement of standards conformance or certification.
