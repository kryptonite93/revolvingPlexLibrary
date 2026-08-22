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

  const selectedCount = () => selectAll.checked ? filteredCount : items.filter((item) => item.checked).length;
  const selectedSize = () => selectAll.checked
    ? filteredSize
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
      const selected = selectAll.checked
        ? seriesItems.length
        : seriesItems.filter((item) => item.checked).length;
      const label = group.querySelector(".manual-series-count strong");
      if (label) label.textContent = `${selected} selected`;
    });
  };

  const update = () => {
    const total = selectedCount();
    const size = selectedSize();
    form.classList.toggle("is-filtered-selection", selectAll.checked);
    items.forEach((item) => { item.disabled = selectAll.checked; });
    status.textContent = total ? `${total} ${total === 1 ? "item" : "items"} selected · ${formatSize(size)}` : "No items selected";
    review.disabled = total === 0;
    count.textContent = `${total} ${total === 1 ? "item" : "items"}`;
    updateSeriesCounts();
  };

  items.forEach((item) => item.addEventListener("change", update));
  selectAll.addEventListener("change", update);
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
  update();
}
