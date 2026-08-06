import streamlit as st
import requests
from google import genai
from google.genai import types
import datetime

st.set_page_config(page_title="英語添削＆チャット システム", page_icon="🏫", layout="wide")

GAS_URL = st.secrets.get("GAS_URL", "")

# --------------------------------------------------
# 1. ログイン認証画面
# --------------------------------------------------
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    st.title("🏫 英語添削＆チャット ログイン")
    input_id = st.text_input("ID（教師ID または 生徒ID）：")
    input_pass = st.text_input("パスワード：", type="password")
    
    if st.button("ログイン"):
        try:
            res_schools = requests.get(f"{GAS_URL}?action=getSchools", timeout=10)
            schools = res_schools.json()
            
            matched_user = None
            matched_school = None
            
            for s in schools:
                s_name = s.get("SchoolName")
                res_users = requests.get(f"{GAS_URL}?action=getUsers&schoolName={s_name}", timeout=10)
                users = res_users.json()
                for u in users:
                    if str(u.get("ID")) == str(input_id) and str(u.get("Password")) == str(input_pass):
                        matched_user = u
                        matched_school = s_name
                        break
                if matched_user:
                    break
            
            if matched_user:
                st.session_state.authenticated = True
                st.session_state.role = matched_user.get("Role")
                st.session_state.user_id = matched_user.get("ID")
                st.session_state.user_name = matched_user.get("Name", "")
                st.session_state.school_name = matched_school
                
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

# --------------------------------------------------
# お題データの取得（Topicsシートからクラス担当のTeacherIDを特定）
# --------------------------------------------------
try:
    res_topics = requests.get(f"{GAS_URL}?action=getTopics&schoolName={school_param}", timeout=10)
    all_topics = res_topics.json()
except Exception:
    all_topics = []

assigned_teacher_id = "default"
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

# Secretsから対応するTeacherIDのAPIキーを取得
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

if st.sidebar.button("ログアウト"):
    st.session_state.authenticated = False
    st.rerun()

# --------------------------------------------------
# 2. 教師画面
# --------------------------------------------------
if st.session_state.get("role") == "teacher":
    st.title(f"👩‍🏫 教師用ダッシュボード ({school_param} / {st.session_state.get('user_name')}先生)")
    
    tab1, tab2, tab3 = st.tabs(["📊 クラス別会話ログ", "📝 お題の管理", "📷 紙媒体の英作文評価"])
    
    with tab1:
        st.subheader("生徒の会話ログ（クラス別タブから集約）")
        try:
            res = requests.get(f"{GAS_URL}?action=getLogs&schoolName={school_param}", timeout=10)
            logs = res.json()
            if logs:
                st.dataframe(logs)
            else:
                st.info("まだログはありません。")
        except Exception as e:
            st.warning(f"ログの取得に失敗しました: {e}")

    with tab2:
        st.subheader("お題の管理 (Topicsシート)")
        if all_topics:
            st.dataframe(all_topics)
        else:
            st.info("お題データがありません。")
            
        with st.form("topic_form"):
            st.write("#### 新規お題の追加")
            t_class = st.text_input("対象クラス（例: 1B）")
            t_topic = st.text_input("お題タイトル")
            t_grammar = st.text_input("ターゲット文法")
            if st.form_submit_button("お題を登録する"):
                payload = {
                    "action": "addTopic",
                    "schoolName": school_param,
                    "class_name": t_class,
                    "topic": t_topic,
                    "target_grammar": t_grammar,
                    "teacher_id": st.session_state.user_id
                }
                requests.post(GAS_URL, json=payload)
                st.success("お題を追加しました！")
                st.rerun()

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
                        f"この手書き/印刷された英作文を読み取り、お題「{paper_topic}」に沿って中学生向けに優しく添削・評価してください。"
                    ]
                )
                st.markdown("### 📋 添削・評価結果")
                st.markdown(response.text)

# --------------------------------------------------
# 3. 生徒画面
# --------------------------------------------------
else:
    st.title("🏫 中学生向け 英語添削チャット")
    st.write(f"ようこそ、**{school_param} {st.session_state.get('class_name')}クラス 名簿{st.session_state.get('student_number')}番 {st.session_state.get('user_name')}さん**")
    
    selected_topic = st.selectbox("本日のお題を選んでね：", topic_titles)
    
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
                response = gemini_client.models.generate_content(
                    model='gemini-2.5-flash',
                    contents=user_input,
                    config=types.GenerateContentConfig(
                        system_instruction="あなたはフレンドリーな英語の先生です。中学生の英語を優しく添削し、英語で1〜2文返答してください。"
                    )
                )
                bot_res = response.text
                st.markdown(bot_res)
        
        # 生徒の所属クラス名（例: "1B"）のタブへ自動保存
        try:
            now_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            payload = {
                "action": "addLog",
                "schoolName": school_param,
                "timestamp": now_time,
                "className": st.session_state.get("class_name"),
                "studentNumber": st.session_state.get("student_number"),
                "name": st.session_state.get("user_name"),
                "topic": selected_topic,
                "userInput": user_input,
                "botResponse": bot_res,
                "teacherComment": ""
            }
            requests.post(GAS_URL, json=payload, timeout=5)
        except Exception:
            pass

        st.session_state.messages.append({"role": "user", "content": user_input})
        st.session_state.messages.append({"role": "assistant", "content": bot_res})
        st.rerun()
