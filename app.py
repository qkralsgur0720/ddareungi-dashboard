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

st.title("🚲 여의동 따릉이 수거·재배치 불균형 분석 대시보드")

st.markdown("""
이 대시보드는 여의동 따릉이 이용 데이터를 바탕으로 **출발 건수, 종료 건수, 순유입량, 불균형 정도**를 분석합니다.

- **출발 건수**: 해당 대여소에서 따릉이가 빠져나간 양
- **종료 건수**: 해당 대여소로 따릉이가 들어온 양
- **순유입량 = 종료 건수 - 출발 건수**
- **순유입량 > 0**: 자전거가 쌓이는 대여소 → 수거 후보
- **순유입량 < 0**: 자전거가 부족해지는 대여소 → 재배치 후보
- **불균형 절댓값 = |순유입량|**: 값이 클수록 우선 관리 필요
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
    pickup_top = pd.read_excel(FILE_NAME, sheet_name="수거후보_과잉_TOP")
    delivery_top = pd.read_excel(FILE_NAME, sheet_name="재배치후보_부족_TOP")
    imbalance_top = pd.read_excel(FILE_NAME, sheet_name="불균형_TOP")

    dfs = [daily, monthly, total, daily_ts, monthly_ts, pickup_top, delivery_top, imbalance_top]
    for df in dfs:
        df.columns = df.columns.str.strip()

    # 날짜/월 정리
    daily["기준_날짜"] = pd.to_datetime(daily["기준_날짜"], errors="coerce")
    daily_ts["기준_날짜"] = pd.to_datetime(daily_ts["기준_날짜"], errors="coerce")

    if "월" not in daily.columns:
        daily["월"] = daily["기준_날짜"].dt.to_period("M").astype(str)

    # 숫자형 정리
    numeric_cols = [
        "위도", "경도", "출발_건수", "종료_건수", "순유입량", "불균형_절댓값",
        "출발_이용_분", "종료_이용_분", "출발_이용_거리", "종료_이용_거리", "총이용량"
    ]

    for df in dfs:
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

    return daily, monthly, total, daily_ts, monthly_ts, pickup_top, delivery_top, imbalance_top


daily_df, monthly_df, total_df, daily_ts_df, monthly_ts_df, pickup_top_df, delivery_top_df, imbalance_top_df = load_data()

# =========================
# 공통 함수
# =========================

def format_num(value):
    try:
        return f"{int(value):,}"
    except Exception:
        return "0"


def get_manage_color(value):
    if value > 0:
        return "red"      # 수거 후보
    elif value < 0:
        return "blue"     # 재배치 후보
    return "gray"


def get_manage_label(value):
    if value > 0:
        return "수거 후보(과잉)"
    elif value < 0:
        return "재배치 후보(부족)"
    return "균형"


def make_balance_map(data, label_metric="순유입량", radius_metric="불균형_절댓값", zoom_start=15):
    map_data = data.dropna(subset=["위도", "경도"]).copy()

    if len(map_data) == 0:
        return None

    map_data["위도"] = pd.to_numeric(map_data["위도"], errors="coerce")
    map_data["경도"] = pd.to_numeric(map_data["경도"], errors="coerce")
    map_data["순유입량"] = pd.to_numeric(map_data["순유입량"], errors="coerce").fillna(0)
    map_data["불균형_절댓값"] = pd.to_numeric(map_data["불균형_절댓값"], errors="coerce").fillna(0)
    map_data["출발_건수"] = pd.to_numeric(map_data["출발_건수"], errors="coerce").fillna(0)
    map_data["종료_건수"] = pd.to_numeric(map_data["종료_건수"], errors="coerce").fillna(0)

    map_data = map_data.dropna(subset=["위도", "경도"])

    if len(map_data) == 0:
        return None

    center_lat = map_data["위도"].mean()
    center_lon = map_data["경도"].mean()

    m = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=zoom_start
    )

    max_value = map_data[radius_metric].max()

    for _, row in map_data.iterrows():
        lat = row["위도"]
        lon = row["경도"]
        net = int(row["순유입량"])
        imbalance = int(row["불균형_절댓값"])
        start_count = int(row["출발_건수"])
        end_count = int(row["종료_건수"])
        color = get_manage_color(net)
        manage_label = get_manage_label(net)

        if max_value > 0:
            radius = 5 + (row[radius_metric] / max_value) * 20
        else:
            radius = 6

        if label_metric == "순유입량":
            label_value = f"{net:+,}"
        else:
            label_value = f"{int(row[label_metric]):,}"

        folium.CircleMarker(
            location=[lat, lon],
            radius=radius,
            color=color,
            fill=True,
            fill_color=color,
            fill_opacity=0.55,
            popup=folium.Popup(
                f"""
                <b>대여소 ID:</b> {row['대여소_ID']}<br>
                <b>대여소명:</b> {row['대여소명']}<br>
                <b>관리유형:</b> {manage_label}<br>
                <b>출발 건수:</b> {start_count:,}건<br>
                <b>종료 건수:</b> {end_count:,}건<br>
                <b>순유입량:</b> {net:+,}건<br>
                <b>불균형 절댓값:</b> {imbalance:,}건<br>
                <b>주소1:</b> {row.get('주소1', '')}<br>
                <b>주소2:</b> {row.get('주소2', '')}
                """,
                max_width=330
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
                    background-color: rgba(255,255,255,0.78);
                    border: 1px solid gray;
                    border-radius: 4px;
                    padding: 1px 3px;
                    white-space: nowrap;
                    transform: translate(7px, -7px);
                ">
                    {label_value}
                </div>
                """
            )
        ).add_to(m)

    return m


