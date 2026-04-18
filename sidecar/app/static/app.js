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
