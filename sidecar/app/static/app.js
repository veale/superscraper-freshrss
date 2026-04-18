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
          }
        }
      }

      // Close the refine block
      const details = form.closest('details');
      if (details) details.removeAttribute('open');

    } catch (err) {
      console.error('Global refine error:', err);
      alert('Failed to apply refine examples. Please try again.');
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = 'Apply to all candidates';
    }
  });
}
