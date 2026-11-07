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
			<button type="submit" class="button" form="main-form">
				<i class="fas fa-floppy-disk icon-purple"></i><?= tohtml( _("Save")) ?>
			</button>
		</div>
	</div>
</div>
<!-- End toolbar -->

<div class="container">

	<form id="main-form" name="v_olsbridge_mode" method="post">
		<input type="hidden" name="token" value="<?= tohtml($_SESSION["token"]) ?>">
		<input type="hidden" name="action" value="mode">

		<div class="form-container">
			<h1 class="u-mb20"><?= tohtml( _("Configure Server")) ?>: OpenLiteSpeed</h1>
			<?php show_alert_message($_SESSION); ?>

			<p class="u-mb10">
				<?= tohtml( _("Status")) ?>:
				<span class="u-ml5"><strong><?= tohtml($olsb["label"]) ?></strong></span>
			</p>
			<?php if (!empty($olsb["version"])) { ?>
				<p class="u-mb20"><?= tohtml( _("Version")) ?>: <span class="u-ml5"><?= tohtml($olsb["version"]) ?></span></p>
			<?php } ?>

			<div class="u-mb20">
				<p class="form-label u-mb10"><?= tohtml( _("Who serves the sites")) ?></p>
				<div class="form-check u-mb10">
					<input class="form-check-input" type="radio" name="v_mode" id="v_mode_off" value="off" <?= $olsb["mode"] == "off" ? "checked" : "" ?>>
					<label for="v_mode_off">
						<?= tohtml( _("Apache, nginx and PHP-FPM, as installed by the panel")) ?>
					</label>
				</div>
				<div class="form-check u-mb10">
					<input class="form-check-input" type="radio" name="v_mode" id="v_mode_offset" value="offset" <?= $olsb["mode"] == "offset" ? "checked" : "" ?>>
					<label for="v_mode_offset">
						<?= tohtml( _("Test: OpenLiteSpeed beside them, on the same ports + 10000, to compare")) ?>
					</label>
				</div>
				<div class="form-check">
					<input class="form-check-input" type="radio" name="v_mode" id="v_mode_replace" value="replace" <?= $olsb["mode"] == "replace" ? "checked" : "" ?>>
					<label for="v_mode_replace">
						<?= tohtml( _("OpenLiteSpeed, with lsphp for each PHP version")) ?>
					</label>
				</div>
			</div>

			<div class="alert alert-info u-mb20" role="alert">
				<i class="fas fa-info"></i>
				<p>
					<?= tohtml( _("The panel keeps working as before: domains, templates, PHP versions, php.ini and Let's Encrypt are read from the Apache, nginx and PHP-FPM configuration. With OpenLiteSpeed, those services stay active for the panel, but only reload OpenLiteSpeed. Switching takes a few seconds, and the sites are down while the ports change hands. If OpenLiteSpeed does not answer on every port, the switch is undone.")) ?>
				</p>
			</div>
		</div>
	</form>

	<div class="form-container">
		<?php if ($olsb["mode"] != "off") { ?>
			<div class="u-mb20">
				<form method="post" class="u-mb10">
					<input type="hidden" name="token" value="<?= tohtml($_SESSION["token"]) ?>">
					<input type="hidden" name="action" value="restart">
					<button type="submit" class="button button-secondary">
						<i class="fas fa-arrow-rotate-left icon-green"></i><?= tohtml( _("Restart OpenLiteSpeed")) ?>
					</button>
				</form>
				<?php if (!empty($olsb["units"])) { ?>
					<p><?= tohtml( _("Services answered by OpenLiteSpeed")) ?>: <?= tohtml(implode(", ", $olsb["units"])) ?></p>
				<?php } ?>
			</div>
		<?php } ?>

		<?php if (!empty($olsb["php"])) { ?>
			<p class="form-label u-mb10">PHP</p>
			<div class="units-table u-mb20">
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
