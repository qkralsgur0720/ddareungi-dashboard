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
이 대시보드는 여의동 따릉이 대여·반납 데이터를 기반으로 **수거 및 재배치 우선 대여소**를 분석합니다.

- **출발 건수**: 해당 대여소에서 따릉이가 빠져나간 양
- **종료 건수**: 해당 대여소로 따릉이가 들어온 양
- **순유입량 = 종료 건수 - 출발 건수**
- **불균형 절댓값 = |순유입량|**
- **순유입량 > 0**: 자전거가 쌓이는 곳 → 수거 후보
- **순유입량 < 0**: 자전거가 부족해지는 곳 → 재배치 후보
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

    daily["기준_날짜"] = pd.to_datetime(daily["기준_날짜"], errors="coerce")
    daily_ts["기준_날짜"] = pd.to_datetime(daily_ts["기준_날짜"], errors="coerce")

    numeric_cols = [
        "출발_건수", "종료_건수", "순유입량", "불균형_절댓값",
        "출발_이용_분", "종료_이용_분",
        "출발_이용_거리", "종료_이용_거리",
        "위도", "경도"
    ]

    for df in [daily, monthly, total, pickup, delivery, imbalance]:
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    ts_numeric_cols = [
        "출발_건수", "종료_건수", "순유입량",
        "불균형_절댓값", "총이용량",
        "출발_이용_분", "종료_이용_분",
        "출발_이용_거리", "종료_이용_거리"
    ]

    for df in [daily_ts, monthly_ts]:
        for col in ts_numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    return daily, monthly, total, daily_ts, monthly_ts, pickup, delivery, imbalance


daily_df, monthly_df, total_df, daily_ts_df, monthly_ts_df, pickup_df, delivery_df, imbalance_df = load_data()


# =========================
# 공통 함수
# =========================

def metric_block(data):
    total_departure = int(data["출발_건수"].sum())
    total_arrival = int(data["종료_건수"].sum())
    net_flow = int(data["순유입량"].sum())
    total_imbalance = int(data["불균형_절댓값"].sum())
    station_count = data["대여소_ID"].nunique()

    col1, col2, col3, col4, col5 = st.columns(5)

    col1.metric("출발 건수", f"{total_departure:,}건")
    col2.metric("종료 건수", f"{total_arrival:,}건")
    col3.metric("순유입량", f"{net_flow:,}건")
    col4.metric("불균형 규모", f"{total_imbalance:,}건")
    col5.metric("대여소 수", f"{station_count:,}개")


def show_top_bar(data, value_col, title, key_prefix):
    top_n = st.slider(
        "상위 몇 개 대여소를 볼까요?",
        min_value=5,
        max_value=30,
        value=10,
        key=f"{key_prefix}_{value_col}_top_n"
    )

    top_data = data.sort_values(value_col, ascending=False).head(top_n)

    fig = px.bar(
        top_data,
        x=value_col,
        y="대여소명",
        orientation="h",
        text=value_col,
        title=title
    )

    fig.update_layout(yaxis={"categoryorder": "total ascending"})
    st.plotly_chart(fig, width="stretch")


def show_bottom_bar(data, value_col, title, key_prefix):
    top_n = st.slider(
        "상위 몇 개 대여소를 볼까요?",
        min_value=5,
        max_value=30,
        value=10,
        key=f"{key_prefix}_{value_col}_bottom_n"
    )

    bottom_data = data.sort_values(value_col, ascending=True).head(top_n)

    fig = px.bar(
        bottom_data,
        x=value_col,
        y="대여소명",
        orientation="h",
        text=value_col,
        title=title
    )

    fig.update_layout(yaxis={"categoryorder": "total ascending"})
    st.plotly_chart(fig, width="stretch")


