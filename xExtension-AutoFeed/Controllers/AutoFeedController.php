<?php

namespace Extension\AutoFeed;

/**
 * Controller for the AutoFeed Discovery extension.
 *
 * Registered as the "AutoFeed" controller, its actions are accessible at
 * URLs like  ?c=AutoFeed&a=discover  (via Minz routing).
 */
class FreshExtension_AutoFeed_Controller extends Minz_ActionController {

	private function requireCsrfPost(): bool {
		if (!Minz_Request::isPost() ||
			Minz_Request::paramString('_csrf') !== FreshRSS_Auth::csrfToken()) {
			Minz_Request::bad(_t('ext.autofeed.error_csrf'));
			Minz_Request::forward(['c' => 'AutoFeed', 'a' => 'discover'], true);
			return false;
		}
		return true;
	}

	private function fetchDiscoveryById(AutoFeedExtension $ext, string $discover_id): array {
		if ($discover_id === '') {
			return [];
		}
		$result = $ext->sidecarRequest('/discover/' . rawurlencode($discover_id), [], 'GET', 15);
		if (!$result['ok']) {
			return [];
		}
		return is_array($result['data']) ? $result['data'] : [];
	}

	/**
	 * GET — Show the discovery form.
	 */
	public function discoverAction(): void {
		Minz_View::prependTitle(_t('ext.autofeed.page_title') . ' · ');
		$this->view->url = Minz_Request::paramString('url');
	}

	/**
	 * POST — Send URL to sidecar, display results.
	 */
	public function analyzeAction(): void {
		if (!$this->requireCsrfPost()) {
			return;
		}
		Minz_View::prependTitle(_t('ext.autofeed.results_title') . ' · ');

		$url = trim(Minz_Request::paramString('url'));
		if (empty($url)) {
			Minz_Request::bad(_t('ext.autofeed.error_no_url'));
			Minz_Request::forward(['c' => 'AutoFeed', 'a' => 'discover'], true);
			return;
		}

		// Normalise URL.
		if (!preg_match('#^https?://#i', $url)) {
			$url = 'https://' . $url;
		}

		$ext = Minz_ExtensionManager::findExtension('AutoFeed');
		if ($ext === null) {
			Minz_Request::bad('AutoFeed extension not found.');
			return;
		}

		$use_browser = Minz_Request::paramBoolean('use_browser');
		$force_skip_rss = Minz_Request::paramBoolean('force_skip_rss');
		$override_xpath_item = trim(Minz_Request::paramString('override_xpath_item'));
		$http_timeout = $use_browser ? 90 : 60;

		$result = $ext->sidecarRequest('/discover', [
			'url'            => $url,
			'timeout'        => $use_browser ? 45 : 30,
			'use_browser'    => $use_browser,
			'force_skip_rss' => $force_skip_rss,
			'services'       => $ext->getServicesPayload(),
		], 'POST', $http_timeout);

		$this->view->target_url = $url;
		$this->view->sidecar_ok = $result['ok'];
		$this->view->sidecar_error = $result['error'];
		$this->view->discovery = $result['data'] ?? [];
		$this->view->override_xpath_item = $override_xpath_item;
	}