def show_metrics(data, label="전체"):
    total_start = int(data["출발_건수"].sum())
    total_end = int(data["종료_건수"].sum())
    net_total = int(total_end - total_start)
    imbalance_sum = int(data["불균형_절댓값"].sum())
    station_count = data["대여소_ID"].nunique()

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric(f"{label} 출발 건수", f"{total_start:,}건")
    col2.metric(f"{label} 종료 건수", f"{total_end:,}건")
    col3.metric("순유입량", f"{net_total:+,}건")
    col4.metric("불균형 합계", f"{imbalance_sum:,}건")
    col5.metric("대여소 수", f"{station_count:,}개")


def show_top_bar(data, metric, title, key_prefix, top_n_default=10):
    top_n = st.slider(
        "상위 몇 개 대여소를 볼까요?",
        min_value=5,
        max_value=30,
        value=top_n_default,
        key=f"{key_prefix}_{metric}_top_n"
    )

    top_data = data.sort_values(metric, ascending=False).head(top_n).copy()

    fig = px.bar(
        top_data,
        x=metric,
        y="대여소명",
        orientation="h",
        text=metric,
        title=title
    )
    fig.update_layout(yaxis={"categoryorder": "total ascending"})
    st.plotly_chart(fig, width="stretch")


def show_signed_bar(data, title, key_prefix, top_n_default=15):
    top_n = st.slider(
        "상위 몇 개 대여소를 볼까요?",
        min_value=5,
        max_value=30,
        value=top_n_default,
        key=f"{key_prefix}_signed_top_n"
    )

    plot_data = data.copy()
    plot_data = plot_data.reindex(plot_data["순유입량"].abs().sort_values(ascending=False).index).head(top_n)

    fig = px.bar(
        plot_data,
        x="순유입량",
        y="대여소명",
        orientation="h",
        text="순유입량",
        color="관리유형",
        title=title
    )
    fig.update_layout(yaxis={"categoryorder": "total ascending"})
    st.plotly_chart(fig, width="stretch")


def show_data_table(data):
    display_cols = [
        "대여소_ID", "대여소명", "주소1", "주소2", "출발_건수", "종료_건수",
        "순유입량", "불균형_절댓값", "관리유형", "위도", "경도"
    ]
    cols = [c for c in display_cols if c in data.columns]
    st.dataframe(data[cols], width="stretch")

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

    st.subheader("전체 기간 기준 수거·재배치 불균형 분석")
    show_metrics(data, "전체 기간")

    st.divider()

    st.subheader("전체 기간 불균형 지도")
    st.caption("빨간색은 수거 후보(과잉), 파란색은 재배치 후보(부족)입니다. 원 크기는 불균형 절댓값 기준입니다.")
    m = make_balance_map(data, label_metric="순유입량", radius_metric="불균형_절댓값")
    if m is not None:
        st_folium(m, width=None, height=650)
    else:
        st.warning("지도에 표시할 위도/경도 데이터가 없습니다.")

    st.divider()

    tab1, tab2, tab3 = st.tabs(["불균형 TOP", "수거 후보", "재배치 후보"])

    with tab1:
        show_top_bar(data, "불균형_절댓값", "전체 기간 불균형 절댓값 상위 대여소", "total_imbalance")
        show_data_table(data.sort_values("불균형_절댓값", ascending=False))

    with tab2:
        pickup = data[data["순유입량"] > 0].sort_values("순유입량", ascending=False)
        show_top_bar(pickup, "순유입량", "수거 후보: 순유입량 상위 대여소", "total_pickup")
        show_data_table(pickup)

    with tab3:
        delivery = data[data["순유입량"] < 0].copy()
        delivery["부족량"] = delivery["순유입량"].abs()
        delivery = delivery.sort_values("부족량", ascending=False)
        show_top_bar(delivery, "부족량", "재배치 후보: 부족량 상위 대여소", "total_delivery")
        show_data_table(delivery)

