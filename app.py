import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
from google import genai
from google.genai import types
import datetime
import re
import pandas as pd

st.set_page_config(page_title="Talky AI 2.0", page_icon="🏫", layout="wide")

# --------------------------------------------------
# サービスアカウント認証初期化
# --------------------------------------------------
@st.cache_resource
def get_gspread_client():
    creds_dict = st.secrets["gcp"]["gcp_service_account"]
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    return gspread.authorize(creds)

gc = get_gspread_client()
master_sheet_id = st.secrets["sheets"]["master_sheet_id"]

@st.cache_data(ttl=300)
def get_schools_list():
    master_book = gc.open_by_key(master_sheet_id)
    return master_book.worksheet("学校管理シート").get_all_records()

# --------------------------------------------------
# 1. ログイン認証画面 (Usersシートを全学校から走査)
# --------------------------------------------------
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    st.title("🏫 Talky AI 2.0 ログイン")
    input_id = st.text_input("ID（教師ID または 生徒ID）：")
    input_pass = st.text_input("パスワード：", type="password")

    if st.button("ログイン"):
        with st.spinner("ログイン中..."):
            try:
                schools_list = get_schools_list()

                matched_user = None
                matched_school = None
                matched_school_id = None

                for school in schools_list:
                    s_name = school.get("SchoolName")
                    s_id = school.get("SheetID")
                    try:
                        school_book = gc.open_by_key(s_id)
                        users = school_book.worksheet("Users").get_all_records()

                        user = next(
                            (u for u in users
                             if str(u.get("ID")) == str(input_id)
                             and str(u.get("Password")) == str(input_pass)),
                            None
                        )
                        if user:
                            matched_user = user
                            matched_school = s_name
                            matched_school_id = s_id
                            break
                    except Exception:
                        continue

                if matched_user:
                    st.session_state.authenticated = True
                    st.session_state.role = str(matched_user.get("Role", "")).strip().lower()
                    st.session_state.user_id = str(matched_user.get("ID", "")).strip()
                    st.session_state.user_name = str(matched_user.get("Name", ""))
                    st.session_state.school_name = matched_school
                    st.session_state.school_sheet_id = matched_school_id

                    if st.session_state.role == "student":
                        st.session_state.class_name = str(matched_user.get("Class", ""))
                        st.session_state.student_number = matched_user.get("StudentNumber", "")
                    else:
                        st.session_state.class_name = ""
                        st.session_state.student_number = ""

                    st.success("ログイン成功！")
                    st.rerun()
                else:
                    st.error("IDまたはパスワードが正しくありません。")

            except Exception as e:
                st.error(f"認証中にエラーが発生しました: {e}")
    st.stop()

school_param = st.session_state.get('school_name')
school_sheet_id = st.session_state.get('school_sheet_id')

def get_school_sheet(sheet_name):
    return gc.open_by_key(school_sheet_id).worksheet(sheet_name)

# --------------------------------------------------
# クラス別ログシート関連のヘルパー関数
# --------------------------------------------------
def sanitize_sheet_name(name: str) -> str:
    """Googleスプレッドシートのシート名に使えない文字を除去し、Logs_プレフィックスを付与する"""
    if not name:
        name = "未設定"
    # シート名に使用できない文字: [ ] * ? / \ :
    cleaned = re.sub(r"[\[\]\*\?/\\:]", "_", str(name)).strip()
    sheet_name = f"Logs_{cleaned}"
    return sheet_name[:100]  # シート名は100文字まで

def get_or_create_class_log_sheet(class_name: str):
    """クラスごとのログシートを取得。存在しなければヘッダー付きで新規作成する。"""
    book = gc.open_by_key(school_sheet_id)
    sheet_name = sanitize_sheet_name(class_name)
    try:
        return book.worksheet(sheet_name)
    except gspread.exceptions.WorksheetNotFound:
        sheet = book.add_worksheet(title=sheet_name, rows=1000, cols=8)
        sheet.append_row(["timestamp", "className", "studentNumber", "name", "topic", "userInput", "botResponse", "memo"])
        return sheet

def load_all_class_logs():
    """school_sheet_id内の Logs_ から始まる全シートを横断して読み込み、1つのリストにまとめる"""
    book = gc.open_by_key(school_sheet_id)
    combined = []
    for ws in book.worksheets():
        if ws.title.startswith("Logs_"):
            try:
                combined.extend(ws.get_all_records())
            except Exception:
                continue
    return combined