	/**
	 * POST — Call sidecar /analyze with LLM, re-render results with recommendation.
	 */
	public function llmAnalyzeAction(): void {
		if (!$this->requireCsrfPost()) {
			return;
		}

		$ext = Minz_ExtensionManager::findExtension('AutoFeed');
		if ($ext === null || !$ext->hasLlmConfigured()) {
			Minz_Request::bad(_t('ext.autofeed.llm_not_configured'));
			Minz_Request::forward(['c' => 'AutoFeed', 'a' => 'discover'], true);
			return;
		}

		$discover_id = trim(Minz_Request::paramString('discover_id'));
		$discovery = $this->fetchDiscoveryById($ext, $discover_id);
		if (empty($discovery)) {
			Minz_Request::bad(_t('ext.autofeed.error_discovery_expired'));
			Minz_Request::forward(['c' => 'AutoFeed', 'a' => 'discover'], true);
			return;
		}

		$url = $discovery['url'] ?? '';
		$results = $discovery['results'] ?? [];

		$result = $ext->sidecarRequest('/analyze', [
			'url'           => $url,
			'results'       => $results,
			'html_skeleton' => $results['html_skeleton'] ?? '',
			'llm'           => [
				'endpoint' => $ext->getLlmEndpoint(),
				'api_key'  => $ext->getLlmApiKey(),
				'model'    => $ext->getLlmModel(),
				'timeout'  => 60,
			],
		], 'POST', 90);

		Minz_View::prependTitle(_t('ext.autofeed.results_title') . ' · ');
		$this->view->target_url       = $url;
		$this->view->sidecar_ok       = true;
		$this->view->sidecar_error    = '';
		$this->view->discovery        = $discovery;
		$this->view->llm_recommendation = null;
		$this->view->llm_error        = '';

		if (!$result['ok']) {
			$this->view->llm_error = $result['error'];
		} else {
			$data = $result['data'] ?? [];
			$this->view->llm_recommendation = $data['recommendation'] ?? null;
			if (!empty($data['errors'])) {
				$this->view->llm_error = implode('; ', $data['errors']);
			}
		}

		// Re-use analyze.phtml instead of a separate view file.
		$this->view->_useLayout(true);
		$this->view->_partial('analyze.phtml');
	}

	/**
	 * POST — Generate an RSS-Bridge PHP script via the sidecar LLM.
	 */
	public function bridgeGenerateAction(): void {
		if (!$this->requireCsrfPost()) {
			return;
		}

		$ext = Minz_ExtensionManager::findExtension('AutoFeed');
		if ($ext === null || !$ext->hasLlmConfigured()) {
			Minz_Request::bad(_t('ext.autofeed.llm_not_configured'));
			Minz_Request::forward(['c' => 'AutoFeed', 'a' => 'discover'], true);
			return;
		}

		$discover_id = trim(Minz_Request::paramString('discover_id'));
		$discovery = $this->fetchDiscoveryById($ext, $discover_id);
		if (empty($discovery)) {
			Minz_Request::bad(_t('ext.autofeed.error_discovery_expired'));
			Minz_Request::forward(['c' => 'AutoFeed', 'a' => 'discover'], true);
			return;
		}

		$url     = $discovery['url'] ?? '';
		$results = $discovery['results'] ?? [];
		$hint    = trim(Minz_Request::paramString('hint'));

		$result = $ext->sidecarRequest('/bridge/generate', [
			'url'           => $url,
			'results'       => $results,
			'html_skeleton' => $results['html_skeleton'] ?? '',
			'llm'           => [
				'endpoint' => $ext->getLlmEndpoint(),
				'api_key'  => $ext->getLlmApiKey(),
				'model'    => $ext->getLlmModel(),
				'timeout'  => 90,
			],
			'hint' => $hint,
		], 'POST', 120);

		Minz_View::prependTitle(_t('ext.autofeed.bridge_generated_title') . ' · ');
		$this->view->bridge_error     = '';
		$this->view->bridge_name      = '';
		$this->view->php_code         = '';
		$this->view->sanity_warnings  = [];
		$this->view->deployed         = false;

		if (!$result['ok']) {
			$this->view->bridge_error = $result['error'];
		} else {
			$data = $result['data'] ?? [];
			if (!empty($data['errors'])) {
				$this->view->bridge_error = implode('; ', $data['errors']);
			} else {
				$this->view->bridge_name     = $data['bridge_name'] ?? '';
				$this->view->php_code        = $data['php_code'] ?? '';
				$this->view->sanity_warnings = $data['sanity_warnings'] ?? [];
			}
		}
	}

