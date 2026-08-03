import streamlit as st
import requests
from google import genai
from google.genai import types

# 画面設定
st.set_page_config(page_title="中学生向け 英語添削＆チャット", page_icon="🏫", layout="centered")

# --------------------------------------------------
# 1. パスワード認証機能（先生ごとのAPI・GASを切り替え）
# --------------------------------------------------
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    st.title("🏫 英語添削＆チャット (先生ログイン)")
    input_password = st.text_input("先生用パスワードを入力してください：", type="password")
    
    if st.button("ログイン"):
        teachers = st.secrets.get("teachers", {})
        matched_teacher = None
        
        for key, config in teachers.items():
            if config.get("password") == input_password:
                matched_teacher = config
                break
        
        if matched_teacher:
            st.session_state.authenticated = True
            st.session_state.gemini_api_key = matched_teacher.get("gemini_api_key")
            st.session_state.gas_url = matched_teacher.get("gas_url")
            st.success("ログイン成功！")
            st.rerun()
        else:
            st.error("パスワードが正しくありません。")
    st.stop()

# --------------------------------------------------
# 2. ログイン後の変数セット
# --------------------------------------------------
GAS_URL = st.session_state.gas_url
GEMINI_API_KEY = st.session_state.gemini_api_key

st.title("🏫 英語添削＆チャット")

# ログアウトボタン（サイドバー）
if st.sidebar.button("ログアウト"):
    st.session_state.authenticated = False
    st.rerun()

# Gemini APIクライアントの初期化
client = genai.Client(api_key=GEMINI_API_KEY)

# --------------------------------------------------
# 3. お題データの取得（GAS経由）
# --------------------------------------------------
@st.cache_data(ttl=60)
def load_topics(url):
    try:
        response = requests.get(url)
        data = response.json()
        if isinstance(data, list):
            return data
        return []
    except Exception as e:
        st.error(f"お題の読み込みに失敗しました: {e}")
        return []

topics = load_topics(GAS_URL)

# --------------------------------------------------
# 4. 生徒情報＆お題選択UI（KeyError防止付き）
# --------------------------------------------------
if topics:
    raw_class_list = [t.get('class') for t in topics if isinstance(t, dict) and t.get('class')]
    
    if raw_class_list:
        class_list = sorted(list(set(raw_class_list)))
        selected_class = st.selectbox("あなたのクラスを選んでね：", class_list)
        
        class_topics = [t for t in topics if isinstance(t, dict) and t.get('class') == selected_class]
        topic_options = [t.get('topic', 'お題なし') for t in class_topics]
        
        selected_topic_idx = st.selectbox(
            "本日のお題を選んでね：", 
            range(len(topic_options)), 
            format_func=lambda x: topic_options[x]
        )
        current_topic = class_topics[selected_topic_idx]
        
        st.info(f"🎯 **ターゲット文法:** {current_topic.get('target_grammar', '自由')}")
    else:
        st.warning("スプレッドシートから 'class' データを取得できませんでした。1行目のヘッダー名を確認してください。")
        current_topic = {"topic": "フリートーク", "target_grammar": "自由"}
        selected_class = "不明"
else:
    current_topic = {"topic": "フリートーク", "target_grammar": "自由"}
    selected_class = "不明"

# 出席番号（プルダウン選択：1番〜50番）
student_numbers = [f"{i} 番" for i in range(1, 51)]
selected_student_number = st.selectbox("出席番号を選んでね：", student_numbers)

st.divider()

# --------------------------------------------------
# 5. LINE風チャット画面
# --------------------------------------------------
if "messages" not in st.session_state:
    st.session_state.messages = []

# 過去の会話ログを表示
for message in st.session_state.messages:
    avatar = "🧑‍🎓" if message["role"] == "user" else "🤖"
    with st.chat_message(message["role"], avatar=avatar):
        st.markdown(message["content"])

# 会話開始前（ログが0件の時）のみアップロード枠を表示
uploaded_file = None
if len(st.session_state.messages) == 0:
    uploaded_file = st.file_uploader(
        "📷 ノートの写真やPDFを添付できるよ（任意）", 
        type=["jpg", "jpeg", "png", "pdf"]
    )

