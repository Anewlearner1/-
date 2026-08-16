/**
 * LitDesk v1.0 — 驗收測試（AC-1 … AC-5）
 *
 * 用 Playwright 開啟 litdesk.html，把模型呼叫層 (window.LitDeskLLM) 換成
 * 可控的 stub，逐條驗證 PRD §3 的驗收標準。不需要 API key、不打網路。
 *
 *   node test_litdesk.mjs
 */
import { chromium } from 'playwright';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import fs from 'node:fs/promises';
import os from 'node:os';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const APP_URL = 'file://' + path.join(HERE, 'litdesk.html');
const CHROME = '/opt/pw-browsers/chromium-1194/chrome-linux/chrome';

let passed = 0, failed = 0;
const log = (...a) => console.log(...a);
function check(name, cond, extra) {
  if (cond) { passed++; log(`  ✓ ${name}`); }
  else { failed++; log(`  ✗ ${name}${extra ? '  → ' + extra : ''}`); }
}
function section(t) { log(`\n${t}`); }

/* ---------- stub 資料 ---------- */

// 12 筆輸入，其中 4 筆以不同方式失敗
const BATCH_INPUT = [
  '10.1056/NEJMoa1000001', '10.1056/NEJMoa1000002', '10.1056/NEJMoa1000003',
  '10.1056/NEJMoa1000004', '10.1056/NEJMoa1000005', '10.1056/NEJMoa1000006',
  '10.1056/NEJMoa1000007', 'https://doi.org/10.1056/NEJMoa1000008',
  'NOT_FOUND_ITEM', 'PAYWALL_ITEM', 'EDITORIAL_ITEM', 'THROWS_ITEM'
].join('\n');

const TRIAGE_STUB = `
window.LitDeskLLM = {
  async triage(q) {
    if (q === 'THROWS_ITEM') throw new Error('network unreachable');
    if (q === 'NOT_FOUND_ITEM') return { fetch_status:'not_found', fetch_note:'搜尋不到這篇文獻' };
    if (q === 'PAYWALL_ITEM') return {
      title:'Effect of X on mortality', journal:'J Test', year:'2021',
      completeness:'僅標題', fetch_status:'paywalled', fetch_note:'摘要被付費牆擋住',
      results:[{outcome:'死亡率', effect:'HR 0.55', ci:'0.3-0.9', p:'0.02', source_quote:'捏造的句子'}]
    };
    if (q === 'EDITORIAL_ITEM') return { title:'A commentary', fetch_status:'not_research', fetch_note:'這是社論' };
    // 一筆刻意缺欄位 + 一條沒有 source_quote 的效果量（AC-2）
    if (q === '10.1056/NEJMoa1000002') return {
      doi:'10.1056/NEJMoa1000002', title:'Trial B', journal:'', year:'',
      design:'RCT', n:'', population:'', intervention:'藥物 B', comparator:'',
      completeness:'全文摘要', fetch_status:'ok', verdict:'略讀', level:'',
      results:[
        { outcome:'住院天數', effect:'MD -1.2 days', ci:'-2.0 to -0.4', p:'0.004' },
        { outcome:'再住院率', effect:'RR 0.80', ci:'0.65-0.98', p:'0.03',
          source_quote:'Readmission was lower in the intervention arm (RR 0.80, 95% CI 0.65-0.98).' }
      ]
    };
    const i = q.slice(-1);
    return {
      doi: 'https://doi.org/10.1056/NEJMoa100000' + i,
      title: 'Trial ' + i + ' of 維生素 D 補充', journal: 'NEJM', year: '202' + (i % 5),
      design: 'RCT', n: (100 * Number(i)) + '', population: '加護病房成人',
      intervention: '維生素 D 補充', comparator: '安慰劑',
      completeness: '全文摘要', fetch_status: 'ok', verdict: '必讀', level: 'Level 1b',
      source_url: 'https://example.org/' + i,
      results: [{ outcome:'ICU 住院天數', effect:'MD -0.' + i + ' days',
                  ci:'-1.1 to 0.2', p:'0.1' + i,
                  source_quote:'ICU length of stay differed by -0.' + i + ' days.' }]
    };
  },
  async contradict(rows) {
    return {
      axes: [{ axis:'ICU 住院天數', rows:[
        {ref:1,value:'MD -0.1 days',direction:'有利'},
        {ref:2,value:'MD -1.2 days',direction:'有利'},
        {ref:3,value:'MD +0.3 days',direction:'不利'}]}],
      conflicts: [
        { type:'方向相反', axis:'ICU 住院天數', refs:[1,3], detail:'甲篇縮短、丙篇延長。' },
        { type:'量級落差', axis:'ICU 住院天數', refs:[1,2], detail:'差距達 1.1 天。' },
        { type:'不可比', axis:'劑量', refs:['2'], detail:'劑量不同。' },
        { type:'方向相反', axis:'無出處', refs:[], detail:'這條沒有篇號，必須被丟棄。' },
        { type:'量級落差', axis:'越界', refs:[99], detail:'篇號越界，必須被丟棄。' }
      ],
      differences: [{ ref:1, items:['樣本：加護病房成人','追蹤：28 天'] }],
      silence: ['都沒有回答長期功能結果。']
    };
  }
};`;