	/**
	 * POST — Deploy a generated bridge file via the sidecar.
	 */
	public function bridgeDeployAction(): void {
		if (!$this->requireCsrfPost()) {
			return;
		}

		$ext = Minz_ExtensionManager::findExtension('AutoFeed');
		if ($ext === null || !$ext->getAutoDeployBridges()) {
			Minz_Request::bad('Bridge auto-deploy is not enabled.');
			Minz_Request::forward(['c' => 'AutoFeed', 'a' => 'discover'], true);
			return;
		}

		$bridge_name = trim(Minz_Request::paramString('bridge_name'));
		$php_code    = Minz_Request::paramString('php_code');

		$result = $ext->sidecarRequest('/bridge/deploy', [
			'bridge_name' => $bridge_name,
			'php_code'    => $php_code,
			'services'    => $ext->getServicesPayload(),
		], 'POST', 30);

		Minz_View::prependTitle(_t('ext.autofeed.bridge_generated_title') . ' · ');
		$this->view->bridge_error    = '';
		$this->view->bridge_name     = $bridge_name;
		$this->view->php_code        = $php_code;
		$this->view->sanity_warnings = [];
		$this->view->deployed        = false;

		if (!$result['ok']) {
			$this->view->bridge_error = $result['error'];
		} else {
			$data = $result['data'] ?? [];
			if (!empty($data['errors'])) {
				$this->view->bridge_error = implode('; ', $data['errors']);
			} else {
				$this->view->deployed = (bool) ($data['deployed'] ?? false);
			}
		}
	}

	/**
	 * POST — Save a scrape config on the sidecar and subscribe via Atom feed.
	 *
	 * Calls sidecar /scrape/config, gets back {config_id, feed_url}, then
	 * creates a standard RSS feed in FreshRSS pointing at that Atom endpoint.
	 * FreshRSS refreshes it on schedule; the sidecar re-runs the scrape each time.
	 */
	/**
	 * Validate that a selector string is within acceptable length bounds.
	 * XPath injection is a DoS vector, not RCE - limit selector length to mitigate.
	 */
	private function validateSelector(string $selector, string $field_name): bool {
		$max_length = 512;
		if (strlen($selector) > $max_length) {
			Minz_Request::bad(sprintf(
				_t('ext.autofeed.selector_too_long') ?: 'Selector %s exceeds maximum length of %d characters',
				$field_name,
				$max_length
			));
			return false;
		}
		return true;
	}

