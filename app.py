import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
import plotly.express as px

# =========================
# 기본 설정
# =========================

st.set_page_config(
    page_title="여의동 따릉이 종료 대여소 분석",
    layout="wide"
)

st.title("🚲 여의동 따릉이 종료 대여소 분석 대시보드")

st.markdown("""
이 대시보드는 여의동 종료 대여소를 기준으로 따릉이 반납 건수를 분석한 결과입니다.

- 여의동 선별 기준: 이용 데이터의 `종료_대여소명`에 `여의동` 포함
- 집계 기준: `종료_대여소_ID`
- 위치 정보: 따릉이 대여소 마스터 정보의 주소, 위도, 경도 매칭
""")

# =========================
# 데이터 불러오기
# =========================

FILE_NAME = "여의동_종료대여소_집계결과.xlsx"

@st.cache_data
def load_data():
    daily = pd.read_excel(FILE_NAME, sheet_name="날짜별_종료대여소")
    monthly = pd.read_excel(FILE_NAME, sheet_name="월별_종료대여소")
    total = pd.read_excel(FILE_NAME, sheet_name="전체기간_종료대여소")

    daily.columns = daily.columns.str.strip()
    monthly.columns = monthly.columns.str.strip()
    total.columns = total.columns.str.strip()

    daily["기준_날짜"] = pd.to_datetime(daily["기준_날짜"], errors="coerce")
    daily["전체_건수"] = pd.to_numeric(daily["전체_건수"], errors="coerce").fillna(0)
    daily["위도"] = pd.to_numeric(daily["위도"], errors="coerce")
    daily["경도"] = pd.to_numeric(daily["경도"], errors="coerce")

    monthly["전체_건수"] = pd.to_numeric(monthly["전체_건수"], errors="coerce").fillna(0)
    monthly["위도"] = pd.to_numeric(monthly["위도"], errors="coerce")
    monthly["경도"] = pd.to_numeric(monthly["경도"], errors="coerce")

    total["전체_건수"] = pd.to_numeric(total["전체_건수"], errors="coerce").fillna(0)
    total["위도"] = pd.to_numeric(total["위도"], errors="coerce")
    total["경도"] = pd.to_numeric(total["경도"], errors="coerce")

    return daily, monthly, total

daily_df, monthly_df, total_df = load_data()

# =========================
# 지도 생성 함수
# =========================

def make_count_map(data, zoom_start=15):
    map_data = data.dropna(subset=["위도", "경도"]).copy()

    if len(map_data) == 0:
        return None

    map_data["위도"] = pd.to_numeric(map_data["위도"], errors="coerce")
    map_data["경도"] = pd.to_numeric(map_data["경도"], errors="coerce")
    map_data["전체_건수"] = pd.to_numeric(map_data["전체_건수"], errors="coerce").fillna(0)

    map_data = map_data.dropna(subset=["위도", "경도"])

    if len(map_data) == 0:
        return None

    center_lat = map_data["위도"].mean()
    center_lon = map_data["경도"].mean()

    m = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=zoom_start
    )

    max_count = map_data["전체_건수"].max()

    for _, row in map_data.iterrows():
        lat = row["위도"]
        lon = row["경도"]
        count = int(row["전체_건수"])

        if max_count > 0:
            radius = 4 + (count / max_count) * 18
        else:
            radius = 5

        folium.CircleMarker(
            location=[lat, lon],
            radius=radius,
            fill=True,
            fill_opacity=0.65,
            popup=folium.Popup(
                f"""
                <b>종료 대여소 ID:</b> {row['종료_대여소_ID']}<br>
                <b>대여소명:</b> {row['종료_대여소명']}<br>
                <b>주소1:</b> {row['주소1']}<br>
                <b>주소2:</b> {row['주소2']}<br>
                <b>전체 건수:</b> {count:,}건
                """,
                max_width=300
            )
        ).add_to(m)

        folium.Marker(
            location=[lat, lon],
            icon=folium.DivIcon(
                html=f"""
                <div style="
                    font-size: 10px;
                    font-weight: bold;
                    color: black;
                    background-color: rgba(255,255,255,0.75);
                    border: 1px solid gray;
                    border-radius: 4px;
                    padding: 1px 3px;
                    white-space: nowrap;
                    transform: translate(7px, -7px);
                ">
                    {count:,}건
                </div>
                """
            )
        ).add_to(m)

    return m


# =========================
# 공통 그래프 함수
# =========================

def show_top_bar(data, title):
    top_n = st.slider(
        "상위 몇 개 대여소를 볼까요?",
        min_value=5,
        max_value=30,
        value=10
    )

    top_data = data.sort_values("전체_건수", ascending=False).head(top_n)

    fig = px.bar(
        top_data,
        x="전체_건수",
        y="종료_대여소명",
        orientation="h",
        text="전체_건수",
        title=title
    )

    fig.update_layout(yaxis={"categoryorder": "total ascending"})
    st.plotly_chart(fig, width="stretch")


# =========================
# 사이드바
# =========================

st.sidebar.header("필터")

view_mode = st.sidebar.radio(
    "분석 기준 선택",
    ["전체 기간", "월별", "날짜별", "시계열 분석"]
)

# =========================
# 전체 기간 화면
# =========================

