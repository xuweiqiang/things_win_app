# Things Windows 비공식 클라이언트

[English](README.md) | [简体中文](README.zh-CN.md) | [日本語](README.ja.md) | [한국어](README.ko.md) | [Español](README.es.md)

공개 커뮤니티 프로토콜을 통해 Things Cloud를 읽고 쓰는 독립형 Windows
데스크톱 클라이언트입니다. macOS, iPhone, iPad의 Things와 작업을
동기화합니다.

> **비공식 프로젝트:** 이 프로젝트는 Cultured Code와 제휴하거나 지원 또는
> 승인을 받은 프로젝트가 아닙니다. Things는 Cultured Code의 상표입니다.
> 비공개 Cloud 프로토콜을 사용하므로 호환성 및 계정 위험이 있습니다.
> 중요한 데이터는 먼저 백업하세요.

## 기능

- pywebview와 Microsoft Edge WebView2로 구동되는 네이티브 데스크톱 창.
- Things Cloud의 원격 변경을 가져오고 Windows 편집을 동일한 history에 제출.
- 제목, 메모, 마감일, 태그, 프로젝트/영역 소속을 포함한 작업 및 프로젝트
  생성과 편집.
- Inbox, Today, Upcoming, Calendar, Anytime, Someday, Logbook, Trash 및
  프로젝트 보기.
- 예정일과 마감일을 표시하는 월간 캘린더. 날짜를 선택하여 해당 작업 확인.
- 선택한 캘린더 날짜에 작업을 만들고, 상세 패널에서 예정일을 변경.
- 어디서나 `Ctrl+T`로 새 작업 창을 열고 Today에 배치.
- 완료, 복원, 취소, 휴지통 이동, 휴지통에서 복원 및 정렬.
- 로컬 디스크 캐시, 영구 오프라인 쓰기 큐, 자동 재시도 및 충돌 해결 UI.
- Windows DPAPI로 보호되는 자격 증명과 선택적 로컬 PIN 검증.
- 첨부 파일은 Windows에 로컬 저장하고 메모에는 첨부 표시 텍스트만 동기화.

## 아직 검증되지 않은 항목

이번 릴리스에서는 실제 Things Cloud 계정 쓰기와 iPhone 왕복 동기화를
검증하지 않았습니다. 현재 검증 범위는 로컬 HTTP API, 영구 큐, 충돌 정책,
그리고 시뮬레이션된 Things Cloud에 대한 양방향 커밋입니다.

다음은 상호 운용성을 보장하는 내용이 아니라 호환성 설계입니다.

- 커뮤니티 Go SDK 규칙에 따라 완전한 Task7 생성 페이로드를 만듭니다.
- 커뮤니티 SDK에서 검증된 `ThingsMac/32209501` 호환 ID와 schema를
  사용합니다.
- 커밋이 성공하면 동일한 history를 읽는 macOS/iOS 클라이언트에서 Windows
  변경 사항을 볼 수 있어야 합니다.

실제 계정으로 아직 검증하지 않은 시나리오는 기존 계정 로그인, Apple 계정
보안 알림, 2단계 인증, 다양한 Things/iOS 버전, 반복 작업 및 Cloud 요청
제한입니다. 첫 검증에는 테스트 계정 또는 복원 가능한 백업을 사용하세요.

## 실행 요구 사항

- Windows 10/11 x64.
- 소스에서 실행할 경우 Python 3.10 이상.
- Microsoft Edge WebView2 Runtime(일반적으로 Windows 10/11에 포함).
- 활성 Things Cloud 계정.

설치 및 시작:

```powershell
python -m pip install -r requirements-dev.txt
python main_window.py
```

`启动.bat`을 두 번 클릭할 수도 있습니다. `dist`에 v3 실행 파일이 있으면
EXE를 먼저 시작하고, 없으면 `main_window.py`를 실행합니다.

브라우저에서 백엔드만 디버그:

```powershell
python app.py
```

백엔드는 기본적으로 `127.0.0.1:55234`에서만 수신합니다.

