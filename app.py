import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
import plotly.express as px

# =========================
# 기본 설정
# =========================

st.set_page_config(
    page_title="여의동 따릉이 수거·재배치 분석",
    layout="wide"
)

st.title("🚲 여의동 따릉이 수거·재배치 분석 대시보드")

st.markdown("""
이 대시보드는 여의동 따릉이 이용 데이터를 바탕으로 **출발·종료 패턴과 수요 불균형**을 분석합니다.

- 여의동 선별 기준: 이용 데이터의 `시작_대여소명` 또는 `종료_대여소명`에 `여의동` 포함
- 집계 기준: `대여소_ID`
- 핵심 지표: `순유입량 = 종료_건수 - 출발_건수`
- 관리 기준: `불균형_절댓값 = |순유입량|`
- 위치 정보: 서울시 공공자전거 따릉이 대여소 마스터 정보의 주소, 위도, 경도 매칭
""")

# =========================
# 데이터 불러오기
# =========================

FILE_NAME = "여의동_따릉이_수거재배치_불균형분석.xlsx"

@st.cache_data
def load_data():
    daily = pd.read_excel(FILE_NAME, sheet_name="날짜별_대여반납_불균형")
    monthly = pd.read_excel(FILE_NAME, sheet_name="월별_대여반납_불균형")
    total = pd.read_excel(FILE_NAME, sheet_name="전체기간_대여반납_불균형")
    daily_ts = pd.read_excel(FILE_NAME, sheet_name="일별_시계열_요약")
    monthly_ts = pd.read_excel(FILE_NAME, sheet_name="월별_시계열_요약")
    pickup = pd.read_excel(FILE_NAME, sheet_name="수거후보_과잉_TOP")
    delivery = pd.read_excel(FILE_NAME, sheet_name="재배치후보_부족_TOP")
    imbalance = pd.read_excel(FILE_NAME, sheet_name="불균형_TOP")

    dfs = [daily, monthly, total, daily_ts, monthly_ts, pickup, delivery, imbalance]
    for df in dfs:
        df.columns = df.columns.str.strip()

    # 날짜 처리
    if "기준_날짜" in daily.columns:
        daily["기준_날짜"] = pd.to_datetime(daily["기준_날짜"], errors="coerce")
    if "기준_날짜" in daily_ts.columns:
        daily_ts["기준_날짜"] = pd.to_datetime(daily_ts["기준_날짜"], errors="coerce")

    # 숫자 처리
    numeric_cols = [
        "출발_건수", "종료_건수", "순유입량", "불균형_절댓값",
        "출발_이용_분", "종료_이용_분",
        "출발_이용_거리", "종료_이용_거리",
        "총이용량", "위도", "경도"
    ]

    for df in dfs:
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        # 건수 관련 NaN은 0 처리
        for col in ["출발_건수", "종료_건수", "순유입량", "불균형_절댓값", "총이용량"]:
            if col in df.columns:
                df[col] = df[col].fillna(0)

    return daily, monthly, total, daily_ts, monthly_ts, pickup, delivery, imbalance

daily_df, monthly_df, total_df, daily_ts_df, monthly_ts_df, pickup_df, delivery_df, imbalance_df = load_data()


# =========================
# 공통 함수
# =========================

def format_int(x):
    try:
        return f"{int(x):,}"
    except:
        return "0"


def get_station_col(df):
    if "대여소명" in df.columns:
        return "대여소명"
    return df.columns[0]


def show_kpis(data, label="전체"):
    total_departure = int(data["출발_건수"].sum()) if "출발_건수" in data.columns else 0
    total_arrival = int(data["종료_건수"].sum()) if "종료_건수" in data.columns else 0
    net_inflow = int(data["순유입량"].sum()) if "순유입량" in data.columns else total_arrival - total_departure
    imbalance_sum = int(data["불균형_절댓값"].sum()) if "불균형_절댓값" in data.columns else abs(net_inflow)
    station_count = data["대여소_ID"].nunique() if "대여소_ID" in data.columns else len(data)

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric(f"{label} 출발 건수", f"{total_departure:,}건")
    col2.metric(f"{label} 종료 건수", f"{total_arrival:,}건")
    col3.metric("순유입량", f"{net_inflow:,}")
    col4.metric("불균형 합계", f"{imbalance_sum:,}")
    col5.metric("대여소 수", f"{station_count:,}개")


