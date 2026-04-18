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
		Minz_View::appendScript($this->getFileUrl('autofeed.js', true));
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
			$sidecar_url = $sidecar_url === '' ? self::DEFAULT_SIDECAR_URL : $sidecar_url;
			$default_ttl_param = Minz_Request::paramString('default_ttl');
			if ($default_ttl_param === '') {
				$default_ttl = self::DEFAULT_TTL;
			} else {
				$default_ttl = (int) $default_ttl_param;
				if ($default_ttl < 60) {
					Minz_Request::bad('Default TTL must be at least 60 seconds.');
					return;
				}
			}

			$auto_deploy_bridges = (bool) Minz_Request::paramInt('auto_deploy_bridges');

			// LLM settings - handle masked API key to prevent overwriting with display value
			$llm_endpoint = trim(Minz_Request::paramString('llm_endpoint'));
			$llm_model = trim(Minz_Request::paramString('llm_model')) ?: 'gpt-4o-mini';
			$llm_api_key_submitted = Minz_Request::paramString('llm_api_key');

			// Only update API key if it's not the masked placeholder
			if (!$this->isMaskedApiKey($llm_api_key_submitted)) {
				$this->setUserConfigurationValue('llm_api_key', $llm_api_key_submitted);
			}

			$this->setUserConfigurationValue('sidecar_url', $sidecar_url);
			$this->setUserConfigurationValue('default_ttl', $default_ttl);
			$this->setUserConfigurationValue('llm_endpoint', $llm_endpoint);
			$this->setUserConfigurationValue('llm_model', $llm_model);
			$this->setUserConfigurationValue('rss_bridge_url', $rss_bridge_url);
			$this->setUserConfigurationValue('auto_deploy_bridges', $auto_deploy_bridges);
			$this->setUserConfigurationValue('fetch_backend', $fetch_backend ?: 'bundled');
			$this->setUserConfigurationValue('playwright_server_url', $playwright_server_url);
			$this->setUserConfigurationValue('browserless_url', $browserless_url);
			$this->setUserConfigurationValue('scrapling_serve_url', $scrapling_serve_url);
			$this->setUserConfigurationValue('services_auth_token', $services_auth_token);
			$this->setUserConfigurationValue('sidecar_auth_token', $sidecar_auth_token);
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

	public function getLlmEndpoint(): string {
		return $this->getUserConfigurationString('llm_endpoint') ?: '';
	}

	public function getLlmApiKey(): string {
		return $this->getUserConfigurationString('llm_api_key') ?: '';
	}

	/**
	 * Return a masked version of the LLM API key for display.
	 * Shows only first 4 + "…" + last 4 characters.
	 */
	public function getLlmApiKeyMasked(): string {
		$key = $this->getLlmApiKey();
		if (strlen($key) <= 12) {
			return $key ? '…' . substr($key, -4) : '';
		}
		return substr($key, 0, 4) . '…' . substr($key, -4);
	}

	/**
	 * Check if a submitted value looks like the masked placeholder.
	 * Used to prevent overwriting the stored key with the masked display value.
	 */
	public function isMaskedApiKey(string $submitted): bool {
		$stored = $this->getLlmApiKey();
		if (empty($stored)) {
			return false;
		}
		$masked = $this->getLlmApiKeyMasked();
		return $submitted === $masked;
	}

	public function getLlmModel(): string {
		return $this->getUserConfigurationString('llm_model') ?: 'gpt-4o-mini';
	}

	public function getRssBridgeUrl(): string {
		return rtrim($this->getUserConfigurationString('rss_bridge_url') ?: '', '/');
	}

	public function getFetchBackend(): string {
		return $this->getUserConfigurationString('fetch_backend') ?: 'bundled';
	}

	public function getPlaywrightServerUrl(): string {
		return rtrim($this->getUserConfigurationString('playwright_server_url') ?: '', '/');
	}

	public function getBrowserlessUrl(): string {
		return rtrim($this->getUserConfigurationString('browserless_url') ?: '', '/');
	}

	public function getScraplingServeUrl(): string {
		return rtrim($this->getUserConfigurationString('scrapling_serve_url') ?: '', '/');
	}

	public function getServicesAuthToken(): string {
		return $this->getUserConfigurationString('services_auth_token') ?: '';
	}

	public function getSidecarAuthToken(): string {
		return $this->getUserConfigurationString('sidecar_auth_token') ?: '';
	}

	public function getAutoDeployBridges(): bool {
		return (bool) $this->getUserConfigurationValue('auto_deploy_bridges');
	}

	public function hasLlmConfigured(): bool {
		return !empty($this->getLlmEndpoint()) && !empty($this->getLlmModel());
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
		$headers = [
			'Content-Type: application/json',
			'Accept: application/json',
		];
		$auth = $this->getSidecarAuthToken();
		if ($auth !== '') {
			$headers[] = 'Authorization: Bearer ' . $auth;
		}
		curl_setopt_array($ch, [
			CURLOPT_URL            => $url,
			CURLOPT_RETURNTRANSFER => true,
			CURLOPT_TIMEOUT        => $timeout,
			CURLOPT_CONNECTTIMEOUT => 5,
			CURLOPT_HTTPHEADER     => $headers,
		]);

		$method = strtoupper($method);
		if ($method === 'GET') {
			// No body for GET requests.
		} else {
			curl_setopt($ch, CURLOPT_CUSTOMREQUEST, $method);
			if ($method !== 'DELETE') {
				curl_setopt($ch, CURLOPT_POSTFIELDS, json_encode($body));
			}
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

	public function getServicesPayload(): array {
		return [
			'fetch_backend'         => $this->getFetchBackend(),
			'playwright_server_url' => $this->getPlaywrightServerUrl(),
			'browserless_url'       => $this->getBrowserlessUrl(),
			'scrapling_serve_url'   => $this->getScraplingServeUrl(),
			'rss_bridge_url'        => $this->getRssBridgeUrl(),
			'auth_token'            => $this->getServicesAuthToken(),
		];
	}
}
