// Pretty-prints the exact server-generated manifest for the selected
// effective assignment. A tag can have an override whose result differs
// completely from its global assignment, so the server always resolves it.
(function () {
	"use strict";

	document.addEventListener("DOMContentLoaded", function () {
		var btn = document.getElementById("manifest-preview-button");
		var panel = document.getElementById("manifest-preview-panel");
		var target = document.getElementById("manifest-target");
		if (!btn || !panel) return;

		var pre = panel.querySelector(".mh-manifest-json");
		function effectiveTarget() {
			return target ? target.value : (btn.dataset.defaultTarget || "");
		}

		btn.addEventListener("click", function () {
			if (!panel.hidden) {
				panel.hidden = true;
				return;
			}
			pre.textContent = "…";
			panel.hidden = false;
			var url = new URL(btn.dataset.url, window.location.origin);
			var espId = effectiveTarget();
			if (espId) url.searchParams.set("esp_id", espId);
			fetch(url).then(function (resp) {
				if (!resp.ok) throw new Error("manifest fetch failed");
				return resp.json();
			}).then(function (data) {
				pre.textContent = JSON.stringify(data, null, 2);
			}).catch(function () {
				pre.textContent = btn.dataset.empty || btn.dataset.loadError;
			});
		});
	});
})();
