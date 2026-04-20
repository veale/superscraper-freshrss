/* AutoFeed UI — app.js */

document.addEventListener('DOMContentLoaded', () => {
  initExampleLinks();
  initFlashMessages();
  initPreviewLoaders();
  initLazyPreviews();
  initClipboard();
  initDeleteConfirmations();
  initUrlSubmitShortcut();
  initRefineInputs();
  initGlobalRefineForm();
  initCandidateRefine();
  initBackendStealthDisable();
  initLlmXpathHunt();
  initUnderTheHood();
});

// Fill the URL input when an example link is clicked
function initExampleLinks() {
  document.querySelectorAll('[data-example-url]').forEach(link => {
    link.addEventListener('click', e => {
      e.preventDefault();
      const input = document.querySelector('.url-input');
      if (input) {
        input.value = link.dataset.exampleUrl;
        input.focus();
      }
    });
  });
}

// Auto-dismiss flash messages after 4s; dismiss button removes immediately
function initFlashMessages() {
  document.querySelectorAll('.flash').forEach(flash => {
    const btn = flash.querySelector('.flash-dismiss');
    if (btn) {
      btn.addEventListener('click', () => removeFlash(flash));
    }
    setTimeout(() => removeFlash(flash), 4000);
  });
}

function removeFlash(el) {
  if (!el.isConnected) return;
  el.style.transition = 'opacity 0.35s';
  el.style.opacity = '0';
  setTimeout(() => el.remove(), 360);
}

/**
 * Progressively load preview fragments for auto-preview targets.
 * Called on discover results page; no-op on home/other pages.
 *
 * Regression guard (manual inspection):
 * 1. Open a results page with >2 candidates in at least one section.
 * 2. Confirm the first 1–2 candidates in each section auto-preview
 *    (preview table appears without clicking anything).
 * 3. Confirm later candidates still show a "Preview" button.
 * 4. Click the Preview button. The button disappears and the preview
 *    renders in its sibling div — NOT inside a <button> wrapper.
 * 5. In the rendered DOM, assert:
 *    document.querySelector('button.preview-btn .preview-empty') === null
 *    and
 *    document.querySelector('button.preview-btn .preview-table') === null.
 */
function initPreviewLoaders() {
  const targets = document.querySelectorAll('.preview-target[data-preview-url]');
  if (targets.length === 0) return;

  // Mark every queued target with a subtle state
  targets.forEach(t => t.classList.add('preview-queued'));

  const queue = Array.from(targets);
  const maxConcurrent = 4;
  let active = 0;

  function next() {
    if (queue.length === 0 || active >= maxConcurrent) return;
    active++;
    const target = queue.shift();
    target.classList.remove('preview-queued');
    target.classList.add('preview-loading');
    fetch(target.dataset.previewUrl)
      .then(r => r.text())
      .then(html => {
        target.innerHTML = html;
        target.classList.remove('preview-loading');
      })
      .catch(e => {
        target.innerHTML = `<div class="preview-error">Preview failed: ${escapeHtml(e.message)}</div>`;
        target.classList.remove('preview-loading');
      })
      .finally(() => { active--; next(); });
  }

  for (let i = 0; i < maxConcurrent; i++) next();
}

// Click-to-load preview for non-auto-preview candidates
function initLazyPreviews() {
  document.addEventListener('click', e => {
    const btn = e.target.closest('.preview-btn');
    if (!btn || btn.disabled) return;

    const targetId = btn.dataset.target;
    const previewUrl = btn.dataset.previewUrl;
    if (!targetId || !previewUrl) return;

    const target = document.getElementById(targetId);
    if (!target) return;

    btn.disabled = true;
    btn.textContent = 'Loading…';
    target.innerHTML = '<div class="skeleton skeleton-preview"></div>';

    fetch(previewUrl)
      .then(r => r.text())
      .then(html => {
        target.innerHTML = html;
        btn.remove();
      })
      .catch(err => {
        target.innerHTML =
          '<div class="preview-error">Preview failed: ' + escapeHtml(err.message) + '</div>';
        btn.disabled = false;
        btn.textContent = 'Retry';
      });
  });
}

