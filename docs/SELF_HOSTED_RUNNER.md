# 집에서 폴링하기 — self-hosted runner 설치

## 왜 필요한가

2026-09 코레일-SR 통합 후, 코레일+ 백엔드는 **데이터센터/VPN IP의 로그인을
차단**합니다 (응답 코드 `-8202`, "VPN 또는 데이터센터를 통해서는 서비스를
이용할 수 없습니다"). GitHub Actions의 기본 러너는 Azure 데이터센터라서
자격증명이 맞아도 로그인 자체가 불가능합니다. 그래서 **폴링 잡만** 집의
기기(주거용 IP)에서 돌립니다. cron·시크릿·워크플로 구조는 그대로이고,
`runs-on`만 `[self-hosted, korail-home]`으로 바뀌었습니다.

CI(pytest)·Cloudflare 배포는 계속 GitHub 호스팅 러너에서 돕니다 — 집 기기는
코레일에 접속하는 잡만 맡습니다.

## 준비물

- 상시 켜져 있는 기기 1대: 라즈베리파이(권장, 아무 모델이나 64-bit OS),
  유휴 맥, 미니PC 등
- Python **3.10 이상** (`python3 --version`으로 확인 — 라즈베리파이 OS
  bookworm은 3.11, macOS는 brew python이면 충분)
- 집 인터넷 (주거용 회선이면 됩니다)

## 설치 (약 10분, 한 번만)

1. **GitHub에서 러너 등록 페이지 열기**
   저장소 → Settings → Actions → Runners → **New self-hosted runner**
   → 기기 OS/아키텍처 선택 (라즈베리파이 = Linux / ARM64)

2. **페이지에 나오는 명령을 기기에서 그대로 실행** — 대략 이런 모양입니다:

   ```sh
   mkdir actions-runner && cd actions-runner
   curl -o actions-runner-….tar.gz -L https://github.com/actions/runner/releases/download/…
   tar xzf actions-runner-….tar.gz
   ./config.sh --url https://github.com/yuangunn/ktx-srt-watcher --token <페이지의 토큰>
   ```

   `config.sh`가 물어보는 것 중 **라벨만** 중요합니다:
   - **labels**: `korail-home` ← 반드시 이 라벨을 추가 (워크플로가 이 라벨로 찾습니다)
   - 나머지(러너 이름, 작업 폴더)는 기본값 Enter

3. **서비스로 등록** (재부팅해도 자동 시작):

   ```sh
   # Linux (라즈베리파이)
   sudo ./svc.sh install && sudo ./svc.sh start

   # macOS
   ./svc.sh install && ./svc.sh start
   ```

4. **확인**: Settings → Actions → Runners에 초록 "Idle"로 보이면 끝.
   다음 3분 크론부터 폴링이 집에서 돕니다. 첫 정상 폴링이 되면 텔레그램으로
   "✅ 코레일 검색 복구" 알림이 옵니다 — 그게 성공 신호입니다.

## 공개 저장소에서의 보안 (중요)

이 저장소는 공개라서, 외부인이 fork PR로 러너에서 코드를 실행하려 들 수
있습니다. 다음이 방어선입니다:

1. **Settings → Actions → General → Fork pull request workflows**에서
   **"Require approval for all outside collaborators"** 를 켜 두세요.
2. 셀프 호스티드 러너를 쓰는 워크플로(`watch.yml`, `api-probe.yml`)에는
   `pull_request` 트리거가 **없습니다** — schedule / repository_dispatch /
   workflow_dispatch 뿐이라 외부 PR이 이 러너에 닿을 수 없습니다.
   앞으로도 셀프 호스티드 잡에 `pull_request` 트리거를 붙이지 마세요.
3. `pull_request`가 있는 `tests.yml`은 `ubuntu-latest`(GitHub 호스팅)에서만
   돕니다. 그대로 두세요.
4. 러너는 관리자 권한 없는 일반 계정으로 돌리는 걸 권합니다.

## 러너가 꺼져 있으면

잡이 큐에 쌓이지 않습니다 — `concurrency` 그룹이 최신 틱만 남기고,
15분 이상 큐에 있으면 CF 워커가 취소합니다. 기기가 다시 켜지면
다음 틱부터 자연히 재개됩니다. 며칠 꺼두면 그동안 감시가 멈출 뿐,
고장나는 것은 없습니다.

## 왜 다른 방법이 아닌가

- **한국 클라우드 IP**: 여전히 데이터센터 대역이라 같은 차단에 걸릴
  가능성이 크고, 월 비용이 듭니다.
- **상용 주거용 프록시**: 약관·법적 회색지대라 권하지 않습니다.
- **CF Worker에서 직접 폴링**: CF도 데이터센터이고, pykorail의 TLS 위장
  (curl-cffi 네이티브 코드)은 Workers 런타임에서 돌지 않습니다.