	public function applyScrapedAction(): void {
		if (!$this->requireCsrfPost()) {
			return;
		}

		$strategy  = Minz_Request::paramString('strategy');
		$feed_url  = trim(Minz_Request::paramString('feed_url'));
		// Falls back to feed_url when page title is empty so FreshRSS never receives a blank name.
		$feed_name = trim(Minz_Request::paramString('feed_name')) ?: $feed_url;
		$category  = Minz_Request::paramInt('category');

		$ext = Minz_ExtensionManager::findExtension('AutoFeed');
		if ($ext === null) {
			Minz_Request::bad('AutoFeed extension not found.');
			return;
		}

		// Get selector values
		$xPathItem = Minz_Request::paramString('xPathItem');
		$xPathItemTitle = Minz_Request::paramString('xPathItemTitle');
		$xPathItemUri = Minz_Request::paramString('xPathItemUri');
		$xPathItemContent = Minz_Request::paramString('xPathItemContent');
		$xPathItemTimestamp = Minz_Request::paramString('xPathItemTimestamp');
		$xPathItemThumbnail = Minz_Request::paramString('xPathItemThumbnail');
		$jsonItem = Minz_Request::paramString('jsonItem');
		$jsonItemTitle = Minz_Request::paramString('jsonItemTitle');
		$jsonItemUri = Minz_Request::paramString('jsonItemUri');
		$jsonItemContent = Minz_Request::paramString('jsonItemContent');
		$jsonItemTimestamp = Minz_Request::paramString('jsonItemTimestamp');

		// Validate selector lengths to prevent XPath injection DoS
		$all_selectors = [
			'xPathItem' => $xPathItem,
			'xPathItemTitle' => $xPathItemTitle,
			'xPathItemUri' => $xPathItemUri,
			'xPathItemContent' => $xPathItemContent,
			'xPathItemTimestamp' => $xPathItemTimestamp,
			'xPathItemThumbnail' => $xPathItemThumbnail,
			'jsonItem' => $jsonItem,
			'jsonItemTitle' => $jsonItemTitle,
			'jsonItemUri' => $jsonItemUri,
			'jsonItemContent' => $jsonItemContent,
			'jsonItemTimestamp' => $jsonItemTimestamp,
		];
		foreach ($all_selectors as $name => $value) {
			if (!empty($value) && !$this->validateSelector($value, $name)) {
				return;
			}
		}

		// Build selectors payload from posted form fields.
		$selectors = [
			'item'           => $xPathItem ?: $jsonItem,
			'item_title'     => $xPathItemTitle ?: $jsonItemTitle,
			'item_link'      => $xPathItemUri ?: $jsonItemUri,
			'item_content'   => $xPathItemContent ?: $jsonItemContent,
			'item_timestamp' => $xPathItemTimestamp ?: $jsonItemTimestamp,
			'item_thumbnail' => $xPathItemThumbnail,
			'item_author'    => '',
		];

		$result = $ext->sidecarRequest('/scrape/config', [
			'url'       => $feed_url,
			'strategy'  => $strategy ?: 'xpath',
			'selectors' => $selectors,
			'services'  => $ext->getServicesPayload(),
			'adaptive'  => true,
		], 'POST', 30);

		if (!$result['ok']) {
			Minz_Request::bad(_t('ext.autofeed.feed_add_error') . ': ' . $result['error']);
			Minz_Request::forward(['c' => 'AutoFeed', 'a' => 'discover'], true);
			return;
		}

		$atom_feed_url = $result['data']['feed_url'] ?? '';
		if (empty($atom_feed_url)) {
			Minz_Request::bad(_t('ext.autofeed.feed_add_error') . ': no feed_url returned');
			Minz_Request::forward(['c' => 'AutoFeed', 'a' => 'discover'], true);
			return;
		}

		$default_ttl = $ext->getDefaultTTL();

		try {
			$feed = new FreshRSS_Feed($atom_feed_url, false);
			$feed->_name($feed_name);
			if ($category > 0) {
				$feed->_categoryId($category);
			}
			$feed->_ttl($default_ttl);

			$feedDAO = FreshRSS_Factory::createFeedDao();
			$res = $feedDAO->addFeedObject($feed);
			if ($res !== false) {
				Minz_Request::good(sprintf(_t('ext.autofeed.feed_added'), $feed_name));
			} else {
				Minz_Request::bad(_t('ext.autofeed.feed_add_failed'));
			}
		} catch (Exception $e) {
			Minz_Request::bad(_t('ext.autofeed.feed_add_error') . ': ' . $e->getMessage());
		}

		Minz_Request::forward(['c' => 'subscription', 'a' => 'index'], true);
	}

