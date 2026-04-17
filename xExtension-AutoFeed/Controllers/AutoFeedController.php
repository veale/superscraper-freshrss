<?php

/**
 * Controller for the AutoFeed Discovery extension.
 *
 * Registered as the "AutoFeed" controller, its actions are accessible at
 * URLs like  ?c=AutoFeed&a=discover  (via Minz routing).
 */
class FreshExtension_AutoFeed_Controller extends Minz_ActionController {

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
		$http_timeout = $use_browser ? 90 : 60;

		$result = $ext->sidecarRequest('/discover', [
			'url'         => $url,
			'timeout'     => $use_browser ? 45 : 30,
			'use_browser' => $use_browser,
		], 'POST', $http_timeout);

		$this->view->target_url = $url;
		$this->view->sidecar_ok = $result['ok'];
		$this->view->sidecar_error = $result['error'];
		$this->view->discovery = $result['data'] ?? [];
	}

	/**
	 * POST — Call sidecar /analyze with LLM, re-render results with recommendation.
	 */
	public function llmAnalyzeAction(): void {
		if (!Minz_Request::isPost()) {
			Minz_Request::forward(['c' => 'AutoFeed', 'a' => 'discover'], true);
			return;
		}

		$ext = Minz_ExtensionManager::findExtension('AutoFeed');
		if ($ext === null || !$ext->hasLlmConfigured()) {
			Minz_Request::bad(_t('ext.autofeed.llm_not_configured'));
			Minz_Request::forward(['c' => 'AutoFeed', 'a' => 'discover'], true);
			return;
		}

		$discovery_json_b64 = Minz_Request::paramString('discovery_json');
		$discovery = [];
		if ($discovery_json_b64 !== '') {
			$decoded = base64_decode($discovery_json_b64, true);
			if ($decoded !== false) {
				$discovery = json_decode($decoded, true) ?: [];
			}
		}

		$url = $discovery['url'] ?? $this->target_url ?? '';
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
	}

	/**
	 * POST — Generate an RSS-Bridge PHP script via the sidecar LLM.
	 */
	public function bridgeGenerateAction(): void {
		if (!Minz_Request::isPost()) {
			Minz_Request::forward(['c' => 'AutoFeed', 'a' => 'discover'], true);
			return;
		}

		$ext = Minz_ExtensionManager::findExtension('AutoFeed');
		if ($ext === null || !$ext->hasLlmConfigured()) {
			Minz_Request::bad(_t('ext.autofeed.llm_not_configured'));
			Minz_Request::forward(['c' => 'AutoFeed', 'a' => 'discover'], true);
			return;
		}

		$discovery_json_b64 = Minz_Request::paramString('discovery_json');
		$discovery = [];
		if ($discovery_json_b64 !== '') {
			$decoded = base64_decode($discovery_json_b64, true);
			if ($decoded !== false) {
				$discovery = json_decode($decoded, true) ?: [];
			}
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
		if (!Minz_Request::isPost()) {
			Minz_Request::forward(['c' => 'AutoFeed', 'a' => 'discover'], true);
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
	 * POST — Apply a discovered feed configuration.
	 *
	 * Creates a new feed subscription in FreshRSS based on the strategy
	 * and config posted from the results page.
	 */
	public function applyAction(): void {
		if (!Minz_Request::isPost()) {
			Minz_Request::forward(['c' => 'AutoFeed', 'a' => 'discover'], true);
			return;
		}

		$strategy  = Minz_Request::paramString('strategy');
		$feed_url  = trim(Minz_Request::paramString('feed_url'));
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
			$feed = new FreshRSS_Feed($feed_url);
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
				Minz_Request::good(_t('ext.autofeed.feed_added', $feed_name));
			} else {
				Minz_Request::bad(_t('ext.autofeed.feed_add_failed'));
			}
		} catch (Exception $e) {
			Minz_Request::bad(_t('ext.autofeed.feed_add_error') . ': ' . $e->getMessage());
		}

		Minz_Request::forward(['c' => 'subscription', 'a' => 'index'], true);
	}
}