# チャット入力欄
if user_input := st.chat_input("英語でメッセージを入力してね（写真添付時は空欄でもOK）..."):
    
    # ユーザー入力を画面に表示
    with st.chat_message("user", avatar="🧑‍🎓"):
        if uploaded_file:
            st.caption(f"📎 添付ファイル: {uploaded_file.name}")
        st.markdown(user_input if user_input else "（画像を送信しました）")

    contents_payload = []

    # 画像・PDFが存在する場合はペイロードに追加
    if uploaded_file:
        file_bytes = uploaded_file.read()
        mime_type = uploaded_file.type
        contents_payload.append({
            "mime_type": mime_type,
            "data": file_bytes
        })

    text_prompt = user_input if user_input else "添付されたファイルの手書き英文を読み取って添削し、返答してください。"
    contents_payload.append(text_prompt)

    # システムプロンプト（和訳なし・ネイティブの友達設定）
    system_instruction = f"""
    あなたは日本の中学校の英語の先生であり、話しかけやすいフレンドリーなネイティブのお友達です。
    
    【現在の学習お題】: {current_topic.get('topic', 'フリートーク')}
    【意識させるターゲット文法】: {current_topic.get('target_grammar', '自由')}

    生徒から画像やPDF、または英文テキストが送られてきたら、以下のフォーマット厳守で返答してください。

    ### 📖 あなたが書いた英文（読み取り結果）
    * 画像やPDFが添付されている場合のみ表示。読み取った英文をここに正確に書き出してください（誤りがあってもそのまま書き出す）。

    ### 📝 添削とアドバイス
    * **評価:** 最初に生徒をしっかり褒めるコメント（絵文字を使って明るく）
    * **ポイント:** 文法やスペルの修正アドバイス。ターゲット文法（{current_topic.get('target_grammar', '自由')}）が正しく使えているかもチェック。中学生にわかりやすい日本語で短く解説する。

    ---
    ### 💬 返事
    生徒の発言に対する返答を英語で1〜2文で書く。
    最後に会話が続くような簡単で答えやすい質問を1つ入れる（中学生レベルの英単語・文法のみ使用）。
    ※日本語訳（和訳）は一切添えず、英語のみで出力してください。
    """

    # AIからの返答を取得・表示
    with st.chat_message("assistant", avatar="🤖"):
        with st.spinner("AI先生が考え中..."):
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=contents_payload,
                config=genai.types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    temperature=0.7,
                ),
            )
            bot_response = response.text
            st.markdown(bot_response)

    # 送信されたテキスト表記の整理
    display_user_msg = f"[ファイル添付] {user_input}" if uploaded_file else user_input

    # ログインした先生個人のスプレッドシート（GAS）へログ送信
    try:
        log_data = {
            "className": selected_class,
            "studentNumber": selected_student_number,
            "topic": current_topic.get('topic', 'フリートーク'),
            "userInput": display_user_msg,
            "botResponse": bot_response
        }
        requests.post(GAS_URL, json=log_data, timeout=5)
    except Exception as e:
        pass

    # 会話履歴に追加保存
    st.session_state.messages.append({"role": "user", "content": display_user_msg})
    st.session_state.messages.append({"role": "assistant", "content": bot_response})
    
    # 画面を再描画して状態を確定
    st.rerun()

# --------------------------------------------------
# 6. チャット入力欄の下に固定表示するフッター（著作権表記）
# --------------------------------------------------
footer_css = """
<style>
    .stChatInputContainer {
        bottom: 25px !important;
    }
    .custom-footer {
        position: fixed;
        bottom: 0;
        left: 0;
        width: 100%;
        background-color: transparent;
        color: #888888;
        text-align: center;
        font-size: 0.75rem;
        padding: 4px 0;
        z-index: 999999;
        pointer-events: none;
    }
</style>
<div class="custom-footer">
    © 2026 English Chat App. All Rights Reserved.
</div>
"""
st.markdown(footer_css, unsafe_allow_html=True)