// Copy-to-clipboard buttons: [data-copy-text] or [data-copy-target="#selector"]
function initClipboard() {
  document.addEventListener('click', e => {
    const btn = e.target.closest('[data-copy-text], [data-copy-target]');
    if (!btn) return;

    let text = btn.dataset.copyText;
    if (!text && btn.dataset.copyTarget) {
      const el = document.querySelector(btn.dataset.copyTarget);
      text = el ? el.textContent.trim() : '';
    }
    if (!text) return;

    if (!navigator.clipboard) {
      btn.textContent = 'Copy failed';
      return;
    }
    navigator.clipboard.writeText(text).then(() => {
      const orig = btn.textContent;
      btn.textContent = 'Copied';
      setTimeout(() => { btn.textContent = orig; }, 2000);
    }).catch(() => {
      btn.textContent = 'Copy failed';
    });
  });
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// Safe delete confirmation using data-confirm attribute with JSON encoding
function initDeleteConfirmations() {
  document.addEventListener('submit', e => {
    const form = e.target.closest('form[data-confirm]');
    if (!form) return;
    try {
      const msg = JSON.parse(form.dataset.confirm);
      if (!confirm(msg)) {
        e.preventDefault();
      }
    } catch {
      // Fallback: if JSON parsing fails, use raw message
      if (!confirm(form.dataset.confirm)) {
        e.preventDefault();
      }
    }
  });
}

// Cmd/Ctrl+Enter submits the URL form on home page
function initUrlSubmitShortcut() {
  document.querySelector('.url-input')?.addEventListener('keydown', e => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
      e.target.form.submit();
    }
  });
}

// Handle add/remove buttons for refine input fields
function initRefineInputs() {
  document.querySelectorAll('.refine-add').forEach(btn => {
    btn.addEventListener('click', () => {
      const role = btn.dataset.role;
      const wrap = btn.parentElement.querySelector('.refine-inputs');
      if (!wrap) return;

      const count = wrap.querySelectorAll('input').length;
      if (count >= 3) return;  // hard cap

      const row = document.createElement('div');
      row.className = 'refine-input-row';

      const input = document.createElement('input');
      input.type = 'text';
      input.name = `${role}_examples`;
      input.placeholder = 'Additional example';

      const remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'btn btn-sm btn-link refine-remove';
      remove.textContent = '−';
      remove.addEventListener('click', () => {
        row.remove();
        // Show add button again if we're below cap
        if (wrap.querySelectorAll('input').length < 3) {
          btn.style.display = '';
        }
      });

      row.append(input, remove);
      wrap.appendChild(row);

      // Hide the add button when at cap
      if (wrap.querySelectorAll('input').length >= 3) {
        btn.style.display = 'none';
      }
    });
  });
}

