# GitHub Project 다중 저장소 동기화

## 목적

GitHub Projects의 기본 Auto-add 워크플로 수 제한과 관계없이 여러 저장소의 열린 Issue와 Pull Request를 하나의 Project에 모은다. 이 자동화는 LLM을 호출하지 않으므로 AI 토큰 비용이 없다.

대상 Project:

- 소유자: `GeonwooKim-fitogether`
- 번호: `1`
- 이름: `전체 웹앱 개발 관리`

현재 대상 저장소:

- `GeonwooKim-fitogether/My-Rosary-App`
- `fitogether-org/Fitstack`
- `GeonwooKim-fitogether/atelier`
- `GeonwooKim-fitogether/Template-repository`

## 동작 방식

`.github/workflows/project-sync.yml`이 30분마다 실행된다.

1. 개인 Project #1의 기존 항목 ID를 모두 읽는다.
2. 각 대상 저장소의 열린 Issue와 PR을 페이지 끝까지 읽는다.
3. Project에 없는 항목만 추가한다.
4. 이미 등록된 항목은 건너뛴다.

GitHub Project의 기존 `Item added to project → Status: Todo` 워크플로가 새 항목의 상태를 설정한다.

## 최초 설정

저장소의 `Settings → Secrets and variables → Actions`에 `PROJECTS_TOKEN` Secret을 만든다.

토큰은 다음 최소 권한을 가져야 한다.

- 위 목록에 있는 저장소의 Issue와 Pull Request를 읽을 권한
- `GeonwooKim-fitogether`의 개인 Project를 읽고 수정할 권한
- 조직에서 SSO 승인이 필요한 경우 `fitogether-org` 사용 승인

토큰 값은 파일이나 로그에 적지 않는다.

설정 후 Actions에서 `GitHub Project 다중 저장소 동기화`를 골라 `Run workflow`로 한 번 실행한다. 실행 요약에서 새로 추가된 항목 수와 건너뛴 항목 수를 확인한다.

## 저장소 추가

워크플로의 `REPOSITORIES` 목록에 `owner/repository` 형식으로 한 줄을 추가한다. 별도의 GitHub Projects Auto-add 워크플로를 만들 필요는 없다.

## 제한

- 최대 30분의 반영 지연이 있다.
- 열린 Issue와 PR만 추가한다. 닫힌 항목의 상태 변경은 GitHub Project의 기존 기본 워크플로가 처리한다.
- Priority, 구분, 다음 행동 같은 사용자 정의 필드는 자동으로 채우지 않는다.
- `PROJECTS_TOKEN`이 없거나 권한이 부족하면 워크플로는 명시적으로 실패한다.
