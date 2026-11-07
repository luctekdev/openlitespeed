<?php
// OpenLiteSpeed (olsbridge): who serves the sites, performance and security
// options, live statistics, blocked IPs and PHP restarts. Installed by olsbridge.

$TAB = "SERVER";

// Main include
include $_SERVER["DOCUMENT_ROOT"] . "/inc/main.php";

// Check user
if ($_SESSION["userContext"] != "admin") {
	header("Location: /list/user/");
	exit();
}

// Live statistics for the dashboard (read only).
if (!empty($_GET["stats"])) {
	exec(HESTIA_CMD . "v-list-sys-litespeed-stats json", $output, $return_var);
	header("Content-Type: application/json");
	header("Cache-Control: no-store");
	echo $return_var == 0 ? implode("", $output) : "{}";
	exit();
}

$options = [
	"cache" => ["on", "off"],
	"http3" => ["on", "off"],
	"protection" => ["off", "standard", "strict"],
	"waf" => ["off", "detect", "block"],
];

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
	} elseif ($action == "options") {
		// Only the options that changed, one command each.
		exec(HESTIA_CMD . "v-list-sys-litespeed json", $output, $return_var);
		$current = json_decode(implode("", $output), true)["options"] ?? [];
		unset($output);
		foreach ($options as $key => $allowed) {
			$value = $_POST["v_" . $key] ?? null;
			if ($value === null || !in_array($value, $allowed, true) || ($current[$key] ?? null) === $value) {
				continue;
			}
			exec(
				HESTIA_CMD . "v-change-sys-litespeed-option " . quoteshellarg($key) . " " . quoteshellarg($value),
				$output,
				$return_var,
			);
			check_return_code($return_var, $output);
			unset($output);
		}
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
	} elseif (
		$action == "unban" &&
		filter_var($_POST["v_ip"] ?? "", FILTER_VALIDATE_IP) &&
		preg_match('/^[A-Z0-9_-]+$/', $_POST["v_chain"] ?? "")
	) {
		exec(
			HESTIA_CMD . "v-delete-firewall-ban " . quoteshellarg($_POST["v_ip"]) . " " . quoteshellarg($_POST["v_chain"]),
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
		"options" => [],
	];
}
$olsb_options = $olsb["options"] + [
	"cache" => "on",
	"http3" => "off",
	"protection" => "standard",
	"waf" => "off",
	"waf_available" => false,
];

// Render page
render_page($user, $TAB, "edit_server_litespeed");

// Flush session messages
unset($_SESSION["error_msg"]);
unset($_SESSION["ok_msg"]);
