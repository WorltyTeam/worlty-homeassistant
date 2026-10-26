<p align="center">
  <img src="https://raw.githubusercontent.com/WorltyTeam/worlty-homeassistant/v2.0.3/assets/products/worlty-hub-hero.png" alt="Worlty 월티 허브" width="640">
</p>

<p align="center">월티에 연결된 조명·냉난방·환기 기기를 Home Assistant에서도 사용하세요.</p>

<p align="center">
  <img src="https://raw.githubusercontent.com/WorltyTeam/worlty-homeassistant/v2.0.3/assets/badges/version.svg" alt="버전 2.0.3">
  <img src="https://raw.githubusercontent.com/WorltyTeam/worlty-homeassistant/v2.0.3/assets/badges/home-assistant.svg" alt="Home Assistant 2026.9.2 이상">
</p>

[버전별 변경 내역](https://github.com/WorltyTeam/worlty-homeassistant/blob/release/2.0.0/CHANGELOG.md)

| 클래식 패드 | 월티 허브 |
| :---: | :---: |
| <img src="https://raw.githubusercontent.com/WorltyTeam/worlty-homeassistant/v2.0.3/assets/products/classic-pad.jpg" alt="클래식 패드 제품 사진" width="240"> | <img src="https://raw.githubusercontent.com/WorltyTeam/worlty-homeassistant/v2.0.3/assets/products/worlty-hub.png" alt="월티 허브 제품 사진" width="240"> |
| 월티 앱의 액세스 토큰으로 연결합니다. | 월티 앱에서 연결 요청을 승인합니다. |
| [클래식 패드 연결 안내](#클래식-패드-연결) | [월티 허브 연결 안내](#월티-허브-연결) |

Worlty 하나로 두 제품을 모두 연결할 수 있습니다.

## 설치

Home Assistant **2026.9.2 이상**이 필요합니다. Home Assistant와 월티 제품을 같은 네트워크에 연결하고, 월티 앱에서 제품이 온라인인지 확인해 주세요.

1. 아래 버튼을 눌러 HACS에 Worlty 저장소를 추가하고 내려받으세요.

   [![HACS에서 Worlty 열기](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=WorltyTeam&repository=worlty-homeassistant&category=integration)

2. 설치가 끝나면 Home Assistant를 재시작하세요. 업데이트한 뒤에도 재시작해 주세요.

<details>
<summary>버튼으로 설치할 수 없거나 HACS를 사용하지 않는 경우</summary>

**HACS에 직접 추가하기**

HACS의 **사용자 지정 저장소**에 `https://github.com/WorltyTeam/worlty-homeassistant`를 입력하고, 종류를 **통합구성요소**로 선택해 내려받으세요.

**HACS 없이 설치하기**

이 저장소를 내려받아 `custom_components/worlty_core` 폴더를 Home Assistant 설정 폴더의 `custom_components` 아래에 복사하세요.

어느 방법으로 설치하든 끝나면 Home Assistant를 재시작해 주세요.

</details>

## 연결

사용하는 제품의 안내를 따라 주세요.

### 클래식 패드 연결

1. 월티 앱에서 연결할 클래식 패드의 **액세스 토큰**을 복사하세요.
2. Home Assistant의 **설정 > 기기 및 서비스**에서 검색된 패드를 선택하세요.
3. 토큰을 붙여 넣고 연결을 완료하세요. 주소와 포트 입력란이 보이면 월티 앱에서 확인한 값을 입력해 주세요.

클래식 패드가 여러 대라면 각 패드의 토큰으로 한 대씩 연결하세요.

### 월티 허브 연결

1. 월티 앱에서 연결할 허브의 **Home Assistant 연결 등록**을 열어 주세요.
2. Home Assistant의 **설정 > 기기 및 서비스**에서 검색된 허브를 선택하고 연결 요청을 보내세요.
3. 월티 앱에서 해당 요청을 승인하세요. 승인되면 Home Assistant에서 연결이 완료됩니다.

**제품이 자동으로 검색되지 않나요?** 아래 버튼을 누르거나 **통합구성요소 추가 > Worlty**로 들어가 사용하는 제품을 선택하세요. 클래식 패드는 월티 앱의 주소·포트·토큰을, 월티 허브는 `.local`로 끝나는 주소를 입력하면 됩니다. 예: `worlty-123abc.local`

[![Home Assistant에 Worlty 추가하기](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=worlty_core)

연결을 마치면 **설정 > 기기 및 서비스 > Worlty**에서 기기와 현재 상태를 확인할 수 있습니다. 원하는 기기를 대시보드나 자동화에 추가해 보세요.

<details>
<summary>클래식 패드의 상세 설정 표시하기</summary>

연결한 클래식 패드의 **구성**을 열어 표시 모드를 선택하세요.

- **기본**: 기기 제어·센서와 루틴·타이머·자동 켜기/끄기 스위치
- **고급**: 기본 항목과 시간·온도·보정값 등의 상세 설정

자동화에서 상세 설정을 사용하고 있다면 **고급** 모드를 선택해 주세요. 필터 잔여 시간과 타이머의 반복 간격·작동 시간 등은 월티 앱에서 변경하세요.

</details>

<details>
<summary>예전 월티 통합구성요소에서 옮기기</summary>

예전 `worlty` 통합구성요소를 사용 중인 경우에만 아래 절차가 필요합니다. 현재 Worlty를 사용 중이라면 업데이트 후 다시 등록하지 않아도 됩니다.

1. Home Assistant 설정을 백업하고 기존 월티 통합구성요소를 비활성화합니다.
2. 새 **Worlty** 통합구성요소에서 기기를 다시 연결합니다.
3. 자동화·대시보드의 엔티티를 새 항목으로 바꾸고, 이름·영역·라벨을 옮깁니다.
4. 정상 동작을 확인한 뒤 기존 통합구성요소를 삭제합니다.

</details>

## 문제 해결

| 증상 | 확인할 내용 |
| --- | --- |
| 기기가 자동으로 검색되지 않아요. | 제품이 켜져 있고 같은 네트워크에 연결되어 있는지 확인한 뒤, 위의 **Home Assistant에 Worlty 추가하기** 버튼으로 직접 연결해 주세요. |
| 클래식 패드의 토큰 오류가 나요. | 연결할 패드가 맞는지 확인하고, 월티 앱에서 해당 패드의 토큰을 다시 복사해 주세요. |
| 월티 허브의 승인을 기다리고 있어요. | 월티 앱에서 Home Assistant 연결 등록이 열려 있는지 확인하고 요청을 승인해 주세요. |
| 연결했던 기기가 응답하지 않아요. | 먼저 월티 앱에서 제품이 온라인인지 확인하고, Home Assistant와 같은 네트워크에 연결되어 있는지 확인해 주세요. |

문제가 계속되면 [문제 제보](https://github.com/WorltyTeam/worlty-homeassistant/issues/new/choose)로 알려 주세요.
로그나 화면을 첨부할 때는 액세스 토큰 등 인증 정보와 개인정보를 지워 주세요.