/* ---------- 主流程 ---------- */

const browser = await chromium.launch({ executablePath: CHROME });
const downloadDir = await fs.mkdtemp(path.join(os.tmpdir(), 'litdesk-dl-'));
const ctx = await browser.newContext({ acceptDownloads: true });
const page = await ctx.newPage();
page.on('dialog', d => d.accept());
page.on('pageerror', e => { failed++; log('  ✗ 頁面 JS 錯誤: ' + e.message); });

await page.addInitScript(TRIAGE_STUB);
await page.goto(APP_URL);
await page.waitForFunction(() => window.LitDeskReady === true);

/* ---------- 純函式單元檢查 ---------- */
section('單元：容錯解析與正規化（PRD §8）');
{
  const r = await page.evaluate(() => {
    const L = window.LitDesk;
    return {
      fenced: L.extractJson('```json\n{"a":1}\n```'),
      preamble: L.extractJson('好的，以下是結果：\n{"a":2}\n希望有幫助'),
      broken: L.extractJson('completely not json'),
      doi1: L.normalizeDoi('https://doi.org/10.1056/NEJMoa2035389'),
      doi2: L.normalizeDoi('DOI: 10.1056/NEJMoa2035389.'),
      doi3: L.normalizeDoi('just a title'),
      lines: L.parseInputLines('a\n\n  b  \r\nc\n')
    };
  });
  check('去除程式碼圍欄後可解析', r.fenced && r.fenced.a === 1);
  check('擷取首個 { 到末個 }', r.preamble && r.preamble.a === 2);
  check('無法解析時回傳 null', r.broken === null);
  check('DOI 正規化（去 doi.org 前綴）', r.doi1 === '10.1056/nejmoa2035389', r.doi1);
  check('DOI 正規化（去 doi: 與尾點）', r.doi2 === '10.1056/nejmoa2035389', r.doi2);
  check('非 DOI 不誤判', r.doi3 === '');
  check('多行輸入切分並去空白', JSON.stringify(r.lines) === '["a","b","c"]', JSON.stringify(r.lines));
}

/* ---------- AC-1 批次 ---------- */
section('AC-1 批次：≥10 筆逐筆處理，單筆失敗不中斷整批，失敗需標明原因');
await page.fill('#inputBox', BATCH_INPUT);
await page.click('#runBtn');
await page.waitForFunction(() => /完成 \d+ 筆/.test(document.querySelector('#progress').textContent), null, { timeout: 30000 });
{
  const cards = await page.$$('#triageResults .paper');
  check('12 筆全部產出卡片（未因失敗中斷）', cards.length === 12, '實際 ' + cards.length);
  const failedTexts = await page.$$eval('#triageResults .paper.failed .fail-msg', ns => ns.map(n => n.textContent));
  check('4 筆標記為失敗', failedTexts.length === 4, '實際 ' + failedTexts.length);
  const joined = failedTexts.join('\n');
  check('失敗原因含「找不到」', joined.includes('找不到'));
  check('失敗原因含「付費牆」', joined.includes('付費牆'));
  check('失敗原因含「非研究論文」', joined.includes('非研究論文'));
  check('例外（network unreachable）也被歸類且保留原因', joined.includes('network unreachable'));
  check('付費牆項目提示下一步（改貼摘要全文）', joined.includes('改貼摘要全文'));
  const prog = await page.textContent('#progress');
  check('進度顯示完成筆數', /完成 12 筆/.test(prog), prog);
}

