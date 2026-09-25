// Applies the saved theme before first paint (loaded from the same origin: CSP forbids inline scripts).
(function () {
  try {
    var pref = localStorage.getItem("rma-theme");
    var dark = pref === "dark" || (pref === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
    document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
  } catch (e) {
    document.documentElement.setAttribute("data-theme", "light");
  }
})();
