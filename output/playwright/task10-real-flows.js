async (page) => {
  // No route mocks. These are actual configured API / Qdrant / Ollama requests.
  const question = page.getByRole('textbox', { name: 'Question · 你的问题' });
  const repository = page.getByRole('combobox', { name: 'Repository', exact: true });
  const ask = page.getByRole('form', { name: '代码问答输入' }).locator('button[type="submit"]');
  const checks = {};
  const assert = (value, message) => { if (!value) throw new Error(message); };
  let calls = 0;
  const observe = request => { if (request.url().endsWith('/api/code-chat') && request.method() === 'POST') calls++; };
  page.on('request', observe);
  try {
    await question.fill(''); await ask.click();
    await page.getByRole('alert').waitFor();
    assert(calls === 0, 'Blank input reached API');
    checks.blank_question_does_not_call_api = true;
    await page.getByRole('button', { name: '清空问答' }).click();
    await repository.fill('stage3-task10-not-indexed'); await repository.press('Enter');
    await question.fill('这个仓库的退款函数在哪里？');
    const emptyResponse = page.waitForResponse(response => response.url().endsWith('/api/code-chat') && response.request().method() === 'POST');
    await ask.click();
    const empty = await (await emptyResponse).json();
    await page.getByRole('heading', { name: 'Sources 0' }).waitFor();
    assert(empty.status === 'insufficient_evidence' && !empty.generation.called && empty.model === null
      && empty.sources.length === 0 && empty.citations.length === 0, 'Empty evidence generated an answer');
    checks.real_empty_evidence_skips_generation = true;
    await page.screenshot({ path: 'output/playwright/task10-empty.png', scale: 'css' });
    await page.getByRole('button', { name: '清空问答' }).click();
    await repository.fill('demo-repository'); await repository.press('Enter');
    await page.getByRole('button', { name: /^试试：/ }).click();
    const answerResponse = page.waitForResponse(response => response.url().endsWith('/api/code-chat') && response.request().method() === 'POST');
    await ask.click();
    const answer = await (await answerResponse).json();
    await page.getByRole('heading', { name: 'Sources 1' }).waitFor();
    const source = page.getByRole('region', { name: '引用来源' });
    assert(answer.generation.called && answer.citations[0].symbol === 'RefundService.refund'
      && (await source.innerText()).includes('wallets.credit(userId, amount);'), 'Real preview missing');
    checks.real_refund_answer_and_preview = true;
    await page.setViewportSize({ width: 1280, height: 1000 });
    await page.getByRole('region', { name: '代码问答记录' }).evaluate(element => { element.scrollTop = 0; });
    await page.screenshot({ path: 'output/playwright/task10-desktop.png', scale: 'css' });
    checks.desktop_no_page_overflow = await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByRole('region', { name: '代码问答记录' }).evaluate(element => { element.scrollTop = 0; });
    await page.screenshot({ path: 'output/playwright/task10-mobile.png', scale: 'css' });
    checks.mobile_no_page_overflow = await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth);
    assert(checks.desktop_no_page_overflow && checks.mobile_no_page_overflow, 'Page overflow');
    await page.setViewportSize({ width: 1280, height: 1000 });
    return { kind: 'real browser requests, no mocked inference', checks, api_calls: calls,
      answer: answer.answer, citation: answer.citations[0], empty_reason: empty.retrieval.empty_reason };
  } finally { page.off('request', observe); }
}