// Handle global refine form submission
function initGlobalRefineForm() {
  const form = document.getElementById('global-refine-form');
  if (!form) return;

  form.addEventListener('submit', async e => {
    e.preventDefault();

    const formData = new FormData(form);
    const submitBtn = form.querySelector('button[type="submit"]');
    submitBtn.disabled = true;
    submitBtn.textContent = 'Applying...';

    // Mark every preview target as reloading
    document.querySelectorAll('.preview-target').forEach(t => {
      t.classList.add('preview-loading');
      t.innerHTML = '<div class="skeleton skeleton-preview"></div>';
    });

    try {
      const response = await fetch('/preview-fragment-refined', {
        method: 'POST',
        body: formData,
      });

      if (!response.ok) {
        throw new Error('Refine failed');
      }

      const data = await response.json();

      // Update each candidate's preview
      for (const [type, previews] of Object.entries(data)) {
        for (const [index, html] of Object.entries(previews)) {
          const targetId = `preview-${type}-${index}`;
          const target = document.getElementById(targetId);
          if (target) {
            target.innerHTML = html;
            target.classList.remove('preview-loading');
          }
        }
      }

      // Any target still in loading state didn't get a response — clear it
      document.querySelectorAll('.preview-target.preview-loading').forEach(t => {
        t.classList.remove('preview-loading');
        t.innerHTML = '<div class="preview-note text-tertiary">No refinement applied to this candidate.</div>';
      });

      // Close the refine block
      const details = form.closest('details');
      if (details) details.removeAttribute('open');

      document.dispatchEvent(new CustomEvent('uth:trace-dirty'));

    } catch (err) {
      console.error('Global refine error:', err);
      // Rollback: tell user, leave previews in unknown state
      document.querySelectorAll('.preview-target.preview-loading').forEach(t => {
        t.classList.remove('preview-loading');
        t.innerHTML = `<div class="preview-error">Refine failed: ${escapeHtml(err.message)}</div>`;
      });
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = 'Apply to all candidates';
    }
  });
}

// Handle per-candidate refine button and form submission
function initCandidateRefine() {
  // Toggle refine panel when "Refine" button is clicked
  document.addEventListener('click', e => {
    const refineBtn = e.target.closest('.card-refine-btn');
    if (!refineBtn) return;

    const index = refineBtn.dataset.index;
    const panel = document.getElementById(`refine-panel-xpath-${index}`);
    if (panel) {
      panel.hidden = !panel.hidden;
    }
  });

  // Cancel button hides the panel
  document.addEventListener('click', e => {
    const cancelBtn = e.target.closest('.refine-cancel');
    if (!cancelBtn) return;

    const index = cancelBtn.dataset.index;
    const panel = document.getElementById(`refine-panel-xpath-${index}`);
    if (panel) {
      panel.hidden = true;
    }
  });

  // Handle refine form submission (both .refine-form-inline and .refine-xpath-form)
  document.addEventListener('submit', async e => {
    const form = e.target.closest('.refine-form-inline, .refine-xpath-form');
    if (!form) return;

    e.preventDefault();

    const formData = new FormData(form);
    // e.submitter carries the clicked button — its name/value override FormData
    const submitter = e.submitter;
    if (submitter && submitter.name === 'mode') {
      formData.set('mode', submitter.value);
    }

    const index = form.dataset.index;
    const card = document.getElementById(`card-xpath-${index}`);
    const previewTarget = card?.querySelector('.preview-target');

    const submitBtns = form.querySelectorAll('button[type="submit"]');
    submitBtns.forEach(b => { b.disabled = true; });
    const clickedLabel = submitter ? submitter.textContent : 'Apply';
    if (submitter) submitter.textContent = 'Applying…';

    if (previewTarget) {
      previewTarget.classList.add('preview-loading');
      previewTarget.innerHTML = '<div class="skeleton skeleton-preview"></div>';
    }

    try {
      const response = await fetch('/candidate-refine', {
        method: 'POST',
        body: formData,
      });

      if (!response.ok) {
        const errData = await response.json().catch(() => ({}));
        throw new Error(errData.error || 'Refine failed');
      }

      const data = await response.json();

      if (previewTarget && data.preview_html) {
        previewTarget.innerHTML = data.preview_html;
        previewTarget.classList.remove('preview-loading');
      }

      if (data.selectors) {
        const selectorsGrid = card?.querySelector('.selectors-grid');
        if (selectorsGrid) {
          let html = '';
          const fields = [
            ['Item', data.selectors.item_selector],
            ['Title', data.selectors.title_selector],
            ['Link', data.selectors.link_selector],
            ['Content', data.selectors.content_selector],
            ['Timestamp', data.selectors.timestamp_selector],
            ['Author', data.selectors.author_selector],
            ['Thumbnail', data.selectors.thumbnail_selector],
          ];
          for (const [label, val] of fields) {
            if (val) {
              html += `<span class="selector-label">${label}</span><code class="selector-value">${escapeHtml(val)}</code>`;
            }
          }
          selectorsGrid.innerHTML = html;
        }

        const saveForm = card?.querySelector('form[action="/save"]');
        if (saveForm) {
          for (const key of ['item_selector', 'title_selector', 'link_selector', 'content_selector', 'timestamp_selector']) {
            const input = saveForm.querySelector(`input[name="${key}"]`);
            if (input && data.selectors[key]) input.value = data.selectors[key];
          }
        }

        // Update the visible item-selector header if it changed.
        if (data.selectors.item_selector) {
          const urlEl = document.getElementById(`item-selector-xpath-${index}`);
          if (urlEl && urlEl.textContent !== data.selectors.item_selector) {
            urlEl.textContent = data.selectors.item_selector;
            urlEl.classList.add('selector-changed');
          }
          // Also update the item_selector input in the advanced xpath editor.
          const xpathForm = card?.querySelector('.refine-xpath-form');
          if (xpathForm) {
            const itemInput = xpathForm.querySelector('input[name="item_selector"]');
            if (itemInput) itemInput.value = data.selectors.item_selector;
          }
        }
      }

      // Show LLM reasoning note if provided.
      if (data.reasoning && card) {
        let note = card.querySelector('.refine-reasoning-note');
        if (!note) {
          note = document.createElement('p');
          note.className = 'refine-reasoning-note text-tertiary';
          const urlEl = card.querySelector('.candidate-url');
          if (urlEl) urlEl.insertAdjacentElement('afterend', note);
          else card.insertBefore(note, card.firstChild);
        }
        note.textContent = `LLM: ${data.reasoning}`;
      }

      // Add refined badge (LLM mode gets a different label)
      if (card && !card.querySelector('.badge-refined')) {
        const badge = document.createElement('span');
        badge.className = 'badge badge-refined';
        badge.textContent = formData.get('mode') === 'llm' ? '🤖 LLM' : 'refined';
        const header = card.querySelector('.candidate-header');
        if (header) header.appendChild(badge);
      }

      const panel = document.getElementById(`refine-panel-xpath-${index}`);
      if (panel) panel.hidden = true;

      document.dispatchEvent(new CustomEvent('uth:trace-dirty'));

    } catch (err) {
      console.error('Candidate refine error:', err);
      if (previewTarget) {
        previewTarget.classList.remove('preview-loading');
        previewTarget.innerHTML = `<div class="preview-error">Refine failed: ${escapeHtml(err.message)}</div>`;
      }
      alert(err.message);
    } finally {
      submitBtns.forEach(b => { b.disabled = false; });
      if (submitter) submitter.textContent = clickedLabel;
    }
  });
}