	/**
	 * POST — Apply a discovered feed configuration.
	 *
	 * Creates a new feed subscription in FreshRSS based on the strategy
	 * and config posted from the results page.
	 */
	public function applyAction(): void {
		if (!$this->requireCsrfPost()) {
			return;
		}

		$strategy  = Minz_Request::paramString('strategy');
		$feed_url  = trim(Minz_Request::paramString('feed_url'));
		// Falls back to feed_url when page title is empty so FreshRSS never receives a blank name.
		$feed_name = trim(Minz_Request::paramString('feed_name')) ?: $feed_url;
		$category  = Minz_Request::paramInt('category');

		if (empty($feed_url)) {
			Minz_Request::bad(_t('ext.autofeed.error_no_feed_url'));
			Minz_Request::forward(['c' => 'AutoFeed', 'a' => 'discover'], true);
			return;
		}

		$ext = Minz_ExtensionManager::findExtension('AutoFeed');
		$default_ttl = $ext ? $ext->getDefaultTTL() : 86400;

		try {
			$feed = new FreshRSS_Feed($feed_url, false);
			$feed->_name($feed_name);

			if ($category > 0) {
				$feed->_categoryId($category);
			}

			// Set the feed type and scraping configuration based on strategy.
			switch ($strategy) {
				case 'rss':
					// Standard RSS/Atom — no special config needed.
					break;

				case 'json_api':
				case 'json_dot_notation':
					$feed->_kind(FreshRSS_Feed::KIND_JSON_DOTNOTATION);
					$feed->_attribute('xpath', [
						'item'          => Minz_Request::paramString('jsonItem'),
						'itemTitle'     => Minz_Request::paramString('jsonItemTitle'),
						'itemContent'   => Minz_Request::paramString('jsonItemContent'),
						'itemUri'       => Minz_Request::paramString('jsonItemUri'),
						'itemTimestamp' => Minz_Request::paramString('jsonItemTimestamp'),
					]);
					break;

				case 'xpath':
			$feed->_kind(FreshRSS_Feed::KIND_HTML_XPATH);
			$feed->_attribute('xpath', [
				'item'          => Minz_Request::paramString('xPathItem'),
				'itemTitle'     => Minz_Request::paramString('xPathItemTitle'),
				'itemContent'   => Minz_Request::paramString('xPathItemContent'),
				'itemUri'       => Minz_Request::paramString('xPathItemUri'),
				'itemTimestamp' => Minz_Request::paramString('xPathItemTimestamp'),
				'itemThumbnail' => Minz_Request::paramString('xPathItemThumbnail'),
			]);
					break;

				case 'embedded_json':
					$feed->_kind(FreshRSS_Feed::KIND_HTML_XPATH_JSON_DOTNOTATION);
					$xpathToJson = Minz_Request::paramString('xPathToJson');
					$feed->_attribute('xpath', [
						'item'          => Minz_Request::paramString('jsonItem'),
						'itemTitle'     => Minz_Request::paramString('jsonItemTitle'),
						'itemContent'   => Minz_Request::paramString('jsonItemContent'),
						'itemUri'       => Minz_Request::paramString('jsonItemUri'),
						'itemTimestamp' => Minz_Request::paramString('jsonItemTimestamp'),
					]);
					if ($xpathToJson) {
						$feed->_attribute('xPathToJson', $xpathToJson);
					}
					break;

				default:
					// Fallback: treat as standard RSS.
					break;
			}

			// Set refresh TTL.
			$feed->_ttl($default_ttl);

			// Persist the feed.
			$feedDAO = FreshRSS_Factory::createFeedDao();
			$result = $feedDAO->addFeedObject($feed);

			if ($result !== false) {
				Minz_Request::good(sprintf(_t('ext.autofeed.feed_added'), $feed_name));
			} else {
				Minz_Request::bad(_t('ext.autofeed.feed_add_failed'));
			}
		} catch (Exception $e) {
			Minz_Request::bad(_t('ext.autofeed.feed_add_error') . ': ' . $e->getMessage());
		}

		Minz_Request::forward(['c' => 'subscription', 'a' => 'index'], true);
	}