# --------------------------------------------------
# お題データ・ユーザーデータの取得と教師IDの判定
# --------------------------------------------------
try:
    all_users = get_school_sheet("Users").get_all_records()
    all_topics = get_school_sheet("Topics").get_all_records()
except Exception:
    all_users = []
    all_topics = []

assigned_teacher_id = "default"
my_class_topics = []

if st.session_state.get("role") == "student":
    student_data = next(
        (u for u in all_users
         if str(u.get("ID", "")).strip() == str(st.session_state.get("user_id")).strip()),
        None
    )
    assigned_teacher_id = str(student_data.get("TeacherID", "default")).strip() if student_data else "default"
    st.session_state.assigned_teacher_id = assigned_teacher_id

    my_class = st.session_state.get("class_name")
    my_class_topics = [t for t in all_topics if str(t.get("Class", "")).strip() == str(my_class).strip()]
    topic_titles = [t.get("Topic") for t in my_class_topics] if my_class_topics else ["フリートーク"]

else:
    assigned_teacher_id = str(st.session_state.get("user_id", "")).strip()
    st.session_state.assigned_teacher_id = assigned_teacher_id
    topic_titles = sorted(set([t.get("Topic") for t in all_topics if t.get("Topic")])) if all_topics else ["フリートーク"]
    my_class_topics = all_topics

# --------------------------------------------------
# APIキーの準備（Geminiクライアントは実際に使う直前で生成する）
# --------------------------------------------------
def build_gemini_client(teacher_id: str):
    """teacher_id に紐づくAPIキーでクライアントを生成。無ければ共通キーにフォールバック。"""
    teachers_sec = st.secrets.get("teachers", {})
    key = teachers_sec.get(teacher_id, {}).get("gemini_api_key", "")
    if not key:
        key = st.secrets.get("DEFAULT_API_KEY", "")
    if not key:
        return None, ""
    return genai.Client(api_key=key), key

tid = st.session_state.get("assigned_teacher_id", "default")
gemini_client, active_api_key = build_gemini_client(tid)

if active_api_key:
    debug_info = f"OK（教師ID: {tid} のキーを使用）"
else:
    debug_info = "⚠️ APIキー未設定（管理者に連絡してください）"

# --------------------------------------------------
# サイドバー情報表示
# --------------------------------------------------
st.sidebar.title("📌 アカウント情報")
st.sidebar.write(f"**学校:** {school_param}")
if st.session_state.get('role') == 'student':
    st.sidebar.write(f"**クラス:** {st.session_state.get('class_name')}")
    st.sidebar.write(f"**名簿番号:** {st.session_state.get('student_number')}番")
st.sidebar.write(f"**氏名:** {st.session_state.get('user_name')}")
st.sidebar.info(f"🔑 **ID:** `{assigned_teacher_id}`")
st.sidebar.warning(f"🔧 **API状態:** {debug_info}")

student_level = "レベル2：英語が苦手な生徒・添削即時に"
if st.session_state.get('role') == 'student':
    st.sidebar.markdown("---")
    st.sidebar.title("⚙️ 学習設定")
    student_level = st.sidebar.selectbox(
        "AIサポートレベルを選択：",
        (
            "レベル1：英語がすごい苦手な生徒・添削を即時に",
            "レベル2：英語が苦手な生徒・添削即時に",
            "レベル3：英語が普通な生徒・添削は終わりと言われた時にまとめて",
            "レベル4：英語が得意な生徒・添削は終わりと言われた時にまとめて"
        )
    )
    # --- デバッグ用（原因特定できたら削除してください）---
st.sidebar.markdown("---")
st.sidebar.markdown("### 🔍 デバッグ情報")
st.sidebar.code(f"tid = {repr(tid)}")
st.sidebar.code(f"利用可能なteacher keys = {list(st.secrets.get('teachers', {}).keys())}")
st.sidebar.code(f"tid が一致するか = {tid in st.secrets.get('teachers', {})}")

if st.sidebar.button("ログアウト"):
    st.session_state.authenticated = False
    st.rerun()

st.sidebar.markdown("---")
st.sidebar.markdown(
    "<p style='text-align: center; color: grey; font-size: small;'>© 2026 Talky AI 2.0 All Rights Reserved.</p>",
    unsafe_allow_html=True
)