/* ---------- AC-2 誠實 ---------- */
section('AC-2 誠實：抓不到的欄位填「未取得」，無來源句的數字不予顯示');
{
  const cardB = await page.evaluate(() => {
    const c = window.LitDesk.state.cards.find(x => x.title === 'Trial B');
    return c && { journal:c.journal, year:c.year, n:c.n, population:c.population,
                  comparator:c.comparator, level:c.level,
                  outcomes:c.results.map(r => r.outcome) };
  });
  check('缺 journal → 未取得', cardB.journal === '未取得', cardB.journal);
  check('缺 year → 未取得', cardB.year === '未取得', cardB.year);
  check('缺 n → 未取得', cardB.n === '未取得', cardB.n);
  check('缺 population → 未取得', cardB.population === '未取得', cardB.population);
  check('缺 comparator → 未取得', cardB.comparator === '未取得', cardB.comparator);
  check('缺 level → 未取得', cardB.level === '未取得', cardB.level);
  check('無 source_quote 的「住院天數」被丟棄',
        !cardB.outcomes.includes('住院天數'), JSON.stringify(cardB.outcomes));
  check('有 source_quote 的「再住院率」保留', cardB.outcomes.includes('再住院率'));

  const paywall = await page.evaluate(() =>
    window.LitDesk.state.cards.find(x => x.query === 'PAYWALL_ITEM'));
  check('僅標題的紀錄不給效果量（風險對策）', paywall.results.length === 0);
  check('僅標題的紀錄 completeness = 僅標題', paywall.completeness === '僅標題');

  const quoteCount = await page.$$eval('#triageResults details.quote blockquote', ns => ns.length);
  check('保留的效果量都可展開來源句', quoteCount >= 8, '實際 ' + quoteCount);

  // 卡片上不得出現任何未經來源句支撐的數字
  const html = await page.innerHTML('#triageResults');
  check('杜撰的 HR 0.55 未出現在畫面上', !html.includes('HR 0.55'));
  check('無來源句的 MD -1.2 days 未出現在畫面上', !html.includes('MD -1.2 days'));
}

/* ---------- F2 入庫（含手動覆寫） ---------- */
section('F2 入庫：可手動覆寫分診結果、加備註與標籤');
{
  // 給第一張卡片加備註與標籤，並覆寫分診
  await page.evaluate(() => {
    const c = window.LitDesk.state.cards.find(x => x.title && x.title.includes('Trial 1'));
    c.note = '這篇的族群跟我的病人最接近';
    c.tags = ['維生素D', 'ICU'];
    c.verdict = '略讀';
    c.verdict_overridden = true;
  });
  await page.click('#selectAllBtn');
  await page.click('#ingestBtn');
  await page.waitForFunction(() => window.LitDesk.state.library.length > 0);
  const lib = await page.evaluate(() => window.LitDesk.state.library.map(r => ({
    t: r.title, v: r.verdict, o: r.verdict_overridden, tags: r.tags, note: r.note, at: r.added_at
  })));
  check('跳過／失敗項目預設不入庫', lib.length === 8, '實際入庫 ' + lib.length);
  const one = lib.find(x => x.t.includes('Trial 1'));
  check('手動覆寫的分診被保存', one.v === '略讀' && one.o === true);
  check('備註被保存', one.note.includes('最接近'));
  check('標籤被保存', one.tags.join(',') === '維生素D,ICU');
  check('added_at 有寫入', /^\d{4}-\d{2}-\d{2} /.test(one.at), one.at);
}

