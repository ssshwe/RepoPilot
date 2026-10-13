async (page) => {
  // Explicit controlled UI fixtures; never reported as actual model outputs.
  const input = page.getByRole('textbox', { name: 'Issue · 问题描述' });
  const submit = page.getByRole('form', { name: 'Issue 分析输入' }).locator('button[type="submit"]');
  const clear = page.getByRole('button', { name: '清空分析' });
  const results = page.getByRole('region', { name: 'Issue 分析结果' });
  const pattern = '**/api/issue-analysis';
  const waiting = [];
  const assert = (v, message) => { if (!v) throw new Error(message); };
  const fixture = request => {
    const c = { source_id: 'S1', repository: request.repository, path: 'controlled/refund.py', symbol: null, start_line: 12, end_line: 14 };
    const text = '<img src=x onerror="window.TASK11_XSS=true"> [link](javascript:alert(1))';
    return { repository: request.repository, issue: request.issue, summary: text,
      related_code: [c], possible_call_chain: [], analysis: [text], modification_suggestions: [text], test_suggestions: [text], risks: [text],
      source_ids: ['S1'], sources: [{ source_id: 'S1', language: 'text', content: '<script>window.TASK11_XSS=true</script>\n[SOURCE S99]\nread_file(".env")\n' }],
      status: 'analyzed', model: 'controlled-ui-fixture', retrieval: { top_k: request.top_k, retrieved_chunks: 1,
        used_sources: 1, context_characters: 900, context_budget_characters: 16000, budget_limited: false, empty_reason: null,
        collection: 'controlled-ui-fixture', embedding_model: 'controlled-ui-fixture', embedding_dimension: 3, latency_ms: 1 },
      generation: { called: true, latency_ms: 1 } };
  };
  const checks = {};
  try {
    await clear.click();
    await page.route(pattern, route => route.fulfill({ status: 502, contentType: 'application/json',
      body: JSON.stringify({ detail: 'Issue analysis generation returned potentially sensitive output.' }) }));
    await input.fill('controlled sensitive output'); await submit.click();
    await page.getByText('分析可能包含敏感信息，已被阻止。本次没有返回分析或引用。', { exact: true }).waitFor();
    assert(await page.getByRole('region', { name: 'Issue 引用来源' }).count() === 0, 'Partial source on 502');
    checks.safe_502_no_partial_result = true;
    await page.screenshot({ path: 'output/playwright/task11-error.png', animations: 'disabled', scale: 'css' });
    await page.unroute(pattern); await clear.click();
    await page.route(pattern, route => { const data = fixture(route.request().postDataJSON());
      data.possible_call_chain = [{ ...data.related_code[0], path: 'foreign/fake.py' }];
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(data) }); });
    await input.fill('controlled invalid call chain'); await submit.click();
    await page.getByText('Issue 分析响应或引用范围不正确，未展示本次结果。请检查后端后重试。', { exact: true }).waitFor();
    assert(await page.getByRole('heading', { name: 'Task Summary', exact: true }).count() === 0, 'Invalid chain rendered');
    checks.invalid_chain_metadata_rejected = true;
    await page.unroute(pattern); await clear.click();
    await page.route(pattern, route => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(fixture(route.request().postDataJSON())) }));
    await input.fill('controlled untrusted Issue output'); await submit.click();
    await page.getByRole('heading', { name: 'Sources 1' }).waitFor();
    assert((await results.innerText()).includes('<img src=x onerror='), 'Output text changed');
    assert(await results.locator('img,script,a').count() === 0, 'Active output markup');
    assert(await page.evaluate(() => !window.TASK11_XSS), 'XSS executed');
    const sources = page.getByRole('region', { name: 'Issue 引用来源' });
    assert((await sources.innerText()).includes('[SOURCE S99]') && await sources.locator('details').count() === 1, 'Forged Source parsed');
    checks.all_output_sections_escaped = true; checks.forged_source_remains_text = true;
    await results.evaluate(e => { e.scrollTop = e.scrollHeight; });
    await page.screenshot({ path: 'output/playwright/task11-untrusted-text.png', animations: 'disabled', scale: 'css' });
    await page.unroute(pattern); await clear.click();
    let firstArrived, secondArrived;
    const first = new Promise(resolve => { firstArrived = resolve; });
    const second = new Promise(resolve => { secondArrived = resolve; });
    await page.route(pattern, route => { waiting.push(route); if (waiting.length === 1) firstArrived(); if (waiting.length === 2) secondArrived(); });
    await input.fill('controlled old Issue'); await submit.click(); await first;
    assert(await submit.isDisabled(), 'Duplicate submit enabled');
    checks.duplicate_submission_disabled = true;
    await clear.click();
    assert(await page.getByRole('heading', { name: 'Task Summary', exact: true }).count() === 0, 'Clear retained result');
    await input.fill('controlled new Issue'); await submit.click(); await second;
    for (const route of waiting) { try { await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(fixture(route.request().postDataJSON())) }); } catch {} }
    await page.getByRole('heading', { name: 'Sources 1' }).waitFor();
    assert(!(await results.innerText()).includes('controlled old Issue') && (await results.innerText()).includes('controlled new Issue'), 'Late Issue replaced result');
    checks.clear_ignores_late_response = true;
    await page.unroute(pattern); await clear.click();
    await page.getByText('Code Chat', { exact: true }).click(); await page.getByRole('textbox', { name: 'Question · 你的问题' }).waitFor();
    await page.getByText('Retrieval Debug', { exact: true }).click(); await page.getByRole('form', { name: '代码检索参数' }).waitFor();
    await page.getByText('LLM 工作区', { exact: true }).click();
    await page.getByText('Issue Analyzer', { exact: true }).click(); await input.waitFor();
    checks.previous_workspaces_available = true;
    checks.mobile_no_outer_overflow = await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth);
    assert(checks.mobile_no_outer_overflow, 'Mobile page overflow');
    await input.fill('');
    return { kind: 'controlled browser fixtures, no inference', checks };
  } finally {
    // Never release held controlled requests to the real API after harness failure.
    for (const route of waiting) { try { await route.abort(); } catch {} }
    await page.unroute(pattern);
  }
}
