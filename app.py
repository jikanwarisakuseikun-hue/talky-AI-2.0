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

# 共通関数：スプレッドシートを開く
def get_school_sheet(school_name, sheet_name):
    return gc.open(school_name).worksheet(sheet_name)

# --------------------------------------------------
# 1. ログイン認証
# --------------------------------------------------
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    st.title("🏫 Talky AI 2.0 ログイン")
    input_id = st.text_input("ID：")
    input_pass = st.text_input("パスワード：", type="password")
    
    if st.button("ログイン"):
        try:
            # SchoolMaster シートから認証
            users = gc.open("SchoolMaster").worksheet("Users").get_all_records()
            user = next((u for u in users if str(u["ID"]) == input_id and str(u["Password"]) == input_pass), None)
            
            if user:
                st.session_state.update({
                    "authenticated": True,
                    "role": user["Role"],
                    "user_id": user["ID"],
                    "user_name": user["Name"],
                    "school_name": user["SchoolName"],
                    "class_name": user.get("Class", ""),
                    "student_number": user.get("StudentNumber", "")
                })
                st.rerun()
            else:
                st.error("IDまたはパスワードが違います。")
        except Exception as e:
            st.error(f"接続エラー: {e}")
    st.stop()

# --------------------------------------------------
# メイン処理
# --------------------------------------------------
school_param = st.session_state.school_name

# お題データ取得
all_topics = get_school_sheet(school_param, "Topics").get_all_records()
# 役割・クラスによるフィルタリング
if st.session_state.role == "student":
    my_class_topics = [t for t in all_topics if str(t["Class"]) == str(st.session_state.class_name)]
    assigned_teacher_id = my_class_topics[0]["TeacherID"] if my_class_topics else "default"
    topic_titles = [t["Topic"] for t in my_class_topics] or ["フリートーク"]
else:
    assigned_teacher_id = st.session_state.user_id
    topic_titles = list(set([t["Topic"] for t in all_topics])) or ["フリートーク"]

# APIキー設定
active_api_key = st.secrets.get("teachers", {}).get(assigned_teacher_id, {}).get("gemini_api_key", st.secrets.get("DEFAULT_API_KEY", ""))
gemini_client = genai.Client(api_key=active_api_key)

# サイドバー（省略可：元の表示を維持）
if st.sidebar.button("ログアウト"):
    st.session_state.authenticated = False
    st.rerun()

# --------------------------------------------------
# 教師用機能
# --------------------------------------------------
if st.session_state.role == "teacher":
    tab1, tab2 = st.tabs(["📊 会話ログ", "📝 お題管理"])
    with tab1:
        logs = get_school_sheet(school_param, "Logs").get_all_records()
        st.dataframe(pd.DataFrame(logs))
    with tab2:
        # 新規お題追加ロジック
        if st.button("お題追加"):
            get_school_sheet(school_param, "Topics").append_row([st.session_state.class_name, "New Topic", "Grammar", assigned_teacher_id])
            st.rerun()

# --------------------------------------------------
# 生徒用チャット
# --------------------------------------------------
else:
    selected_topic = st.selectbox("お題を選択：", topic_titles)
    if "messages" not in st.session_state: st.session_state.messages = []
    
    if user_input := st.chat_input("英語で入力..."):
        # Gemini呼び出し
        response = gemini_client.models.generate_content(model='gemini-3.5-flash', contents=user_input)
        bot_res = response.text
        
        # 直接スプレッドシートに保存
        get_school_sheet(school_param, "Logs").append_row([
            str(datetime.datetime.now()), st.session_state.class_name, st.session_state.student_number, 
            st.session_state.user_name, selected_topic, user_input, bot_res
        ])
        
        st.session_state.messages.append({"role": "user", "content": user_input})
        st.session_state.messages.append({"role": "assistant", "content": bot_res})
        st.rerun()
