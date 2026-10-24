"""Define constants for the Worlty integration."""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.components.switch import SwitchDeviceClass
from homeassistant.const import (
    CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
    CONCENTRATION_PARTS_PER_MILLION,
    LIGHT_LUX,
    PERCENTAGE,
    Platform,
    UnitOfApparentPower,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfPrecipitationDepth,
    UnitOfPressure,
    UnitOfReactivePower,
    UnitOfSpeed,
    UnitOfTemperature,
    UnitOfVolume,
    UnitOfVolumeFlowRate,
)

from ..const import DOMAIN as DOMAIN

MANUFACTURER = "Worlty"

LOGGER = logging.getLogger(__package__)

APP_MANAGED_NUMBER_KEYS = frozenset(
    {
        "filter_remain",
        "interval_ms",
        "running_ms",
        "running_min_ms",
        "day_int_ms",
        "day_run_ms",
        "night_int_ms",
        "night_run_ms",
        "day_speed",
        "night_speed",
    }
)


def is_app_managed_number(platform: str, key: str | None) -> bool:
    """Check whether the app manages this filter, timer, or speed setting."""
    return platform == "number" and key in APP_MANAGED_NUMBER_KEYS


class WorltyBaseType(Enum):
    """Define Worlty entity types."""

    BINARY_SENSOR = 1
    SENSOR = 2
    LIGHT = 3
    SWITCH = 4
    EVENT = 5
    FAN = 6
    INPUT = 7
    CLIMATE = 8
    COVER = 9


def is_advanced_entity(platform: str, key: str) -> bool:
    """Identify detailed inputs and automation states shown in advanced mode."""
    return platform in {"number", "time", "date"} or (
        platform == "sensor"
        and key in {"last_execute", "saved_execute", "worlty_run_ms"}
    )


def map_worlty_state(lang, state) -> Any:
    """Translate a device state or keep the original value when no translation exists."""
    return {
        "en": {
            "high": "High",
            "medium": "Medium",
            "low": "Low",
            "auto": "Auto",
            "weak": "Weak",
            "boost": "Boost",
            "eco": "Eco",
            "arrive": "Arrive",
            "call": "Call",
            "detect": "Detect",
            "down": "Down",
            "idle": "idle",
            "left": "Left",
            "move": "Move",
            "open": "Open",
            "right": "Right",
            "ring": "Ring",
            "up": "Up",
            "wait": "Wait",
        },
        "ko": {
            "high": "강",
            "medium": "중",
            "low": "약",
            "auto": "자동",
            "weak": "최저",
            "boost": "최고",
            "eco": "절전",
            "arrive": "도착",
            "call": "통화",
            "detect": "감지",
            "down": "아래쪽",
            "idle": "대기",
            "left": "왼쪽",
            "move": "이동",
            "open": "열기",
            "right": "오른쪽",
            "ring": "벨소리",
            "up": "위쪽",
            "wait": "기다림",
        },
    }.get(lang, {}).get(state, state)


