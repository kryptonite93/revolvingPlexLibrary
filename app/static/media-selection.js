(() => {
  const form = document.querySelector("[data-manual-protection-form]");
  if (!form) return;

  const filteredSelection = form.querySelector("[data-filtered-selection]");
  const lifecycleSelections = [...form.querySelectorAll("[data-lifecycle-checkbox]")];
  const seriesSelections = [...form.querySelectorAll("[data-series-checkbox]")];
  const actions = [...form.querySelectorAll("[data-selection-action]")];
  const status = form.querySelector("[data-selection-status]");
  const filteredCount = Number(form.dataset.filteredCount || 0);

  const selectedLifecycleCount = () =>
    lifecycleSelections.filter((selection) => selection.checked).length;

  const updateSeriesSelections = () => {
    seriesSelections.forEach((seriesSelection) => {
      const seasons = lifecycleSelections.filter(
        (selection) => selection.dataset.seriesKey === seriesSelection.dataset.seriesKey,
      );
      const selected = seasons.filter((selection) => selection.checked).length;
      seriesSelection.checked = seasons.length > 0 && selected === seasons.length;
      seriesSelection.indeterminate = selected > 0 && selected < seasons.length;
    });
  };

  const updateSelectionState = () => {
    const allFiltered = filteredSelection.checked;
    const selected = selectedLifecycleCount();
    lifecycleSelections.forEach((selection) => {
      selection.disabled = allFiltered;
    });
    seriesSelections.forEach((selection) => {
      selection.disabled = allFiltered;
    });
    actions.forEach((action) => {
      action.disabled = !allFiltered && selected === 0;
    });
    form.classList.toggle("is-filtered-selection", allFiltered);
    status.textContent = allFiltered
      ? `All ${filteredCount} filtered ${filteredCount === 1 ? "lifecycle" : "lifecycles"} selected across every page`
      : selected === 0
        ? "No lifecycles selected"
        : `${selected} ${selected === 1 ? "lifecycle" : "lifecycles"} selected on this page`;
  };

  lifecycleSelections.forEach((selection) => {
    selection.addEventListener("change", () => {
      updateSeriesSelections();
      updateSelectionState();
    });
  });

  seriesSelections.forEach((seriesSelection) => {
    seriesSelection.addEventListener("click", (event) => event.stopPropagation());
    seriesSelection.addEventListener("change", () => {
      lifecycleSelections
        .filter((selection) => selection.dataset.seriesKey === seriesSelection.dataset.seriesKey)
        .forEach((selection) => {
          selection.checked = seriesSelection.checked;
        });
      updateSeriesSelections();
      updateSelectionState();
    });
  });

  filteredSelection.addEventListener("change", updateSelectionState);
  updateSeriesSelections();
  updateSelectionState();
})();
