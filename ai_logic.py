"""PC AI 서버와 Raspberry Pi 클라이언트가 함께 사용하는 AI 판단 코드."""

from __future__ import annotations

"""
이 파일을 따로 분리한 이유:
- pc_ai_server.py는 HTTP 요청 처리와 서버 실행을 담당한다.
- ai_logic.py는 AI 모델 호출 결과를 실제 이벤트로 바꾸는 판단 로직을 담당한다.
- 이렇게 나누면 서버 코드와 AI 판단 코드를 따로 읽을 수 있어 제출용으로 구조가 더 명확하다.

현재 사용하는 AI 방식:
1. Whisper: 한국어 안내 음성을 텍스트로 변환한다.
2. YAMNet: 주변 소리를 영어 라벨과 점수로 분류한다.
3. 키워드/우선순위 로직: 모델 결과를 Arduino가 이해하는 이벤트 이름으로 바꾼다.
"""

# 실행 중 어떤 이벤트가 감지됐는지 기록하기 위해 사용한다.
import logging

# 여러 개의 예측 결과를 함수 인자로 받을 때 타입을 표시하기 위해 사용한다.
from typing import Iterable

# Raspberry Pi 코드와 같은 이벤트 이름을 쓰기 위해 가져온다.
from raspberry_main import Event


# 숫자가 클수록 더 위험한 이벤트이다.
# 여러 이벤트가 동시에 감지되면 우선순위가 높은 이벤트를 최종 결과로 사용한다.
EVENT_PRIORITY = {
    Event.NONE: 0,
    Event.RIGHT_TURN: 1,
    Event.LEFT_TURN: 2,
    Event.SPEED_WARNING: 3,
    Event.HORN: 4,
    Event.SIREN: 5,
    Event.SCREAM: 6,
}

# 음성 인식 결과에서 찾을 한국어 안내 문구이다.
# 공백을 제거하고 비교하므로 "좌 회전"처럼 띄어쓰기가 달라도 인식할 수 있다.
# Whisper가 문장을 완전히 똑같이 받아쓰지 않아도 핵심 단어만 포함되면 이벤트로 판단한다.
TEXT_KEYWORDS = {
    Event.SPEED_WARNING: (
        "속도위반",
        "속도 위반",
        "과속",
        "제한속도",
        "제한 속도",
        "속도를 줄이세요",
    ),
    Event.LEFT_TURN: ("좌회전", "왼쪽", "좌측"),
    Event.RIGHT_TURN: ("우회전", "오른쪽", "우측"),
}

# YAMNet 소리 분류 모델의 영어 라벨에서 찾을 위험 소리 키워드이다.
# YAMNet은 "siren", "car horn"처럼 영어 라벨을 반환하므로 영어 키워드를 사용한다.
SOUND_KEYWORDS = {
    Event.SIREN: ("siren", "emergency vehicle"),
    Event.SCREAM: (
        "screaming",
        "scream",
        "shout",
        "yell",
        "children shouting",
    ),
    Event.HORN: ("car horn", "vehicle horn", "honk", "honking"),
}


def detect_text_event(text: str) -> Event:
    """음성 인식 문장에서 좌회전, 우회전, 과속 안내를 찾는다."""

    # lower()로 대소문자 차이를 없애고 split()/join()으로 공백을 모두 제거한다.
    # 예: "좌 회전 입니다" -> "좌회전입니다"
    normalized = "".join(text.lower().split())

    # 과속 경고를 방향 안내보다 먼저 검사한다.
    # 과속 경고는 안전 관련 이벤트라 방향 안내보다 높은 우선순위를 갖는다.
    for event in (Event.SPEED_WARNING, Event.LEFT_TURN, Event.RIGHT_TURN):
        # 각 이벤트에 등록된 키워드 중 하나라도 문장에 포함되면 해당 이벤트로 판단한다.
        if any("".join(keyword.split()) in normalized for keyword in TEXT_KEYWORDS[event]):
            return event
    return Event.NONE


def detect_sound_event(
    predictions: Iterable[tuple[str, float]], threshold: float
) -> tuple[Event, str, float]:
    """소리 분류 결과 중 임계값을 넘은 가장 위험한 이벤트를 고른다."""

    # best_* 변수에는 현재까지 찾은 가장 적절한 소리 이벤트를 저장한다.
    best_event = Event.NONE
    best_label = ""
    best_score = 0.0

    # predictions는 YAMNet이 반환한 (라벨, 점수) 목록이다.
    # 점수가 threshold 이상이고 라벨에 위험 키워드가 들어 있으면 후보 이벤트가 된다.
    for label, score in predictions:
        for event, keywords in SOUND_KEYWORDS.items():
            if score >= threshold and any(keyword in label.lower() for keyword in keywords):
                # 서로 다른 이벤트가 동시에 감지되면 우선순위가 높은 이벤트를 선택한다.
                # 같은 이벤트라면 점수가 더 높은 라벨을 선택한다.
                if EVENT_PRIORITY[event] > EVENT_PRIORITY[best_event] or (
                    event == best_event and score > best_score
                ):
                    best_event, best_label, best_score = event, label, score
    return best_event, best_label, best_score


