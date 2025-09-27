document.addEventListener("DOMContentLoaded", () => {
  const tabs = Array.from(document.querySelectorAll(".tab"));
  const panels = Array.from(document.querySelectorAll(".tabpanel"));

  function activateById(panelId){
    panels.forEach(p => p.classList.toggle("hidden", p.id !== panelId));
    tabs.forEach(t => {
      const active = t.getAttribute("data-target") === panelId;
      t.classList.toggle("active", active);
      t.setAttribute("aria-selected", active ? "true" : "false");
    });
  }

  function activateFromHash(){
    const hash = window.location.hash.replace("#", "");
    const target = hash ? `panel-${hash}` : (panels[0] && panels[0].id);
    if (target) activateById(target);
  }

  document.body.addEventListener("click", (e) => {
    const t = e.target.closest(".tab");
    if (!t) return;
    const target = t.getAttribute("data-target");
    if (!target) return;
    const slug = target.replace(/^panel-/, "");
    if (history.pushState) history.pushState(null, "", `#${slug}`);
    activateById(target);
  });

  window.addEventListener("hashchange", activateFromHash);
  activateFromHash();
});