section('F2 重複 DOI：提示已存在並提供更新／略過');
{
  await page.fill('#inputBox', '10.1056/NEJMoa1000003');
  await page.click('#runBtn');
  await page.waitForFunction(() => /完成 1 筆/.test(document.querySelector('#progress').textContent));
  await page.click('#selectAllBtn');
  await page.click('#ingestBtn');   // dialog handler 一律 accept → 走「更新」
  await page.waitForTimeout(300);
  const n = await page.evaluate(() => window.LitDesk.state.library.length);
  check('重複 DOI 走更新而非新增（總數不變）', n === 8, '實際 ' + n);
}

/* ---------- AC-3 留存 ---------- */
section('AC-3 留存：關閉重開後證據庫仍在，且能以關鍵字搜尋');
{
  const page2 = await ctx.newPage();
  page2.on('dialog', d => d.accept());
  await page2.goto(APP_URL);
  await page2.waitForFunction(() => window.LitDeskReady === true);
  const n = await page2.evaluate(() => window.LitDesk.state.library.length);
  check('重開後證據庫仍有 8 筆', n === 8, '實際 ' + n);
  await page2.click('nav button[data-tab="library"]');
  const rows = await page2.$$eval('#libTable tbody tr', r => r.length);
  check('證據庫表格渲染 8 列', rows === 8, '實際 ' + rows);

  await page2.fill('#searchBox', '最接近');           // 命中備註
  await page2.waitForTimeout(150);
  check('關鍵字搜尋涵蓋備註',
        (await page2.$$eval('#libTable tbody tr', r => r.length)) === 1);

  await page2.fill('#searchBox', 'ICU');              // 命中標籤
  await page2.waitForTimeout(150);
  const tagRows = await page2.$$eval('#libTable tbody tr', r => r.length);
  check('關鍵字搜尋涵蓋標籤', tagRows === 1, '實際 ' + tagRows);

  await page2.fill('#searchBox', '加護病房');          // 命中族群
  await page2.waitForTimeout(150);
  const popRows = await page2.$$eval('#libTable tbody tr', r => r.length);
  // Trial B 的族群是「未取得」，故命中 7 筆而非 8
  check('關鍵字搜尋涵蓋族群', popRows === 7, '實際 ' + popRows);

  await page2.fill('#searchBox', '維生素 D 補充');     // 命中介入
  await page2.waitForTimeout(150);
  check('關鍵字搜尋涵蓋介入',
        (await page2.$$eval('#libTable tbody tr', r => r.length)) === 7);

  await page2.fill('#searchBox', '');
  await page2.selectOption('#filterVerdict', '略讀');
  await page2.waitForTimeout(200);
  const vRows = await page2.$$eval('#libTable tbody tr', r => r.length);
  // 略讀 = Trial 1（手動覆寫）+ Trial B（模型判為略讀）
  check('依分診結果篩選', vRows === 2, '實際 ' + vRows);
  await page2.selectOption('#filterVerdict', '');
  await page2.close();
}

/* ---------- AC-4 對質 ---------- */
section('AC-4 對質：勾選 ≥3 篇產出矛盾點清單，每點必須標明來自哪幾篇');
{
  await page.reload();
  await page.waitForFunction(() => window.LitDeskReady === true);
  await page.click('nav button[data-tab="library"]');

  // 少於 3 篇時按鈕停用
  await page.click('#libTable tbody tr:nth-child(1) input[type=checkbox]');
  await page.click('#libTable tbody tr:nth-child(2) input[type=checkbox]');
  await page.click('nav button[data-tab="conflict"]');
  check('勾選 2 篇時對質按鈕停用', await page.isDisabled('#contradictBtn'));

  await page.click('nav button[data-tab="library"]');
  await page.click('#libTable tbody tr:nth-child(3) input[type=checkbox]');
  await page.click('nav button[data-tab="conflict"]');
  check('勾選 3 篇時對質按鈕啟用', !(await page.isDisabled('#contradictBtn')));

  await page.click('#contradictBtn');
  await page.waitForSelector('#conflictOut .conflict', { timeout: 15000 });

  const conflicts = await page.$$eval('#conflictOut .conflict', ns => ns.map(n => ({
    type: n.querySelector('.ctype').textContent,
    refs: n.querySelector('.refs') ? n.querySelector('.refs').textContent : ''
  })));
  check('無篇號的矛盾點被丟棄，越界篇號也被丟棄',
        conflicts.length === 3, '實際 ' + conflicts.length + ' 條');
  check('每個矛盾點都標明出處篇號',
        conflicts.every(c => /出處：\[\d+\]/.test(c.refs)), JSON.stringify(conflicts));
  const types = conflicts.map(c => c.type);
  check('分歧分類使用規定的四種類型',
        types.every(t => ['方向相反','量級落差','不可比','單點證據'].includes(t)), types.join(','));
  check('字串型 refs ("2") 也能正確解析',
        conflicts.some(c => c.type === '不可比' && c.refs.includes('[2]')));

  const out = await page.innerText('#conflictOut');
  check('輸出含比較軸', out.includes('比較軸') && out.includes('ICU 住院天數'));
  check('輸出含可觀察差異', out.includes('可觀察差異') && out.includes('追蹤：28 天'));
  check('輸出含沉默區', out.includes('沉默區') && out.includes('長期功能結果'));
  check('可觀察差異註明「只陳述差異」', out.includes('不宣稱哪個差異造成了矛盾'));
  check('篇號對照表存在', out.includes('篇號對照'));
}

