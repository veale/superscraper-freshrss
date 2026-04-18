<?php
/**
 * Verify that every _t('ext.autofeed.XXX') call in extension.php,
 * configure.phtml, and views/**.phtml has a matching key in
 * i18n/en/ext.php.
 *
 * Usage (from repo root):
 *   php xExtension-AutoFeed/tests/i18n_check.php
 */

$root = dirname(__DIR__);

// Collect all files to scan
$files_to_scan = array_merge(
    glob($root . '/*.php') ?: [],
    glob($root . '/*.phtml') ?: [],
    glob($root . '/Controllers/*.php') ?: [],
    glob($root . '/views/**/*.phtml') ?: [],
    glob($root . '/views/*/*.phtml') ?: []
);
$files_to_scan = array_unique($files_to_scan);

$used_keys = [];
foreach ($files_to_scan as $f) {
    $content = file_get_contents($f);
    if (preg_match_all("/_t\(\s*['\"]ext\.autofeed\.([a-z_]+)['\"]/", $content, $m)) {
        foreach ($m[1] as $k) {
            $used_keys[$k] = basename($f);
        }
    }
}

// Load defined keys
$translations = require $root . '/i18n/en/ext.php';
$defined_keys = $translations['ext']['autofeed'] ?? [];

if (empty($defined_keys)) {
    echo "ERROR: i18n/en/ext.php loaded but ['ext']['autofeed'] is empty. Check array structure.\n";
    exit(2);
}

$missing = array_diff(array_keys($used_keys), array_keys($defined_keys));
$unused  = array_diff(array_keys($defined_keys), array_keys($used_keys));

if ($missing) {
    echo "MISSING translation keys (used in code but not defined in i18n/en/ext.php):\n";
    foreach ($missing as $k) {
        echo "  - ext.autofeed.{$k}  (in {$used_keys[$k]})\n";
    }
    echo "\n";
}

if ($unused) {
    echo "UNUSED translation keys (defined in i18n/en/ext.php but not called anywhere):\n";
    foreach ($unused as $k) {
        echo "  - ext.autofeed.{$k}\n";
    }
    echo "\n";
}

if (!$missing && !$unused) {
    echo "OK — all " . count($used_keys) . " translation keys used are defined, nothing unused.\n";
    exit(0);
}

echo "Found " . count($missing) . " missing, " . count($unused) . " unused.\n";
exit($missing ? 1 : 0);
