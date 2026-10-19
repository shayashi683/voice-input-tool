# Voice Input Tool

OpenAI GPT-Transcribe / ReazonSpeech (sherpa-onnx) による
macOS 向け音声入力ツールです。

## GPT-Transcribe を使う

メニューバーの「設定...」で音声認識を **GPT-Transcribe** に変更し、OpenAI APIキーを設定してください。録音停止後に音声をOpenAIへ送り、日本語・英語の言語ヒントと、設定した話題・用語を使って文字起こしします。APIキーは `.env` に保存し、`config.json` やログには出しません。

GPT-Transcribeは句読点を含む結果を返すため、通常は「OpenAIで補正する（任意）」をオフにして使えます。必要な場合は、同じOpenAI APIキーで追加の句読点補正を使えます。この場合、文字起こし後のテキストもOpenAIへ送信します。「話題」には仕事内容などの短い背景、「用語」には商品名や略語の正確な表記を指定できます。認識に不要な用語を大量に入れることは避けてください。

新規インストールの初期設定はローカルのReazonSpeechで、アップデート時は既存の音声認識設定を引き継ぎます。設定でGPT-Transcribeを選んだ場合だけ音声をクラウドに送ります。APIが失敗した場合も元音声は履歴に保存し、失敗理由を記録します。別モデルへ自動で切り替えることはありません。録音開始時のモデルを履歴に保存するため、切り替え前の中断録音が自動復旧でクラウドに送信されることはありません。明示的な履歴の再文字起こしでは、現在選択中のモデルを使います。

GPT-Transcribeの送信上限はWAV 25MBです（16kHz・モノラルPCM16で約13分）。上限を超えた録音は送信せず、元音声を履歴に残します。長い録音は区切るか、ローカルモデルで再文字起こししてください。

コマンドから一時的にモデルを指定する場合:

```bash
python voice_input.py --asr gpt-transcribe --retry-recording <録音ID> --no-llm
python voice_input.py --asr reazonspeech --retry-recording <録音ID> --no-llm
```

