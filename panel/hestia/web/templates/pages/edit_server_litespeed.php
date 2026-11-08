<!-- OpenLiteSpeed (olsbridge). Installed by olsbridge. -->
<!-- Begin toolbar -->
<div class="toolbar">
	<div class="toolbar-inner">
		<div class="toolbar-buttons">
			<a class="button button-secondary button-back js-button-back" href="/edit/server/">
				<i class="fas fa-arrow-left icon-blue"></i><?= tohtml( _("Back")) ?>
			</a>
		</div>
		<div class="toolbar-buttons">
			<?php if ($olsb["mode"] != "off") { ?>
				<form method="post" class="u-mr10">
					<input type="hidden" name="token" value="<?= tohtml($_SESSION["token"]) ?>">
					<input type="hidden" name="action" value="restart">
					<button type="submit" class="button button-secondary">
						<i class="fas fa-arrow-rotate-left icon-green"></i><?= tohtml( _("Restart")) ?>
					</button>
				</form>
			<?php } ?>
			<button type="submit" class="button" form="olsb-options">
				<i class="fas fa-floppy-disk icon-purple"></i><?= tohtml( _("Save")) ?>
			</button>
		</div>
	</div>
</div>
<!-- End toolbar -->

<div class="container">
	<div class="form-container">
		<h1 class="u-mb20"><?= tohtml( _("Configure Server")) ?>: OpenLiteSpeed</h1>
		<?php show_alert_message($_SESSION); ?>
		<p class="u-mb10">
			<?= tohtml( _("Status")) ?>: <strong class="u-ml5"><?= tohtml($olsb["label"]) ?></strong>
			<?php if (!empty($olsb["version"])) { ?>
				<span class="u-ml10"><?= tohtml($olsb["version"]) ?></span>
			<?php } ?>
		</p>

		<?php if ($olsb["mode"] != "off") { ?>
			<!-- Live dashboard -->
			<details class="box-collapse u-mb10" open>
				<summary class="box-collapse-header">
					<i class="fas fa-gauge-high u-mr10"></i><?= tohtml( _("Live")) ?>
					<span class="u-ml10" id="olsb-updated"></span>
				</summary>
				<div class="box-collapse-content">
					<div class="units-table u-mb20">
						<div class="units-table-row">
							<div class="units-table-cell"><?= tohtml( _("Requests per second")) ?></div>
							<div class="units-table-cell"><strong id="olsb-rps">-</strong></div>
						</div>
						<div class="units-table-row">
							<div class="units-table-cell"><?= tohtml( _("Served from cache")) ?></div>
							<div class="units-table-cell"><strong id="olsb-cache">-</strong></div>
						</div>
						<div class="units-table-row">
							<div class="units-table-cell"><?= tohtml( _("Connections")) ?></div>
							<div class="units-table-cell"><strong id="olsb-conns">-</strong></div>
						</div>
						<div class="units-table-row">
							<div class="units-table-cell"><?= tohtml( _("Traffic out")) ?></div>
							<div class="units-table-cell"><strong id="olsb-bw">-</strong></div>
						</div>
						<div class="units-table-row">
							<div class="units-table-cell"><?= tohtml( _("Uptime")) ?></div>
							<div class="units-table-cell" id="olsb-uptime">-</div>
						</div>
						<div class="units-table-row">
							<div class="units-table-cell"><?= tohtml( _("Attacks in 24 h (WAF)")) ?></div>
							<div class="units-table-cell" id="olsb-waf">-</div>
						</div>
					</div>
					<p class="form-label u-mb10"><?= tohtml( _("Sites")) ?></p>
					<div class="units-table u-mb20" id="olsb-sites"></div>
					<p class="form-label u-mb10">PHP (lsphp)</p>
					<div class="units-table u-mb10" id="olsb-php"></div>
				</div>
			</details>
		<?php } ?>

		<form id="olsb-options" method="post">
			<input type="hidden" name="token" value="<?= tohtml($_SESSION["token"]) ?>">
			<input type="hidden" name="action" value="options">

			<!-- Performance -->
			<details class="box-collapse u-mb10" open>
				<summary class="box-collapse-header">
					<i class="fas fa-bolt u-mr10"></i><?= tohtml( _("Performance")) ?>
				</summary>
				<div class="box-collapse-content">
					<div class="u-mb20">
						<label for="v_cache" class="form-label">LSCache</label>
						<select class="form-select" name="v_cache" id="v_cache">
							<option value="on" <?= $olsb_options["cache"] == "on" ? "selected" : "" ?>><?= tohtml( _("On")) ?></option>
							<option value="off" <?= $olsb_options["cache"] == "off" ? "selected" : "" ?>><?= tohtml( _("Off")) ?></option>
						</select>
						<small class="form-text">
							<?= tohtml( _("Page cache in the web server. It stores only what the site asks for: install the LiteSpeed Cache plugin in WordPress (or the equivalent for other platforms) to use it. Sites that do not ask are not affected.")) ?>
						</small>
					</div>
					<div class="u-mb20">
						<label for="v_webp" class="form-label">WebP</label>
						<select class="form-select" name="v_webp" id="v_webp">
							<option value="on" <?= $olsb_options["webp"] == "on" ? "selected" : "" ?>><?= tohtml( _("On")) ?></option>
							<option value="off" <?= $olsb_options["webp"] == "off" ? "selected" : "" ?>><?= tohtml( _("Off")) ?></option>
						</select>
						<small class="form-text">
							<?= tohtml( _("PNG and JPEG images go out as WebP to the browsers that accept it, usually 70 to 95% smaller, with no change to the sites. A copy (image.png.webp) is made next to each image every 10 minutes, as the owner of the site.")) ?>
							<?php if (!empty($olsb_options["webp_stats"])) { ?>
								<br><?= tohtml($olsb_options["webp_stats"]) ?>
							<?php } ?>
						</small>
					</div>
					<div class="u-mb10">
						<label for="v_http3" class="form-label">HTTP/3 (QUIC)</label>
						<select class="form-select" name="v_http3" id="v_http3">
							<option value="on" <?= $olsb_options["http3"] == "on" ? "selected" : "" ?>><?= tohtml( _("On")) ?></option>
							<option value="off" <?= $olsb_options["http3"] == "off" ? "selected" : "" ?>><?= tohtml( _("Off")) ?></option>
						</select>
						<small class="form-text">
							<?= tohtml( _("Faster connections on mobile and unstable networks. Turning it on opens UDP port 443 in the firewall (rule HTTP3).")) ?>
						</small>
					</div>
				</div>
			</details>

			<!-- PHP and resources -->
			<details class="box-collapse u-mb10">
				<summary class="box-collapse-header">
					<i class="fas fa-microchip u-mr10"></i><?= tohtml( _("PHP and resources")) ?>
				</summary>
				<div class="box-collapse-content">
					<div class="u-mb20">
						<label for="v_php_timeout" class="form-label"><?= tohtml( _("PHP request time limit")) ?></label>
						<select class="form-select" name="v_php_timeout" id="v_php_timeout">
							<option value="30" <?= $olsb_options["php_timeout"] == "30" ? "selected" : "" ?>><?= tohtml( _("30 seconds")) ?></option>
							<option value="60" <?= $olsb_options["php_timeout"] == "60" ? "selected" : "" ?>><?= tohtml( _("60 seconds")) ?></option>
							<option value="120" <?= $olsb_options["php_timeout"] == "120" ? "selected" : "" ?>><?= tohtml( _("2 minutes")) ?></option>
							<option value="300" <?= $olsb_options["php_timeout"] == "300" ? "selected" : "" ?>><?= tohtml( _("5 minutes")) ?></option>
							<option value="600" <?= $olsb_options["php_timeout"] == "600" ? "selected" : "" ?>><?= tohtml( _("10 minutes")) ?></option>
						</select>
						<small class="form-text"><?= tohtml( _("Longest time, in real seconds, a PHP request may run before it is stopped (503), like request_terminate_timeout in PHP-FPM. It frees PHP processes held by stuck scripts, such as one waiting on an external API, which max_execution_time does not count. Raise it for long imports, backups or reports.")) ?></small>
					</div>
					<div class="u-mb20">
						<label for="v_php_idle" class="form-label"><?= tohtml( _("Idle PHP processes")) ?></label>
						<select class="form-select" name="v_php_idle" id="v_php_idle">
							<option value="300" <?= $olsb_options["php_idle"] == "300" ? "selected" : "" ?>><?= tohtml( _("End after 5 minutes idle")) ?></option>
							<option value="1800" <?= $olsb_options["php_idle"] == "1800" ? "selected" : "" ?>><?= tohtml( _("End after 30 minutes idle")) ?></option>
							<option value="3600" <?= $olsb_options["php_idle"] == "3600" ? "selected" : "" ?>><?= tohtml( _("End after 1 hour idle")) ?></option>
							<option value="off" <?= $olsb_options["php_idle"] == "off" ? "selected" : "" ?>><?= tohtml( _("Keep running")) ?></option>
						</select>
						<small class="form-text"><?= tohtml( _("Each site keeps a PHP process with its OPcache (about 50 MB). Ending it when the site is idle frees memory on servers with many sites; the next visit starts it again, a little slower.")) ?></small>
					</div>
					<div class="u-mb10">
						<label for="v_mem_cache" class="form-label"><?= tohtml( _("Static file memory cache")) ?></label>
						<select class="form-select" name="v_mem_cache" id="v_mem_cache">
							<option value="auto" <?= $olsb_options["mem_cache"] == "auto" ? "selected" : "" ?>><?= tohtml( _("Automatic (1/32 of the RAM)")) ?></option>
							<option value="64M" <?= $olsb_options["mem_cache"] == "64M" ? "selected" : "" ?>><?= tohtml( _("64 MB")) ?></option>
							<option value="256M" <?= $olsb_options["mem_cache"] == "256M" ? "selected" : "" ?>><?= tohtml( _("256 MB")) ?></option>
							<option value="512M" <?= $olsb_options["mem_cache"] == "512M" ? "selected" : "" ?>><?= tohtml( _("512 MB")) ?></option>
						</select>
						<small class="form-text"><?= tohtml( _("Small images, CSS and JavaScript are served from memory, already compressed.")) ?></small>
					</div>
				</div>
			</details>

			<!-- Resources per customer -->
			<details class="box-collapse u-mb10">
				<summary class="box-collapse-header">
					<i class="fas fa-scale-balanced u-mr10"></i><?= tohtml( _("Resources per customer")) ?>
				</summary>
				<div class="box-collapse-content">
					<p class="u-mb20">
						<?= tohtml( _("Each user's PHP runs in the user's own control group (cgroups v2, like CloudLinux LVE), together with the user's SSH and cron. One site cannot take the CPU, memory or disk of the others: when the server is busy, every user gets an equal share, and no user goes above the limits below. A package's CPU quota, memory limit and swap limit, when set (Resource limits in the server settings), win over these defaults for the users of that package.")) ?>
					</p>
					<?php if (!$olsb_options["limits_available"]) { ?>
						<p class="u-mb20"><strong><?= tohtml( _("Unavailable: this server does not have cgroups v2 with systemd.")) ?></strong></p>
					<?php } ?>
					<div class="u-mb20">
						<label for="v_limits" class="form-label"><?= tohtml( _("Limits per customer")) ?></label>
						<select class="form-select" name="v_limits" id="v_limits">
							<option value="on" <?= $olsb_options["limits"] == "on" ? "selected" : "" ?>><?= tohtml( _("On")) ?></option>
							<option value="off" <?= $olsb_options["limits"] == "off" ? "selected" : "" ?>><?= tohtml( _("Off")) ?></option>
						</select>
					</div>
					<?php
					$olsb_limit_fields = [
						"limit_cpu" => [_("CPU per user"), ["50%" => _("half a core"), "100%" => _("1 core"), "200%" => _("2 cores"), "400%" => _("4 cores")], _("Busy loops, image processing and heavy plugins of one user stop at this share.")],
						"limit_mem" => [_("Memory per user"), ["512M" => "512 MB", "1G" => "1 GB", "2G" => "2 GB", "4G" => "4 GB"], _("All PHP processes of the user together. Above it, the user's largest process is ended (the request gets an error); swap is limited to a quarter of it.")],
						"limit_tasks" => [_("Processes per user"), ["100" => "100", "200" => "200", "400" => "400", "1000" => "1000"], _("Processes and threads, against fork bombs. Each site uses up to pm.max_children PHP processes, plus one.")],
						"limit_io" => [_("Disk throughput per user"), ["10M" => "10 MB/s", "25M" => "25 MB/s", "50M" => "50 MB/s", "100M" => "100 MB/s"], _("Reads and writes on the disk of /home, each. Files already in memory do not count.")],
						"limit_iops" => [_("Disk operations per user"), ["500" => "500/s", "1000" => "1000/s", "2000" => "2000/s"], _("Reads and writes per second, each.")],
					];
					foreach ($olsb_limit_fields as $olsb_key => [$olsb_label, $olsb_values, $olsb_help]) { ?>
						<div class="u-mb20">
							<label for="v_<?= $olsb_key ?>" class="form-label"><?= tohtml($olsb_label) ?></label>
							<select class="form-select" name="v_<?= $olsb_key ?>" id="v_<?= $olsb_key ?>">
								<?php foreach ($olsb_values as $olsb_value => $olsb_text) { ?>
									<option value="<?= tohtml($olsb_value) ?>" <?= $olsb_options[$olsb_key] == $olsb_value ? "selected" : "" ?>><?= tohtml($olsb_text) ?></option>
								<?php } ?>
								<option value="off" <?= $olsb_options[$olsb_key] == "off" ? "selected" : "" ?>><?= tohtml( _("No limit")) ?></option>
							</select>
							<small class="form-text"><?= tohtml($olsb_help) ?></small>
						</div>
					<?php } ?>
					<?php if (!empty($olsb_limits)) { ?>
						<p class="form-label u-mb10"><?= tohtml( _("Users")) ?> <span class="u-ml10" id="olsb-users-updated"></span></p>
						<div class="units-table u-mb10" id="olsb-users">
							<?php foreach ($olsb_limits as $olsb_row) { ?>
								<div class="units-table-row">
									<div class="units-table-cell"><strong><?= tohtml($olsb_row["user"]) ?></strong> <?= tohtml($olsb_row["package"]) ?></div>
									<div class="units-table-cell">CPU <?= tohtml($olsb_row["cpu"] ?? "-") ?>, <?= tohtml( _("memory")) ?> <?= tohtml($olsb_row["mem"] ?? "-") ?></div>
									<div class="units-table-cell"><?= tohtml(round($olsb_row["memory"] / 1048576)) ?> MB</div>
									<div class="units-table-cell">OOM <?= tohtml($olsb_row["oom_kills"]) ?></div>
								</div>
							<?php } ?>
						</div>
						<small class="form-text"><?= tohtml( _("CPU now, CPU time held back by the limit since the user's group started, memory in use, and processes ended for lack of memory. Values with * come from the user's package.")) ?></small>
					<?php } ?>
				</div>
			</details>

			<!-- Security -->
			<details class="box-collapse u-mb10" open>
				<summary class="box-collapse-header">
					<i class="fas fa-shield-halved u-mr10"></i><?= tohtml( _("Security")) ?>
				</summary>
				<div class="box-collapse-content">
					<div class="u-mb20">
						<label for="v_protection" class="form-label"><?= tohtml( _("Protection")) ?></label>
						<select class="form-select" name="v_protection" id="v_protection">
							<option value="off" <?= $olsb_options["protection"] == "off" ? "selected" : "" ?>><?= tohtml( _("Off")) ?></option>
							<option value="standard" <?= $olsb_options["protection"] == "standard" ? "selected" : "" ?>><?= tohtml( _("Standard")) ?></option>
							<option value="strict" <?= $olsb_options["protection"] == "strict" ? "selected" : "" ?>><?= tohtml( _("Strict")) ?></option>
						</select>
						<small class="form-text">
							<?= tohtml( _("Standard: an IP with more than 300 simultaneous connections is blocked for 10 minutes; under attack (over 8000 connections) visitors answer a reCAPTCHA, search engines pass; 10 failed WordPress logins in 5 minutes block the IP for 15 minutes; scanners, repeat offenders and blocked logins are banned in the firewall for 1 hour. Strict: 100 connections, 30 PHP requests per second per IP, reCAPTCHA over 2500 connections, 5 failed logins.")) ?>
						</small>
					</div>
					<div class="u-mb10">
						<label for="v_waf" class="form-label"><?= tohtml( _("Web application firewall (WAF)")) ?></label>
						<select class="form-select" name="v_waf" id="v_waf">
							<option value="off" <?= $olsb_options["waf"] == "off" ? "selected" : "" ?>><?= tohtml( _("Off")) ?></option>
							<option value="detect" <?= $olsb_options["waf"] == "detect" ? "selected" : "" ?>><?= tohtml( _("Detect only (log)")) ?></option>
							<option value="block" <?= $olsb_options["waf"] == "block" ? "selected" : "" ?>><?= tohtml( _("Block")) ?></option>
						</select>
						<small class="form-text">
							<?= tohtml( _("ModSecurity with the OWASP Core Rule Set and the WordPress exclusions: blocks SQL injection, XSS, path traversal, command injection and known scanners. Start with Detect only for a few days, check the attacks below, then switch to Block. In Block mode, IPs with 5 blocked requests in 10 minutes are banned in the firewall for 1 hour.")) ?>
						</small>
					</div>
				</div>
			</details>
		</form>

		<?php if ($olsb["mode"] != "off") { ?>
			<!-- Blocked -->
			<details class="box-collapse u-mb10">
				<summary class="box-collapse-header">
					<i class="fas fa-ban u-mr10 icon-red"></i><?= tohtml( _("Blocked IPs")) ?>
					<span class="u-ml10" id="olsb-blocked-count"></span>
				</summary>
				<div class="box-collapse-content">
					<p class="form-label u-mb10"><?= tohtml( _("Firewall")) ?></p>
					<div class="units-table u-mb20" id="olsb-bans"></div>
					<p class="form-label u-mb10"><?= tohtml( _("Blocked by OpenLiteSpeed (until the ban period ends or a restart)")) ?></p>
					<p class="u-mb20 u-console" id="olsb-olsblocked">-</p>
					<p class="form-label u-mb10"><?= tohtml( _("WAF: most frequent rules and IPs in 24 h")) ?></p>
					<div class="units-table u-mb10" id="olsb-wafrules"></div>
				</div>
			</details>
		<?php } ?>

		<!-- Who serves the sites -->
		<details class="box-collapse u-mb10">
			<summary class="box-collapse-header">
				<i class="fas fa-server u-mr10"></i><?= tohtml( _("Who serves the sites")) ?>
			</summary>
			<div class="box-collapse-content">
				<form method="post">
					<input type="hidden" name="token" value="<?= tohtml($_SESSION["token"]) ?>">
					<input type="hidden" name="action" value="mode">
					<div class="form-check u-mb10">
						<input class="form-check-input" type="radio" name="v_mode" id="v_mode_off" value="off" <?= $olsb["mode"] == "off" ? "checked" : "" ?>>
						<label for="v_mode_off"><?= tohtml( _("Apache, nginx and PHP-FPM, as installed by the panel")) ?></label>
					</div>
					<div class="form-check u-mb10">
						<input class="form-check-input" type="radio" name="v_mode" id="v_mode_offset" value="offset" <?= $olsb["mode"] == "offset" ? "checked" : "" ?>>
						<label for="v_mode_offset"><?= tohtml( _("Test: OpenLiteSpeed beside them, on the same ports + 10000, to compare")) ?></label>
					</div>
					<div class="form-check u-mb10">
						<input class="form-check-input" type="radio" name="v_mode" id="v_mode_replace" value="replace" <?= $olsb["mode"] == "replace" ? "checked" : "" ?>>
						<label for="v_mode_replace"><?= tohtml( _("OpenLiteSpeed, with lsphp for each PHP version")) ?></label>
					</div>
					<p class="u-mb10">
						<?= tohtml( _("The panel keeps working as before: domains, templates, PHP versions, php.ini and Let's Encrypt are read from the Apache, nginx and PHP-FPM configuration. Switching takes a few seconds, with the sites down while the ports change hands; if OpenLiteSpeed does not answer on every port, the switch is undone.")) ?>
					</p>
					<button type="submit" class="button button-secondary"><?= tohtml( _("Apply")) ?></button>
				</form>
			</div>
		</details>

		<?php if (!empty($olsb["php"])) { ?>
			<details class="box-collapse u-mb10">
				<summary class="box-collapse-header"><i class="fab fa-php u-mr10"></i>PHP</summary>
				<div class="box-collapse-content">
					<div class="units-table">
						<?php foreach ($olsb["php"] as $php) { ?>
							<div class="units-table-row">
								<div class="units-table-cell">php-<?= tohtml($php["version"]) ?></div>
								<div class="units-table-cell">
									<?= tohtml($olsb["mode"] == "off" ? "php-fpm" : $php["served_by"]) ?>
									<?php if (!$php["lsphp"]) { ?>
										<span class="u-ml5">(<?= tohtml( _("lsphp not installed yet")) ?>)</span>
									<?php } ?>
								</div>
								<div class="units-table-cell">
									<?php if ($olsb["mode"] != "off" && $php["served_by"] == "lsphp") { ?>
										<form method="post">
											<input type="hidden" name="token" value="<?= tohtml($_SESSION["token"]) ?>">
											<input type="hidden" name="action" value="php_restart">
											<input type="hidden" name="v_version" value="<?= tohtml($php["version"]) ?>">
											<button type="submit" class="button button-secondary">
												<i class="fas fa-arrow-rotate-left icon-green"></i><?= tohtml( _("Restart PHP")) ?>
											</button>
										</form>
									<?php } ?>
								</div>
							</div>
						<?php } ?>
					</div>
				</div>
			</details>
		<?php } ?>

		<?php if (!empty($olsb["warnings"])) { ?>
			<details class="box-collapse u-mb10">
				<summary class="box-collapse-header">
					<i class="fas fa-triangle-exclamation u-mr10 icon-orange"></i><?= tohtml( _("Configuration not converted")) ?> (<?= count($olsb["warnings"]) ?>)
				</summary>
				<div class="box-collapse-content">
					<?php foreach ($olsb["warnings"] as $line) { ?>
						<p class="u-mb5 u-console"><?= tohtml($line) ?></p>
					<?php } ?>
				</div>
			</details>
		<?php } ?>
	</div>