// Disable "Solve Cloudflare" checkbox when backend isn't stealthy
function initBackendStealthDisable() {
  document.addEventListener('change', e => {
    const select = e.target.closest('select[name="fetch_backend_override"]');
    if (!select) return;
    const form = select.closest('form');
    if (!form) return;
    const cb = form.querySelector('input[name="solve_cloudflare"]');
    if (!cb) return;
    const stealthy = ['stealthy', 'scrapling_serve'].includes(select.value);
    cb.disabled = !stealthy;
    if (!stealthy) cb.checked = false;
  });
}

/* ── Under the hood (UTH) transparency panels ────────────────────────────── */

const UTH = {
  cache: new Map(),   // discover_id -> bundle
  inflight: new Map(),
};

function initUnderTheHood() {
  const panels = document.querySelectorAll('details.under-the-hood[data-uth]');
  if (panels.length === 0) return;

  panels.forEach(panel => {
    panel.addEventListener('toggle', () => {
      if (panel.open) renderUthPanel(panel);
    });
  });

  // After any refine/LLM/analyze action completes on this page, the trace
  // has changed — mark all panels stale so the next open re-fetches.
  document.addEventListener('uth:trace-dirty', () => {
    UTH.cache.clear();
    document.querySelectorAll('details.under-the-hood[data-uth]').forEach(p => {
      p.dataset.uthLoaded = '';
      if (p.open) renderUthPanel(p);
    });
  });
}