# =========================
# 월별 화면
# =========================

elif view_mode == "월별":
    st.subheader("월별 수거·재배치 불균형 분석")

    months = sorted(monthly_df["월"].dropna().unique())
    selected_month = st.sidebar.selectbox("월 선택", months)

    data = monthly_df[monthly_df["월"] == selected_month].copy()

    show_metrics(data, selected_month)

    st.divider()

    st.subheader(f"{selected_month} 불균형 지도")
    m = make_balance_map(data, label_metric="순유입량", radius_metric="불균형_절댓값")
    if m is not None:
        st_folium(m, width=None, height=650)
    else:
        st.warning("지도에 표시할 위도/경도 데이터가 없습니다.")

    st.divider()

    tab1, tab2, tab3 = st.tabs(["월별 불균형 TOP", "월별 수거 후보", "월별 재배치 후보"])

    with tab1:
        show_top_bar(data, "불균형_절댓값", f"{selected_month} 불균형 절댓값 상위 대여소", "monthly_imbalance")
        show_data_table(data.sort_values("불균형_절댓값", ascending=False))

    with tab2:
        pickup = data[data["순유입량"] > 0].sort_values("순유입량", ascending=False)
        show_top_bar(pickup, "순유입량", f"{selected_month} 수거 후보 상위 대여소", "monthly_pickup")
        show_data_table(pickup)

    with tab3:
        delivery = data[data["순유입량"] < 0].copy()
        delivery["부족량"] = delivery["순유입량"].abs()
        delivery = delivery.sort_values("부족량", ascending=False)
        show_top_bar(delivery, "부족량", f"{selected_month} 재배치 후보 상위 대여소", "monthly_delivery")
        show_data_table(delivery)

# =========================
# 날짜별 화면
# =========================

elif view_mode == "날짜별":
    st.subheader("날짜별 수거·재배치 불균형 분석")

    available_dates = sorted(daily_df["기준_날짜"].dropna().dt.date.unique())
    selected_date = st.sidebar.selectbox("날짜 선택", available_dates)

    data = daily_df[daily_df["기준_날짜"].dt.date == selected_date].copy()

    show_metrics(data, str(selected_date))

    st.divider()

    st.subheader(f"{selected_date} 불균형 지도")
    m = make_balance_map(data, label_metric="순유입량", radius_metric="불균형_절댓값")
    if m is not None:
        st_folium(m, width=None, height=650)
    else:
        st.warning("지도에 표시할 위도/경도 데이터가 없습니다.")

    st.divider()

    tab1, tab2, tab3 = st.tabs(["일별 불균형 TOP", "일별 수거 후보", "일별 재배치 후보"])

    with tab1:
        show_top_bar(data, "불균형_절댓값", f"{selected_date} 불균형 절댓값 상위 대여소", "daily_imbalance")
        show_data_table(data.sort_values("불균형_절댓값", ascending=False))

    with tab2:
        pickup = data[data["순유입량"] > 0].sort_values("순유입량", ascending=False)
        show_top_bar(pickup, "순유입량", f"{selected_date} 수거 후보 상위 대여소", "daily_pickup")
        show_data_table(pickup)

    with tab3:
        delivery = data[data["순유입량"] < 0].copy()
        delivery["부족량"] = delivery["순유입량"].abs()
        delivery = delivery.sort_values("부족량", ascending=False)
        show_top_bar(delivery, "부족량", f"{selected_date} 재배치 후보 상위 대여소", "daily_delivery")
        show_data_table(delivery)

# =========================
# 시계열 분석 화면
# =========================