	/**
		* POST — Preview a candidate's scrape results. Returns HTML fragment.
		*/
	public function previewAction(): void {
		// No CSRF needed for preview - it's a read-only fetch that doesn't modify state
		$ext = Minz_ExtensionManager::findExtension('AutoFeed');
		if ($ext === null) {
			http_response_code(500);
			echo '<div class="alert alert-error">AutoFeed extension not found.</div>';
			return;
		}

		$url = Minz_Request::paramString('url');
		$strategy = Minz_Request::paramString('strategy');
		$timeout = Minz_Request::paramInt('timeout') ?: 30;

		if (empty($url) || empty($strategy)) {
			http_response_code(400);
			echo '<div class="alert alert-error">Missing url or strategy.</div>';
			return;
		}

		// Build selectors based on strategy
		$selectors = [];
		switch ($strategy) {
			case 'rss':
				// RSS doesn't need selectors
				break;
			case 'json_api':
			case 'json_dot_notation':
				$selectors = [
					'item' => Minz_Request::paramString('jsonItem') ?: 'items',
					'title' => Minz_Request::paramString('jsonItemTitle') ?: 'title',
					'link' => Minz_Request::paramString('jsonItemUri') ?: 'url',
					'content' => Minz_Request::paramString('jsonItemContent') ?: 'content|body',
					'timestamp' => Minz_Request::paramString('jsonItemTimestamp') ?: 'published|date',
				];
				break;
			case 'xpath':
				$selectors = [
					'item' => Minz_Request::paramString('xPathItem') ?: '//article',
					'title' => Minz_Request::paramString('xPathItemTitle') ?: './/text()',
					'link' => Minz_Request::paramString('xPathItemUri') ?: './/a/@href',
					'content' => Minz_Request::paramString('xPathItemContent') ?: './/text()',
					'timestamp' => Minz_Request::paramString('xPathItemTimestamp') ?: './/time/@datetime',
					'thumbnail' => Minz_Request::paramString('xPathItemThumbnail') ?: './/img/@src',
				];
				break;
			case 'embedded_json':
				$selectors = [
					'item' => Minz_Request::paramString('jsonItem') ?: 'data',
					'title' => Minz_Request::paramString('jsonItemTitle') ?: 'title',
					'link' => Minz_Request::paramString('jsonItemUri') ?: 'url',
					'content' => Minz_Request::paramString('jsonItemContent') ?: 'content|body',
					'timestamp' => Minz_Request::paramString('jsonItemTimestamp') ?: 'published|date',
				];
				$xpathToJson = Minz_Request::paramString('xPathToJson');
				if ($xpathToJson) {
					$selectors['xpath_to_json'] = $xpathToJson;
				}
				break;
		}

		// Build request payload
		$payload = [
			'url' => $url,
			'strategy' => $strategy,
			'selectors' => $selectors,
			'timeout' => $timeout,
			'services' => $ext->getServicesPayload(),
		];

		$result = $ext->sidecarRequest('/preview', $payload, 'POST', $timeout + 10);

		if (!$result['ok']) {
			http_response_code(500);
			$error = $result['error'] ?? 'Unknown error';
			echo '<div class="alert alert-error">Preview failed: ' . htmlspecialchars($error) . '</div>';
			return;
		}

		$data = $result['data'] ?? [];
		$items = $data['items'] ?? [];
		$field_counts = $data['field_counts'] ?? [];
		$errors = $data['errors'] ?? [];

		// Render preview fragment
		header('Content-Type: text/html; charset=utf-8');

		if (!empty($errors)) {
			echo '<div class="alert alert-warning">';
			foreach ($errors as $err) {
				echo '<div>' . htmlspecialchars($err) . '</div>';
			}
			echo '</div>';
		}

		if (empty($items)) {
			echo '<p class="help">' . _t('ext.autofeed.preview_no_items') . '</p>';
			return;
		}

		$total = count($items);
		$title_count = $field_counts['title'] ?? 0;
		$link_count = $field_counts['link'] ?? 0;
		$timestamp_count = $field_counts['timestamp'] ?? 0;

		echo '<div class="autofeed-preview-stats">';
		echo htmlspecialchars("$total items · T=$title_count/$total U=$link_count/$total D=$timestamp_count/$total");
		echo '</div>';

		echo '<table class="autofeed-preview-table">';
		echo '<thead><tr><th>#</th><th>Title</th><th>Link</th><th>Timestamp</th></tr></thead>';
		echo '<tbody>';

		foreach ($items as $i => $item) {
			$title = htmlspecialchars(mb_substr($item['title'] ?? '(untitled)', 0, 60));
			if (strlen($item['title'] ?? '') > 60) {
				$title .= '…';
			}
			$link = htmlspecialchars($item['link'] ?? '');
			$timestamp = htmlspecialchars($item['timestamp'] ?? '-');

			echo '<tr>';
			echo '<td>' . ($i + 1) . '</td>';
			echo '<td>' . $title . '</td>';
			echo '<td>' . ($link ? '<a href="' . $link . '" target="_blank" rel="noopener">link</a>' : '-') . '</td>';
			echo '<td>' . $timestamp . '</td>';
			echo '</tr>';
		}

		echo '</tbody></table>';
	}
}