async function renderUthPanel(panel) {
  const discoverId = panel.dataset.discoverId;
  const panelKey = panel.dataset.panel;
  const body = panel.querySelector('[data-uth-body]');
  if (!body || !discoverId) return;

  body.innerHTML = '<p class="uth-placeholder text-tertiary">Loading trace…</p>';

  let bundle;
  try {
    bundle = await fetchUthBundle(discoverId);
  } catch (err) {
    body.innerHTML = `<div class="uth-error">Failed to load trace: ${escapeHtml(err.message)}</div>`;
    return;
  }
  if (!bundle) {
    body.innerHTML = '<p class="uth-placeholder text-tertiary">No trace recorded for this discovery.</p>';
    return;
  }

  body.innerHTML = '';
  body.append(renderUthHeader(bundle, panelKey, discoverId));

  if (panelKey === 'discovery') {
    body.append(renderDiscoverySections(bundle, discoverId));
  }

  body.append(renderActionsForPanel(bundle, panelKey));

  panel.dataset.uthLoaded = '1';
}

async function fetchUthBundle(discoverId) {
  if (UTH.cache.has(discoverId)) return UTH.cache.get(discoverId);
  if (UTH.inflight.has(discoverId)) return UTH.inflight.get(discoverId);

  const p = fetch(`/debug/discover/${encodeURIComponent(discoverId)}`)
    .then(r => {
      if (r.status === 404) return null;
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    })
    .then(data => {
      UTH.cache.set(discoverId, data);
      UTH.inflight.delete(discoverId);
      return data;
    })
    .catch(err => {
      UTH.inflight.delete(discoverId);
      throw err;
    });

  UTH.inflight.set(discoverId, p);
  return p;
}

function renderUthHeader(bundle, panelKey, discoverId) {
  const wrap = document.createElement('div');
  wrap.className = 'uth-section';

  const hdr = document.createElement('div');
  hdr.className = 'uth-section-header';
  hdr.innerHTML = `
    <span class="uth-kind">Panel</span>
    <span class="uth-meta">${escapeHtml(panelKey)}</span>
    <span class="uth-meta">· discover_id=${escapeHtml(discoverId)}</span>
    <span class="uth-meta">· ${Object.keys(bundle.artifacts || {}).length} artifact(s), ${bundle.actions?.length || 0} action(s)</span>
  `;

  const refreshBtn = document.createElement('button');
  refreshBtn.type = 'button';
  refreshBtn.className = 'btn btn-sm btn-secondary uth-refresh-btn';
  refreshBtn.textContent = 'Refresh';
  refreshBtn.style.marginLeft = 'auto';
  refreshBtn.addEventListener('click', e => {
    e.preventDefault();
    UTH.cache.delete(discoverId);
    const panel = refreshBtn.closest('details.under-the-hood');
    if (panel) renderUthPanel(panel);
  });
  hdr.append(refreshBtn);

  wrap.append(hdr);
  return wrap;
}