# --------------------------------------------------
# 2. 教師画面
# --------------------------------------------------
if st.session_state.get("role") == "teacher":
    st.title(f"👩‍🏫 Talky AI 2.0 教師用ダッシュボード ({school_param} / {st.session_state.get('user_name')}先生)")

    tab1, tab2, tab3 = st.tabs(["📊 クラス別会話ログ", "📝 お題の管理", "📷 紙媒体の英作文評価"])

    with tab1:
        st.subheader("生徒の会話ログ・やり取り確認 ＆ 総合評価")
        try:
            logs = load_all_class_logs()
            if logs:
                df_logs = pd.DataFrame(logs)
                date_col = next((col for col in df_logs.columns if "time" in col.lower() or "date" in col.lower() or "日時" in col or "日付" in col), None)

                if date_col:
                    df_logs["date"] = pd.to_datetime(df_logs[date_col], errors="coerce").dt.date

                    col_f1, col_f2, col_f3, col_f4 = st.columns(4)
                    with col_f1:
                        use_date_filter = st.checkbox("📅 日付で絞り込む", value=False)
                        selected_date = st.date_input("日付を選択", value=datetime.date.today(), disabled=not use_date_filter)
                    with col_f2:
                        class_col = next((c for c in df_logs.columns if "class" in c.lower() or "クラス" in c), None)
                        class_list = sorted(df_logs[class_col].dropna().unique().tolist()) if class_col else []
                        selected_class = st.selectbox("🏫 クラスで絞り込み", ["すべて表示"] + class_list) if class_list else "すべて表示"
                    with col_f3:
                        name_col = next((c for c in df_logs.columns if "name" in c.lower() or "氏名" in c or "名前" in c), None)
                        student_list = sorted(df_logs[name_col].dropna().unique().tolist()) if name_col else []
                        selected_student = st.selectbox("👤 生徒で絞り込み", ["すべて表示"] + student_list) if student_list else "すべて表示"
                    with col_f4:
                        selected_eval = st.selectbox("🏆 総合評価で絞り込み", ["すべて表示", "評価: A", "評価: B", "評価: C"])

                    positive_keywords = ["完璧", "素晴らしい", "great job", "excellent", "well done"]
                    improve_keywords = ["もう少し", "改善", "気をつけ", "try again", "let's improve"]

                    eval_results = []
                    for _, row in df_logs.iterrows():
                        bot_text = str(row.get("botResponse", ""))
                        user_text = str(row.get("userInput", ""))
                        combined = (bot_text + user_text).lower()

                        if any(k.lower() in combined for k in positive_keywords):
                            eval_results.append("A")
                        elif any(k.lower() in combined for k in improve_keywords):
                            eval_results.append("B")
                        else:
                            eval_results.append("B" if len(user_text) > 10 else "C")

                    df_logs["evaluation"] = eval_results
                    filtered_df = df_logs.copy()

                    if use_date_filter:
                        filtered_df = filtered_df[filtered_df["date"] == selected_date]
                    if selected_class != "すべて表示" and class_col:
                        filtered_df = filtered_df[filtered_df[class_col] == selected_class]
                    if selected_student != "すべて表示" and name_col:
                        filtered_df = filtered_df[filtered_df[name_col] == selected_student]
                    if selected_eval != "すべて表示":
                        target_grade = selected_eval.replace("評価: ", "")
                        filtered_df = filtered_df[filtered_df["evaluation"] == target_grade]

                    st.markdown("---")
                    if not filtered_df.empty:
                        for idx, row in filtered_df.iterrows():
                            grade = row.get("evaluation", "B")
                            badge_color = "🟢" if grade == "A" else ("🟡" if grade == "B" else "🔴")
                            with st.expander(f"{badge_color} 【評価: {grade}】 {row.get(date_col, '')} | クラス: {row.get('className', '')} | 氏名: {row.get('name', '')} (お題: {row.get('topic', '')})"):
                                st.markdown(f"**🏆 総合評価:** `ランク {grade}`")
                                st.markdown(f"**🧑‍🎓 生徒の発話:**\n> {row.get('userInput', '')}")
                                st.markdown(f"**🤖 AIの返答・フィードバック:**\n{row.get('botResponse', '')}")
                        st.markdown("---")
                        st.dataframe(filtered_df.drop(columns=["date"], errors="ignore"))
                    else:
                        st.info("⚠️ 条件に一致するログはありません。")
                else:
                    st.dataframe(df_logs)
            else:
                st.info("まだログが保存されていません。")
        except Exception as e:
            st.warning(f"ログの取得に失敗しました: {e}")

    with tab2:
        st.subheader("お題の管理 (Topicsシート)")
        if all_topics:
            st.dataframe(all_topics)
        else:
            st.info("お題データがありません。")

        st.markdown("---")
        tab_add, tab_edit = st.tabs(["➕ 新規お題の追加", "✏️ 既存お題の上書き編集"])

        with tab_add:
            with st.form("topic_form_add"):
                t_class = st.text_input("対象クラス（例: 1年2組）")
                t_topic = st.text_input("お題タイトル")
                t_grammar = st.text_input("ターゲット文法")
                if st.form_submit_button("お題を登録する"):
                    try:
                        sheet = get_school_sheet("Topics")
                        sheet.append_row([t_class, t_topic, t_grammar, st.session_state.user_id])
                        st.success("お題を追加しました！")
                        st.rerun()
                    except Exception as e:
                        st.error(f"追加に失敗しました: {e}")

        with tab_edit:
            if all_topics:
                topic_options = [f"クラス: {t.get('Class')} / お題: {t.get('Topic')}" for t in all_topics]
                selected_topic_str = st.selectbox("編集するお題を選択", topic_options)
                selected_idx = topic_options.index(selected_topic_str)
                target_topic_data = all_topics[selected_idx]

                with st.form("topic_form_edit"):
                    edit_class = st.text_input("対象クラス", value=target_topic_data.get("Class", ""))
                    edit_topic = st.text_input("お題タイトル", value=target_topic_data.get("Topic", ""))
                    edit_grammar = st.text_input("ターゲット文法", value=target_topic_data.get("TargetGrammar", ""))

                    if st.form_submit_button("変更を上書き保存する"):
                        try:
                            sheet = get_school_sheet("Topics")
                            row_idx = selected_idx + 2
                            sheet.update(range_name=f"A{row_idx}:D{row_idx}", values=[[edit_class, edit_topic, edit_grammar, st.session_state.user_id]])
                            st.success("お題を上書き保存しました！")
                            st.rerun()
                        except Exception as e:
                            st.error(f"更新に失敗しました: {e}")
            else:
                st.info("編集できるお題がありません。")

    with tab3:
        st.subheader("📷 紙媒体の英作文一括評価")
        uploaded_paper = st.file_uploader("ノートやプリントの画像をアップロード", type=["jpg", "jpeg", "png", "pdf"])
        paper_topic = st.text_input("お題・テーマ", value="自由記述")

        if uploaded_paper and st.button("AIで添削・評価する"):
            if gemini_client is None:
                st.error("APIキーが設定されていないため実行できません。管理者に連絡してください。")
            else:
                with st.spinner("AIが解析中..."):
                    response = gemini_client.models.generate_content(
                        model='gemini-3.5-flash',
                        contents=[
                            types.Part.from_bytes(data=uploaded_paper.read(), mime_type=uploaded_paper.type),
                            f"This is a handwritten English composition about '{paper_topic}'. Please evaluate it and give advice in Japanese."
                        ]
                    )
                    st.markdown("### 📋 添削・評価結果")
                    st.markdown(response.text)

