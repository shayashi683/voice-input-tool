# Voice Input Tool

ReazonSpeech (sherpa-onnx) + LLM整形（OpenRouter/Cerebras GPT OSS または ローカルOllama）による
macOS 向けローカル音声入力ツールです。

## 特徴

- 完全ローカルASR（ReazonSpeech K2 v2 int8）
- マイク入力を事前準備し、録音開始直後の頭欠けを抑制
- 録音は明示的に停止するまで継続（無音では停止しない）
- 録音開始時に入力欄（キャレット）の近くへ小さなプレビューパネルが出る
- VADが発話の区切りを検出するたびに文字起こしし、パネルへリアルタイムに追記
- 録音停止後に、全文をまとめてLLMで整形してパネルの内容を置き換える（デフォルト: ON）
- パネルの内容は編集でき、Enter で元の入力欄へ入力、Esc で取消（本文はクリップボードに残る）
- ASR/VADモデルは起動直後にバックグラウンドで事前読み込みし、初回録音時の頭欠けを防ぐ
- 発話ごとのLLM補正もオン/オフ切替可能（デフォルト: ON）
- LLM補正ON時は、必ずLLM補正後のテキストだけを出力（未補正フォールバックなし）
- 整形バックエンドを OpenRouter（クラウド）と Ollama（ローカル）から選択可能
- OpenRouter 経由で Cerebras の GPT OSS を使用
  - model: `openai/gpt-oss-120b`
  - provider: `Cerebras`
  - provider fallback: 無効
  - `data_collection: deny` / `zdr: true`
- Ollama バックエンドではテキストを外部に送らず、ローカルモデルだけで整形
- macOS メニューバーアプリとして常駐
- Raycast から起動/終了可能（Script Command 同梱）
- ネイティブ設定画面（PyObjC）でGUIから設定変更可能
- グローバルホットキー対応（設定画面でカスタマイズ可能）
- 確定した本文はカーソル位置に入力（貼り付け不可時は本文をクリップボードに保持）
- 詳細ログを `logs/` に出力

## 社員向けセットアップ（初回）

この手順は、他の社員が自分のMacで最短で使い始めるためのものです。

> 重要: 現在の既定設定では、ツール本体・設定・モデルを `~/voice-input-tool` に置く前提です。別の場所に置く場合は、`voice_input.py` / `voice_input_tool/config.py` のパス設定も変更してください。

### 0. 事前に必要なもの

- macOS
- Python 3.11 推奨（3.10以上でも動作想定）
- ターミナル操作権限
- 空き容量 2GB 以上（ASRモデルが約1.4GB）
- LLM補正を使う場合: OpenRouter API Key、または Ollama（ローカル実行）

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

### 4. OpenRouter API Keyを設定する（LLM補正を使う場合）

LLM補正を使う場合は、`.env` に `OPENROUTER_API_KEY` を保存します。`.env` はGit管理対象外です。

```bash
cd ~/voice-input-tool
cat > .env <<'EOF'
OPENROUTER_API_KEY=sk-or-v1-xxxxx
EOF
chmod 600 .env
```

`sk-or-v1-xxxxx` は各自のAPI Keyに置き換えてください。設定画面からAPI Keyを入力することもできます。その場合も `.env` に保存され、`config.json` には保存されません。

LLM補正を使わない場合は、起動後にメニューバーの `LLM補正: ON/OFF` でOFFにしてください。API Key未設定のままLLM補正ONで使うと、補正に失敗します（その区間は補正前の認識結果のまま表示されます）。

API Keyを使わずローカルで整形したい場合は、次の「Ollamaで整形する」を参照してください。

### 4b. Ollamaで整形する（API Key不要）

