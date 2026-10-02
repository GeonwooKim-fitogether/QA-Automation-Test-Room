# 웹앱 환경 스타터

이 폴더는 프로젝트에 그대로 복사하기 위한 시작점이다. 프레임워크 종속 코드는 넣지 않고 PostgreSQL 기반 환경 경계만 제공한다.

## 적용 순서

1. 이 폴더의 내용을 대상 저장소 루트로 복사한다.
2. `.env.example`을 복사해 `.env.local`을 만들고 로컬 값만 입력한다.
3. `.env.local`을 git에 커밋하지 않는다.
4. 아래 명령으로 로컬 DB를 실행한다.

```bash
docker compose --env-file .env.local -f compose.local.yml up -d
docker compose --env-file .env.local -f compose.local.yml ps
```

5. 앱이 `DATABASE_URL`을 읽도록 연결한다.
6. 프로젝트의 migration 도구에 맞춰 `db/migrations/`을 사용한다.

## 종료와 초기화

DB 종료:

```bash
docker compose --env-file .env.local -f compose.local.yml down
```

로컬 데이터까지 완전히 삭제:

```bash
docker compose --env-file .env.local -f compose.local.yml down -v
```

`down -v`는 local 데이터만 지울 때 사용한다. development·staging·production에는 실행하지 않는다.
