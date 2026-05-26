import streamlit as st
import pandas as pd
import folium
import requests
import math
from datetime import datetime
from streamlit_folium import st_folium
import plotly.express as px

try:
    from ortools.constraint_solver import pywrapcp, routing_enums_pb2
    ORTOOLS_AVAILABLE = True
except Exception:
    ORTOOLS_AVAILABLE = False


# =========================
# 기본 설정
# =========================

st.set_page_config(
    page_title="여의동 따릉이 수거·재배치 분석",
    layout="wide"
)

st.title("🚲 여의동 따릉이 수거·재배치 분석 대시보드")

st.markdown("""
이 대시보드는 여의동 따릉이 이용 데이터를 바탕으로 **출발·종료 패턴, 수요 불균형, 실시간 수거·재배치 후보, 경로 추천**을 분석합니다.

- 과거 분석 기준: 2025년 여의동 따릉이 출발·종료 데이터
- 실시간 분석 기준: 서울시 공공자전거 실시간 대여정보 API
- 핵심 지표: `순유입량 = 종료_건수 - 출발_건수`
- 관리 기준: `불균형_절댓값 = |순유입량|`
- 실시간 판단 기준: 현재 자전거 수, 거치대 수, 현재 점유율
- 경로 추천 방식: 직선거리 휴리스틱 / OSRM 도로망 거리행렬 / OR-Tools VRP
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

    if "기준_날짜" in daily.columns:
        daily["기준_날짜"] = pd.to_datetime(daily["기준_날짜"], errors="coerce")

    if "기준_날짜" in daily_ts.columns:
        daily_ts["기준_날짜"] = pd.to_datetime(daily_ts["기준_날짜"], errors="coerce")

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

        for col in ["출발_건수", "종료_건수", "순유입량", "불균형_절댓값", "총이용량"]:
            if col in df.columns:
                df[col] = df[col].fillna(0)

    for df in [daily, monthly, total, pickup, delivery, imbalance]:
        if "대여소_ID" in df.columns:
            df["매칭_ID"] = (
                df["대여소_ID"]
                .astype(str)
                .str.replace("ST-", "", regex=False)
                .str.strip()
            )

    return daily, monthly, total, daily_ts, monthly_ts, pickup, delivery, imbalance


daily_df, monthly_df, total_df, daily_ts_df, monthly_ts_df, pickup_df, delivery_df, imbalance_df = load_data()


# =========================
# 세션 상태 초기화
# =========================

session_keys = [
    "realtime_decision_df",
    "realtime_route_df",
    "realtime_vrp_input_df",
    "realtime_loaded_time",
    "realtime_depot_lat",
    "realtime_depot_lon",
    "realtime_method_used"
]

for key in session_keys:
    if key not in st.session_state:
        st.session_state[key] = None


# =========================
# 공통 함수
# =========================

def format_int(x):
    try:
        return f"{int(round(float(x))):,}"
    except Exception:
        return "0"


def get_station_col(df):
    if "대여소명" in df.columns:
        return "대여소명"
    if "실시간_대여소명" in df.columns:
        return "실시간_대여소명"
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
# 실시간 API 함수
# =========================

@st.cache_data(ttl=60)
def fetch_realtime_bike_data(api_key):
    all_rows = []
    start = 1
    step = 1000

    while True:
        end = start + step - 1
        url = f"http://openapi.seoul.go.kr:8088/{api_key}/json/bikeList/{start}/{end}/"

        try:
            response = requests.get(url, timeout=20)
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            return pd.DataFrame(), {"error": str(e), "url": url}

        if "rentBikeStatus" not in data:
            return pd.DataFrame(), data

        status = data["rentBikeStatus"]
        result = status.get("RESULT", {})
        code = result.get("CODE", "")

        if code and code != "INFO-000":
            return pd.DataFrame(), data

        rows = status.get("row", [])
        total_count = int(status.get("list_total_count", len(rows)))

        all_rows.extend(rows)

        if end >= total_count or len(rows) == 0:
            break

        start += step

    df = pd.DataFrame(all_rows)

    if len(df) == 0:
        return df, {}

    df.columns = df.columns.str.strip()

    rename_map = {
        "rackTotCnt": "거치대수",
        "stationName": "실시간_대여소명",
        "parkingBikeTotCnt": "현재자전거수",
        "shared": "API_거치율",
        "stationLatitude": "위도",
        "stationLongitude": "경도",
        "stationId": "대여소_ID"
    }

    df = df.rename(columns=rename_map)

    required_cols = ["거치대수", "실시간_대여소명", "현재자전거수", "API_거치율", "위도", "경도", "대여소_ID"]

    for col in required_cols:
        if col not in df.columns:
            df[col] = None

    for col in ["거치대수", "현재자전거수", "API_거치율", "위도", "경도"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["대여소_ID"] = df["대여소_ID"].astype(str).str.strip()

    df["매칭_ID"] = (
        df["대여소_ID"]
        .astype(str)
        .str.replace("ST-", "", regex=False)
        .str.strip()
    )

    return df, {}


def get_historical_month_data(monthly_df, selected_month_number):
    month_str = f"{selected_month_number:02d}"

    temp = monthly_df.copy()
    temp["월"] = temp["월"].astype(str)

    month_data = temp[temp["월"].str[-2:] == month_str].copy()

    if len(month_data) == 0:
        month_data = total_df.copy()

    return month_data


def prepare_realtime_decision_data(
    realtime_df,
    historical_df,
    target_rate,
    pickup_rate,
    delivery_rate,
    realtime_weight,
    history_weight
):
    if len(realtime_df) == 0:
        return pd.DataFrame()

    hist = historical_df.copy()

    if "매칭_ID" not in hist.columns:
        hist["매칭_ID"] = (
            hist["대여소_ID"]
            .astype(str)
            .str.replace("ST-", "", regex=False)
            .str.strip()
        )

    hist_cols = [
        "매칭_ID", "대여소명", "출발_건수", "종료_건수",
        "순유입량", "불균형_절댓값", "관리유형", "주소1", "주소2"
    ]

    hist_cols = [col for col in hist_cols if col in hist.columns]

    merged = pd.merge(
        realtime_df,
        hist[hist_cols],
        on="매칭_ID",
        how="inner"
    )

    if len(merged) == 0:
        merged = realtime_df[
            realtime_df["실시간_대여소명"].astype(str).str.contains("여의", na=False)
        ].copy()

    if len(merged) == 0:
        return pd.DataFrame()

    merged["거치대수"] = pd.to_numeric(merged["거치대수"], errors="coerce").fillna(0)
    merged["현재자전거수"] = pd.to_numeric(merged["현재자전거수"], errors="coerce").fillna(0)
    merged["API_거치율"] = pd.to_numeric(merged["API_거치율"], errors="coerce").fillna(0)
    merged["위도"] = pd.to_numeric(merged["위도"], errors="coerce")
    merged["경도"] = pd.to_numeric(merged["경도"], errors="coerce")

    merged = merged[(merged["거치대수"] > 0) & merged["위도"].notna() & merged["경도"].notna()].copy()

    if len(merged) == 0:
        return pd.DataFrame()

    merged["현재점유율"] = merged["현재자전거수"] / merged["거치대수"]
    merged["목표자전거수"] = (merged["거치대수"] * target_rate).round().astype(int)

    merged["수거필요대수"] = (
        merged["현재자전거수"] - merged["목표자전거수"]
    ).clip(lower=0).round().astype(int)

    merged["재배치필요대수"] = (
        merged["목표자전거수"] - merged["현재자전거수"]
    ).clip(lower=0).round().astype(int)

    merged["실시간관리유형"] = "정상"

    merged.loc[
        (merged["현재점유율"] >= pickup_rate) & (merged["수거필요대수"] > 0),
        "실시간관리유형"
    ] = "수거 후보"

    merged.loc[
        (merged["현재점유율"] <= delivery_rate) & (merged["재배치필요대수"] > 0),
        "실시간관리유형"
    ] = "재배치 후보"

    for col in ["순유입량", "불균형_절댓값", "출발_건수", "종료_건수"]:
        if col not in merged.columns:
            merged[col] = 0
        merged[col] = pd.to_numeric(merged[col], errors="coerce").fillna(0)

    max_pickup_hist = max(merged["순유입량"].clip(lower=0).max(), 1)
    max_delivery_hist = max((-merged["순유입량"].clip(upper=0)).max(), 1)
    max_pickup_need = max(merged["수거필요대수"].max(), 1)
    max_delivery_need = max(merged["재배치필요대수"].max(), 1)

    merged["과거수거가중치"] = merged["순유입량"].clip(lower=0) / max_pickup_hist
    merged["과거재배치가중치"] = (-merged["순유입량"].clip(upper=0)) / max_delivery_hist

    merged["실시간수거가중치"] = merged["수거필요대수"] / max_pickup_need
    merged["실시간재배치가중치"] = merged["재배치필요대수"] / max_delivery_need

    merged["수거우선점수"] = (
        realtime_weight * merged["실시간수거가중치"]
        + history_weight * merged["과거수거가중치"]
    )

    merged["재배치우선점수"] = (
        realtime_weight * merged["실시간재배치가중치"]
        + history_weight * merged["과거재배치가중치"]
    )

    return merged


# =========================
# 거리 및 경로 함수
# =========================

def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )

    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return r * c


def get_haversine_matrix(nodes):
    n = len(nodes)
    matrix = [[0 for _ in range(n)] for _ in range(n)]

    for i in range(n):
        for j in range(n):
            if i == j:
                matrix[i][j] = 0
            else:
                matrix[i][j] = int(
                    haversine_km(
                        nodes[i]["lat"], nodes[i]["lon"],
                        nodes[j]["lat"], nodes[j]["lon"]
                    ) * 1000
                )

    return matrix


@st.cache_data(ttl=300)
def get_osrm_matrix(nodes):
    if len(nodes) <= 1:
        return [[0]], [[0]], None

    if len(nodes) > 25:
        return None, None, "OSRM 공개 서버 안정성을 위해 후보 노드는 25개 이하로 제한하는 것이 좋습니다."

    coord_text = ";".join([f"{node['lon']},{node['lat']}" for node in nodes])

    url = (
        f"https://router.project-osrm.org/table/v1/driving/{coord_text}"
        f"?annotations=duration,distance"
    )

    try:
        response = requests.get(url, timeout=25)
        response.raise_for_status()
        data = response.json()

        if data.get("code") != "Ok":
            return None, None, data

        durations = data.get("durations")
        distances = data.get("distances")

        if durations is None or distances is None:
            return None, None, data

        duration_matrix = [
            [int(x) if x is not None else 10**7 for x in row]
            for row in durations
        ]

        distance_matrix = [
            [int(x) if x is not None else 10**7 for x in row]
            for row in distances
        ]

        return duration_matrix, distance_matrix, None

    except Exception as e:
        return None, None, str(e)


def build_vrp_nodes(decision_df, depot_lat, depot_lon, candidate_limit, vehicle_count, vehicle_capacity):
    pickup_df = decision_df[
        (decision_df["실시간관리유형"] == "수거 후보") &
        (decision_df["수거필요대수"] > 0)
    ].copy()

    delivery_df = decision_df[
        (decision_df["실시간관리유형"] == "재배치 후보") &
        (decision_df["재배치필요대수"] > 0)
    ].copy()

    pickup_df = pickup_df.sort_values("수거우선점수", ascending=False).head(candidate_limit)
    delivery_df = delivery_df.sort_values("재배치우선점수", ascending=False).head(candidate_limit)

    max_total_capacity = vehicle_count * vehicle_capacity

    pickup_total = int(pickup_df["수거필요대수"].sum())
    delivery_total = int(delivery_df["재배치필요대수"].sum())

    if pickup_total <= 0 or delivery_total <= 0:
        return [], pd.DataFrame()

    target_total = min(pickup_total, delivery_total, max_total_capacity)

    nodes = [{
        "node_index": 0,
        "node_type": "depot",
        "대여소_ID": "DEPOT",
        "대여소명": "차량 출발지",
        "demand": 0,
        "priority_score": 0,
        "lat": depot_lat,
        "lon": depot_lon
    }]

    remaining_pickup = target_total

    for _, row in pickup_df.iterrows():
        if remaining_pickup <= 0:
            break

        amount = int(min(row["수거필요대수"], remaining_pickup))

        if amount <= 0:
            continue

        nodes.append({
            "node_index": len(nodes),
            "node_type": "pickup",
            "대여소_ID": row["대여소_ID"],
            "대여소명": row["실시간_대여소명"],
            "demand": amount,
            "priority_score": float(row["수거우선점수"]),
            "lat": float(row["위도"]),
            "lon": float(row["경도"])
        })

        remaining_pickup -= amount

    remaining_delivery = target_total

    for _, row in delivery_df.iterrows():
        if remaining_delivery <= 0:
            break

        amount = int(min(row["재배치필요대수"], remaining_delivery))

        if amount <= 0:
            continue

        nodes.append({
            "node_index": len(nodes),
            "node_type": "delivery",
            "대여소_ID": row["대여소_ID"],
            "대여소명": row["실시간_대여소명"],
            "demand": -amount,
            "priority_score": float(row["재배치우선점수"]),
            "lat": float(row["위도"]),
            "lon": float(row["경도"])
        })

        remaining_delivery -= amount

    nodes_df = pd.DataFrame(nodes)

    return nodes, nodes_df


def solve_ortools_vrp(nodes, cost_matrix, vehicle_count, vehicle_capacity):
    if not ORTOOLS_AVAILABLE:
        return pd.DataFrame(), "OR-Tools가 설치되어 있지 않습니다. requirements.txt에 ortools를 추가하세요."

    if len(nodes) <= 1:
        return pd.DataFrame(), "VRP를 풀 후보 노드가 부족합니다."

    manager = pywrapcp.RoutingIndexManager(
        len(nodes),
        vehicle_count,
        0
    )

    routing = pywrapcp.RoutingModel(manager)

    def distance_callback(from_index, to_index):
        from_node = manager.IndexToNode(from_index)
        to_node = manager.IndexToNode(to_index)
        return int(cost_matrix[from_node][to_node])

    transit_callback_index = routing.RegisterTransitCallback(distance_callback)

    routing.SetArcCostEvaluatorOfAllVehicles(transit_callback_index)

    demands = [int(node["demand"]) for node in nodes]

    def demand_callback(from_index):
        from_node = manager.IndexToNode(from_index)
        return demands[from_node]

    demand_callback_index = routing.RegisterUnaryTransitCallback(demand_callback)

    routing.AddDimensionWithVehicleCapacity(
        demand_callback_index,
        0,
        [vehicle_capacity] * vehicle_count,
        True,
        "Capacity"
    )

    capacity_dimension = routing.GetDimensionOrDie("Capacity")

    for node_idx, node in enumerate(nodes):
        if node_idx == 0:
            continue

        index = manager.NodeToIndex(node_idx)
        routing.AddDisjunction([index], 1_000_000)

    search_parameters = pywrapcp.DefaultRoutingSearchParameters()
    search_parameters.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    )
    search_parameters.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    )
    search_parameters.time_limit.seconds = 10

    solution = routing.SolveWithParameters(search_parameters)

    if solution is None:
        return pd.DataFrame(), "VRP 해를 찾지 못했습니다. 후보 수나 차량 용량을 조정해보세요."

    route_rows = []

    for vehicle_id in range(vehicle_count):
        index = routing.Start(vehicle_id)
        order = 1
        cumulative_cost = 0

        while not routing.IsEnd(index):
            node_id = manager.IndexToNode(index)
            next_index = solution.Value(routing.NextVar(index))

            if node_id != 0:
                node = nodes[node_id]
                load_after = solution.Value(capacity_dimension.CumulVar(index))

                if node["node_type"] == "pickup":
                    work = "수거"
                    amount = int(node["demand"])
                elif node["node_type"] == "delivery":
                    work = "재배치"
                    amount = abs(int(node["demand"]))
                else:
                    work = "출발지"
                    amount = 0

                route_rows.append({
                    "차량": vehicle_id + 1,
                    "순서": order,
                    "작업": work,
                    "대여소_ID": node["대여소_ID"],
                    "대여소명": node["대여소명"],
                    "작업대수": amount,
                    "방문후_차량적재량": load_after,
                    "위도": node["lat"],
                    "경도": node["lon"],
                    "누적비용": cumulative_cost
                })

                order += 1

            if not routing.IsEnd(next_index):
                from_node = manager.IndexToNode(index)
                to_node = manager.IndexToNode(next_index)
                cumulative_cost += cost_matrix[from_node][to_node]

            index = next_index

    route_df = pd.DataFrame(route_rows)

    if len(route_df) == 0:
        return pd.DataFrame(), "방문 노드가 없습니다. 후보 수나 제약조건을 조정해보세요."

    return route_df, None


def recommend_simple_route(decision_df, depot_lat, depot_lon, vehicle_capacity, max_stops, use_osrm=False):
    pickup_df = decision_df[
        (decision_df["실시간관리유형"] == "수거 후보") &
        (decision_df["수거필요대수"] > 0)
    ].copy()

    delivery_df = decision_df[
        (decision_df["실시간관리유형"] == "재배치 후보") &
        (decision_df["재배치필요대수"] > 0)
    ].copy()

    if len(pickup_df) == 0 or len(delivery_df) == 0:
        return pd.DataFrame(), None

    pickup_df["남은수거량"] = pickup_df["수거필요대수"]
    delivery_df["남은배치량"] = delivery_df["재배치필요대수"]

    route = []
    current_lat = depot_lat
    current_lon = depot_lon
    load = 0
    total_distance = 0
    step_no = 1

    while step_no <= max_stops:
        if load == 0:
            candidates = pickup_df[pickup_df["남은수거량"] > 0].copy()

            if len(candidates) == 0:
                break

            candidates["거리_km"] = candidates.apply(
                lambda row: haversine_km(current_lat, current_lon, row["위도"], row["경도"]),
                axis=1
            )

            candidates["선택점수"] = candidates["수거우선점수"] / (candidates["거리_km"] + 0.2)
            selected = candidates.sort_values("선택점수", ascending=False).iloc[0]

            amount = int(min(selected["남은수거량"], vehicle_capacity - load))

            if amount <= 0:
                break

            distance = float(selected["거리_km"])
            total_distance += distance

            route.append({
                "차량": 1,
                "순서": step_no,
                "작업": "수거",
                "대여소_ID": selected["대여소_ID"],
                "대여소명": selected.get("실시간_대여소명", selected.get("대여소명", "")),
                "작업대수": amount,
                "방문후_차량적재량": load + amount,
                "이동거리_km": round(distance, 3),
                "누적거리_km": round(total_distance, 3),
                "위도": selected["위도"],
                "경도": selected["경도"]
            })

            pickup_df.loc[pickup_df["대여소_ID"] == selected["대여소_ID"], "남은수거량"] -= amount

            load += amount
            current_lat = selected["위도"]
            current_lon = selected["경도"]
            step_no += 1

        else:
            candidates = delivery_df[delivery_df["남은배치량"] > 0].copy()

            if len(candidates) == 0:
                break

            candidates["거리_km"] = candidates.apply(
                lambda row: haversine_km(current_lat, current_lon, row["위도"], row["경도"]),
                axis=1
            )

            candidates["선택점수"] = candidates["재배치우선점수"] / (candidates["거리_km"] + 0.2)
            selected = candidates.sort_values("선택점수", ascending=False).iloc[0]

            amount = int(min(selected["남은배치량"], load))

            if amount <= 0:
                break

            distance = float(selected["거리_km"])
            total_distance += distance

            route.append({
                "차량": 1,
                "순서": step_no,
                "작업": "재배치",
                "대여소_ID": selected["대여소_ID"],
                "대여소명": selected.get("실시간_대여소명", selected.get("대여소명", "")),
                "작업대수": amount,
                "방문후_차량적재량": load - amount,
                "이동거리_km": round(distance, 3),
                "누적거리_km": round(total_distance, 3),
                "위도": selected["위도"],
                "경도": selected["경도"]
            })

            delivery_df.loc[delivery_df["대여소_ID"] == selected["대여소_ID"], "남은배치량"] -= amount

            load -= amount
            current_lat = selected["위도"]
            current_lon = selected["경도"]
            step_no += 1

    route_df = pd.DataFrame(route)

    return route_df, None


def make_realtime_status_map(decision_df):
    map_data = decision_df.dropna(subset=["위도", "경도"]).copy()

    if len(map_data) == 0:
        return None

    center_lat = map_data["위도"].mean()
    center_lon = map_data["경도"].mean()

    m = folium.Map(location=[center_lat, center_lon], zoom_start=15)

    for _, row in map_data.iterrows():
        status = row["실시간관리유형"]

        if status == "수거 후보":
            color = "red"
            label = f"수거 {int(row['수거필요대수'])}"
        elif status == "재배치 후보":
            color = "blue"
            label = f"배치 {int(row['재배치필요대수'])}"
        else:
            color = "gray"
            label = f"{int(row['현재자전거수'])}대"

        popup_text = f"""
        <b>대여소 ID:</b> {row.get('대여소_ID', '')}<br>
        <b>대여소명:</b> {row.get('실시간_대여소명', '')}<br>
        <b>현재 자전거 수:</b> {int(row.get('현재자전거수', 0))}대<br>
        <b>거치대 수:</b> {int(row.get('거치대수', 0))}개<br>
        <b>API 거치율:</b> {row.get('API_거치율', 0):.1f}%<br>
        <b>계산 점유율:</b> {row.get('현재점유율', 0):.1%}<br>
        <b>목표 자전거 수:</b> {int(row.get('목표자전거수', 0))}대<br>
        <b>수거 필요 대수:</b> {int(row.get('수거필요대수', 0))}대<br>
        <b>재배치 필요 대수:</b> {int(row.get('재배치필요대수', 0))}대<br>
        <b>실시간 관리유형:</b> {status}<br>
        <b>수거 우선점수:</b> {row.get('수거우선점수', 0):.3f}<br>
        <b>재배치 우선점수:</b> {row.get('재배치우선점수', 0):.3f}
        """

        radius = 7

        if status == "수거 후보":
            radius = 7 + min(row["수거필요대수"], 20) * 0.5
        elif status == "재배치 후보":
            radius = 7 + min(row["재배치필요대수"], 20) * 0.5

        folium.CircleMarker(
            location=[row["위도"], row["경도"]],
            radius=radius,
            color=color,
            fill=True,
            fill_color=color,
            fill_opacity=0.65,
            popup=folium.Popup(popup_text, max_width=350)
        ).add_to(m)

        folium.Marker(
            location=[row["위도"], row["경도"]],
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


def make_route_map(route_df, depot_lat, depot_lon):
    if route_df is None or len(route_df) == 0:
        return None

    center_lat = route_df["위도"].mean()
    center_lon = route_df["경도"].mean()

    m = folium.Map(location=[center_lat, center_lon], zoom_start=15)

    folium.Marker(
        location=[depot_lat, depot_lon],
        tooltip="차량 출발지",
        popup="차량 출발지",
        icon=folium.Icon(color="green", icon="home")
    ).add_to(m)

    vehicle_groups = route_df.groupby("차량") if "차량" in route_df.columns else [(1, route_df)]

    route_colors = ["purple", "orange", "darkred", "cadetblue", "darkgreen", "black"]

    for vehicle_id, group in vehicle_groups:
        group = group.sort_values("순서")
        points = [[depot_lat, depot_lon]]
        color_line = route_colors[(int(vehicle_id) - 1) % len(route_colors)]

        for _, row in group.iterrows():
            if row["작업"] == "수거":
                color = "red"
                icon = "arrow-up"
            else:
                color = "blue"
                icon = "arrow-down"

            points.append([row["위도"], row["경도"]])

            popup_text = f"""
            <b>차량 {int(row.get('차량', 1))} - {int(row['순서'])}. {row['작업']}</b><br>
            <b>대여소:</b> {row['대여소명']}<br>
            <b>작업 대수:</b> {int(row['작업대수'])}대<br>
            <b>방문 후 차량 적재량:</b> {int(row.get('방문후_차량적재량', 0))}대<br>
            """

            if "이동거리_km" in row:
                popup_text += f"<b>이동거리:</b> {row.get('이동거리_km', '')} km<br>"
            if "누적거리_km" in row:
                popup_text += f"<b>누적거리:</b> {row.get('누적거리_km', '')} km<br>"

            folium.Marker(
                location=[row["위도"], row["경도"]],
                tooltip=f"차량 {int(row.get('차량', 1))} - {int(row['순서'])}. {row['작업']} {int(row['작업대수'])}대",
                popup=folium.Popup(popup_text, max_width=350),
                icon=folium.Icon(color=color, icon=icon)
            ).add_to(m)

            folium.Marker(
                location=[row["위도"], row["경도"]],
                icon=folium.DivIcon(
                    html=f"""
                    <div style="
                        font-size: 11px;
                        font-weight: bold;
                        color: #111111;
                        background-color: rgba(255,255,255,0.92);
                        border: 1px solid #333333;
                        border-radius: 5px;
                        padding: 1px 5px;
                        white-space: nowrap;
                        box-shadow: 1px 1px 2px rgba(0,0,0,0.25);
                        transform: translate(8px, -8px);
                    ">
                        차량 {int(row.get('차량', 1))}-{int(row['순서'])}. {row['작업']} {int(row['작업대수'])}대
                    </div>
                    """
                )
            ).add_to(m)

        folium.PolyLine(
            points,
            color=color_line,
            weight=4,
            opacity=0.75
        ).add_to(m)

    return m


def make_vrp_input(decision_df):
    rows = []

    for _, row in decision_df.iterrows():
        if row["실시간관리유형"] == "수거 후보" and row["수거필요대수"] > 0:
            rows.append({
                "node_type": "pickup",
                "대여소_ID": row["대여소_ID"],
                "대여소명": row["실시간_대여소명"],
                "demand": int(row["수거필요대수"]),
                "priority_score": round(row["수거우선점수"], 4),
                "위도": row["위도"],
                "경도": row["경도"]
            })

        elif row["실시간관리유형"] == "재배치 후보" and row["재배치필요대수"] > 0:
            rows.append({
                "node_type": "delivery",
                "대여소_ID": row["대여소_ID"],
                "대여소명": row["실시간_대여소명"],
                "demand": -int(row["재배치필요대수"]),
                "priority_score": round(row["재배치우선점수"], 4),
                "위도": row["위도"],
                "경도": row["경도"]
            })

    return pd.DataFrame(rows)


# =========================
# 사이드바
# =========================

st.sidebar.header("필터")

view_mode = st.sidebar.radio(
    "분석 기준 선택",
    ["전체 기간", "월별", "날짜별", "시계열 분석", "후보 분석", "실시간 경로 추천"]
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


# =========================
# 실시간 경로 추천 화면
# =========================

elif view_mode == "실시간 경로 추천":
    st.subheader("실시간 따릉이 현황 기반 수거·재배치 경로 추천")

    st.markdown("""
    이 화면은 서울시 공공자전거 실시간 대여정보 API를 활용하여 현재 여의동 대여소의 자전거 수를 불러오고,
    운영 시나리오 값에 따라 수거 후보와 재배치 후보를 판단합니다.
    """)

    with st.expander("지표 설명 보기", expanded=True):
        st.markdown("""
        **현재 점유율**은 실시간 API의 `현재 자전거 수 / 거치대 수`로 계산됩니다.  
        예를 들어 현재 자전거 수가 68대이고 거치대 수가 22개라면 점유율은 약 309%입니다.  
        이는 오류가 아니라 거치대 수보다 훨씬 많은 자전거가 몰려 있다는 뜻입니다.

        **월별 출발 건수는 점유율이 아닙니다.**  
        월별 출발 건수는 일정 기간 동안 해당 대여소에서 자전거가 빠져나간 횟수입니다.  
        따라서 출발 건수가 많은 대여소는 반복적으로 자전거가 부족해질 가능성이 있고,
        종료 건수가 많은 대여소는 반복적으로 자전거가 쌓일 가능성이 있습니다.

        이 대시보드는 다음 두 정보를 결합합니다.

        1. **실시간 상태**: 현재 자전거 수, 거치대 수, 현재 점유율  
        2. **과거 패턴**: 2025년 같은 월의 출발·종료 불균형

        즉, 단순히 지금 많은 곳/적은 곳만 보는 것이 아니라,
        과거에도 반복적으로 과잉·부족이 발생했는지를 함께 반영하여 우선순위를 계산합니다.
        """)

    st.divider()

    st.markdown("## 1. 운영 시나리오 설정")

    with st.expander("운영 조건 입력", expanded=True):
        col1, col2, col3 = st.columns(3)

        with col1:
            vehicle_count = st.number_input("투입 차량 수", min_value=1, max_value=10, value=1, step=1)
            vehicle_capacity = st.number_input("차량 1대당 적재 가능 대수", min_value=1, max_value=100, value=20, step=1)
            max_stops = st.number_input("휴리스틱 추천 경로 최대 방문 지점 수", min_value=2, max_value=30, value=10, step=1)

        with col2:
            target_rate = st.slider("목표 점유율", min_value=0.1, max_value=0.9, value=0.5, step=0.05)
            pickup_rate = st.slider("수거 기준 점유율", min_value=0.5, max_value=1.0, value=0.8, step=0.05)
            delivery_rate = st.slider("재배치 기준 점유율", min_value=0.0, max_value=0.5, value=0.2, step=0.05)

        with col3:
            realtime_weight = st.slider("실시간 상태 가중치", min_value=0.0, max_value=1.0, value=0.7, step=0.1)
            history_weight = round(1 - realtime_weight, 2)
            st.metric("과거 패턴 가중치", history_weight)

            selected_month_number = st.selectbox(
                "반영할 과거 월별 패턴",
                list(range(1, 13)),
                index=datetime.now().month - 1
            )

    if pickup_rate <= target_rate:
        st.warning("수거 기준 점유율은 목표 점유율보다 높게 설정하는 것이 좋습니다.")

    if delivery_rate >= target_rate:
        st.warning("재배치 기준 점유율은 목표 점유율보다 낮게 설정하는 것이 좋습니다.")

    st.divider()

    st.markdown("## 2. 경로 추천 방식 설정")

    route_method = st.radio(
        "경로 추천 방식",
        [
            "직선거리 휴리스틱",
            "OSRM 도로망 거리 + OR-Tools VRP",
            "직선거리 거리행렬 + OR-Tools VRP"
        ]
    )

    candidate_limit = st.slider(
        "VRP 후보 대여소 수 제한",
        min_value=3,
        max_value=25,
        value=12,
        step=1
    )

    if route_method == "OSRM 도로망 거리 + OR-Tools VRP":
        st.info(
            "OSRM은 별도 인증키 없이 테스트 가능하지만, 공개 서버는 대량 호출용이 아닙니다. "
            "후보 수를 너무 크게 설정하지 않는 것이 좋습니다."
        )

    if "OR-Tools" in route_method and not ORTOOLS_AVAILABLE:
        st.warning("OR-Tools가 설치되어 있지 않습니다. requirements.txt에 ortools를 추가해야 합니다.")

    st.divider()

    st.markdown("## 3. 실시간 API 설정")

    try:
        api_key = st.secrets["SEOUL_API_KEY"]
        st.success("Streamlit Secrets에서 서울시 API 인증키를 불러왔습니다.")
    except Exception:
        api_key = st.text_input("서울시 OpenAPI 인증키 입력", type="password")
        st.info("배포용으로는 Streamlit Secrets에 SEOUL_API_KEY를 저장하는 것을 권장합니다.")

    st.divider()

    st.markdown("## 4. 차량 출발지 설정")

    center_lat_default = float(total_df["위도"].dropna().mean())
    center_lon_default = float(total_df["경도"].dropna().mean())

    col1, col2 = st.columns(2)

    with col1:
        depot_lat = st.number_input("차량 출발지 위도", value=center_lat_default, format="%.8f")

    with col2:
        depot_lon = st.number_input("차량 출발지 경도", value=center_lon_default, format="%.8f")

    col_run1, col_run2 = st.columns([1, 1])

    with col_run1:
        run_button = st.button("실시간 데이터 불러오기 및 경로 추천 실행")

    with col_run2:
        reset_button = st.button("저장된 실시간 결과 초기화")

    if reset_button:
        for key in session_keys:
            st.session_state[key] = None
        st.success("저장된 실시간 분석 결과를 초기화했습니다.")

    if run_button:
        if not api_key:
            st.error("서울시 OpenAPI 인증키가 필요합니다.")
            st.stop()

        with st.spinner("실시간 따릉이 데이터를 불러오는 중입니다..."):
            realtime_df, error_data = fetch_realtime_bike_data(api_key)

        if len(realtime_df) == 0:
            st.error("실시간 데이터를 불러오지 못했습니다. 인증키 또는 API 응답을 확인하세요.")
            if error_data:
                st.json(error_data)
            st.stop()

        historical_month_df = get_historical_month_data(monthly_df, selected_month_number)

        decision_df = prepare_realtime_decision_data(
            realtime_df=realtime_df,
            historical_df=historical_month_df,
            target_rate=target_rate,
            pickup_rate=pickup_rate,
            delivery_rate=delivery_rate,
            realtime_weight=realtime_weight,
            history_weight=history_weight
        )

        if len(decision_df) == 0:
            st.error("여의동 대여소와 매칭된 실시간 데이터가 없습니다.")
            st.stop()

        vrp_input_df = make_vrp_input(decision_df)

        route_df = pd.DataFrame()
        method_used = route_method

        if route_method == "직선거리 휴리스틱":
            route_df, route_error = recommend_simple_route(
                decision_df=decision_df,
                depot_lat=depot_lat,
                depot_lon=depot_lon,
                vehicle_capacity=int(vehicle_capacity),
                max_stops=int(max_stops)
            )

        else:
            nodes, nodes_df = build_vrp_nodes(
                decision_df=decision_df,
                depot_lat=depot_lat,
                depot_lon=depot_lon,
                candidate_limit=int(candidate_limit),
                vehicle_count=int(vehicle_count),
                vehicle_capacity=int(vehicle_capacity)
            )

            if len(nodes) <= 1:
                st.warning("VRP를 구성할 수거/재배치 후보가 부족합니다.")
                route_df = pd.DataFrame()
            else:
                if route_method == "OSRM 도로망 거리 + OR-Tools VRP":
                    duration_matrix, distance_matrix, osrm_error = get_osrm_matrix(nodes)

                    if osrm_error is not None or duration_matrix is None:
                        st.warning(f"OSRM 거리행렬 생성에 실패했습니다. 직선거리 행렬로 대체합니다. 오류: {osrm_error}")
                        cost_matrix = get_haversine_matrix(nodes)
                    else:
                        cost_matrix = duration_matrix

                else:
                    cost_matrix = get_haversine_matrix(nodes)

                route_df, route_error = solve_ortools_vrp(
                    nodes=nodes,
                    cost_matrix=cost_matrix,
                    vehicle_count=int(vehicle_count),
                    vehicle_capacity=int(vehicle_capacity)
                )

                if route_error:
                    st.warning(route_error)

        st.session_state["realtime_decision_df"] = decision_df
        st.session_state["realtime_route_df"] = route_df
        st.session_state["realtime_vrp_input_df"] = vrp_input_df
        st.session_state["realtime_loaded_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        st.session_state["realtime_depot_lat"] = depot_lat
        st.session_state["realtime_depot_lon"] = depot_lon
        st.session_state["realtime_method_used"] = method_used

    if st.session_state["realtime_decision_df"] is not None:
        decision_df = st.session_state["realtime_decision_df"]
        route_df = st.session_state["realtime_route_df"]
        vrp_input_df = st.session_state["realtime_vrp_input_df"]
        depot_lat = st.session_state["realtime_depot_lat"]
        depot_lon = st.session_state["realtime_depot_lon"]
        method_used = st.session_state["realtime_method_used"]

        st.success(
            f"실시간 분석 결과가 저장되어 있습니다. "
            f"갱신 시각: {st.session_state['realtime_loaded_time']} / 경로 방식: {method_used}"
        )

        st.divider()

        st.markdown("## 5. 실시간 현황 요약")

        total_now = int(decision_df["현재자전거수"].sum())
        total_rack = int(decision_df["거치대수"].sum())
        pickup_count = len(decision_df[decision_df["실시간관리유형"] == "수거 후보"])
        delivery_count = len(decision_df[decision_df["실시간관리유형"] == "재배치 후보"])
        avg_occ = decision_df["현재점유율"].mean()

        col1, col2, col3, col4, col5 = st.columns(5)
        col1.metric("현재 자전거 수", f"{total_now:,}대")
        col2.metric("총 거치대 수", f"{total_rack:,}개")
        col3.metric("평균 점유율", f"{avg_occ:.1%}")
        col4.metric("수거 후보", f"{pickup_count:,}개")
        col5.metric("재배치 후보", f"{delivery_count:,}개")

        st.divider()

        st.markdown("## 6. 실시간 현황 지도")

        realtime_map = make_realtime_status_map(decision_df)

        if realtime_map is not None:
            st_folium(realtime_map, width=None, height=650, key="realtime_status_map")
        else:
            st.warning("지도에 표시할 수 있는 데이터가 없습니다.")

        st.divider()

        st.markdown("## 7. 수거·재배치 후보")

        tab1, tab2, tab3 = st.tabs(["수거 후보", "재배치 후보", "전체 실시간 현황"])

        with tab1:
            pickup_candidates = decision_df[decision_df["실시간관리유형"] == "수거 후보"].copy()
            pickup_candidates = pickup_candidates.sort_values("수거우선점수", ascending=False)

            if len(pickup_candidates) == 0:
                st.info("현재 설정 기준에서 수거 후보가 없습니다.")
            else:
                st.dataframe(
                    pickup_candidates[
                        [
                            "대여소_ID", "실시간_대여소명", "거치대수", "현재자전거수", "API_거치율", "현재점유율",
                            "목표자전거수", "수거필요대수", "수거우선점수",
                            "출발_건수", "종료_건수", "순유입량", "불균형_절댓값"
                        ]
                    ],
                    width="stretch"
                )

        with tab2:
            delivery_candidates = decision_df[decision_df["실시간관리유형"] == "재배치 후보"].copy()
            delivery_candidates = delivery_candidates.sort_values("재배치우선점수", ascending=False)

            if len(delivery_candidates) == 0:
                st.info("현재 설정 기준에서 재배치 후보가 없습니다.")
            else:
                st.dataframe(
                    delivery_candidates[
                        [
                            "대여소_ID", "실시간_대여소명", "거치대수", "현재자전거수", "API_거치율", "현재점유율",
                            "목표자전거수", "재배치필요대수", "재배치우선점수",
                            "출발_건수", "종료_건수", "순유입량", "불균형_절댓값"
                        ]
                    ],
                    width="stretch"
                )

        with tab3:
            st.dataframe(decision_df, width="stretch")

        st.divider()

        st.markdown("## 8. 경로 추천 결과")

        if route_df is None or len(route_df) == 0:
            st.warning("수거 후보와 재배치 후보가 모두 있어야 경로 추천이 가능합니다.")
        else:
            route_map = make_route_map(route_df, depot_lat, depot_lon)

            if route_map is not None:
                st_folium(route_map, width=None, height=650, key="recommended_route_map")

            st.markdown("### 추천 경로 표")
            st.dataframe(route_df, width="stretch")

            if method_used == "직선거리 휴리스틱":
                st.info(
                    "현재 경로는 직선거리 기반 휴리스틱 추천입니다. "
                    "수거 후보와 재배치 후보를 우선점수와 거리 기준으로 순차 연결합니다."
                )
            elif method_used == "OSRM 도로망 거리 + OR-Tools VRP":
                st.info(
                    "현재 경로는 OSRM 도로망 이동시간 행렬과 OR-Tools VRP 모델을 결합하여 계산했습니다. "
                    "다만 OSRM 공개 서버는 과제 데모 수준으로 사용하는 것이 적절합니다."
                )
            else:
                st.info(
                    "현재 경로는 위도·경도 직선거리 행렬과 OR-Tools VRP 모델을 결합하여 계산했습니다."
                )

        st.divider()

        st.markdown("## 9. VRP 입력 데이터")

        if vrp_input_df is None or len(vrp_input_df) == 0:
            st.warning("VRP 입력 데이터로 변환할 후보가 없습니다.")
        else:
            st.dataframe(vrp_input_df, width="stretch")

            csv = vrp_input_df.to_csv(index=False).encode("utf-8-sig")

            st.download_button(
                label="VRP 입력 데이터 CSV 다운로드",
                data=csv,
                file_name="vrp_input_yeouido_realtime.csv",
                mime="text/csv"
            )

    else:
        st.info("아직 실시간 분석 결과가 없습니다. 위 설정을 입력한 뒤 실행 버튼을 눌러주세요.")
