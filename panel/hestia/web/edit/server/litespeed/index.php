<?php
// OpenLiteSpeed (olsbridge): switch the sites between OpenLiteSpeed and
// Apache/nginx/PHP-FPM, restart it and its PHP. Installed by olsbridge.

$TAB = "SERVER";

// Main include
include $_SERVER["DOCUMENT_ROOT"] . "/inc/main.php";

// Check user
if ($_SESSION["userContext"] != "admin") {
	header("Location: /list/user/");
	exit();
}

if (!empty($_POST["action"])) {
	verify_csrf($_POST);
	$action = $_POST["action"];
	if ($action == "mode" && in_array($_POST["v_mode"] ?? "", ["replace", "offset", "off"], true)) {
		exec(
			HESTIA_CMD . "v-change-sys-web-litespeed " . quoteshellarg($_POST["v_mode"]),
			$output,
			$return_var,
		);
		check_return_code($return_var, $output);
		unset($output);
	} elseif ($action == "restart") {
		exec(HESTIA_CMD . "v-restart-sys-litespeed", $output, $return_var);
		check_return_code($return_var, $output);
		unset($output);
	} elseif (
		$action == "php_restart" &&
		preg_match('/^([0-9]+\.[0-9]+)?$/', $_POST["v_version"] ?? "x")
	) {
		exec(
			HESTIA_CMD . "v-restart-sys-litespeed-php " . quoteshellarg($_POST["v_version"]),
			$output,
			$return_var,
		);
		check_return_code($return_var, $output);
		unset($output);
	}
	if (empty($_SESSION["error_msg"])) {
		$_SESSION["ok_msg"] = _("Changes have been saved.");
	}
}

exec(HESTIA_CMD . "v-list-sys-litespeed json", $output, $return_var);
$olsb = json_decode(implode("", $output), true);
unset($output);
if (!is_array($olsb)) {
	$olsb = [
		"mode" => "off",
		"label" => _("unavailable"),
		"running" => false,
		"version" => "",
		"units" => [],
		"php" => [],
		"warnings" => [],
		"port_offset" => "",
	];
}

// Render page
render_page($user, $TAB, "edit_server_litespeed");

// Flush session messages
unset($_SESSION["error_msg"]);
unset($_SESSION["ok_msg"]);
