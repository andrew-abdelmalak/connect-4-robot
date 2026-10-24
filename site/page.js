// Page furniture: the phone menu and the back-to-top button.

const header = document.querySelector(".top");
const menuBtn = document.getElementById("menu-btn");
const menu = document.getElementById("menu");

function setMenu(open, { focusButton = false } = {}) {
  header.dataset.menu = open ? "open" : "closed";
  menuBtn.setAttribute("aria-expanded", String(open));
  menuBtn.textContent = open ? "Close" : "Menu";
  if (open) menu.querySelector("a")?.focus();
  else if (focusButton) menuBtn.focus();
}
menuBtn.addEventListener("click", () => setMenu(header.dataset.menu !== "open"));
menu.addEventListener("click", (e) => { if (e.target.closest("a")) setMenu(false); });
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && header.dataset.menu === "open") setMenu(false, { focusButton: true });
});
document.addEventListener("click", (e) => {
  if (header.dataset.menu === "open" && !header.contains(e.target)) setMenu(false);
});

// Back to top: shown once the visitor has scrolled past the game.
const toTop = document.getElementById("to-top");
new IntersectionObserver(([entry]) => {
  toTop.hidden = entry.isIntersecting || entry.boundingClientRect.top > 0;
}).observe(document.getElementById("play"));

// Image viewer: each picture opens at full size, from its start, with its caption.
const viewer = document.getElementById("viewer");
const viewerCap = document.getElementById("viewer-cap");
const viewerImg = document.createElement("img");
document.getElementById("viewer-fig").prepend(viewerImg);
document.querySelectorAll("a[data-view]").forEach((link) => {
  link.addEventListener("click", (e) => {
    if (!viewer.showModal) return; // no dialog support: follow the link
    e.preventDefault();
    const img = link.querySelector("img");
    viewerImg.src = link.href;
    viewerImg.alt = img.alt;
    viewerCap.textContent = link.closest("figure")?.querySelector("figcaption")?.textContent || "";
    viewer.showModal();
    viewer.scrollTop = 0;
  });
});
viewer.addEventListener("click", (e) => { if (e.target === viewer) viewer.close(); });
