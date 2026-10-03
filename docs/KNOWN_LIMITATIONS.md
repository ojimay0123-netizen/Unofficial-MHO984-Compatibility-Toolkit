# 既知の制限 / Known limitations

| 範囲 | 制限 |
|---|---|
| 機種 | MHO984 validated beta。非MHO984のオンライン取得は機種ガードで拒否。他機種互換は未保証 |
| Firmware/形式 | RG03配置、LA lower16、anonymous FTPは観測に基づく互換実装。Firmware更新で変わる可能性 |
| チャンネル | BINに実際に記録されたCH/LAのみ復元。未記録D0–D15は後から作れない |
| 時間軸 | Rawが既定。参考+2.091228967 ppmは1台由来の相対補正で全個体共通・トレーサブル校正ではない。固定遅延等は残る |
| 取得状態 | SINGLEを実行しSTOPへ移行。取得後本体はSTOPに残る。中止・失敗時は本体状態を確認 |
| 保存 | 本体C:/のBINは自動削除しない。途中PCデータセットが残る場合がある |
| 規模 | 最大BINサイズ、RAM要件、処理時間上限は未評価。動的表示間引きは全処理のメモリ上限を保証しない |
| 依存 | requirements.txtは範囲指定。同じbetaでも導入日で依存版が変わり得る。検証済み組合せの確定一覧は未記載 |
| プロトコル | CAN FD未実装。I2Cの記載範囲は7-bit。RS-485はUARTペイロードとAnalog A-Bによる解析であり通信規格全体の検証ではない |
| 復号精度 | サンプルレート、ノイズ、極性、しきい値、速度設定に依存。OKは規格適合認証ではない |
| オーバーレイ | 高密度時は描画数を制限。保存イベント一覧と画面に表示された件数は異なり得る |
| 公開版識別 | main文書更新は既存タグ・配布ZIPに自動反映されない |

詳細は [CALIBRATION.md](CALIBRATION.md)、[PROTOCOL_DECODING.md](PROTOCOL_DECODING.md)、[HARDWARE_VALIDATION.md](HARDWARE_VALIDATION.md) を参照してください。

## English

Online acquisition is guarded for MHO984. RG03/LA/FTP behavior is empirical and firmware-dependent. Only recorded channels can be recovered. Raw timing is the default; the bundled relative correction is unit-specific and non-traceable. Acquisition leaves the scope stopped; files can accumulate and interrupted datasets can remain. Maximum capture size, RAM requirements and runtime limits are unmeasured. Dependencies are range-based. CAN FD is absent; protocol OK is not certification. Dense overlays limit rendered events, not the stored list. Main updates do not automatically change published assets.