function renderDiscoverySections(bundle, discoverId) {
  const frag = document.createDocumentFragment();
  const discovery = bundle.discovery || {};
  const artifacts = bundle.artifacts || {};

  // Fetch info
  if (discovery.fetch) {
    frag.append(uthSection('Fetch', null, body => {
      body.append(uthField('Request', jsonBlock(discovery.fetch)));
    }));
  }

  // Artifacts (HTML blobs + downloads)
  const artKeys = Object.keys(artifacts);
  if (artKeys.length) {
    frag.append(uthSection('HTML artifacts', null, body => {
      for (const kind of artKeys) {
        const meta = artifacts[kind] || {};
        const row = document.createElement('div');
        row.className = 'uth-artifact-row';
        const sizeKb = (meta.size || 0) / 1024;
        row.innerHTML = `
          <span class="uth-artifact-name">${escapeHtml(kind)}</span>
          <span class="uth-artifact-size">${sizeKb.toFixed(1)} KB${meta.truncated ? ' (truncated)' : ''}</span>
        `;
        const link = document.createElement('a');
        link.href = `/debug/discover/${encodeURIComponent(discoverId)}/artifact/${encodeURIComponent(kind)}`;
        link.textContent = 'Download full';
        link.className = 'uth-download text-secondary';
        link.target = '_blank';
        link.rel = 'noopener';
        row.append(link);

        const viewBtn = document.createElement('button');
        viewBtn.type = 'button';
        viewBtn.className = 'btn btn-sm btn-link';
        viewBtn.textContent = 'View';
        viewBtn.style.padding = '0 var(--space-2)';
        const viewHolder = document.createElement('div');
        viewHolder.style.width = '100%';
        viewBtn.addEventListener('click', async () => {
          if (viewHolder.dataset.loaded) {
            viewHolder.style.display = viewHolder.style.display === 'none' ? '' : 'none';
            return;
          }
          viewBtn.disabled = true;
          viewBtn.textContent = 'Loading…';
          try {
            const resp = await fetch(link.href);
            const text = await resp.text();
            const pre = document.createElement('pre');
            pre.className = 'uth-code uth-code--wide';
            pre.innerHTML = highlightHtml(text);
            viewHolder.append(pre);
            viewHolder.dataset.loaded = '1';
            viewBtn.textContent = 'Hide';
            viewBtn.disabled = false;
          } catch (err) {
            viewHolder.innerHTML = `<div class="uth-error">Failed: ${escapeHtml(err.message)}</div>`;
            viewBtn.textContent = 'View';
            viewBtn.disabled = false;
          }
        });
        row.append(viewBtn);

        body.append(row);
        body.append(viewHolder);
      }
    }));
  }

  // Steps (rss, embedded_json, api_static, prune, xpath_heuristic, browser_fetch, graphql, xpath_scrapling, initial_examples, skeleton)
  if (discovery.steps && Object.keys(discovery.steps).length) {
    frag.append(uthSection('Pipeline steps', null, body => {
      for (const [name, data] of Object.entries(discovery.steps)) {
        body.append(uthField(name, jsonBlock(data)));
      }
    }));
  }

  if (discovery.decision) {
    frag.append(uthSection('Decision', null, body => {
      body.append(uthField('Signals', jsonBlock(discovery.decision)));
    }));
  }

  return frag;
}

function renderActionsForPanel(bundle, panelKey) {
  const actions = (bundle.actions || []).filter(a => a.panel === panelKey);

  const section = uthSection(
    panelKey === 'discovery' ? 'Actions on this discovery' : 'Actions on this panel',
    actions.length ? `${actions.length} recorded` : null,
    body => {
      if (actions.length === 0) {
        body.innerHTML = '<p class="uth-action-empty">No actions recorded yet. Run a refine, preview, or LLM step and refresh.</p>';
        return;
      }
      const list = document.createElement('div');
      list.className = 'uth-actions-list';
      actions.slice().reverse().forEach(a => list.append(renderAction(a)));
      body.append(list);
    }
  );
  return section;
}