</div>

<?php if ($olsb["mode"] != "off") { ?>
<form id="olsb-unban" method="post" class="u-hidden">
	<input type="hidden" name="token" value="<?= tohtml($_SESSION["token"]) ?>">
	<input type="hidden" name="action" value="unban">
	<input type="hidden" name="v_ip" id="olsb-unban-ip">
	<input type="hidden" name="v_chain" id="olsb-unban-chain">
</form>
<script>
(function () {
	const $ = (id) => document.getElementById(id);
	const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
	const num = (n) => Number(n).toLocaleString();
	const bytes = (b) => b > 1048576 ? (b / 1048576).toFixed(1) + " MB/s" : (b / 1024).toFixed(1) + " KB/s";
	const row = (cells) => '<div class="units-table-row">' + cells.map((c) => '<div class="units-table-cell">' + c + "</div>").join("") + "</div>";
	const labels = {
		reqs: <?= json_encode(_("requests")) ?>, cache: <?= json_encode(_("from cache")) ?>,
		inUse: <?= json_encode(_("in use")) ?>, waiting: <?= json_encode(_("waiting")) ?>,
		unban: <?= json_encode(_("Unban")) ?>, none: <?= json_encode(_("none")) ?>,
		events: <?= json_encode(_("events")) ?>, blocked: <?= json_encode(_("blocked")) ?>,
		updated: <?= json_encode(_("updated")) ?>,
		held: <?= json_encode(_("held back")) ?>, memory: <?= json_encode(_("memory")) ?>,
		tasks: <?= json_encode(_("processes")) ?>,
	};
	async function refresh() {
		let d;
		try {
			const r = await fetch("?stats=1", {credentials: "same-origin", cache: "no-store"});
			d = await r.json();
		} catch (e) { return; }
		if (!d.total) return;
		const t = d.total;
		$("olsb-rps").textContent = num(t.req_per_sec) + " (" + num(t.req_processing) + " " + labels.inUse + ")";
		$("olsb-cache").textContent = num(t.cache_hits_per_sec) + "/s, " + t.cache_ratio + "% " + labels.cache;
		$("olsb-conns").textContent = num(t.ssl_conns) + " HTTPS, " + num(t.conns) + " HTTP";
		$("olsb-bw").textContent = bytes(t.bps_out);
		$("olsb-uptime").textContent = t.uptime;
		$("olsb-waf").textContent = num(d.waf.events) + " " + labels.events + ", " + num(d.waf.blocked) + " " + labels.blocked;
		$("olsb-sites").innerHTML = d.sites.slice(0, 15).map((s) => row([esc(s.domain), num(s.req_per_sec) + " req/s", num(s.total_reqs) + " " + labels.reqs, num(s.cache_hits) + " " + labels.cache])).join("");
		$("olsb-php").innerHTML = d.php.map((p) => row([esc(p.name), num(p.in_use) + " " + labels.inUse, num(p.waiting) + " " + labels.waiting, num(p.req_per_sec) + " req/s"])).join("") || row([labels.none]);
		$("olsb-bans").innerHTML = d.firewall_bans.slice(0, 50).map((b) => row([esc(b.ip), esc(b.chain), esc(b.date),
			'<button type="button" class="button button-secondary" data-ip="' + esc(b.ip) + '" data-chain="' + esc(b.chain) + '">' + labels.unban + "</button>"])).join("") || row([labels.none]);
		$("olsb-olsblocked").textContent = d.ols_blocked.length ? d.ols_blocked.join(", ") : labels.none;
		$("olsb-blocked-count").textContent = "(" + (d.firewall_bans.length + d.ols_blocked.length) + ")";
		$("olsb-wafrules").innerHTML = d.waf.rules.map((r) => row([esc(r.name), num(r.count)])).concat(d.waf.ips.map((r) => row([esc(r.name), num(r.count)]))).join("") || row([labels.none]);
		$("olsb-updated").textContent = labels.updated + " " + new Date().toLocaleTimeString();
		// CPU per user: difference of the group's CPU time between two refreshes.
		if ($("olsb-users") && d.users) {
			const mark = (u, k) => esc(u[k] || "-") + (u.from && u.from[k] === "package" ? "*" : "");
			$("olsb-users").innerHTML = d.users.map((u) => {
				const prev = cpuPrev[u.uid];
				const pct = prev && d.time > prev.time ? Math.round((u.cpu_usec - prev.usec) / 10000 / (d.time - prev.time)) + "%" : "-";
				cpuPrev[u.uid] = {usec: u.cpu_usec, time: d.time};
				return row(["<strong>" + esc(u.user) + "</strong> " + esc(u.package || ""),
					"CPU " + pct + " / " + mark(u, "cpu") + ", " + labels.held + " " + num(Math.round(u.throttled_usec / 1e6)) + " s",
					labels.memory + " " + num(Math.round(u.memory / 1048576)) + " MB / " + mark(u, "mem"),
					num(u.tasks_now) + " / " + mark(u, "tasks") + " " + labels.tasks + ", OOM " + num(u.oom_kills)]);
			}).join("") || row([labels.none]);
			$("olsb-users-updated").textContent = labels.updated + " " + new Date().toLocaleTimeString();
		}
	}
	const cpuPrev = {};
	document.addEventListener("click", (e) => {
		const b = e.target.closest("button[data-ip]");
		if (!b) return;
		$("olsb-unban-ip").value = b.dataset.ip;
		$("olsb-unban-chain").value = b.dataset.chain;
		$("olsb-unban").submit();
	});
	refresh();
	setInterval(refresh, 5000);
})();
</script>
<?php } ?>