def show_bar(data, x_col, title, key_prefix, top_n_default=10):
    if x_col not in data.columns:
        st.warning(f"{x_col} 컬럼이 없습니다.")
        return

    station_col = get_station_col(data)

    top_n = st.slider(
        "상위 몇 개 대여소를 볼까요?",
        min_value=5,
        max_value=30,
        value=top_n_default,
        key=f"{key_prefix}_{x_col}_top_n"
    )

    plot_data = data.sort_values(x_col, ascending=False).head(top_n)

    fig = px.bar(
        plot_data,
        x=x_col,
        y=station_col,
        orientation="h",
        text=x_col,
        title=title,
        hover_data=[col for col in ["대여소_ID", "주소1", "주소2", "관리유형"] if col in plot_data.columns]
    )

    fig.update_layout(yaxis={"categoryorder": "total ascending"})
    st.plotly_chart(fig, width="stretch")


def show_negative_bar(data, title, key_prefix, top_n_default=10):
    if "순유입량" not in data.columns:
        st.warning("순유입량 컬럼이 없습니다.")
        return

    station_col = get_station_col(data)

    top_n = st.slider(
        "하위 몇 개 대여소를 볼까요?",
        min_value=5,
        max_value=30,
        value=top_n_default,
        key=f"{key_prefix}_negative_top_n"
    )

    plot_data = data.sort_values("순유입량", ascending=True).head(top_n)

    fig = px.bar(
        plot_data,
        x="순유입량",
        y=station_col,
        orientation="h",
        text="순유입량",
        title=title,
        hover_data=[col for col in ["대여소_ID", "주소1", "주소2", "관리유형"] if col in plot_data.columns]
    )

    fig.update_layout(yaxis={"categoryorder": "total descending"})
    st.plotly_chart(fig, width="stretch")


def make_metric_map(data, metric_col, label_suffix="건", zoom_start=15, circle_color="#2563eb"):
    map_data = data.dropna(subset=["위도", "경도"]).copy()

    if len(map_data) == 0:
        return None

    map_data["위도"] = pd.to_numeric(map_data["위도"], errors="coerce")
    map_data["경도"] = pd.to_numeric(map_data["경도"], errors="coerce")
    map_data[metric_col] = pd.to_numeric(map_data[metric_col], errors="coerce").fillna(0)

    map_data = map_data.dropna(subset=["위도", "경도"])

    if len(map_data) == 0:
        return None

    center_lat = map_data["위도"].mean()
    center_lon = map_data["경도"].mean()

    m = folium.Map(location=[center_lat, center_lon], zoom_start=zoom_start)

    max_value = map_data[metric_col].abs().max()

    for _, row in map_data.iterrows():
        lat = row["위도"]
        lon = row["경도"]
        value = row[metric_col]

        radius = 5 if max_value == 0 else 4 + (abs(value) / max_value) * 18

        popup_text = f"""
        <b>대여소 ID:</b> {row.get('대여소_ID', '')}<br>
        <b>대여소명:</b> {row.get('대여소명', '')}<br>
        <b>주소1:</b> {row.get('주소1', '')}<br>
        <b>주소2:</b> {row.get('주소2', '')}<br>
        <b>출발 건수:</b> {format_int(row.get('출발_건수', 0))}건<br>
        <b>종료 건수:</b> {format_int(row.get('종료_건수', 0))}건<br>
        <b>순유입량:</b> {format_int(row.get('순유입량', 0))}<br>
        <b>불균형 절댓값:</b> {format_int(row.get('불균형_절댓값', 0))}<br>
        <b>관리유형:</b> {row.get('관리유형', '')}
        """

        folium.CircleMarker(
            location=[lat, lon],
            radius=radius,
            color=circle_color,
            fill=True,
            fill_color=circle_color,
            fill_opacity=0.60,
            popup=folium.Popup(popup_text, max_width=330)
        ).add_to(m)

        folium.Marker(
            location=[lat, lon],
            icon=folium.DivIcon(
                html=f"""
                <div style="
                    font-size: 10px;
                    font-weight: bold;
                    color: #111111;
                    background-color: rgba(255,255,255,0.92);
                    border: 1px solid #333333;
                    border-radius: 5px;
                    padding: 1px 4px;
                    white-space: nowrap;
                    box-shadow: 1px 1px 2px rgba(0,0,0,0.25);
                    transform: translate(8px, -8px);
                ">
                    {format_int(value)}{label_suffix}
                </div>
                """
            )
        ).add_to(m)

    return m