def map_worlty_sub(lang, sub_id) -> str:
    """Translate a child identifier into a display name."""
    return {
        "en": {
            "device": "Device",
            "ac": "Air conditioner",
            "boiler": "Boiler",
            "fan": "Fan",
            "dimming": "Dimmable light",
            "switch": "Switch",
            "sensor": "Sensor",
            "binary_sensor": "Status sensor",
            "ctrl_mode": "Controller mode",
            "ctrl_speed": "Controller fan speed",
            "target_speed": "Target fan speed",
            "blr-err": "Boiler error",
            "front": "Entrance",
            "event": "Event",
            "cook": "Gas valve",
            "bell": "Bell",
            "light": "Light",
            "outlet": "Outlet",
            "away": "Away",
            "fire_alert": "Fire alert",
            "filter_alert": "Filter alert",
            "filter_use": "Filter usage",
            "gas_leak": "Gas leak",
            "consumption": "Total consumption",
            "usage": "Usage",
            "elevator": "Elevator",
            "direction": "Elevator direction",
            "floor": "Elevator floor",
            "location": "Location",
            "apt-0": "Shared entrance alert",
            "apt-1": "Open shared entrance",
            "apt-2": "Open shared entrance on next call",
            "apt-3": "End next shared entrance call",
            "apt-4": "Always end shared entrance calls",
            "home-0": "Home entrance alert",
            "home-1": "Open home entrance",
            "home-2": "Open home entrance on next call",
            "home-3": "End next home entrance call",
            "home-4": "Always end home entrance calls",
            "timer": "Timer",
            "auto_start": "Automatic on",
            "auto_stop": "Automatic off",
            "auto_dry": "Automatic drying",
            "interval_ms": "Timer interval",
            "running_min_ms": "Minimum run time",
            "running_ms": "Timer run time",
            "start_time": "Timer start time",
            "end_time": "Timer end time",
            "start_date": "Timer start date",
            "end_date": "Timer end date",
            "weekday": "Operating days",
            "lock": "Lock",
            "day_start": "Daytime start time",
            "day_int_ms": "Daytime interval",
            "day_run_ms": "Daytime run time",
            "day_speed": "Daytime fan speed",
            "night_start": "Nighttime start time",
            "night_int_ms": "Nighttime interval",
            "night_run_ms": "Nighttime run time",
            "night_speed": "Nighttime fan speed",
            "filter_remain": "Filter remaining time",
            "count1": "Internal counter",
            "count1_value": "Internal counter value",
            "try_count": "Attempt count",
            "try_limit": "Attempt limit",
            "saved_execute": "Saved execution time",
            "boiler_nooff": "Prevent boiler shutdown",
            "b_temp_off": "Boiler turn off temperature",
            "b_temp_on": "Boiler turn on temperature",
            "routine": "Routine",
            "routine_t1": "Routine 1 start time",
            "routine_t2": "Routine 2 start time",
            "routine_t3": "Routine 3 start time",
            "routine_t4": "Routine 4 start time",
            "routine_t1_t": "Routine 1 target temperature",
            "routine_t2_t": "Routine 2 target temperature",
            "routine_t3_t": "Routine 3 target temperature",
            "routine_t4_t": "Routine 4 target temperature",
            "routine_re_t": "Routine temperature restore time",
            "open_prd": "Periodic opening",
            "open_prd_ms": "Periodic opening interval",
            "eva_in": "Evaporator inlet temperature",
            "eva_out": "Evaporator outlet temperature",
            "temp_offset": "Current temperature offset",
            "temp_target": "Target temperature",
            "last_execute": "Last timer run",
            "worlty": "Worlty control",
            "worlty_offset": "Worlty control offset",
            "worlty_run_ms": "Worlty control run time",
            "water_in": "Water inlet",
            "water_out": "Water outlet",
            "bypass": "Bypass status",
            "bypasser": "Bypass control",
            "heater": "Heater",
            "purifier": "Purifier",
            "purify": "Air purification status",
        },
        "ko": {
            "device": "장치",
            "ac": "에어컨",
            "boiler": "보일러",
            "fan": "환풍기",
            "dimming": "디밍 조명",
            "switch": "스위치",
            "sensor": "센서",
            "binary_sensor": "상태 센서",
            "ctrl_mode": "리모컨 모드",
            "ctrl_speed": "리모컨 풍량",
            "target_speed": "목표 풍량",
            "blr-err": "보일러 오류",
            "front": "현관 앞",
            "event": "이벤트",
            "cook": "가스밸브",
            "bell": "초인종",
            "light": "조명",
            "outlet": "콘센트",
            "away": "외출",
            "fire_alert": "화재 감지",
            "filter_alert": "필터 경고",
            "filter_use": "필터 사용",
            "gas_leak": "가스 누출",
            "consumption": "누적 소비전력",
            "usage": "소비전력",
            "elevator": "엘리베이터",
            "direction": "엘리베이터 방향",
            "floor": "엘리베이터 층",
            "location": "위치",
            "apt-0": "공동현관 알림",
            "apt-1": "공동현관 열기",
            "apt-2": "공동현관 다음 호출 시 열기",
            "apt-3": "공동현관 다음 호출 종료",
            "apt-4": "공동현관 호출 항상 종료",
            "home-0": "세대 현관 알림",
            "home-1": "세대 현관 열기",
            "home-2": "세대 현관 다음 호출 시 열기",
            "home-3": "세대 현관 다음 호출 종료",
            "home-4": "세대 현관 호출 항상 종료",
            "timer": "타이머",
            "auto_start": "자동 켜기",
            "auto_stop": "자동 끄기",
            "auto_dry": "자동 건조",
            "interval_ms": "타이머 작동 간격",
            "running_min_ms": "최소 작동 시간",
            "running_ms": "타이머 작동 시간",
            "start_time": "타이머 시작 시각",
            "end_time": "타이머 종료 시각",
            "start_date": "타이머 시작 날짜",
            "end_date": "타이머 종료 날짜",
            "weekday": "작동 요일",
            "lock": "잠금",
            "day_start": "주간 시작 시각",
            "day_int_ms": "주간 작동 간격",
            "day_run_ms": "주간 작동 시간",
            "day_speed": "주간 풍량",
            "night_start": "야간 시작 시각",
            "night_int_ms": "야간 작동 간격",
            "night_run_ms": "야간 작동 시간",
            "night_speed": "야간 풍량",
            "filter_remain": "필터 잔여 시간",
            "count1": "내부 카운터",
            "count1_value": "내부 카운터 값",
            "try_count": "시도 횟수",
            "try_limit": "시도 한도",
            "saved_execute": "저장된 작동 시각",
            "boiler_nooff": "보일러 꺼짐 방지",
            "b_temp_off": "보일러 꺼짐 온도",
            "b_temp_on": "보일러 켜짐 온도",
            "routine": "루틴",
            "routine_t1": "루틴 1 시작 시각",
            "routine_t2": "루틴 2 시작 시각",
            "routine_t3": "루틴 3 시작 시각",
            "routine_t4": "루틴 4 시작 시각",
            "routine_t1_t": "루틴 1 목표 온도",
            "routine_t2_t": "루틴 2 목표 온도",
            "routine_t3_t": "루틴 3 목표 온도",
            "routine_t4_t": "루틴 4 목표 온도",
            "routine_re_t": "루틴 온도 복원 시각",
            "open_prd": "주기적 열기",
            "open_prd_ms": "주기적 열기 간격",
            "eva_in": "증발기 입구 온도",
            "eva_out": "증발기 출구 온도",
            "temp_offset": "현재 온도 보정값",
            "temp_target": "목표 온도",
            "last_execute": "타이머 최근 실행 시각",
            "worlty": "월티 제어",
            "worlty_offset": "월티 제어 보정값",
            "worlty_run_ms": "월티 제어 작동 시간",
            "water_in": "출수",
            "water_out": "환수",
            "bypass": "바이패스 상태",
            "bypasser": "바이패스 제어",
            "heater": "전열",
            "purifier": "공기 청정",
            "purify": "공기 청정 상태",
        },
    }.get(lang, {}).get(sub_id, sub_id)