# --------------------------------------------------
# 3. 生徒画面
# --------------------------------------------------
else:
    st.title("Talky AI 2.0")
    st.write(f"ようこそ、**{school_param} {st.session_state.get('class_name')}クラス 名簿{st.session_state.get('student_number')}番 {st.session_state.get('user_name')}さん**")

    selected_topic = st.selectbox("本日のお題を選んでね：", topic_titles)

    selected_topic_data = next((t for t in my_class_topics if t.get("Topic") == selected_topic), None)
    if selected_topic_data:
        topic_teacher_id = str(selected_topic_data.get("TeacherID", "default")).strip()
        target_grammar = selected_topic_data.get("TargetGrammar", "特になし")
    else:
        topic_teacher_id = st.session_state.get("assigned_teacher_id", "default")
        target_grammar = "特になし"

    st.session_state.assigned_teacher_id = topic_teacher_id

    if topic_teacher_id != tid:
        gemini_client, active_api_key = build_gemini_client(topic_teacher_id)

    if "current_topic" not in st.session_state:
        st.session_state.current_topic = selected_topic

    if st.session_state.current_topic != selected_topic:
        st.session_state.current_topic = selected_topic
        st.session_state.messages = []
        st.rerun()

    if "messages" not in st.session_state:
        st.session_state.messages = []

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"], avatar="🧑‍🎓" if msg["role"]=="user" else "🤖"):
            st.markdown(msg["content"])

    if not gemini_client:
        st.error("⚠️ APIキーが設定されていないため、AIと会話できません。先生に連絡してください。")

    if gemini_client and (user_input := st.chat_input("英語でメッセージを入力してね...")):
        with st.chat_message("user", avatar="🧑‍🎓"):
            st.markdown(user_input)

        with st.chat_message("assistant", avatar="🤖"):
            with st.spinner("AI先生が考え中..."):

                chat_history_text = "\n".join([f"{m['role']}: {m['content']}" for m in st.session_state.messages])
                chat_history_text += f"\nuser: {user_input}"

                if "レベル1" in student_level:
                    sys_instruction = (
                        f"あなたは非常に優しい英語の先生です。\n"
                        f"現在のお題: 「{selected_topic}」 / ターゲット文法: 「{target_grammar}」\n"
                        f"【レベル1設定】生徒は英語が苦手です。生徒のメッセージを受け取ったら、即座に日本語で「【即時添削】」を提示し、どこを直すと良いか優しく日本語で解説してから、英語で1文だけ簡単な返事や問いかけをしてください。"
                    )
                elif "レベル2" in student_level:
                    sys_instruction = (
                        f"あなたはフレンドリーな英語の先生です。\n"
                        f"現在のお題: 「{selected_topic}」 / ターゲット文法: 「{target_grammar}」\n"
                        f"【レベル2設定】生徒は英語が少し苦手です。生徒のメッセージを受け取ったら、すぐに簡潔な日本語の「【添削】」と、英語での自然な返答を1〜2文返してください。"
                    )
                elif "レベル3" in student_level:
                    is_finishing = any(keyword in user_input.lower() for keyword in ["終わり", "おわり", "終了", "bye", "that's all", "finish"])
                    if is_finishing:
                        sys_instruction = (
                            f"あなたは英語の先生です。\n"
                            f"【レベル3設定】生徒から会話を終わるという申し出がありました。\n"
                            f"以下の【これまでの実際の会話履歴】をすべて読み込み、その内容に具体的に結びつけて、良かった点や改善ポイント、総合的なアドバイスを**すべて日本語で**分かりやすくフィードバックしてください。\n\n"
                            f"【これまでの実際の会話履歴】\n{chat_history_text}"
                        )
                    else:
                        sys_instruction = (
                            f"あなたはフレンドリーな英語の先生です。\n"
                            f"現在のお題: 「{selected_topic}」 / ターゲット文法: 「{target_grammar}」\n"
                            f"【レベル3設定】その都度細かい日本語での添削はせず、自然な英語の会話をテンポよく継続してください（返答は英語で1〜2文）。生徒が「終わり」と言うまでまとめの添削は控えてください。"
                        )
                else:  # レベル4
                    is_finishing = any(keyword in user_input.lower() for keyword in ["終わり", "おわり", "終了", "bye", "that's all", "finish"])
                    if is_finishing:
                        sys_instruction = (
                            f"あなたは優秀な英語の先生です。\n"
                            f"【レベル4設定】生徒は英語が得意です。会話を終えるにあたり、以下の【これまでの実際の会話履歴】をすべて読み込み、生徒が実際に使った表現を踏まえて、よりネイティブらしい高度な表現や文法のバリエーションを含めた発展的な総合アドバイスを**すべて日本語で**まとめてフィードバックしてください。\n\n"
                            f"【これまでの実際の会話履歴】\n{chat_history_text}"
                        )
                    else:
                        sys_instruction = (
                            f"あなたはフレンドリーな英語の先生です。\n"
                            f"現在のお題: 「{selected_topic}」 / ターゲット文法: 「{target_grammar}」\n"
                            f"【レベル4設定】生徒は英語が得意です。細かい日本語の添削はせず、自然でスムーズな英語の会話をハイレベルかつテンポよく継続してください（返答は英語で1〜2文）。生徒が「終わり」と言うまでまとめの添削は控えてください。"
                        )

                try:
                    response = gemini_client.models.generate_content(
                        model='gemini-3.5-flash',
                        contents=chat_history_text,
                        config=types.GenerateContentConfig(
                            system_instruction=sys_instruction
                        )
                    )
                    bot_res = response.text
                except Exception as e:
                    bot_res = f"⚠️ AIとの通信でエラーが発生しました: {e}"

                st.markdown(bot_res)

        try:
            jst = datetime.timezone(datetime.timedelta(hours=9))
            now_time = datetime.datetime.now(jst).strftime("%Y-%m-%d %H:%M:%S")

            class_name = st.session_state.get("class_name")
            logs_sheet = get_or_create_class_log_sheet(class_name)
            logs_sheet.append_row([
                now_time,
                class_name,
                st.session_state.get("student_number"),
                st.session_state.get("user_name"),
                selected_topic,
                user_input,
                bot_res,
                ""
            ])
        except Exception:
            pass

        st.session_state.messages.append({"role": "user", "content": user_input})
        st.session_state.messages.append({"role": "assistant", "content": bot_res})
        st.rerun()
