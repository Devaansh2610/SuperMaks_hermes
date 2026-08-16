const { chromium } = require('playwright');
(async () => {
  const b = await chromium.launch({ args:['--no-sandbox','--use-fake-ui-for-media-stream','--use-fake-device-for-media-stream'] });
  const page = await b.newPage({ viewport:{width:1440,height:900} });
  const errors = [];
  page.on('pageerror', e => errors.push('PAGEERROR: '+e.message));
  page.on('console', m => { if (m.type()==='error') errors.push('CONSOLE: '+m.text()); });
  await page.goto('http://localhost:8731/index.html',{waitUntil:'domcontentloaded'});
  await page.waitForTimeout(2200);
  await page.click('#dormant');
  await page.waitForTimeout(2500);
  await page.keyboard.press('2');            // open telemetry drawer
  await page.waitForTimeout(600);
  const info = await page.evaluate(() => ({
    count: document.getElementById('agentCount').textContent,
    rows: [...document.querySelectorAll('#agentList .agent')].map(e => e.className + ' → ' + e.querySelector('b').textContent),
  }));
  console.log(JSON.stringify(info, null, 1));
  console.log('errors:', errors.length ? errors.join(' || ') : '(none)');
  await page.screenshot({ path:'agents.png' });
  await b.close();
})();
