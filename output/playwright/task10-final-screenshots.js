async (page) => {
  await page.getByRole('button', { name: '提问', exact: true }).waitFor();
  if (await page.getByRole('button', { name: '提问', exact: true }).isDisabled()) throw new Error('Question still pending');
  const history = page.getByRole('region', { name: '代码问答记录' });
  await page.setViewportSize({ width: 1280, height: 1000 });
  await history.evaluate(element => { element.scrollTop = 0; });
  await page.screenshot({ path: 'output/playwright/task10-desktop.png', animations: 'disabled', scale: 'css' });
  await page.setViewportSize({ width: 390, height: 844 });
  await history.evaluate(element => { element.scrollTop = 0; });
  await page.screenshot({ path: 'output/playwright/task10-mobile.png', animations: 'disabled', scale: 'css' });
  await history.evaluate(element => { element.scrollTop = 270; });
  await page.screenshot({ path: 'output/playwright/task10-mobile-preview.png', animations: 'disabled', scale: 'css' });
  await page.setViewportSize({ width: 1280, height: 1000 });
  await history.evaluate(element => { element.scrollTop = 0; });
  return { kind: 'screenshots of existing real response; no new API request', files: 3 };
}
