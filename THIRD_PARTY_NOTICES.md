# Third-Party Notices

このソース配布ZIPには、NumPy/Matplotlib/Pythonの実行バイナリやソースコードを意図的に同梱していません。過去のユーザー提供ソースを含む全コードの来歴確認については `SOURCE_PROVENANCE.md` も参照してください。`setup_venv.bat` により、利用者の環境へ各プロジェクトの公式Pythonパッケージを別途インストールします。

主な依存関係:

- NumPy — BSD-3-Clause系ライセンス。https://numpy.org/
- Matplotlib — Matplotlib project licenseおよび同梱コンポーネント固有ライセンス。https://matplotlib.org/
- Python / tkinter — Python Software Foundation等のライセンス。https://www.python.org/

各パッケージは自身の依存コンポーネントを含む場合があります。再配布用にPython環境や実行ファイルをバンドルする場合は、その時点で実際に同梱する全コンポーネントのライセンスとnoticeを改めて確認してください。

本プロジェクト独自ソースのライセンスはルートの `LICENSE` を参照してください。
