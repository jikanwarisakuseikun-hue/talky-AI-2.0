import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
from google import genai
from google.genai import types
import datetime
import pandas as pd

st.set_page_config(page_title="Talky AI 2.0", page_icon="🏫", layout="wide")

# --------------------------------------------------
# サービスアカウント認証初期化
# --------------------------------------------------
@st.cache_resource
def get_gspread_client():
    creds_dict = st.secrets["gcp"]["gcp_service_account"]
    scopes = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    return gspread.authorize(creds)

gc = get_gspread_client()
master_sheet_id = st.secrets["sheets"]["master_sheet_id"]

# --------------------------------------------------
# 1. ログイン認証画面
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
                # マスターシートの「学校管理シート」から全学校のIDリストを取得
                master_book = gc.open_by_key(master_sheet_id)
                schools_list = master_book.worksheet("学校管理シート").get_all_records()
                
                matched_user = None
                matched_school = None
                matched_school_id = None
                
                # 各学校のスプレッドシートを順に走査してユーザーを検索
                for school in schools_list:
                    s_name = school["SchoolName"]
                    s_id = school["SheetID"]
                    try:
                        school_book = gc.open_by_key(s_id)
                        users = school_book.worksheet("Users").get_all_records()
                        
                        # IDとパスワードの一致確認（型違いを防ぐため文字列化して比較）
                        user = next((u for u in users if str(u.get("ID")) == str(input_id) and str(u.get("Password")) == str(input_pass)), None)
                        if user:
                            matched_user = user
                            matched_school = s_name
                            matched_school_id = s_id
                            break
                    except Exception:
                        continue
                
                if matched_user:
                    st.session_state.authenticated = True
                    st.session_state.role = matched_user.get("Role")
                    st.session_state.user_id = matched_user.get("ID")
                    st.session_state.user_name = matched_user.get("Name", "")
                    st.session_state.school_name = matched_school
                    st.session_state.school_sheet_id = matched_school_id
                    
                    if st.session_state.role == "student":
                        st.session_state.class_name = matched_user.get("Class")
                        st.session_state.student_number = matched_user.get("StudentNumber")
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

# 各学校のシートを取得するヘルパー関数
def get_school_sheet(sheet_name):
    return gc.open_by_key(school_sheet_id).worksheet(sheet_name)

# --------------------------------------------------
# お題データの取得
# --------------------------------------------------
try:
    all_topics = get_school_sheet("Topics").get_all_records()
except Exception:
    all_topics = []

assigned_teacher_id = "default"
my_class_topics = []
if st.session_state.get("role") == "student":
    my_class = st.session_state.get("class_name")
    my_class_topics = [t for t in all_topics if str(t.get("Class")) == str(my_class)]
    if my_class_topics:
        assigned_teacher_id = my_class_topics[0].get("TeacherID", "default")
    topic_titles = [t.get("Topic") for t in my_class_topics] if my_class_topics else ["フリートーク"]
else:
    assigned_teacher_id = st.session_state.get("user_id")
    topic_titles = list(set([t.get("Topic") for t in all_topics])) if all_topics else ["フリートーク"]

st.session_state.assigned_teacher_id = assigned_teacher_id

active_api_key = st.secrets.get("teachers", {}).get(assigned_teacher_id, {}).get("gemini_api_key", st.secrets.get("DEFAULT_API_KEY", ""))
gemini_client = genai.Client(api_key=active_api_key)

# --------------------------------------------------
# サイドバー情報表示
# --------------------------------------------------
st.sidebar.title("📌 アカウント情報")
st.sidebar.write(f"**学校:** {school_param}")
if st.session_state.get('role') == 'student':
    st.sidebar.write(f"**クラス:** {st.session_state.get('class_name')}")
    st.sidebar.write(f"**名簿番号:** {st.session_state.get('student_number')}番")