elif view_mode == "시계열 분석":
    st.subheader("시계열 분석")

    daily_ts = daily_ts_df.copy().sort_values("기준_날짜")
    monthly_ts = monthly_ts_df.copy().sort_values("월")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("전체 출발 건수", f"{int(daily_ts['출발_건수'].sum()):,}건")
    col2.metric("전체 종료 건수", f"{int(daily_ts['종료_건수'].sum()):,}건")
    col3.metric("전체 순유입량", f"{int(daily_ts['순유입량'].sum()):+,}건")
    col4.metric("전체 불균형 합계", f"{int(daily_ts['불균형_절댓값'].sum()):,}건")

    st.divider()

    st.markdown("### 일별 출발·종료 건수 추이")
    fig_daily_flow = px.line(
        daily_ts,
        x="기준_날짜",
        y=["출발_건수", "종료_건수"],
        title="여의동 일별 출발·종료 건수 추이"
    )
    st.plotly_chart(fig_daily_flow, width="stretch")

    st.markdown("### 일별 순유입량 추이")
    fig_daily_net = px.bar(
        daily_ts,
        x="기준_날짜",
        y="순유입량",
        title="여의동 일별 순유입량(종료-출발)"
    )
    st.plotly_chart(fig_daily_net, width="stretch")

    st.markdown("### 일별 불균형 합계 추이")
    fig_daily_imb = px.line(
        daily_ts,
        x="기준_날짜",
        y="불균형_절댓값",
        title="여의동 일별 불균형 절댓값 합계"
    )
    st.plotly_chart(fig_daily_imb, width="stretch")

    st.divider()

    st.markdown("### 월별 출발·종료 건수 추이")
    fig_monthly_flow = px.line(
        monthly_ts,
        x="월",
        y=["출발_건수", "종료_건수"],
        markers=True,
        title="여의동 월별 출발·종료 건수 추이"
    )
    st.plotly_chart(fig_monthly_flow, width="stretch")

    st.markdown("### 월별 불균형 합계")
    fig_monthly_imb = px.bar(
        monthly_ts,
        x="월",
        y="불균형_절댓값",
        title="여의동 월별 불균형 절댓값 합계"
    )
    st.plotly_chart(fig_monthly_imb, width="stretch")

    st.divider()

    st.markdown("### 대여소별 일별 불균형 추이")
    station_list = sorted(daily_df["대여소명"].dropna().unique())
    selected_station = st.selectbox("대여소 선택", station_list)

    station_data = daily_df[daily_df["대여소명"] == selected_station].sort_values("기준_날짜").copy()

    fig_station = px.line(
        station_data,
        x="기준_날짜",
        y=["출발_건수", "종료_건수", "불균형_절댓값"],
        title=f"{selected_station} 일별 출발·종료·불균형 추이"
    )
    st.plotly_chart(fig_station, width="stretch")

    st.markdown("### 시계열 요약 데이터")
    st.dataframe(daily_ts, width="stretch")

# =========================
# 후보 분석 화면
# =========================

elif view_mode == "후보 분석":
    st.subheader("수거·재배치 후보 분석")

    st.markdown("""
    - **수거 후보**: 순유입량이 큰 대여소. 반납이 대여보다 많아 자전거가 쌓이는 지점입니다.
    - **재배치 후보**: 순유입량이 음수이고 부족량이 큰 대여소. 대여가 반납보다 많아 자전거가 부족해지는 지점입니다.
    - **불균형 TOP**: 수거/재배치 방향과 관계없이 불균형 절댓값이 큰 지점입니다.
    """)

    tab1, tab2, tab3 = st.tabs(["수거 후보", "재배치 후보", "불균형 TOP"])

    with tab1:
        data = pickup_top_df.copy().sort_values("순유입량", ascending=False)
        show_top_bar(data, "순유입량", "전체 기간 수거 후보 TOP", "candidate_pickup")
        m = make_balance_map(data.head(30), label_metric="순유입량", radius_metric="불균형_절댓값")
        if m is not None:
            st_folium(m, width=None, height=600)
        show_data_table(data)

    with tab2:
        data = delivery_top_df.copy()
        data["부족량"] = data["순유입량"].abs()
        data = data.sort_values("부족량", ascending=False)
        show_top_bar(data, "부족량", "전체 기간 재배치 후보 TOP", "candidate_delivery")
        m = make_balance_map(data.head(30), label_metric="순유입량", radius_metric="불균형_절댓값")
        if m is not None:
            st_folium(m, width=None, height=600)
        show_data_table(data)

    with tab3:
        data = imbalance_top_df.copy().sort_values("불균형_절댓값", ascending=False)
        show_signed_bar(data, "전체 기간 불균형 TOP: 수거/재배치 방향 포함", "candidate_imbalance")
        m = make_balance_map(data.head(30), label_metric="순유입량", radius_metric="불균형_절댓값")
        if m is not None:
            st_folium(m, width=None, height=600)
        show_data_table(data)
