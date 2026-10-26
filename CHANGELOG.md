# 변경 내역

## v2.0.3 · 2026-10-26

월티 허브 검색 과정에서 불필요하게 기다릴 수 있는 구간을 줄였습니다.

기존 연결과 기기·엔티티 ID 및 저장 설정은 유지됩니다. 업데이트한 뒤에는 Home Assistant를 재시작해 주세요.

[설치·연결 안내](https://github.com/WorltyTeam/worlty-homeassistant#readme)

## v2.0.2 · 2026-10-25

월티 허브의 기기 등록·조회 방식을 최신 Home Assistant 규격에 맞춰, 향후 버전에서 지원이 종료될 기능의 사용을 정리했습니다.

기존 연결과 기기·엔티티 ID, 이름·영역·라벨 및 저장 설정은 유지됩니다. 업데이트한 뒤에는 Home Assistant를 재시작해 주세요.

[설치·연결 안내](https://github.com/WorltyTeam/worlty-homeassistant#readme)

## v2.0.1 · 2026-10-25

HACS에서 제품 이미지와 버전 배지가 정상으로 표시되도록 이미지 주소를 수정했습니다.

[설치·연결 안내](https://github.com/WorltyTeam/worlty-homeassistant#readme)

## v2.0.0 · 2026-10-25

클래식 패드와 월티 허브에 연결된 기기를 Home Assistant에서도 제어하세요.

### 주요 내용

- 클래식 패드와 월티 허브를 **Worlty 통합구성요소 하나**로 연결할 수 있습니다.
- 자동 검색에서 연결할 제품의 이름을 확인할 수 있습니다.
- 클래식 패드는 **기본·고급 표시 모드**를 선택할 수 있습니다. 기본 모드에서도 기기 제어와 루틴·타이머·자동 켜기/끄기 스위치를 사용할 수 있습니다.
- 냉난방 프리셋의 상태 표시와 사용자 지정 허브 이름 반영을 개선했습니다.
- 연결 문제를 확인할 수 있도록 진단 정보를 보강했습니다.

### 지원 환경

Home Assistant **2026.9.2 이상**이 필요합니다.

### 설치 및 업데이트

[설치·연결 안내](https://github.com/WorltyTeam/worlty-homeassistant/blob/release/2.0.0/README.md)를 따라 주세요. 설치하거나 업데이트한 뒤에는 Home Assistant를 재시작해 주세요.

현재 Worlty를 업데이트할 때는 기기를 다시 등록할 필요가 없습니다. 예전 `worlty` 통합구성요소를 사용 중이라면 README의 이관 안내를 먼저 확인해 주세요.
