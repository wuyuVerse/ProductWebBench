async function collectDomTransition(page, probe) {
  const observe = async text => {
    const handle = await page.waitForFunction(({selector, text}) => {
      const matches = document.querySelectorAll(selector);
      if(matches.length !== 1) return false;
      const element = matches[0], rect = element.getBoundingClientRect(), style = getComputedStyle(element);
      const visible = rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
      return visible && element.textContent.trim() === text
        ? {text:element.textContent, visible, observed_at_ms:Date.now()} : false;
    }, {selector:probe.selector, text}, {timeout:probe.timeout_ms});
    try { return await handle.jsonValue(); } finally { await handle.dispose(); }
  };
  const before = await observe(probe.before_text);
  await page.locator(probe.trigger_selector).click({timeout:probe.timeout_ms});
  const active = await observe(probe.active_text);
  const settled = await observe(probe.settled_text);
  return {before, active, settled, action:{type:'click',selector:probe.trigger_selector,status:'passed'}};
}
module.exports = {collectDomTransition};
