(() => {
  const saveButtons = [...document.querySelectorAll("[data-save-button]")];

  function formSnapshot(form) {
    return JSON.stringify(
      [...form.elements]
        .filter((field) => field.name && field.name !== "csrf" && !["submit", "button"].includes(field.type))
        .map((field) => ({
          name: field.name,
          type: field.type,
          value: ["checkbox", "radio"].includes(field.type) ? String(field.checked) : field.value,
          disabled: field.disabled,
        })),
    );
  }

  function updateButton(button, dirty) {
    button.classList.toggle("is-dirty", dirty);
    button.classList.toggle("button-attention", dirty);
    button.classList.toggle("button-secondary", !dirty);
    if (button.dataset.dirtyLabel) {
      button.textContent = dirty ? button.dataset.dirtyLabel : button.dataset.cleanLabel;
    }
  }

  saveButtons.forEach((button) => {
    const form = button.form;
    if (!form) return;
    const initialSnapshot = formSnapshot(form);
    button.dataset.cleanLabel = button.textContent;

    const updateDirtyState = () => updateButton(button, formSnapshot(form) !== initialSnapshot);
    form.addEventListener("input", updateDirtyState);
    form.addEventListener("change", updateDirtyState);
    form.addEventListener("submit", () => {
      button.textContent = button.dataset.savingLabel || "Saving…";
      button.disabled = true;
    });
  });
})();
