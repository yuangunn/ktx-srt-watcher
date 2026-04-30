# KTX/SRT 취소표 감시 봇

GitHub Actions cron + Cloudflare Worker bridge가 30분마다 [코레일](https://www.letskorail.com)/[SRT](https://etk.srail.kr)를 폴링해 취소표를 잡고, 텔레그램으로 알림을 보냅니다. 옵션으로 임시예약(20분 hold)까지 자동으로 잡고, 결제 마감 reminder(T+5/10/15/19분)도 자동 발송합니다. **결제는 절대 자동화하지 않습니다** — 본인 직접.

PWA([Cloudflare Pages](https://pages.cloudflare.com))로 워치 추가/수정/삭제, 통계 보기, 로그 확인. iOS Safari에서 standalone 설치 가능.

---

## 무엇이 동작하나

- **취소표 감시** — 30분 cron이 `[수원→부산] 2026-05-23 06:00–14:00 KTX` 같은 워치를 돌리며 잔여석 발견 시 텔레그램 발송
- **자동 임시예약** (옵션) — 잔여석 발견 즉시 20분 hold. 결제는 코레일톡/SR 앱에서 직접
- **대기예약 fallback** (옵션) — 매진된 좌석에 대해 코레일 `try_waiting` / SRT `reserve_standby`로 대기 등록
- **결제 마감 reminder** — 임시예약 성공 시 T+5/10/15/19분에 텔레그램 reminder 자동 전송
- **PWA 설정 화면** — 워치 CRUD, 폴링 간격, 조용한 시간, 빈 결과 알림 토글
- **통계 + 최근 실행 로그** — 7일/30일 실행 횟수, 일별 막대 그래프, 최근 워크플로우 로그 모달
- **시스템 안전망** — Heartbeat(6시간 침묵 시 알림), PAT 만료 7일 전 경고, 일요일 21시 주간 요약

## 무엇이 동작하지 않나 (의도)

- ❌ **결제** — 카드 정보 저장/사용 안 함. 임시예약까지만, 결제는 사용자가 앱에서
- ❌ **명절/오픈런 초고경쟁** — 30분 cron으로는 매크로 봇 못 이김. 비성수기/평일 캔슬 대상
- ❌ **타인 좌석** — 본인 코레일/SR 계정으로만 동작

---

## 아키텍처

```
       ┌─────────────────────┐
       │  PWA (CF Pages)     │ 사용자가 워치 추가/수정/삭제,
       │  ktx-srt-watcher    │ 설정 변경, "지금 확인" 버튼,
       │  .pages.dev         │ 통계 / 최근 실행 로그 보기
       └──────────┬──────────┘
                  │ GitHub Contents API
                  │ (PAT 기반)
                  ▼
       ┌─────────────────────┐    ┌─────────────────────┐
       │  GitHub Repo        │◄───│  CF Worker          │
       │  (private)          │    │  (cloudflare-worker)│
       │  - config.json      │    │                     │
       │  - state.json       │    │  Cron */30:         │
       │  - .github/...      │    │   → repository_     │
       │  - worker/...       │    │     dispatch ──┐    │
       └──────────┬──────────┘                     │    │
                  ▼                                │    │
       ┌─────────────────────┐                     │    │
       │  GitHub Actions     │◄────────────────────┘    │
       │  ticket-watch.yml   │                          │
       │                     │   POST /reminder/        │
       │   python -m         │   schedule ◄─────────────┤
       │     worker.main     │                          │
       │                     │   Cron */1:              │
       │   - korail2 / SRT   │    → process REMINDERS   │
       │   - Telegram alert  │      KV → Telegram       │
       │   - state.json      │                          │
       │     commit          │   Cron */6h: heartbeat   │
       └──────────┬──────────┘   Cron daily: PAT check  │
                  │                                     │
                  │              Cron weekly:           │
                  ▼              digest                 │
       ┌─────────────────────┐                          │
       │  텔레그램 봇 알림   │◄─────────────────────────┘
       └─────────────────────┘
```

---

## 셋업 (자기 인스턴스 만들기)

### 0. 사전 준비

- **GitHub** 계정 (free OK, private repo가 됨)
- **Cloudflare** 계정 (free OK)
- **Node.js 18+** + `npm`
- 코레일 / SR 본인 계정 (앱 로그인되는 그 ID/PW)
- 텔레그램 (봇은 [@BotFather](https://t.me/BotFather)에서 새로 발급 가능)

### 1. 레포 fork

```bash
gh repo fork yuangunn/ktx-srt-watcher --clone
cd ktx-srt-watcher
```

또는 GitHub 웹에서 Fork 후 `git clone`.

### 2. 텔레그램 봇 만들기

[@BotFather](https://t.me/BotFather)에서:
- `/newbot` → 이름 / username 입력 → 발급된 token 메모
- 본인 봇 채팅창에 `/start` 한 번 보내기 (서버가 chat_id 알려면 첫 메시지 필요)
- chat_id 확인: `https://api.telegram.org/bot<TOKEN>/getUpdates` 의 `chat.id`

### 3. GitHub Secrets 등록

본인 fork된 repo Settings → Secrets and variables → Actions:

| Secret | 값 |
|---|---|
| `KORAIL_ID` | 코레일 회원번호 (10자리 숫자) — 또는 가입 이메일 |
| `KORAIL_PW` | 코레일 비밀번호 |
| `SRT_ID` | SR 회원번호 (10자리) — 휴대폰 번호 형식도 가능 (`010XXXXXXXX`) |
| `SRT_PW` | SR 비밀번호 |
| `TELEGRAM_BOT_TOKEN` | BotFather에서 발급받은 token |
| `TELEGRAM_CHAT_ID` | 본인 chat_id (숫자) |

`CF_WORKER_URL` + `REMINDER_TOKEN`은 CF Worker 배포 후에 추가 (4단계).

### 4. Cloudflare Worker 배포

```bash
cd cloudflare-worker
npm install
npx wrangler login            # 브라우저에서 CF 계정 인증
npx wrangler kv namespace create REMINDERS    # KV 생성, 출력의 id를 wrangler.toml [[kv_namespaces]] 항목에 복사
```

**wrangler.toml** 안에 `[[kv_namespaces]] id` 값 갱신:
```toml
[[kv_namespaces]]
binding = "REMINDERS"
id = "<여기에 방금 출력된 id>"
```

또 `[vars] GITHUB_REPO` 값을 본인 fork 경로로 갱신:
```toml
[vars]
GITHUB_REPO = "<your-username>/ktx-srt-watcher"
```

CF Worker secret 등록:

```bash
# fine-grained PAT 만들고 (github.com/settings/personal-access-tokens/new)
#   - Repository access: 본인 fork만
#   - Permissions: Contents Read/write + Actions Read/write
# 발급된 github_pat_... 값을 입력
npx wrangler secret put GITHUB_TOKEN

# 텔레그램
npx wrangler secret put TELEGRAM_BOT_TOKEN
npx wrangler secret put TELEGRAM_CHAT_ID

# 임의의 strong shared secret (worker.main이 CF Worker에 인증할 때 사용)
# 예: openssl rand -hex 32
npx wrangler secret put REMINDER_TOKEN

npx wrangler deploy
```

배포 완료 출력의 URL(예: `https://ktx-srt-watcher-cron.<account>.workers.dev`)을 메모.

### 5. GitHub Secrets 추가

```bash
# CF Worker URL
gh secret set CF_WORKER_URL -R <your-username>/ktx-srt-watcher --body "https://ktx-srt-watcher-cron.<account>.workers.dev"

# 4단계의 REMINDER_TOKEN과 똑같은 값
gh secret set REMINDER_TOKEN -R <your-username>/ktx-srt-watcher --body "<same-value-as-cf>"
```

### 6. PWA 배포 (Cloudflare Pages)

1. [pages.cloudflare.com](https://pages.cloudflare.com) → Create project → Connect to Git → 본인 fork 선택
2. Build settings:
   - Framework preset: **None**
   - Build command: (비움)
   - Build output directory: `frontend`
3. Deploy → 출력 URL(예: `https://ktx-srt-watcher.pages.dev`)

iOS Safari로 그 URL 접속 → 공유 → 홈 화면에 추가.

### 7. PWA 첫 연결

PWA 첫 화면에서 PAT 입력:
- Repository: `<your-username>/ktx-srt-watcher`
- Personal Access Token: 위에서 만든 fine-grained PAT (4단계에서 발급한 거 재사용 가능)

### 8. 첫 워치 추가

PWA `+ 워치 추가` 버튼:
- 제공자, 출발/도착, 날짜, 시간 범위, 열차 종류, 인원, 좌석 등급, 자동 예약 토글

저장하면 GitHub의 `config.json`에 commit, 다음 cron tick에 폴링 시작.

---

## 설정 옵션 (PWA 설정 패널)

| 항목 | 의미 | 기본값 |
|---|---|---|
| 폴링 간격 | "기본"이면 CF cron 그대로 (30분), 또는 5/10/15/30/60분 중 선택해 더 느리게 throttle | 기본 |
| 빈 결과도 알림 | cron이 잔여 0건 발견해도 텔레그램으로 요약 메시지 발송 | OFF |
| 대기예약 자동 등록 | 매진 좌석에 대해 자동 대기예약 (코레일 `try_waiting` / SRT `reserve_standby`) | OFF |
| 조용한 시간 (KST) | 이 시간대 cron 알림은 음소거 (`disable_notification`). 수동/임시예약/reminder는 무관 | 비활성 |
| 워치별 자동 예약 | 좌석 발견 시 임시예약 자동 시도 | OFF |

---

## 비용 (free tier 안에 안전한 설정)

| 서비스 | 사용량 | 한도 | 여유 |
|---|---|---|---|
| GitHub Actions (private) | ~1,440분/월 (30분 cron × 1분/run × 31일) | 2,000분/월 | 안전 |
| Cloudflare Workers | ~3,000 invocations/일 (5개 cron + reminder) | 100,000/일 | 매우 여유 |
| Cloudflare Pages | 정적 파일 호스팅 | 500 build/월, 무제한 요청 | 여유 |
| Cloudflare KV | 30 reads + 30 writes/일 | 100k/일 | 여유 |

폴링 간격을 5분으로 낮추면 GitHub Actions 한도 초과 가능 — wrangler.toml의 cron을 수정하기 전 확인.

---

## 트러블슈팅

| 증상 | 원인 / 해결 |
|---|---|
| Actions가 30분이 아니라 50분~60분에 한 번씩 도는 듯 | GHA private free-tier가 schedule cron을 throttle. CF Worker가 `*/30 repository_dispatch`로 우회. CF Worker 정상 배포돼있는지 `npx wrangler tail` 확인 |
| 텔레그램 알림이 안 옴 | 1) `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` GitHub secret 확인. 2) 봇 채팅창에 `/start` 한 번 보냈는지. 3) PWA의 "조용한 시간"이 지금 시간대를 포함하는지 |
| 자동 예약 직후 "동일한 예약" 에러 | state.json commit이 다음 run보다 늦게 propagate된 case. 어댑터가 `WRR800029` / "동일한 예약" 메시지를 detect하면 silent dedupe — 정상 동작 |
| `결제 마감 12:06`처럼 시간이 이상함 | 이전 버그. 지금은 코레일/SRT 응답의 실제 deadline + KST 변환을 사용. 신규 임시예약부터 정상 |
| Run watcher 로그가 비어 보임 | `poll_interval_min` filter로 skip된 run. 모달 상단에 안내 배너 표시. 실제 조회 결과는 직전 실제 실행 클릭해서 확인 |
| PAT 만료 임박 알림이 옴 | CF Worker의 daily PAT check 동작 중. 안내된 절차로 토큰 regenerate + `npx wrangler secret put GITHUB_TOKEN` 갱신 |
| 워커가 6시간 침묵 알림 | Heartbeat 동작. CF Worker 또는 GHA Actions, PAT 점검 필요 |

---

## 라이선스 / 면책

- 코드: MIT 또는 본인 fork에서 자유 결정
- **Korail/SRT 약관 준수 필수** — 본인 좌석만, 영리 목적 금지, 매크로 의심 트래픽 자제
- 본 시스템은 비공식 (코레일/SR 공식 라이브러리 아님). 두 서비스의 anti-bot 정책 변경 시 동작 멈출 수 있음
- 결제 자동화 절대 금지 — 임시예약(20분 hold)까지만, 결제는 직접

## 디렉토리 구조

```
ktx-srt-watcher/
├── worker/                       # Python 백엔드
│   ├── main.py                   # 진입점
│   ├── adapters/
│   │   ├── base.py               # Provider Protocol
│   │   ├── korail.py             # 코레일 (chasehuh fork pin)
│   │   └── srt.py                # SRT (SRTrain)
│   ├── matcher.py
│   ├── notifier.py
│   ├── models.py
│   └── state.py                  # state.json 읽기/쓰기 + semantic merge
├── tests/                        # pytest, 150+
├── frontend/                     # PWA
│   ├── index.html
│   ├── DESIGN.md                 # 디자인 시스템 (google-labs/design.md format)
│   ├── manifest.json
│   ├── sw.js
│   ├── css/app.css
│   ├── js/app.js
│   └── icons/
├── cloudflare-worker/            # CF Worker (cron bridge + reminders + heartbeat)
│   ├── wrangler.toml
│   ├── src/index.js
│   └── package.json
├── .github/workflows/
│   └── watch.yml                 # GHA cron + dispatch handler
├── config.json                   # 사용자가 PWA로 편집
├── state.json                    # 워커가 자동 갱신
└── requirements.txt
```

기여 / 이슈는 GitHub PR로.
