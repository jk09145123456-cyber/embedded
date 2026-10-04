"""Raspberry Pi 클라이언트와 PC AI 서버가 공유하는 통신 규약."""

from enum import Enum


# AI 모델 입력은 16 kHz 오디오를 기준으로 맞춘다.
SAMPLE_RATE = 16_000


class Event(str, Enum):
    """시스템에서 사용할 수 있는 이벤트 명령 목록."""

    # NONE은 아무 알림도 필요 없다는 의미이다.
    NONE = "NONE"

    # 방향 안내 이벤트이다.
    RIGHT_TURN = "RIGHT_TURN"
    LEFT_TURN = "LEFT_TURN"

    # 안전 관련 이벤트이다.
    SPEED_WARNING = "SPEED_WARNING"
    HORN = "HORN"
    SCREAM = "SCREAM"
    SIREN = "SIREN"
