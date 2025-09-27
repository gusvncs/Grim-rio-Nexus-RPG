document.addEventListener("DOMContentLoaded", () => {
  const count = (name) => document.querySelectorAll(`input[name="${name}[]"]:checked`).length;
  const updateCounters = () => {
    const s = count("spells");
    const r = count("runes");
    const elS = document.getElementById("count-spells");
    const elR = document.getElementById("count-runes");
    if (elS) elS.textContent = `${s} selecionadas`;
    if (elR) elR.textContent = `${r} selecionadas`;
  };
  updateCounters();

  document.body.addEventListener("change", (e) => {
    if (e.target.matches('input[type="checkbox"][name="spells[]"], input[type="checkbox"][name="runes[]"]')) {
      updateCounters();
    }
  });

  document.body.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-bulk]");
    if (!btn) return;
    const kind = btn.getAttribute("data-bulk");
    const action = btn.getAttribute("data-action");
    const inputs = document.querySelectorAll(`input[name="${kind}[]"]`);
    inputs.forEach(i => i.checked = (action === "select"));
    updateCounters();
  });
});