st.sidebar.write(f"**氏名:** {st.session_state.get('user_name')}")
st.sidebar.info(f"🔑 **担当AI (TeacherID):** `{assigned_teacher_id}`")

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
            logs = get_school_sheet("Logs").get_all_records()
            
            if logs:
                df_logs = pd.DataFrame(logs)
                
                date_col = None
                for col in df_logs.columns:
                    if "time" in col.lower() or "date" in col.lower() or "日時" in col or "日付" in col:
                        date_col = col
                        break
                
                if date_col:
                    df_logs["date"] = pd.to_datetime(df_logs[date_col], errors="coerce").dt.date
                    
                    st.markdown("#### 🔍 ログの絞り込み設定")
                    col_f1, col_f2, col_f3 = st.columns(3)
                    
                    with col_f1:
                        use_date_filter = st.checkbox("📅 日付で絞り込む", value=False)
                        selected_date = st.date_input("日付を選択", value=datetime.date.today(), disabled=not use_date_filter)
                    with col_f2:
                        name_col = next((c for c in df_logs.columns if "name" in c.lower() or "氏名" in c or "名前" in c), None)
                        student_list = sorted(df_logs[name_col].dropna().unique().tolist()) if name_col else []
                        
                        if len(student_list) > 1:
                            selected_student = st.selectbox("👤 生徒で絞り込み", ["すべて表示"] + student_list)
                        elif len(student_list) == 1:
                            st.write(f"👤 生徒: **{student_list[0]}**")
                            selected_student = student_list[0]
                        else:
                            selected_student = "すべて表示"
                            
                    with col_f3:
                        selected_eval = st.selectbox("🏆 総合評価で絞り込み", ["すべて表示", "評価: A", "評価: B", "評価: C"])
                    
                    eval_results = []
                    for _, row in df_logs.iterrows():
                        text = str(row.get("botResponse", "")) + str(row.get("userInput", ""))
                        if "完璧" in text or "素晴らしい" in text or "excellent" in text.lower():
                            eval_results.append("A")
                        elif "もう少し" in text or "しい" in text or "try" in text.lower():
                            eval_results.append("B")
                        else:
                            eval_results.append("B" if len(str(row.get("userInput", ""))) > 10 else "C")
                    
                    df_logs["evaluation"] = eval_results
                    
                    filtered_df = df_logs.copy()
                    if use_date_filter:
                        filtered_df = filtered_df[filtered_df["date"] == selected_date]
                    if selected_student != "すべて表示" and name_col:
                        filtered_df = filtered_df[filtered_df[name_col] == selected_student]
                    if selected_eval != "すべて表示":
                        target_grade = selected_eval.replace("評価: ", "")
                        filtered_df = filtered_df[filtered_df["evaluation"] == target_grade]
                    
                    st.markdown("---")
                    
                    if not filtered_df.empty:
                        st.write(f"### 💬 やり取り履歴と総合評価ビュー （表示中: {len(filtered_df)}件 / 全{len(df_logs)}件中）")
                        for idx, row in filtered_df.iterrows():
                            grade = row.get("evaluation", "B")
                            badge_color = "🟢" if grade == "A" else ("🟡" if grade == "B" else "🔴")
                            
                            t_val = row.get(date_col, "")
                            c_val = row.get("className", row.get("Class", ""))
                            n_val = row.get("name", row.get("氏名", ""))
                            top_val = row.get("topic", row.get("お題", ""))
                            
                            with st.expander(f"{badge_color} 【評価: {grade}】 {t_val} | クラス: {c_val} | 氏名: {n_val} (お題: {top_val})"):
                                st.markdown(f"**🏆 総合評価:** `ランク {grade}`")
                                st.markdown(f"**🧑‍🎓 生徒の発話:**\n> {row.get('userInput', '')}")
                                st.markdown(f"**🤖 AIの返答・フィードバック:**\n{row.get('botResponse', '')}")
                        
                        st.markdown("---")
                        st.subheader("📋 ログ一覧データ（評価つき）")
                        st.dataframe(filtered_df.drop(columns=["date"], errors="ignore"))
                    else:
                        st.info("⚠️ 条件に一致するログはありません。")
                else:
                    st.dataframe(df_logs)
            else:
                st.info("まだサーバーにログが保存されていません。")
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
                t_class = st.text_input("対象クラス（例: 1B）")
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
                            # 1行目はヘッダーなので index + 2
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
    
    if "current_topic" not in st.session_state:
        st.session_state.current_topic = selected_topic

    if st.session_state.current_topic != selected_topic:
        st.session_state.current_topic = selected_topic
        st.session_state.messages = []
        st.rerun()

    target_grammar = "特になし（自由な会話）"
    for t in my_class_topics:
        if t.get("Topic") == selected_topic:
            target_grammar = t.get("TargetGrammar", "特になし")
            break

    if "messages" not in st.session_state:
        st.session_state.messages = []

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"], avatar="🧑‍🎓" if msg["role"]=="user" else "🤖"):
            st.markdown(msg["content"])

    if user_input := st.chat_input("英語でメッセージを入力してね..."):
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

                response = gemini_client.models.generate_content(
                    model='gemini-3.5-flash',
                    contents=user_input,
                    config=types.GenerateContentConfig(
                        system_instruction=sys_instruction
                    )
                )
                bot_res = response.text
                st.markdown(bot_res)
        
        try:
            jst = datetime.timezone(datetime.timedelta(hours=9))
            now_time = datetime.datetime.now(jst).strftime("%Y-%m-%d %H:%M:%S")
            
            # 各学校の "Logs" シートに直接追加
            logs_sheet = get_school_sheet("Logs")
            logs_sheet.append_row([
                now_time,
                st.session_state.get("class_name"),
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
