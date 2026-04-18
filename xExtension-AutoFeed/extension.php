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

		$this->registerHook('menu_other_entry', [$this, 'hookMenuEntry']);

		// TEMPORARY DIAGNOSTIC — remove after translations confirmed working in production
		$test = _t('ext.autofeed.menu_discover');
		Minz_Log::notice("AutoFeed init: menu_discover translation = '{$test}'");

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
		parent::init();
		$this->registerTranslates();

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

			$rss_bridge_url = trim(Minz_Request::paramString('rss_bridge_url'));
			$rss_bridge_deploy_mode = Minz_Request::paramString('rss_bridge_deploy_mode') ?: 'auto';
			$fetch_backend = Minz_Request::paramString('fetch_backend');
			$playwright_server_url = trim(Minz_Request::paramString('playwright_server_url'));
			$browserless_url = trim(Minz_Request::paramString('browserless_url'));
			$scrapling_serve_url = trim(Minz_Request::paramString('scrapling_serve_url'));
			$services_auth_token = Minz_Request::paramString('services_auth_token');
			$sidecar_auth_token = Minz_Request::paramString('sidecar_auth_token');

			$sftp_host = trim(Minz_Request::paramString('sftp_host'));
			$sftp_port = Minz_Request::paramString('sftp_port') ?: '22';
			$sftp_user = trim(Minz_Request::paramString('sftp_user'));
			$sftp_key_path = trim(Minz_Request::paramString('sftp_key_path'));
			$sftp_target_dir = trim(Minz_Request::paramString('sftp_target_dir'));

			// Start from existing config so keys not in this form are not lost.
			$conf = $this->getUserConfiguration() ?? [];

			// Only update API key if it's not the masked placeholder
			if (!$this->isMaskedApiKey($llm_api_key_submitted)) {
				$conf['llm_api_key'] = $llm_api_key_submitted;
			}

			$conf['sidecar_url']            = $sidecar_url;
			$conf['default_ttl']            = $default_ttl;
			$conf['llm_endpoint']           = $llm_endpoint;
			$conf['llm_model']              = $llm_model;
			$conf['rss_bridge_url']         = $rss_bridge_url;
			$conf['rss_bridge_deploy_mode'] = $rss_bridge_deploy_mode;
			$conf['auto_deploy_bridges']    = $auto_deploy_bridges;
			$conf['fetch_backend']          = $fetch_backend ?: 'bundled';
			$conf['playwright_server_url']  = $playwright_server_url;
			$conf['browserless_url']        = $browserless_url;
			$conf['scrapling_serve_url']    = $scrapling_serve_url;
			$conf['services_auth_token']    = $services_auth_token;
			$conf['sidecar_auth_token']     = $sidecar_auth_token;
			$conf['sftp_host']              = $sftp_host;
			$conf['sftp_port']              = $sftp_port;
			$conf['sftp_user']              = $sftp_user;
			$conf['sftp_key_path']          = $sftp_key_path;
			$conf['sftp_target_dir']        = $sftp_target_dir;

			$this->setUserConfiguration($conf);

			Minz_Request::good(_t('feedback.conf.updated'));
		}
	}

	// ─── Helpers (accessible from controllers / views) ───────────────────

	/**
	 * Return the configured sidecar base URL (no trailing slash).
	 */
	public function getSidecarUrl(): string {
		return rtrim(
			$this->getUserConfigurationValue('sidecar_url') ?: self::DEFAULT_SIDECAR_URL,
			'/'
		);
	}

	public function getDefaultTTL(): int {
		return $this->getUserConfigurationValue('default_ttl') ?: self::DEFAULT_TTL;
	}

	public function getLlmEndpoint(): string {
		return $this->getUserConfigurationValue('llm_endpoint') ?: '';
	}

	public function getLlmApiKey(): string {
		return $this->getUserConfigurationValue('llm_api_key') ?: '';
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
		return $this->getUserConfigurationValue('llm_model') ?: 'gpt-4o-mini';
	}

	public function getRssBridgeUrl(): string {
		return rtrim($this->getUserConfigurationValue('rss_bridge_url') ?: '', '/');
	}

	public function getFetchBackend(): string {
		return $this->getUserConfigurationValue('fetch_backend') ?: 'bundled';
	}

	public function getPlaywrightServerUrl(): string {
		return rtrim($this->getUserConfigurationValue('playwright_server_url') ?: '', '/');
	}

	public function getBrowserlessUrl(): string {
		return rtrim($this->getUserConfigurationValue('browserless_url') ?: '', '/');
	}

	public function getScraplingServeUrl(): string {
		return rtrim($this->getUserConfigurationValue('scrapling_serve_url') ?: '', '/');
	}

	public function getServicesAuthToken(): string {
		return $this->getUserConfigurationValue('services_auth_token') ?: '';
	}

	public function getSidecarAuthToken(): string {
		return $this->getUserConfigurationValue('sidecar_auth_token') ?: '';
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
