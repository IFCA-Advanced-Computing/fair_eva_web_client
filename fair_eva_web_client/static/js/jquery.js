/* Minimal jQuery-like interface to support simple selectors used in templates */
// This is not a full implementation of jQuery; it only supports the
// functionality needed by the FAIR EVA templates (text replacement).
(function() {
  function JQ(selector) {
    if (!(this instanceof JQ)) return new JQ(selector);
    this.element = document.querySelector(selector);
  }
  JQ.prototype.text = function(content) {
    if (!this.element) return this;
    if (content === undefined) {
      return this.element.textContent;
    }
    this.element.textContent = content;
    return this;
  };
  window.$ = function(selector) {
    return new JQ(selector);
  };
})();