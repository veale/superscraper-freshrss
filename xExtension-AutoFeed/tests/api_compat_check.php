<?php
/**
 * Run this INSIDE a FreshRSS container to see whether your Minz_Extension
 * build has the methods this extension relies on.
 *
 *   docker exec freshrss php /var/www/FreshRSS/extensions/xExtension-AutoFeed/tests/api_compat_check.php
 */

require '/var/www/FreshRSS/lib/Minz/Extension.php';

$required = [
    'getUserConfigurationValue',   // per-key read
    'getUserConfiguration',        // full-array read
    'setUserConfiguration',        // full-array write (batched)
    'registerHook',                // hook registration
    'registerController',
    'registerViews',
    'registerTranslates',
    'getFileUrl',
    'hasFile',
];

$methods = get_class_methods('Minz_Extension');

$missing = [];
foreach ($required as $m) {
    if (!in_array($m, $methods, true)) {
        $missing[] = $m;
    }
}

if ($missing === []) {
    echo "OK — Minz_Extension has all methods this extension relies on.\n";
    exit(0);
}

echo "MISSING methods on this FreshRSS's Minz_Extension:\n";
foreach ($missing as $m) {
    echo "  - {$m}\n";
}
echo "\nThis extension will not work correctly on this FreshRSS.\n";
echo "See AutoFeed README 'Compatibility' section for required FreshRSS version.\n";
exit(1);
