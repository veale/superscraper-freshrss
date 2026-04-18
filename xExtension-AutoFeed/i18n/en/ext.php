<?php

return [
	'ext' => [
		'autofeed' => [
			// Menu
			'menu_discover' => 'Auto-Discover Feed',

			// Settings
			'settings_title' => 'AutoFeed Discovery Settings',
			'sidecar_url' => 'Sidecar Service URL',
			'sidecar_url_help' => 'URL of the AutoFeed sidecar service. Default: http://autofeed-sidecar:8000',
			'test_connection' => 'Test Connection',
			'default_ttl' => 'Default Refresh Interval (seconds)',
			'default_ttl_help' => 'How often discovered feeds are refreshed. Default: 86400 (24 hours).',
			'llm_settings' => 'LLM Settings (optional)',
			'llm_settings_help' => 'Configure an OpenAI-compatible endpoint for LLM-assisted analysis. Leave blank to skip LLM features.',
			'llm_endpoint' => 'LLM API Endpoint',
			'llm_api_key' => 'LLM API Key',
			'llm_model' => 'LLM Model Name',
			'rss_bridge_settings' => 'RSS-Bridge (optional)',
			'rss_bridge_url' => 'RSS-Bridge URL',
			'rss_bridge_url_help' => 'URL of your RSS-Bridge instance for fallback bridge generation.',

			// Discovery page
			'page_title' => 'Auto-Discover Feed',
			'page_description' => 'Enter any URL and AutoFeed will try to discover the best way to create an RSS feed from it.',
			'url_label' => 'Website URL',
			'url_help' => 'The URL of the page you want to turn into a feed.',
			'discover_btn' => 'Discover',
			'advanced_discovery_label' => 'Use advanced discovery (browser-based, slower)',
			'advanced_discovery_help' => 'Enables Phase 2: loads the page in a headless browser to capture XHR/fetch API calls and JS-rendered content. Takes 5–20 seconds instead of &lt;5 seconds.',

			// Results page
			'results_title' => 'Discovery Results',
			'results_for' => 'Results for',
			'sidecar_error' => 'Sidecar error',
			'back' => 'Back',
			'no_results' => 'No feed sources could be discovered for this URL. The site may require JavaScript rendering (Phase 2) or an RSS-Bridge script.',
			'try_again' => 'Try another URL',
			'warnings' => 'Warnings',

			// Sections
			'section_rss' => 'RSS / Atom Feeds',
			'section_api' => 'JSON API Endpoints',
			'section_graphql' => 'GraphQL Operations',
			'section_embedded' => 'Embedded JSON Data',
			'section_xpath' => 'HTML Scraping (XPath)',

			// Card details
			'score' => 'Score',
			'confidence' => 'Confidence',
			'fields' => 'Fields',
			'items' => 'items',
			'source' => 'Source',
			'path' => 'Path',
			'configure_mapping' => 'Configure field mapping & subscribe',
			'category' => 'Category',
			'subscribe' => 'Subscribe',
			'subscribe_scraped' => 'Subscribe (adaptive scrape)',
			'embedded_note' => 'Embedded JSON is applied via HTML+XPath+JSON mode — FreshRSS will fetch the page and walk the configured JSON path on each refresh.',

			// Metadata
			'frameworks_detected' => 'Frameworks detected',
			'anti_bot_warning' => 'Anti-bot protection detected. This site may require stealth fetching (Phase 2) to scrape reliably.',

			// Feed creation
			'feed_added' => 'Feed added: %s',
			'feed_add_failed' => 'Failed to add feed. It may already exist.',
				'feed_add_error' => 'Error adding feed',
				'error_no_url' => 'Please enter a URL.',
				'error_no_feed_url' => 'No feed URL specified.',
				'error_csrf' => 'CSRF validation failed. Please try again.',

			// LLM analysis
			'llm_analyze_btn'    => 'Analyse with LLM',
			'llm_analyzing'      => 'Analysing…',
			'llm_recommendation' => 'LLM Recommendation',
			'llm_reasoning'      => 'Reasoning',
			'llm_caveats'        => 'Caveats',
			'llm_not_configured' => 'LLM is not configured. Add an endpoint in Settings.',
			'error_llm_timeout'  => 'LLM request timed out.',
			'error_llm_auth'     => 'LLM authentication failed. Check your API key.',
			'error_llm_generic'  => 'LLM error',

			// RSS-Bridge
			'bridge_generate_btn'   => 'Generate RSS-Bridge Script',
			'bridge_generating'     => 'Generating…',
			'bridge_generated_title' => 'Generated RSS-Bridge Script',
			'bridge_copy'           => 'Copy to clipboard',
			'bridge_copied'         => 'Copied!',
			'bridge_deploy_btn'     => 'Deploy to bridge directory',
			'bridge_deployed'       => 'Bridge deployed.',
			'bridge_subscribe'      => 'Subscribe via RSS-Bridge',
			'bridge_php_label'      => 'PHP source',
			'error_bridge_generic'  => 'Bridge error',

			// Auto-deploy setting
			'auto_deploy_bridges'         => 'Automatically deploy generated bridges',
			'auto_deploy_bridges_help'    => 'When enabled, the sidecar writes generated PHP files into the shared ./generated-bridges/ directory, making them immediately available to RSS-Bridge.',
			'auto_deploy_bridges_warning' => 'Warning: this allows the sidecar to write PHP files to disk. Only enable if you trust the LLM output or review files before restarting RSS-Bridge.',

			// External services (advanced)
			'external_services_title'    => 'External Services (advanced)',
			'external_services_help'     => 'Optional. Point AutoFeed at your own already-running Playwright, Browserless, Scrapling, or RSS-Bridge containers. Leave defaults for the bundled out-of-the-box experience.',
				'fetch_backend'              => 'Browser fetch backend',
				'fetch_backend_bundled'      => 'Bundled (in-process Playwright)',
				'fetch_backend_playwright'   => 'External Playwright server (WebSocket)',
				'fetch_backend_browserless'  => 'Browserless (CDP)',
				'fetch_backend_scrapling'    => 'Scrapling-serve (HTTP)',
				'services_auth_token'        => 'Bearer token (optional)',
				'playwright_server_url'      => 'Playwright Server WebSocket URL',
				'playwright_server_url_help' => 'WebSocket endpoint for a remote Playwright run-server (e.g. ws://playwright-server:3000/).',
				'browserless_url'            => 'Browserless CDP endpoint',
				'browserless_url_help'       => 'CDP WebSocket URL for your Browserless instance (append ?token=... if required).',
				'scrapling_serve_url'        => 'Scrapling-serve HTTP URL',
				'scrapling_serve_url_help'   => 'HTTP endpoint of scrapling-serve, used for stealth fetching and adaptive scraping.',
				'services_auth_token_help'   => 'Optional Bearer token added to requests sent to external services above.',
				'sidecar_auth_token'         => 'Sidecar inbound token (optional)',
				'sidecar_auth_token_help'    => 'When present, mutating requests send this token as Authorization: Bearer <token> to the sidecar and the sidecar validates it.',
	
				// RSS-Bridge delivery modes (Tier 3)
				'rss_bridge_deploy_mode'     => 'Bridge deployment mode',
				'rss_bridge_deploy_mode_auto' => 'Auto (try local first, then remote)',
				'rss_bridge_deploy_mode_local' => 'Local only (shared volume)',
				'rss_bridge_deploy_mode_remote' => 'Remote only (HTTP API)',
				'rss_bridge_deploy_mode_help' => 'Controls how generated RSS-Bridge PHP files are delivered. "Auto" tries the local shared volume first; "Remote only" forces HTTP API deployment.',
				'auto_deploy_remote_warning' => 'Warning: You have a remote RSS-Bridge URL configured but auto-deploy is set to "Auto". The bridge will be written to the local sidecar volume, not sent to your remote RSS-Bridge. Use "Remote only" mode or copy the PHP manually.',
	
				// SFTP deployment (Tier 3.3)
				'sftp_deploy_title'          => 'SFTP Deployment (optional)',
				'sftp_deploy_help'           => 'Deploy bridges via SFTP to a remote RSS-Bridge host you can SSH into.',
				'sftp_host'                  => 'SFTP Host',
				'sftp_port'                  => 'SFTP Port',
				'sftp_user'                  => 'SFTP Username',
				'sftp_key_path'              => 'SSH Private Key Path',
				'sftp_target_dir'            => 'Target Directory',
				'sftp_test'                  => 'Test SFTP Connection',
	
				// Preview
				'preview'                    => 'Preview',
				'preview_no_items'           => 'No items found with the current selectors. Try adjusting the XPath or JSON paths.',
			],
		],
	];
