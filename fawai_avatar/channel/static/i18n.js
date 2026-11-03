/* term:界面多语言  doc:doc/功能/界面多语言/说明.md
 * Conventional UI i18n: locale catalogs + t(key) + data-i18n DOM apply.
 */
(function (global) {
  "use strict";

  var STORAGE_KEY = "fawai_locale";
  var SUPPORTED = ["zh-CN", "en"];
  var DEFAULT_LOCALE = "zh-CN";
  var catalogs = Object.create(null);
  var locale = DEFAULT_LOCALE;
  var readyPromise = null;
  var changeListeners = [];

  function normalizeLocale(v) {
    if (!v) return "";
    var s = String(v).trim();
    if (SUPPORTED.indexOf(s) >= 0) return s;
    var low = s.toLowerCase();
    if (low === "zh" || low.indexOf("zh") === 0) return "zh-CN";
    if (low === "en" || low.indexOf("en") === 0) return "en";
    return "";
  }

  function detectLocale() {
    try {
      var saved = normalizeLocale(localStorage.getItem(STORAGE_KEY) || "");
      if (saved) return saved;
    } catch (e) {}
    try {
      var nav = normalizeLocale(navigator.language || (navigator.languages && navigator.languages[0]) || "");
      if (nav) return nav;
    } catch (e2) {}
    return DEFAULT_LOCALE;
  }

  function lookup(dict, key) {
    if (!dict || !key) return undefined;
    if (Object.prototype.hasOwnProperty.call(dict, key) && typeof dict[key] === "string") {
      return dict[key];
    }
    var parts = String(key).split(".");
    var cur = dict;
    for (var i = 0; i < parts.length; i++) {
      if (cur == null || typeof cur !== "object") return undefined;
      cur = cur[parts[i]];
    }
    return typeof cur === "string" ? cur : undefined;
  }

  function format(str, vars) {
    if (!vars || typeof str !== "string") return str;
    return str.replace(/\{(\w+)\}/g, function (_, name) {
      return vars[name] != null ? String(vars[name]) : "{" + name + "}";
    });
  }

  function t(key, vars) {
    var dict = catalogs[locale] || catalogs[DEFAULT_LOCALE] || {};
    var raw = lookup(dict, key);
    if (raw == null && locale !== DEFAULT_LOCALE) {
      raw = lookup(catalogs[DEFAULT_LOCALE] || {}, key);
    }
    if (raw == null) return key;
    return format(raw, vars);
  }

  function tOr(key, fallback, vars) {
    var dict = catalogs[locale] || {};
    var raw = lookup(dict, key);
    if (raw == null && locale !== DEFAULT_LOCALE) {
      raw = lookup(catalogs[DEFAULT_LOCALE] || {}, key);
    }
    if (raw == null) return fallback != null ? format(String(fallback), vars) : key;
    return format(raw, vars);
  }

  function setAttrFromSpec(el, spec) {
    if (!spec) return;
    String(spec)
      .split(";")
      .forEach(function (part) {
        var bit = part.trim();
        if (!bit) return;
        var idx = bit.indexOf(":");
        if (idx <= 0) return;
        var attr = bit.slice(0, idx).trim();
        var key = bit.slice(idx + 1).trim();
        if (!attr || !key) return;
        el.setAttribute(attr, t(key));
      });
  }

  function applyNode(el) {
    if (!el || el.nodeType !== 1) return;
    var key = el.getAttribute("data-i18n");
    if (key) {
      if (el.hasAttribute("data-i18n-html")) el.innerHTML = t(key);
      else el.textContent = t(key);
    }
    var ph = el.getAttribute("data-i18n-placeholder");
    if (ph) el.setAttribute("placeholder", t(ph));
    var titleKey = el.getAttribute("data-i18n-title");
    if (titleKey) el.setAttribute("title", t(titleKey));
    var ariaKey = el.getAttribute("data-i18n-aria");
    if (ariaKey) el.setAttribute("aria-label", t(ariaKey));
    setAttrFromSpec(el, el.getAttribute("data-i18n-attr"));
  }

  function apply(root) {
    var scope = root && root.querySelectorAll ? root : document;
    if (scope === document) {
      document.documentElement.lang = locale === "zh-CN" ? "zh-CN" : "en";
      document.documentElement.setAttribute("data-locale", locale);
      var titleEl = document.querySelector("title");
      if (titleEl) titleEl.textContent = t("app.documentTitle");
    }
    var nodes = scope.querySelectorAll
      ? scope.querySelectorAll("[data-i18n],[data-i18n-placeholder],[data-i18n-title],[data-i18n-aria],[data-i18n-attr]")
      : [];
    for (var i = 0; i < nodes.length; i++) applyNode(nodes[i]);
    if (scope !== document && scope.matches && scope.matches("[data-i18n],[data-i18n-placeholder],[data-i18n-title],[data-i18n-aria],[data-i18n-attr]")) {
      applyNode(scope);
    }
    syncLangButtons();
    document.documentElement.removeAttribute("data-i18n-pending");
  }

  function syncLangButtons() {
    document.querySelectorAll("[data-locale-pref]").forEach(function (btn) {
      var on = btn.getAttribute("data-locale-pref") === locale;
      btn.setAttribute("aria-pressed", on ? "true" : "false");
    });
  }

  async function loadCatalog(code) {
    if (catalogs[code]) return catalogs[code];
    var url = "/assets/i18n/" + code + ".json";
    var res = await fetch(url, { cache: "no-cache" });
    if (!res.ok) throw new Error("i18n catalog missing: " + code);
    catalogs[code] = await res.json();
    return catalogs[code];
  }

  function emitChange() {
    for (var i = 0; i < changeListeners.length; i++) {
      try {
        changeListeners[i](locale);
      } catch (e) {}
    }
    try {
      document.dispatchEvent(new CustomEvent("fawai:localechange", { detail: { locale: locale } }));
    } catch (e2) {}
  }

  async function setLocale(next, opts) {
    var code = normalizeLocale(next) || DEFAULT_LOCALE;
    await loadCatalog(code);
    if (code !== DEFAULT_LOCALE) await loadCatalog(DEFAULT_LOCALE);
    locale = code;
    if (!opts || opts.persist !== false) {
      try {
        localStorage.setItem(STORAGE_KEY, locale);
      } catch (e) {}
    }
    apply(document);
    if (!opts || opts.emit !== false) emitChange();
    return locale;
  }

  async function init(opts) {
    if (readyPromise) return readyPromise;
    readyPromise = (async function () {
      document.documentElement.setAttribute("data-i18n-pending", "1");
      var initial = (opts && opts.locale) || detectLocale();
      try {
        await setLocale(initial, { persist: false, emit: false });
        try {
          localStorage.setItem(STORAGE_KEY, locale);
        } catch (e) {}
      } catch (err) {
        // Catalog fetch failed: keep default locale, unblock UI, do not kill the page.
        locale = normalizeLocale(initial) || DEFAULT_LOCALE;
        try {
          document.documentElement.lang = locale === "zh-CN" ? "zh-CN" : "en";
          document.documentElement.setAttribute("data-locale", locale);
        } catch (e2) {}
        console.warn("[i18n] init failed, continuing with", locale, err);
      } finally {
        document.documentElement.removeAttribute("data-i18n-pending");
      }
      return locale;
    })();
    return readyPromise;
  }

  function onChange(fn) {
    if (typeof fn === "function") changeListeners.push(fn);
    return function () {
      changeListeners = changeListeners.filter(function (x) {
        return x !== fn;
      });
    };
  }

  global.FawaiI18n = {
    STORAGE_KEY: STORAGE_KEY,
    supported: SUPPORTED.slice(),
    init: init,
    t: t,
    tOr: tOr,
    apply: apply,
    setLocale: setLocale,
    getLocale: function () {
      return locale;
    },
    detectLocale: detectLocale,
    onChange: onChange,
    normalizeLocale: normalizeLocale,
  };
})(typeof window !== "undefined" ? window : globalThis);