def make_value_map(data, value_col, circle_color="#2563eb", label_suffix="건"):
    map_data = data.dropna(subset=["위도", "경도"]).copy()

    if len(map_data) == 0:
        return None

    map_data["위도"] = pd.to_numeric(map_data["위도"], errors="coerce")
    map_data["경도"] = pd.to_numeric(map_data["경도"], errors="coerce")
    map_data[value_col] = pd.to_numeric(map_data[value_col], errors="coerce").fillna(0)

    map_data = map_data.dropna(subset=["위도", "경도"])

    if len(map_data) == 0:
        return None

    center_lat = map_data["위도"].mean()
    center_lon = map_data["경도"].mean()

    m = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=15
    )

    max_value = map_data[value_col].abs().max()

    for _, row in map_data.iterrows():
        lat = row["위도"]
        lon = row["경도"]
        value = row[value_col]

        if max_value > 0:
            radius = 5 + (abs(value) / max_value) * 20
        else:
            radius = 6

        label_value = int(value)

        folium.CircleMarker(
            location=[lat, lon],
            radius=radius,
            color=circle_color,
            fill=True,
            fill_color=circle_color,
            fill_opacity=0.55,
            popup=folium.Popup(
                f"""
                <b>대여소 ID:</b> {row['대여소_ID']}<br>
                <b>대여소명:</b> {row['대여소명']}<br>
                <b>주소1:</b> {row.get('주소1', '')}<br>
                <b>주소2:</b> {row.get('주소2', '')}<br>
                <b>출발 건수:</b> {int(row.get('출발_건수', 0)):,}건<br>
                <b>종료 건수:</b> {int(row.get('종료_건수', 0)):,}건<br>
                <b>순유입량:</b> {int(row.get('순유입량', 0)):,}건<br>
                <b>불균형 절댓값:</b> {int(row.get('불균형_절댓값', 0)):,}건<br>
                <b>관리유형:</b> {row.get('관리유형', '')}
                """,
                max_width=350
            )
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
                    {label_value:,}{label_suffix}
                </div>
                """
            )
        ).add_to(m)

    return m


def make_imbalance_map(data):
    map_data = data.dropna(subset=["위도", "경도"]).copy()

    if len(map_data) == 0:
        return None

    map_data["위도"] = pd.to_numeric(map_data["위도"], errors="coerce")
    map_data["경도"] = pd.to_numeric(map_data["경도"], errors="coerce")
    map_data["순유입량"] = pd.to_numeric(map_data["순유입량"], errors="coerce").fillna(0)
    map_data["불균형_절댓값"] = pd.to_numeric(map_data["불균형_절댓값"], errors="coerce").fillna(0)

    map_data = map_data.dropna(subset=["위도", "경도"])

    if len(map_data) == 0:
        return None

    center_lat = map_data["위도"].mean()
    center_lon = map_data["경도"].mean()

    m = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=15
    )

    max_imbalance = map_data["불균형_절댓값"].max()

    for _, row in map_data.iterrows():
        lat = row["위도"]
        lon = row["경도"]
        net = int(row["순유입량"])
        imbalance = int(row["불균형_절댓값"])

        if net > 0:
            circle_color = "#dc2626"
        elif net < 0:
            circle_color = "#2563eb"
        else:
            circle_color = "#6b7280"

        if max_imbalance > 0:
            radius = 5 + (imbalance / max_imbalance) * 22
        else:
            radius = 6

        folium.CircleMarker(
            location=[lat, lon],
            radius=radius,
            color=circle_color,
            fill=True,
            fill_color=circle_color,
            fill_opacity=0.55,
            popup=folium.Popup(
                f"""
                <b>대여소 ID:</b> {row['대여소_ID']}<br>
                <b>대여소명:</b> {row['대여소명']}<br>
                <b>주소1:</b> {row.get('주소1', '')}<br>
                <b>주소2:</b> {row.get('주소2', '')}<br>
                <b>출발 건수:</b> {int(row.get('출발_건수', 0)):,}건<br>
                <b>종료 건수:</b> {int(row.get('종료_건수', 0)):,}건<br>
                <b>순유입량:</b> {net:,}건<br>
                <b>불균형 절댓값:</b> {imbalance:,}건<br>
                <b>관리유형:</b> {row.get('관리유형', '')}
                """,
                max_width=350
            )
        ).add_to(m)

        if net > 0:
            label_text = f"+{net:,}"
        else:
            label_text = f"{net:,}"

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
                    {label_text}
                </div>
                """
            )
        ).add_to(m)

    return m


