import { test, expect } from '@playwright/test';

/**
 * 채팅 UI 스크롤 동작 테스트
 *
 * 테스트 시나리오:
 * 1. 새 질문을 입력하면 사용자 말풍선이 상단바 아래 적절한 위치에 표시
 * 2. Placeholder가 생성되어 AI 응답 공간 확보
 * 3. 사용자 말풍선이 화면 밖으로 넘어가지 않음
 */

test.describe('채팅 스크롤 동작', () => {
  test.beforeEach(async ({ page }) => {
    // 테스트용 스레드 페이지로 이동
    await page.goto('http://localhost:3000/chat/test-thread-id');

    // 페이지 로딩 대기
    await page.waitForLoadState('networkidle');
  });

  test('새 질문 입력 시 사용자 말풍선이 적절한 위치에 표시되어야 함', async ({ page }) => {
    // Textarea 찾기
    const textarea = page.locator('textarea[placeholder*="질문"]');
    await expect(textarea).toBeVisible();

    // 질문 입력
    const testQuestion = '이것은 테스트 질문입니다';
    await textarea.fill(testQuestion);
    await textarea.press('Enter');

    // 사용자 메시지가 표시될 때까지 대기
    const userMessage = page.locator('text=' + testQuestion).first();
    await expect(userMessage).toBeVisible({ timeout: 5000 });

    // 스크롤 애니메이션 완료 대기
    await page.waitForTimeout(200);

    // 사용자 말풍선이 뷰포트 내에 있는지 확인
    await expect(userMessage).toBeInViewport();

    // 사용자 말풍선의 위치가 적절한지 확인 (상단바 아래에 있어야 함)
    const messageBox = await userMessage.boundingBox();
    expect(messageBox).not.toBeNull();

    if (messageBox) {
      // Y 위치가 헤더 높이(64px) + 여유(24px) = 88px 근처에 있어야 함
      expect(messageBox.y).toBeGreaterThan(50);  // 최소값
      expect(messageBox.y).toBeLessThan(200);     // 최대값
    }
  });

  test('Placeholder가 생성되어야 함', async ({ page }) => {
    const textarea = page.locator('textarea[placeholder*="질문"]');

    // 질문 입력
    await textarea.fill('테스트 질문');
    await textarea.press('Enter');

    // 스크롤 컨테이너 찾기
    const scrollContainer = page.locator('.overflow-y-auto').first();

    // Placeholder가 생성되어 스크롤 높이가 증가했는지 확인
    await page.waitForTimeout(200);

    const scrollHeight = await scrollContainer.evaluate((el) => el.scrollHeight);
    const clientHeight = await scrollContainer.evaluate((el) => el.clientHeight);

    // Placeholder가 생성되면 스크롤 가능한 높이가 컨테이너 높이보다 커야 함
    expect(scrollHeight).toBeGreaterThan(clientHeight);
  });

  test('스크롤 컨테이너가 제대로 작동해야 함', async ({ page }) => {
    const textarea = page.locator('textarea[placeholder*="질문"]');
    const scrollContainer = page.locator('.overflow-y-auto').first();

    // 초기 스크롤 위치 확인
    const initialScrollTop = await scrollContainer.evaluate((el) => el.scrollTop);

    // 질문 입력
    await textarea.fill('스크롤 테스트 질문');
    await textarea.press('Enter');

    // 스크롤 애니메이션 완료 대기
    await page.waitForTimeout(300);

    // 스크롤이 변경되었는지 확인
    const finalScrollTop = await scrollContainer.evaluate((el) => el.scrollTop);

    // 새 메시지가 추가되면 스크롤 위치가 변경되어야 함
    expect(finalScrollTop).toBeGreaterThan(initialScrollTop);
  });

  test('여러 질문을 연속으로 입력해도 정상 작동해야 함', async ({ page }) => {
    const textarea = page.locator('textarea[placeholder*="질문"]');

    // 여러 질문 연속 입력
    for (let i = 1; i <= 3; i++) {
      const question = `테스트 질문 ${i}`;
      await textarea.fill(question);
      await textarea.press('Enter');

      // 메시지 표시 대기
      const userMessage = page.locator(`text=${question}`).first();
      await expect(userMessage).toBeVisible({ timeout: 5000 });

      // 다음 질문 전에 잠시 대기
      await page.waitForTimeout(500);
    }

    // 모든 메시지가 표시되는지 확인
    for (let i = 1; i <= 3; i++) {
      const message = page.locator(`text=테스트 질문 ${i}`).first();
      await expect(message).toBeVisible();
    }
  });

  test('입력창이 항상 하단에 고정되어 있어야 함', async ({ page }) => {
    const inputContainer = page.locator('.sticky.bottom-0').first();
    await expect(inputContainer).toBeVisible();

    // 스크롤해도 입력창이 보여야 함
    await page.evaluate(() => window.scrollTo(0, 500));
    await page.waitForTimeout(100);

    await expect(inputContainer).toBeInViewport();
  });
});

/**
 * 콘솔 로그 테스트
 * 스크롤 디버그 로그가 제대로 출력되는지 확인
 */
test.describe('스크롤 디버그 로그', () => {
  test('스크롤 디버그 정보가 콘솔에 출력되어야 함', async ({ page }) => {
    const consoleLogs: string[] = [];

    // 콘솔 로그 캡처
    page.on('console', (msg) => {
      if (msg.type() === 'log') {
        consoleLogs.push(msg.text());
      }
    });

    await page.goto('http://localhost:3000/chat/test-thread-id');
    await page.waitForLoadState('networkidle');

    const textarea = page.locator('textarea[placeholder*="질문"]');
    await textarea.fill('로그 테스트');
    await textarea.press('Enter');

    // 로그가 출력될 때까지 대기
    await page.waitForTimeout(500);

    // 콘솔 로그가 비어있지 않은지 확인
    expect(consoleLogs.length).toBeGreaterThan(0);
  });
});