def make_imbalance_map(data, zoom_start=15):
    map_data = data.dropna(subset=["위도", "경도"]).copy()

    if len(map_data) == 0:
        return None

    for col in ["위도", "경도", "순유입량", "불균형_절댓값"]:
        map_data[col] = pd.to_numeric(map_data[col], errors="coerce")

    map_data = map_data.dropna(subset=["위도", "경도"])

    if len(map_data) == 0:
        return None

    center_lat = map_data["위도"].mean()
    center_lon = map_data["경도"].mean()

    m = folium.Map(location=[center_lat, center_lon], zoom_start=zoom_start)

    max_imbalance = map_data["불균형_절댓값"].max()

    for _, row in map_data.iterrows():
        lat = row["위도"]
        lon = row["경도"]
        net = row["순유입량"]
        imbalance = row["불균형_절댓값"]

        if net > 0:
            color = "red"
            status = "수거 후보(과잉)"
        elif net < 0:
            color = "blue"
            status = "재배치 후보(부족)"
        else:
            color = "gray"
            status = "균형"

        radius = 5 if max_imbalance == 0 else 4 + (imbalance / max_imbalance) * 22

        popup_text = f"""
        <b>대여소 ID:</b> {row.get('대여소_ID', '')}<br>
        <b>대여소명:</b> {row.get('대여소명', '')}<br>
        <b>주소1:</b> {row.get('주소1', '')}<br>
        <b>주소2:</b> {row.get('주소2', '')}<br>
        <b>출발 건수:</b> {format_int(row.get('출발_건수', 0))}건<br>
        <b>종료 건수:</b> {format_int(row.get('종료_건수', 0))}건<br>
        <b>순유입량:</b> {format_int(net)}<br>
        <b>불균형 절댓값:</b> {format_int(imbalance)}<br>
        <b>관리유형:</b> {status}
        """

        folium.CircleMarker(
            location=[lat, lon],
            radius=radius,
            color=color,
            fill=True,
            fill_color=color,
            fill_opacity=0.65,
            popup=folium.Popup(popup_text, max_width=330)
        ).add_to(m)

        label = f"+{format_int(net)}" if net > 0 else format_int(net)

        folium.Marker(
            location=[lat, lon],
            icon=folium.DivIcon(
                html=f"""
                <div style="
                    font-size: 10px;
                    font-weight: bold;
                    color: #111111;
                    background-color: rgba(255,255,255,0.92);
                    border: 1px solid #333333;
                    border-radius: 5px;
                    padding: 1px 4px;
                    white-space: nowrap;
                    box-shadow: 1px 1px 2px rgba(0,0,0,0.25);
                    transform: translate(8px, -8px);
                ">
                    {label}
                </div>
                """
            )
        ).add_to(m)

    return m