def map_worlty_to_platform(worlty_type, worlty_class) -> str:
    """Return the HA platform for the device type and class."""
    mapping = {
        WorltyBaseType.BINARY_SENSOR.value: Platform.BINARY_SENSOR.value,
        WorltyBaseType.CLIMATE.value: Platform.CLIMATE.value,
        WorltyBaseType.COVER.value: Platform.COVER.value,
        WorltyBaseType.EVENT.value: Platform.SENSOR.value,
        WorltyBaseType.FAN.value: Platform.FAN.value,
        WorltyBaseType.LIGHT.value: Platform.LIGHT.value,
        WorltyBaseType.SENSOR.value: Platform.SENSOR.value,
        WorltyBaseType.SWITCH.value: Platform.SWITCH.value,
    }
    ha_type = mapping.get(worlty_type)
    if worlty_type == WorltyBaseType.CLIMATE.value and worlty_class == 1:
        ha_type = Platform.WATER_HEATER.value
    elif worlty_type == WorltyBaseType.INPUT.value and worlty_class == 0:
        ha_type = Platform.NUMBER.value
    elif worlty_type == WorltyBaseType.INPUT.value and worlty_class == 3:
        ha_type = Platform.TIME.value
    elif worlty_type == WorltyBaseType.INPUT.value and worlty_class == 4:
        ha_type = Platform.DATE.value
    return ha_type


