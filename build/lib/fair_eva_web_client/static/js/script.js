// Additional JS for evaluation page.
//
// The original FAIR EVA interface defines functions to periodically update
// indicator values by polling the server.  In this minimal client there is
// no live polling, but we provide no‑op functions so that references
// present in the templates do not cause errors.  If you wish to implement
// live updates, replace the body of update_values() with an AJAX call.

// Placeholder for the interval ID used in the template.  Assigning on the
// global object makes it accessible from inline scripts.
var intervalID;

function update_values() {
  // Intentionally left blank.  In a full implementation this function
  // would request updated scores from the server and update the DOM.
}

function stopTextColor() {
  // Cancel the polling interval if it is defined.
  if (typeof intervalID !== 'undefined') {
    clearInterval(intervalID);
  }
}