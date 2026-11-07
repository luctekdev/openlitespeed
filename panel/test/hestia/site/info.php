<?php
header('Content-Type: application/json');
$keys = ['REMOTE_ADDR', 'HTTPS', 'HTTP_HOST', 'REQUEST_URI', 'QUERY_STRING', 'SCRIPT_NAME',
         'SCRIPT_FILENAME', 'DOCUMENT_ROOT', 'PHP_AUTH_USER', 'PHP_AUTH_PW', 'HTTP_AUTHORIZATION',
         'REQUEST_METHOD', 'PATH_INFO'];
$out = [];
foreach ($keys as $k) {
    $out[$k] = isset($_SERVER[$k]) ? $_SERVER[$k] : null;
}
$out['user'] = posix_getpwuid(posix_geteuid())['name'];
$out['post'] = file_get_contents('php://input');
echo json_encode($out, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES), "\n";