function renderAction(action) {
  const card = document.createElement('div');
  card.className = 'uth-section';

  const hdr = document.createElement('div');
  hdr.className = 'uth-section-header';
  const when = action.timestamp
    ? new Date(action.timestamp * 1000).toLocaleTimeString()
    : '';
  const pills = [];
  if (action.kind) pills.push(action.kind);
  if (action.mode) pills.push(`mode:${action.mode}`);
  if (action.error) pills.push('error');
  hdr.innerHTML = `
    <span class="uth-kind">${escapeHtml(action.kind || 'action')}</span>
    ${action.mode ? `<span class="uth-pill">${escapeHtml(action.mode)}</span>` : ''}
    ${action.error ? `<span class="uth-pill" style="background:var(--error);color:var(--accent-fg);border-color:var(--error);">error</span>` : ''}
    <span class="uth-meta">${escapeHtml(when)}</span>
    <span class="uth-meta">· ${escapeHtml(action.action_id || '')}</span>
  `;
  card.append(hdr);

  const body = document.createElement('div');
  body.className = 'uth-section-body';

  if (action.provenance) {
    body.append(uthField('Provenance', jsonBlock(action.provenance)));
  }
  if (action.inputs) {
    body.append(uthField('Inputs', jsonBlock(action.inputs)));
  }
  if (action.llm_call) {
    body.append(renderLlmCall(action.llm_call));
  }
  if (action.outputs) {
    body.append(uthField('Outputs', jsonBlock(action.outputs)));
  }
  if (action.error) {
    const err = document.createElement('div');
    err.className = 'uth-error';
    err.textContent = action.error;
    body.append(err);
  }

  card.append(body);
  return card;
}

function renderLlmCall(llm) {
  const wrap = document.createElement('div');
  wrap.className = 'uth-field';

  const label = document.createElement('div');
  label.className = 'uth-field-label';
  const modelBit = llm.model ? ` · ${escapeHtml(llm.model)}` : '';
  const tokBit = llm.tokens_used != null ? ` · ${llm.tokens_used} tokens` : '';
  label.innerHTML = `LLM call${modelBit}${tokBit}`;
  wrap.append(label);

  if (llm.system) {
    wrap.append(uthSubField('System prompt', textBlock(llm.system)));
  }
  if (llm.user) {
    wrap.append(uthSubField('User prompt', textBlock(llm.user)));
  }
  if (llm.raw_content) {
    const pre = document.createElement('pre');
    pre.className = 'uth-code';
    pre.innerHTML = highlightJson(tryPrettyJson(llm.raw_content));
    wrap.append(uthSubField('Response', pre));
  }
  if (llm.error) {
    const err = document.createElement('div');
    err.className = 'uth-error';
    err.textContent = llm.error;
    wrap.append(err);
  }
  return wrap;
}

function uthSection(title, meta, buildBody) {
  const section = document.createElement('div');
  section.className = 'uth-section';
  const hdr = document.createElement('div');
  hdr.className = 'uth-section-header';
  hdr.innerHTML = `<span class="uth-kind">${escapeHtml(title)}</span>${meta ? ` <span class="uth-meta">${escapeHtml(meta)}</span>` : ''}`;
  const body = document.createElement('div');
  body.className = 'uth-section-body';
  if (buildBody) buildBody(body);
  section.append(hdr, body);
  return section;
}

function uthField(label, content) {
  const wrap = document.createElement('div');
  wrap.className = 'uth-field';
  const lbl = document.createElement('div');
  lbl.className = 'uth-field-label';
  lbl.textContent = label;
  wrap.append(lbl);
  if (content instanceof Node) wrap.append(content);
  else {
    const pre = document.createElement('pre');
    pre.className = 'uth-code';
    pre.textContent = String(content);
    wrap.append(pre);
  }
  return wrap;
}

function uthSubField(label, content) {
  return uthField(label, content);
}

function jsonBlock(value) {
  const pre = document.createElement('pre');
  pre.className = 'uth-code';
  pre.innerHTML = highlightJson(safeStringify(value));
  return pre;
}

function textBlock(value) {
  const pre = document.createElement('pre');
  pre.className = 'uth-code';
  pre.textContent = String(value ?? '');
  return pre;
}