def show_maps(data, key_prefix):
    tab1, tab2, tab3 = st.tabs(["불균형 지도", "출발 건수 지도", "종료 건수 지도"])

    with tab1:
        st.caption("빨간색: 수거 후보(종료 > 출발), 파란색: 재배치 후보(출발 > 종료), 원 크기: 불균형 절댓값")
        m = make_imbalance_map(data)
        if m is not None:
            st_folium(m, width=None, height=650, key=f"{key_prefix}_imbalance_map")
        else:
            st.warning("지도에 표시할 위도/경도 데이터가 없습니다.")

    with tab2:
        st.caption("원 크기와 라벨은 출발 건수를 의미합니다.")
        m = make_metric_map(data, "출발_건수", "건", circle_color="#2563eb")
        if m is not None:
            st_folium(m, width=None, height=650, key=f"{key_prefix}_departure_map")
        else:
            st.warning("지도에 표시할 위도/경도 데이터가 없습니다.")

    with tab3:
        st.caption("원 크기와 라벨은 종료 건수를 의미합니다.")
        m = make_metric_map(data, "종료_건수", "건", circle_color="#16a34a")
        if m is not None:
            st_folium(m, width=None, height=650, key=f"{key_prefix}_arrival_map")
        else:
            st.warning("지도에 표시할 위도/경도 데이터가 없습니다.")


# =========================
# 사이드바
# =========================

st.sidebar.header("필터")

view_mode = st.sidebar.radio(
    "분석 기준 선택",
    ["전체 기간", "월별", "날짜별", "시계열 분석", "후보 분석"]
)


# =========================
# 전체 기간 화면
# =========================

if view_mode == "전체 기간":
    data = total_df.copy()

    st.subheader("전체 기간 기준 대여·반납 불균형 분석")
    show_kpis(data, "전체 기간")

    st.divider()

    tab1, tab2, tab3, tab4 = st.tabs(["출발 상위", "종료 상위", "불균형 상위", "순유출 상위"])

    with tab1:
        show_bar(data, "출발_건수", "전체 기간 출발 건수 상위 대여소", "total_departure")
    with tab2:
        show_bar(data, "종료_건수", "전체 기간 종료 건수 상위 대여소", "total_arrival")
    with tab3:
        show_bar(data, "불균형_절댓값", "전체 기간 불균형 절댓값 상위 대여소", "total_imbalance")
    with tab4:
        show_negative_bar(data, "전체 기간 순유출 상위 대여소", "total_outflow")

    st.divider()

    st.subheader("전체 기간 지도")
    show_maps(data, "total")

    st.divider()

    st.subheader("전체 기간 데이터 표")
    st.dataframe(data, width="stretch")


# =========================
# 월별 화면
# =========================

elif view_mode == "월별":
    st.subheader("월별 대여·반납 불균형 분석")

    months = sorted(monthly_df["월"].dropna().unique())

    selected_month = st.sidebar.selectbox("월 선택", months)

    data = monthly_df[monthly_df["월"] == selected_month].copy()

    show_kpis(data, selected_month)

    st.divider()

    tab1, tab2, tab3, tab4 = st.tabs(["출발 상위", "종료 상위", "불균형 상위", "순유출 상위"])

    with tab1:
        show_bar(data, "출발_건수", f"{selected_month} 출발 건수 상위 대여소", "month_departure")
    with tab2:
        show_bar(data, "종료_건수", f"{selected_month} 종료 건수 상위 대여소", "month_arrival")
    with tab3:
        show_bar(data, "불균형_절댓값", f"{selected_month} 불균형 절댓값 상위 대여소", "month_imbalance")
    with tab4:
        show_negative_bar(data, f"{selected_month} 순유출 상위 대여소", "month_outflow")

    st.divider()

    st.subheader(f"{selected_month} 지도")
    show_maps(data, "monthly")

    st.divider()

    st.subheader("월별 데이터 표")
    st.dataframe(data, width="stretch")


# =========================
# 날짜별 화면
# =========================

elif view_mode == "날짜별":
    st.subheader("날짜별 대여·반납 불균형 분석")

    available_dates = sorted(daily_df["기준_날짜"].dropna().dt.date.unique())

    selected_date = st.sidebar.selectbox("날짜 선택", available_dates)

    data = daily_df[daily_df["기준_날짜"].dt.date == selected_date].copy()

    show_kpis(data, str(selected_date))

    st.divider()

    tab1, tab2, tab3, tab4 = st.tabs(["출발 상위", "종료 상위", "불균형 상위", "순유출 상위"])

    with tab1:
        show_bar(data, "출발_건수", f"{selected_date} 출발 건수 상위 대여소", "day_departure")
    with tab2:
        show_bar(data, "종료_건수", f"{selected_date} 종료 건수 상위 대여소", "day_arrival")
    with tab3:
        show_bar(data, "불균형_절댓값", f"{selected_date} 불균형 절댓값 상위 대여소", "day_imbalance")
    with tab4:
        show_negative_bar(data, f"{selected_date} 순유출 상위 대여소", "day_outflow")

    st.divider()

    st.subheader(f"{selected_date} 지도")
    show_maps(data, "daily")

    st.divider()

    st.subheader("날짜별 데이터 표")
    st.dataframe(data, width="stretch")