def render_analysis_page(data, title_prefix, key_prefix):
    st.subheader(f"{title_prefix} 대여·반납 분석")

    metric_block(data)

    st.divider()

    st.markdown("## 1. 출발 대여소 분석")

    st.markdown("### 출발 건수 상위 대여소")
    show_top_bar(data, "출발_건수", f"{title_prefix} 출발 건수 상위 대여소", f"{key_prefix}_dep")

    st.markdown("### 출발 건수 지도")
    dep_map = make_value_map(
        data=data,
        value_col="출발_건수",
        circle_color="#2563eb",
        label_suffix="건"
    )

    if dep_map is not None:
        st_folium(dep_map, width=None, height=650, key=f"{key_prefix}_dep_map")
    else:
        st.warning("출발 지도에 표시할 위도/경도 데이터가 없습니다.")

    st.divider()

    st.markdown("## 2. 종료 대여소 분석")

    st.markdown("### 종료 건수 상위 대여소")
    show_top_bar(data, "종료_건수", f"{title_prefix} 종료 건수 상위 대여소", f"{key_prefix}_arr")

    st.markdown("### 종료 건수 지도")
    arr_map = make_value_map(
        data=data,
        value_col="종료_건수",
        circle_color="#16a34a",
        label_suffix="건"
    )

    if arr_map is not None:
        st_folium(arr_map, width=None, height=650, key=f"{key_prefix}_arr_map")
    else:
        st.warning("종료 지도에 표시할 위도/경도 데이터가 없습니다.")

    st.divider()

    st.markdown("## 3. 불균형 분석")

    st.markdown("### 불균형 절댓값 상위 대여소")
    show_top_bar(data, "불균형_절댓값", f"{title_prefix} 불균형 상위 대여소", f"{key_prefix}_imb")

    st.markdown("### 순유입량 상위 대여소: 수거 후보")
    pickup_data = data[data["순유입량"] > 0].copy()
    if len(pickup_data) > 0:
        show_top_bar(pickup_data, "순유입량", f"{title_prefix} 수거 후보 상위 대여소", f"{key_prefix}_pickup")
    else:
        st.info("수거 후보 데이터가 없습니다.")

    st.markdown("### 순유입량 하위 대여소: 재배치 후보")
    delivery_data = data[data["순유입량"] < 0].copy()
    if len(delivery_data) > 0:
        show_bottom_bar(delivery_data, "순유입량", f"{title_prefix} 재배치 후보 상위 대여소", f"{key_prefix}_delivery")
    else:
        st.info("재배치 후보 데이터가 없습니다.")

    st.markdown("### 불균형 지도")
    st.caption("빨간색은 수거 후보(과잉), 파란색은 재배치 후보(부족), 원 크기는 불균형 절댓값을 의미합니다.")

    imb_map = make_imbalance_map(data)

    if imb_map is not None:
        st_folium(imb_map, width=None, height=650, key=f"{key_prefix}_imb_map")
    else:
        st.warning("불균형 지도에 표시할 위도/경도 데이터가 없습니다.")

    st.divider()

    st.markdown("## 4. 데이터 표")
    st.dataframe(data, width="stretch")


# =========================
# 사이드바
# =========================

st.sidebar.header("필터")

view_mode = st.sidebar.radio(
    "분석 기준 선택",
    ["전체 기간", "월별", "날짜별", "시계열 분석", "후보 분석"]
)

# =========================
# 전체 기간
# =========================

if view_mode == "전체 기간":
    data = total_df.copy()
    render_analysis_page(data, "전체 기간", "total")


# =========================
# 월별
# =========================

elif view_mode == "월별":
    months = sorted(monthly_df["월"].dropna().unique())

    selected_month = st.sidebar.selectbox(
        "월 선택",
        months
    )

    data = monthly_df[monthly_df["월"] == selected_month].copy()
    render_analysis_page(data, f"{selected_month}", "monthly")