公式仕様: [GPT-Transcribe](https://developers.openai.com/api/docs/models/gpt-transcribe)、[文字起こしと用語ヒント](https://developers.openai.com/api/docs/guides/speech-to-text)。

## 特徴

- GPT-Transcribeによるクラウド音声認識、または完全ローカルASR（ReazonSpeech K2 v2 int8）
- 待機中も直前 0.75 秒の音声だけを循環保持し、ホットキー直後の話し始めを保護
- USBマイクの抜き差しを監視し、再接続した指定マイクへ自動で復帰
- 録音は明示的に停止するまで継続（無音では停止しない）
- 会話内の長い無音を発話境界として認識し、途中の発話抜けを防止
- 音声エンジンを排他制御し、アプリ再起動時の重複貼り付けを防止
- 文字起こし・LLM補正・入力処理は録音停止後に一括実行
- ASRモデルは初回文字起こし時に遅延読み込みし、起動を軽くする
- LLM補正オン/オフ切替可能（補正用APIキー未設定時はASR結果をそのまま入力）
- LLM補正ON時も、補正APIが一時的に失敗した場合はASR結果をそのまま入力
- LLMが説明文や英語のメタ返答を返した場合は補正失敗として扱い、ASR結果へフォールバック
- 追加の句読点補正はOpenAI APIの `gpt-5.6-luna` を使用（初期設定はOFF）
- macOS メニューバーアプリとして常駐
- ネイティブ設定画面（PyObjC）でGUIから設定変更可能
- グローバルホットキー対応（設定画面でカスタマイズ可能）
- 認識結果はカーソル位置に入力（貼り付け不可時は補正済みテキストをクリップボードに保持）
- 詳細ログを `logs/` に出力

## USBマイクの再接続

設定の「入力マイク」で選んだマイクは、接続順で変わる番号ではなく `name:Shure MV7+` のような名前で記憶します。旧形式の番号は、起動時に現在のマイク名へ移行します。同じ名前のマイクが複数ある場合は、最初に見つかった入力を使用します。

macOSの機器一覧と既定入力の変更を約1秒間隔で確認し、接続が変わると古いストリームを閉じて機器一覧を更新します。指定マイクが外れた間は利用可能な既定入力を使い、再接続すると指定マイクに戻ります。音声が3秒以上届かない場合も接続を開き直します。機器が使用可能になるまで、自動で再試行します。

録音中に再接続しても、切断前に受信した音声と保存先は維持します。ただし、マイクが切断中の音声や接続を開き直す間の音声は取得できません。未接続のまま設定を保存しても、指定マイクの選択は保持します。

## 文字起こしの保持と復旧

録音開始時に `recordings/<録音ID>/` を作成し、録音中の音声をバックグラウンドで逐次保存します。停止後は保存を確定してから、専用ワーカーが順番に文字起こしします。前の文字起こし中でも次の録音を開始できます。

- `audio.f32`: 16 kHz・モノラル・little-endian float32 の元音声
- `raw.txt`: ASRの認識原文
- `transcript.txt`: 入力に使うテキスト。補正失敗時は原文
- `recording.json`: 処理状態・作成日時・原文・補正結果・失敗理由

認識結果は貼り付け前に保存します。貼り付けは要求ID付きで送信し、ネイティブ側の応答を確認します。応答がない場合は `delivery_unknown` として残し、重複入力を避けるため自動で再貼り付けしません。`paste_sent` はキーイベントの送信確認であり、入力先アプリ内の反映までは保証しません。

終了時は録音を保存してからマイクを閉じます。再起動すると中断された録音を復元し、結果を履歴に保存します。古い入力先へ自動貼り付けはしません。録音中の強制終了・停電では、直近の未保存の音声が失われる可能性があります（通常は約0.5秒間隔でディスクへ同期。ディスク停止や書き込み遅延時は長くなります）。保存に失敗した場合は通知し、終了時まで音声をメモリに保持して再保存を試みます。

履歴はメニューバーの「録音履歴を開く」から確認できます。コマンドからの表示・再文字起こしも可能です。

```bash
python voice_input.py --history
python voice_input.py --show-recording <録音ID>
python voice_input.py --retry-recording <録音ID> --no-llm
```

再文字起こしは新しい録音IDに保存し、元の結果を上書きしません。インストール版では `~/Library/Application Support/VoiceInputTool/` が保存先です。このフォルダで `.venv-framework/bin/python voice_input.py ...` を実行できます。

音声と履歴は自動削除しません。音声は1時間あたり約230 MBです。不要になった録音は、処理が終了してから該当IDのフォルダごと削除できます。フォルダは本人だけが読み書きできる権限で作成します。変更前に失われた音声は、保存済みデータがなければ復元できません。

### 開発時の検証

```bash
python -m unittest discover -s tests -q
./scripts/test_native_output.sh
APP_BUNDLE_PATH=/tmp/VoiceInputTool-test.app ./scripts/build_macos_app.sh
```

回帰テストでは保存途中の障害、連続録音、中断からの復元、空の認識・補正結果、貼り付け応答タイムアウト、ネイティブ受信中のUTF-8・JSONの分断を検証します。

## Macアプリとして使う（推奨）

社外配布版はPKGで提供します。PKGを開くと、`/Applications/VoiceInputTool.app` と実行に必要なファイルを配置します。

公開URL:

```text
https://voice-input-tool.pages.dev/
```

無料配布の開発版は未署名です。警告が出る場合は、PKGを右クリックして「開く」を選んでください。完全に警告なしで配布するにはApple Developer Programでの署名とNotarizationが必要です。

インストール後にアプリが自動で開き、初回は「はじめての設定」ガイドを表示します。ガイドに沿ってAPIキー、マイクとアクセシビリティ権限、試し録音を設定できます。キーは `.env` に保存され、`config.json` には保存されません。ガイドを閉じても、完了にするまでは次回起動時に再表示します。メニューバーの 🎙 →「はじめての設定...」からも開けます。

社員は1Passwordの `noko_shared` にある **VoiceInputTool - OpenAI API** からキーをコピーし、ガイドの設定画面で音声認識をGPT-Transcribeに変更して貼り付けてください。権限を許可したら、メモ帳などの入力欄で設定済みホットキー（初期値は `Ctrl+Shift+Space`）を押して録音を開始し、もう一度押して停止すると動作を確認できます。

## 開発環境から手動セットアップする

このリポジトリを取得した後、次を実行すると、依存関係・音声認識モデル・Macアプリの作成までまとめて行います。

```bash
cd ~/voice-input-tool
./scripts/install_mac_app.sh --open
```

OpenAI APIキーは、起動後の設定画面から入力してください。環境変数 `OPENAI_API_KEY` が設定されている場合は、インストール時に `.env` へ保存します。キーは `config.json` には保存されません。

アプリは既定では `~/Applications/VoiceInputTool.app` に作成されます。次回以降はFinderからこのアプリを開くか、次で起動できます。

```bash
open ~/Applications/VoiceInputTool.app
```

API Keyを後で入れる場合は、アプリ起動後にメニューバーの 🎙 →「設定...」から入力できます。

## 配布用ソースについて

このリポジトリには、インストール済みアプリから反映したアプリ本体、テスト、ローカルビルド用スクリプトを含みます。配布サイトの `landing/`、PKG作成用スクリプト、Cloudflare Pagesへのデプロイスクリプトは含まれていません。アプリをローカルで作成する場合は、上記の `scripts/install_mac_app.sh` を使ってください。

`scripts/build_runtime_payload.sh` と `scripts/package_runtime_chunks.sh` は、配布用ランタイムの作成・分割用です。これらだけではPKGや配布サイトの作成・公開はできません。

## 社員向けセットアップ（手動）

通常は配布PKGをインストールし、初回の設定ガイドに沿って進めてください。以下は開発環境を手動で構築する場合の手順です。

> 以下の例は `~/voice-input-tool` に置く前提です。別の場所に置く場合は、そのフォルダで `scripts/install_mac_app.sh` を実行してください。作成されるMacアプリには実際のフォルダパスが保存されます。

### 0. 事前に必要なもの

- macOS
- Python 3.11 推奨（3.10以上でも動作想定）
- ターミナル操作権限
- 空き容量 2GB 以上（ASRモデルが約1.4GB）
- GPT-Transcribeまたは追加のLLM補正を使う場合: OpenAI APIキー

Python が入っているか確認します。`python3.11` があれば優先して使います。

```bash
if command -v python3.11 >/dev/null; then python3.11 --version; else python3 --version; fi
```

Python がない、または古い場合は、社内の標準手順に従って Python を入れてください。Homebrew を使える環境なら次で入れられます。

```bash
brew install python@3.11
```

`git` やビルドツールがない場合は、以下を実行して Command Line Tools を入れます。

```bash
xcode-select --install
```

### 1. ツール本体を配置する

社内Gitから取得する場合:

```bash
cd ~
git clone <社内GitリポジトリURL> voice-input-tool
cd ~/voice-input-tool
```

`<社内GitリポジトリURL>` は、共有時に実際のURLへ置き換えてください。

ZIPや社内ファイル共有で受け取った場合は、展開後のフォルダ名を `voice-input-tool` にして、ホーム直下に置いてください。

```bash
cd ~
# 例: Downloads に展開された場合
mv ~/Downloads/voice-input-tool ~/voice-input-tool
cd ~/voice-input-tool
```

### 2. Python仮想環境を作る

```bash
cd ~/voice-input-tool
PYTHON_BIN="$(command -v python3.11 || command -v python3)"
"$PYTHON_BIN" -m venv .venv-framework
source .venv-framework/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

2回目以降に作業する場合も、コマンド実行前は次で仮想環境を有効化します。

```bash
cd ~/voice-input-tool
source .venv-framework/bin/activate
```

### 3. 音声認識モデルをダウンロードする

```bash
mkdir -p ~/voice-input-tool/models
cd ~/voice-input-tool/models

# ASRモデル (ReazonSpeech K2 v2 int8)
curl -L -O https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-zipformer-ja-reazonspeech-2024-08-01.tar.bz2
tar xjf sherpa-onnx-zipformer-ja-reazonspeech-2024-08-01.tar.bz2

cd ~/voice-input-tool
```

モデル配置を確認します。

```bash
test -f models/sherpa-onnx-zipformer-ja-reazonspeech-2024-08-01/encoder-epoch-99-avg-1.int8.onnx && \
test -f models/sherpa-onnx-zipformer-ja-reazonspeech-2024-08-01/decoder-epoch-99-avg-1.int8.onnx && \
test -f models/sherpa-onnx-zipformer-ja-reazonspeech-2024-08-01/joiner-epoch-99-avg-1.int8.onnx && \
test -f models/sherpa-onnx-zipformer-ja-reazonspeech-2024-08-01/tokens.txt && \
echo "モデル配置 OK"
```

社内で複数人がセットアップする場合は、上記 `.tar.bz2` を社内ファイル共有に置いてからコピーすると、各自のダウンロード時間を短縮できます。

### 4. OpenAI APIキーを設定する（クラウド機能を使う場合）

GPT-Transcribeまたは追加のLLM補正を使う場合は、起動後の設定画面でOpenAI APIキーを入力してください。キーは `.env` の `OPENAI_API_KEY` に保存されます。`.env` はGit管理対象外で、`config.json` にはキーを保存しません。

ローカルのReazonSpeechを選び、追加のLLM補正をOFFにすればAPIキーなしで使えます。GPT-Transcribeでは追加のLLM補正は通常OFFのままで使えます。補正APIが失敗した場合は、補正前のASR結果を入力します。

### 5. 起動前テストを行う

```bash
cd ~/voice-input-tool
source .venv-framework/bin/activate
python voice_input.py --test --no-llm
```

ASRのテスト結果が表示されれば、Python依存関係とモデル配置は正常です。

### 6. 起動する

ターミナルから起動する場合:

```bash
cd ~/voice-input-tool
chmod +x start.sh
./start.sh
```

起動すると、メニューバーに 🎙 アイコンが表示されます。`~/Applications/VoiceInputTool.app` がある場合は、`./start.sh` からそのアプリを起動します。

設定でLLM補正をOFFにした後、一時的にLLM補正ONで起動したい場合は次を使います。

```bash
./start.sh --llm
```

### 7. macOSの権限を許可する

初回起動時、または初回録音時に権限許可が必要です。

| 権限 | 用途 | 許可する対象 |
|------|------|--------------|
| マイク | 音声入力 | `VoiceInputTool.app` / ターミナル / iTerm |
| アクセシビリティ | ホットキー、カーソル位置への入力 | `VoiceInputTool.app` / ターミナル / iTerm |
| 入力監視 | ホットキーが反応しない場合に必要なことがあります | `VoiceInputTool.app` / ターミナル / iTerm |

設定場所:

1. macOSの「システム設定」を開く
2. 「プライバシーとセキュリティ」を開く
3. 「マイク」「アクセシビリティ」「入力監視」を確認
4. 起動に使っているアプリ（`VoiceInputTool.app`、Terminal、iTermなど）を許可
5. 権限を変更したら、Voice Input Toolを一度終了して再起動

### 8. 動作確認

1. 入力したいアプリ（Slack、Notion、ブラウザ、エディタなど）のテキスト欄にカーソルを置く
2. `Ctrl+Shift+Space` を押す、またはメニューバーの 🎙 →「録音開始」を選ぶ
3. 話す
4. 話している間、メニューバーが三つの白い丸の入力中アイコンに変わることを確認
5. もう一度 `Ctrl+Shift+Space` を押す、またはメニューから停止する
6. 録音停止後、認識・補正後のテキストがカーソル位置に入力される

### 9. ダブルクリックで起動する

次を実行すると、ネイティブアプリが `~/Applications/VoiceInputTool.app` に作成されます。

```bash
cd ~/voice-input-tool
./scripts/build_macos_app.sh
```

作成後はFinderからダブルクリックで起動できます。

ログイン時に自動起動したい場合は、macOSの「システム設定」→「一般」→「ログイン項目」に `VoiceInputTool.app` を追加してください。

### 10. 更新手順

社内Gitから取得している場合:

```bash
cd ~/voice-input-tool
git pull
source .venv-framework/bin/activate
python -m pip install -r requirements.txt --upgrade
```

モデル更新が案内された場合だけ、手順3のモデルダウンロードを再実行してください。

## 使い方

### メニューバーから操作

メニューバーの 🎙 アイコンをクリックします。

| メニュー項目 | 説明 |
|-------------|------|
| 録音開始/停止 | 録音をトグル |
| LLM補正: ON/OFF | LLM補正の有効/無効を切替 |
| アクセシビリティ許可を確認 | 自動入力に必要なmacOS権限を確認 |
| 設定... | 設定画面を開く |
| はじめての設定... | 初回の設定ガイドを開く |
| 終了 | アプリを終了 |

### CLIから使う

```bash
cd ~/voice-input-tool
source .venv-framework/bin/activate

# メニューバーアプリとして起動
./start.sh

# テストモード（WAVファイルでASR動作確認）
python voice_input.py --test --no-llm
```

### ホットキー

デフォルト: `Ctrl+Shift+Space` で録音開始/停止。設定画面で変更できます。

## 入力フロー

1. ホットキー/メニューで録音開始
2. マイク入力をメモリ上に蓄積
3. ホットキー/メニューで録音停止
4. 停止後に録音全体をASRでテキスト化
5. LLM補正ONの場合はOpenAI APIで句読点を整形
6. 補正済みテキストを録音開始時の前面アプリへ入力

重要な仕様:

- 無音では文字起こしを開始しません。停止操作後にだけ文字起こしします。
- LLM補正ON時は、補正したテキストを出力します。
- LLM補正に失敗した場合は、補正前のASR結果を出力します。
- LLM補正OFF時は、ASR結果をそのまま出力します。
- 貼り付けに失敗した場合でも、補正済みテキストはクリップボードに残ります。

## 状態表示

メニューバーアイコンは状態に応じて変わります。

| アイコン | 状態 |
|---------|------|
| 🎙 | 停止中 |
| ⏳ | マイク起動中 |
| 🟢 | 入力待機中 |
| ![入力中](assets/typing-indicator.svg) | 音声入力中 |
| 📝 | 音声認識中 |
| 🧠 | LLM補正中 |
| ⌨️ | カーソル位置へ入力中 |

入力待機中から音声入力中への切り替えは、マイク入力の音量とノイズ床をもとに判定します。音声入力中は三つの白い丸が順番に跳ねるインジケーターを表示します。

## 設定画面

メニューバーの 🎙 →「設定...」で開きます。

| 設定項目 | 説明 | デフォルト |
|---------|------|-----------|
| 音声認識 | GPT-Transcribe / ローカルReazonSpeech | ローカルReazonSpeech |
| OpenAI APIキー | GPT-Transcribeと追加の句読点補正で共用 | `.env` から読み込み |
| 話題・用語 | GPT-Transcribeに渡す背景と正しい用語表記 | 未設定 |
| OpenAIで補正する（任意） | 追加の句読点補正をON/OFF | OFF |
| 録音 開始/停止 | ホットキー設定 | Ctrl+Shift+Space |
| 入力マイク | 使用するマイク。未選択時はシステムの既定入力 | 自動選択 |
| LLM 補正プロンプト | LLM補正時のシステムプロンプト | 句読点挿入のみ |

設定はツールフォルダ内の `config.json` に保存されます。API Key は `.env` に保存され、`config.json` には保存されません。

## LLM設定

既定値は `voice_input_tool/config.py` の `DEFAULTS` で管理しています。

| キー | 既定値 | 説明 |
|------|--------|------|
| `llm_model` | `gpt-5.6-luna` | OpenAI APIのモデル名。設定画面でGPT-5.6 Luna / Terra / Solを選択可能 |
| `use_llm` | `false` | 文字起こし結果に追加の句読点補正を行うか |

補正には文字起こしと同じOpenAI APIキーを使います。補正APIが利用できない場合は、補正前のASR結果を入力します。以前の外部サービスを使う設定から更新した場合、送信先がOpenAIに変わるため、追加の補正はOFFになります。必要な場合は設定画面で有効にしてください。

## ログとトラブルシュート

ログは以下に出力します。

```bash
~/voice-input-tool/logs/voice-input.log
~/voice-input-tool/logs/voice-input-error.log
```

リアルタイム確認:

```bash
tail -f ~/voice-input-tool/logs/voice-input.log
```

出力されない場合は、以下を確認します。

```bash
tail -n 120 ~/voice-input-tool/logs/voice-input.log
tail -n 120 ~/voice-input-tool/logs/voice-input-error.log
```

よく見るログ:

| ログ | 意味 |
|------|------|
| `ASR (...s): ...` | ASRは成功 |
| `LLM補正リクエスト` | OpenAI APIへ送信開始 |
| `LLM応答` | OpenAI APIから応答あり |
| `content_len=0` / `content=None` | LLM本文が空。LLM補正失敗扱い |
| `LLMが補正対象ではなく説明文を返しました` | LLMが句読点補正ではないメタ返答を返したため、ASR結果を使用 |
| `finish_reason=length` | reasoning等で token を使い切った可能性 |
| `カーソル位置に入力しました` | 入力成功 |
| `貼り付け不可のためコピーしました` | 補正済みテキストをクリップボードに保持 |

ログでは API Key をマスクします。

初期セットアップで詰まりやすい点:

| 症状 | 確認すること |
|------|--------------|
| `ModuleNotFoundError` が出る | `cd ~/voice-input-tool` → `source .venv-framework/bin/activate` → `python -m pip install -r requirements.txt` を再実行 |
| `モデルファイルが見つかりません` と出る | `models/` 配下に ASRモデル展開済みフォルダがあるか確認 |
| メニューバーに何も出ない | PKG版は `/Applications/VoiceInputTool.app`、手動ビルド版は `~/Applications/VoiceInputTool.app` を起動しているか確認。`logs/native-status.log` と `logs/native-engine.err.log` も確認 |
| マイク入力できない | macOSの「マイク」権限で、起動に使っているアプリを許可して再起動 |
| `Ctrl+Shift+Space` が効かない | 「アクセシビリティ」と、必要に応じて「入力監視」を許可して再起動。別アプリのショートカットと衝突していないかも確認 |
| カーソル位置に入力されない | 「アクセシビリティ」権限を確認。貼り付け不可のアプリではクリップボードに残ります |
| GPT-Transcribeで文字起こしに失敗する | 設定のOpenAI APIキー、OpenAIのAPI利用枠、ネットワーク接続を確認。元音声は履歴に保存されます |
| LLM補正が適用されない | 設定のOpenAI APIキー、OpenAIのAPI利用枠、選択した補正モデルの利用可否を確認。補正失敗時はASR結果を入力します |

## ファイル構成

```
voice_input.py            - CLIエントリーポイント
voice_input_tool/cli.py   - 起動モードの選択
voice_input_tool/engine.py - アプリ状態と処理フローの調整
voice_input_tool/audio_recorder.py - プリロール/ポストロール付き録音
voice_input_tool/transcription.py  - ASRの遅延読み込みと文字起こし
voice_input_tool/gpt_transcription.py - OpenAI GPT-Transcribe音声認識
voice_input_tool/llm_correction.py - OpenAI APIによる句読点補正
voice_input_tool/text_delivery.py  - カーソル位置への入力
voice_input_tool/native_bridge.py  - ネイティブアプリとのJSONL連携
native/                   - macOS常駐UI・ホットキー・貼り付け
packaging/                - Info.plistとインストールテンプレート
scripts/                  - ローカルビルド・テスト・ランタイム作成
tests/                    - 標準unittestによる回帰テスト
```

開発時の回帰テスト:

```bash
.runtime/VoiceInputTool/.venv-framework/bin/python3 -m unittest discover -s tests -v
bash -n scripts/*.sh start.sh packaging/postinstall.sh.in
```

## パフォーマンス

| 項目 | 結果 |
|------|------|
| ローカルASR処理速度 | 0.06〜0.16秒（10秒程度の音声） |
| モデル読み込み | 初回文字起こし時に遅延実行 |
| 日本語精度 | テスト5問中ほぼ完璧 |
| LLM補正 | 句読点のみ挿入、文章変更なし |

## モデル

- クラウドASR: OpenAI `gpt-transcribe`
- ローカルASR: sherpa-onnx-zipformer-ja-reazonspeech-2024-08-01 (int8)
- 追加の句読点補正: OpenAI `gpt-5.6-luna`（初期設定はOFF）
