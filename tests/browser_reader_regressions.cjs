/* Actual UI regression checks. Requires run_browser_reader.py's isolated server. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const settings = JSON.parse(process.env.HANDOFFDESK_READER_TEST);
const { chromium } = require(settings.playwright);

(async () => {
  const browser = await chromium.launch({ executablePath: settings.browser, headless: true });
  const context = await browser.newContext({ viewport: { width: 1280, height: 860 }, acceptDownloads: true });
  const page = await context.newPage();
  page.setDefaultTimeout(7000);
  const checks = [], errors = [], requests = [];
  const original = await fs.readFile(settings.attachment);
  page.on('pageerror', error => errors.push(String(error)));
  page.on('request', request => {
    if (request.url().endsWith('/api')) requests.push(request.postDataJSON());
  });
  // Poll through the DevTools evaluation API: older Playwright waitForFunction
  // compiles predicates with eval, which conflicts with the reader's strict CSP.
  const waitForFunction = async (predicate, argument, options = {}) => {
    const deadline = Date.now() + (options.timeout ?? 7000);
    do {
      if (await page.evaluate(predicate, argument)) return;
      await page.waitForTimeout(50);
    } while (Date.now() < deadline);
    throw new Error('Timed out waiting for browser state');
  };
  const select = async id => {
    await page.locator(`.record-card[data-record-id="${id}"]`).click();
    await waitForFunction(record => document.querySelector('.record-identity')?.textContent === '记录 ' + record, id);
    await page.locator('.detail-title').waitFor({ state: 'visible' });
    await waitForFunction(record => {
      const item = Array.from(document.querySelectorAll('.record-card')).find(element => element.dataset.recordId === record);
      return item && !item.querySelector('.unread-dot') && document.querySelector('.read-badge')?.textContent === '✓ 已读';
    }, id);
  };
  async function check(name, fn) {
    const start = Date.now();
    try { await fn(); checks.push({ name, passed: true, ms: Date.now() - start }); }
    catch (error) { checks.push({ name, passed: false, error: String(error), ms: Date.now() - start }); }
  }
  try {
    await page.goto(settings.url);
    await waitForFunction(() => document.querySelectorAll('.record-card').length === 2);
    await page.waitForTimeout(300);
    await check('automatic detail does not acknowledge reading', async () => {
      assert.equal(requests.filter(request => request.method === 'ack').length, 0);
      assert.equal(await page.locator('.unread-dot').count(), 2);
      assert.match(await page.locator('.read-badge').innerText(), /未读/);
    });
    if (settings.focus === 'automatic') return;
    await select(settings.record);
    await page.waitForTimeout(100);
    if (settings.focus === 'all') {
      await check('explicit reading and mark unread survive polling and refresh', async () => {
        assert.equal(requests.filter(request => request.method === 'ack').length, 1, 'One explicit selection acknowledges only once');
        assert.equal(await page.locator('.unread-dot').count(), 1, 'The other record stays unread');
        await page.getByRole('button', { name: '标为未读', exact: true }).click();
        await page.waitForTimeout(1800);
        await page.locator('#refresh').click();
        await page.waitForTimeout(150);
        assert.equal(await page.locator('.unread-dot').count(), 2);
        assert.match(await page.locator('.read-badge').innerText(), /未读/);
        assert.equal(requests.filter(request => request.method === 'ack').length, 1);
      });
    }
    await check('missing attachment is a persistent file error, not lost connection', async () => {
      await fs.rename(settings.attachment, settings.attachment + '.moved');
      await page.locator('.file-card').getByRole('button', { name: '预览', exact: true }).click();
      await page.waitForTimeout(1800);
      assert.equal(await page.locator('#connection-error').isVisible(), false);
      assert.match(await page.locator('#live-state').innerText(), /自动更新/);
      const error = page.locator('.file-card .operation-error');
      assert.equal(await error.isVisible(), true);
      assert.match(await error.innerText(), /缺失|不存在/);
      assert.match(await error.innerText(), /验证报告 中文.txt/);
    });
    await check('manual refresh updates current file state in both directions', async () => {
      const before = await page.locator('.record-identity').innerText();
      await page.locator('#refresh').click();
      await waitForFunction(() => document.querySelector('.file-status')?.textContent.includes('缺失'), null, { timeout: 2500 });
      assert.equal(await page.locator('.record-identity').innerText(), before);
      await fs.rename(settings.attachment + '.moved', settings.attachment);
      await page.locator('#refresh').click();
      await waitForFunction(() => !document.querySelector('.file-status'), null, { timeout: 2500 });
      assert.equal(await page.locator('.record-identity').innerText(), before);
      // Refreshing status must not erase the unresolved action error.
      assert.equal(await page.locator('.file-card .operation-error').isVisible(), true);
      await page.locator('.file-card .operation-error').getByRole('button', { name: '重试', exact: true }).click();
      await page.locator('#preview-modal').waitFor({ state: 'visible' });
      assert.match(await page.locator('#preview-content').innerText(), /中文验收正文/);
      await page.locator('#preview-close').click();
      assert.equal(await page.locator('.file-card .operation-error').count(), 0);
    });
    if (settings.focus === 'all') {
      await check('download uses a readable safe filename', async () => {
        const pending = page.waitForEvent('download');
        await page.getByRole('button', { name: '导出 ↗', exact: true }).click();
        const download = await pending;
        assert.match(download.suggestedFilename(), /中文项目.*中文导出/);
        assert.doesNotMatch(download.suggestedFilename(), /[<>:"/\\|?*]/);
        assert.match(await fs.readFile(await download.path(), 'utf8'), /记录者报告|记录者提供/);
      });
      await check('same-name tasks are distinguishable and verification is attributed', async () => {
        await page.locator('#project-list button').click();
        const names = await page.locator('#workline-select option').allTextContents();
        const same = names.filter(name => name.includes('Windows 修复交接'));
        assert.equal(same.length, 2);
        assert.equal(new Set(same).size, 2);
        await select(settings.record);
        assert.match(await page.locator('.verification').innerText(), /记录者报告/);
      });
      await check('file versions and explicit evidence copy describe current contents', async () => {
        await fs.appendFile(settings.attachment, '证据保存时的新增内容。\n');
        await page.locator('#refresh').click();
        await waitForFunction(() => document.querySelector('.file-version.changed')?.textContent.includes('已变化'), null, { timeout: 2500 });
        assert.equal(await page.locator('.file-card').count(), 1);
        await page.getByRole('button', { name: '保存证据副本', exact: true }).click();
        await waitForFunction(() => document.querySelectorAll('.file-card').length === 2, null, { timeout: 4000 });
        const evidence = page.locator('.file-card').filter({ has: page.locator('.file-version', { hasText: '证据副本 · 保存于' }) });
        assert.equal(await evidence.count(), 1);
        assert.equal(await evidence.locator('.file-name').innerText(), '验证报告 中文.txt（证据副本）');
        assert.match(await evidence.innerText(), /不是原记录时的快照/);
        await fs.appendFile(settings.attachment, '证据保存后的再次变化。\n');
        await evidence.getByRole('button', { name: '预览', exact: true }).click();
        await page.locator('#preview-modal').waitFor({ state: 'visible' });
        const content = await page.locator('#preview-content').innerText();
        assert.match(content, /证据保存时的新增内容/);
        assert.doesNotMatch(content, /证据保存后的再次变化/);
        await page.locator('#preview-close').click();
        const evidencePath = await evidence.locator('.file-path').innerText();
        const fixtureBase = path.dirname(path.dirname(settings.attachment));
        assert(path.resolve(evidencePath).startsWith(path.resolve(fixtureBase) + path.sep));
        await fs.appendFile(evidencePath, '受控修改证据副本自身。\n');
        await page.locator('#refresh').dispatchEvent('click');
        await waitForFunction(() => Array.from(document.querySelectorAll('.file-version.changed')).some(element => element.textContent.includes('证据副本与保存时相比已变化')), null, { timeout: 2500 });
        assert.match(await evidence.innerText(), /证据副本 · 保存于/);
      });
      await check('manual refresh preserves reading position and focused action', async () => {
        await page.locator('.file-card').first().getByRole('button', { name: '预览', exact: true }).focus();
        await page.evaluate(() => window.scrollTo(0, 500));
        const before = await page.evaluate(() => ({ y: window.scrollY, focus: document.activeElement.dataset.focusKey }));
        await page.locator('#refresh').dispatchEvent('click');
        await page.waitForTimeout(250);
        const after = await page.evaluate(() => ({ y: window.scrollY, focus: document.activeElement.dataset.focusKey }));
        assert.equal(after.y, before.y);
        assert.equal(after.focus, before.focus);
      });
      await check('slow detail refresh does not replace a newly selected record', async () => {
        let release, requested;
        const ready = new Promise(resolve => { requested = resolve; });
        const gate = new Promise(resolve => { release = resolve; });
        const delay = async route => {
          const payload = route.request().postDataJSON();
          if (payload.method !== 'detail' || payload.args[0] !== settings.record) return route.continue();
          const response = await route.fetch();
          requested(); await gate;
          await route.fulfill({ response });
        };
        await page.route('**/api', delay);
        try {
          await page.locator('#refresh').dispatchEvent('click');
          await Promise.race([ready, new Promise((_, reject) => setTimeout(() => reject(new Error('Refresh did not request detail')), 3000))]);
          await select(settings.older);
          release();
          await page.waitForTimeout(200);
          assert.equal(await page.locator('.record-identity').innerText(), '记录 ' + settings.older);
        } finally { release(); await page.unroute('**/api', delay); }
        await select(settings.record);
      });
      await check('actual communication failures and recovery use connection state', async () => {
        const disconnect = route => route.request().postDataJSON().method === 'changes' ? route.abort('connectionrefused') : route.continue();
        await page.route('**/api', disconnect);
        try {
          await waitForFunction(() => document.getElementById('live-state').textContent.includes('等待重新连接'), null, { timeout: 4000 });
          assert.equal(await page.locator('#connection-error').isVisible(), true);
        } finally { await page.unroute('**/api', disconnect); }
        await waitForFunction(() => document.getElementById('live-state').textContent.includes('自动更新'), null, { timeout: 4000 });
        assert.equal(await page.locator('#connection-error').isVisible(), false);
      });
      await check('HTTP session and service failures differ from validation errors', async () => {
        for (const status of [403, 503, 400]) {
          const reject = route => route.request().postDataJSON().method === 'changes'
            ? route.fulfill({ status, contentType: 'application/json', body: JSON.stringify({ ok: false, error: '受控 HTTP 错误 ' + status }) })
            : route.continue();
          await page.route('**/api', reject);
          try {
            await page.locator('#refresh').dispatchEvent('click');
            const target = status === 400 ? '#operation-error' : '#connection-error';
            await page.locator(target).waitFor({ state: 'visible' });
            assert.match(await page.locator(target).innerText(), new RegExp(String(status)));
            assert.equal(await page.locator('#connection-error').isVisible(), status !== 400);
          } finally { await page.unroute('**/api', reject); }
          await waitForFunction(() => document.getElementById('live-state').textContent.includes('自动更新'), null, { timeout: 4000 });
          if (status === 400) await page.locator('#operation-error').getByRole('button', { name: '关闭提示', exact: true }).click();
        }
      });
      await check('responsive detail, read controls and Chinese filenames fit the page', async () => {
        for (const width of [1280, 800, 390]) {
          await page.setViewportSize({ width, height: 860 });
          const back = page.getByRole('button', { name: '← 返回进展列表', exact: true });
          if (await back.isVisible()) await back.click();
          await select(settings.record);
          const documentWidth = await page.evaluate(() => document.documentElement.scrollWidth);
          assert.equal(documentWidth, width);
          assert.equal(await page.locator('.read-toggle').isVisible(), true);
          await page.evaluate(() => window.scrollTo(0, 0));
          await page.screenshot({ path: settings.output.replace(/\.json$/, '-' + width + '.png'), fullPage: true });
          if (width <= 930) await page.getByRole('button', { name: '← 返回进展列表', exact: true }).click();
        }
      });
    }
  } finally {
    if (await fs.stat(settings.attachment + '.moved').catch(() => null)) {
      await fs.rename(settings.attachment + '.moved', settings.attachment);
    }
    await fs.writeFile(settings.attachment, original);
    const result = { checks, frontend_errors: errors, passed: checks.every(check => check.passed) && !errors.length };
    await fs.writeFile(settings.output, JSON.stringify(result, null, 2));
    console.log(JSON.stringify(result));
    process.exitCode = result.passed ? 0 : 1;
    await context.close(); await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