if view_mode == "전체 기간":
    data = total_df.copy()

    st.subheader("전체 기간 기준 종료 대여소 분석")

    total_count = int(data["전체_건수"].sum())
    station_count = data["종료_대여소_ID"].nunique()
    avg_count = round(data["전체_건수"].mean(), 1)

    col1, col2, col3 = st.columns(3)
    col1.metric("전체 종료 건수", f"{total_count:,}건")
    col2.metric("분석 대여소 수", f"{station_count:,}개")
    col3.metric("대여소 평균 종료 건수", f"{avg_count:,}건")

    st.divider()

    st.subheader("종료 건수 상위 대여소")
    show_top_bar(data, "전체 기간 종료 건수 상위 대여소")

    st.divider()

    st.subheader("전체 기간 종료 대여소 위치 및 건수 지도")

    m = make_count_map(data)

    if m is not None:
        st_folium(m, width=None, height=650)
    else:
        st.warning("지도에 표시할 위도/경도 데이터가 없습니다.")

    st.divider()

    st.subheader("전체 기간 데이터 표")
    st.dataframe(data, width="stretch")


# =========================
# 월별 화면
# =========================

elif view_mode == "월별":
    st.subheader("월별 종료 대여소 분석")

    months = sorted(monthly_df["월"].dropna().unique())

    selected_month = st.sidebar.selectbox(
        "월 선택",
        months
    )

    data = monthly_df[monthly_df["월"] == selected_month].copy()

    total_count = int(data["전체_건수"].sum())
    station_count = data["종료_대여소_ID"].nunique()
    avg_count = round(data["전체_건수"].mean(), 1)

    col1, col2, col3 = st.columns(3)
    col1.metric("선택 월", selected_month)
    col2.metric("월 종료 건수", f"{total_count:,}건")
    col3.metric("대여소 수", f"{station_count:,}개")

    st.divider()

    st.subheader(f"{selected_month} 종료 건수 상위 대여소")
    show_top_bar(data, f"{selected_month} 종료 건수 상위 대여소")

    st.divider()

    st.subheader(f"{selected_month} 종료 대여소 위치 및 건수 지도")

    m = make_count_map(data)

    if m is not None:
        st_folium(m, width=None, height=650)
    else:
        st.warning("지도에 표시할 위도/경도 데이터가 없습니다.")

    st.divider()

    st.subheader("월별 데이터 표")
    st.dataframe(data, width="stretch")


# =========================
# 날짜별 화면
# =========================

else:
    st.subheader("날짜별 종료 대여소 분석")

    available_dates = sorted(daily_df["기준_날짜"].dropna().dt.date.unique())

    selected_date = st.sidebar.selectbox(
        "날짜 선택",
        available_dates
    )

    data = daily_df[daily_df["기준_날짜"].dt.date == selected_date].copy()

    total_count = int(data["전체_건수"].sum())
    station_count = data["종료_대여소_ID"].nunique()
    avg_count = round(data["전체_건수"].mean(), 1)

    col1, col2, col3 = st.columns(3)
    col1.metric("선택 날짜", str(selected_date))
    col2.metric("일 종료 건수", f"{total_count:,}건")
    col3.metric("대여소 수", f"{station_count:,}개")

    st.divider()

    st.subheader(f"{selected_date} 종료 건수 상위 대여소")
    show_top_bar(data, f"{selected_date} 종료 건수 상위 대여소")

    st.divider()

    st.subheader(f"{selected_date} 종료 대여소 위치 및 건수 지도")

    m = make_count_map(data)

    if m is not None:
        st_folium(m, width=None, height=650)
    else:
        st.warning("지도에 표시할 위도/경도 데이터가 없습니다.")

    st.divider()

    st.subheader("날짜별 데이터 표")
    st.dataframe(data, width="stretch")

# =========================
# 시계열 분석 화면
# =========================

elif view_mode == "시계열 분석":
    st.subheader("시계열 분석")

    # 날짜별 전체 여의동 종료 건수
    daily_total = (
        daily_df.groupby("기준_날짜", as_index=False)
        .agg({
            "전체_건수": "sum",
            "전체_이용_분": "sum",
            "전체_이용_거리": "sum"
        })
        .sort_values("기준_날짜")
    )

    # 7일 이동평균
    daily_total["7일_이동평균"] = daily_total["전체_건수"].rolling(window=7).mean()

    st.markdown("### 여의동 전체 일별 종료 건수 추이")

    fig_daily = px.line(
        daily_total,
        x="기준_날짜",
        y=["전체_건수", "7일_이동평균"],
        title="여의동 전체 일별 종료 건수 및 7일 이동평균"
    )

    st.plotly_chart(fig_daily, width="stretch")

    st.divider()

    # 월별 전체 추이
    monthly_total = (
        monthly_df.groupby("월", as_index=False)
        .agg({
            "전체_건수": "sum",
            "전체_이용_분": "sum",
            "전체_이용_거리": "sum"
        })
        .sort_values("월")
    )

    st.markdown("### 월별 전체 종료 건수 추이")

    fig_monthly = px.line(
        monthly_total,
        x="월",
        y="전체_건수",
        markers=True,
        title="여의동 월별 전체 종료 건수 추이"
    )

    st.plotly_chart(fig_monthly, width="stretch")

    st.divider()

    # 대여소별 시계열
    st.markdown("### 대여소별 일별 종료 건수 추이")

    station_list = sorted(daily_df["종료_대여소명"].dropna().unique())

    selected_station = st.selectbox(
        "대여소 선택",
        station_list
    )

    station_data = daily_df[daily_df["종료_대여소명"] == selected_station].copy()
    station_data = station_data.sort_values("기준_날짜")

    station_data["7일_이동평균"] = station_data["전체_건수"].rolling(window=7).mean()

    fig_station = px.line(
        station_data,
        x="기준_날짜",
        y=["전체_건수", "7일_이동평균"],
        title=f"{selected_station} 일별 종료 건수 추이"
    )

    st.plotly_chart(fig_station, width="stretch")

    st.divider()

    st.markdown("### 시계열 데이터 표")
    st.dataframe(daily_total, width="stretch")
