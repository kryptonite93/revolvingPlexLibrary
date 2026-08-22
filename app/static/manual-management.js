const form = document.querySelector("[data-manual-management-form]");

if (form) {
  const items = [...form.querySelectorAll("[data-manual-item]")];
  const selectAll = form.querySelector("[data-manual-select-all]");
  const status = form.querySelector("[data-manual-selection-status]");
  const review = form.querySelector("[data-manual-review]");
  const dialog = form.querySelector("[data-manual-dialog]");
  const count = form.querySelector("[data-manual-dialog-count]");
  const acknowledge = form.querySelector("[data-manual-acknowledge]");
  const execute = form.querySelector("[data-manual-execute]");
  const filteredCount = Number(form.dataset.filteredCount || 0);
  const filteredSize = Number(form.dataset.filteredSize || 0);
  const storagePrefix = "rpm-manual-selection:";
  const storageKey = `${storagePrefix}${form.dataset.selectionKey || "current"}`;
  const excluded = new Map();

  try {
    for (let index = sessionStorage.length - 1; index >= 0; index -= 1) {
      const key = sessionStorage.key(index);
      if (key?.startsWith(storagePrefix) && key !== storageKey) sessionStorage.removeItem(key);
    }
    const stored = JSON.parse(sessionStorage.getItem(storageKey) || "null");
    if (stored?.all === true && Array.isArray(stored.excluded)) {
      stored.excluded.forEach(([id, size]) => {
        if (typeof id === "string" && Number.isFinite(Number(size))) {
          excluded.set(id, Math.max(0, Number(size)));
        }
      });
      selectAll.checked = true;
      items.forEach((item) => { item.checked = !excluded.has(item.value); });
    }
  } catch (_error) {
    try { sessionStorage.removeItem(storageKey); } catch (_storageError) { /* No-op. */ }
  }

  const persistSelection = () => {
    try {
      if (selectAll.checked) {
        sessionStorage.setItem(
          storageKey,
          JSON.stringify({ all: true, excluded: [...excluded.entries()] }),
        );
      } else {
        sessionStorage.removeItem(storageKey);
      }
    } catch (_error) {
      // Selection still works on the current page when browser storage is unavailable.
    }
  };

  const syncExcludedInputs = () => {
    form.querySelectorAll("[data-manual-exclusion]").forEach((input) => input.remove());
    if (!selectAll.checked) return;
    excluded.forEach((_size, id) => {
      const input = document.createElement("input");
      input.type = "hidden";
      input.name = "excluded_lifecycle_ids";
      input.value = id;
      input.dataset.manualExclusion = "";
      form.append(input);
    });
  };

  const selectedCount = () => selectAll.checked
    ? Math.max(0, filteredCount - excluded.size)
    : items.filter((item) => item.checked).length;
  const selectedSize = () => selectAll.checked
    ? Math.max(0, filteredSize - [...excluded.values()].reduce((total, size) => total + size, 0))
    : items.filter((item) => item.checked).reduce((total, item) => total + Number(item.dataset.size || 0), 0);
  const formatSize = (bytes) => {
    const gibibyte = 1024 ** 3;
    const tebibyte = 1024 ** 4;
    const divisor = bytes >= tebibyte ? tebibyte : gibibyte;
    const unit = bytes >= tebibyte ? "TiB" : "GiB";
    return `${(bytes / divisor).toLocaleString(undefined, { maximumFractionDigits: 2 })} ${unit}`;
  };

  const updateSeriesCounts = () => {
    form.querySelectorAll(".manual-series-group").forEach((group) => {
      const seriesItems = [...group.querySelectorAll("[data-manual-item]")];
      const selected = seriesItems.filter((item) => item.checked).length;
      const label = group.querySelector(".manual-series-count strong");
      if (label) label.textContent = `${selected} selected`;
    });
  };

  const update = () => {
    const total = selectedCount();
    const size = selectedSize();
    form.classList.toggle("is-filtered-selection", selectAll.checked);
    syncExcludedInputs();
    status.textContent = total ? `${total} ${total === 1 ? "item" : "items"} selected · ${formatSize(size)}` : "No items selected";
    review.disabled = total === 0;
    count.textContent = `${total} ${total === 1 ? "item" : "items"}`;
    updateSeriesCounts();
  };

  items.forEach((item) => item.addEventListener("change", () => {
    if (selectAll.checked) {
      if (item.checked) excluded.delete(item.value);
      else excluded.set(item.value, Number(item.dataset.size || 0));
      persistSelection();
    }
    update();
  }));
  selectAll.addEventListener("change", () => {
    excluded.clear();
    items.forEach((item) => { item.checked = selectAll.checked; });
    persistSelection();
    update();
  });
  review.addEventListener("click", () => {
    acknowledge.checked = false;
    execute.disabled = true;
    dialog.showModal();
  });
  acknowledge.addEventListener("change", () => { execute.disabled = !acknowledge.checked; });
  dialog.querySelectorAll("[data-manual-dialog-close]").forEach((button) => {
    button.addEventListener("click", () => dialog.close());
  });
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) dialog.close();
  });
  form.addEventListener("submit", () => {
    try { sessionStorage.removeItem(storageKey); } catch (_error) { /* No-op. */ }
  });
  update();
}
