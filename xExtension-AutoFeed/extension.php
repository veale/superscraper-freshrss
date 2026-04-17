<?php

/**
 * AutoFeed Discovery extension for FreshRSS.
 *
 * Provides automatic feed source discovery from any URL using a Python
 * sidecar service that performs RSS autodiscovery, embedded-JSON detection,
 * API endpoint extraction, and heuristic XPath generation.
 */
final class AutoFeedExtension extends Minz_Extension {

	/** @var string Default sidecar URL when running in Docker Compose. */
	private const DEFAULT_SIDECAR_URL = 'http://autofeed-sidecar:8000';

	/** @var int Default refresh interval for discovered feeds (24 h). */
	private const DEFAULT_TTL = 86400;

	// ─── Lifecycle ───────────────────────────────────────────────────────

	#[\Override]
	public function init(): void {
		parent::init();

		$this->registerController('AutoFeed');
		$this->registerViews();
		$this->registerTranslates();

		$this->registerHook(Minz_HookType::MenuOtherEntry, [$this, 'hookMenuEntry']);

		Minz_View::appendStyle($this->getFileUrl('autofeed.css', true));
	}

	// ─── Hooks ───────────────────────────────────────────────────────────

	/**
	 * Add an "Auto-Discover Feed" link to the header dropdown menu.
	 */
	public function hookMenuEntry(): string {
		$url = Minz_Url::display(['c' => 'AutoFeed', 'a' => 'discover']);
		$label = _t('ext.autofeed.menu_discover');
		$active = Minz_Request::controllerName() === 'AutoFeed' ? ' active' : '';
		return '<li class="item' . $active . '">'
			. '<a href="' . $url . '">' . $label . '</a>'
			. '</li>';
	}

	// ─── Configuration ───────────────────────────────────────────────────

	#[\Override]
	public function handleConfigureAction(): void {
		if (Minz_Request::isPost()) {
			$sidecar_url = trim(Minz_Request::paramString('sidecar_url'));
			$default_ttl = Minz_Request::paramInt('default_ttl');
			$llm_endpoint = trim(Minz_Request::paramString('llm_endpoint'));
			$llm_api_key = trim(Minz_Request::paramString('llm_api_key'));
			$llm_model = trim(Minz_Request::paramString('llm_model'));
			$rss_bridge_url = trim(Minz_Request::paramString('rss_bridge_url'));

			if (empty($sidecar_url)) {
				$sidecar_url = self::DEFAULT_SIDECAR_URL;
			}
			if ($default_ttl < 60) {
				$default_ttl = self::DEFAULT_TTL;
			}

			$this->setUserConfigurationValue('sidecar_url', $sidecar_url);
			$this->setUserConfigurationValue('default_ttl', $default_ttl);
			$this->setUserConfigurationValue('llm_endpoint', $llm_endpoint);
			$this->setUserConfigurationValue('llm_api_key', $llm_api_key);
			$this->setUserConfigurationValue('llm_model', $llm_model);
			$this->setUserConfigurationValue('rss_bridge_url', $rss_bridge_url);
		}
	}

	// ─── Helpers (accessible from controllers / views) ───────────────────

	/**
	 * Return the configured sidecar base URL (no trailing slash).
	 */
	public function getSidecarUrl(): string {
		return rtrim(
			$this->getUserConfigurationString('sidecar_url') ?: self::DEFAULT_SIDECAR_URL,
			'/'
		);
	}

	public function getDefaultTTL(): int {
		return $this->getUserConfigurationValue('default_ttl') ?: self::DEFAULT_TTL;
	}

	/**
	 * Make a JSON request to the sidecar service.
	 *
	 * @param  string               $path    API path, e.g. "/discover".
	 * @param  array<string,mixed>  $body    Request body (will be JSON-encoded).
	 * @param  string               $method  HTTP method.
	 * @param  int                  $timeout Timeout in seconds.
	 * @return array{ok: bool, status: int, data: mixed, error: string}
	 */
	public function sidecarRequest(
		string $path,
		array $body = [],
		string $method = 'POST',
		int $timeout = 60
	): array {
		$url = $this->getSidecarUrl() . $path;

		$ch = curl_init();
		curl_setopt_array($ch, [
			CURLOPT_URL            => $url,
			CURLOPT_RETURNTRANSFER => true,
			CURLOPT_TIMEOUT        => $timeout,
			CURLOPT_CONNECTTIMEOUT => 5,
			CURLOPT_HTTPHEADER     => [
				'Content-Type: application/json',
				'Accept: application/json',
			],
		]);

		if ($method === 'POST') {
			curl_setopt($ch, CURLOPT_POST, true);
			curl_setopt($ch, CURLOPT_POSTFIELDS, json_encode($body));
		}

		$response = curl_exec($ch);
		$httpCode = (int) curl_getinfo($ch, CURLINFO_HTTP_CODE);
		$error    = curl_error($ch);
		curl_close($ch);

		if ($response === false || $httpCode === 0) {
			return [
				'ok'     => false,
				'status' => 0,
				'data'   => null,
				'error'  => $error ?: 'Could not connect to sidecar at ' . $url,
			];
		}

		$data = json_decode($response, true);

		return [
			'ok'     => $httpCode >= 200 && $httpCode < 300,
			'status' => $httpCode,
			'data'   => $data,
			'error'  => $httpCode >= 400 ? ('Sidecar returned HTTP ' . $httpCode) : '',
		];
	}
}
