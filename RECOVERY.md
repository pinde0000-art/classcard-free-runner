# 복구 안내 (포맷 후 다시 세팅하기)

이 문서는 PC를 포맷했거나 새 컴퓨터로 옮겼을 때 CLJ를 되살리는 절차다.

## 먼저 알아둘 것

**서비스는 PC가 없어도 계속 돌아간다.** 웹앱은 GitHub Pages, 실행은 GitHub
Actions, 계정 저장소는 Cloudflare에 있다. PC는 코드를 고치는 도구일 뿐이다.
그러니 포맷했다고 급할 것 없다.

## 무엇이 어디에 있나

| 항목 | 위치 | 포맷 영향 |
|---|---|---|
| 코드 전체 (docs, worker, handler, *.py, workflows) | GitHub `pinde0000-art/classcard-free-runner` | 없음 |
| 배포된 웹앱 | GitHub Pages (`main` 브랜치의 `/docs`) | 없음 |
| Worker 실행본 | Cloudflare Workers | 없음 |
| 계정 자격증명 (암호화 상태) | Cloudflare KV `classcard-accounts` | 없음 |
| GitHub Secret (`RUNNER_KEY`, `CLASSCARD_ID`, `CLASSCARD_PASSWORD`) | GitHub 저장소 설정 | 없음 |
| 구버전·실험 코드 | GitHub `pinde0000-art/classcard-archive` (비공개) | 없음 |
| **비밀키 원본 값** | **OneDrive `classcard-secrets/` 또는 비밀번호 관리자** | **여기 없으면 영영 못 되찾음** |

## 복구 절차

### 1. 도구 설치

- Git
- GitHub CLI (`gh`) — 설치 후 `gh auth login`
- Node.js (Worker 배포용)
- Python 3.12 (러너 스크립트 확인용. 실제 실행은 Actions가 한다)

### 2. 코드 받기

```bash
gh repo clone pinde0000-art/classcard-free-runner
cd classcard-free-runner
```

구버전 참고가 필요하면:

```bash
gh repo clone pinde0000-art/classcard-archive
```

### 3. 비밀키 되돌리기 (필요할 때만)

Cloudflare와 GitHub에 이미 등록돼 있으므로 **평소에는 할 필요가 없다.**
Cloudflare 계정을 옮기거나 시크릿이 초기화됐을 때만 한다.

값은 OneDrive `classcard-secrets/` 또는 비밀번호 관리자에서 꺼낸다.

```bash
cd worker
npx wrangler secret put ACCOUNT_KEY
npx wrangler secret put RUNNER_KEY
gh secret set RUNNER_KEY --repo pinde0000-art/classcard-free-runner
```

`ACCOUNT_KEY`는 반드시 **원래 값 그대로** 넣어야 한다. 새로 만들면 KV에 있는
기존 계정 자격증명을 하나도 복호화하지 못해, 모든 사용자가 계정을 다시
연결해야 한다.

### 4. 확인

```bash
curl -s "https://pinde0000-art.github.io/classcard-free-runner/?v=$RANDOM" | head -3
gh run list --workflow=pages-build-deployment --limit 1
```

## 웹앱 배포 방법

`docs/` 안의 파일을 고쳐서 `main`에 push하면 끝이다. `pages-build-deployment`
워크플로가 자동으로 돈다. 캐시가 강하니 확인할 때는 `?v=타임스탬프`를 붙인다.

Worker는 별개다. `worker/`를 고쳤으면 `npx wrangler deploy`를 직접 실행해야
한다.

## 포맷하기 전에 확인할 것

- [ ] `git status`가 깨끗하고 `git push`가 끝나 있다
- [ ] `git stash list`가 비어 있다 (stash는 GitHub에 올라가지 않는다)
- [ ] OneDrive `classcard-secrets/`의 값을 비밀번호 관리자에도 넣어뒀다
- [ ] OneDrive 동기화가 "최신 상태"로 끝나 있다
