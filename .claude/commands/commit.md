# 커밋 절차

## Commit Convention

```
<type>(<scope>): <subject>
```

| Type | 용도 |
|------|------|
| `feat` | 새 기능 |
| `fix` | 버그 수정 |
| `docs` | 문서 변경 |
| `chore` | 빌드, 설정, 의존성 등 |

예시:
- `feat(content): 콘텐츠 상세 페이지 레이아웃 리디자인`
- `fix(chat): 스트리밍 메시지 스크롤 오류 수정`
- `docs(readme): 프로젝트 구조 설명 추가`
- `chore(deps): react-pdf 의존성 추가`

## 커밋 전 브랜치 확인 (필수)

**첫 커밋 전에 반드시 피처 브랜치를 생성한다.** (탐색·수정은 어디서든 가능하지만, 커밋은 피처 브랜치에서만)

```bash
# 1. 현재 브랜치 확인
git branch

# 2. develop이거나 main이면 피처 브랜치 생성
git checkout develop && git pull
git checkout -b feat/<scope>   # 예: feat/threads-header
```

- `develop` 브랜치에 **직접 커밋 금지** (핫픽스 등 예외 상황 제외)
- `main` 브랜치에 **직접 커밋 금지**
- 이미 피처 브랜치라면 그대로 커밋 진행

## 커밋 전 코드 검증 (필수)

커밋 직전에 `/simplify`를 실행하여 변경된 코드를 리뷰한다.

1. `/simplify` 실행 → 코드 재사용, 품질, 효율성 검토
2. `/simplify`가 코드를 수정한 경우:
   - 빌드 체크 필수 (`tsc --noEmit` 또는 `vite build`)
   - UI/기능 변경이 있었으면 Playwright MCP 재테스트
3. `/simplify` 결과 "코드 깨끗함"이면 바로 커밋 진행

> 단, 현재 대화에서 이미 `/simplify`를 실행했고 이후 코드 변경이 없었다면 스킵 가능.

## 커밋 실행

위 확인이 완료되면 staged 변경사항을 커밋한다:

```bash
git add <관련 파일들>
git commit -m "$(cat <<'EOF'
type(scope): subject
EOF
)"
```
