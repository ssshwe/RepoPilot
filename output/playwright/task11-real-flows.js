async (page) => {
  // Real requests to the existing configured Provider; no route mocks.
  const input = page.getByRole('textbox', { name: 'Issue · 问题描述' });
  const repository = page.getByRole('combobox', { name: 'Repository', exact: true });
  const submit = page.getByRole('form', { name: 'Issue 分析输入' }).locator('button[type="submit"]');
  const checks = {};
  const assert = (value, message) => { if (!value) throw new Error(message); };
  let calls = 0;
  const observe = request => { if (request.url().endsWith('/api/issue-analysis') && request.method() === 'POST') calls++; };
  page.on('request', observe);
  try {
    await input.fill(''); await submit.click();
    await page.getByRole('alert').waitFor();
    assert(calls === 0, 'Blank Issue reached API');
    checks.blank_issue_no_request = true;
    await page.getByRole('button', { name: '清空分析' }).click();
    await repository.fill('stage3-task11-not-indexed'); await repository.press('Enter');
    await input.fill('退款接口可能重复入账，请分析需要检查的代码。');
    const emptyPromise = page.waitForResponse(r => r.url().endsWith('/api/issue-analysis') && r.request().method() === 'POST');
    await submit.click(); const empty = await (await emptyPromise).json();
    await page.getByRole('heading', { name: 'Sources 0' }).waitFor();
    assert(!empty.generation.called && empty.model === null && empty.status === 'insufficient_evidence', 'Empty Issue called generation');
    checks.real_empty_evidence_skips_generation = true;
    await page.screenshot({ path: 'output/playwright/task11-empty.png', animations: 'disabled', scale: 'css' });
    await page.getByRole('button', { name: '清空分析' }).click();
    await repository.fill('demo-repository'); await repository.press('Enter');
    await page.getByRole('button', { name: '使用示例' }).click();
    const realPromise = page.waitForResponse(r => r.url().endsWith('/api/issue-analysis') && r.request().method() === 'POST');
    await submit.click(); const real = await (await realPromise).json();
    await page.getByRole('heading', { name: 'Task Summary', exact: true }).waitFor();
    assert(real.status === 'analyzed' && real.sources.length > 0, 'Expected mapped real response');
    for (const name of ['Task Summary', 'Related Code', 'Possible Call Chain', 'Analysis', 'Modification Suggestions', 'Test Suggestions', 'Risks']) {
      await page.getByRole('heading', { name, exact: true }).waitFor();
    }
    checks.real_structured_sections_and_sources = true;
    const results = page.getByRole('region', { name: 'Issue 分析结果' });
    await page.setViewportSize({ width: 1280, height: 1000 });
    await results.evaluate(e => { e.scrollTop = 0; });
    await page.screenshot({ path: 'output/playwright/task11-desktop.png', animations: 'disabled', scale: 'css' });
    await results.evaluate(e => { e.scrollTop = e.scrollHeight; });
    await page.screenshot({ path: 'output/playwright/task11-desktop-sources.png', animations: 'disabled', scale: 'css' });
    checks.desktop_no_outer_overflow = await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth);
    await page.setViewportSize({ width: 390, height: 844 });
    await results.evaluate(e => { e.scrollTop = 0; });
    await page.screenshot({ path: 'output/playwright/task11-mobile.png', animations: 'disabled', scale: 'css' });
    checks.mobile_no_outer_overflow = await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth);
    assert(checks.mobile_no_outer_overflow && checks.desktop_no_outer_overflow, 'Page overflow');
    return { kind: 'real browser requests, no mocked inference', checks, api_calls: calls,
      summary: real.summary, analysis: real.analysis, source_ids: real.source_ids,
      response_is_not_proof_of_semantic_quality: true };
  } finally { page.off('request', observe); }
}