/* ---------- AC-5 匯出 ---------- */
section('AC-5 匯出：CSV 以 UTF-8 BOM 輸出');
{
  await page.click('nav button[data-tab="library"]');
  const [dl] = await Promise.all([
    page.waitForEvent('download'),
    page.click('#exportCsvBtn')
  ]);
  const csvPath = path.join(downloadDir, 'litdesk.csv');
  await dl.saveAs(csvPath);
  const buf = await fs.readFile(csvPath);
  check('檔名為 litdesk.csv', dl.suggestedFilename() === 'litdesk.csv', dl.suggestedFilename());
  check('前三個位元組是 UTF-8 BOM (EF BB BF)',
        buf[0] === 0xEF && buf[1] === 0xBB && buf[2] === 0xBF,
        [buf[0], buf[1], buf[2]].map(b => b.toString(16)).join(' '));
  const text = buf.toString('utf8');
  check('使用 CRLF 換行（Windows Excel 友善）', text.includes('\r\n'));
  check('中文表頭正確', text.includes('標題') && text.includes('資料完整度'));
  check('中文內容正確', text.includes('維生素 D 補充') && text.includes('加護病房成人'));
  check('備註與標籤有匯出', text.includes('最接近') && text.includes('維生素D; ICU'));
  const dataLines = text.replace(/^﻿/, '').trim().split('\r\n');
  check('資料列數 = 表頭 1 + 8 筆', dataLines.length === 9, '實際 ' + dataLines.length);
  check('未取得欄位以「未取得」輸出而非空白或杜撰值', text.includes('未取得'));

  const [dl2] = await Promise.all([
    page.waitForEvent('download'),
    page.click('#exportMdBtn')
  ]);
  const mdPath = path.join(downloadDir, 'litdesk.md');
  await dl2.saveAs(mdPath);
  const md = await fs.readFile(mdPath, 'utf8');
  check('Markdown 表格格式正確', md.startsWith('| 標題 |') && md.includes('| --- |'));
  check('Markdown 內容含中文', md.includes('維生素 D 補充'));
}

/* ---------- 儲存失敗處理 ---------- */
section('非功能：儲存失敗需明確告知本次未存檔（PRD §6）');
{
  const msg = await page.evaluate(async () => {
    const orig = window.localStorage.setItem;
    window.localStorage.setItem = () => { throw new Error('QuotaExceededError'); };
    await window.LitDesk.saveLibrary();
    window.localStorage.setItem = orig;
    const t = document.querySelector('.toast');
    return t ? t.textContent : '';
  });
  check('存檔失敗時跳出明確警告', msg.includes('未寫入儲存空間'), msg);
}

/* ---------- 收尾 ---------- */
await ctx.close();
await browser.close();
await fs.rm(downloadDir, { recursive: true, force: true });

log(`\n${'='.repeat(52)}`);
log(`通過 ${passed}　失敗 ${failed}`);
log('='.repeat(52));
process.exit(failed === 0 ? 0 : 1);