## EXE 빌드

`build_onefile.bat`을 두 번 클릭하거나 다음을 실행합니다.

```powershell
python -m PyInstaller --noconfirm --clean --onefile --windowed `
  --name ThingsWindows_v3.0.4 `
  --add-data "index.html;." `
  --add-data "vue.global.prod.js;." `
  --add-data "tailwindcss.js;." `
  --collect-all webview `
  main_window.py
```

실행 파일은 `dist\ThingsWindows_v3.0.4.exe`에 생성됩니다. `build/`,
`dist/`, 로그 및 런타임 데이터는 Git에 커밋하지 않습니다.

## 테스트

```powershell
python -m py_compile app.py things_cloud.py sync_engine.py main_window.py
python -m pytest -q
```

테스트는 메모리의 시뮬레이션된 Things Cloud를 사용하며 실제 계정에
접근하지 않습니다. 생성, 편집, 프로젝트/영역 이동, 일정, 마감일, 태그,
완료/취소, 휴지통, 오프라인 큐 재시작, 원격 변경 병합, 동일 필드 충돌,
충돌 재시도/포기 및 API 정적 자산을 다룹니다.

## 동기화 동작 및 제한

- 쓰기는 디스크 기반 outbox에 들어가며 Cloud 성공 후에만 제거됩니다.
- 커밋은 `ancestor-index`를 사용하며 HTTP 409가 발생하면 충돌 여부를
  판단하기 전에 원격 변경을 가져옵니다.
- 로컬과 원격이 같은 필드를 동시에 편집해도 자동으로 덮어쓰지 않습니다.
  UI에서 로컬 재시도 또는 로컬 변경 포기를 선택할 수 있습니다.
- 서로 다른 필드의 동시 편집은 가능한 범위에서 병합됩니다.
- history 손상을 막기 위해 현재 반복 작업 수정은 거부됩니다.
- 영구 삭제는 구현되지 않았으며 복구 가능한 휴지통만 지원합니다.
- 첨부 파일 본문은 Things Cloud에 업로드되지 않습니다. 메모의 첨부 표시만
  동기화됩니다.
- 메모는 CRC32 체크섬이 포함된 Things 일반 텍스트 객체로 기록됩니다.

구현 세부 사항은 [중국어 아키텍처 및 프로토콜 문서](docs/ARCHITECTURE.md)를
참조하세요.

## 데이터 위치

애플리케이션 데이터는 `%APPDATA%\ThingsWindows\`에 저장됩니다. 각 Cloud
history 캐시는 `%APPDATA%\ThingsWindows\accounts\<history-hash>\`에
저장됩니다.

- `history_items.json`: 로컬 Cloud 상태 캐시.
- `known_head.json`: 마지막으로 확인된 원격 head.
- `outbox.json`: 제출 대기 중인 쓰기 작업.
- `conflicts.json`: 영구 저장된 충돌 기록.
- `sync_state.json`: 최신 동기화 상태.
- `credentials.dat`: 애플리케이션 디렉터리의 DPAPI 암호화 자격 증명.
- `pins.json`: 애플리케이션 디렉터리의 고정 항목.
- `attachments\`: 애플리케이션 디렉터리의 로컬 첨부 파일.

로그는 EXE 또는 소스 디렉터리의 `things_debug.log`에 기록됩니다. 이 파일은
Git에서 무시됩니다.

## 참고 자료 및 라이선스

동기화 프로토콜 참고 자료:

- [arthursoares/things-cloud-sdk](https://github.com/arthursoares/things-cloud-sdk)
- [evanpurkhiser/things3-cloud](https://github.com/evanpurkhiser/things3-cloud)

이 프로젝트는 커뮤니티 소스의 MIT 라이선스와 저작권 고지를 유지합니다.
[LICENSE](LICENSE)를 참조하세요. 함께 제공되는 Vue 및 Tailwind 브라우저
빌드도 각각의 MIT 라이선스 고지를 유지합니다.
