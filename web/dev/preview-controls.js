export function mountPreviewControls({ slot, documentRef }) {
  const fieldset = documentRef.createElement("fieldset");
  fieldset.className = "verify-fixture";
  fieldset.innerHTML = `<legend data-i18n="fixtureLabel"></legend><label><input type="radio" name="fixtureMode" value="typed" checked/><span data-i18n="fixtureTyped"></span></label><label><input type="radio" name="fixtureMode" value="handwritten"/><span data-i18n="fixtureHandwritten"></span></label>`;
  slot.replaceChildren(fieldset);
  return { fixtureInputs: fieldset.querySelectorAll('input[name="fixtureMode"]') };
}