def detect_specific_sound_event(
    predictions: Iterable[tuple[str, float]], event: Event, threshold: float
) -> tuple[Event, str, float]:
    """특정 이벤트만 따로 검사한다. 비명은 더 민감하게 보기 위해 사용한다."""

    # 일반 소리 감지 기준과 별개로 특정 이벤트만 따로 보고 싶을 때 사용하는 함수이다.
    # 현재는 SCREAM을 더 낮은 threshold로 검사하기 위해 사용한다.
    best_label = ""
    best_score = 0.0
    for label, score in predictions:
        if score >= threshold and any(
            keyword in label.lower() for keyword in SOUND_KEYWORDS[event]
        ):
            if score > best_score:
                best_label = label
                best_score = score
    return (event, best_label, best_score) if best_label else (Event.NONE, "", 0.0)


def merge_events(stt_event: Event, sound_event: Event) -> Event:
    """음성 안내 이벤트와 주변 소리 이벤트 중 우선순위가 높은 것을 반환한다."""

    # 예: 좌회전 안내와 사이렌이 동시에 감지되면 사이렌이 더 중요하므로 SIREN을 반환한다.
    return max((stt_event, sound_event), key=EVENT_PRIORITY.get)


class WhisperTranscriber:
    """faster-whisper 모델로 한국어 안내 음성을 텍스트로 변환한다."""

    def __init__(self, model_name: str, device: str, compute_type: str) -> None:
        # faster_whisper는 음성 인식 모델이 필요할 때만 로드한다.
        # PC/Colab 환경에 따라 cpu 또는 cuda를 선택할 수 있게 인자로 받는다.
        from faster_whisper import WhisperModel

        logging.info(
            "Loading faster-whisper model: %s (device=%s, compute_type=%s)",
            model_name,
            device,
            compute_type,
        )
        self.model = WhisperModel(
            model_name,
            device=device,
            compute_type=compute_type,
        )

    def transcribe(self, audio) -> str:
        """오디오 배열을 한국어 문장으로 변환한다."""

        # language="ko"로 지정해서 한국어 음성으로 인식한다.
        # beam_size=1은 속도를 우선한 설정이다.
        # vad_filter=True는 말소리가 아닌 구간을 어느 정도 걸러 준다.
        # condition_on_previous_text=False는 이전 문장에 끌려가 오인식되는 것을 줄이기 위한 설정이다.
        segments, _ = self.model.transcribe(
            audio,
            language="ko",
            beam_size=1,
            vad_filter=True,
            condition_on_previous_text=False,
        )
        return " ".join(segment.text.strip() for segment in segments).strip()


class YamnetClassifier:
    """YAMNet 모델로 사이렌, 경적, 비명 같은 주변 소리를 분류한다."""

    def __init__(self) -> None:
        # csv는 모델 라벨 파일을 읽고, tensorflow_hub는 YAMNet 모델을 불러온다.
        # YAMNet은 TensorFlow Hub에 공개된 사전 학습 소리 분류 모델이다.
        import csv
        import tensorflow_hub as hub

        logging.info("Loading YAMNet from TensorFlow Hub")
        self.model = hub.load("https://tfhub.dev/google/yamnet/1")

        # 모델이 반환하는 점수는 숫자 인덱스 기준이므로,
        # class_map_path()에서 라벨 파일을 읽어 인덱스를 사람이 읽는 이름으로 바꾼다.
        class_map_path = self.model.class_map_path().numpy().decode("utf-8")
        with open(class_map_path, newline="", encoding="utf-8") as class_map:
            self.class_names = [row["display_name"] for row in csv.DictReader(class_map)]

    def classify(self, audio, top_k: int = 521) -> list[tuple[str, float]]:
        """오디오를 YAMNet에 넣고 점수가 높은 라벨 순서로 반환한다."""

        import numpy as np
        import tensorflow as tf

        # YAMNet은 TensorFlow tensor 형식의 float32 waveform을 입력으로 받는다.
        waveform = tf.convert_to_tensor(audio, dtype=tf.float32)

        # scores는 시간 프레임별 라벨 점수이다.
        # 한 오디오 조각 안에서 한 번이라도 강하게 감지된 소리를 잡기 위해 max(axis=0)을 사용한다.
        scores, _, _ = self.model(waveform)
        peak_scores = np.asarray(scores).max(axis=0)

        # 점수가 높은 순서로 정렬해서 상위 라벨만 반환한다.
        indices = np.argsort(peak_scores)[::-1][:top_k]
        return [(self.class_names[index], float(peak_scores[index])) for index in indices]