OpenRouterの代わりに、ローカルの [Ollama](https://ollama.com) で整形できます。テキストが外部に出ないため、社外に出せない内容の入力にも使えます。

```bash
brew install ollama
brew services start ollama      # または: ollama serve
ollama pull qwen3:8b            # 好みのモデルを取得
```

設定画面の「整形バックエンド」で `Ollama（ローカル）` を選び、「Ollama モデル」で取得済みモデルを選択して保存します（サーバーURLの既定は `http://127.0.0.1:11434/v1`）。

`config.json` から直接指定することもできます。

```json
{
  "use_llm": true,
  "llm_backend": "ollama",
  "ollama_model": "qwen3:8b"
}
```

動作確認は次のコマンドで行えます。

```bash
./start.sh --test --llm --llm-backend ollama
```

ローカルモデルはクラウドより遅いため、タイムアウトは既定60秒です（`ollama_timeout`）。`<think>` 形式の思考出力を返すモデルでも、思考部分は自動的に取り除いて入力します。

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
3. カーソルの近くに「🎙 話してください」のパネルが出ることを確認して話す
4. 話している間、パネルが「● 聞き取り中…」になり、区切りごとに認識結果が追記されることを確認
   （メニューバーも三つの白い丸の入力中アイコンに変わる）
5. もう一度 `Ctrl+Shift+Space` を押す、またはメニューから停止する
6. 文章整形がONなら「✨ 文章を整形中…」の後、パネルの内容が整形結果に置き換わり「✅ 確認して Enter」になる
7. 内容を確認（必要なら編集）して `Enter` を押すと、元の入力欄にまとめて入力される。
   `Esc` で取消（本文はクリップボードに残る）、`Shift+Enter` でパネル内に改行

マイクを使わずにパネルの動きだけ確かめたい場合は、入力したいアプリを前面にしてから次を実行します
（3 秒後にテスト音源で録音〜確認待ちまで自動で進みます。ホットキーは登録しないので常駐中のアプリと共存できます）。

```bash
python voice_input.py --demo
```

### 9. ダブルクリックで起動する

同梱のネイティブアプリを `~/Applications/VoiceInputTool.app` に配置すると、Finderからダブルクリックで起動できます。デスクトップにある `VoiceInputTool.app` は、このアプリへのショートカットです。

ログイン時に自動起動したい場合は、macOSの「システム設定」→「一般」→「ログイン項目」に `VoiceInputTool.app` を追加してください。

### 9b. Raycastから起動する

`raycast/` に Raycast の Script Command を同梱しています。

| コマンド | ファイル | 動作 |
|---------|---------|------|
| `Voice Input Tool を起動` | `raycast/voice-input-start.sh` | 未起動なら起動。起動済みなら何もしない |
| `Voice Input Tool を終了` | `raycast/voice-input-quit.sh` | 常駐プロセスを SIGTERM で終了 |

登録手順:

1. Raycast を開き `⌘,` で設定を開く
2. `Extensions` タブ → 左下の `+` → `Add Script Directory`
3. このリポジトリの `raycast` フォルダを選択

登録すると Raycast の検索窓から `Voice Input Tool を起動` で起動できます。よく使う場合は、コマンドを選んで `Record Hotkey` でホットキー、`Alias` で短縮名（例: `vi`）を割り当てられます。

起動スクリプトは次の順で起動方法を選びます。

1. `~/Applications/VoiceInputTool.app` があれば `open` で起動（マイク/アクセシビリティ権限がアプリ自身に紐づくため、こちらが推奨）
2. 無ければ `start.sh` を Raycast のプロセスから切り離して起動（標準出力は `logs/raycast-start.log`）

> 注意: 2. の場合、マイク・アクセシビリティ・入力監視の権限は起動元の `Raycast` に紐づきます。初回は権限ダイアログで `Raycast` を許可してください。権限をアプリ側に持たせたい場合は `~/Applications/VoiceInputTool.app` を配置してから使ってください。

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
| LLM補正（発話ごと）: ON/OFF | 発話の区切りごとの句読点補正を切替 |
| 文章整形（停止後）: ON/OFF | 録音停止後の全文整形を切替 |
| アクセシビリティ許可を確認 | 自動入力に必要なmacOS権限を確認 |
| 設定... | 設定画面を開く |
| 終了 | アプリを終了 |

### CLIから使う

```bash
cd ~/voice-input-tool
source .venv-framework/bin/activate

# メニューバーアプリとして起動
./start.sh

# 停止後の全文整形を一時的に無効化して起動
./start.sh --no-final-polish

# 設定でOFFにしている全文整形を、一時的に有効にして起動
./start.sh --final-polish

# テストモード（WAVファイルでASR動作確認）
python voice_input.py --test --no-llm

# デモモード（マイク・ホットキーを使わず、テスト音源でプレビューパネルの流れを再現）
python voice_input.py --demo
```

### ホットキー

デフォルト: `Ctrl+Shift+Space` で録音開始/停止。設定画面で変更できます。

## 入力フロー

1. ホットキー/メニューで録音開始。前面アプリを入力先として覚え、その入力欄（キャレット）の
   近くにプレビューパネルを表示する（キャレット位置が取れないアプリでは入力欄・ウィンドウ・
   マウス位置の順にフォールバック）
2. VADが発話の区切りを検出するたびに、その区間をASRでテキスト化
3. LLM補正（発話ごと）がONなら、区間ごとに句読点を補正
4. 認識結果をパネルへ追記していく（この時点では入力欄には何も打ち込まない）
5. ホットキー/メニューで録音停止。パネルが編集可能になり、Enter/Esc を受け付ける
6. 文章整形（停止後）がONなら、全文をLLMでまとめて整形し、パネルの内容を整形結果で置き換える
   （整形を待っている間に自分で編集や Enter をした場合、整形結果は捨てられる）
7. `Enter` でパネルの本文（編集後の内容）を入力先アプリのカーソル位置へ入力。
   `Esc` で取消（本文はクリップボードにコピーされる）。`Shift+Enter` はパネル内の改行

重要な仕様:

- 録音中は入力欄に直接打ち込みません。入力は Enter で確定したときにまとめて行います。
- LLM補正ON時は、まず補正前の認識結果をそのままパネルへ追記し、
  補正が終わった区間から補正結果へ上書きしていきます。補正に失敗した区間は生のまま残ります。
- 全文整形に失敗した場合は、整形前の本文のまま確認待ちになります。
- 確認待ちの間はパネル以外のキー操作は普段どおり使えます。別のウィンドウをクリックすると
  パネルはキー入力を受け取らなくなりますが、表示は残ります。次の録音を始めると前の本文は
  クリップボードへコピーされ、パネルは新しい録音に使われます。
- 貼り付けに失敗した場合でも、本文はクリップボードに残ります。
- 改行は Shift+Return のキーイベントとして入力するため、Slack 等のチャットで送信になりません。

### 停止後の全文整形について

発話の区切りごとに補正する方式では、LLMが文の断片しか見られないため、
全体としての読みやすさを整えられません。そこで録音停止後に、その回の全文を
もう一度LLMへ渡し、フィラー除去・句読点・誤変換の修正をまとめて行います。

整形結果は入力欄ではなくパネルの本文を置き換えるだけなので、入力欄側のテキストを
削除したり打ち直したりすることはありません。整形結果に納得できなければ、
Enter を押す前にパネル上で編集するか、Esc で取り消してください。

## 状態表示

パネルのヘッダーとメニューバーアイコンは状態に応じて変わります。

| メニューバー | パネル | 状態 |
|---------|------|------|
| 🎙 | （非表示） | 停止中 |
| ⏳ | ⏳ マイク起動中… | マイク起動中 |
| 🟢 | 🎙 話してください | 入力待機中 |
| ![入力中](assets/typing-indicator.svg) | ● 聞き取り中… | 音声入力中（ドットが脈動） |
| 📝 | 📝 認識中… | 音声認識中 |
| 🧠 | 🧠 補正中… | LLM補正中（発話ごと） |
| ✨ | ✨ 文章を整形中… | 文章を整形中（録音停止後）。Enter でそのまま入力も可 |
| ✅ | ✅ 確認して Enter | 確認待ち（Enter で入力 ／ Shift+Enter で改行 ／ Esc で取消） |
| ⌨️ | （非表示） | カーソル位置へ入力中 |

入力待機中から音声入力中への切り替えは、マイク入力の音量とノイズ床をもとに判定します。音声入力中は三つの白い丸が順番に跳ねるインジケーターを表示します。

## 設定画面

メニューバーの 🎙 →「設定...」で開きます。

| 設定項目 | 説明 | デフォルト |
|---------|------|-----------|
| LLM 句読点補正 | 発話ごとの補正のON/OFF切替 | ON |
| 文章整形 | 録音停止後に全文を整形してパネルの内容を置き換えるかの切替 | ON |
| 整形バックエンド | OpenRouter（クラウド）/ Ollama（ローカル） | OpenRouter |
| OpenRouter API Key | LLM補正用APIキー | `.env` から読み込み |
| Ollama サーバー | Ollama の OpenAI 互換エンドポイント | `http://127.0.0.1:11434/v1` |
| Ollama モデル | 取得済みモデルから選択（直接入力も可） | `qwen3:8b` |
| 録音 開始/停止 | ホットキー設定 | Ctrl+Shift+Space |
| 入力マイク | 使用するマイク。未選択時はシステムの既定入力 | 自動選択 |
| LLM 補正プロンプト（発話ごと） | 区間ごとの補正で使うシステムプロンプト | 句読点挿入のみ |
| 文章整形プロンプト（録音停止後） | 全文整形で使うシステムプロンプト | フィラー除去・句読点・誤変換修正 |

設定は `~/voice-input-tool/config.json` に保存されます。API Key は `.env` に保存され、`config.json` には保存されません。

## LLM設定

既定値は `voice_input_tool/config.py` の `DEFAULTS` で管理しています。

| キー | 既定値 | 説明 |
|------|--------|------|
| `llm_backend` | `openrouter` | 整形バックエンド。`openrouter` / `ollama` |
| `llm_model` | `openai/gpt-oss-120b` | OpenRouter のモデル名 |
| `llm_provider_order` | `["Cerebras"]` | OpenRouter で使用する provider |
| `ollama_base_url` | `http://127.0.0.1:11434/v1` | Ollama の OpenAI 互換エンドポイント。`/v1` は自動補完 |
| `ollama_model` | `qwen3:8b` | Ollama のモデル名 |
| `ollama_timeout` | `60.0` | Ollama へのリクエストタイムアウト（秒） |

OpenRouterへのリクエストでは provider fallback を無効にしています。Cerebras が利用できない場合、LLM補正は失敗します。補正に失敗した区間は補正前の認識結果のまま残ります。

バックエンドは起動時に `--llm-backend openrouter|ollama` で一時的に上書きできます（`config.json` は変更しません）。

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
| `LLM補正リクエスト` | OpenRouterへ送信開始 |
| `LLM応答` | OpenRouterから応答あり |
| `content_len=0` / `content=None` | LLM本文が空。LLM補正失敗扱い |
| `finish_reason=length` | reasoning等で token を使い切った可能性 |
| `入力位置: caret ...` / `element` / `window` / `mouse` | パネルの配置に使った位置の種類 |
| `プレビューパネル: 確定` / `取消` | Enter / Esc が押された |
| `カーソル位置に入力しました` | 入力成功 |
| `貼り付け不可のためコピーしました` | 本文をクリップボードに保持 |

ログでは API Key をマスクします。

初期セットアップで詰まりやすい点:

| 症状 | 確認すること |
|------|--------------|
| `ModuleNotFoundError` が出る | `cd ~/voice-input-tool` → `source .venv-framework/bin/activate` → `python -m pip install -r requirements.txt` を再実行 |
| `モデルファイルが見つかりません` と出る | `models/` 配下に ASRモデル展開済みフォルダがあるか確認 |
| メニューバーに何も出ない | `~/Applications/VoiceInputTool.app` を起動しているか確認。`logs/native-status.log` と `logs/native-engine.err.log` も確認 |
| マイク入力できない | macOSの「マイク」権限で、起動に使っているアプリを許可して再起動 |
| `Ctrl+Shift+Space` が効かない | 「アクセシビリティ」と、必要に応じて「入力監視」を許可して再起動。別アプリのショートカットと衝突していないかも確認 |
| カーソル位置に入力されない | 「アクセシビリティ」権限を確認。貼り付け不可のアプリではクリップボードに残ります |
| パネルが入力欄から離れた場所に出る | キャレット位置を Accessibility API で公開しないアプリ（Electron 系など）では、入力欄・ウィンドウ・マウス位置の順にフォールバックします。ログの `入力位置:` 行で何が使われたか確認できます |
| Enter を押してもパネルが反応しない | 確認待ち中に別のウィンドウをクリックするとパネルはキー入力を受け取りません。もう一度録音を始めるか、Esc の代わりにクリップボードから貼り付けてください（本文は次の録音開始時にコピーされます） |
| LLM補正で出力されない（OpenRouter） | `.env` の `OPENROUTER_API_KEY`、OpenRouterの残高、Cerebras providerの利用可否を確認。急ぎの場合はメニューでLLM補正をOFF |
| LLM補正で出力されない（Ollama） | `ollama serve` が動いているか、`ollama list` に設定したモデルがあるかを確認。`curl http://127.0.0.1:11434/v1/models` で疎通確認。遅い場合は `ollama_timeout` を延長 |
| 設定画面の「Ollama モデル」が空 | Ollama未起動またはURL誤り。起動後にバックエンドを再選択すると再取得します（モデル名の直接入力も可） |
| 停止後に整形されない | メニューの `文章整形（停止後）` がONか確認。整形を待たずに Enter を押すと整形前の本文が入力されます。詳細は「停止後の全文整形について」を参照 |
| Raycastにコマンドが出てこない | `raycast` フォルダを Script Directory に登録済みか、`chmod +x raycast/*.sh` されているか確認 |
| Raycastから起動するとマイク/ホットキーが使えない | `~/Applications/VoiceInputTool.app` が無い場合、権限は起動元の `Raycast` に紐づきます。システム設定でRaycastを許可するか、`.app` を配置してください |

## ファイル構成

```
voice_input.py       - CLIエントリーポイント（互換用ラッパー）
voice_input_tool/    - Python実装（engine/ASR/LLM/設定UI/ネイティブ連携）
native/              - macOSネイティブ常駐アプリ/ランチャーのソース
packaging/           - VoiceInputTool.app 用 Info.plist
launch-agents/       - 自動起動用 LaunchAgent サンプル
raycast/             - Raycast Script Command（起動/終了）
assets/              - メニューバー用アイコン素材
start.sh             - CLI起動用シェルスクリプト
requirements.txt     - Python依存パッケージ一覧
models/              - ASRモデル（.gitignore済み）
logs/                - ログファイル（.gitignore済み）
config.json          - ユーザー設定（.gitignore済み）
.env                 - 環境変数（.gitignore済み）
```

## パフォーマンス

| 項目 | 結果 |
|------|------|
| ASR処理速度 | 0.06〜0.16秒（10秒程度の音声） |
| モデル読み込み | 起動直後にバックグラウンドで事前読み込み |
| 日本語精度 | テスト5問中ほぼ完璧 |
| LLM補正（発話ごと） | 句読点のみ挿入、文章変更なし |
| 文章整形（停止後） | フィラー除去・句読点・誤変換修正。内容は変えない。結果はパネルで確認してから入力 |

## モデル

- ASR: sherpa-onnx-zipformer-ja-reazonspeech-2024-08-01 (int8)
- LLM（OpenRouter）: openai/gpt-oss-120b (Cerebras固定・fallback無効)
- LLM（Ollama）: 任意のローカルモデル（既定 `qwen3:8b`）