def get_worlty_description(worlty_device_type: int, worlty_device_class: int) -> tuple:
    """Return the name key, device class, icon, and unit for a device type."""
    # Tuple fields: key, device_class, icon, unit_of_measurement.
    unknown = (None, None, None, None)
    descriptions = {
        WorltyBaseType.BINARY_SENSOR.value: {
            0: ("binary_sensor", None, None, None),
            1: (
                BinarySensorDeviceClass.BATTERY.value,
                BinarySensorDeviceClass.BATTERY,
                None,
                None,
            ),
            2: (
                BinarySensorDeviceClass.BATTERY_CHARGING.value,
                BinarySensorDeviceClass.BATTERY_CHARGING,
                None,
                None,
            ),
            3: (
                BinarySensorDeviceClass.CO.value,
                BinarySensorDeviceClass.CO,
                None,
                None,
            ),
            4: (
                BinarySensorDeviceClass.COLD.value,
                BinarySensorDeviceClass.COLD,
                None,
                None,
            ),
            5: (
                BinarySensorDeviceClass.CONNECTIVITY.value,
                BinarySensorDeviceClass.CONNECTIVITY,
                None,
                None,
            ),
            6: (
                BinarySensorDeviceClass.DOOR.value,
                BinarySensorDeviceClass.DOOR,
                None,
                None,
            ),
            7: (
                "error",
                BinarySensorDeviceClass.PROBLEM,
                None,
                None,
            ),
            8: (
                "filter",
                BinarySensorDeviceClass.PROBLEM,
                None,
                None,
            ),
            9: (
                "fire",
                BinarySensorDeviceClass.SAFETY,
                None,
                None,
            ),
            10: (
                BinarySensorDeviceClass.GARAGE_DOOR.value,
                BinarySensorDeviceClass.GARAGE_DOOR,
                None,
                None,
            ),
            11: (
                BinarySensorDeviceClass.GAS.value,
                BinarySensorDeviceClass.GAS,
                None,
                None,
            ),
            12: (
                BinarySensorDeviceClass.HEAT.value,
                BinarySensorDeviceClass.HEAT,
                None,
                None,
            ),
            13: (
                BinarySensorDeviceClass.LIGHT.value,
                BinarySensorDeviceClass.LIGHT,
                None,
                None,
            ),
            14: (
                BinarySensorDeviceClass.LOCK.value,
                BinarySensorDeviceClass.LOCK,
                None,
                None,
            ),
            15: (
                BinarySensorDeviceClass.MOISTURE.value,
                BinarySensorDeviceClass.MOISTURE,
                None,
                None,
            ),
            16: (
                BinarySensorDeviceClass.MOTION.value,
                BinarySensorDeviceClass.MOTION,
                None,
                None,
            ),
            17: (
                BinarySensorDeviceClass.MOVING.value,
                BinarySensorDeviceClass.MOVING,
                None,
                None,
            ),
            18: (
                BinarySensorDeviceClass.OCCUPANCY.value,
                BinarySensorDeviceClass.OCCUPANCY,
                None,
                None,
            ),
            19: (
                BinarySensorDeviceClass.OPENING.value,
                BinarySensorDeviceClass.OPENING,
                None,
                None,
            ),
            20: (
                BinarySensorDeviceClass.PLUG.value,
                BinarySensorDeviceClass.PLUG,
                None,
                None,
            ),
            21: (
                BinarySensorDeviceClass.POWER.value,
                BinarySensorDeviceClass.POWER,
                None,
                None,
            ),
            22: (
                BinarySensorDeviceClass.PRESENCE.value,
                BinarySensorDeviceClass.PRESENCE,
                None,
                None,
            ),
            23: (
                BinarySensorDeviceClass.PROBLEM.value,
                BinarySensorDeviceClass.PROBLEM,
                None,
                None,
            ),
            24: (
                BinarySensorDeviceClass.RUNNING.value,
                BinarySensorDeviceClass.RUNNING,
                None,
                None,
            ),
            25: (
                BinarySensorDeviceClass.SAFETY.value,
                BinarySensorDeviceClass.SAFETY,
                None,
                None,
            ),
            26: (
                BinarySensorDeviceClass.SMOKE.value,
                BinarySensorDeviceClass.SMOKE,
                None,
                None,
            ),
            27: (
                BinarySensorDeviceClass.SOUND.value,
                BinarySensorDeviceClass.SOUND,
                None,
                None,
            ),
            28: (
                BinarySensorDeviceClass.TAMPER.value,
                BinarySensorDeviceClass.TAMPER,
                None,
                None,
            ),
            29: (
                BinarySensorDeviceClass.UPDATE.value,
                BinarySensorDeviceClass.UPDATE,
                None,
                None,
            ),
            30: (
                BinarySensorDeviceClass.VIBRATION.value,
                BinarySensorDeviceClass.VIBRATION,
                None,
                None,
            ),
            31: (
                BinarySensorDeviceClass.WINDOW.value,
                BinarySensorDeviceClass.WINDOW,
                None,
                None,
            ),
            32: (
                "guard",
                BinarySensorDeviceClass.SAFETY,
                None,
                None,
            ),
        },
        WorltyBaseType.CLIMATE.value: {
            1: ("boiler", None, None, None),
            2: ("ac", None, None, None),
        },
        WorltyBaseType.EVENT.value: {
            0: ("event", None, None, None),
            1: ("event_uss", None, None, None),
        },
        WorltyBaseType.INPUT.value: {
            0: ("number", None, None, None),
            3: ("time", None, None, None),
            4: ("date", None, None, None),
        },
        WorltyBaseType.FAN.value: {
            0: ("fan", None, None, None),
            1: ("sysclein", None, None, None),
            2: ("airone", None, None, None),
        },
        WorltyBaseType.LIGHT.value: {
            0: ("light", None, None, None),
            1: ("dimming", None, None, None),
        },
        WorltyBaseType.SENSOR.value: {
            0: ("sensor", None, None, None),
            1: (
                SensorDeviceClass.APPARENT_POWER.value,
                SensorDeviceClass.APPARENT_POWER,
                None,
                UnitOfApparentPower.VOLT_AMPERE,
            ),
            2: (
                SensorDeviceClass.AQI.value,
                SensorDeviceClass.AQI,
                None,
                None,
            ),
            3: (
                SensorDeviceClass.ATMOSPHERIC_PRESSURE.value,
                SensorDeviceClass.ATMOSPHERIC_PRESSURE,
                None,
                UnitOfPressure.HPA,
            ),
            4: (
                SensorDeviceClass.BATTERY.value,
                SensorDeviceClass.BATTERY,
                None,
                PERCENTAGE,
            ),
            5: (
                SensorDeviceClass.CO2.value,
                SensorDeviceClass.CO2,
                None,
                CONCENTRATION_PARTS_PER_MILLION,
            ),
            6: (
                SensorDeviceClass.CO.value,
                SensorDeviceClass.CO,
                None,
                CONCENTRATION_PARTS_PER_MILLION,
            ),
            7: (
                SensorDeviceClass.CURRENT.value,
                SensorDeviceClass.CURRENT,
                None,
                None,
            ),
            8: (
                SensorDeviceClass.DATA_RATE.value,
                SensorDeviceClass.DATA_RATE,
                None,
                None,
            ),
            9: (
                SensorDeviceClass.DATA_SIZE.value,
                SensorDeviceClass.DATA_SIZE,
                None,
                None,
            ),
            10: (
                SensorDeviceClass.DATE.value,
                SensorDeviceClass.DATE,
                None,
                None,
            ),
            11: (
                SensorDeviceClass.DISTANCE.value,
                SensorDeviceClass.DISTANCE,
                None,
                None,
            ),
            12: (
                SensorDeviceClass.DURATION.value,
                SensorDeviceClass.DURATION,
                None,
                None,
            ),
            13: (
                SensorDeviceClass.ENERGY.value,
                SensorDeviceClass.ENERGY,
                None,
                UnitOfEnergy.KILO_WATT_HOUR,
            ),
            14: (
                SensorDeviceClass.ENUM.value,
                SensorDeviceClass.ENUM,
                None,
                None,
            ),
            15: (
                "floor",
                None,
                "mdi:elevator",
                None,
            ),
            16: (
                SensorDeviceClass.FREQUENCY.value,
                SensorDeviceClass.FREQUENCY,
                None,
                None,
            ),
            17: (
                SensorDeviceClass.GAS.value,
                SensorDeviceClass.GAS,
                None,
                UnitOfVolume.CUBIC_METERS,
            ),
            18: (
                "heat",
                SensorDeviceClass.VOLUME_FLOW_RATE,
                None,
                UnitOfVolumeFlowRate.CUBIC_METERS_PER_HOUR,
            ),
            19: (
                "hotwater",
                SensorDeviceClass.VOLUME_FLOW_RATE,
                None,
                UnitOfVolumeFlowRate.CUBIC_METERS_PER_HOUR,
            ),
            20: (
                SensorDeviceClass.HUMIDITY.value,
                SensorDeviceClass.HUMIDITY,
                None,
                PERCENTAGE,
            ),
            21: (
                SensorDeviceClass.ILLUMINANCE.value,
                SensorDeviceClass.ILLUMINANCE,
                None,
                LIGHT_LUX,
            ),
            22: (
                SensorDeviceClass.IRRADIANCE.value,
                SensorDeviceClass.IRRADIANCE,
                None,
                None,
            ),
            23: (
                SensorDeviceClass.MOISTURE.value,
                SensorDeviceClass.MOISTURE,
                None,
                PERCENTAGE,
            ),
            24: (
                SensorDeviceClass.MONETARY.value,
                SensorDeviceClass.MONETARY,
                None,
                None,
            ),
            25: (
                SensorDeviceClass.NITROGEN_DIOXIDE.value,
                SensorDeviceClass.NITROGEN_DIOXIDE,
                None,
                CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
            ),
            26: (
                SensorDeviceClass.NITROGEN_MONOXIDE.value,
                SensorDeviceClass.NITROGEN_MONOXIDE,
                None,
                CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
            ),
            27: (
                SensorDeviceClass.NITROUS_OXIDE.value,
                SensorDeviceClass.NITROUS_OXIDE,
                None,
                CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
            ),
            28: (
                SensorDeviceClass.OZONE.value,
                SensorDeviceClass.OZONE,
                None,
                CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
            ),
            29: (
                "percentage",
                None,
                "mdi:percent",
                PERCENTAGE,
            ),
            30: (
                SensorDeviceClass.PM1.value,
                SensorDeviceClass.PM1,
                None,
                CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
            ),
            31: (
                SensorDeviceClass.PM10.value,
                SensorDeviceClass.PM10,
                None,
                CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
            ),
            32: (
                SensorDeviceClass.PM25.value,
                SensorDeviceClass.PM25,
                None,
                CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
            ),
            33: (
                SensorDeviceClass.POWER_FACTOR.value,
                SensorDeviceClass.POWER_FACTOR,
                None,
                PERCENTAGE,
            ),
            34: (
                SensorDeviceClass.POWER.value,
                SensorDeviceClass.POWER,
                None,
                UnitOfPower.WATT,
            ),
            35: (
                SensorDeviceClass.PRECIPITATION.value,
                SensorDeviceClass.PRECIPITATION,
                None,
                UnitOfPrecipitationDepth.MILLIMETERS,
            ),
            36: (
                SensorDeviceClass.PRECIPITATION_INTENSITY.value,
                SensorDeviceClass.PRECIPITATION_INTENSITY,
                None,
                None,
            ),
            37: (
                SensorDeviceClass.PRESSURE.value,
                SensorDeviceClass.PRESSURE,
                None,
                UnitOfPressure.HPA,
            ),
            38: (
                SensorDeviceClass.REACTIVE_POWER.value,
                SensorDeviceClass.REACTIVE_POWER,
                None,
                UnitOfReactivePower.VOLT_AMPERE_REACTIVE,
            ),
            39: (
                SensorDeviceClass.SIGNAL_STRENGTH.value,
                SensorDeviceClass.SIGNAL_STRENGTH,
                None,
                None,
            ),
            40: (
                SensorDeviceClass.SOUND_PRESSURE.value,
                SensorDeviceClass.SOUND_PRESSURE,
                None,
                None,
            ),
            41: (
                SensorDeviceClass.SPEED.value,
                SensorDeviceClass.SPEED,
                None,
                None,
            ),
            42: (
                "string",
                None,
                "mdi:format-text",
                None,
            ),
            43: (
                SensorDeviceClass.SULPHUR_DIOXIDE.value,
                SensorDeviceClass.SULPHUR_DIOXIDE,
                None,
                CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
            ),
            44: (
                SensorDeviceClass.TEMPERATURE.value,
                SensorDeviceClass.TEMPERATURE,
                None,
                UnitOfTemperature.CELSIUS,
            ),
            45: (
                SensorDeviceClass.TIMESTAMP.value,
                SensorDeviceClass.TIMESTAMP,
                None,
                None,
            ),
            46: (
                SensorDeviceClass.VOLATILE_ORGANIC_COMPOUNDS.value,
                SensorDeviceClass.VOLATILE_ORGANIC_COMPOUNDS,
                None,
                CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
            ),
            47: (
                SensorDeviceClass.VOLTAGE.value,
                SensorDeviceClass.VOLTAGE,
                None,
                UnitOfElectricPotential.VOLT,
            ),
            48: (
                SensorDeviceClass.VOLUME.value,
                SensorDeviceClass.VOLUME,
                None,
                None,
            ),
            49: (
                SensorDeviceClass.WATER.value,
                SensorDeviceClass.VOLUME_FLOW_RATE,
                None,
                UnitOfVolumeFlowRate.CUBIC_METERS_PER_HOUR,
            ),
            50: (
                "weather",
                None,
                None,
                None,
            ),
            51: (
                SensorDeviceClass.WEIGHT.value,
                SensorDeviceClass.WEIGHT,
                None,
                None,
            ),
            52: (
                SensorDeviceClass.WIND_SPEED.value,
                SensorDeviceClass.WIND_SPEED,
                None,
                UnitOfSpeed.METERS_PER_SECOND,
            ),
            53: (
                "multisensor",
                None,
                None,
                None,
            ),
        },
        WorltyBaseType.SWITCH.value: {
            0: (
                SwitchDeviceClass.SWITCH.value,
                SwitchDeviceClass.SWITCH,
                None,
                None,
            ),
            1: (
                SwitchDeviceClass.OUTLET.value,
                SwitchDeviceClass.OUTLET,
                None,
                None,
            ),
            2: (
                "valve",
                SwitchDeviceClass.SWITCH,
                "mdi:pipe-valve",
                None,
            ),
            3: (
                "open",
                SwitchDeviceClass.SWITCH,
                "mdi:lock-open",
                None,
            ),
            4: (
                "lock",
                SwitchDeviceClass.SWITCH,
                "mdi:lock",
                None,
            ),
            5: (
                "cook",
                SwitchDeviceClass.SWITCH,
                "mdi:chef-hat",
                None,
            ),
            6: (
                "elevator",
                SwitchDeviceClass.SWITCH,
                "mdi:elevator",
                None,
            ),
            7: (
                "interphone",
                SwitchDeviceClass.SWITCH,
                "mdi:phone",
                None,
            ),
        },
    }.get(worlty_device_type, {})
    return descriptions.get(worlty_device_class, unknown)
