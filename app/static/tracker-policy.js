const forms = document.querySelectorAll(".tracker-policy-form");

const modeCopy = {
  RATIO_ONLY: "Seed time is not used; the ratio and grace period must pass.",
  TIME_ONLY: "Ratio is not used; seed time and the grace period must pass.",
  RATIO_OR_TIME: "Either the ratio or seed-time requirement may satisfy this tracker rule.",
  RATIO_AND_TIME: "Both the ratio and seed-time requirements must pass.",
  NEVER_REMOVE: "Never remove blocks cleanup for this tracker; other policy fields do not apply.",
};

function setFieldState(form, fieldName, enabled) {
  const field = form.querySelector(`[data-policy-field="${fieldName}"]`);
  if (!field) return;
  field.classList.toggle("policy-field-disabled", !enabled);
  field.setAttribute("aria-disabled", String(!enabled));
  field.querySelectorAll("input").forEach((input) => {
    input.disabled = !enabled;
  });
}

function updatePolicyFields(form) {
  const requirement = form.querySelector("[data-policy-requirement]");
  const mode = requirement.value;
  const neverRemove = mode === "NEVER_REMOVE";
  setFieldState(form, "ratio", !neverRemove && mode !== "TIME_ONLY");
  setFieldState(form, "time", !neverRemove && mode !== "RATIO_ONLY");
  setFieldState(form, "grace", !neverRemove);
  setFieldState(form, "automation", !neverRemove);
  form.querySelector("[data-policy-note]").textContent = modeCopy[mode];
}

forms.forEach((form) => {
  const requirement = form.querySelector("[data-policy-requirement]");
  requirement.addEventListener("change", () => updatePolicyFields(form));
  updatePolicyFields(form);
});
