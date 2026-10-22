/* On-demand A-share code / Chinese-name suggestions.
   Server owns the real listed-symbol catalog. No LLM, no remote search CDN. */
(function () {
  'use strict';
  const input = document.getElementById('stockCode');
  const panel = document.getElementById('stockSuggestions');
  if (!input || !panel) return;

  let timer = null;
  let pending = null;
  let serial = 0;
  let results = [];
  let activeIndex = -1;
  let composing = false;
  let lastQuery = '';
  const cache = new Map();
  const WAIT_MS = 220;

  function close() {
    panel.hidden = true;
    panel.replaceChildren();
    input.setAttribute('aria-expanded', 'false');
    input.removeAttribute('aria-activedescendant');
    results = [];
    activeIndex = -1;
  }

  function cancel() {
    if (timer !== null) clearTimeout(timer);
    timer = null;
    if (pending) pending.abort();
    pending = null;
    serial += 1;
  }

  function message(text) {
    panel.replaceChildren();
    const node = document.createElement('div');
    node.className = 'stock-suggest-message';
    node.textContent = text;
    panel.appendChild(node);
    panel.hidden = false;
    input.setAttribute('aria-expanded', 'true');
    input.removeAttribute('aria-activedescendant');
  }

  function highlight(next) {
    const options = panel.querySelectorAll('[role="option"]');
    if (!options.length) return;
    activeIndex = Math.max(0, Math.min(options.length - 1, next));
    options.forEach((el, index) => {
      const selected = index === activeIndex;
      el.classList.toggle('active', selected);
      el.setAttribute('aria-selected', String(selected));
    });
    input.setAttribute('aria-activedescendant', 'stock-suggest-' + activeIndex);
    options[activeIndex].scrollIntoView({block: 'nearest'});
  }

  function choose(index) {
    const item = results[index];
    if (!item) return;
    cancel();
    input.value = item.code;
    close();
    input.focus();
  }

  function draw(items, query) {
    panel.replaceChildren();
    results = items;
    activeIndex = -1;
    if (!items.length) {
      // Do not pretend an empty search response confirms that no stock exists.
      message('没有匹配项，可尝试完整名称或六位证券代码');
      return;
    }
    const fragment = document.createDocumentFragment();
    const funds = items.filter(item => item.asset_type === 'etf');
    if (funds.length > 1) {
      const hint = document.createElement('div');
      hint.className = 'stock-suggest-message stock-suggest-hint';
      hint.textContent = '找到 ' + funds.length +
        ' 只 ETF；按规模从大到小排列，规模缺失的排在后面。点击具体基金继续分析。';
      fragment.appendChild(hint);
    }
    items.forEach((item, index) => {
      const option = document.createElement('button');
      option.type = 'button';
      option.id = 'stock-suggest-' + index;
      option.className = 'stock-suggest-option';
      option.setAttribute('role', 'option');
      option.setAttribute('aria-selected', 'false');
      const name = document.createElement('span');
      name.className = 'stock-suggest-name';
      name.textContent = String(item.name || '');
      const code = document.createElement('span');
      code.className = 'stock-suggest-code';
      code.textContent = String(item.code || '');
      if (item.asset_type === 'etf') {
        const left = document.createElement('span');
        left.className = 'stock-suggest-info';
        const scale = document.createElement('span');
        scale.className = 'stock-suggest-meta';
        const size = Number(item.scale_yi);
        if (item.scale_yi !== null && item.scale_yi !== undefined &&
            Number.isFinite(size) && size > 0) {
          const proxy = item.scale_kind === '二级市场市值近似';
          scale.textContent = (proxy ? '市值近似 ' : '规模估算 ') +
            size.toFixed(2) + ' 亿元' +
            (item.scale_as_of ? ' · ' + item.scale_as_of : '') +
            (proxy ? ' · 非基金净资产' : '');
        } else {
          scale.textContent = '规模暂无可核实数据';
        }
        left.append(name, scale);
        option.append(left, code);
      } else {
        option.append(name, code);
      }
      // mousedown prevents blur before click; normal keyboard tab works too.
      option.addEventListener('mousedown', event => event.preventDefault());
      option.addEventListener('click', () => choose(index));
      fragment.appendChild(option);
    });
    panel.appendChild(fragment);
    panel.hidden = false;
    input.setAttribute('aria-expanded', 'true');
    input.removeAttribute('aria-activedescendant');
  }

  async function search(rawQuery, ticket) {
    const query = rawQuery.trim();
    const key = query.toLocaleLowerCase();
    if (cache.has(key)) {
      if (ticket === serial && input.value.trim() === query) draw(cache.get(key), query);
      return;
    }
    pending = new AbortController();
    const controller = pending;
    try {
      const maxResults = /etf/i.test(query) || /沪深300|黄金股|黄金产业/.test(query) ? 60 : 8;
      const res = await fetch('/api/stocks/suggest?q=' +
        encodeURIComponent(query) + '&limit=' + maxResults, {
          signal: controller.signal,
          headers: {Accept: 'application/json'},
        });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const payload = await res.json();
      const items = Array.isArray(payload.items) ? payload.items.filter(
        item => item && /^[A-Z]{2}[0-9]{6}$/.test(item.code) &&
          typeof item.name === 'string'
      ).slice(0, maxResults) : [];
      if (cache.size > 80) cache.clear();
      cache.set(key, items);
      if (ticket === serial && input.value.trim() === query)
        draw(items, query);
    } catch (err) {
      if (err.name !== 'AbortError' && ticket === serial &&
          input.value.trim() === query) {
        // The API may be temporarily offline; manual code entry still works.
        message('候选列表暂不可用，可直接输入六位证券代码后分析');
      }
    } finally {
      if (pending === controller) pending = null;
    }
  }

  function schedule() {
    cancel();
    close();
    if (composing) return;
    const query = input.value.trim();
    if (!query || query.length > 40) return;
    const ticket = serial;
    lastQuery = query;
    timer = setTimeout(() => {
      timer = null;
      message('正在查找股票与ETF…');
      search(query, ticket);
    }, WAIT_MS);
  }

  input.addEventListener('input', schedule);
  input.addEventListener('compositionstart', () => {
    composing = true;
    cancel();
    close();
  });
  input.addEventListener('compositionend', () => {
    composing = false;
    schedule();
  });
  input.addEventListener('keydown', event => {
    if (composing || event.isComposing) return;
    if (event.key === 'Escape') {
      if (!panel.hidden) {event.preventDefault();cancel();close();}
      return;
    }
    if ((event.key === 'ArrowDown' || event.key === 'ArrowUp') &&
        !panel.hidden && results.length) {
      event.preventDefault();
      highlight(event.key === 'ArrowDown'
        ? (activeIndex + 1) % results.length
        : (activeIndex + results.length - 1) % results.length);
      return;
    }
    if (event.key === 'Enter') {
      event.preventDefault();
      if (!panel.hidden && results.length) {
        choose(activeIndex < 0 ? 0 : activeIndex);
      } else if (typeof submitAnalysis === 'function') {
        cancel();close();
        submitAnalysis();
      }
    }
  });
  input.addEventListener('blur', () => {
    // Allow mouse events and focus on a suggestion to finish first.
    setTimeout(() => {
      if (!panel.contains(document.activeElement)) {cancel();close();}
    }, 120);
  });
  document.addEventListener('pointerdown', event => {
    if (!panel.contains(event.target) && event.target !== input)
      {cancel();close();}
  });
  const submit = document.getElementById('submitBtn');
  if (submit) submit.addEventListener('click', () => {cancel();close();});
})();