function safeStringify(value) {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function tryPrettyJson(raw) {
  if (typeof raw !== 'string') return safeStringify(raw);
  try {
    return JSON.stringify(JSON.parse(raw), null, 2);
  } catch {
    return raw;
  }
}

/* Minimal, dependency-free syntax highlighter. Tokenizes JSON and HTML;
   XPath values are left as monospace since no single highlight scheme fits
   every context they appear in.  */

function highlightJson(src) {
  if (typeof src !== 'string') src = String(src);
  const safe = escapeHtml(src);
  return safe
    // strings (including keys — key detection done after)
    .replace(/"(?:\\.|[^"\\])*"(?=\s*:)/g, m => `<span class="tok-key">${m}</span>`)
    .replace(/"(?:\\.|[^"\\])*"/g, m =>
      m.includes('class="tok-') ? m : `<span class="tok-string">${m}</span>`
    )
    .replace(/\b(true|false|null)\b/g, '<span class="tok-bool">$1</span>')
    .replace(/(?<![\w"])-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b/g, '<span class="tok-num">$&</span>');
}

function highlightHtml(src) {
  if (typeof src !== 'string') src = String(src);
  const parts = [];
  let i = 0;
  const tagRe = /<(!--[\s\S]*?--|\/?[a-zA-Z][\w:-]*[^<>]*?\/?)>/g;
  let m;
  while ((m = tagRe.exec(src)) !== null) {
    if (m.index > i) parts.push(escapeHtml(src.slice(i, m.index)));
    const inner = m[1];
    if (inner.startsWith('!--')) {
      parts.push(`<span class="tok-comment">${escapeHtml('<' + inner + '>')}</span>`);
    } else {
      parts.push(highlightHtmlTag(inner));
    }
    i = m.index + m[0].length;
  }
  if (i < src.length) parts.push(escapeHtml(src.slice(i)));
  return parts.join('');
}

function highlightHtmlTag(inner) {
  const slash = inner.startsWith('/') ? '/' : '';
  let body = slash ? inner.slice(1) : inner;
  const selfClose = body.endsWith('/');
  if (selfClose) body = body.slice(0, -1);
  const nameMatch = body.match(/^([a-zA-Z][\w:-]*)/);
  const name = nameMatch ? nameMatch[1] : '';
  const attrs = nameMatch ? body.slice(name.length) : body;
  const attrsHtml = attrs.replace(
    /([a-zA-Z_:][\w:.-]*)(\s*=\s*)("[^"]*"|'[^']*'|[^\s"'>]+)/g,
    (_, n, eq, v) => ` <span class="tok-attr">${escapeHtml(n)}</span>${escapeHtml(eq)}<span class="tok-string">${escapeHtml(v)}</span>`
  );
  return `<span class="tok-punc">&lt;${slash}</span><span class="tok-tag">${escapeHtml(name)}</span>${attrsHtml}<span class="tok-punc">${selfClose ? '/' : ''}&gt;</span>`;
}

// "Ask LLM to find XPath" button on discover results page
function initLlmXpathHunt() {
  const btn = document.getElementById('llm-xpath-btn');
  if (!btn) return;
  const status = document.getElementById('llm-xpath-status');
  const discoverIdBtn = btn.dataset.discoverId;

  btn.addEventListener('click', async () => {
    btn.disabled = true;
    btn.textContent = 'Searching…';
    if (status) { status.textContent = 'Asking LLM to find XPath selectors…'; status.style.display = ''; }

    try {
      const resp = await fetch(`/llm-xpath/${encodeURIComponent(discoverIdBtn)}`, { method: 'POST' });
      const data = await resp.json();
      if (!resp.ok) {
        btn.disabled = false;
        btn.textContent = 'Ask LLM to find XPath';
        if (status) status.textContent = 'Error: ' + (data.error || resp.statusText);
        return;
      }
      if (data.reload) {
        if (status) status.textContent = `Found: ${data.item_selector} (${data.probe_count} items). Reloading…`;
        setTimeout(() => location.reload(), 800);
      } else {
        btn.disabled = false;
        btn.textContent = 'Ask LLM to find XPath';
        if (status) status.textContent = data.error || 'Done.';
      }
    } catch (err) {
      btn.disabled = false;
      btn.textContent = 'Ask LLM to find XPath';
      if (status) status.textContent = 'Request failed: ' + escapeHtml(err.message);
    }
  });
}
