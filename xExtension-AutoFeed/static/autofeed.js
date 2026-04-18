document.addEventListener('DOMContentLoaded', function () {
	// Spinner: disable button and swap text on form submit
	document.querySelectorAll('[data-autofeed-spinner]').forEach(function (btn) {
		btn.closest('form').addEventListener('submit', function () {
			btn.disabled = true;
			var spinnerText = btn.getAttribute('data-spinner-text');
			if (spinnerText) {
				btn.textContent = spinnerText;
			}
		});
	});

	// Clipboard copy
	document.querySelectorAll('[data-autofeed-copy]').forEach(function (btn) {
		btn.addEventListener('click', function () {
			var selector = btn.getAttribute('data-autofeed-copy');
			var target = document.querySelector(selector);
			if (!target) return;
			navigator.clipboard.writeText(target.textContent).then(function () {
				var orig = btn.textContent;
				var copiedText = btn.getAttribute('data-copied-text') || 'Copied!';
				btn.textContent = copiedText;
				setTimeout(function () { btn.textContent = orig; }, 2000);
			});
		});
	});

	// Preview button handler
	var previewDebounceTimers = {};
	document.querySelectorAll('.autofeed-preview-btn').forEach(function (btn) {
		btn.addEventListener('click', function () {
			var card = btn.closest('.autofeed-card');
			var container = card.querySelector('.autofeed-preview-container');
			var index = card.getAttribute('data-candidate-index');

			// Get values from button data attributes
			var url = btn.getAttribute('data-url');
			var strategy = btn.getAttribute('data-strategy');

			// Build form data from the card's form inputs
			var form = card.querySelector('form');
			var formData = new FormData(form);

			// Build payload
			var payload = {
				url: url,
				strategy: strategy,
				timeout: 30,
			};

			// Add selectors based on strategy
			if (strategy === 'xpath') {
				payload.selectors = {
					item: formData.get('xPathItem') || btn.getAttribute('data-xpath-item'),
					title: formData.get('xPathItemTitle') || btn.getAttribute('data-xpath-title'),
					content: formData.get('xPathItemContent') || btn.getAttribute('data-xpath-content'),
					link: formData.get('xPathItemUri') || btn.getAttribute('data-xpath-uri'),
					timestamp: formData.get('xPathItemTimestamp') || btn.getAttribute('data-xpath-timestamp'),
					thumbnail: formData.get('xPathItemThumbnail') || btn.getAttribute('data-xpath-thumbnail'),
				};
			} else if (strategy === 'json_api' || strategy === 'json_dot_notation') {
				payload.selectors = {
					item: formData.get('jsonItem'),
					title: formData.get('jsonItemTitle'),
					link: formData.get('jsonItemUri'),
					content: formData.get('jsonItemContent'),
					timestamp: formData.get('jsonItemTimestamp'),
				};
			} else if (strategy === 'embedded_json') {
				payload.selectors = {
					item: formData.get('jsonItem'),
					title: formData.get('jsonItemTitle'),
					link: formData.get('jsonItemUri'),
					content: formData.get('jsonItemContent'),
					timestamp: formData.get('jsonItemTimestamp'),
				};
				var xPathToJson = formData.get('xPathToJson');
				if (xPathToJson) {
					payload.selectors.xpath_to_json = xPathToJson;
				}
			}

			// Show loading state
			container.innerHTML = '<div class="autofeed-loading">Loading preview...</div>';

			// Make the request
			var csrfToken = form.querySelector('input[name="_csrf"]').value;
			fetch('?c=AutoFeed&a=preview', {
				method: 'POST',
				headers: {
					'Content-Type': 'application/x-www-form-urlencoded',
					'X-CSRF-Token': csrfToken,
				},
				body: new URLSearchParams({
					_csrf: csrfToken,
					url: payload.url,
					strategy: payload.strategy,
					timeout: payload.timeout,
					xPathItem: payload.selectors?.item || '',
					xPathItemTitle: payload.selectors?.title || '',
					xPathItemContent: payload.selectors?.content || '',
					xPathItemUri: payload.selectors?.link || '',
					xPathItemTimestamp: payload.selectors?.timestamp || '',
					xPathItemThumbnail: payload.selectors?.thumbnail || '',
					jsonItem: payload.selectors?.item || '',
					jsonItemTitle: payload.selectors?.title || '',
					jsonItemUri: payload.selectors?.link || '',
					jsonItemContent: payload.selectors?.content || '',
					jsonItemTimestamp: payload.selectors?.timestamp || '',
					xPathToJson: payload.selectors?.xpath_to_json || '',
				}),
			})
			.then(function (response) { return response.text(); })
			.then(function (html) {
				container.innerHTML = html;
			})
			.catch(function (err) {
				container.innerHTML = '<div class="alert alert-error">Preview error: ' + err.message + '</div>';
			});
		});

		// Add debounced live preview on input changes
		var card = btn.closest('.autofeed-card');
		var inputs = card.querySelectorAll('input[name^="xPathItem"], input[name^="jsonItem"]');
		inputs.forEach(function (input) {
			input.addEventListener('input', function () {
				var index = card.getAttribute('data-candidate-index');
				if (previewDebounceTimers[index]) {
					clearTimeout(previewDebounceTimers[index]);
				}
				previewDebounceTimers[index] = setTimeout(function () {
					btn.click();
				}, 600);
			});
		});
	});
});