# =========================
# 시계열 분석 화면
# =========================

elif view_mode == "시계열 분석":
    st.subheader("시계열 분석")

    st.markdown("### 여의동 전체 일별 출발·종료 건수 추이")

    fig_daily = px.line(
        daily_ts_df,
        x="기준_날짜",
        y=["출발_건수", "종료_건수"],
        markers=False,
        title="일별 출발 건수와 종료 건수"
    )
    st.plotly_chart(fig_daily, width="stretch")

    st.divider()

    st.markdown("### 여의동 전체 일별 순유입량 추이")

    fig_net = px.bar(
        daily_ts_df,
        x="기준_날짜",
        y="순유입량",
        title="일별 순유입량 = 종료 건수 - 출발 건수"
    )
    st.plotly_chart(fig_net, width="stretch")

    st.divider()

    st.markdown("### 여의동 전체 일별 불균형 규모 추이")

    fig_imbalance = px.line(
        daily_ts_df,
        x="기준_날짜",
        y="불균형_절댓값",
        title="일별 불균형 절댓값 합계"
    )
    st.plotly_chart(fig_imbalance, width="stretch")

    st.divider()

    st.markdown("### 월별 출발·종료 건수 추이")

    fig_month = px.line(
        monthly_ts_df,
        x="월",
        y=["출발_건수", "종료_건수"],
        markers=True,
        title="월별 출발 건수와 종료 건수"
    )
    st.plotly_chart(fig_month, width="stretch")

    st.divider()

    st.markdown("### 대여소별 불균형 시계열")

    station_list = sorted(daily_df["대여소명"].dropna().unique())
    selected_station = st.selectbox("대여소 선택", station_list)

    station_data = daily_df[daily_df["대여소명"] == selected_station].copy()
    station_data = station_data.sort_values("기준_날짜")

    fig_station = px.line(
        station_data,
        x="기준_날짜",
        y=["출발_건수", "종료_건수", "순유입량"],
        title=f"{selected_station} 일별 출발·종료·순유입량 추이"
    )
    st.plotly_chart(fig_station, width="stretch")

    st.divider()

    st.markdown("### 일별 시계열 요약 표")
    st.dataframe(daily_ts_df, width="stretch")


# =========================
# 후보 분석 화면
# =========================

elif view_mode == "후보 분석":
    st.subheader("수거·재배치 후보 분석")

    st.markdown("""
    - **수거 후보(과잉)**: `순유입량 > 0`, 즉 종료 건수가 출발 건수보다 큰 대여소
    - **재배치 후보(부족)**: `순유입량 < 0`, 즉 출발 건수가 종료 건수보다 큰 대여소
    - **불균형 TOP**: `불균형_절댓값`이 큰 대여소
    """)

    tab1, tab2, tab3 = st.tabs(["수거 후보", "재배치 후보", "불균형 TOP"])

    with tab1:
        st.subheader("수거 후보: 자전거가 쌓이는 대여소")
        show_bar(pickup_df, "순유입량", "수거 후보 TOP", "pickup_candidate")
        st.dataframe(pickup_df, width="stretch")

    with tab2:
        st.subheader("재배치 후보: 자전거가 부족해지는 대여소")
        show_negative_bar(delivery_df, "재배치 후보 TOP", "delivery_candidate")
        st.dataframe(delivery_df, width="stretch")

    with tab3:
        st.subheader("불균형 TOP: 우선 관리 대상")
        show_bar(imbalance_df, "불균형_절댓값", "불균형 절댓값 TOP", "imbalance_candidate")
        st.dataframe(imbalance_df, width="stretch")
