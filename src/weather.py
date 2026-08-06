import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

WEATHER_TIMEOUT_SECONDS = 10
MAX_CITY_NAME_LENGTH = 100

WEATHER_CODE_NAMES = {
    0: "晴朗",
    1: "大部晴朗",
    2: "局部多云",
    3: "阴天",
    45: "有雾",
    48: "雾凇",
    51: "小毛毛雨",
    53: "中毛毛雨",
    55: "强毛毛雨",
    61: "小雨",
    63: "中雨",
    65: "大雨",
    71: "小雪",
    73: "中雪",
    75: "大雪",
    80: "阵雨",
    81: "强阵雨",
    82: "暴雨",
    95: "雷暴",
}


class WeatherError(RuntimeError):
    """表示天气查询失败，但不暴露网络内部细节。"""


@dataclass
class WeatherReport:
    """保存经过校验的当前天气结果。"""

    location: str
    observed_at: str
    temperature_celsius: float
    apparent_temperature_celsius: float
    wind_speed_kmh: float
    weather_code: int

    def to_text(self) -> str:
        """把结构化天气数据转换为给模型阅读的文本。"""
        weather_name = WEATHER_CODE_NAMES.get(
            self.weather_code,
            f"未知天气代码 {self.weather_code}",
        )

        return (
            f"{self.location} 的当前模型天气（数据时间：{self.observed_at}）："
            f"{weather_name}，气温 {self.temperature_celsius}°C，"
            f"体感 {self.apparent_temperature_celsius}°C，"
            f"风速 {self.wind_speed_kmh} km/h。"
            "数据来源：Open-Meteo。"
        )


def validate_city_name(city: str) -> str:
    """校验城市名称，避免发送空白或异常长的网络请求。"""
    normalized_city = city.strip()

    if not normalized_city:
        raise WeatherError("城市名称不能为空")

    if len(normalized_city) > MAX_CITY_NAME_LENGTH:
        raise WeatherError("城市名称过长")

    return normalized_city


def read_number(data: dict[str, object], field_name: str) -> float:
    """从 API 响应中安全读取数值字段。"""
    value = data.get(field_name)

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WeatherError("天气服务返回的数据格式无效")

    return float(value)


def get_current_weather(city: str) -> WeatherReport:
    """通过城市名查询当前模型天气。"""
    city_name = validate_city_name(city)
    logger.info("开始天气查询")

    try:
        with httpx.Client(
            timeout=WEATHER_TIMEOUT_SECONDS,
            follow_redirects=False,
        ) as client:
            geocoding_response = client.get(
                GEOCODING_URL,
                params={
                    "name": city_name,
                    "count": 1,
                    "language": "zh",
                    "format": "json",
                },
            )
            geocoding_response.raise_for_status()
            geocoding_data = geocoding_response.json()

            results = geocoding_data.get("results")
            if not isinstance(results, list) or not results:
                raise WeatherError("没有找到该城市")

            location_data = results[0]
            if not isinstance(location_data, dict):
                raise WeatherError("天气服务返回的数据格式无效")

            location_name = location_data.get("name")
            country_name = location_data.get("country")

            if not isinstance(location_name, str):
                raise WeatherError("天气服务返回的数据格式无效")

            latitude = read_number(location_data, "latitude")
            longitude = read_number(location_data, "longitude")

            forecast_response = client.get(
                FORECAST_URL,
                params={
                    "latitude": latitude,
                    "longitude": longitude,
                    "current": (
                        "temperature_2m,apparent_temperature,"
                        "weather_code,wind_speed_10m"
                    ),
                    "temperature_unit": "celsius",
                    "wind_speed_unit": "kmh",
                    "timezone": "auto",
                },
            )
            forecast_response.raise_for_status()
            forecast_data = forecast_response.json()

    except httpx.TimeoutException as error:
        logger.warning("天气查询超时")
        raise WeatherError("天气查询超时，请稍后再试") from error
    except httpx.HTTPStatusError as error:
        logger.warning(
            "天气服务返回错误状态码: status_code=%s",
            error.response.status_code,
        )
        raise WeatherError("天气服务暂时不可用，请稍后再试") from error
    except httpx.RequestError as error:
        logger.warning("无法连接天气服务")
        raise WeatherError("无法连接天气服务，请检查网络") from error
    except (TypeError, ValueError) as error:
        logger.warning("天气服务返回的数据格式无效")
        raise WeatherError("天气服务返回的数据格式无效") from error

    current_data = forecast_data.get("current")
    if not isinstance(current_data, dict):
        raise WeatherError("天气服务返回的数据格式无效")

    weather_code_value = current_data.get("weather_code")
    observed_at = current_data.get("time")

    if (
        isinstance(weather_code_value, bool)
        or not isinstance(weather_code_value, int)
        or not isinstance(observed_at, str)
    ):
        raise WeatherError("天气服务返回的数据格式无效")

    location = location_name
    if isinstance(country_name, str) and country_name:
        location = f"{location_name}，{country_name}"

    report = WeatherReport(
        location=location,
        observed_at=observed_at,
        temperature_celsius=read_number(current_data, "temperature_2m"),
        apparent_temperature_celsius=read_number(
            current_data,
            "apparent_temperature",
        ),
        wind_speed_kmh=read_number(current_data, "wind_speed_10m"),
        weather_code=weather_code_value,
    )

    logger.info("天气查询成功")
    return report