# =========================
# 날짜별
# =========================

elif view_mode == "날짜별":
    available_dates = sorted(daily_df["기준_날짜"].dropna().dt.date.unique())

    selected_date = st.sidebar.selectbox(
        "날짜 선택",
        available_dates
    )

    data = daily_df[daily_df["기준_날짜"].dt.date == selected_date].copy()
    render_analysis_page(data, f"{selected_date}", "daily")


# =========================
# 시계열 분석
# =========================

elif view_mode == "시계열 분석":
    st.subheader("시계열 분석")

    st.markdown("### 여의동 전체 일별 출발·종료 건수 추이")

    fig_daily_flow = px.line(
        daily_ts_df.sort_values("기준_날짜"),
        x="기준_날짜",
        y=["출발_건수", "종료_건수"],
        title="일별 출발 건수와 종료 건수"
    )

    st.plotly_chart(fig_daily_flow, width="stretch")

    st.divider()

    st.markdown("### 일별 순유입량 추이")

    fig_daily_net = px.line(
        daily_ts_df.sort_values("기준_날짜"),
        x="기준_날짜",
        y="순유입량",
        title="일별 순유입량 추이"
    )

    st.plotly_chart(fig_daily_net, width="stretch")

    st.divider()

    st.markdown("### 일별 불균형 규모 추이")

    fig_daily_imb = px.line(
        daily_ts_df.sort_values("기준_날짜"),
        x="기준_날짜",
        y="불균형_절댓값",
        title="일별 불균형 절댓값 합계 추이"
    )

    st.plotly_chart(fig_daily_imb, width="stretch")

    st.divider()

    st.markdown("### 월별 출발·종료 건수 추이")

    fig_monthly_flow = px.line(
        monthly_ts_df.sort_values("월"),
        x="월",
        y=["출발_건수", "종료_건수"],
        markers=True,
        title="월별 출발 건수와 종료 건수"
    )

    st.plotly_chart(fig_monthly_flow, width="stretch")

    st.divider()

    st.markdown("### 월별 순유입량 추이")

    fig_monthly_net = px.line(
        monthly_ts_df.sort_values("월"),
        x="월",
        y="순유입량",
        markers=True,
        title="월별 순유입량 추이"
    )

    st.plotly_chart(fig_monthly_net, width="stretch")

    st.divider()

    st.markdown("### 대여소별 출발·종료·순유입량 추이")

    station_list = sorted(daily_df["대여소명"].dropna().unique())

    selected_station = st.selectbox(
        "대여소 선택",
        station_list
    )

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
# 후보 분석
# =========================

elif view_mode == "후보 분석":
    st.subheader("수거·재배치 후보 분석")

    tab1, tab2, tab3 = st.tabs([
        "수거 후보",
        "재배치 후보",
        "불균형 TOP"
    ])

    with tab1:
        st.markdown("### 수거 후보: 순유입량이 큰 대여소")
        st.caption("종료 건수가 출발 건수보다 많아 자전거가 쌓이는 대여소입니다.")

        show_top_bar(pickup_df, "순유입량", "수거 후보 상위 대여소", "pickup_page")
        st.dataframe(pickup_df, width="stretch")

    with tab2:
        st.markdown("### 재배치 후보: 순유입량이 작은 대여소")
        st.caption("출발 건수가 종료 건수보다 많아 자전거가 부족해지는 대여소입니다.")

        show_bottom_bar(delivery_df, "순유입량", "재배치 후보 상위 대여소", "delivery_page")
        st.dataframe(delivery_df, width="stretch")

    with tab3:
        st.markdown("### 불균형 TOP")
        st.caption("수거 또는 재배치 필요성이 큰 대여소입니다.")

        show_top_bar(imbalance_df, "불균형_절댓값", "불균형 상위 대여소", "imbalance_page")
        st.dataframe(imbalance_df, width="stretch")
