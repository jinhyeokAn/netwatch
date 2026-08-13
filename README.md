# NetWatch

미니 네트워크 관제 대시보드 (모니터링 / 장애 알림 / IP 관리 / 작업 영향도 관리)

## 로컬 실행

```bash
python -m venv venv
venv\Scripts\activate        # macOS/Linux는 source venv/bin/activate
pip install -r requirements.txt
python app.py
```

브라우저에서 http://localhost:5000 접속.

## 환경변수 설정법

아래 나오는 `set VAR=값` 방식은 터미널 켤 때마다 다시 쳐야 함. 매번 치기 귀찮으면 프로젝트 폴더에 `.env` 파일 만들어서 넣어두면 앱 시작할 때 자동으로 읽음 (이미 `.gitignore`에 있어서 git에는 안 올라감):

```
EMAIL_FROM=보내는사람@gmail.com
EMAIL_APP_PASSWORD=생성된16자리비밀번호
EMAIL_TO=받는사람@gmail.com
DISCORD_WEBHOOK_URL=여기에_웹후크_URL
```

## 로그인

기본 관리자 계정: `admin` / `changeme123` (첫 실행시 자동 생성됨)

**보안상 꼭 바꾸세요:**
```bash
set ADMIN_USERNAME=원하는아이디
set ADMIN_PASSWORD=원하는비밀번호
python app.py
```
DB가 이미 만들어진 상태(계정이 이미 있음)라면 위 설정은 무시됨 - "사용자관리" 메뉴에서 새 계정 추가하고 기존 admin은 그냥 안 쓰면 됨.

세션 암호화 키도 바꾸는 게 좋음(원래는 랜덤 값 써야 함):
```bash
set SECRET_KEY=아무렇게나긴랜덤문자열
```

## 이메일 리포트 설정 (선택)

Gmail 기준. **앱 비밀번호**가 필요함 (일반 로그인 비밀번호 아님):
1. 보낼 계정으로 쓸 Gmail에서 2단계 인증 켜기
2. https://myaccount.google.com/apppasswords 에서 앱 비밀번호 생성 (16자리)
3. 아래처럼 설정:

```bash
set EMAIL_FROM=보내는사람@gmail.com
set EMAIL_APP_PASSWORD=생성된16자리비밀번호
set EMAIL_TO=받는사람@gmail.com
python app.py
```

테스트 계정 하나만 있으면 `EMAIL_FROM`과 `EMAIL_TO`에 같은 주소 넣어도 됨 (자기 자신한테 보내기).

"통계" 메뉴에서 "지금 리포트 이메일로 발송" 버튼으로 즉시 테스트 가능.

**매일 자동 발송하려면:**
```bash
set EMAIL_REPORT_ENABLED=true
set EMAIL_REPORT_HOUR=9
```
설정한 시각(24시간제, 서버 로컬시간)에 매일 1회 자동 발송됨.

## Discord 알림 설정 (선택)

Discord 서버 > 채널 설정 > 연동 > 웹후크 에서 URL 발급 후:

```bash
set DISCORD_WEBHOOK_URL=여기에_웹후크_URL      # macOS/Linux는 export
python app.py
```

## 체크 주기 조절 (선택)

기본 30초. 바꾸려면:

```bash
set CHECK_INTERVAL_SEC=10
```

## 사용법

1. 로그인 (기본 admin/changeme123)
2. "장비 관리"에서 모니터링할 장비 등록 (IP는 실제 접근 가능한 것: 공유기, 본인 PC, 8.8.8.8 등)
3. "대시보드"에서 실시간 상태 확인 (체크주기마다 자동 새로고침됨)
4. 장애 발생하면 "장애 이력"에 자동 기록, Discord 설정했으면 알림도 옴
5. 점검 예정이면 "작업 일정"에 등록 - 해당 시간엔 알림 안 옴, 대시보드에 "점검중" 표시
6. "통계"에서 가동률/MTTR 확인, 이메일 리포트 발송
7. "감사로그"에서 누가 뭘 했는지 이력 확인
8. "서브넷계산기"는 독립 도구, 로그인만 하면 언제든 사용

## 배포 (Render.com 무료)

1. 이 폴더를 GitHub 저장소로 push
2. render.com 가입 후 New > Web Service > 저장소 연결
3. Build Command: `pip install -r requirements.txt`
4. Start Command: `python app.py`
5. Environment 탭에서 `DISCORD_WEBHOOK_URL` 등록 (필요시)
