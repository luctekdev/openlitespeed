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
	}
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
