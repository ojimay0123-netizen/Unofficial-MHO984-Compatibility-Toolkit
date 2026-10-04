# Public Publishing Checklist

GitHub等で公開する前の最終確認用です。

- [ ] `SOURCE_PROVENANCE.md` を確認し、初期コードを含めMITで公開できる権利がある。
- [ ] RIGOL firmware / official software / manuals / logos / website assetsをリポジトリやRelease ZIPへ追加していない。
- [ ] Repository名・説明の先頭付近に **Unofficial / independent / not affiliated with RIGOL** を表示する。
- [ ] 対応機種を **MHO984 validated beta** とし、MHO934/MHO954を対応済みと表示しない。
- [ ] `+2.091229 ppm` を全MHO984共通の校正値、絶対精度、トレーサブル校正として表示しない。
- [ ] Viewerの初期Digital timebaseがRawであることを確認する。
- [ ] `FORCE_TRIGGER_ON_TIMEOUT` が公開版既定OFFであることを確認する。
- [ ] LAN fallback discoveryが公開版既定OFFであることを確認する。
- [ ] 公開サンプル/Issue添付からIP、serial、username、機密波形を除去する。
- [ ] ユーザー自身のオシロに残るMemory BINのストレージ注意をREADMEに残す。
- [ ] `LICENSE`, `LEGAL_NOTICE.md`, `SECURITY.md`, `PRIVACY.md`, `THIRD_PARTY_NOTICES.md` をReleaseに含める。
- [ ] EXE化やPython runtime同梱を行う場合は、その配布物に実際に含まれる第三者ライブラリのlicense/noticeを別途監査する。
- [ ] 商用配布、企業公式配布、他社商標を使った販売ページ等を行う場合は、必要に応じて専門家・権利者へ確認する。

## 推奨Repository description

> Independent, unofficial MHO984 compatibility toolkit for synchronized analog + D0-D15 capture and analysis. MHO984 validated beta; not affiliated with or endorsed by RIGOL.

## 推奨Release tag

`v0.1.0-beta.9`

## beta.2 acquisition compatibility

- Confirm that an immediate `:TRIGger:SWEep?` value of `AUTO` after `:SINGle` is logged but is not by itself treated as failure.
- Confirm final acquisition success still requires a confirmed `STOP`.
