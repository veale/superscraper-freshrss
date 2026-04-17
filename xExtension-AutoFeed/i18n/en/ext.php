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
			'embedded_note' => 'Embedded JSON requires HTML+XPath+JSON mode. Full support coming in Phase 2.',

			// Metadata
			'frameworks_detected' => 'Frameworks detected',
			'anti_bot_warning' => 'Anti-bot protection detected. This site may require stealth fetching (Phase 2) to scrape reliably.',

			// Feed creation
			'feed_added' => 'Feed added: %s',
			'feed_add_failed' => 'Failed to add feed. It may already exist.',
			'feed_add_error' => 'Error adding feed',
			'error_no_url' => 'Please enter a URL.',
			'error_no_feed_url' => 'No feed URL specified.',
		],
	],
];
